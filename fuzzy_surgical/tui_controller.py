"""Main curses interface: fuzzy search, directory picker and editing."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import curses
import curses.ascii

from fuzzy_surgical.colors import Theme
from fuzzy_surgical.directory_picker import DirectoryPicker
from fuzzy_surgical.editor_controller import EditorController
from fuzzy_surgical.fuzzy_matcher import FuzzyMatcher
from fuzzy_surgical.indexer import FileIndexer
from fuzzy_surgical.line_jump import LineJumpResolver
from fuzzy_surgical.query_parser import LineReference, parse_line_reference
from fuzzy_surgical.renderer import (
    draw_code_line,
    draw_box,
    fill_row,
    match_offset,
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
    relative_path,
    safe_start_directory,
)

CTRL_O = 15
CTRL_Q = 17
CTRL_U = 21
CTRL_X = 24

DIRECTORY_KEYS = (curses.KEY_F2, CTRL_O)
QUIT_KEYS = (curses.KEY_F10, CTRL_X, CTRL_Q)
REFRESH_KEY = getattr(curses, "KEY_F5", -1)
REFRESH_TIMEOUT_MS = 250
REFRESH_INTERVAL_SECONDS = 1.5


class TUIController:
    """Coordinates the result view, editor and directory picker."""

    def __init__(
        self,
        indexer: FileIndexer,
        root_dir: Optional[Path] = None,
        ignore_dirs: Optional[Sequence[str]] = None,
        extensions: Optional[Sequence[str]] = None,
    ) -> None:
        self.indexer = indexer
        self.root_dir = root_dir
        self.ignore_dirs = list(ignore_dirs) if ignore_dirs is not None else None
        self.extensions = list(extensions) if extensions is not None else None

        self.matcher = FuzzyMatcher()
        self.jump_resolver = LineJumpResolver()
        self.editor = EditorController()
        self.theme = Theme()

        self.file_lines: Dict[str, List[tuple]] = {}
        self.query = ""
        self.results: List[Tuple[str, int, str, float]] = []
        self.selected_idx = 0
        self.scroll_offset = 0
        self.x_offset = 0
        self.load_error = ""
        self.jump_notice = ""
        self.jump_active = False
        self.help_visible = False
        self.last_refresh = 0.0

    def run(self, start_dir: Optional[Path] = None) -> None:
        """Start curses, choosing a directory first when none was supplied."""
        try:
            curses.wrapper(self._run, start_dir)
        except KeyboardInterrupt:
            pass

    def _run(self, stdscr, start_dir: Optional[Path]) -> None:
        self.stdscr = stdscr
        self.theme.init()
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            stdscr.keypad(True)
        except curses.error:
            pass

        root = self.root_dir
        if root is None:
            picker = DirectoryPicker(
                stdscr,
                self.theme,
                start_dir or safe_start_directory(),
                self.ignore_dirs,
            )
            root = picker.run()
            if root is None:
                return

        self._load_directory(root)
        self._search_loop()

    def _load_directory(self, path: Path) -> None:
        self.root_dir = path
        self.query = ""
        self._reload_index()

    def _reload_index(self) -> None:
        """Rebuild the index for the current root, keeping the query."""
        path = self.root_dir
        if path is None:
            return
        self._draw_loading(path)
        preserve = self._selected_target()
        self.load_error = ""
        try:
            self.file_lines = self.indexer.index_directory(
                path,
                ignore_dirs=self.ignore_dirs,
                extensions=self.extensions,
            )
        except Exception as exc:  # filesystem errors must never kill the TUI
            self.file_lines = {}
            self.load_error = str(exc)

        self.last_refresh = time.monotonic()
        self._update_results(preserve=preserve)

    def _selected_target(self) -> Optional[Tuple[str, int]]:
        if not self.results:
            return None
        index = max(0, min(self.selected_idx, len(self.results) - 1))
        file_path, line_num = self.results[index][:2]
        return file_path, line_num

    def _refresh_changed_files(self) -> None:
        """Pick up edits made by other programs while the search view is idle."""
        now = time.monotonic()
        if now - self.last_refresh < REFRESH_INTERVAL_SECONDS:
            return
        self.last_refresh = now
        try:
            changed = self.indexer.reindex_changed()
        except Exception:
            return
        if not changed:
            return
        self.file_lines = self.indexer.file_lines
        self._update_results(preserve=self._selected_target())

    def _read_search_key(self):
        """Read a key, timing out so external file changes can be noticed."""
        try:
            self.stdscr.timeout(REFRESH_TIMEOUT_MS)
        except curses.error:
            return read_key(self.stdscr)
        try:
            return read_key(self.stdscr)
        finally:
            try:
                self.stdscr.timeout(-1)
            except curses.error:
                pass

    def _draw_loading(self, path: Path) -> None:
        self.stdscr.erase()
        height, width = window_size(self.stdscr)
        if height == 0:
            return
        safe_addstr(
            self.stdscr,
            0,
            0,
            " fuzzy-surgical ",
            self.theme.attr("ui.accent", bold=True),
        )
        safe_addstr(
            self.stdscr,
            2,
            0,
            f"Indexing {shorten(display_path(path), max(10, width - 12))} ...",
            self.theme.attr("ui.path", bold=True),
        )
        safe_addstr(
            self.stdscr,
            3,
            0,
            "Large folders are capped for safety. Ctrl+C cancels.",
            self.theme.attr("ui.dim", dim=True),
        )
        self.stdscr.refresh()

    def _search_loop(self) -> None:
        while True:
            self._draw_ui()
            key = self._read_search_key()
            if key is None:
                self._refresh_changed_files()
                continue

            if self.help_visible:
                self.help_visible = False
                continue

            if key == curses.KEY_RESIZE:
                continue
            if key == curses.ascii.ESC:
                if self.query:
                    self.query = ""
                    self._update_results()
                else:
                    return
            elif key in QUIT_KEYS:
                return
            elif key in DIRECTORY_KEYS:
                chosen = DirectoryPicker(
                    self.stdscr,
                    self.theme,
                    self.root_dir or safe_start_directory(),
                    self.ignore_dirs,
                ).run()
                if chosen is not None:
                    self._load_directory(chosen)
            elif key == CTRL_U:
                self.query = ""
                self._update_results()
            elif key == curses.KEY_F1:
                self.help_visible = True
            elif key == REFRESH_KEY:
                self._reload_index()
            elif is_enter(key):
                self._open_editor()
            elif key == curses.KEY_UP:
                self._move_selection(-1)
            elif key == curses.KEY_DOWN:
                self._move_selection(1)
            elif key == curses.KEY_PPAGE:
                self._move_selection(-self._visible_rows())
            elif key == curses.KEY_NPAGE:
                self._move_selection(self._visible_rows())
            elif key == curses.KEY_HOME:
                self.selected_idx = 0
                self.x_offset = 0
                self._ensure_visible()
            elif key == curses.KEY_END:
                self.selected_idx = max(0, len(self.results) - 1)
                self._ensure_visible()
            elif key == curses.KEY_LEFT:
                self.x_offset = max(0, self.x_offset - 8)
            elif key == curses.KEY_RIGHT:
                self.x_offset += 8
            elif is_backspace(key):
                if self.query:
                    self.query = self.query[:-1]
                    self._update_results()
            else:
                char = printable_char(key)
                if char:
                    self.query += char
                    self._update_results()

    def _update_results(self, preserve: Optional[Tuple[str, int]] = None) -> None:
        """Re-run the query and keep the previous line selected when possible."""
        self.jump_notice = ""
        self.jump_active = False
        reference = parse_line_reference(self.query)
        if reference is not None and self._apply_jump(reference, preserve):
            return

        try:
            self.results = self.matcher.search_lines(self.file_lines, self.query)
        except Exception:
            self.results = []
        self.selected_idx = 0
        self.scroll_offset = 0
        self.x_offset = 0

        if preserve is None:
            return
        for index, (file_path, line_num, _, _) in enumerate(self.results):
            if file_path == preserve[0] and line_num == preserve[1]:
                self.selected_idx = index
                visible = self._visible_rows()
                self.scroll_offset = max(0, self.selected_idx - visible + 1)
                return

    def _apply_jump(
        self,
        reference: LineReference,
        preserve: Optional[Tuple[str, int]],
    ) -> bool:
        """Show the referenced file and select its requested line."""
        try:
            jump = self.jump_resolver.resolve(
                reference,
                self.file_lines,
                self.root_dir,
                prefer_file=preserve[0] if preserve else None,
                prefer_line=preserve[1] if preserve else None,
            )
        except Exception:
            jump = None

        if jump is None:
            self.jump_notice = f"No indexed file matches '{reference.path}'."
            return False

        self.results = jump.results
        self.jump_active = True
        self.selected_idx = self._selected_index_for(jump.selected_line)
        visible = self._visible_rows()
        self.scroll_offset = max(0, self.selected_idx - visible // 2)
        self.x_offset = 0
        if jump.notice:
            self.jump_notice = jump.notice
        return True

    def _selected_index_for(self, line_num: int) -> int:
        for index, (_, number, _, _) in enumerate(self.results):
            if number == line_num:
                return index
        return 0

    def _move_selection(self, delta: int) -> None:
        if not self.results:
            return
        self.selected_idx = max(
            0,
            min(len(self.results) - 1, self.selected_idx + delta),
        )
        self.x_offset = 0
        self._ensure_visible()

    def _visible_rows(self) -> int:
        stdscr = getattr(self, "stdscr", None)
        if stdscr is None:
            return 10
        height, _ = window_size(stdscr)
        return max(1, height - 4)

    def _ensure_visible(self) -> None:
        visible = self._visible_rows()
        if self.selected_idx < self.scroll_offset:
            self.scroll_offset = self.selected_idx
        elif self.selected_idx >= self.scroll_offset + visible:
            self.scroll_offset = self.selected_idx - visible + 1

    def _draw_ui(self) -> None:
        self.stdscr.erase()
        height, width = window_size(self.stdscr)
        if height == 0 or width == 0:
            return

        if height < 8 or width < 30:
            safe_addstr(
                self.stdscr,
                0,
                0,
                "Terminal too small for fuzzy-surgical. Resize or Ctrl+X.",
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
        root_label = display_path(self.root_dir or Path.cwd())
        safe_addstr(
            self.stdscr,
            0,
            max(0, width - len(root_label) - 2),
            shorten(root_label, max(0, width - 20)),
            self.theme.attr("ui.dim", dim=True),
        )

        safe_addstr(self.stdscr, 1, 0, "Search: ", self.theme.attr("ui.accent", bold=True))
        safe_addstr(self.stdscr, 1, 8, self.query[: width - 9])
        try:
            self.stdscr.move(1, min(8 + len(self.query), width - 2))
        except curses.error:
            pass

        safe_addstr(
            self.stdscr,
            2,
            0,
            "-" * max(0, width - 1),
            self.theme.attr("ui.dim", dim=True),
        )

        top = 3
        bottom = height - 2
        visible = max(1, bottom - top + 1)
        self._ensure_visible()
        self._draw_results(top, visible, width)
        self._draw_status(height, width)

        if self.help_visible:
            self._draw_help(height, width)

        self.stdscr.refresh()

    def _draw_results(self, top: int, visible: int, width: int) -> None:
        if not self.results:
            if self.load_error:
                message = f"Could not read that folder: {self.load_error}"
                attr = self.theme.attr("ui.error")
            elif self.jump_notice:
                message = self.jump_notice
                attr = self.theme.attr("ui.error")
            elif not self.file_lines:
                message = "No readable text files found. F2 chooses another folder."
                attr = self.theme.attr("ui.dim", dim=True)
            elif self.query:
                message = f"No matches for '{self.query}'. Esc clears the search."
                attr = self.theme.attr("ui.dim", dim=True)
            else:
                message = "Start typing to search."
                attr = self.theme.attr("ui.dim", dim=True)
            safe_addstr(self.stdscr, top, 0, message[: width - 1], attr)
            return

        prefix_budget = min(max(14, width // 2), max(10, width - 14))
        end = min(len(self.results), self.scroll_offset + visible)
        for index in range(self.scroll_offset, end):
            row = top + (index - self.scroll_offset)
            file_path, line_num, line_text, _ = self.results[index]
            selected = index == self.selected_idx
            base = curses.A_REVERSE if selected else 0
            if selected:
                fill_row(self.stdscr, row, base, width)

            path = relative_path(file_path, self.root_dir)
            path = shorten(path, prefix_budget)
            safe_addstr(
                self.stdscr,
                row,
                0,
                path,
                self.theme.attr("ui.path") | base,
            )

            number_text = f":{line_num}"
            number_x = min(len(path), max(0, width - 2))
            safe_addstr(
                self.stdscr,
                row,
                number_x,
                number_text,
                self.theme.attr("ui.lineno") | base,
            )

            code_x = number_x + len(number_text) + 1
            code_width = width - code_x - 1
            if code_width <= 0:
                continue

            positions = []
            display_text = sanitize(line_text)
            row_offset = self.x_offset
            if self.query and not self.jump_active:
                _, _, positions = self.matcher.fuzzy_match(self.query, display_text)
                row_offset += match_offset(positions, code_width)

            draw_code_line(
                self.stdscr,
                row,
                code_x,
                display_text,
                file_path,
                self.theme,
                highlight_positions=positions,
                base_attr=base,
                max_width=code_width,
                x_offset=row_offset,
            )

    def _draw_status(self, height: int, width: int) -> None:
        if width < 72:
            left = " Enter edit  F2 dirs  F5 reload  Esc quit "
        else:
            left = " Enter edit  F2 directory  F5 reload  F1 help  Esc quit  Ctrl+X quit "
        if self.results:
            position = self.selected_idx + 1
            right = f" {position}/{len(self.results)} "
        else:
            right = " 0/0 "
        if self.jump_active and self.jump_notice and self.results:
            left = " " + shorten(self.jump_notice, max(10, width - len(right) - 2)) + " "
        padding = max(0, width - len(left) - len(right))
        bar = (left + " " * padding + right)[: width - 1]
        safe_addstr(
            self.stdscr,
            height - 1,
            0,
            bar,
            self.theme.attr("ui.status"),
        )

    def _draw_help(self, height: int, width: int) -> None:
        lines = [
            "Type           fuzzy search every indexed line",
            "file.py:12     jump to that line in a matching file",
            "Up / Down      move selection",
            "PgUp / PgDn    jump one page",
            "Left / Right   scroll long code lines",
            "Enter          edit the selected line",
            "F2             choose another directory",
            "F5             reload changed files from disk",
            "Ctrl+U         clear the search",
            "Esc            clear search, then quit",
            "Ctrl+X / F10   quit",
            "F1             close this help",
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
            "Controls",
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

    def _open_editor(self) -> None:
        if not self.results:
            return
        index = max(0, min(self.selected_idx, len(self.results) - 1))
        file_path, line_num, _, _ = self.results[index]

        # Refresh this file first so external edits are visible and any
        # conflict is caught against the current disk contents.
        self.indexer.reindex_file(file_path)
        self.file_lines = self.indexer.file_lines
        lines = self.file_lines.get(file_path) or []
        if not lines:
            self.jump_notice = "That file is no longer readable. F5 reloads the index."
            self._update_results()
            return

        line_num = max(1, min(line_num, len(lines)))
        line_text = lines[line_num - 1][1]
        context = self.indexer.get_file_context(file_path, line_num, context_lines=8)

        match_column = None
        if self.query:
            found = line_text.lower().find(self.query.lower())
            if found >= 0:
                match_column = found

        modified = self.editor.edit_line(
            stdscr=self.stdscr,
            file_path=file_path,
            line_num=line_num,
            original_line=line_text,
            context_lines=context,
            theme=self.theme,
            match_column=match_column,
        )

        if modified:
            self.indexer.reindex_file(file_path)
            self.file_lines = self.indexer.file_lines
        self.last_refresh = time.monotonic()
        self._update_results(preserve=(file_path, line_num))
