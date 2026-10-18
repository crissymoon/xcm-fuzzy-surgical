"""Issues panel for linter and language server diagnostics."""

from __future__ import annotations

from typing import List, Optional, Sequence

import curses

from fuzzy_surgical.colors import Theme
from fuzzy_surgical.lsp.diagnostics import Diagnostic, count_by_severity
from fuzzy_surgical.renderer import draw_box, safe_addstr, sanitize, window_size

MAX_VISIBLE = 12
SEVERITY_LABEL = {"error": "E", "warning": "W", "info": "I", "hint": "H"}


class DiagnosticPanel:
    """Selection and drawing for the F9 issues list."""

    def __init__(self) -> None:
        self.items: List[Diagnostic] = []
        self.visible = False
        self.selected = 0
        self._scroll = 0

    def set_items(self, items: Sequence[Diagnostic]) -> None:
        self.items = list(items)
        if self.items:
            self.selected = max(0, min(self.selected, len(self.items) - 1))
        else:
            self.selected = 0
            self._scroll = 0
            self.visible = False

    def toggle(self) -> None:
        self.visible = bool(self.items) and not self.visible

    def close(self) -> None:
        self.visible = False

    def move(self, delta: int) -> None:
        if not self.items:
            return
        self.selected = max(0, min(len(self.items) - 1, self.selected + delta))
        self._ensure_visible()

    def selected_diagnostic(self) -> Optional[Diagnostic]:
        if not self.items:
            return None
        return self.items[max(0, min(self.selected, len(self.items) - 1))]

    def _visible_rows(self, height: int) -> int:
        return max(1, min(MAX_VISIBLE, height // 2 - 2))

    def _ensure_visible(self) -> None:
        rows = min(MAX_VISIBLE, len(self.items))
        if self.selected < self._scroll:
            self._scroll = self.selected
        elif self.selected >= self._scroll + rows:
            self._scroll = self.selected - rows + 1

    def draw(self, win, theme: Theme, status: str = "") -> None:
        """Draw the issues panel across the lower part of the window."""
        if not self.visible or not self.items:
            return
        height, width = window_size(win)
        if height < 8 or width < 30:
            return

        rows = self._visible_rows(height)
        box_height = min(rows + 2, height - 3)
        top = max(2, height - box_height - 1)
        box_width = max(20, width - 2)
        left = max(0, (width - box_width) // 2)
        counts = count_by_severity(self.items)
        title = (
            f"Issues: {counts['error']} errors, {counts['warning']} warnings, "
            f"{counts['info']} info"
        )
        overlay = theme.attr("ui.overlay")
        draw_box(win, top, left, box_height, box_width, title, theme, overlay)

        self._ensure_visible()
        window_rows = max(1, box_height - 2)
        end = min(len(self.items), self._scroll + window_rows)
        for offset, index in enumerate(range(self._scroll, end), 1):
            item = self.items[index]
            label = SEVERITY_LABEL.get(item.severity, "I")
            source = sanitize(item.source)[:14]
            message = sanitize(item.message)
            available = max(10, box_width - len(source) - 4)
            prefix = f" {item.line:>5}:{item.column:<3} {label} "
            line = (prefix + message)[:available].ljust(available) + source + " "
            attr = overlay
            if index == self.selected:
                attr = theme.attr("ui.accent", bold=True) | curses.A_REVERSE
            elif item.severity == "error":
                attr = theme.attr("ui.error")
            elif item.severity == "warning":
                attr = theme.attr("ui.lineno")
            safe_addstr(win, top + offset, left + 1, line[: box_width - 2], attr)

        footer = " Enter jumps to the line  Up/Down select  F9 close "
        if status:
            footer = " " + status
        safe_addstr(
            win,
            top + box_height - 1,
            left + 1,
            footer[: box_width - 2],
            overlay | curses.A_DIM,
        )
