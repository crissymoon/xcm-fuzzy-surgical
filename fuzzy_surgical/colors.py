"""Centralised curses colour theme.

One registry owns every colour pair so the editor, picker and result view
never collide on pair ids.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import curses

ColorSpec = Tuple[int, int]

# (foreground, background) for terminals with 8/16 colours.
_BASIC: Dict[str, ColorSpec] = {
    "keyword": (curses.COLOR_MAGENTA, -1),
    "string": (curses.COLOR_GREEN, -1),
    "comment": (curses.COLOR_CYAN, -1),
    "number": (curses.COLOR_YELLOW, -1),
    "function": (curses.COLOR_BLUE, -1),
    "class": (curses.COLOR_CYAN, -1),
    "builtin": (curses.COLOR_YELLOW, -1),
    "constant": (curses.COLOR_MAGENTA, -1),
    "decorator": (curses.COLOR_YELLOW, -1),
    "operator": (curses.COLOR_WHITE, -1),
    "ui.path": (curses.COLOR_CYAN, -1),
    "ui.lineno": (curses.COLOR_YELLOW, -1),
    "ui.accent": (curses.COLOR_MAGENTA, -1),
    "ui.dim": (curses.COLOR_WHITE, -1),
    "ui.error": (curses.COLOR_RED, -1),
    "ui.success": (curses.COLOR_GREEN, -1),
    "ui.status": (curses.COLOR_BLACK, curses.COLOR_CYAN),
    "ui.overlay": (curses.COLOR_BLACK, curses.COLOR_WHITE),
    "ui.selection": (curses.COLOR_BLACK, curses.COLOR_CYAN),
    "ui.find": (curses.COLOR_BLACK, curses.COLOR_YELLOW),
    "edit.active": (curses.COLOR_CYAN, -1),
    "edit.changed": (curses.COLOR_GREEN, -1),
    "edit.original": (curses.COLOR_RED, -1),
}

# 256-colour variants. The palette stays readable on dark and light themes.
_EXTENDED: Dict[str, ColorSpec] = {
    "keyword": (39, -1),
    "string": (114, -1),
    "comment": (244, -1),
    "number": (179, -1),
    "function": (81, -1),
    "class": (213, -1),
    "builtin": (37, -1),
    "constant": (173, -1),
    "decorator": (179, -1),
    "operator": (250, -1),
    "ui.path": (81, -1),
    "ui.lineno": (179, -1),
    "ui.accent": (213, -1),
    "ui.dim": (244, -1),
    "ui.error": (203, -1),
    "ui.success": (114, -1),
    "ui.status": (0, 51),
    "ui.overlay": (0, 252),
    "ui.selection": (16, 153),
    "ui.find": (16, 178),
    "edit.active": (81, -1),
    "edit.changed": (114, -1),
    "edit.original": (203, -1),
}


class Theme:
    """Initialises and resolves curses attributes by semantic category."""

    def __init__(self) -> None:
        self.enabled = False
        self.extended = False
        self._pairs: Dict[str, int] = {}
        self._next_pair = 1

    def init(self) -> None:
        """Start colours. Safe to call once inside curses.wrapper."""
        try:
            if not curses.has_colors():
                return
            curses.start_color()
            try:
                curses.use_default_colors()
            except curses.error:
                pass
        except curses.error:
            return

        self.enabled = True
        try:
            self.extended = curses.COLORS >= 256
        except curses.error:
            self.extended = False

        palette = _EXTENDED if self.extended else _BASIC
        for category, (fg, bg) in palette.items():
            if self._next_pair >= getattr(curses, "COLOR_PAIRS", 64):
                break
            pair = self._next_pair
            try:
                curses.init_pair(pair, fg, bg)
            except curses.error:
                continue
            self._pairs[category] = pair
            self._next_pair += 1

    def pair(self, category: str) -> int:
        return self._pairs.get(category, 0)

    def attr(
        self,
        category: Optional[str] = None,
        *,
        bold: bool = False,
        dim: bool = False,
        underline: bool = False,
        reverse: bool = False,
    ) -> int:
        """Build a curses attribute for a semantic category."""
        value = 0
        if category:
            pair = self._pairs.get(category)
            if pair:
                value |= curses.color_pair(pair)
        if bold:
            value |= curses.A_BOLD
        if dim:
            value |= curses.A_DIM
        if underline:
            value |= curses.A_UNDERLINE
        if reverse:
            value |= curses.A_REVERSE
        return value
