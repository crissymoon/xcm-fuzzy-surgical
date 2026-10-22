"""Tests for finding text in the whole-file editor."""

from __future__ import annotations

import curses

from fuzzy_surgical import utils as utils_module
from fuzzy_surgical.editor_find import (
    FindPrompt,
    draw_match,
    find_matches,
)
from fuzzy_surgical.full_editor import CTRL_F, FullEditor, TextBuffer
from fuzzy_surgical.utils import ChordKey


class DrawScreen:
    """Minimal curses window that records drawing and cursor moves."""

    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.height = height
        self.width = width
        self.operations = []

    def getmaxyx(self):
        return self.height, self.width

    def addstr(self, y, x, text, attr=0):
        self.operations.append(("addstr", y, x, text, attr))

    def erase(self) -> None:
        self.operations = []

    def refresh(self) -> None:
        return None

    def move(self, y, x) -> None:
        self.operations.append(("move", y, x))

    def timeout(self, milliseconds) -> None:
        return None


class StubTheme:
    def attr(self, category=None, **kwargs):
        return 0


def _editor(text: str) -> FullEditor:
    editor = FullEditor(None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer(text)
    return editor


def _type(editor: FullEditor, text: str) -> None:
    for char in text:
        editor._handle_edit_key(char)


def test_find_matches_is_case_insensitive_for_lowercase_queries() -> None:
    lines = ["Alpha beta", "BETA gamma", "beta"]

    assert find_matches(lines, "beta") == [
        (0, 6, 10),
        (1, 0, 4),
        (2, 0, 4),
    ]


def test_find_matches_respects_case_when_the_query_has_uppercase() -> None:
    lines = ["alpha Beta beta", "BETA"]

    assert find_matches(lines, "Beta") == [(0, 6, 10)]
    assert find_matches(lines, "BETA") == [(1, 0, 4)]


def test_find_matches_skips_overlapping_hits() -> None:
    assert find_matches(["aaaa"], "aa") == [(0, 0, 2), (0, 2, 4)]


def test_typing_opens_a_query_and_moves_the_caret_onto_the_match() -> None:
    editor = _editor("first line\nsecond target line\nthird target\n")

    editor._handle_edit_key(CTRL_F)
    assert editor.find.active

    _type(editor, "target")

    assert editor.buffer.cursor_row == 1
    assert editor.buffer.cursor_col == 7
    assert "Match 1 of 2" in editor.notice


def test_enter_cycles_forward_and_wraps() -> None:
    editor = _editor("one two one two one")

    editor._handle_edit_key(CTRL_F)
    _type(editor, "two")
    assert editor.buffer.cursor_col == 4

    editor._handle_edit_key("\n")
    assert editor.buffer.cursor_col == 12

    editor._handle_edit_key("\n")
    assert editor.buffer.cursor_col == 4


def test_up_goes_to_the_previous_match() -> None:
    editor = _editor("one two one two one")

    editor._handle_edit_key(CTRL_F)
    _type(editor, "two")
    editor._handle_edit_key(curses.KEY_UP)

    assert editor.buffer.cursor_col == 12


def test_a_caret_after_the_last_match_wraps_to_the_first() -> None:
    editor = _editor("alpha\nbeta alpha\n")
    editor.buffer.set_cursor(1, 8)

    editor._handle_edit_key(CTRL_F)
    _type(editor, "alpha")

    assert editor.buffer.cursor_row == 0
    assert editor.buffer.cursor_col == 0


def test_a_query_without_matches_is_reported_and_keeps_the_caret() -> None:
    editor = _editor("alpha beta\n")
    editor.buffer.set_cursor(0, 3)

    editor._handle_edit_key(CTRL_F)
    _type(editor, "missing")

    assert editor.find.matches == []
    assert editor.buffer.cursor_row == 0
    assert editor.buffer.cursor_col == 3
    assert "No matches for 'missing'" in editor.notice


def test_escape_closes_the_prompt_and_keeps_the_query_for_next_time() -> None:
    editor = _editor("alpha beta\n")

    editor._handle_edit_key(CTRL_F)
    _type(editor, "beta")
    editor._handle_edit_key(curses.ascii.ESC)

    assert editor.find.active is False
    assert editor.find.query == "beta"
    assert editor.find.matches == []
    assert editor.buffer.cursor_col == 6


def test_ctrl_f_on_an_open_prompt_steps_to_the_next_match() -> None:
    editor = _editor("alpha beta alpha\n")

    editor._handle_edit_key(CTRL_F)
    _type(editor, "alpha")
    editor._handle_edit_key(CTRL_F)

    assert editor.buffer.cursor_col == 11


def test_f3_repeats_the_last_find_after_the_prompt_closes() -> None:
    editor = _editor("alpha beta alpha\n")

    editor._handle_edit_key(CTRL_F)
    _type(editor, "alpha")
    editor._handle_edit_key(curses.ascii.ESC)
    editor._handle_edit_key(curses.KEY_F3)

    assert editor.buffer.cursor_col == 11


def test_typing_in_the_prompt_does_not_edit_the_file() -> None:
    editor = _editor("alpha\nbeta\n")

    editor._handle_edit_key(CTRL_F)
    _type(editor, "alp")

    assert editor.buffer.to_text() == "alpha\nbeta\n"
    assert editor.buffer.dirty is False
    assert editor.find.query == "alp"


def test_backspace_edits_the_query_until_it_is_empty() -> None:
    editor = _editor("alpha\n")

    editor._handle_edit_key(CTRL_F)
    _type(editor, "alp")
    editor._handle_edit_key(curses.KEY_BACKSPACE)
    editor._handle_edit_key(curses.KEY_BACKSPACE)
    editor._handle_edit_key(curses.KEY_BACKSPACE)

    assert editor.find.query == ""
    assert editor.find.active is True


def test_a_paste_goes_into_the_query_not_the_file() -> None:
    editor = _editor("alpha\nbeta\n")

    editor._handle_edit_key(CTRL_F)
    editor._find_paste("bet\n")

    assert editor.buffer.to_text() == "alpha\nbeta\n"
    assert editor.find.query == "bet"
    assert editor.buffer.cursor_row == 1


def test_cmd_f_opens_the_find_prompt() -> None:
    editor = _editor("alpha\n")

    editor._handle_edit_key(ChordKey("f", super=True))

    assert editor.find.active is True
    assert editor.notice == ""


def test_the_prompt_reopens_with_the_previous_query_and_jumps() -> None:
    editor = _editor("alpha beta alpha\n")

    editor._handle_edit_key(CTRL_F)
    _type(editor, "alpha")
    editor._handle_edit_key(curses.ascii.ESC)
    editor.buffer.set_cursor(0, 5)
    editor._handle_edit_key(CTRL_F)

    assert editor.find.query == "alpha"
    assert editor.buffer.cursor_col == 11


def test_find_prompt_hint_reports_the_position() -> None:
    prompt = FindPrompt()
    prompt.open(0, 0)
    prompt.query = "beta"
    prompt.load(find_matches(["alpha beta", "beta"], "beta"))
    prompt.index = 1

    hint = prompt.hint()

    assert "2/2" in hint
    assert "line 2" in hint
    assert "Enter next" in hint


def test_draw_match_paints_the_text_at_its_display_cells() -> None:
    screen = DrawScreen()

    draw_match(
        screen,
        3,
        5,
        "\tvalue",
        1,
        6,
        0,
        40,
        StubTheme(),
        current=True,
    )

    assert screen.operations == [("addstr", 3, 9, "value", 0)]


def test_draw_match_skips_a_hit_scrolled_out_of_the_segment() -> None:
    screen = DrawScreen()

    draw_match(screen, 3, 5, "value", 0, 5, 10, 20, StubTheme(), current=False)

    assert screen.operations == []


def test_the_caret_is_placed_after_the_status_bar_is_drawn() -> None:
    screen = DrawScreen()
    editor = FullEditor(screen)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("alpha\nbeta\n")
    editor.buffer.set_cursor(0, 0)

    editor._draw()

    assert screen.operations[-1][0] == "move"
    assert screen.operations[-1][1] == 2
    assert screen.operations[-1][2] == editor._gutter_width()


def test_the_find_prompt_owns_the_caret_while_it_is_open() -> None:
    screen = DrawScreen()
    editor = FullEditor(screen)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("alpha\n")
    editor.find.open(0, 0)
    editor.find.query = "alp"
    editor.find.load(find_matches(editor.buffer.lines, "alp"))

    editor._draw()

    assert screen.operations[-1] == ("move", 24 - 2, len("Find: alp"))


def test_bar_caret_and_restore_write_the_terminal_sequences(monkeypatch) -> None:
    sequences = []

    def fake_write(sequence: str) -> bool:
        sequences.append(sequence)
        return True

    monkeypatch.setattr(utils_module, "_terminal_write", fake_write)

    assert utils_module.enable_bar_caret() is True
    utils_module.restore_caret_shape()

    assert sequences == ["\x1b[5 q", "\x1b[0 q"]
