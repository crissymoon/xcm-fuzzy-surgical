"""Numeric jump prompt for the whole-file editor.

Hybrid line numbers show the caret line as its true number and every other
line as the distance from the caret. Moving to one of those gutter numbers
needs a direction and a count, so Ctrl+G opens a single-row prompt: typing
digits collects the number, Up and Down jump that many logical lines in the
gutter's own terms, and Enter goes to that absolute line number instead.

The prompt is a state object with no drawing code so the key handling can be
tested without a terminal.
"""

from __future__ import annotations

from typing import Optional

import curses

from fuzzy_surgical.utils import is_backspace, is_enter

MAX_DIGITS = 7

TYPED = "typed"
IGNORED = "ignored"
CANCEL = "cancel"
UP = "up"
DOWN = "down"
ABSOLUTE = "absolute"


def _digit(key) -> Optional[str]:
    """Return the typed digit for a key event, or None for anything else."""
    if isinstance(key, str) and len(key) == 1 and key.isdigit():
        return key
    if isinstance(key, int) and 48 <= key <= 57:
        return chr(key)
    return None


class JumpPrompt:
    """Collect a number and a direction for one jump."""

    def __init__(self) -> None:
        self.active = False
        self.text = ""

    @property
    def value(self) -> int:
        return int(self.text) if self.text else 0

    def open(self) -> None:
        self.active = True
        self.text = ""

    def close(self) -> None:
        self.active = False
        self.text = ""

    def press(self, key) -> str:
        """Handle one key and return the action it asks for."""
        digit = _digit(key)
        if digit is not None:
            if len(self.text) < MAX_DIGITS:
                self.text += digit
            return TYPED
        if is_backspace(key):
            self.text = self.text[:-1]
            return TYPED
        if key == curses.ascii.ESC:
            return CANCEL
        if key == curses.KEY_UP:
            return UP
        if key == curses.KEY_DOWN:
            return DOWN
        if is_enter(key):
            return ABSOLUTE
        return IGNORED

    def hint(self, hybrid: bool) -> str:
        """Return the prompt row describing what the typed number will do."""
        if not self.text:
            return (
                "Jump: type a number. Up or Down jump that many lines, "
                "Enter goes to that line, Esc cancels."
            )
        number = self.text
        if hybrid:
            return (
                f"Jump {number}: Up {number} lines up, Down {number} lines "
                f"down, Enter line {number}, Esc cancels."
            )
        return (
            f"Line {number}: Enter jumps there, Up or Down {number} lines, "
            "Esc cancels."
        )


class EditorJumpMixin:
    """Jump actions for the whole-file editor."""

    def _open_jump_prompt(self) -> None:
        """Open the one-row prompt that jumps by a line or a distance."""
        self.jump.open()
        self.error = ""
        self.notice = ""
        self.completion.close()
        self.diagnostics_panel.close()
        find = getattr(self, "find", None)
        if find is not None:
            find.close()

    def _handle_jump_key(self, key) -> None:
        """Interpret a key while the jump prompt is open."""
        action = self.jump.press(key)
        if action == CANCEL:
            self.jump.close()
            self.notice = "Jump cancelled."
            return
        if action in (TYPED, IGNORED):
            return
        number = self.jump.value
        if number < 1:
            self.notice = "Type a line number first."
            return
        if action == UP:
            self._jump_lines(-number, "up")
        elif action == DOWN:
            self._jump_lines(number, "down")
        else:
            self._jump_absolute(number)

    def _jump_lines(self, delta: int, direction: str) -> None:
        """Move the caret a number of logical lines, matching the gutter."""
        self.jump.close()
        self.error = ""
        self.notice = ""
        before = self.buffer.cursor_row
        target = max(0, min(self.buffer.line_count - 1, before + delta))
        self.buffer.set_cursor(target, self.buffer.cursor_col)
        self.selection.clear()
        self.completion.close()
        self._pending_center = True
        moved = abs(target - before)
        if moved == 0:
            self.notice = (
                "Already at the first line."
                if direction == "up"
                else "Already at the last line."
            )
            return
        plural = "" if moved == 1 else "s"
        self.notice = (
            f"Jumped {moved} line{plural} {direction} to line {target + 1}."
        )

    def _jump_absolute(self, number: int) -> None:
        """Move the caret to a one-based line number, clamped to the file."""
        self.jump.close()
        self.error = ""
        self.buffer.goto_line(number)
        self.buffer.cursor_col = 0
        self.selection.clear()
        self.completion.close()
        self._pending_center = True
        landed = self.buffer.cursor_row + 1
        if landed == number:
            self.notice = f"Jumped to line {landed}."
        else:
            self.notice = (
                f"Line {number} is past the end, so line {landed} is the last."
            )
