"""Tests for the Ctrl+G jump prompt used with hybrid line numbers."""

from __future__ import annotations

import curses

from fuzzy_surgical import editor_jump
from fuzzy_surgical.editor_preferences import EditorPreferences
from fuzzy_surgical.editor_jump import JumpPrompt
from fuzzy_surgical.full_editor import FullEditor, TextBuffer
from tests.test_editor_preferences import FakeScreen

CTRL_G = 7


def _editor(line_count: int = 100, mode: str = "hybrid") -> FullEditor:
    editor = FullEditor(None, preferences=EditorPreferences(mode))
    editor.path = "/tmp/example.py"
    editor.buffer = TextBuffer("\n".join(f"line {index}" for index in range(line_count)))
    return editor


def test_prompt_collects_digits_and_edits_them() -> None:
    prompt = JumpPrompt()
    prompt.open()

    assert prompt.press("1") == editor_jump.TYPED
    assert prompt.press("2") == editor_jump.TYPED
    assert prompt.value == 12

    assert prompt.press(curses.KEY_BACKSPACE) == editor_jump.TYPED
    assert prompt.value == 1


def test_prompt_reports_direction_and_absolute_keys() -> None:
    prompt = JumpPrompt()
    prompt.open()

    assert prompt.press(curses.KEY_UP) == editor_jump.UP
    assert prompt.press(curses.KEY_DOWN) == editor_jump.DOWN
    assert prompt.press("\n") == editor_jump.ABSOLUTE
    assert prompt.press(curses.ascii.ESC) == editor_jump.CANCEL


def test_hybrid_hint_reads_the_gutter_number() -> None:
    prompt = JumpPrompt()
    prompt.open()
    prompt.press("1")
    prompt.press("2")

    hint = prompt.hint(hybrid=True)

    assert "Up 12 lines up" in hint
    assert "Down 12 lines down" in hint
    assert "Enter line 12" in hint


def test_absolute_hint_explains_enter() -> None:
    prompt = JumpPrompt()
    prompt.open()
    prompt.press("4")

    assert prompt.hint(hybrid=False).startswith("Line 4: Enter jumps there")


def test_relative_jump_moves_by_logical_lines() -> None:
    editor = _editor()
    editor.buffer.cursor_row = 50

    editor._jump_lines(5, "down")

    assert editor.buffer.cursor_row == 55
    assert "5 lines down to line 56" in editor.notice
    assert editor._line_number_text(50, 3).strip() == "5"


def test_relative_jump_clamps_at_the_first_and_last_line() -> None:
    editor = _editor(line_count=10)
    editor.buffer.cursor_row = 1

    editor._jump_lines(-8, "up")

    assert editor.buffer.cursor_row == 0
    assert editor.notice == "Jumped 1 line up to line 1."

    editor._jump_lines(-8, "up")

    assert editor.buffer.cursor_row == 0
    assert editor.notice == "Already at the first line."

    editor.buffer.cursor_row = 9
    editor._jump_lines(8, "down")

    assert editor.buffer.cursor_row == 9
    assert editor.notice == "Already at the last line."


def test_enter_jumps_to_an_absolute_line() -> None:
    editor = _editor()
    editor._open_jump_prompt()

    for key in ("3", "0"):
        editor._handle_jump_key(key)
    editor._handle_jump_key("\n")

    assert editor.buffer.cursor_row == 29
    assert editor.notice == "Jumped to line 30."
    assert editor.jump.active is False


def test_absolute_jump_past_the_end_lands_on_the_last_line() -> None:
    editor = _editor(line_count=20)

    editor._open_jump_prompt()
    for key in ("9", "9"):
        editor._handle_jump_key(key)
    editor._handle_jump_key("\n")

    assert editor.buffer.cursor_row == 19
    assert "last" in editor.notice


def test_empty_prompt_asks_for_a_number() -> None:
    editor = _editor()

    editor._open_jump_prompt()
    editor._handle_jump_key(curses.KEY_DOWN)

    assert editor.jump.active is True
    assert editor.notice == "Type a line number first."


def test_ctrl_g_opens_the_prompt_without_typing_digits() -> None:
    editor = _editor()
    editor.buffer.cursor_row = 10

    finished = editor._handle_edit_key(CTRL_G)
    editor._handle_edit_key("3")
    editor._handle_edit_key(curses.KEY_DOWN)

    assert finished is False
    assert editor.buffer.cursor_row == 13
    assert editor.buffer.lines[10] == "line 10"


def test_escape_closes_the_prompt() -> None:
    editor = _editor()

    editor._open_jump_prompt()
    editor._handle_jump_key(curses.ascii.ESC)

    assert editor.jump.active is False
    assert editor.notice == "Jump cancelled."


def test_editor_draws_the_prompt_row() -> None:
    screen = FakeScreen()
    editor = FullEditor(screen, preferences=EditorPreferences("hybrid"))
    editor.path = "/tmp/example.py"
    editor.buffer = TextBuffer("alpha\nbeta\ngamma\n")
    editor.buffer.cursor_row = 1
    editor._open_jump_prompt()
    editor._handle_jump_key("2")

    editor._draw()

    row = "".join(screen.rows[screen.height - 2])
    assert "Jump 2:" in row
