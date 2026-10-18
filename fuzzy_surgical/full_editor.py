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
import time
from pathlib import Path
from typing import Optional, Tuple

import curses
import curses.ascii

from fuzzy_surgical.colors import Theme
from fuzzy_surgical.completion_ui import CompletionPopup
from fuzzy_surgical.devbox import DevBoxSession
from fuzzy_surgical.diagnostics_ui import DiagnosticPanel
from fuzzy_surgical.file_writer import FileWriteError, WriteResult, file_digest, write_text
from fuzzy_surgical.history import SnapshotStore
from fuzzy_surgical.lsp.completion import completion_prefix
from fuzzy_surgical.lsp.protocol import utf16_column
from fuzzy_surgical.mouse import (
    MOUSE_KEY,
    ScrollGate,
    ClickTracker,
    MouseEvent,
    read_mouse,
    reset_input_state,
)
from fuzzy_surgical.renderer import (
    draw_box,
    draw_code_line,
    index_for_display_column,
    safe_addstr,
    sanitize,
    shorten,
    window_size,
)
from fuzzy_surgical.text_buffer import (
    EditorOpenError,
    TextBuffer,
    detect_newline,
    normalize_newlines,
    read_editor_file,
)
from fuzzy_surgical.utils import (
    display_path,
    is_backspace,
    is_enter,
    printable_char,
    read_key,
)

CTRL_SPACE = 0
CTRL_L = 12
CTRL_N = 14
CTRL_S = 19
CTRL_W = 23
CTRL_Y = 25
CTRL_Z = 26
TAB = 9
COMPLETION_DELAY = 0.18

__all__ = [
    "EditorOpenError",
    "FullEditor",
    "TextBuffer",
    "detect_newline",
    "normalize_newlines",
    "read_editor_file",
]


def _devbox_advice(devbox, path: str) -> str:
    """Ask the dev box why completion is unavailable, tolerating test doubles."""
    advice = getattr(devbox, "advice_for", None)
    if callable(advice):
        try:
            return str(advice(path))
        except Exception:
            return ""
    return ""


class FullEditor:
    """Scrollable editor for one whole file."""

    def __init__(
        self,
        stdscr,
        theme: Optional[Theme] = None,
        history: Optional[SnapshotStore] = None,
        devbox: Optional[DevBoxSession] = None,
    ) -> None:
        self.stdscr = stdscr
        self.theme = theme if theme is not None else Theme()
        self.history = history
        self.devbox = devbox
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
        self.click_tracker = ClickTracker()
        self.scroll_gate = ScrollGate()
        self.completion = CompletionPopup()
        self.diagnostics_panel = DiagnosticPanel()
        self.diagnostics = []
        self.assist_status = ""
        self._completion_due_at = 0.0
        self._completion_manual = False
        self._completion_pending = False

    def edit(self, file_path: Path) -> bool:
        """Open the file and return True when at least one save happened."""
        reset_input_state()
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
        self.completion = CompletionPopup()
        self.diagnostics_panel = DiagnosticPanel()
        self.diagnostics = []
        self._completion_due_at = 0.0
        self._completion_manual = False
        self._completion_pending = False
        if self.devbox is not None:
            self.devbox.open_document(self.path, self.buffer.to_text())
            self.assist_status = _devbox_advice(self.devbox, self.path) or str(
                getattr(self.devbox, "status", "")
            )
        try:
            curses.curs_set(1)
        except curses.error:
            pass

        self._set_input_timeout(120)
        try:
            self._draw()
            while True:
                key = read_key(self.stdscr)
                if key is None or key == -1:
                    if self._poll_assist() or self._completion_due():
                        self._draw()
                    continue
                if key == curses.KEY_RESIZE:
                    self._draw()
                    continue
                if key == MOUSE_KEY:
                    event = read_mouse(self.stdscr)
                    if event is not None:
                        self._handle_mouse(event)
                    self._draw()
                    continue
                finished = False
                if self.mode == "edit":
                    finished = self._handle_edit_key(key)
                elif self.mode == "saved":
                    self.mode = "edit"
                elif self.mode == "discard":
                    if key in ("s", "S"):
                        self._save()
                    elif key in ("d", "D"):
                        finished = True
                    else:
                        self.mode = "edit"
                elif self.mode == "history":
                    self._handle_history_key(key)
                elif self.mode == "help":
                    self.mode = "edit"
                if finished:
                    return self.saved_any
                self._draw()
        finally:
            self._set_input_timeout(-1)
            self.completion.close()
            if self.devbox is not None:
                self.devbox.close_document()

    def _set_input_timeout(self, milliseconds: int) -> None:
        if self.stdscr is None:
            return
        try:
            self.stdscr.timeout(milliseconds)
        except (AttributeError, curses.error):
            pass

    def _poll_assist(self) -> bool:
        """Apply background dev box events, returning True when drawing changed."""
        if self.devbox is None:
            return False
        changed = False
        for event in self.devbox.poll():
            kind = event[0] if event else ""
            if kind == "completions":
                sequence = int(event[1])
                if self.completion.set_result(sequence, event[2]):
                    self._completion_pending = False
                    row = self.buffer.cursor_row
                    line = self.buffer.lines[row]
                    start, prefix = completion_prefix(line, self.buffer.cursor_col)
                    self.completion.prefix_start = start
                    self.completion.refine(prefix)
                    if not self.completion.visible:
                        self._report_no_completions()
                    self._completion_manual = False
                    changed = True
            elif kind == "diagnostics":
                self.diagnostics = [
                    item
                    for item in event[2]
                    if os.path.normcase(os.path.abspath(item.path))
                    == os.path.normcase(self.path)
                ]
                self.diagnostics_panel.set_items(self.diagnostics)
                changed = True
            elif kind == "status":
                self.assist_status = str(event[1])
                changed = True
        return changed

    def _completion_due(self) -> bool:
        if self.devbox is None or self._completion_due_at <= 0:
            return False
        if time.monotonic() < self._completion_due_at:
            return False
        self._completion_due_at = 0.0
        if self.completion.requested and self.completion.visible:
            return False
        self._request_completion()
        return True

    def _request_completion(self) -> None:
        if self.devbox is None:
            self.notice = "Language server support is off (--no-lsp)."
            self._completion_manual = False
            self._completion_pending = False
            return
        advice = _devbox_advice(self.devbox, self.path)
        if advice:
            self.notice = advice
            self.assist_status = advice
            self._completion_manual = False
            self._completion_pending = False
            return
        row = self.buffer.cursor_row
        line = self.buffer.lines[row]
        start, prefix = completion_prefix(line, self.buffer.cursor_col)
        sequence = self.devbox.request_completion(
            self.buffer.to_text(), row, utf16_column(line, self.buffer.cursor_col)
        )
        if sequence:
            self.completion.open(start, prefix, sequence)
            self._completion_pending = True
        else:
            self._completion_pending = False
            self._report_no_completions()

    def _report_no_completions(self) -> None:
        self._completion_pending = False
        advice = _devbox_advice(self.devbox, self.path) if self.devbox is not None else ""
        if advice:
            self.notice = advice
        elif self._completion_manual:
            message = "No completions returned for this position."
            error = self.devbox.client_error if self.devbox is not None else ""
            if error:
                message = f"{message} ({error})"
            self.notice = message
        self._completion_manual = False

    def _after_edit(self) -> None:
        if self.devbox is None:
            return
        row = self.buffer.cursor_row
        line = self.buffer.lines[row]
        start, prefix = completion_prefix(line, self.buffer.cursor_col)
        if self.completion.requested and self.completion.visible:
            self.completion.prefix_start = start
            self.completion.refine(prefix)
        if len(prefix) < 2:
            return
        self._completion_due_at = time.monotonic() + COMPLETION_DELAY

    def _accept_completion(self) -> None:
        item = self.completion.selected_item()
        if item is None:
            return
        row = self.buffer.cursor_row
        start = min(self.completion.prefix_start, self.buffer.cursor_col)
        self.buffer.replace_span(row, start, self.buffer.cursor_col, item.insert_text)
        self.completion.close()
        self._completion_due_at = 0.0
        self.error = ""

    def _jump_to_diagnostic(self) -> None:
        item = self.diagnostics_panel.selected_diagnostic()
        if item is None:
            return
        self.buffer.goto_line(item.line)
        self.diagnostics_panel.close()
        self._after_cursor_move()
        self.notice = f"Line {item.line}: {item.message[:60]}"

    def _handle_edit_key(self, key) -> bool:
        self.error = ""
        if self.completion.visible:
            if key == curses.KEY_UP:
                self.completion.move(-1)
                return False
            if key == curses.KEY_DOWN:
                self.completion.move(1)
                return False
            if is_enter(key) or key == TAB:
                self._accept_completion()
                return False
            if key == curses.ascii.ESC:
                self.completion.close()
                return False
        if self.diagnostics_panel.visible:
            if key == curses.KEY_UP:
                self.diagnostics_panel.move(-1)
                return False
            if key == curses.KEY_DOWN:
                self.diagnostics_panel.move(1)
                return False
            if is_enter(key):
                self._jump_to_diagnostic()
                return False
            if key in (curses.ascii.ESC, curses.KEY_F9, CTRL_L):
                self.diagnostics_panel.close()
                return False

        if key in (CTRL_SPACE, CTRL_N) or key == curses.KEY_F12:
            self._completion_manual = True
            self._request_completion()
            return False
        if key in (curses.KEY_F9, CTRL_L):
            self.diagnostics_panel.toggle()
            if not self.diagnostics_panel.visible and not self.diagnostics:
                self.notice = "No issues reported for this file yet."
            return False

        edited = False
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
            self._after_edit()
        elif key == curses.KEY_F1:
            self.mode = "help"
        elif key == CTRL_Z:
            self.buffer.undo()
            self._after_cursor_move()
            self._after_edit()
        elif key == CTRL_Y:
            self.buffer.redo()
            self._after_cursor_move()
            self._after_edit()
        elif is_enter(key):
            self.buffer.insert_newline()
            self._after_cursor_move()
            edited = True
        elif key == curses.KEY_UP:
            self.buffer.move_up()
        elif key == curses.KEY_DOWN:
            self.buffer.move_down()
        elif key == curses.KEY_LEFT:
            self.buffer.move_left()
            self.completion.close()
        elif key == curses.KEY_RIGHT:
            self.buffer.move_right()
            self.completion.close()
        elif key == curses.KEY_HOME:
            self.buffer.move_home()
            self.completion.close()
        elif key == curses.KEY_END:
            self.buffer.move_end()
            self.completion.close()
        elif key == curses.KEY_PPAGE:
            self.buffer.move_page(-self._page_size())
            self.completion.close()
        elif key == curses.KEY_NPAGE:
            self.buffer.move_page(self._page_size())
            self.completion.close()
        elif key == curses.KEY_DC:
            self.buffer.delete()
            edited = True
        elif is_backspace(key):
            self.buffer.backspace()
            edited = True
        elif key == TAB:
            self.buffer.insert_text("    ")
            edited = True
        else:
            char = printable_char(key)
            if char:
                self.buffer.insert_text(char)
                edited = True
        if edited:
            self._after_edit()
        return False

    def _handle_mouse(self, event: MouseEvent) -> None:
        """Apply a click or wheel tick to the active editor mode."""
        if event.is_scroll:
            self._scroll_mouse(event.direction)
            return

        if self.mode in ("saved", "help"):
            self.mode = "edit"
            return
        if self.mode == "discard":
            self.mode = "edit"
            return
        if self.mode == "history":
            self._click_history(event)
            return
        if self.mode != "edit":
            return

        position = self._buffer_position(event.y, event.x)
        if position is None:
            return
        row, column = position
        self.buffer.set_cursor(row, column)
        self.error = ""
        self.notice = ""
        if event.x < self._gutter_width():
            self.x_offset = 0

    def _scroll_mouse(self, direction: int) -> None:
        delta = self.scroll_gate.lines(direction)
        if delta == 0:
            return
        if self.mode == "edit":
            self._scroll_view(delta)
        elif self.mode == "history":
            self._select_history(self.history_index + delta)

    def _scroll_view(self, delta: int) -> None:
        """Scroll the viewport and carry the caret with it.

        The wheel moves the document under the pointer, so the caret keeps its
        screen row and the text moves on every emitted row. At the first and
        last line the caret stops with the view instead of leaving it.
        """
        if delta == 0 or self.buffer.line_count == 0:
            return
        height, _ = window_size(self.stdscr) if self.stdscr is not None else (24, 80)
        rows = self._content_rows(height)
        last_row = max(0, self.buffer.line_count - 1)
        target = max(0, min(last_row, self.buffer.cursor_row + delta))
        moved = target - self.buffer.cursor_row
        if moved == 0:
            return
        self.buffer.move_page(moved)
        max_scroll = max(0, self.buffer.line_count - rows)
        self.scroll_row = max(0, min(max_scroll, self.scroll_row + moved))
        # Keep the horizontal position: scrolling vertically must not jump the
        # view back to column zero.
        self.x_offset = max(0, self.x_offset)

    def _gutter_width(self) -> int:
        number_width = max(3, len(str(self.buffer.line_count)))
        return number_width + 2

    def _buffer_position(self, y: int, x: int) -> Optional[Tuple[int, int]]:
        """Translate a screen cell in the edit view to a buffer position."""
        if self.stdscr is None:
            return None
        height, width = window_size(self.stdscr)
        top = 2
        rows = self._content_rows(height)
        if y < top or y >= top + rows:
            return None
        row = self.scroll_row + (y - top)
        if row < 0 or row >= self.buffer.line_count:
            return None
        gutter = self._gutter_width()
        available = max(1, width - gutter - 1)
        display_column = self.x_offset + max(0, min(available - 1, x - gutter))
        column = index_for_display_column(self.buffer.lines[row], display_column)
        return row, column

    def _click_history(self, event: MouseEvent) -> None:
        height, _width = window_size(self.stdscr)
        rows = max(1, height - 6)
        box_height = min(height - 1, rows + 4)
        top = max(1, (height - box_height) // 2)
        first_row = top + 2
        offset = event.y - first_row
        if offset < 0 or offset >= rows:
            return
        index = self.history_scroll + offset
        if index < 0 or index >= len(self.history_entries):
            return
        if self.click_tracker.classify(event) == "double":
            self.history_index = index
            self._restore_selected()
        else:
            self._select_history(index)

    def _select_history(self, index: int) -> None:
        if not self.history_entries:
            return
        self.history_index = max(0, min(len(self.history_entries) - 1, index))
        self._ensure_history_visible()

    def _page_size(self) -> int:
        height, _ = window_size(self.stdscr)
        return max(1, height - 5)

    def _after_cursor_move(self) -> None:
        """Keep the horizontal view; the draw pass clamps it to the caret.

        Resetting ``x_offset`` here made a vertical move jump back to column
        zero, which made wheel scrolling look like it ignored the view.
        """

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
        self.completion.close()
        if self.devbox is not None:
            self.devbox.document_saved(self.path, self.buffer.to_text())
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
            if self.diagnostics_panel.visible:
                self.diagnostics_panel.draw(
                    self.stdscr, self.theme, status=self.assist_status
                )
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
        issues = f" !{len(self.diagnostics)}" if self.diagnostics else ""
        label = shorten(display_path(Path(self.path)), max(10, width - 34))
        title = f"Editing {label} ({self.buffer.line_count} lines){issues}{marker}"
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
            "Ctrl+S save  F2 save  F6 snapshots  F8 reload  "
            "^N complete  F9 issues  F1 help"[: width - 1],
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

        if self.completion.visible:
            self.completion.draw(
                self.stdscr,
                self.theme,
                caret_row,
                caret_col,
                max_rows=max(1, height - 5),
                max_width=max(24, width - gutter_width - 2),
            )

    def _draw_status(self, height: int, width: int) -> None:
        state = "modified" if self.buffer.dirty else "saved"
        left = f" Esc back  {state} "
        if self.assist_status:
            left += f" {self.assist_status} "
        if self._completion_pending and self.completion.requested:
            left += " waiting for completions "
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
            "  Mouse click    place the cursor at the clicked cell",
            "  Mouse wheel    scroll the file with the caret, trackpads follow",
            "",
            "Editing",
            "  Enter          split the line",
            "  Backspace      delete backwards, joining lines at column 0",
            "  Delete         delete forwards, joining lines at the end",
            "  Tab            insert four spaces",
            "  Ctrl+Z         undo",
            "  Ctrl+Y         redo",
            "",
            "Dev box (language server and linters)",
            "  Ctrl+N         request completions now (F12 and Ctrl+Space too)",
            "  typing         suggestions appear after two characters",
            "  Up / Down      choose a suggestion, Enter or Tab inserts",
            "  Esc            close the suggestion list without inserting",
            "  F9 / Ctrl+L    show issues, Enter jumps to the line",
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
