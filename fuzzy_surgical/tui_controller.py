"""Main curses interface: fuzzy search, directory picker and editing."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import curses
import curses.ascii

from fuzzy_surgical import __version__
from fuzzy_surgical.colors import Theme
from fuzzy_surgical.devbox import DevBoxSession, load_devbox_config
from fuzzy_surgical.directory_picker import DirectoryPicker
from fuzzy_surgical.editor_controller import EditorController
from fuzzy_surgical.file_browser import FileBrowser
from fuzzy_surgical.file_writer import file_digest
from fuzzy_surgical.full_editor import EditorOpenError, FullEditor
from fuzzy_surgical.fuzzy_matcher import FuzzyMatcher
from fuzzy_surgical.history import SnapshotStore
from fuzzy_surgical.indexer import FileIndexer
from fuzzy_surgical.line_jump import LineJumpResolver
from fuzzy_surgical.mouse import (
    MOUSE_KEY,
    ScrollGate,
    ClickTracker,
    disable_mouse,
    enable_mouse,
    read_mouse,
)
from fuzzy_surgical.query_parser import LineReference, parse_line_reference
from fuzzy_surgical.renderer import (
    draw_code_line,
    draw_box,
    fill_row,
    match_offset,
    middle_shorten,
    safe_addstr,
    sanitize,
    shorten,
    window_size,
)
from fuzzy_surgical.utils import (
    disable_flow_control,
    display_path,
    is_backspace,
    is_enter,
    printable_char,
    read_key,
    relative_path,
    restore_terminal_mode,
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
CONTENT_CHECK_INTERVAL_SECONDS = 3.0


class TUIController:
    """Coordinates the result view, editor and directory picker."""

    def __init__(
        self,
        indexer: FileIndexer,
        root_dir: Optional[Path] = None,
        ignore_dirs: Optional[Sequence[str]] = None,
        extensions: Optional[Sequence[str]] = None,
        history: Optional[SnapshotStore] = None,
        devbox_config: Optional[str] = None,
        enable_lsp: bool = True,
        enable_linters: bool = True,
    ) -> None:
        self.indexer = indexer
        self.root_dir = root_dir
        self.ignore_dirs = list(ignore_dirs) if ignore_dirs is not None else None
        self.extensions = list(extensions) if extensions is not None else None
        self.history = history
        self.devbox_config = devbox_config
        self.enable_lsp = enable_lsp
        self.enable_linters = enable_linters
        self.devbox: Optional[DevBoxSession] = None

        self.matcher = FuzzyMatcher()
        self.jump_resolver = LineJumpResolver()
        self.editor = EditorController(history=history)
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
        self.last_content_check = 0.0
        self.status_notice = ""
        self.status_notice_until = 0.0
        self.saved_path = ""
        self.saved_digest = ""
        self.saved_at = 0.0
        self.termios_state = None
        self.click_tracker = ClickTracker()
        self.scroll_gate = ScrollGate()

    def run(self, start_dir: Optional[Path] = None) -> None:
        """Start curses, choosing a directory first when none was supplied."""
        try:
            curses.wrapper(self._run, start_dir)
        except KeyboardInterrupt:
            pass
        finally:
            restore_terminal_mode(self.termios_state)

    def _run(self, stdscr, start_dir: Optional[Path]) -> None:
        self.termios_state = disable_flow_control()
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
        enable_mouse(stdscr)

        try:
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
        finally:
            self._close_devbox()
            disable_mouse()

    def _load_directory(self, path: Path) -> None:
        self.root_dir = path
        self.query = ""
        self._close_devbox()
        self.devbox = self._build_devbox(path)
        self._reload_index()

    def _build_devbox(self, path: Path) -> Optional[DevBoxSession]:
        if not self.enable_lsp and not self.enable_linters:
            return None
        try:
            config = load_devbox_config(path, self.devbox_config)
            return DevBoxSession(
                path,
                config=config,
                enable_lsp=self.enable_lsp,
                enable_linters=self.enable_linters,
            )
        except Exception as exc:
            self.load_error = f"Dev box disabled: {exc}"
            return None

    def _close_devbox(self) -> None:
        session = self.devbox
        self.devbox = None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

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
        target = self._selected_target()
        try:
            changed = self.indexer.reindex_changed()
        except Exception:
            return
        if target is not None and now - self.last_content_check >= CONTENT_CHECK_INTERVAL_SECONDS:
            self.last_content_check = now
            try:
                path = target[0]
                before = self.indexer.file_lines.get(path)
                self.indexer.reindex_file(path)
                after = self.indexer.file_lines.get(path)
                if before != after and path not in changed:
                    changed.append(path)
            except Exception:
                pass
        if not changed:
            return
        self.file_lines = self.indexer.file_lines
        self._update_results(preserve=target)
        if target is not None and target[0] in changed:
            name = relative_path(target[0], self.root_dir)
            self._set_notice(f"Reloaded {name} from disk", 3.0)
        self._check_saved_file(changed)

    def _check_saved_file(self, changed: List[str]) -> None:
        """Warn when another program rewrites a file right after a save."""
        if not self.saved_path:
            return
        if time.monotonic() - self.saved_at > 20.0:
            self.saved_path = ""
            self.saved_digest = ""
            return
        if self.saved_path not in changed:
            return
        digest = file_digest(self.saved_path)
        if digest and self.saved_digest and digest != self.saved_digest:
            name = display_path(Path(self.saved_path))
            self._set_notice(f"Another program changed {name} after the save", 10.0)
            self.saved_path = ""
            self.saved_digest = ""

    def _set_notice(self, text: str, seconds: float = 6.0) -> None:
        """Show a short lived message in the status bar."""
        self.status_notice = text
        self.status_notice_until = time.monotonic() + seconds

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
                try:
                    self._refresh_changed_files()
                except Exception:
                    pass
                continue

            if self.help_visible:
                self.help_visible = False
                continue

            if key == curses.KEY_RESIZE:
                continue
            if key == MOUSE_KEY:
                self._handle_mouse()
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
            elif key == curses.KEY_F3:
                self._open_browser()
            elif key == curses.KEY_F4:
                self._open_full_editor()
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

    def _handle_mouse(self) -> None:
        event = read_mouse(self.stdscr)
        if event is None:
            return
        if event.is_scroll:
            delta = self.scroll_gate.lines(event.direction)
            if delta:
                self._move_selection(delta)
            return

        top = 3
        offset = event.y - top
        if offset < 0 or offset >= self._visible_rows():
            return
        index = self.scroll_offset + offset
        if index < 0 or index >= len(self.results):
            return
        self.selected_idx = index
        self._ensure_visible()
        if self.click_tracker.classify(event) == "double":
            self._open_editor()

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
            f" fuzzy-surgical {__version__} ",
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
        if self.results:
            index = max(0, min(self.selected_idx, len(self.results) - 1))
            label = display_path(Path(self.results[index][0]))
            label = shorten(label, max(0, width - 4))
            safe_addstr(
                self.stdscr,
                2,
                max(0, width - len(label) - 2),
                " " + label + " ",
                self.theme.attr("ui.path", dim=True),
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
            left = " Enter edit  F3 files  F4 editor  Esc quit "
        else:
            left = (
                " Enter edit  F3 files  F4 file editor  F2 directory  "
                "F5 reload  F1 help  Esc quit "
            )
        if self.results:
            position = self.selected_idx + 1
            right = f" {position}/{len(self.results)} "
        else:
            right = " 0/0 "
        if self.status_notice and time.monotonic() < self.status_notice_until:
            left = " " + middle_shorten(self.status_notice, max(10, width - len(right) - 2)) + " "
        elif self.jump_active and self.jump_notice and self.results:
            left = " " + middle_shorten(self.jump_notice, max(10, width - len(right) - 2)) + " "
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
            "Mouse click    select a row, double click to edit it",
            "Mouse wheel    scroll three rows",
            "Enter          edit the selected line",
            "F3             browse folders and edit whole files",
            "F4             open the selected file in the full editor",
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
            saved = self.editor.last_save
            if saved is not None:
                line = self.editor.last_save_line or line_num
                name = display_path(Path(saved.path))
                message = f"Saved {name}:{line} ({saved.bytes_written} bytes, verified)"
                if saved.inode_replaced:
                    message += " - inode replaced"
                self._set_notice(message, 10.0)
                self.saved_path = file_path
                self.saved_digest = saved.digest
                self.saved_at = time.monotonic()
        self.last_refresh = time.monotonic()
        self._update_results(preserve=(file_path, line_num))

    def _open_browser(self) -> None:
        """Browse the indexed tree and edit whole files."""
        preserve = self._selected_target()
        try:
            changed = FileBrowser(
                self.stdscr,
                self.theme,
                self.root_dir or safe_start_directory(),
                history=self.history,
                ignore_names=self.ignore_dirs,
                extensions=self.extensions,
                devbox=self.devbox,
            ).run()
        except Exception as exc:  # the browser must never take down the search
            self._set_notice(f"File browser failed: {exc}", 8.0)
            return
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        if changed:
            self._refresh_after_save(changed, preserve)
        else:
            self._update_results(preserve=preserve)

    def _open_full_editor(self) -> None:
        """Open the selected file in the whole-file editor."""
        if not self.results:
            self._set_notice("Search for a line first, then press F4.", 6.0)
            return
        index = max(0, min(self.selected_idx, len(self.results) - 1))
        file_path, line_num = self.results[index][:2]
        preserve = (file_path, line_num)
        try:
            self.indexer.reindex_file(file_path)
        except Exception:
            pass
        self.file_lines = self.indexer.file_lines
        try:
            saved = FullEditor(
                self.stdscr, self.theme, self.history, devbox=self.devbox
            ).edit(Path(file_path))
        except EditorOpenError as exc:
            self._update_results(preserve=preserve)
            self._set_notice(str(exc), 8.0)
            return
        except (OSError, curses.error) as exc:
            self._update_results(preserve=preserve)
            self._set_notice(f"Could not open that file: {exc}", 8.0)
            return
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        if saved:
            self._refresh_after_save(file_path, preserve)
        else:
            self._update_results(preserve=preserve)

    def _refresh_after_save(self, file_path: str, preserve: Optional[Tuple[str, int]]) -> None:
        """Reindex one saved file and keep the search selection stable."""
        try:
            self.indexer.reindex_file(file_path)
        except Exception:
            pass
        self.file_lines = self.indexer.file_lines
        self.last_refresh = time.monotonic()
        self._update_results(preserve=preserve)
        name = display_path(Path(file_path))
        self._set_notice(f"Saved {name}", 8.0)
