"""Viewport state for the whole-file editor.

The editor wraps long lines, so the viewport is a pair: the logical line at
the top and the wrapped segment within that line. Everything else - caret
visibility, wheel scrolling, paging, mouse hit testing - is derived from that
pair through ``editor_layout``, which keeps the screen stable when the
terminal is resized or when a line grows past the right edge.
"""

from __future__ import annotations

from typing import Optional, Tuple

from fuzzy_surgical import editor_layout
from fuzzy_surgical.renderer import index_for_display_column, window_size

# Keep this many rows between the caret and the viewport edge on keyboard moves.
SCROLL_MARGIN = 3


class EditorScrollMixin:
    """Soft-wrap viewport helpers shared by drawing and mouse handling."""

    def _content_rows(self, height: int) -> int:
        return max(1, height - 4)

    def _gutter_width(self) -> int:
        number_width = max(3, len(str(self.buffer.line_count)))
        return number_width + 2

    def _wrap_width(self, width: int) -> int:
        return max(1, width - self._gutter_width() - 1)

    def _caret_part(self, available: int) -> Tuple[int, int]:
        line = self.buffer.lines[self.buffer.cursor_row]
        return editor_layout.caret_segment(
            line, self.buffer.cursor_col, available
        )

    def _caret_cell(self) -> int:
        line = self.buffer.lines[self.buffer.cursor_row]
        return editor_layout.caret_display_cell(line, self.buffer.cursor_col)

    def _visible_segments(self, height: int, width: int):
        return editor_layout.visible_segments(
            self.buffer.lines,
            self.scroll_row,
            self.scroll_part,
            self._content_rows(height),
            self._wrap_width(width),
        )

    def _ensure_caret_visible(self, height: int, width: int) -> None:
        """Move the viewport only when the caret leaves the margin band."""
        rows = self._content_rows(height)
        available = self._wrap_width(width)
        lines = self.buffer.lines
        row = self.buffer.cursor_row
        part, _ = self._caret_part(available)
        margin = 0 if getattr(self, "_dragging", False) else min(
            SCROLL_MARGIN, max(0, (rows - 1) // 2)
        )
        screen = editor_layout.screen_offset(
            lines,
            self.scroll_row,
            self.scroll_part,
            row,
            part,
            available,
            rows,
        )
        if screen is None:
            screen = rows if (row, part) >= (self.scroll_row, self.scroll_part) else -1
        if screen < margin:
            top = editor_layout.top_for_caret(
                lines, row, part, margin, available
            )
            self.scroll_row, self.scroll_part = self._clamp_top(
                top, height, width
            )
        elif screen > rows - 1 - margin:
            top = editor_layout.top_for_caret(
                lines, row, part, rows - 1 - margin, available
            )
            self.scroll_row, self.scroll_part = self._clamp_top(
                top, height, width
            )

    def _clamp_top(self, top: Tuple[int, int], height: int, width: int):
        """Stop the viewport at the last segment so no blank tail appears."""
        lines = self.buffer.lines
        if not lines:
            return (0, 0)
        available = self._wrap_width(width)
        rows = self._content_rows(height)
        last_row = len(lines) - 1
        last_part = editor_layout.segment_count(lines[last_row], available) - 1
        max_top = editor_layout.top_for_caret(
            lines, last_row, last_part, rows - 1, available
        )
        return top if top <= max_top else max_top

    def _center_caret(self, height: int, width: int) -> None:
        """Put the caret in the middle row of the viewport when possible."""
        available = self._wrap_width(width)
        part, _ = self._caret_part(available)
        top = editor_layout.top_for_caret(
            self.buffer.lines,
            self.buffer.cursor_row,
            part,
            self._content_rows(height) // 2,
            available,
        )
        self.scroll_row, self.scroll_part = self._clamp_top(top, height, width)

    def _caret_screen(self, height: int, width: int) -> Tuple[int, int]:
        """Return the caret's screen row and column, after fitting the view."""
        self._ensure_caret_visible(height, width)
        available = self._wrap_width(width)
        part, inner = self._caret_part(available)
        segments = self._visible_segments(height, width)
        index = editor_layout.segment_index(
            segments, self.buffer.cursor_row, part
        )
        if index is None:
            index = 0
        return 2 + index, self._gutter_width() + inner

    def _page_size(self) -> int:
        height, _ = window_size(self.stdscr) if self.stdscr is not None else (24, 80)
        return max(1, self._content_rows(height))

    def _move_display(self, delta: int) -> None:
        """Move the caret by screen rows without touching the text."""
        if delta == 0 or not self.buffer.lines:
            return
        height, width = (
            window_size(self.stdscr) if self.stdscr is not None else (24, 80)
        )
        target = editor_layout.move_display_rows(
            self.buffer.lines,
            self.buffer.cursor_row,
            self.buffer.cursor_col,
            delta,
            self._wrap_width(width),
        )
        if target != (self.buffer.cursor_row, self.buffer.cursor_col):
            self.buffer.set_cursor(*target)
            self._ensure_caret_visible(height, width)

    def _shift_view(self, delta: int) -> None:
        """Move the viewport by screen rows, leaving the caret in place."""
        if delta == 0 or not self.buffer.lines:
            return
        height, width = (
            window_size(self.stdscr) if self.stdscr is not None else (24, 80)
        )
        available = self._wrap_width(width)
        lines = self.buffer.lines
        step = 1 if delta > 0 else -1
        for _ in range(abs(delta)):
            if step > 0:
                following = editor_layout.next_segment(
                    lines, self.scroll_row, self.scroll_part, available
                )
                if following is None:
                    break
                self.scroll_row, self.scroll_part = following
            else:
                if self.scroll_part > 0:
                    self.scroll_part -= 1
                elif self.scroll_row > 0:
                    self.scroll_row -= 1
                    self.scroll_part = (
                        editor_layout.segment_count(
                            lines[self.scroll_row], available
                        )
                        - 1
                    )
                else:
                    break
        top = (self.scroll_row, self.scroll_part)
        self.scroll_row, self.scroll_part = self._clamp_top(
            top, height, width
        )

    def _scroll_view(self, delta: int) -> None:
        """Scroll by wheel rows, carrying the caret and centering it.

        The caret moves through the wrapped text with the wheel, and the
        viewport is set so it lands in the middle row. Near the first and last
        line it slides toward the edge instead of leaving the document.
        """
        if delta == 0 or not self.buffer.lines:
            return
        height, width = (
            window_size(self.stdscr) if self.stdscr is not None else (24, 80)
        )
        available = self._wrap_width(width)
        target = editor_layout.move_display_rows(
            self.buffer.lines,
            self.buffer.cursor_row,
            self.buffer.cursor_col,
            delta,
            available,
        )
        if target != (self.buffer.cursor_row, self.buffer.cursor_col):
            self.buffer.set_cursor(*target)
        self._center_caret(height, width)

    def _buffer_position(self, y: int, x: int) -> Optional[Tuple[int, int]]:
        """Translate a screen cell in the edit view to a buffer position."""
        if self.stdscr is None:
            return None
        height, width = window_size(self.stdscr)
        top = 2
        rows = self._content_rows(height)
        if y < top or y >= top + rows:
            return None
        segments = self._visible_segments(height, width)
        index = y - top
        if index < 0 or index >= len(segments):
            return None
        segment = segments[index]
        gutter = self._gutter_width()
        available = self._wrap_width(width)
        offset = max(0, min(available - 1, x - gutter))
        column = index_for_display_column(
            self.buffer.lines[segment.row], segment.start_cell + offset
        )
        return segment.row, column

    def _drag_position(self, y: int, x: int) -> Optional[Tuple[int, int]]:
        """Map a drag cell to the buffer, scrolling when the pointer leaves."""
        if self.stdscr is None or self.buffer.line_count == 0:
            return None
        height, _width = window_size(self.stdscr)
        top = 2
        rows = self._content_rows(height)
        if y < top:
            self._shift_view(-1)
            y = top
        elif y >= top + rows:
            self._shift_view(1)
            y = top + rows - 1
        return self._buffer_position(y, x)
