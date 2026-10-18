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
