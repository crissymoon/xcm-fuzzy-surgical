"""Right-click context menu shared by the editor views.

The menu is drawn inside the existing curses window and stays a plain model:
the caller owns the actions. Nothing blocks on input, so the editor keeps its
single read loop and the menu simply consumes the next keys and clicks while
it is open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from fuzzy_surgical.renderer import draw_box, safe_addstr, shorten

MIN_WIDTH = 18
MAX_WIDTH = 46


@dataclass(frozen=True)
class MenuItem:
    """One selectable action."""

    action: str
    label: str
    hint: str = ""
    enabled: bool = True
    key: str = ""


class ContextMenu:
    """A small popup list opened at the pointer."""

    def __init__(self) -> None:
        self.items: List[MenuItem] = []
        self.index = 0
        self.visible = False
        self.x = 0
        self.y = 0
        self.box_top = 0
        self.box_left = 0
        self.box_width = 0
        self.box_height = 0

    # -- lifecycle -----------------------------------------------------
    def open(
        self,
        x: int,
        y: int,
        items: Sequence[MenuItem],
        screen_height: int,
        screen_width: int,
    ) -> bool:
        """Show the menu near (x, y), flipping it when it would overflow."""
        self.items = list(items)
        self.visible = bool(self.items)
        if not self.visible:
            return False
        self.x = max(0, x)
        self.y = max(0, y)
        self.index = self._first_enabled()
        if self.index < 0:
            self.visible = False
            return False
        self._place(screen_height, screen_width)
        return True

    def close(self) -> None:
        self.visible = False
        self.items = []

    # -- geometry ------------------------------------------------------
    def _content_width(self) -> int:
        widest = 0
        for item in self.items:
            text = len(item.label)
            if item.hint:
                text += 2 + len(item.hint)
            widest = max(widest, text)
        return widest

    def _place(self, screen_height: int, screen_width: int) -> None:
        content = self._content_width()
        width = max(MIN_WIDTH, min(MAX_WIDTH, content + 4))
        width = max(4, min(width, max(4, screen_width - 1)))
        height = len(self.items) + 2
        height = max(3, min(height, max(3, screen_height - 1)))
        left = min(self.x + 1, max(0, screen_width - width))
        top = self.y + 1
        if top + height > screen_height:
            top = max(0, self.y - height + 1)
        self.box_left = max(0, left)
        self.box_top = max(0, top)
        self.box_width = width
        self.box_height = height

    def contains(self, x: int, y: int) -> bool:
        """Return True when a cell falls inside the menu box."""
        return (
            self.visible
            and self.box_left <= x < self.box_left + self.box_width
            and self.box_top <= y < self.box_top + self.box_height
        )

    def item_at(self, x: int, y: int) -> Optional[MenuItem]:
        """Return the enabled item under a cell, or None."""
        if not self.contains(x, y):
            return None
        offset = y - self.box_top - 1
        if offset < 0 or offset >= len(self.items):
            return None
        item = self.items[offset]
        return item if item.enabled else None

    # -- selection -----------------------------------------------------
    def _first_enabled(self) -> int:
        for index, item in enumerate(self.items):
            if item.enabled:
                return index
        return -1

    def _next_enabled(self, start: int, direction: int) -> int:
        count = len(self.items)
        if not count:
            return -1
        index = start
        for _ in range(count):
            if 0 <= index < count and self.items[index].enabled:
                return index
            index = (index + direction) % count
        return -1

    def move(self, direction: int) -> None:
        if direction == 0:
            return
        target = self._next_enabled(self.index + direction, direction)
        if target >= 0:
            self.index = target

    def selected_item(self) -> Optional[MenuItem]:
        if not self.visible or not self.items:
            return None
        if 0 <= self.index < len(self.items) and self.items[self.index].enabled:
            return self.items[self.index]
        return None

    def select_matching(self, letter: str) -> bool:
        """Select the item whose shortcut letter matches, for keyboard use."""
        wanted = (letter or "").lower()
        if not wanted:
            return False
        for index, item in enumerate(self.items):
            if item.enabled and item.key.lower() == wanted:
                self.index = index
                return True
        return False

    # -- drawing -------------------------------------------------------
    def draw(self, win, theme, screen_height: int, screen_width: int) -> None:
        if not self.visible or not self.items:
            return
        width = max(4, min(self.box_width, screen_width))
        height = max(3, min(self.box_height, screen_height))
        top = max(0, min(self.box_top, screen_height - height))
        left = max(0, min(self.box_left, screen_width - width))
        base = theme.attr("ui.overlay")
        draw_box(win, top, left, height, width, "Actions", theme, base)
        inner = max(1, width - 4)
        label_width = max((len(item.label) for item in self.items), default=0)
        label_width = min(label_width, max(1, inner - 1))
        for offset, item in enumerate(self.items):
            row = top + 1 + offset
            if row >= top + height - 1:
                break
            selected = offset == self.index and item.enabled
            if selected:
                attr = theme.attr("ui.selection", bold=True)
            elif not item.enabled:
                attr = theme.attr("ui.dim", dim=True)
            else:
                attr = base
            label = item.label[:label_width]
            padding = " " * max(1, label_width - len(label) + 1)
            hint_width = max(0, inner - label_width - 1)
            hint = item.hint[:hint_width] if item.hint else ""
            line = label + padding + hint.rjust(hint_width)
            safe_addstr(win, row, left + 1, " " + line[:inner] + " ", attr)
        if height < len(self.items) + 2:
            safe_addstr(
                win,
                top + height - 1,
                left + 2,
                shorten(" more ", width - 4),
                base,
            )


__all__ = ["ContextMenu", "MenuItem"]
