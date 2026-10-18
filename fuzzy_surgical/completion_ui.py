"""Completion popup state and drawing for the full editor."""

from __future__ import annotations

from typing import List, Optional, Sequence

import curses

from fuzzy_surgical.colors import Theme
from fuzzy_surgical.lsp.completion import CompletionItem, filter_completions
from fuzzy_surgical.renderer import draw_box, safe_addstr, sanitize, window_size

MAX_VISIBLE = 9
MIN_WIDTH = 24


class CompletionPopup:
    """Holds the request lifecycle, filtering and selection of completions."""

    def __init__(self) -> None:
        self.items: List[CompletionItem] = []
        self.filtered: List[CompletionItem] = []
        self.selected = 0
        self.visible = False
        self.requested = False
        self.sequence = 0
        self.prefix = ""
        self.prefix_start = 0
        self.error = ""
        self._scroll = 0

    def open(self, prefix_start: int, prefix: str, sequence: int) -> None:
        self.requested = True
        self.visible = False
        self.items = []
        self.filtered = []
        self.selected = 0
        self._scroll = 0
        self.prefix_start = max(0, prefix_start)
        self.prefix = prefix
        self.sequence = sequence
        self.error = ""

    def close(self) -> None:
        self.requested = False
        self.visible = False
        self.items = []
        self.filtered = []
        self.selected = 0
        self._scroll = 0
        self.error = ""

    def set_result(self, sequence: int, items: Sequence[CompletionItem]) -> bool:
        """Apply a result when it belongs to the active request."""
        if not self.requested or sequence != self.sequence:
            return False
        self.items = list(items)
        self.refine(self.prefix)
        return True

    def refine(self, prefix: str) -> None:
        self.prefix = prefix
        self.filtered = filter_completions(self.items, prefix)
        self.selected = max(0, min(self.selected, max(0, len(self.filtered) - 1)))
        self.visible = self.requested and bool(self.filtered)
        if not self.filtered:
            self._scroll = 0

    def move(self, delta: int) -> None:
        if not self.filtered:
            return
        self.selected = max(0, min(len(self.filtered) - 1, self.selected + delta))
        self._ensure_visible()

    def selected_item(self) -> Optional[CompletionItem]:
        if not self.filtered:
            return None
        return self.filtered[max(0, min(self.selected, len(self.filtered) - 1))]

    def _ensure_visible(self) -> None:
        rows = min(MAX_VISIBLE, len(self.filtered))
        if rows <= 0:
            self._scroll = 0
            return
        if self.selected < self._scroll:
            self._scroll = self.selected
        elif self.selected >= self._scroll + rows:
            self._scroll = self.selected - rows + 1

    def _detail_lines(self, width: int) -> List[str]:
        """Return up to two lines of type and documentation for the selection."""
        item = self.selected_item()
        if item is None:
            return []
        parts = []
        if item.detail.strip():
            parts.append(item.detail)
        if item.documentation.strip():
            text = " ".join(item.documentation.split())
            if text:
                parts.append(text)
        if not parts:
            parts.append(item.kind)
        limit = max(8, width - 4)
        lines = []
        for part in parts:
            text = sanitize(part).replace("\n", " ").strip()
            if text:
                lines.append(text[:limit])
            if len(lines) >= 2:
                break
        return lines

    def draw(
        self,
        win,
        theme: Theme,
        row: int,
        column: int,
        max_rows: int,
        max_width: int,
    ) -> None:
        """Draw the popup near a screen cell, always staying inside the window."""
        if not self.visible or not self.filtered:
            return
        height, width = window_size(win)
        if height < 4 or width < MIN_WIDTH:
            return

        box_width = min(max_width, width - 2, 76)
        box_width = max(MIN_WIDTH, box_width)

        detail = self._detail_lines(box_width)
        detail_height = len(detail) + 1 if detail and max_rows >= 6 else 0
        available_rows = max(1, min(MAX_VISIBLE, max_rows - detail_height))
        rows = min(available_rows, len(self.filtered))
        box_height = rows + 2 + detail_height

        left = max(0, min(column, width - box_width - 1))
        below = row + 1
        if below + box_height <= height - 2:
            top = below
        else:
            top = max(1, row - box_height)
        if top + box_height > height - 1:
            top = max(1, height - box_height - 1)

        overlay = theme.attr("ui.overlay")
        title = f"Completions ({len(self.filtered)})"
        draw_box(win, top, left, box_height, box_width, title, theme, overlay)
        safe_addstr(
            win,
            top + 1,
            left + 1,
            " " * (box_width - 2),
            overlay,
        )

        self._ensure_visible()
        end = min(len(self.filtered), self._scroll + rows)
        name_width = min(34, max(10, box_width - 24))
        for offset, index in enumerate(range(self._scroll, end), 1):
            item = self.filtered[index]
            active = index == self.selected
            kind = item.kind[:10]
            label = sanitize(item.label)[:name_width].ljust(name_width)
            detail_text = sanitize(item.detail)[: max(0, box_width - name_width - 18)]
            line = f" {label} {kind:<11} {detail_text}"
            line = line[: box_width - 2].ljust(box_width - 2)
            attr = overlay
            if active:
                attr = theme.attr("ui.accent", bold=True) | curses.A_REVERSE
            safe_addstr(win, top + offset, left + 1, line, attr)

        if detail_height:
            separator_row = top + 1 + rows
            safe_addstr(
                win,
                separator_row,
                left + 1,
                "-" * (box_width - 2),
                overlay,
            )
            for index, text in enumerate(detail, 1):
                safe_addstr(
                    win,
                    separator_row + index,
                    left + 2,
                    text[: max(0, box_width - 4)],
                    theme.attr("ui.dim", dim=True),
                )

        footer = " Enter/Tab insert  Esc close "
        safe_addstr(
            win,
            top + box_height - 1,
            left + max(1, box_width - len(footer) - 1),
            footer[: box_width - 2],
            overlay,
        )
