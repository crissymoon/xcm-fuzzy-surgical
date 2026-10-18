"""Drawing for the whole-file editor, kept out of the controller."""

from __future__ import annotations

from pathlib import Path

import curses

from fuzzy_surgical import editor_layout
from fuzzy_surgical.editor_overlays import (
    draw_discard as draw_discard_overlay,
    draw_help as draw_help_overlay,
    draw_history as draw_history_overlay,
    draw_saved as draw_saved_overlay,
)
from fuzzy_surgical.renderer import (
    draw_code_line,
    safe_addstr,
    sanitize,
    shorten,
    window_size,
)
from fuzzy_surgical.selection import draw_selection
from fuzzy_surgical.utils import display_path

# The clipboard shortcuts lead the hint line because macOS terminals claim the
# Command equivalents (Cmd+X, Cmd+C, Cmd+V) for themselves and never forward
# them to the application.
EDITOR_KEY_HINT = (
    "Ctrl+S save  Ctrl+X cut  Ctrl+C copy  Ctrl+V paste  "
    "^N complete  F7 numbers  F10 actions  F1 help"
)


class EditorViewMixin:
    """Render the editor buffer, status line and overlays."""

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
        if self.mode == "edit" and self.menu.visible:
            self.menu.draw(self.stdscr, self.theme, height, width)
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
            EDITOR_KEY_HINT[: width - 1],
            self.theme.attr("ui.dim", dim=True),
        )

    def _draw_edit(self, height: int, width: int) -> None:
        rows = self._content_rows(height)
        top = 2
        number_width = max(3, len(str(self.buffer.line_count)))
        gutter_width = self._gutter_width()
        available = self._wrap_width(width)

        if self._pending_center:
            self._center_caret(height, width)
            self._pending_center = False
        else:
            self._ensure_caret_visible(height, width)

        segments = self._visible_segments(height, width)
        for offset, segment in enumerate(segments):
            screen_row = top + offset
            active = segment.row == self.buffer.cursor_row
            if segment.part == 0:
                number = self._line_number_text(segment.row, number_width)
                attr = (
                    self.theme.attr("edit.active", bold=True)
                    if active
                    else self.theme.attr("ui.lineno", dim=True)
                )
                safe_addstr(self.stdscr, screen_row, 0, number, attr)
            draw_code_line(
                self.stdscr,
                screen_row,
                gutter_width,
                self.buffer.lines[segment.row],
                self.path,
                self.theme,
                base_attr=curses.A_BOLD if active else 0,
                max_width=available,
                x_offset=segment.start_cell,
            )
            draw_selection(
                self.stdscr,
                screen_row,
                gutter_width,
                self.buffer.lines[segment.row],
                self.selection.span(
                    segment.row, len(self.buffer.lines[segment.row])
                ),
                segment.start_cell,
                available,
                self.theme,
            )

        if (self.scroll_row, self.scroll_part) != (0, 0):
            safe_addstr(self.stdscr, top, max(0, width - 8), " ^ more", curses.A_DIM)
        more_below = False
        if segments:
            last = segments[-1]
            more_below = (
                editor_layout.next_segment(
                    self.buffer.lines, last.row, last.part, available
                )
                is not None
            )
        if more_below:
            safe_addstr(
                self.stdscr,
                top + rows - 1,
                max(0, width - 8),
                " v more",
                curses.A_DIM,
            )

        caret_display = self._caret_cell()
        caret_part, caret_inner = self._caret_part(available)
        caret_index = editor_layout.segment_index(
            segments, self.buffer.cursor_row, caret_part
        )
        if caret_index is None:
            caret_index = 0
        caret_row = top + caret_index
        caret_col = min(gutter_width + caret_inner, max(0, width - 2))
        if 0 <= caret_row < height - 2:
            rendered = sanitize(self.buffer.lines[self.buffer.cursor_row])
            caret_char = (
                rendered[caret_display]
                if 0 <= caret_display < len(rendered)
                else " "
            )
            safe_addstr(self.stdscr, caret_row, caret_col, caret_char, curses.A_REVERSE)
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
        if self.selection.has_text:
            left += " selection "
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
        draw_saved_overlay(
            self.stdscr,
            self.theme,
            height,
            width,
            path=saved.path if saved is not None else self.path,
            line_count=self.buffer.line_count,
            byte_count=saved.bytes_written if saved is not None else None,
            history_note=self.history_note,
            saved_line=self.saved_line,
            saved_proof=self.saved_proof,
        )

    def _draw_discard(self, height: int, width: int) -> None:
        draw_discard_overlay(
            self.stdscr, self.theme, height, width, path=self.path
        )

    def _draw_history(self, height: int, width: int) -> None:
        self._ensure_history_visible()
        draw_history_overlay(
            self.stdscr,
            self.theme,
            height,
            width,
            entries=self.history_entries,
            selected=self.history_index,
            scroll=self.history_scroll,
        )

    def _draw_help(self, height: int, width: int) -> None:
        draw_help_overlay(self.stdscr, self.theme, height, width)
