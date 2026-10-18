"""Tests for Command and Option chord decoding and editing actions."""

from __future__ import annotations

from typing import List

import pytest

from fuzzy_surgical import full_editor as editor_module
from fuzzy_surgical import utils as utils_module
from fuzzy_surgical.full_editor import FullEditor, TextBuffer
from fuzzy_surgical.selection import ClipboardResult
from fuzzy_surgical.utils import ChordKey, read_key

ESC = "\x1b"


@pytest.fixture(autouse=True)
def _clear_pending_keys():
    utils_module._PENDING.clear()
    yield
    utils_module._PENDING.clear()


class FakeScreen:
    """Minimal stand-in for a curses window."""

    def __init__(self, values: List[object]) -> None:
        self.values = list(values)
        self.timeouts: List[int] = []

    def timeout(self, milliseconds: int) -> None:
        self.timeouts.append(milliseconds)

    def get_wch(self):
        if not self.values:
            raise FakeTimeout()
        value = self.values.pop(0)
        if value is None:
            raise FakeTimeout()
        return value


class FakeTimeout(Exception):
    """Stand-in for curses.error on a timed-out read."""


@pytest.fixture(autouse=True)
def _patch_read_one(monkeypatch):
    def fake_read_one(stdscr):
        try:
            return stdscr.get_wch()
        except FakeTimeout:
            return None

    monkeypatch.setattr(utils_module, "_read_one", fake_read_one)


def test_escape_prefix_letter_is_an_alt_chord() -> None:
    screen = FakeScreen(list(ESC + "x"))

    key = read_key(screen, input_timeout=120)

    assert key == ChordKey("x")
    assert not key.super


def test_csi_u_super_modifier_is_a_command_chord() -> None:
    screen = FakeScreen(list(ESC + "[120;9u"))

    key = read_key(screen, input_timeout=120)

    assert key == ChordKey("x", super=True)


def test_xterm_modifier_report_is_a_command_chord() -> None:
    screen = FakeScreen(list(ESC + "[27;9;99~"))

    key = read_key(screen, input_timeout=120)

    assert key == ChordKey("c", super=True)


def test_a_plain_escape_is_not_a_chord() -> None:
    screen = FakeScreen([ESC, None])

    assert read_key(screen, input_timeout=120) == 27


def test_ctrl_sequences_are_untouched() -> None:
    screen = FakeScreen(["x", "\x18"])

    assert read_key(screen, input_timeout=120) == "x"
    assert read_key(screen, input_timeout=120) == 24


def _editor(text: str) -> FullEditor:
    editor = FullEditor(FakeScreen([]), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer(text)
    return editor


@pytest.fixture
def clipboard(monkeypatch):
    calls = []

    def fake_copy(text):
        calls.append(text)
        return ClipboardResult(True, f"Copied {len(text)} characters.", "pbcopy")

    monkeypatch.setattr(editor_module, "copy_to_clipboard", fake_copy)
    return calls


def test_cmd_x_cuts_the_selection(clipboard) -> None:
    editor = _editor("alpha beta")
    editor.selection.start(0, 0)
    editor.selection.extend(0, 5)

    finished = editor._handle_edit_key(ChordKey("x", super=True))

    assert finished is False
    assert editor.buffer.to_text() == " beta"
    assert clipboard == ["alpha"]
    assert "Cut" in editor.notice


def test_alt_x_cuts_the_selection(clipboard) -> None:
    editor = _editor("alpha beta")
    editor.selection.start(0, 6)
    editor.selection.extend(0, 10)

    editor._handle_edit_key(ChordKey("x"))

    assert editor.buffer.to_text() == "alpha "
    assert clipboard == ["beta"]


def test_cmd_a_selects_everything(clipboard) -> None:
    editor = _editor("one\ntwo")

    editor._handle_edit_key(ChordKey("a", super=True))

    assert editor.selection.has_text
    assert editor.selection.text(editor.buffer.lines) == "one\ntwo"


def test_an_unbound_chord_does_not_type_its_letter(clipboard) -> None:
    editor = _editor("alpha")
    editor.buffer.set_cursor(0, 0)

    editor._handle_edit_key(ChordKey("q", super=True))

    assert editor.buffer.to_text() == "alpha"
    assert "Cmd+Q is not bound" in editor.notice


def test_cmd_x_without_a_selection_reports_it(clipboard) -> None:
    editor = _editor("alpha")

    editor._handle_edit_key(ChordKey("x", super=True))

    assert editor.buffer.to_text() == "alpha"
    assert "Nothing selected" in editor.notice
