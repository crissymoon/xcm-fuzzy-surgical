"""Surgical line editor shown inside the existing curses session."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import curses
import curses.ascii

from fuzzy_surgical.colors import Theme
from fuzzy_surgical.file_writer import FileWriteError, write_text
from fuzzy_surgical.renderer import (
    draw_box,
    draw_code_line,
    safe_addstr,
    sanitize,
    shorten,
    window_size,
)
from fuzzy_surgical.utils import is_backspace, is_enter, printable_char, read_key


class EditorController:
    """Edits nearby lines and writes changes atomically."""

    def __init__(self, theme: Optional[Theme] = None) -> None:
        self.theme = theme
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

        while True:
            self._draw_editor()
            key = read_key(self.stdscr)
            if key is None:
                continue

            if self.mode == "edit":
                if key == curses.ascii.ESC:
                    return False
                if is_enter(key):
                    self._save_active_edit()
                    if self.line_edits:
                        self.mode = "confirm"
                    else:
                        return False
                elif key == curses.KEY_UP:
                    self._save_active_edit()
                    if self.active_ctx_idx > 0:
                        self.active_ctx_idx -= 1
                        self._load_active_edit()
                elif key == curses.KEY_DOWN:
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

            elif self.mode == "confirm":
                if key in ("y", "Y"):
                    if self._apply_changes():
                        return True
                    self.save_error = "Could not write the file. Check permissions."
                    continue
                if key in ("n", "N", curses.ascii.ESC):
                    return False

            elif self.mode == "help":
                self.mode = "edit"

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
            self.context_lines[self.active_ctx_idx][0] if self.mode == "edit" else self.line_num
        )
        title = f"Editing {shorten(self.file_path, max(10, width - 20))}:{active_number}"
        safe_addstr(
            self.stdscr,
            0,
            0,
            title[: width - 1],
            self.theme.attr("ui.accent", bold=True),
        )

        if self.mode == "edit":
            self._draw_edit_mode(height, width)
        elif self.mode == "confirm":
            self._draw_confirm_mode(height, width)
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

        changed_count = len(self.line_edits)
        if self.edited_line != self.context_lines[self.active_ctx_idx][1]:
            if self.context_lines[self.active_ctx_idx][0] not in self.line_edits:
                changed_count += 1
        changed_label = f"  [{changed_count} line(s) modified]" if changed_count else ""
        help_text = f"Enter confirm  Esc cancel  Up/Down switch line  F1 help{changed_label}"
        safe_addstr(
            self.stdscr,
            edit_y + 3,
            0,
            help_text[: width - 1],
            self.theme.attr("ui.dim", dim=True),
        )

    def _draw_confirm_mode(self, height: int, width: int) -> None:
        count = len(self.line_edits)
        safe_addstr(
            self.stdscr,
            2,
            0,
            f"Apply {count} change(s)? (y/n)"[: width - 1],
            self.theme.attr("ui.accent", bold=True),
        )
        if self.save_error:
            safe_addstr(
                self.stdscr,
                3,
                0,
                self.save_error[: width - 1],
                self.theme.attr("ui.error", bold=True),
            )

        originals = {number: text for number, text in self.context_lines}
        row = 4
        for number in sorted(self.line_edits):
            if row >= height - 1:
                break
            new_text = self.line_edits[number]
            old_text = originals.get(number, "")

            safe_addstr(
                self.stdscr,
                row,
                0,
                f"Line {number} - Original:"[: width - 1],
                self.theme.attr("edit.original", bold=True),
            )
            row += 1
            if row < height - 1:
                safe_addstr(self.stdscr, row, 2, old_text[: width - 3])
                row += 1

            if row < height - 1:
                safe_addstr(
                    self.stdscr,
                    row,
                    0,
                    f"Line {number} - Modified:"[: width - 1],
                    self.theme.attr("edit.changed", bold=True),
                )
                row += 1
            if row < height - 1:
                safe_addstr(self.stdscr, row, 2, new_text[: width - 3])
                row += 2

    def _draw_help_mode(self, height: int, width: int) -> None:
        lines = [
            "Up / Down      switch context line",
            "Left / Right   move the cursor",
            "Home / End     jump to start or end",
            "Delete         remove the character under the cursor",
            "Backspace      delete the previous character",
            "Enter          confirm edits",
            "Esc            cancel all edits",
            "F1             close this help",
            "",
            "Modified lines are marked + and shown in green.",
            "Original text is shown in red before writing.",
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

        try:
            write_text(self.file_path, "".join(lines))
        except FileWriteError as exc:
            self.save_error = str(exc)
            return False
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
