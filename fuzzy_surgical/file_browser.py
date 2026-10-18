"""Curses file browser that opens the full file editor.

The browser is a lazy, per-folder view. Enter descends into folders or opens
a file, Backspace walks back up, and typing fuzzy filters the current folder.
Nothing here recurses, so pointing it at a desktop or documents folder is safe.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

import curses
import curses.ascii

from fuzzy_surgical.browser_navigator import BrowserEntry, FileBrowserNavigator
from fuzzy_surgical.colors import Theme
from fuzzy_surgical.devbox import DevBoxSession
from fuzzy_surgical.full_editor import EditorOpenError, FullEditor
from fuzzy_surgical.history import SnapshotStore
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
    fill_row,
    safe_addstr,
    shorten,
    window_size,
)
from fuzzy_surgical.utils import (
    display_path,
    format_file_size,
    is_backspace,
    is_enter,
    printable_char,
    read_key,
)

CTRL_U = 21
CTRL_Q = 17
CTRL_X = 24


class FileBrowser:
    """Browse folders and files, then edit a file in place."""

    def __init__(
        self,
        stdscr,
        theme: Theme,
        start_dir: Optional[Path],
        history: Optional[SnapshotStore] = None,
        ignore_names: Optional[Sequence[str]] = None,
        extensions: Optional[Sequence[str]] = None,
        devbox: Optional[DevBoxSession] = None,
    ) -> None:
        self.stdscr = stdscr
        self.theme = theme
        self.history = history
        self.devbox = devbox
        self.nav = FileBrowserNavigator(start_dir, ignore_names, extensions)
        self.query = ""
        self.selected = 0
        self.scroll = 0
        self.entries: List[BrowserEntry] = []
        self.error = ""
        self.notice = ""
        self.help_visible = False
        self.changed_path = ""
        self.click_tracker = ClickTracker()
        self.scroll_gate = ScrollGate()
        self._refresh()

    def run(self) -> Optional[str]:
        """Return the last saved path, or None when nothing was written."""
        reset_input_state()
        while True:
            self._draw()
            key = read_key(self.stdscr)
            if key is None:
                continue
            if self.help_visible:
                self.help_visible = False
                continue
            if key == curses.KEY_RESIZE:
                continue
            if key == MOUSE_KEY:
                event = read_mouse(self.stdscr)
                if event is not None:
                    self._handle_mouse(event)
                continue
            if key == curses.ascii.ESC or key in (CTRL_X, CTRL_Q, curses.KEY_F10):
                return self.changed_path or None
            if key == curses.KEY_UP:
                self._move(-1)
            elif key == curses.KEY_DOWN:
                self._move(1)
            elif key == curses.KEY_PPAGE:
                self._move(-self._visible_rows())
            elif key == curses.KEY_NPAGE:
                self._move(self._visible_rows())
            elif key == curses.KEY_HOME:
                self.selected = 0
                self._ensure_visible()
            elif key == curses.KEY_END:
                self.selected = max(0, len(self.entries) - 1)
                self._ensure_visible()
            elif key == curses.KEY_F1:
                self.help_visible = True
            elif is_enter(key):
                self._activate()
            elif is_backspace(key):
                if self.query:
                    self.query = self.query[:-1]
                    self._refresh(select_first_child=True)
                else:
                    self._go_parent()
            elif key == CTRL_U:
                self.query = ""
                self._refresh()
            else:
                char = printable_char(key)
                if char:
                    self.query += char
                    self._refresh(select_first_child=True)

    def _refresh(self, select_first_child: bool = False) -> None:
        self.entries = self.nav.list_entries(self.query)
        if select_first_child and self.query:
            nav_rows = self.nav.navigation_rows(self.query)
            if len(self.entries) > nav_rows:
                self.selected = nav_rows
            else:
                self.selected = 0
        else:
            self.selected = min(self.selected, max(0, len(self.entries) - 1))
        self._ensure_visible()

    def _go_parent(self) -> None:
        if self.nav.go_parent():
            self.query = ""
            self.selected = 0
            self.scroll = 0
            self._refresh()

    def _activate(self) -> None:
        if not self.entries:
            return
        self.selected = max(0, min(self.selected, len(self.entries) - 1))
        entry = self.entries[self.selected]
        self.error = ""
        self.notice = ""

        if entry.kind == "parent":
            self._go_parent()
            return
        if entry.kind == "dir":
            if self.nav.change(entry.path):
                self.query = ""
                self.selected = 0
                self.scroll = 0
                self._refresh()
            else:
                self.error = self.nav.error
            return
        self._edit_file(entry)

    def _edit_file(self, entry: BrowserEntry) -> None:
        try:
            saved = FullEditor(
                self.stdscr, self.theme, self.history, devbox=self.devbox
            ).edit(entry.path)
        except EditorOpenError as exc:
            self.error = str(exc)
            return
        except (OSError, curses.error) as exc:
            self.error = "Could not open that file: " + str(exc)
            return
        if saved:
            self.changed_path = str(entry.path)
            self.notice = "Saved " + shorten(entry.label, 40)
            try:
                curses.curs_set(1)
            except curses.error:
                pass
        self._refresh()

    def _move(self, delta: int) -> None:
        if not self.entries:
            return
        self.selected = max(0, min(len(self.entries) - 1, self.selected + delta))
        self._ensure_visible()

    def _handle_mouse(self, event: MouseEvent) -> None:
        if event.is_scroll:
            delta = self.scroll_gate.lines(event.direction)
            if delta:
                self._move(delta)
            return
        top = 4
        offset = event.y - top
        if offset < 0 or offset >= self._visible_rows():
            return
        index = self.scroll + offset
        if index < 0 or index >= len(self.entries):
            return
        self.selected = index
        self._ensure_visible()
        if self.click_tracker.classify(event) == "double":
            self._activate()

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
                "Terminal too small for the file browser. Resize or press Esc.",
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
        if width >= 44:
            safe_addstr(
                self.stdscr,
                0,
                width - 20,
                " browse and edit ",
                self.theme.attr("ui.accent"),
            )

        path_text = display_path(self.nav.cwd)
        safe_addstr(self.stdscr, 1, 0, "Path: ", self.theme.attr("ui.lineno", bold=True))
        safe_addstr(
            self.stdscr,
            1,
            6,
            shorten(path_text, max(0, width - 8)),
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
            message = self.nav.error or "No files or folders here. Backspace goes up."
            safe_addstr(
                self.stdscr,
                top,
                0,
                message[: width - 1],
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

            if entry.kind == "parent":
                label = ".. up one level"
                attr = self.theme.attr("ui.dim", dim=True) | base
                safe_addstr(self.stdscr, row, 1, label[: width - 2], attr)
                continue

            if entry.kind == "dir":
                label = entry.label + "/"
                attr = self.theme.attr("ui.path", bold=True) | base
            else:
                label = entry.label
                attr = self.theme.attr("ui.lineno") | base
            safe_addstr(self.stdscr, row, 1, label[: max(0, width - 14)], attr)

            if entry.kind == "file" and width >= 50:
                size = f" {format_file_size(entry.size)} "
                safe_addstr(
                    self.stdscr,
                    row,
                    max(0, width - len(size) - 1),
                    size,
                    self.theme.attr("ui.dim", dim=True) | base,
                )

        if self.nav.truncated:
            safe_addstr(
                self.stdscr,
                height - 2,
                0,
                "Showing the first 5000 entries. Type to filter."[: width - 1],
                self.theme.attr("ui.dim", dim=True),
            )
        elif self.error:
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

        if self.help_visible:
            self._draw_help(height, width)

        if width < 72:
            left = " Enter open  Backspace up  Esc back "
        else:
            left = " Enter open  Backspace up  Esc back  F1 help  type to filter "
        right = f" {len(self.entries)} items "
        padding = max(0, width - len(left) - len(right))
        bar = (left + " " * padding + right)[: width - 1]
        safe_addstr(self.stdscr, height - 1, 0, bar, self.theme.attr("ui.status"))
        self.stdscr.refresh()

    def _draw_help(self, height: int, width: int) -> None:
        lines = [
            "Type           fuzzy filter this folder",
            "Up / Down      move the selection",
            "PgUp / PgDn    move one page",
            "Mouse click    select a row, double click to open it",
            "Mouse wheel    scroll four rows",
            "Enter          open a folder or edit a file",
            "Backspace      delete filter text, or go up a folder",
            "Ctrl+U         clear the filter",
            "Esc            return to the search view",
            "F1             close this help",
            "",
            "Editing a file opens the full file editor.",
            "Ctrl+S there writes to disk and F6 lists snapshots.",
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
            "File browser controls",
            self.theme,
            overlay,
        )
        for offset, line in enumerate(lines, 1):
            safe_addstr(self.stdscr, top + offset, left + 2, line[: box_width - 4], overlay)
