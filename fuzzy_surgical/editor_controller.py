"""Surgical line editor shown inside the existing curses session."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import curses
import curses.ascii

from fuzzy_surgical.colors import Theme
from fuzzy_surgical.file_writer import FileWriteError, WriteResult, write_text
from fuzzy_surgical.history import SnapshotStore
from fuzzy_surgical.mouse import MOUSE_KEY, ClickTracker, MouseEvent, ScrollGate, read_mouse
from fuzzy_surgical.renderer import (
    draw_box,
    draw_code_line,
    index_for_display_column,
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

CTRL_S = 19
CTRL_W = 23


class EditorController:
    """Edits nearby lines and writes changes back to the same file."""

    def __init__(
        self,
        theme: Optional[Theme] = None,
        history: Optional[SnapshotStore] = None,
    ) -> None:
        self.theme = theme
        self.history = history
        self.stdscr = None
        self.file_path = ""
        self.line_num = 1
        self.original_line = ""
        self.context_lines: List[Tuple[int, str]] = []
        self.active_ctx_idx = 0
        self.line_edits: Dict[int, str] = {}
        self.edited_line = ""
        self.cursor_pos = 0
        self.mode = "edit"
        self.save_error = ""
        self.last_save: Optional[WriteResult] = None
        self.last_save_line = 0
        self.saved_history_note = ""
        self.click_tracker = ClickTracker()
        self.scroll_gate = ScrollGate()
        self.ctx_start = 0
        self.visible_ctx_count = 0
        self.edit_row = 0
        self.edit_view_start = 0

    def edit_line(
        self,
        stdscr,
        file_path: str,
        line_num: int,
        original_line: str,
        context_lines: List[Tuple[int, str]],
        theme: Optional[Theme] = None,
        match_column: Optional[int] = None,
    ) -> bool:
        """Open the line editor. Returns True when the file was modified."""
        if theme is not None:
            self.theme = theme
        if self.theme is None:
            self.theme = Theme()
            self.theme.init()

        if not context_lines:
            context_lines = [(line_num, original_line)]

        return self._edit_line_ui(
            stdscr,
            file_path,
            line_num,
            original_line,
            context_lines,
            match_column,
        )

    def _edit_line_ui(
        self,
        stdscr,
        file_path: str,
        line_num: int,
        original_line: str,
        context_lines: List[Tuple[int, str]],
        match_column: Optional[int] = None,
    ) -> bool:
        self.stdscr = stdscr
        self.file_path = file_path
        self.line_num = line_num
        self.original_line = original_line
        self.context_lines = context_lines

        try:
            curses.curs_set(1)
        except curses.error:
            pass

        try:
            self.active_ctx_idx = next(
                index for index, (number, _) in enumerate(context_lines) if number == line_num
            )
        except StopIteration:
            self.active_ctx_idx = len(context_lines) // 2

        self.line_edits = {}
        self.edited_line = original_line
        if match_column is None:
            self.cursor_pos = len(original_line)
        else:
            self.cursor_pos = max(0, min(len(original_line), match_column))
        self.mode = "edit"
        self.save_error = ""
        self.last_save = None
        self.last_save_line = 0
        self.saved_history_note = ""
        self.saved_proof = ""
        self.saved_line_number = 0
        self.saved_edit_count = 0

        while True:
            self._draw_editor()
            key = read_key(self.stdscr)
            if key is None:
                continue

            if self.mode == "edit":
                if key == MOUSE_KEY:
                    event = read_mouse(self.stdscr)
                    if event is not None:
                        self._handle_mouse(event)
                    continue
                if key == curses.ascii.ESC:
                    if self._has_pending_changes():
                        self.mode = "discard"
                    else:
                        return False
                elif key in (CTRL_S, CTRL_W) or key == curses.KEY_F2 or is_enter(key):
                    self._save_and_show_proof()
                elif key == curses.KEY_UP:
                    self.save_error = ""
                    self._save_active_edit()
                    if self.active_ctx_idx > 0:
                        self.active_ctx_idx -= 1
                        self._load_active_edit()
                elif key == curses.KEY_DOWN:
                    self.save_error = ""
                    self._save_active_edit()
                    if self.active_ctx_idx < len(self.context_lines) - 1:
                        self.active_ctx_idx += 1
                        self._load_active_edit()
                elif key == curses.KEY_LEFT:
                    self.cursor_pos = max(0, self.cursor_pos - 1)
                elif key == curses.KEY_RIGHT:
                    self.cursor_pos = min(len(self.edited_line), self.cursor_pos + 1)
                elif key == curses.KEY_HOME:
                    self.cursor_pos = 0
                elif key == curses.KEY_END:
                    self.cursor_pos = len(self.edited_line)
                elif key == curses.KEY_DC:
                    if self.cursor_pos < len(self.edited_line):
                        self.edited_line = (
                            self.edited_line[: self.cursor_pos]
                            + self.edited_line[self.cursor_pos + 1 :]
                        )
                elif is_backspace(key):
                    if self.cursor_pos > 0:
                        self.edited_line = (
                            self.edited_line[: self.cursor_pos - 1]
                            + self.edited_line[self.cursor_pos :]
                        )
                        self.cursor_pos -= 1
                elif key == curses.KEY_F1:
                    self.mode = "help"
                else:
                    char = printable_char(key)
                    if char:
                        self.edited_line = (
                            self.edited_line[: self.cursor_pos]
                            + char
                            + self.edited_line[self.cursor_pos :]
                        )
                        self.cursor_pos += 1

            elif self.mode == "saved":
                return True

            elif self.mode == "discard":
                if key in ("s", "S"):
                    self._save_and_show_proof()
                elif key in ("d", "D"):
                    return False
                else:
                    self.mode = "edit"

            elif self.mode == "help":
                self.mode = "edit"

    def _has_pending_changes(self) -> bool:
        return self._pending_count() > 0

    def _pending_count(self) -> int:
        count = len(self.line_edits)
        if not self.context_lines:
            return count
        number, original = self.context_lines[self.active_ctx_idx]
        if self.edited_line != original and number not in self.line_edits:
            count += 1
        return count

    def _save_now(self) -> bool:
        """Save from the edit screen without walking through the review step."""
        self._save_active_edit()
        if not self.line_edits:
            self.save_error = "No changes to save. Type an edit first."
            return False
        return self._apply_changes()

    def _save_and_show_proof(self) -> None:
        """Write the file, then stay on a screen that shows the disk copy."""
        if self._save_now():
            self.mode = "saved"

    def _read_disk_line(self, number: int) -> str:
        """Read one line back from disk, the way another program would."""
        if number <= 0:
            return ""
        try:
            with open(
                self.file_path,
                "r",
                encoding="utf-8",
                errors="surrogateescape",
                newline="",
            ) as handle:
                for index, text in enumerate(handle, 1):
                    if index == number:
                        return _split_line_ending(text)[0]
        except OSError:
            return ""
        return ""

    def _save_active_edit(self) -> None:
        number, original = self.context_lines[self.active_ctx_idx]
        if self.edited_line != original:
            self.line_edits[number] = self.edited_line
        else:
            self.line_edits.pop(number, None)

    def _load_active_edit(self) -> None:
        number, original = self.context_lines[self.active_ctx_idx]
        self.edited_line = self.line_edits.get(number, original)
        self.cursor_pos = len(self.edited_line)

    def _handle_mouse(self, event: MouseEvent) -> None:
        """Move the caret or switch context lines from a click or wheel tick."""
        if event.is_scroll:
            delta = self.scroll_gate.lines(event.direction, 1)
            if delta:
                self._move_context(delta)
            return

        if event.y == self.edit_row:
            display_column = self.edit_view_start + max(0, event.x)
            self.cursor_pos = index_for_display_column(self.edited_line, display_column)
            self.save_error = ""
            return

        offset = event.y - 2
        if offset < 0 or offset >= self.visible_ctx_count:
            return
        index = self.ctx_start + offset
        if index < 0 or index >= len(self.context_lines):
            return
        if index == self.active_ctx_idx:
            return
        self.save_error = ""
        self._save_active_edit()
        self.active_ctx_idx = index
        self._load_active_edit()

    def _move_context(self, delta: int) -> None:
        if delta == 0:
            return
        target = max(
            0,
            min(len(self.context_lines) - 1, self.active_ctx_idx + delta),
        )
        if target == self.active_ctx_idx:
            return
        self.save_error = ""
        self._save_active_edit()
        self.active_ctx_idx = target
        self._load_active_edit()

    def _draw_editor(self) -> None:
        self.stdscr.erase()
        height, width = window_size(self.stdscr)
        if height == 0 or width == 0:
            return

        if height < 8 or width < 30:
            safe_addstr(
                self.stdscr,
                0,
                0,
                "Terminal too small for the editor. Resize or press Esc.",
                self.theme.attr("ui.error", bold=True),
            )
            self.stdscr.refresh()
            return

        active_number = (
            self.context_lines[self.active_ctx_idx][0]
            if self.mode in ("edit", "discard")
            else self.line_num
        )
        label = display_path(Path(self.file_path))
        title = f"Editing {shorten(label, max(10, width - 20))}:{active_number}"
        safe_addstr(
            self.stdscr,
            0,
            0,
            title[: width - 1],
            self.theme.attr("ui.accent", bold=True),
        )

        if self.mode == "edit":
            self._draw_edit_mode(height, width)
        elif self.mode == "saved":
            self._draw_saved_mode(height, width)
        elif self.mode == "discard":
            self._draw_discard_mode(height, width)
        else:
            self._draw_help_mode(height, width)

        self.stdscr.refresh()

    def _draw_edit_mode(self, height: int, width: int) -> None:
        safe_addstr(
            self.stdscr,
            1,
            0,
            "Context (Up/Down switches line, F1 help):"[: width - 1],
            self.theme.attr("ui.dim", dim=True),
        )

        max_ctx_rows = max(3, height - 7)
        half = max_ctx_rows // 2
        ctx_start = max(0, self.active_ctx_idx - half)
        ctx_end = min(len(self.context_lines), ctx_start + max_ctx_rows)
        ctx_start = max(0, ctx_end - max_ctx_rows)
        visible_ctx = self.context_lines[ctx_start:ctx_end]
        active_number = self.context_lines[self.active_ctx_idx][0]
        self.ctx_start = ctx_start
        self.visible_ctx_count = len(visible_ctx)

        if ctx_start > 0:
            safe_addstr(self.stdscr, 1, max(0, width - 8), " ^ more", curses.A_DIM)
        if ctx_end < len(self.context_lines):
            end_row = len(visible_ctx) + 2
            if end_row < height - 1:
                safe_addstr(self.stdscr, end_row, max(0, width - 8), " v more", curses.A_DIM)

        for offset, (number, original) in enumerate(visible_ctx):
            row = 2 + offset
            active = number == active_number
            changed = number in self.line_edits

            marker = ">" if active else ("+" if changed else " ")
            gutter = f"{marker} {number:>5} "
            if active:
                gutter_attr = self.theme.attr("edit.active", bold=True)
            elif changed:
                gutter_attr = self.theme.attr("edit.changed", bold=True)
            else:
                gutter_attr = self.theme.attr("ui.dim", dim=True)
            safe_addstr(self.stdscr, row, 0, gutter, gutter_attr)

            text = self.edited_line if active else self.line_edits.get(number, original)
            draw_code_line(
                self.stdscr,
                row,
                len(gutter),
                text,
                self.file_path,
                self.theme,
                base_attr=curses.A_BOLD if active else 0,
                max_width=max(0, width - len(gutter) - 1),
            )

        edit_y = len(visible_ctx) + 3
        safe_addstr(
            self.stdscr,
            edit_y,
            0,
            f"Edit line {active_number}:"[: width - 1],
            self.theme.attr("ui.lineno", bold=True),
        )

        text = self.edited_line
        display_text = sanitize(text)
        caret_display = len(sanitize(text[: self.cursor_pos]))
        available = max(1, width - 2)
        if len(display_text) <= available:
            view_start = 0
        else:
            view_start = max(0, caret_display - available * 2 // 3)

        edit_row = edit_y + 1
        self.edit_row = edit_row
        self.edit_view_start = view_start
        draw_code_line(
            self.stdscr,
            edit_row,
            0,
            display_text,
            self.file_path,
            self.theme,
            max_width=available,
            x_offset=view_start,
        )

        caret_col = caret_display - view_start
        if 0 <= caret_col < available:
            safe_addstr(
                self.stdscr,
                edit_row,
                caret_col,
                " ",
                curses.A_REVERSE,
            )
            try:
                self.stdscr.move(edit_row, min(caret_col, width - 2))
            except curses.error:
                pass

        if view_start > 0:
            safe_addstr(self.stdscr, edit_row, 0, "<", curses.A_DIM)
        if view_start + available < len(display_text):
            safe_addstr(self.stdscr, edit_row, max(0, width - 2), ">", curses.A_DIM)

        changed_count = self._pending_count()
        changed_label = f"  [{changed_count} line(s) modified]" if changed_count else ""
        if self.save_error:
            safe_addstr(
                self.stdscr,
                edit_y + 2,
                0,
                self.save_error[: width - 1],
                self.theme.attr("ui.error", bold=True),
            )
        help_text = (
            f"Enter save  F2 save  Esc discard  Up/Down line  F1 help{changed_label}"
        )
        safe_addstr(
            self.stdscr,
            edit_y + 3,
            0,
            help_text[: width - 1],
            self.theme.attr("ui.dim", dim=True),
        )

    def _draw_discard_mode(self, height: int, width: int) -> None:
        pending = self._pending_count()
        safe_addstr(
            self.stdscr,
            2,
            0,
            f"{pending} unsaved change(s). s saves and exits, d discards, Esc keeps editing."
            [: width - 1],
            self.theme.attr("ui.error", bold=True),
        )
        safe_addstr(
            self.stdscr,
            3,
            0,
            f"File: {shorten(display_path(Path(self.file_path)), max(10, width - 8))}",
            self.theme.attr("ui.path"),
        )
        safe_addstr(
            self.stdscr,
            5,
            0,
            "Nothing has been written to disk yet."[: width - 1],
            self.theme.attr("ui.dim", dim=True),
        )

    def _draw_saved_mode(self, height: int, width: int) -> None:
        """Show the text that was read back from disk after the write."""
        saved = self.last_save
        path = saved.path if saved is not None else self.file_path
        written = f"{saved.bytes_written} bytes" if saved is not None else "written"
        lines = [
            "Saved to disk",
            "",
            path,
        ]
        if saved is not None and saved.requested_path and saved.requested_path != path:
            lines.extend(["", f"Opened through {saved.requested_path}"])
        lines.extend(
            [
                "",
                f"Line {self.saved_line_number} now reads on disk:",
                f"  {sanitize(self.saved_proof)}",
                "",
                f"{self.saved_edit_count} line(s) written, {written}, verified",
                "",
                "Press any key to return.",
            ]
        )
        if self.saved_history_note:
            lines.insert(len(lines) - 2, f"History: {self.saved_history_note}")
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
            "Saved",
            self.theme,
            overlay,
        )
        proof_index = next(
            (index for index, line in enumerate(lines, 1) if line.startswith("  ")),
            0,
        )
        count_index = next(
            (index for index, line in enumerate(lines, 1) if "written" in line),
            0,
        )
        for offset, line in enumerate(lines, 1):
            attr = overlay
            if offset == 1:
                attr = self.theme.attr("ui.accent", bold=True)
            elif offset == 3:
                attr = self.theme.attr("ui.path")
            elif offset == proof_index:
                attr = self.theme.attr("edit.changed", bold=True)
            elif offset == count_index:
                attr = self.theme.attr("ui.accent")
            safe_addstr(
                self.stdscr,
                top + offset,
                left + 2,
                line[: box_width - 4],
                attr,
            )

    def _draw_help_mode(self, height: int, width: int) -> None:
        lines = [
            "Up / Down      switch context line",
            "Left / Right   move the cursor",
            "Home / End     jump to start or end",
            "Mouse click    place the caret or switch context line",
            "Mouse wheel    scroll the context by one line",
            "Delete         remove the character under the cursor",
            "Backspace      delete the previous character",
            "Enter          save the edited lines to disk",
            "F2             also saves",
            "Ctrl+S         also saves when the terminal delivers it",
            "Ctrl+W         also saves, for terminals with flow control",
            "Esc            leaves, asking before discarding",
            "F1             close this help",
            "",
            "Modified lines are marked + and shown in green.",
            "A save reads the file back and shows the disk copy.",
            "The saved screen shows the absolute path that changed.",
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
            "Editor controls",
            self.theme,
            overlay,
        )
        for offset, line in enumerate(lines, 1):
            safe_addstr(
                self.stdscr,
                top + offset,
                left + 2,
                line[: box_width - 4],
                overlay,
            )

    def _snapshot_before_write(self, text: str) -> str:
        """Store the pre-edit text in SQLite when history is available."""
        if self.history is None:
            return ""
        if not self.history.available:
            return "history disabled"
        snapshot = self.history.record_text(self.file_path, text, reason="line edit")
        if self.history.error:
            return self.history.error
        if snapshot is None:
            return "no new snapshot needed"
        return "previous version stored in SQLite"

    def _apply_changes(self) -> bool:
        """Apply edited lines after checking that the disk copy is unchanged."""
        self.save_error = ""
        try:
            with open(
                self.file_path,
                "r",
                encoding="utf-8",
                errors="surrogateescape",
                newline="",
            ) as handle:
                lines = handle.readlines()
        except OSError as exc:
            self.save_error = f"Could not read the file: {exc.strerror or exc}"
            return False

        edits = self.line_edits or {self.line_num: self.edited_line}
        originals = dict(self.context_lines)

        for number in edits:
            if not 0 < number <= len(lines):
                self.save_error = f"Line {number} no longer exists. Press Esc and reopen."
                return False
            expected = originals.get(number)
            if expected is None:
                continue
            current = _split_line_ending(lines[number - 1])[0]
            if current != expected:
                self.save_error = (
                    "File changed on disk. Press Esc and reopen to edit the new text."
                )
                return False

        for number, new_text in edits.items():
            _, ending = _split_line_ending(lines[number - 1])
            if not ending and number < len(lines):
                ending = "\n"
            lines[number - 1] = new_text + ending

        self.saved_history_note = self._snapshot_before_write("".join(lines))

        try:
            result = write_text(self.file_path, "".join(lines))
        except FileWriteError as exc:
            self.save_error = f"{exc} [{shorten(self.file_path, 40)}]"
            return False
        self.last_save = result
        self.last_save_line = min(edits) if edits else self.line_num
        self.saved_line_number = self.last_save_line
        self.saved_edit_count = len(edits)
        self.saved_proof = self._read_disk_line(self.last_save_line)
        return True


def _split_line_ending(line: str) -> Tuple[str, str]:
    """Return a line without its terminator plus the original terminator."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""
