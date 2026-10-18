"""Curses directory picker with fuzzy filtering."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

import curses
import curses.ascii

from fuzzy_surgical.colors import Theme
from fuzzy_surgical.directory_navigator import DirEntry, DirectoryNavigator
from fuzzy_surgical.mouse import (
    MOUSE_KEY,
    SCROLL_LINES,
    ClickTracker,
    MouseEvent,
    read_mouse,
)
from fuzzy_surgical.renderer import fill_row, safe_addstr, shorten, window_size
from fuzzy_surgical.utils import (
    display_path,
    is_backspace,
    is_enter,
    printable_char,
    read_key,
)


class DirectoryPicker:
    """Fuzzy directory browser that is safe to run from any start folder."""

    def __init__(
        self,
        stdscr,
        theme: Theme,
        start_dir: Optional[Path],
        ignore_names: Optional[Sequence[str]] = None,
    ) -> None:
        self.stdscr = stdscr
        self.theme = theme
        self.nav = DirectoryNavigator(start_dir, ignore_names)
        self.query = ""
        self.selected = 0
        self.scroll = 0
        self.entries: List[DirEntry] = []
        self.click_tracker = ClickTracker()
        self._refresh()

    def run(self) -> Optional[Path]:
        while True:
            self._draw()
            key = read_key(self.stdscr)
            if key is None:
                continue

            if key == curses.ascii.ESC:
                return None
            if key in (24, 17, curses.KEY_F10):  # Ctrl+X, Ctrl+Q, F10
                return None
            if key == curses.KEY_RESIZE:
                continue
            if key == MOUSE_KEY:
                event = read_mouse(self.stdscr)
                if event is not None:
                    chosen = self._handle_mouse(event)
                    if chosen is not None:
                        return chosen
                continue
            if key == curses.KEY_UP:
                self._move(-1)
            elif key == curses.KEY_DOWN:
                self._move(1)
            elif key == curses.KEY_PPAGE:
                self._move(-max(1, self._visible_rows()))
            elif key == curses.KEY_NPAGE:
                self._move(max(1, self._visible_rows()))
            elif key == curses.KEY_HOME:
                self.selected = 0
                self._ensure_visible()
            elif key == curses.KEY_END:
                self.selected = max(0, len(self.entries) - 1)
                self._ensure_visible()
            elif is_enter(key):
                chosen = self._activate()
                if chosen is not None:
                    return chosen
            elif is_backspace(key):
                if self.query:
                    self.query = self.query[:-1]
                    self._refresh(select_first_child=True)
                else:
                    self.nav.go_parent()
                    self.query = ""
                    self._refresh()
            elif key == 21:  # Ctrl+U
                self.query = ""
                self._refresh()
            else:
                char = printable_char(key)
                if char:
                    self.query += char
                    self._refresh(select_first_child=True)

    def _refresh(self, select_first_child: bool = False) -> None:
        self.entries = self.nav.list_entries(self.query)
        self.selected = 0
        if select_first_child and self.query:
            nav_rows = 1 + (1 if self.nav.has_parent() else 0)
            if len(self.entries) > nav_rows:
                self.selected = nav_rows
        self._ensure_visible()

    def _activate(self) -> Optional[Path]:
        if not self.entries:
            return self.nav.cwd
        self.selected = max(0, min(self.selected, len(self.entries) - 1))
        entry = self.entries[self.selected]

        if entry.kind == "current":
            return self.nav.cwd
        if entry.kind == "parent":
            self.nav.go_parent()
            self.query = ""
            self._refresh()
            return None
        if self.nav.change(entry.path):
            self.query = ""
            self._refresh()
        return None

    def _move(self, delta: int) -> None:
        if not self.entries:
            return
        self.selected = max(0, min(len(self.entries) - 1, self.selected + delta))
        self._ensure_visible()

    def _handle_mouse(self, event: MouseEvent) -> Optional[Path]:
        if event.is_scroll:
            self._move(event.direction * SCROLL_LINES)
            return None
        top = 4
        offset = event.y - top
        if offset < 0 or offset >= self._visible_rows():
            return None
        index = self.scroll + offset
        if index < 0 or index >= len(self.entries):
            return None
        self.selected = index
        self._ensure_visible()
        if self.click_tracker.classify(event) == "double":
            return self._activate()
        return None

    def _visible_rows(self) -> int:
        height, _ = window_size(self.stdscr)
        return max(1, height - 6)

    def _ensure_visible(self) -> None:
        visible = self._visible_rows()
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + visible:
            self.scroll = self.selected - visible + 1

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
                "Terminal too small for fuzzy-surgical. Resize to continue.",
                self.theme.attr("ui.error", bold=True),
            )
            self.stdscr.refresh()
            return

        safe_addstr(
            self.stdscr,
            0,
            0,
            " fuzzy-surgical ",
            self.theme.attr("ui.accent", bold=True),
        )
        if width >= 52:
            safe_addstr(
                self.stdscr,
                0,
                width - 24,
                " choose a directory ",
                self.theme.attr("ui.accent"),
            )

        path_text = display_path(self.nav.cwd)
        safe_addstr(self.stdscr, 1, 0, "Path: ", self.theme.attr("ui.lineno", bold=True))
        safe_addstr(
            self.stdscr,
            1,
            6,
            shorten(path_text, width - 8),
            self.theme.attr("ui.path"),
        )

        safe_addstr(self.stdscr, 2, 0, "Filter: ", self.theme.attr("ui.accent", bold=True))
        safe_addstr(self.stdscr, 2, 8, self.query[: width - 9])
        try:
            self.stdscr.move(2, min(8 + len(self.query), width - 2))
        except curses.error:
            pass

        safe_addstr(
            self.stdscr,
            3,
            0,
            "-" * max(0, width - 1),
            self.theme.attr("ui.dim", dim=True),
        )

        top = 4
        bottom = height - 3
        visible = max(1, bottom - top + 1)
        self._ensure_visible()

        if not self.entries:
            safe_addstr(
                self.stdscr,
                top,
                0,
                "No directories here. Use Backspace to go up.",
                self.theme.attr("ui.dim", dim=True),
            )

        end = min(len(self.entries), self.scroll + visible)
        for index in range(self.scroll, end):
            row = top + (index - self.scroll)
            entry = self.entries[index]
            selected = index == self.selected
            base = curses.A_REVERSE if selected else 0
            if selected:
                fill_row(self.stdscr, row, base, width)

            if entry.kind == "current":
                marker = "."
                label = f"{marker}  {entry.label}"
                attr = self.theme.attr("ui.accent", bold=True) | base
            elif entry.kind == "parent":
                label = f".. {entry.label}"
                attr = self.theme.attr("ui.dim", dim=True) | base
            else:
                label = f"{entry.label}/"
                attr = self.theme.attr("ui.path") | base

            safe_addstr(self.stdscr, row, 1, label[: width - 2], attr)

        nav_rows = 1 + (1 if self.nav.has_parent() else 0)
        if self.query and len(self.entries) <= nav_rows:
            message_row = top + len(self.entries)
            if message_row < height - 2:
                safe_addstr(
                    self.stdscr,
                    message_row,
                    1,
                    "No matching folders. Enter searches this folder.",
                    self.theme.attr("ui.dim", dim=True),
                )

        if self.nav.truncated:
            safe_addstr(
                self.stdscr,
                height - 2,
                0,
                "Showing the first 5000 folders. Type to filter.",
                self.theme.attr("ui.dim", dim=True),
            )
        elif self.nav.error:
            safe_addstr(
                self.stdscr,
                height - 2,
                0,
                self.nav.error[: width - 1],
                self.theme.attr("ui.error"),
            )

        left = " Enter open  Backspace up  Esc cancel  type to filter "
        right = f" {len(self.entries)} items "
        padding = max(0, width - len(left) - len(right))
        bar = (left + " " * padding + right)[: width - 1]
        safe_addstr(
            self.stdscr,
            height - 1,
            0,
            bar,
            self.theme.attr("ui.status"),
        )
        self.stdscr.refresh()
