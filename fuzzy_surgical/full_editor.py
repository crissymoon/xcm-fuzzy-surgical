"""Full file editor shown inside the existing curses session.

The line editor deliberately touches one line at a time. This editor is the
other mode: a scrollable text buffer with a cursor, syntax colors, undo and
redo, SQLite snapshots and a save that verifies the disk copy.

Capabilities are kept intentionally small and predictable. There is no plugin
system and no language server: the goal is a safe in-terminal fix for a file
the user just found with the fuzzy search.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path
from typing import List, Optional, Tuple

import curses
import curses.ascii

from fuzzy_surgical.colors import Theme
from fuzzy_surgical.file_writer import FileWriteError, WriteResult, file_digest, write_text
from fuzzy_surgical.history import SnapshotStore
from fuzzy_surgical.renderer import (
    draw_box,
    draw_code_line,
    safe_addstr,
    sanitize,
    shorten,
    window_size,
)
from fuzzy_surgical.utils import (
    display_path,
    is_backspace,
    is_enter,
    printable_char,
    read_key,
)

MAX_EDITOR_BYTES = 8_000_000
MAX_EDITOR_LINES = 200_000
MAX_UNDO_STEPS = 500
COALESCE_SECONDS = 0.6

CTRL_S = 19
CTRL_W = 23
CTRL_Y = 25
CTRL_Z = 26
TAB = 9


class EditorOpenError(Exception):
    """Raised when a file cannot be opened safely in the full editor."""


def detect_newline(text: str) -> str:
    """Return the dominant line ending without guessing on empty text."""
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    if crlf and crlf >= lf and crlf >= cr:
        return "\r\n"
    if cr and cr > lf:
        return "\r"
    return "\n"


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_editor_file(file_path: str) -> str:
    """Read a text file with the editor caps, raising EditorOpenError."""
    try:
        info = os.stat(file_path)
    except OSError as exc:
        raise EditorOpenError("Could not read the file: " + (exc.strerror or str(exc))) from exc
    if not stat.S_ISREG(info.st_mode):
        raise EditorOpenError("That path is not a regular file.")
    if info.st_size > MAX_EDITOR_BYTES:
        raise EditorOpenError(
            f"File is larger than {MAX_EDITOR_BYTES // 1_000_000} MB. Edit it outside the TUI."
        )
    try:
        with open(file_path, "rb") as handle:
            data = handle.read(MAX_EDITOR_BYTES + 1)
    except OSError as exc:
        raise EditorOpenError("Could not read the file: " + (exc.strerror or str(exc))) from exc
    if b"\x00" in data[:8192]:
        raise EditorOpenError("That looks like a binary file, so it was not opened.")
    text = data.decode("utf-8", "surrogateescape")
    if text.count("\n") > MAX_EDITOR_LINES:
        raise EditorOpenError("File has too many lines for the built-in editor.")
    return text


class TextBuffer:
    """Editable lines with cursor movement, undo and redo."""

    def __init__(self, text: str = "") -> None:
        self.newline = detect_newline(text)
        self.trailing_newline = False
        self.lines: List[str] = [""]
        self.cursor_row = 0
        self.cursor_col = 0
        self.original_text = ""
        self.undo_stack: List[Tuple[List[str], bool, int, int]] = []
        self.redo_stack: List[Tuple[List[str], bool, int, int]] = []
        self._last_edit = ""
        self._last_edit_at = 0.0
        self._reset(text)

    def _reset(self, text: str) -> None:
        normalized = normalize_newlines(text)
        self.trailing_newline = normalized.endswith("\n")
        body = normalized[:-1] if self.trailing_newline else normalized
        self.lines = body.split("\n")
        if not self.lines:
            self.lines = [""]
        self.cursor_row = 0
        self.cursor_col = 0
        self.original_text = self.to_text()

    def to_text(self) -> str:
        body = self.newline.join(self.lines)
        if self.trailing_newline:
            return body + self.newline
        return body

    @property
    def dirty(self) -> bool:
        return self.to_text() != self.original_text

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def mark_saved(self) -> None:
        self.original_text = self.to_text()

    def _clamp(self) -> None:
        if not self.lines:
            self.lines = [""]
        self.cursor_row = max(0, min(self.cursor_row, len(self.lines) - 1))
        self.cursor_col = max(0, min(self.cursor_col, len(self.lines[self.cursor_row])))

    def _state(self) -> Tuple[List[str], bool, int, int]:
        return (list(self.lines), self.trailing_newline, self.cursor_row, self.cursor_col)

    def _restore(self, state: Tuple[List[str], bool, int, int]) -> None:
        self.lines, self.trailing_newline, self.cursor_row, self.cursor_col = state
        self._clamp()
        self._last_edit = ""

    def _begin_edit(self, kind: str) -> None:
        now = time.monotonic()
        coalesce = (
            kind in ("insert", "backspace", "delete")
            and kind == self._last_edit
            and now - self._last_edit_at <= COALESCE_SECONDS
            and bool(self.undo_stack)
        )
        if not coalesce:
            self.undo_stack.append(self._state())
            if len(self.undo_stack) > MAX_UNDO_STEPS:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
        self._last_edit = kind
        self._last_edit_at = now

    def insert_text(self, text: str) -> None:
        if not text:
            return
        text = normalize_newlines(text)
        self._begin_edit("insert")
        row = self.cursor_row
        line = self.lines[row]
        before = line[: self.cursor_col]
        after = line[self.cursor_col :]
        parts = text.split("\n")
        if len(parts) == 1:
            self.lines[row] = before + text + after
            self.cursor_col += len(text)
            return
        replacement = [before + parts[0]]
        replacement.extend(parts[1:-1])
        replacement.append(parts[-1] + after)
        self.lines[row : row + 1] = replacement
        self.cursor_row = row + len(parts) - 1
        self.cursor_col = len(parts[-1])

    def insert_newline(self) -> None:
        self._begin_edit("newline")
        row = self.cursor_row
        line = self.lines[row]
        before = line[: self.cursor_col]
        after = line[self.cursor_col :]
        self.lines[row : row + 1] = [before, after]
        self.cursor_row = row + 1
        self.cursor_col = 0

    def backspace(self) -> None:
        if self.cursor_col > 0:
            self._begin_edit("backspace")
            row = self.cursor_row
            self.lines[row] = (
                self.lines[row][: self.cursor_col - 1] + self.lines[row][self.cursor_col :]
            )
            self.cursor_col -= 1
        elif self.cursor_row > 0:
            self._begin_edit("backspace")
            previous = self.lines[self.cursor_row - 1]
            current = self.lines.pop(self.cursor_row)
            self.cursor_row -= 1
            self.cursor_col = len(previous)
            self.lines[self.cursor_row] = previous + current

    def delete(self) -> None:
        line = self.lines[self.cursor_row]
        if self.cursor_col < len(line):
            self._begin_edit("delete")
            row = self.cursor_row
            self.lines[row] = line[: self.cursor_col] + line[self.cursor_col + 1 :]
        elif self.cursor_row < len(self.lines) - 1:
            self._begin_edit("delete")
            row = self.cursor_row
            self.lines[row] = line + self.lines[row + 1]
            del self.lines[row + 1]

    def move_left(self) -> None:
        if self.cursor_col > 0:
            self.cursor_col -= 1
        elif self.cursor_row > 0:
            self.cursor_row -= 1
            self.cursor_col = len(self.lines[self.cursor_row])

    def move_right(self) -> None:
        line = self.lines[self.cursor_row]
        if self.cursor_col < len(line):
            self.cursor_col += 1
        elif self.cursor_row < len(self.lines) - 1:
            self.cursor_row += 1
            self.cursor_col = 0

    def move_up(self) -> None:
        if self.cursor_row > 0:
            self.cursor_row -= 1
            self._clamp()

    def move_down(self) -> None:
        if self.cursor_row < len(self.lines) - 1:
            self.cursor_row += 1
            self._clamp()

    def move_home(self) -> None:
        self.cursor_col = 0

    def move_end(self) -> None:
        self.cursor_col = len(self.lines[self.cursor_row])

    def move_page(self, delta: int) -> None:
        self.cursor_row = max(0, min(len(self.lines) - 1, self.cursor_row + delta))
        self._clamp()

    def goto_line(self, line_number: int) -> None:
        self.cursor_row = max(0, min(len(self.lines) - 1, line_number - 1))
        self._clamp()

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append(self._state())
        self._restore(self.undo_stack.pop())
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append(self._state())
        self._restore(self.redo_stack.pop())
        return True

    def replace_text(self, text: str) -> None:
        """Load text as an undoable change, keeping the saved baseline."""
        self._begin_edit("restore")
        normalized = normalize_newlines(text)
        self.trailing_newline = normalized.endswith("\n")
        body = normalized[:-1] if self.trailing_newline else normalized
        self.lines = body.split("\n") or [""]
        self.cursor_row = 0
        self.cursor_col = 0


class FullEditor:
    """Scrollable editor for one whole file."""

    def __init__(
        self,
        stdscr,
        theme: Optional[Theme] = None,
        history: Optional[SnapshotStore] = None,
    ) -> None:
        self.stdscr = stdscr
        self.theme = theme if theme is not None else Theme()
        self.history = history
        self.path = ""
        self.buffer = TextBuffer()
        self.disk_digest = ""
        self.scroll_row = 0
        self.x_offset = 0
        self.mode = "edit"
        self.error = ""
        self.notice = ""
        self.saved_any = False
        self.last_save: Optional[WriteResult] = None
        self.history_note = ""
        self.saved_proof = ""
        self.saved_line = 0
        self.history_entries = []
        self.history_index = 0
        self.history_scroll = 0

    def edit(self, file_path: Path) -> bool:
        """Open the file and return True when at least one save happened."""
        self.path = os.path.abspath(str(file_path))
        self.buffer = TextBuffer(read_editor_file(self.path))
        self.disk_digest = file_digest(self.path)
        self.scroll_row = 0
        self.x_offset = 0
        self.mode = "edit"
        self.error = ""
        self.notice = ""
        self.saved_any = False
        self.last_save = None
        try:
            curses.curs_set(1)
        except curses.error:
            pass

        while True:
            self._draw()
            key = read_key(self.stdscr)
            if key is None:
                continue
            if key == curses.KEY_RESIZE:
                continue
            if self.mode == "edit":
                if self._handle_edit_key(key):
                    return self.saved_any
            elif self.mode == "saved":
                self.mode = "edit"
            elif self.mode == "discard":
                if key in ("s", "S"):
                    self._save()
                elif key in ("d", "D"):
                    return self.saved_any
                else:
                    self.mode = "edit"
            elif self.mode == "history":
                self._handle_history_key(key)
            elif self.mode == "help":
                self.mode = "edit"

    def _handle_edit_key(self, key) -> bool:
        self.error = ""
        if key == curses.ascii.ESC:
            if self.buffer.dirty:
                self.mode = "discard"
            else:
                return True
        elif key in (CTRL_S, CTRL_W) or key == curses.KEY_F2:
            self._save()
        elif key == curses.KEY_F6:
            self._open_history()
        elif key == curses.KEY_F8:
            self._reload()
        elif key == curses.KEY_F1:
            self.mode = "help"
        elif key == CTRL_Z:
            self.buffer.undo()
            self._after_cursor_move()
        elif key == CTRL_Y:
            self.buffer.redo()
            self._after_cursor_move()
        elif is_enter(key):
            self.buffer.insert_newline()
            self._after_cursor_move()
        elif key == curses.KEY_UP:
            self.buffer.move_up()
        elif key == curses.KEY_DOWN:
            self.buffer.move_down()
        elif key == curses.KEY_LEFT:
            self.buffer.move_left()
        elif key == curses.KEY_RIGHT:
            self.buffer.move_right()
        elif key == curses.KEY_HOME:
            self.buffer.move_home()
        elif key == curses.KEY_END:
            self.buffer.move_end()
        elif key == curses.KEY_PPAGE:
            self.buffer.move_page(-self._page_size())
        elif key == curses.KEY_NPAGE:
            self.buffer.move_page(self._page_size())
        elif key == curses.KEY_DC:
            self.buffer.delete()
        elif is_backspace(key):
            self.buffer.backspace()
        elif key == TAB:
            self.buffer.insert_text("    ")
        else:
            char = printable_char(key)
            if char:
                self.buffer.insert_text(char)
        return False

    def _page_size(self) -> int:
        height, _ = window_size(self.stdscr)
        return max(1, height - 5)

    def _after_cursor_move(self) -> None:
        self.x_offset = 0

    def _save(self) -> None:
        self.error = ""
        self.notice = ""
        if not self.buffer.dirty:
            self.notice = "No changes to save."
            return

        current = file_digest(self.path)
        if self.disk_digest and current and current != self.disk_digest:
            self.error = "File changed on disk. Press F8 to reload before saving."
            return

        snapshots = 0
        history_note = ""
        if self.history is not None:
            if self.history.available:
                if self.history.record_current(self.path, reason="full editor") is not None:
                    snapshots = 1
                if self.history.error:
                    history_note = self.history.error
            else:
                history_note = "history disabled"

        try:
            result = write_text(self.path, self.buffer.to_text())
        except FileWriteError as exc:
            self.error = str(exc)
            return

        self.buffer.mark_saved()
        self.disk_digest = result.digest
        self.last_save = result
        self.saved_any = True
        self.saved_line = self.buffer.cursor_row + 1
        self.saved_proof = self._read_disk_line(self.saved_line)
        if history_note:
            self.history_note = history_note
        elif snapshots:
            self.history_note = "previous version stored in SQLite"
        elif self.history is not None and not self.history.available:
            self.history_note = "history disabled"
        else:
            self.history_note = "no snapshot was needed"
        self.mode = "saved"

    def _read_disk_line(self, number: int) -> str:
        if number <= 0:
            return ""
        try:
            with open(
                self.path,
                "r",
                encoding="utf-8",
                errors="surrogateescape",
                newline="",
            ) as handle:
                for index, line in enumerate(handle, 1):
                    if index == number:
                        return line.rstrip("\r\n")
        except OSError:
            return ""
        return ""

    def _reload(self) -> None:
        if self.buffer.dirty:
            self.error = "Save or discard your changes before reloading with F8."
            return
        try:
            text = read_editor_file(self.path)
        except EditorOpenError as exc:
            self.error = str(exc)
            return
        self.buffer = TextBuffer(text)
        self.disk_digest = file_digest(self.path)
        self.scroll_row = 0
        self.x_offset = 0
        self.notice = "Reloaded from disk."

    def _open_history(self) -> None:
        if self.history is None or not self.history.available:
            self.error = "SQLite history is disabled. Start without --no-history to keep snapshots."
            return
        entries = self.history.list_revisions(self.path, limit=100)
        if self.history.error:
            self.error = self.history.error
            return
        if not entries:
            self.notice = "No snapshots stored for this file yet."
            return
        self.history_entries = entries
        self.history_index = 0
        self.history_scroll = 0
        self.mode = "history"

    def _handle_history_key(self, key) -> None:
        if key == curses.ascii.ESC or key == curses.KEY_F6:
            self.mode = "edit"
            return
        count = len(self.history_entries)
        if key == curses.KEY_UP:
            self.history_index = max(0, self.history_index - 1)
        elif key == curses.KEY_DOWN:
            self.history_index = min(max(0, count - 1), self.history_index + 1)
        elif key == curses.KEY_PPAGE:
            self.history_index = max(0, self.history_index - 10)
        elif key == curses.KEY_NPAGE:
            self.history_index = min(max(0, count - 1), self.history_index + 10)
        elif is_enter(key) and count:
            self._restore_selected()
        self._ensure_history_visible()

    def _restore_selected(self) -> None:
        if not self.history_entries:
            return
        revision = self.history_entries[
            max(0, min(self.history_index, len(self.history_entries) - 1))
        ]
        if self.history is None:
            return
        text = self.history.read_text(revision.id)
        if text is None:
            self.error = self.history.error or "That snapshot could not be read."
            return
        self.buffer.replace_text(text)
        self.saved_line = 1
        self.scroll_row = 0
        self.x_offset = 0
        self.notice = (
            "Snapshot loaded into the buffer as unsaved text. Press Ctrl+S to write it."
        )
        self.mode = "edit"

    def _ensure_history_visible(self) -> None:
        height, _ = window_size(self.stdscr)
        rows = max(1, height - 6)
        if self.history_index < self.history_scroll:
            self.history_scroll = self.history_index
        elif self.history_index >= self.history_scroll + rows:
            self.history_scroll = self.history_index - rows + 1

    def _draw(self) -> None:
        self.stdscr.erase()
        height, width = window_size(self.stdscr)
        if height == 0 or width == 0:
            return
        if height < 8 or width < 30:
            safe_addstr(
                self.stdscr,
                0,
                0,
                "Terminal too small for the file editor. Resize or press Esc.",
                self.theme.attr("ui.error", bold=True),
            )
            self.stdscr.refresh()
            return

        self._draw_header(height, width)
        if self.mode == "edit":
            self._draw_edit(height, width)
        elif self.mode == "saved":
            self._draw_saved(height, width)
        elif self.mode == "discard":
            self._draw_discard(height, width)
        elif self.mode == "history":
            self._draw_history(height, width)
        else:
            self._draw_help(height, width)
        self._draw_status(height, width)
        self.stdscr.refresh()

    def _draw_header(self, height: int, width: int) -> None:
        marker = " *" if self.buffer.dirty else ""
        label = shorten(display_path(Path(self.path)), max(10, width - 28))
        title = f"Editing {label} ({self.buffer.line_count} lines){marker}"
        safe_addstr(
            self.stdscr,
            0,
            0,
            title[: width - 1],
            self.theme.attr("ui.accent", bold=True),
        )
        position = f" {self.buffer.cursor_row + 1}:{self.buffer.cursor_col + 1} "
        safe_addstr(
            self.stdscr,
            0,
            max(0, width - len(position) - 1),
            position,
            self.theme.attr("ui.lineno"),
        )
        safe_addstr(
            self.stdscr,
            1,
            0,
            "Ctrl+S save  F2 save  F6 snapshots  F8 reload  Ctrl+Z undo  F1 help"[: width - 1],
            self.theme.attr("ui.dim", dim=True),
        )

    def _content_rows(self, height: int) -> int:
        return max(1, height - 4)

    def _draw_edit(self, height: int, width: int) -> None:
        rows = self._content_rows(height)
        top = 2
        if self.buffer.cursor_row < self.scroll_row:
            self.scroll_row = self.buffer.cursor_row
        elif self.buffer.cursor_row >= self.scroll_row + rows:
            self.scroll_row = self.buffer.cursor_row - rows + 1

        number_width = max(3, len(str(self.buffer.line_count)))
        gutter_width = number_width + 2
        available = max(1, width - gutter_width - 1)

        line_text = self.buffer.lines[self.buffer.cursor_row]
        caret_display = len(sanitize(line_text[: self.buffer.cursor_col]))
        if caret_display < self.x_offset:
            self.x_offset = caret_display
        elif caret_display >= self.x_offset + available:
            self.x_offset = caret_display - available + 1

        end = min(self.buffer.line_count, self.scroll_row + rows)
        for row_index in range(self.scroll_row, end):
            screen_row = top + (row_index - self.scroll_row)
            active = row_index == self.buffer.cursor_row
            number = f"{row_index + 1:>{number_width}} "
            attr = self.theme.attr("edit.active", bold=True) if active else self.theme.attr(
                "ui.lineno", dim=True
            )
            safe_addstr(self.stdscr, screen_row, 0, number, attr)
            draw_code_line(
                self.stdscr,
                screen_row,
                gutter_width,
                self.buffer.lines[row_index],
                self.path,
                self.theme,
                base_attr=curses.A_BOLD if active else 0,
                max_width=available,
                x_offset=self.x_offset,
            )

        if self.scroll_row > 0:
            safe_addstr(self.stdscr, top, max(0, width - 8), " ^ more", curses.A_DIM)
        if end < self.buffer.line_count:
            safe_addstr(self.stdscr, top + rows - 1, max(0, width - 8), " v more", curses.A_DIM)

        caret_row = top + (self.buffer.cursor_row - self.scroll_row)
        caret_col = gutter_width + caret_display - self.x_offset
        if 0 <= caret_col < width - 1 and 0 <= caret_row < height - 2:
            safe_addstr(self.stdscr, caret_row, caret_col, " ", curses.A_REVERSE)
            try:
                self.stdscr.move(caret_row, caret_col)
            except curses.error:
                pass

        if self.error:
            safe_addstr(
                self.stdscr,
                height - 2,
                0,
                self.error[: width - 1],
                self.theme.attr("ui.error", bold=True),
            )
        elif self.notice:
            safe_addstr(
                self.stdscr,
                height - 2,
                0,
                self.notice[: width - 1],
                self.theme.attr("ui.success"),
            )

    def _draw_status(self, height: int, width: int) -> None:
        state = "modified" if self.buffer.dirty else "saved"
        left = f" Esc back  {state} "
        right = f" {self.buffer.cursor_row + 1}/{self.buffer.line_count} "
        if width < 64:
            left = " Ctrl+S save  Esc back "
        padding = max(0, width - len(left) - len(right))
        bar = (left + " " * padding + right)[: width - 1]
        safe_addstr(self.stdscr, height - 1, 0, bar, self.theme.attr("ui.status"))

    def _draw_saved(self, height: int, width: int) -> None:
        saved = self.last_save
        path = saved.path if saved is not None else self.path
        written = f"{saved.bytes_written} bytes" if saved is not None else "written"
        lines = [
            "Saved to disk",
            "",
            path,
            "",
            f"{self.buffer.line_count} lines, {written}, verified",
            f"History: {self.history_note}",
            "",
            f"Line {self.saved_line} now reads on disk:",
            f"  {sanitize(self.saved_proof)}",
            "",
            "Press any key to keep editing.",
        ]
        box_width = min(width - 2, max(len(line) for line in lines) + 4)
        box_height = len(lines) + 2
        top = max(0, (height - box_height) // 2)
        left = max(0, (width - box_width) // 2)
        overlay = self.theme.attr("ui.overlay")
        draw_box(self.stdscr, top, left, box_height, box_width, "Saved", self.theme, overlay)
        for offset, line in enumerate(lines, 1):
            attr = overlay
            if offset == 1:
                attr = self.theme.attr("ui.accent", bold=True)
            elif offset == 3:
                attr = self.theme.attr("ui.path")
            elif offset == 9:
                attr = self.theme.attr("edit.changed", bold=True)
            elif offset == 5:
                attr = self.theme.attr("ui.accent")
            safe_addstr(self.stdscr, top + offset, left + 2, line[: box_width - 4], attr)

    def _draw_discard(self, height: int, width: int) -> None:
        safe_addstr(
            self.stdscr,
            2,
            0,
            "Unsaved changes. s saves and exits, d discards, Esc keeps editing."[: width - 1],
            self.theme.attr("ui.error", bold=True),
        )
        safe_addstr(
            self.stdscr,
            3,
            0,
            f"File: {shorten(display_path(Path(self.path)), max(10, width - 8))}",
            self.theme.attr("ui.path"),
        )
        safe_addstr(
            self.stdscr,
            5,
            0,
            "Nothing has been written to disk yet."[: width - 1],
            self.theme.attr("ui.dim", dim=True),
        )

    def _draw_history(self, height: int, width: int) -> None:
        rows = max(1, height - 6)
        count = len(self.history_entries)
        self._ensure_history_visible()
        end = min(count, self.history_scroll + rows)
        box_height = min(height - 1, rows + 4)
        box_width = min(width - 2, max(48, width - 8))
        top = max(1, (height - box_height) // 2)
        left = max(0, (width - box_width) // 2)
        overlay = self.theme.attr("ui.overlay")
        draw_box(
            self.stdscr,
            top,
            left,
            box_height,
            box_width,
            f"SQLite snapshots ({count})",
            self.theme,
            overlay,
        )
        header = "  when                 size    reason"
        safe_addstr(self.stdscr, top + 1, left + 2, header[: box_width - 4], overlay)
        for offset, index in enumerate(range(self.history_scroll, end), 2):
            revision = self.history_entries[index]
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(revision.created_at))
            label = (
                f"{stamp}  {revision.original_size:>8}  {revision.reason or 'save'}"
            )
            selected = index == self.history_index
            attr = overlay
            if selected:
                attr = self.theme.attr("ui.accent", bold=True) | curses.A_REVERSE
            safe_addstr(self.stdscr, top + offset, left + 2, label[: box_width - 4], attr)
        footer = "Enter loads into the buffer  Esc closes"
        safe_addstr(
            self.stdscr,
            top + box_height - 2,
            left + 2,
            footer[: box_width - 4],
            overlay,
        )

    def _draw_help(self, height: int, width: int) -> None:
        lines = [
            "Precise movement",
            "  Left / Right   move one character, wrapping lines",
            "  Up / Down      move one line",
            "  Home / End     start or end of line",
            "  PgUp / PgDn    move one page",
            "",
            "Editing",
            "  Enter          split the line",
            "  Backspace      delete backwards, joining lines at column 0",
            "  Delete         delete forwards, joining lines at the end",
            "  Tab            insert four spaces",
            "  Ctrl+Z         undo",
            "  Ctrl+Y         redo",
            "",
            "Saving and safety",
            "  Ctrl+S / F2    write and verify the disk copy",
            "  F6             browse SQLite snapshots, Enter loads one",
            "  F8             reload the file from disk",
            "  Esc            leave, asking before discarding changes",
            "",
            "Press any key to return.",
        ]
        box_width = min(width - 2, max(len(line) for line in lines) + 4)
        box_height = len(lines) + 2
        top = max(0, (height - box_height) // 2)
        left = max(0, (width - box_width) // 2)
        overlay = self.theme.attr("ui.overlay")
        draw_box(
            self.stdscr,
            top,
            left,
            box_height,
            box_width,
            "File editor controls",
            self.theme,
            overlay,
        )
        for offset, line in enumerate(lines, 1):
            safe_addstr(self.stdscr, top + offset, left + 2, line[: box_width - 4], overlay)
