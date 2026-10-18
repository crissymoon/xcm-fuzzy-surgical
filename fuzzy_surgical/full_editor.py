"""Full file editor shown inside the existing curses session.

This is the only editor in the application: a scrollable text buffer with a
cursor, syntax colors, undo and redo, SQLite snapshots and a save that
verifies the disk copy. Search results and the file browser both open it
through :func:`edit_file`, so selection, copy and paste behave the same
everywhere.

Capabilities are kept intentionally small and predictable. There is no plugin
system; language support stays optional through the dev box, so the editor is
still a safe in-terminal fix for a file the user just found with the search.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

import curses
import curses.ascii

from fuzzy_surgical import editor_jump
from fuzzy_surgical.colors import Theme
from fuzzy_surgical.completion_ui import CompletionPopup
from fuzzy_surgical.context_menu import ContextMenu
from fuzzy_surgical.devbox import DevBoxSession
from fuzzy_surgical.diagnostics_ui import DiagnosticPanel
from fuzzy_surgical.editor_chords import run_chord
from fuzzy_surgical.editor_mouse import EditorMouseMixin
from fuzzy_surgical.editor_preferences import EditorPreferences
from fuzzy_surgical.editor_scroll import EditorScrollMixin
from fuzzy_surgical.editor_view import EditorViewMixin
from fuzzy_surgical.file_writer import FileWriteError, WriteResult, file_digest, write_text
from fuzzy_surgical.history import SnapshotStore
from fuzzy_surgical.lsp.completion import completion_prefix
from fuzzy_surgical.lsp.protocol import utf16_column
from fuzzy_surgical.mouse import (
    MOUSE_KEY,
    ScrollGate,
    ClickTracker,
    read_mouse,
    reset_input_state,
)
from fuzzy_surgical.renderer import window_size
from fuzzy_surgical.selection import (
    Selection,
    copy_to_clipboard,
    is_truncated_fragment,
    read_clipboard,
)
from fuzzy_surgical.text_buffer import (
    EditorOpenError,
    TextBuffer,
    detect_newline,
    normalize_newlines,
    read_editor_file,
)
from fuzzy_surgical.utils import (
    ChordKey,
    PastedText,
    disable_bracketed_paste,
    disable_interrupt_keys,
    enable_bracketed_paste,
    is_backspace,
    is_enter,
    printable_char,
    read_key,
    restore_terminal_mode,
)

CTRL_SPACE = 0
CTRL_A = 1
CTRL_C = 3
CTRL_G = 7
CTRL_L = 12
CTRL_N = 14
CTRL_S = 19
CTRL_V = 22
CTRL_W = 23
CTRL_X = 24
CTRL_Y = 25
CTRL_Z = 26
TAB = 9
SPACE = 32
COMPLETION_DELAY = 0.18
CLIPBOARD_FALLBACK_SECONDS = 300.0
# A drag can end without a release report on some terminals, and a terminal
# may keep Cmd+C for itself. The editor therefore copies shortly after the
# selection settles and watches the clipboard for a while, restoring the full
# text when the terminal replaces it with a word or another fragment.
AUTO_COPY_DELAY = 0.22
CLIPBOARD_GUARD_SECONDS = 10.0
CLIPBOARD_GUARD_FAST_SECONDS = 2.5
CLIPBOARD_GUARD_FAST_GAP = 0.15
CLIPBOARD_GUARD_SLOW_GAP = 0.6
SHIFT_UP = getattr(curses, "KEY_SR", 337)
SHIFT_DOWN = getattr(curses, "KEY_SF", 336)
SHIFT_LEFT = getattr(curses, "KEY_SLEFT", 393)
SHIFT_RIGHT = getattr(curses, "KEY_SRIGHT", 402)
SHIFT_HOME = getattr(curses, "KEY_SHOME", 391)
SHIFT_END = getattr(curses, "KEY_SEND", 394)

__all__ = [
    "EditorOpenError",
    "FullEditor",
    "TextBuffer",
    "detect_newline",
    "edit_file",
    "normalize_newlines",
    "read_editor_file",
]


def edit_file(
    stdscr,
    theme: Optional[Theme],
    history: Optional[SnapshotStore],
    devbox: Optional[DevBoxSession],
    file_path,
    start_line: int = 0,
    preferences: Optional[EditorPreferences] = None,
) -> bool:
    """Open the one editor surface for a file, optionally at a line.

    The search view and the file browser both go through this function so
    every way into an editor gets the same selection, copy, paste and save
    behavior. ``preferences`` carries choices such as the line number style
    from one opened file to the next.
    """
    return FullEditor(
        stdscr, theme, history, devbox=devbox, preferences=preferences
    ).edit(Path(file_path), start_line=start_line)


def _devbox_advice(devbox, path: str) -> str:
    """Ask the dev box why completion is unavailable, tolerating test doubles."""
    advice = getattr(devbox, "advice_for", None)
    if callable(advice):
        try:
            return str(advice(path))
        except Exception:
            return ""
    return ""


class FullEditor(
    editor_jump.EditorJumpMixin, EditorScrollMixin, EditorMouseMixin, EditorViewMixin
):
    """Scrollable editor for one whole file."""

    def __init__(
        self,
        stdscr,
        theme: Optional[Theme] = None,
        history: Optional[SnapshotStore] = None,
        devbox: Optional[DevBoxSession] = None,
        preferences: Optional[EditorPreferences] = None,
    ) -> None:
        self.stdscr = stdscr
        self.theme = theme if theme is not None else Theme()
        self.history = history
        self.devbox = devbox
        self.preferences = preferences or EditorPreferences()
        self.path = ""
        self.buffer = TextBuffer()
        self.disk_digest = ""
        self.scroll_row = 0
        self.scroll_part = 0
        self._pending_center = False
        self._dragging = False
        self.mode = "edit"
        self.error = ""
        self.notice = ""
        self.saved_any = False
        self.last_save: Optional[WriteResult] = None
        self.history_note = ""
        self.saved_proof = ""
        self.saved_line = 0
        self.history_entries = []
        self.history_index = 0
        self.history_scroll = 0
        self.click_tracker = ClickTracker()
        self.scroll_gate = ScrollGate()
        self.selection = Selection()
        self.completion = CompletionPopup()
        self.menu = ContextMenu()
        self.jump = editor_jump.JumpPrompt()
        self.diagnostics_panel = DiagnosticPanel()
        self.diagnostics = []
        self.assist_status = ""
        self._completion_due_at = 0.0
        self._completion_manual = False
        self._completion_pending = False
        # The exact text this editor last copied. Paste falls back to it when
        # the system clipboard holds only a truncated prefix, which happens
        # when a terminal copies its own stale selection over ours.
        self.clipboard_text = ""
        self.clipboard_at = 0.0
        self._auto_copy_at = 0.0
        self._clipboard_guard_until = 0.0
        self._clipboard_guard_next = 0.0

    def edit(self, file_path: Path, start_line: int = 0) -> bool:
        """Open the file and return True when at least one save happened.

        ``start_line`` places the caret on that one-based line so a search
        result opens straight at the matched code.
        """
        reset_input_state()
        self.path = os.path.abspath(str(file_path))
        self.buffer = TextBuffer(read_editor_file(self.path))
        self.disk_digest = file_digest(self.path)
        if start_line > 0:
            self.buffer.goto_line(start_line)
        self.scroll_row = 0
        self.scroll_part = 0
        self._pending_center = True
        self._dragging = False
        self.mode = "edit"
        self.error = ""
        self.notice = ""
        self.saved_any = False
        self.last_save = None
        self.selection.clear()
        self.completion = CompletionPopup()
        self.menu.close()
        self.jump.close()
        self.diagnostics_panel = DiagnosticPanel()
        self.diagnostics = []
        self._completion_due_at = 0.0
        self._completion_manual = False
        self._completion_pending = False
        self._auto_copy_at = 0.0
        self._clipboard_guard_until = 0.0
        self._clipboard_guard_next = 0.0
        if self.devbox is not None:
            self.devbox.open_document(self.path, self.buffer.to_text())
            self.assist_status = _devbox_advice(self.devbox, self.path) or str(
                getattr(self.devbox, "status", "")
            )
        try:
            curses.curs_set(1)
        except curses.error:
            pass

        self._set_input_timeout(120)
        mode_state = disable_interrupt_keys()
        enable_bracketed_paste()
        try:
            self._draw()
            while True:
                key = read_key(self.stdscr, input_timeout=120)
                if isinstance(key, PastedText):
                    self._insert_paste(key.text)
                    self._draw()
                    continue
                if key is None or key == -1:
                    changed = self._tick_clipboard()
                    if self._poll_assist() or self._completion_due():
                        changed = True
                    if changed:
                        self._draw()
                    continue
                if key == curses.KEY_RESIZE:
                    self._draw()
                    continue
                if key == MOUSE_KEY:
                    event = read_mouse(self.stdscr)
                    if event is not None:
                        self._handle_mouse(event)
                        self._draw()
                    continue
                finished = False
                if self.mode == "edit":
                    finished = self._handle_edit_key(key)
                elif self.mode == "saved":
                    self.mode = "edit"
                elif self.mode == "discard":
                    if key in ("s", "S"):
                        self._save()
                    elif key in ("d", "D"):
                        finished = True
                    else:
                        self.mode = "edit"
                elif self.mode == "history":
                    self._handle_history_key(key)
                elif self.mode == "help":
                    self.mode = "edit"
                if finished:
                    return self.saved_any
                self._draw()
        finally:
            disable_bracketed_paste()
            restore_terminal_mode(mode_state)
            self._set_input_timeout(-1)
            self.completion.close()
            if self.devbox is not None:
                self.devbox.close_document()

    def _set_input_timeout(self, milliseconds: int) -> None:
        if self.stdscr is None:
            return
        try:
            self.stdscr.timeout(milliseconds)
        except (AttributeError, curses.error):
            pass

    def _poll_assist(self) -> bool:
        """Apply background dev box events, returning True when drawing changed."""
        if self.devbox is None:
            return False
        changed = False
        for event in self.devbox.poll():
            kind = event[0] if event else ""
            if kind == "completions":
                sequence = int(event[1])
                if self.completion.set_result(sequence, event[2]):
                    self._completion_pending = False
                    row = self.buffer.cursor_row
                    line = self.buffer.lines[row]
                    start, prefix = completion_prefix(line, self.buffer.cursor_col)
                    self.completion.prefix_start = start
                    self.completion.refine(prefix)
                    if not self.completion.visible:
                        self._report_no_completions()
                    self._completion_manual = False
                    changed = True
            elif kind == "diagnostics":
                self.diagnostics = [
                    item
                    for item in event[2]
                    if os.path.normcase(os.path.abspath(item.path))
                    == os.path.normcase(self.path)
                ]
                self.diagnostics_panel.set_items(self.diagnostics)
                changed = True
            elif kind == "status":
                self.assist_status = str(event[1])
                changed = True
        return changed

    def _completion_due(self) -> bool:
        if self.devbox is None or self._completion_due_at <= 0:
            return False
        if time.monotonic() < self._completion_due_at:
            return False
        self._completion_due_at = 0.0
        if self.completion.requested and self.completion.visible:
            return False
        self._request_completion()
        return True

    def _request_completion(self) -> None:
        if self.devbox is None:
            self.notice = "Language server support is off (--no-lsp)."
            self._completion_manual = False
            self._completion_pending = False
            return
        advice = _devbox_advice(self.devbox, self.path)
        if advice:
            self.notice = advice
            self.assist_status = advice
            self._completion_manual = False
            self._completion_pending = False
            return
        row = self.buffer.cursor_row
        line = self.buffer.lines[row]
        start, prefix = completion_prefix(line, self.buffer.cursor_col)
        sequence = self.devbox.request_completion(
            self.buffer.to_text(), row, utf16_column(line, self.buffer.cursor_col)
        )
        if sequence:
            self.completion.open(start, prefix, sequence)
            self._completion_pending = True
        else:
            self._completion_pending = False
            self._report_no_completions()

    def _report_no_completions(self) -> None:
        self._completion_pending = False
        advice = _devbox_advice(self.devbox, self.path) if self.devbox is not None else ""
        if advice:
            self.notice = advice
        elif self._completion_manual:
            message = "No completions returned for this position."
            error = self.devbox.client_error if self.devbox is not None else ""
            if error:
                message = f"{message} ({error})"
            self.notice = message
        self._completion_manual = False

    def _after_edit(self) -> None:
        if self.devbox is None:
            return
        row = self.buffer.cursor_row
        line = self.buffer.lines[row]
        start, prefix = completion_prefix(line, self.buffer.cursor_col)
        if self.completion.requested and self.completion.visible:
            self.completion.prefix_start = start
            self.completion.refine(prefix)
        if len(prefix) < 2:
            return
        self._completion_due_at = time.monotonic() + COMPLETION_DELAY

    def _accept_completion(self) -> None:
        item = self.completion.selected_item()
        if item is None:
            return
        row = self.buffer.cursor_row
        start = min(self.completion.prefix_start, self.buffer.cursor_col)
        self.buffer.replace_span(row, start, self.buffer.cursor_col, item.insert_text)
        self.completion.close()
        self._completion_due_at = 0.0
        self.error = ""

    def _jump_to_diagnostic(self) -> None:
        item = self.diagnostics_panel.selected_diagnostic()
        if item is None:
            return
        self.buffer.goto_line(item.line)
        self._pending_center = True
        self.diagnostics_panel.close()
        self._after_cursor_move()
        self.notice = f"Line {item.line}: {item.message[:60]}"

    def _handle_edit_key(self, key) -> bool:
        self.error = ""
        if self.menu.visible:
            return self._handle_menu_key(key)
        if isinstance(key, ChordKey):
            return run_chord(self, key)
        if self.jump.active:
            self._handle_jump_key(key)
            return False
        if key == CTRL_C:
            self._copy_selection()
            return False
        if key == CTRL_X:
            self._cut_selection()
            return False
        if key == CTRL_V:
            self._paste_clipboard()
            return False
        if key == CTRL_A:
            self._select_all()
            return False
        if key == curses.ascii.ESC and self.selection.active:
            self.selection.clear()
            self.notice = "Selection cleared."
            return False
        if self.completion.visible:
            if key == curses.KEY_UP:
                self.completion.move(-1)
                return False
            if key == curses.KEY_DOWN:
                self.completion.move(1)
                return False
            if is_enter(key) or key == TAB:
                self._accept_completion()
                return False
            if key == curses.ascii.ESC:
                self.completion.close()
                return False
        if self.diagnostics_panel.visible:
            if key == curses.KEY_UP:
                self.diagnostics_panel.move(-1)
                return False
            if key == curses.KEY_DOWN:
                self.diagnostics_panel.move(1)
                return False
            if is_enter(key):
                self._jump_to_diagnostic()
                return False
            if key in (curses.ascii.ESC, curses.KEY_F9, CTRL_L):
                self.diagnostics_panel.close()
                return False

        if key in (CTRL_SPACE, CTRL_N) or key == curses.KEY_F12:
            self._completion_manual = True
            self._request_completion()
            return False
        if key in (curses.KEY_F9, CTRL_L):
            self.diagnostics_panel.toggle()
            if not self.diagnostics_panel.visible and not self.diagnostics:
                self.notice = "No issues reported for this file yet."
            return False
        if key == curses.KEY_F10:
            # Terminals such as Apple Terminal never pass a right click on,
            # so the same Actions menu is reachable from the keyboard.
            self._open_context_menu_at_caret()
            return False

        edited = False
        if self.selection.has_text:
            if is_backspace(key) or key == curses.KEY_DC:
                self._delete_selection()
                return False
            if self._key_inserts_text(key):
                self._delete_selection()
        if key == curses.ascii.ESC:
            if self.buffer.dirty:
                self.mode = "discard"
            else:
                return True
        elif key in (CTRL_S, CTRL_W) or key == curses.KEY_F2:
            self._save()
        elif key == curses.KEY_F7:
            self._toggle_line_numbers()
        elif key == CTRL_G:
            self._open_jump_prompt()
        elif key == curses.KEY_F6:
            self._open_history()
        elif key == curses.KEY_F8:
            self._reload()
            self._after_edit()
        elif key == curses.KEY_F1:
            self.mode = "help"
        elif key == CTRL_Z:
            self.buffer.undo()
            self._after_cursor_move()
            self._after_edit()
        elif key == CTRL_Y:
            self.buffer.redo()
            self._after_cursor_move()
            self._after_edit()
        elif is_enter(key):
            self.buffer.insert_newline()
            self._after_cursor_move()
            edited = True
        elif key == curses.KEY_UP:
            self._move_display(-1)
        elif key == curses.KEY_DOWN:
            self._move_display(1)
        elif key == SHIFT_LEFT:
            self._extend_selection("left")
        elif key == SHIFT_RIGHT:
            self._extend_selection("right")
        elif key == SHIFT_UP:
            self._extend_selection("up")
        elif key == SHIFT_DOWN:
            self._extend_selection("down")
        elif key == SHIFT_HOME:
            self._extend_selection("home")
        elif key == SHIFT_END:
            self._extend_selection("end")
        elif key == curses.KEY_LEFT:
            self.buffer.move_left()
            self.completion.close()
        elif key == curses.KEY_RIGHT:
            self.buffer.move_right()
            self.completion.close()
        elif key == curses.KEY_HOME:
            self.buffer.move_home()
            self.completion.close()
        elif key == curses.KEY_END:
            self.buffer.move_end()
            self.completion.close()
        elif key == curses.KEY_PPAGE:
            self._move_display(-self._page_size())
            self.completion.close()
        elif key == curses.KEY_NPAGE:
            self._move_display(self._page_size())
            self.completion.close()
        elif key == curses.KEY_DC:
            self.buffer.delete()
            edited = True
        elif is_backspace(key):
            self.buffer.backspace()
            edited = True
        elif key == TAB:
            self.buffer.insert_text("    ")
            edited = True
        else:
            char = printable_char(key)
            if char:
                self.buffer.insert_text(char)
                edited = True
        if self.selection.active:
            self.selection.clear()
            self._auto_copy_at = 0.0
        if edited:
            self._after_edit()
        return False

    def _copy_selection(self) -> None:
        """Copy the selected text, reporting the result in the status line."""
        if not self.selection.has_text:
            self.notice = (
                "Nothing selected. Drag with the mouse, double click a word, "
                "or press Ctrl+A."
            )
            return
        text = self.selection.text(self.buffer.lines)
        result = copy_to_clipboard(text)
        if result.ok:
            self._remember_clipboard(text)
            self.error = ""
            self.notice = result.message
        else:
            self.error = result.message

    def _remember_clipboard(self, text: str) -> None:
        """Keep the exact copied text and watch for a terminal truncation."""
        now = time.monotonic()
        self.clipboard_text = text
        self.clipboard_at = now
        self._auto_copy_at = 0.0
        self._clipboard_guard_until = now + CLIPBOARD_GUARD_SECONDS
        self._clipboard_guard_next = now + CLIPBOARD_GUARD_FAST_GAP

    def _schedule_auto_copy(self) -> None:
        """Copy once the selection stops changing, without waiting for release."""
        if self.selection.has_text:
            self._auto_copy_at = time.monotonic() + AUTO_COPY_DELAY

    def _clipboard_is_fresh(self) -> bool:
        return (
            bool(self.clipboard_text)
            and time.monotonic() - self.clipboard_at <= CLIPBOARD_FALLBACK_SECONDS
        )

    @staticmethod
    def _clipboard_was_truncated(system_text: str, copied_text: str) -> bool:
        """Return True when the system clipboard holds only part of a copy."""
        return is_truncated_fragment(system_text, copied_text)

    def _tick_clipboard(self) -> bool:
        """Settle pending copies and repair a clipboard the terminal truncated.

        Terminals that keep Cmd+C for themselves can replace the editor's copy
        with a stale word at any moment while a selection is visible. Watching
        the clipboard for a short window means a following Cmd+V still receives
        the whole block, even though the app never saw the copy key.
        """
        now = time.monotonic()
        changed = False
        if self._auto_copy_at and now >= self._auto_copy_at:
            self._auto_copy_at = 0.0
            text = self.selection.text(self.buffer.lines)
            if text and text != self.clipboard_text:
                result = copy_to_clipboard(text)
                if result.ok:
                    self._remember_clipboard(text)
                    self.error = ""
                    self.notice = (
                        f"{result.message} Ctrl+V pastes, Ctrl+X cuts."
                    )
                    changed = True
        if not self.clipboard_text or now >= self._clipboard_guard_until:
            return changed
        if now < self._clipboard_guard_next:
            return changed
        fast_until = self._clipboard_guard_until - CLIPBOARD_GUARD_SECONDS
        fast_until += CLIPBOARD_GUARD_FAST_SECONDS
        gap = CLIPBOARD_GUARD_FAST_GAP if now < fast_until else CLIPBOARD_GUARD_SLOW_GAP
        self._clipboard_guard_next = now + gap
        current = read_clipboard()
        if not current.ok:
            return changed
        if current.text.strip("\r\n") and not is_truncated_fragment(
            current.text, self.clipboard_text
        ):
            return changed
        result = copy_to_clipboard(self.clipboard_text)
        if result.ok:
            self.notice = (
                f"Restored {self._selection_label(self.clipboard_text)} "
                "to the clipboard."
            )
            changed = True
        return changed

    def _select_all(self) -> None:
        """Select the whole buffer and let the copy settle in the background."""
        self.selection.select_all(self.buffer.lines)
        self.buffer.set_cursor(self.selection.head_row, self.selection.head_col)
        self._schedule_auto_copy()
        self.notice = (
            f"Selected {len(self.buffer.lines)} lines. "
            "Ctrl+C copies, Ctrl+X cuts."
        )

    def _cut_selection(self) -> None:
        """Copy the selection and remove it, in one undoable change."""
        if not self.selection.has_text:
            self.notice = "Nothing selected to cut."
            return
        text = self.selection.text(self.buffer.lines)
        result = copy_to_clipboard(text)
        if not result.ok:
            self.error = result.message
            return
        self._remember_clipboard(text)
        label = self._selection_label(text)
        self._delete_selection()
        self.error = ""
        self.notice = f"Cut {label} to the clipboard."

    def _delete_selection(self) -> None:
        """Remove the selected range as a single undo step."""
        if not self.selection.has_text:
            return
        (start_row, start_col), (end_row, end_col) = self.selection.ordered()
        self.buffer.replace_range(start_row, start_col, end_row, end_col, "")
        self.selection.clear()
        self._after_cursor_move()
        self._after_edit()

    def _insert_paste(self, text: str) -> None:
        """Insert a terminal bracketed paste, repairing a truncated clipboard.

        A terminal that keeps Cmd+V for itself sends the clipboard text as one
        marked block. If that block is only a word or another fragment of the
        text the editor last copied, the full remembered copy is used instead,
        so a stale terminal clipboard cannot shorten the paste.
        """
        text = normalize_newlines(text)
        method = "terminal paste"
        if (
            text
            and self._clipboard_is_fresh()
            and is_truncated_fragment(text, self.clipboard_text)
        ):
            text = self.clipboard_text
            method = "app clipboard"
        if not text:
            self.notice = "The pasted text was empty."
            return
        if self.selection.has_text:
            (start_row, start_col), (end_row, end_col) = self.selection.ordered()
            self.buffer.replace_range(start_row, start_col, end_row, end_col, text)
        else:
            self.buffer.insert_text(text)
        self.selection.clear()
        self._auto_copy_at = 0.0
        self._after_cursor_move()
        self._after_edit()
        self.error = ""
        self.notice = f"Pasted {self._selection_label(text)} ({method})."

    def _paste_clipboard(self) -> None:
        """Insert the system clipboard at the caret, replacing a selection.

        The system clipboard wins unless it looks like a truncated version of
        text this editor just copied. That fallback keeps a multi-word copy
        intact when a terminal overwrites the clipboard with its own stale
        single-word selection.
        """
        read = read_clipboard()
        text = read.text if read.ok else ""
        method = read.method if read.ok else ""
        if self._clipboard_is_fresh():
            if not text:
                text, method = self.clipboard_text, "app clipboard"
            elif self._clipboard_was_truncated(text, self.clipboard_text):
                text, method = self.clipboard_text, "app clipboard"
        if not text:
            if not read.ok:
                self.error = read.message
            else:
                self.notice = "Clipboard is empty."
            return
        if self.selection.has_text:
            (start_row, start_col), (end_row, end_col) = self.selection.ordered()
            self.buffer.replace_range(
                start_row, start_col, end_row, end_col, text
            )
        else:
            self.buffer.insert_text(text)
        self.selection.clear()
        self._after_cursor_move()
        self._after_edit()
        self.error = ""
        self.notice = f"Pasted {self._selection_label(text)} ({method})."

    @staticmethod
    def _selection_label(text: str) -> str:
        characters = len(text)
        label = f"{characters} character{'s' if characters != 1 else ''}"
        lines = text.count("\n") + 1
        if lines > 1:
            label = f"{lines} lines, {label}"
        return label

    @staticmethod
    def _key_inserts_text(key) -> bool:
        return is_enter(key) or key == TAB or printable_char(key) is not None

    def _extend_selection(self, direction: str) -> None:
        """Move the caret with Shift held, extending the selection."""
        if not self.selection.active:
            self.selection.start(
                self.buffer.cursor_row, self.buffer.cursor_col
            )
        if direction == "left":
            self.buffer.move_left()
        elif direction == "right":
            self.buffer.move_right()
        elif direction == "up":
            self.buffer.move_up()
        elif direction == "down":
            self.buffer.move_down()
        elif direction == "home":
            self.buffer.move_home()
        elif direction == "end":
            self.buffer.move_end()
        self.selection.extend(self.buffer.cursor_row, self.buffer.cursor_col)
        self._schedule_auto_copy()
        self._after_cursor_move()

    def _undo(self) -> None:
        if not self.buffer.undo():
            self.notice = "Nothing to undo."
            return
        self.selection.clear()
        self._after_cursor_move()
        self._after_edit()

    def _redo(self) -> None:
        if not self.buffer.redo():
            self.notice = "Nothing to redo."
            return
        self.selection.clear()
        self._after_cursor_move()
        self._after_edit()

    def _line_number_text(self, row_index: int, number_width: int) -> str:
        """Return one gutter cell, absolute or hybrid relative to the caret."""
        if self.preferences.hybrid_line_numbers:
            if row_index == self.buffer.cursor_row:
                number = row_index + 1
            else:
                number = abs(row_index - self.buffer.cursor_row)
        else:
            number = row_index + 1
        return f"{number:>{number_width}} "

    def _toggle_line_numbers(self) -> None:
        """Switch between absolute and hybrid numbers for this and later files."""
        self.preferences.toggle_line_numbers()
        self.notice = self.preferences.describe_line_numbers()
        self.error = ""

    def _select_history(self, index: int) -> None:
        if not self.history_entries:
            return
        self.history_index = max(0, min(len(self.history_entries) - 1, index))
        self._ensure_history_visible()

    def _after_cursor_move(self) -> None:
        """The draw pass fits the viewport; nothing to settle here."""

    def _save(self) -> None:
        self.error = ""
        self.notice = ""
        if not self.buffer.dirty:
            self.notice = "No changes to save."
            return

        current = file_digest(self.path)
        if self.disk_digest and current and current != self.disk_digest:
            self.error = "File changed on disk. Press F8 to reload before saving."
            return

        snapshots = 0
        history_note = ""
        if self.history is not None:
            if self.history.available:
                if self.history.record_current(self.path, reason="full editor") is not None:
                    snapshots = 1
                if self.history.error:
                    history_note = self.history.error
            else:
                history_note = "history disabled"

        try:
            result = write_text(self.path, self.buffer.to_text())
        except FileWriteError as exc:
            self.error = str(exc)
            return

        self.buffer.mark_saved()
        self.disk_digest = result.digest
        self.last_save = result
        self.saved_any = True
        self.completion.close()
        if self.devbox is not None:
            self.devbox.document_saved(self.path, self.buffer.to_text())
        self.saved_line = self.buffer.cursor_row + 1
        self.saved_proof = self._read_disk_line(self.saved_line)
        if history_note:
            self.history_note = history_note
        elif snapshots:
            self.history_note = "previous version stored in SQLite"
        elif self.history is not None and not self.history.available:
            self.history_note = "history disabled"
        else:
            self.history_note = "no snapshot was needed"
        self.mode = "saved"

    def _read_disk_line(self, number: int) -> str:
        if number <= 0:
            return ""
        try:
            with open(
                self.path,
                "r",
                encoding="utf-8",
                errors="surrogateescape",
                newline="",
            ) as handle:
                for index, line in enumerate(handle, 1):
                    if index == number:
                        return line.rstrip("\r\n")
        except OSError:
            return ""
        return ""

    def _reload(self) -> None:
        if self.buffer.dirty:
            self.error = "Save or discard your changes before reloading with F8."
            return
        try:
            text = read_editor_file(self.path)
        except EditorOpenError as exc:
            self.error = str(exc)
            return
        self.buffer = TextBuffer(text)
        self.disk_digest = file_digest(self.path)
        self.scroll_row = 0
        self.scroll_part = 0
        self._pending_center = True
        self.selection.clear()
        self.notice = "Reloaded from disk."

    def _open_history(self) -> None:
        if self.history is None or not self.history.available:
            self.error = "SQLite history is disabled. Start without --no-history to keep snapshots."
            return
        entries = self.history.list_revisions(self.path, limit=100)
        if self.history.error:
            self.error = self.history.error
            return
        if not entries:
            self.notice = "No snapshots stored for this file yet."
            return
        self.history_entries = entries
        self.history_index = 0
        self.history_scroll = 0
        self.mode = "history"

    def _handle_history_key(self, key) -> None:
        if key == curses.ascii.ESC or key == curses.KEY_F6:
            self.mode = "edit"
            return
        count = len(self.history_entries)
        if key == curses.KEY_UP:
            self.history_index = max(0, self.history_index - 1)
        elif key == curses.KEY_DOWN:
            self.history_index = min(max(0, count - 1), self.history_index + 1)
        elif key == curses.KEY_PPAGE:
            self.history_index = max(0, self.history_index - 10)
        elif key == curses.KEY_NPAGE:
            self.history_index = min(max(0, count - 1), self.history_index + 10)
        elif is_enter(key) and count:
            self._restore_selected()
        self._ensure_history_visible()

    def _restore_selected(self) -> None:
        if not self.history_entries:
            return
        revision = self.history_entries[
            max(0, min(self.history_index, len(self.history_entries) - 1))
        ]
        if self.history is None:
            return
        text = self.history.read_text(revision.id)
        if text is None:
            self.error = self.history.error or "That snapshot could not be read."
            return
        self.buffer.replace_text(text)
        self.saved_line = 1
        self.scroll_row = 0
        self.scroll_part = 0
        self._pending_center = True
        self.selection.clear()
        self.notice = (
            "Snapshot loaded into the buffer as unsaved text. Press Ctrl+S to write it."
        )
        self.mode = "edit"

    def _ensure_history_visible(self) -> None:
        height, _ = window_size(self.stdscr)
        rows = max(1, height - 6)
        if self.history_index < self.history_scroll:
            self.history_scroll = self.history_index
        elif self.history_index >= self.history_scroll + rows:
            self.history_scroll = self.history_index - rows + 1
