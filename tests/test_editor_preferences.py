"""Tests for shared editor preferences and hybrid line numbers."""

from __future__ import annotations

import curses

from fuzzy_surgical.editor_preferences import EditorPreferences, normalize_line_numbers
from fuzzy_surgical.full_editor import FullEditor, TextBuffer


class FakeScreen:
    """Minimal curses surface that records drawn text row by row."""

    def __init__(self, height: int = 20, width: int = 80) -> None:
        self.height = height
        self.width = width
        self.rows = [[" "] * width for _ in range(height)]

    def getmaxyx(self):
        return self.height, self.width

    def erase(self) -> None:
        self.rows = [[" "] * self.width for _ in range(self.height)]

    def refresh(self) -> None:
        return None

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        if y < 0 or y >= self.height or x < 0 or x >= self.width:
            raise curses.error()
        for offset, char in enumerate(text):
            if x + offset >= self.width - 1:
                break
            self.rows[y][x + offset] = char

    def move(self, y: int, x: int) -> None:
        return None

    def text(self) -> str:
        return "\n".join("".join(row) for row in self.rows)


def test_unknown_line_number_mode_falls_back_to_absolute() -> None:
    assert normalize_line_numbers("relative") == "absolute"
    assert normalize_line_numbers(None) == "absolute"
    assert normalize_line_numbers("HYBRID") == "hybrid"


def test_hybrid_numbers_show_the_caret_line_absolute() -> None:
    editor = FullEditor(None, preferences=EditorPreferences("hybrid"))
    editor.buffer = TextBuffer("one\ntwo\nthree\nfour")
    editor.buffer.cursor_row = 2

    labels = [editor._line_number_text(row, 3) for row in range(4)]

    assert labels == ["  2 ", "  1 ", "  3 ", "  1 "]


def test_absolute_numbers_stay_sequential() -> None:
    editor = FullEditor(None)
    editor.buffer = TextBuffer("one\ntwo\nthree")
    editor.buffer.cursor_row = 2

    labels = [editor._line_number_text(row, 3) for row in range(3)]

    assert labels == ["  1 ", "  2 ", "  3 "]


def test_toggle_is_shared_between_editors() -> None:
    preferences = EditorPreferences()
    first = FullEditor(None, preferences=preferences)

    first._toggle_line_numbers()

    assert preferences.hybrid_line_numbers is True
    assert "hybrid" in first.notice
    second = FullEditor(None, preferences=preferences)
    assert second.preferences.hybrid_line_numbers is True

    second._toggle_line_numbers()

    assert preferences.hybrid_line_numbers is False
    assert "absolute" in second.notice


def test_editor_draws_hybrid_gutter() -> None:
    screen = FakeScreen()
    editor = FullEditor(screen, preferences=EditorPreferences("hybrid"))
    editor.path = "/tmp/example.py"
    editor.buffer = TextBuffer("one\ntwo\nthree\nfour\nfive\n")
    editor.buffer.cursor_row = 2
    editor.buffer.cursor_col = 0

    editor._draw()

    assert "".join(screen.rows[2])[:5].startswith("  2 ")
    assert "".join(screen.rows[4])[:5].startswith("  3 ")
