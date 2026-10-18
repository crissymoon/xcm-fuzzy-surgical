"""Text buffer and safe file loading for the whole-file editor.

This module is deliberately independent of curses so the buffer, newline
handling and editor caps can be tested without a terminal.
"""

from __future__ import annotations

import os
import stat
import time
from typing import List, Tuple

MAX_EDITOR_BYTES = 8_000_000
MAX_EDITOR_LINES = 200_000
MAX_UNDO_STEPS = 500
COALESCE_SECONDS = 0.6


class EditorOpenError(Exception):
    """Raised when a file cannot be opened safely in the full editor."""


def detect_newline(text: str) -> str:
    """Return the dominant line ending without guessing on empty text."""
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    if crlf and crlf >= lf and crlf >= cr:
        return "\r\n"
    if cr and cr > lf:
        return "\r"
    return "\n"


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_editor_file(file_path: str) -> str:
    """Read a text file with the editor caps, raising EditorOpenError."""
    try:
        info = os.stat(file_path)
    except OSError as exc:
        raise EditorOpenError("Could not read the file: " + (exc.strerror or str(exc))) from exc
    if not stat.S_ISREG(info.st_mode):
        raise EditorOpenError("That path is not a regular file.")
    if info.st_size > MAX_EDITOR_BYTES:
        raise EditorOpenError(
            f"File is larger than {MAX_EDITOR_BYTES // 1_000_000} MB. Edit it outside the TUI."
        )
    try:
        with open(file_path, "rb") as handle:
            data = handle.read(MAX_EDITOR_BYTES + 1)
    except OSError as exc:
        raise EditorOpenError("Could not read the file: " + (exc.strerror or str(exc))) from exc
    if b"\x00" in data[:8192]:
        raise EditorOpenError("That looks like a binary file, so it was not opened.")
    text = data.decode("utf-8", "surrogateescape")
    if text.count("\n") > MAX_EDITOR_LINES:
        raise EditorOpenError("File has too many lines for the built-in editor.")
    return text


class TextBuffer:
    """Editable lines with cursor movement, undo and redo."""

    def __init__(self, text: str = "") -> None:
        self.newline = detect_newline(text)
        self.trailing_newline = False
        self.lines: List[str] = [""]
        self.cursor_row = 0
        self.cursor_col = 0
        self.original_text = ""
        self.undo_stack: List[Tuple[List[str], bool, int, int]] = []
        self.redo_stack: List[Tuple[List[str], bool, int, int]] = []
        self._last_edit = ""
        self._last_edit_at = 0.0
        self._reset(text)

    def _reset(self, text: str) -> None:
        normalized = normalize_newlines(text)
        self.trailing_newline = normalized.endswith("\n")
        body = normalized[:-1] if self.trailing_newline else normalized
        self.lines = body.split("\n")
        if not self.lines:
            self.lines = [""]
        self.cursor_row = 0
        self.cursor_col = 0
        self.original_text = self.to_text()

    def to_text(self) -> str:
        body = self.newline.join(self.lines)
        if self.trailing_newline:
            return body + self.newline
        return body

    @property
    def dirty(self) -> bool:
        return self.to_text() != self.original_text

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def mark_saved(self) -> None:
        self.original_text = self.to_text()

    def _clamp(self) -> None:
        if not self.lines:
            self.lines = [""]
        self.cursor_row = max(0, min(self.cursor_row, len(self.lines) - 1))
        self.cursor_col = max(0, min(self.cursor_col, len(self.lines[self.cursor_row])))

    def _state(self) -> Tuple[List[str], bool, int, int]:
        return (list(self.lines), self.trailing_newline, self.cursor_row, self.cursor_col)

    def _restore(self, state: Tuple[List[str], bool, int, int]) -> None:
        self.lines, self.trailing_newline, self.cursor_row, self.cursor_col = state
        self._clamp()
        self._last_edit = ""

    def _begin_edit(self, kind: str) -> None:
        now = time.monotonic()
        coalesce = (
            kind in ("insert", "backspace", "delete")
            and kind == self._last_edit
            and now - self._last_edit_at <= COALESCE_SECONDS
            and bool(self.undo_stack)
        )
        if not coalesce:
            self.undo_stack.append(self._state())
            if len(self.undo_stack) > MAX_UNDO_STEPS:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
        self._last_edit = kind
        self._last_edit_at = now

    def insert_text(self, text: str) -> None:
        if not text:
            return
        text = normalize_newlines(text)
        self._begin_edit("insert")
        row = self.cursor_row
        line = self.lines[row]
        before = line[: self.cursor_col]
        after = line[self.cursor_col :]
        parts = text.split("\n")
        if len(parts) == 1:
            self.lines[row] = before + text + after
            self.cursor_col += len(text)
            return
        replacement = [before + parts[0]]
        replacement.extend(parts[1:-1])
        replacement.append(parts[-1] + after)
        self.lines[row : row + 1] = replacement
        self.cursor_row = row + len(parts) - 1
        self.cursor_col = len(parts[-1])

    def replace_span(self, row: int, start: int, end: int, text: str) -> None:
        """Replace a range on one line, used by completion acceptance."""
        if row < 0 or row >= len(self.lines):
            return
        line = self.lines[row]
        start = max(0, min(start, len(line)))
        end = max(start, min(end, len(line)))
        normalized = normalize_newlines(text)
        if not normalized and start == end:
            return
        self._begin_edit("completion")
        parts = normalized.split("\n")
        before = line[:start]
        after = line[end:]
        if len(parts) == 1:
            self.lines[row] = before + normalized + after
            self.cursor_row = row
            self.cursor_col = start + len(normalized)
            return
        replacement = [before + parts[0]]
        replacement.extend(parts[1:-1])
        replacement.append(parts[-1] + after)
        self.lines[row : row + 1] = replacement
        self.cursor_row = row + len(parts) - 1
        self.cursor_col = len(parts[-1])

    def insert_newline(self) -> None:
        self._begin_edit("newline")
        row = self.cursor_row
        line = self.lines[row]
        before = line[: self.cursor_col]
        after = line[self.cursor_col :]
        self.lines[row : row + 1] = [before, after]
        self.cursor_row = row + 1
        self.cursor_col = 0

    def backspace(self) -> None:
        if self.cursor_col > 0:
            self._begin_edit("backspace")
            row = self.cursor_row
            self.lines[row] = (
                self.lines[row][: self.cursor_col - 1] + self.lines[row][self.cursor_col :]
            )
            self.cursor_col -= 1
        elif self.cursor_row > 0:
            self._begin_edit("backspace")
            previous = self.lines[self.cursor_row - 1]
            current = self.lines.pop(self.cursor_row)
            self.cursor_row -= 1
            self.cursor_col = len(previous)
            self.lines[self.cursor_row] = previous + current

    def delete(self) -> None:
        line = self.lines[self.cursor_row]
        if self.cursor_col < len(line):
            self._begin_edit("delete")
            row = self.cursor_row
            self.lines[row] = line[: self.cursor_col] + line[self.cursor_col + 1 :]
        elif self.cursor_row < len(self.lines) - 1:
            self._begin_edit("delete")
            row = self.cursor_row
            self.lines[row] = line + self.lines[row + 1]
            del self.lines[row + 1]

    def move_left(self) -> None:
        if self.cursor_col > 0:
            self.cursor_col -= 1
        elif self.cursor_row > 0:
            self.cursor_row -= 1
            self.cursor_col = len(self.lines[self.cursor_row])

    def move_right(self) -> None:
        line = self.lines[self.cursor_row]
        if self.cursor_col < len(line):
            self.cursor_col += 1
        elif self.cursor_row < len(self.lines) - 1:
            self.cursor_row += 1
            self.cursor_col = 0

    def move_up(self) -> None:
        if self.cursor_row > 0:
            self.cursor_row -= 1
            self._clamp()

    def move_down(self) -> None:
        if self.cursor_row < len(self.lines) - 1:
            self.cursor_row += 1
            self._clamp()

    def move_home(self) -> None:
        self.cursor_col = 0

    def move_end(self) -> None:
        self.cursor_col = len(self.lines[self.cursor_row])

    def move_page(self, delta: int) -> None:
        self.cursor_row = max(0, min(len(self.lines) - 1, self.cursor_row + delta))
        self._clamp()

    def goto_line(self, line_number: int) -> None:
        self.cursor_row = max(0, min(len(self.lines) - 1, line_number - 1))
        self._clamp()

    def set_cursor(self, row: int, column: int) -> None:
        """Place the cursor without touching text or the undo history."""
        self.cursor_row = max(0, min(len(self.lines) - 1, row))
        self.cursor_col = max(0, min(len(self.lines[self.cursor_row]), column))

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append(self._state())
        self._restore(self.undo_stack.pop())
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append(self._state())
        self._restore(self.redo_stack.pop())
        return True

    def replace_text(self, text: str) -> None:
        """Load text as an undoable change, keeping the saved baseline."""
        self._begin_edit("restore")
        normalized = normalize_newlines(text)
        self.trailing_newline = normalized.endswith("\n")
        body = normalized[:-1] if self.trailing_newline else normalized
        self.lines = body.split("\n") or [""]
        self.cursor_row = 0
        self.cursor_col = 0
