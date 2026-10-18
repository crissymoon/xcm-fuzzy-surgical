"""Mouse, drag selection and Actions menu for the whole-file editor."""

from __future__ import annotations

from typing import Optional, Tuple

import curses
import curses.ascii

from fuzzy_surgical.context_menu import MenuItem
from fuzzy_surgical.mouse import MouseEvent
from fuzzy_surgical.renderer import index_for_display_column, sanitize, window_size
from fuzzy_surgical.utils import is_enter, printable_char

SPACE = 32


class EditorMouseMixin:
    """Translate clicks, drags, wheel ticks and menu keys into edits."""

    def _open_context_menu_at_caret(self) -> None:
        """Open the Actions menu beside the caret, for keyboard-only use."""
        if self.stdscr is None:
            return
        height, width = window_size(self.stdscr)
        gutter = self._gutter_width()
        row = 2 + (self.buffer.cursor_row - self.scroll_row)
        line = self.buffer.lines[self.buffer.cursor_row]
        column = len(sanitize(line[: self.buffer.cursor_col]))
        x = max(1, min(width - 2, gutter + column - self.x_offset))
        y = max(1, min(height - 2, row))
        self._open_context_menu(x, y)

    def _open_context_menu(self, x: int, y: int) -> None:
        """Show the right-click menu at the pointer."""
        height, width = window_size(self.stdscr) if self.stdscr is not None else (24, 80)
        selected = self.selection.has_text
        items = [
            MenuItem("cut", "Cut", "Ctrl+X", selected, "x"),
            MenuItem("copy", "Copy", "Ctrl+C", selected, "c"),
            MenuItem("paste", "Paste", "Ctrl+V", True, "v"),
            MenuItem(
                "select_all",
                "Select all",
                "Ctrl+A",
                self.buffer.line_count > 0,
                "a",
            ),
            MenuItem(
                "undo", "Undo", "Ctrl+Z", bool(self.buffer.undo_stack), "u"
            ),
            MenuItem(
                "redo", "Redo", "Ctrl+Y", bool(self.buffer.redo_stack), "r"
            ),
            MenuItem("save", "Save", "Ctrl+S", self.buffer.dirty, "s"),
        ]
        self.completion.close()
        self.diagnostics_panel.close()
        self.menu.open(x, y, items, height, width)

    def _handle_menu_key(self, key) -> bool:
        if key == curses.KEY_UP:
            self.menu.move(-1)
            return False
        if key == curses.KEY_DOWN:
            self.menu.move(1)
            return False
        if key == curses.ascii.ESC:
            self.menu.close()
            return False
        if is_enter(key) or key == SPACE:
            self._activate_menu()
            return False
        letter = printable_char(key)
        if letter:
            if self.menu.select_matching(letter):
                self._activate_menu()
            else:
                self.menu.close()
        return False

    def _activate_menu(self) -> None:
        item = self.menu.selected_item()
        if item is None:
            self.menu.close()
            return
        self._run_menu_action(item.action)

    def _run_menu_action(self, action: str) -> None:
        self.menu.close()
        self.error = ""
        if action == "cut":
            self._cut_selection()
        elif action == "copy":
            self._copy_selection()
        elif action == "paste":
            self._paste_clipboard()
        elif action == "select_all":
            self.selection.select_all(self.buffer.lines)
            self.buffer.set_cursor(
                self.selection.head_row, self.selection.head_col
            )
            self.notice = f"Selected {self.buffer.line_count} lines. Ctrl+C copies."
        elif action == "undo":
            self._undo()
        elif action == "redo":
            self._redo()
        elif action == "save":
            self._save()

    def _handle_mouse(self, event: MouseEvent) -> None:
        """Apply a click, drag, release or wheel tick to the active mode."""
        if self.menu.visible:
            if event.is_scroll:
                self.menu.close()
                self._scroll_mouse(event.direction)
                return
            if event.is_right_click:
                self._open_context_menu(event.x, event.y)
                return
            if event.is_click:
                item = self.menu.item_at(event.x, event.y)
                if item is not None:
                    self._run_menu_action(item.action)
                elif not self.menu.contains(event.x, event.y):
                    self.menu.close()
            return
        if event.is_right_click:
            if self.mode == "edit":
                self._open_context_menu(event.x, event.y)
            elif self.mode in ("saved", "help"):
                # A right click on a result panel is still an editing gesture:
                # return to the buffer and offer the actions there.
                self.mode = "edit"
                self._open_context_menu(event.x, event.y)
            return
        if event.is_scroll:
            self._scroll_mouse(event.direction)
            return

        if self.mode in ("saved", "help"):
            if event.is_click:
                self.mode = "edit"
            return
        if self.mode == "discard":
            if event.is_click:
                self.mode = "edit"
            return
        if self.mode == "history":
            if event.is_click:
                self._click_history(event)
            return
        if self.mode != "edit":
            return

        if event.is_release:
            if self.selection.release():
                text = self.selection.text(self.buffer.lines)
                if text:
                    self.notice = (
                        f"Selected {self._selection_label(text)}. "
                        "Ctrl+C copies, right click or F10 for actions."
                    )
            return
        if event.is_drag:
            if not self.selection.active:
                return
            position = self._drag_position(event.y, event.x)
            if position is not None:
                self.selection.extend(*position)
                self.buffer.set_cursor(*position)
                self._after_cursor_move()
            return
        if not event.is_click:
            return

        position = self._buffer_position(event.y, event.x)
        if position is None:
            return
        row, column = position
        kind = self.click_tracker.classify(event)
        if kind is None:
            return
        self.error = ""
        self.notice = ""
        if event.x < self._gutter_width():
            self.x_offset = 0
        if kind == "triple":
            self.selection.select_line(self.buffer.lines, row)
            self.buffer.set_cursor(row, len(self.buffer.lines[row]))
            self.notice = "Line selected. Ctrl+C copies."
            return
        if kind == "double":
            self.selection.select_word(self.buffer.lines, row, column)
            self.buffer.set_cursor(
                self.selection.head_row, self.selection.head_col
            )
            self.notice = "Word selected. Ctrl+C copies."
            return
        self.selection.start(row, column)
        self.buffer.set_cursor(row, column)

    def _drag_position(self, y: int, x: int):
        """Map a drag cell to the buffer, scrolling when the pointer leaves."""
        if self.stdscr is None or self.buffer.line_count == 0:
            return None
        height, width = window_size(self.stdscr)
        top = 2
        rows = self._content_rows(height)
        if y < top:
            self._scroll_view(-1)
            row = self.scroll_row
        elif y >= top + rows:
            self._scroll_view(1)
            row = self.scroll_row + rows - 1
        else:
            row = self.scroll_row + (y - top)
        row = max(0, min(self.buffer.line_count - 1, row))
        gutter = self._gutter_width()
        available = max(1, width - gutter - 1)
        offset = max(0, min(available - 1, x - gutter))
        display_column = self.x_offset + offset
        line = self.buffer.lines[row]
        return row, index_for_display_column(line, display_column)

    def _scroll_mouse(self, direction: int) -> None:
        delta = self.scroll_gate.lines(direction)
        if delta == 0:
            return
        if self.mode == "edit":
            self._scroll_view(delta)
        elif self.mode == "history":
            self._select_history(self.history_index + delta)

    def _buffer_position(self, y: int, x: int) -> Optional[Tuple[int, int]]:
        """Translate a screen cell in the edit view to a buffer position."""
        if self.stdscr is None:
            return None
        height, width = window_size(self.stdscr)
        top = 2
        rows = self._content_rows(height)
        if y < top or y >= top + rows:
            return None
        row = self.scroll_row + (y - top)
        if row < 0 or row >= self.buffer.line_count:
            return None
        gutter = self._gutter_width()
        available = max(1, width - gutter - 1)
        display_column = self.x_offset + max(0, min(available - 1, x - gutter))
        column = index_for_display_column(self.buffer.lines[row], display_column)
        return row, column

    def _click_history(self, event: MouseEvent) -> None:
        height, _width = window_size(self.stdscr)
        rows = max(1, height - 6)
        box_height = min(height - 1, rows + 4)
        top = max(1, (height - box_height) // 2)
        first_row = top + 2
        offset = event.y - first_row
        if offset < 0 or offset >= rows:
            return
        index = self.history_scroll + offset
        if index < 0 or index >= len(self.history_entries):
            return
        if self.click_tracker.classify(event) == "double":
            self.history_index = index
            self._restore_selected()
        else:
            self._select_history(index)
