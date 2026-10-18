"""Tests for bracketed paste parsing and the editor paste repair."""

from __future__ import annotations

from typing import List

import pytest

from fuzzy_surgical import full_editor as editor_module
from fuzzy_surgical import utils as utils_module
from fuzzy_surgical.full_editor import FullEditor, TextBuffer
from fuzzy_surgical.selection import ClipboardResult
from fuzzy_surgical.utils import PastedText, read_key


@pytest.fixture(autouse=True)
def _clear_pending_keys():
    utils_module._PENDING.clear()
    yield
    utils_module._PENDING.clear()


ESC = "\x1b"


class FakeScreen:
    def __init__(self, values: List[object], height: int = 24, width: int = 80) -> None:
        self.values = list(values)
        self.height = height
        self.width = width
        self.timeouts: List[int] = []

    def getmaxyx(self):
        return self.height, self.width

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


def _patch_timeout(monkeypatch) -> None:
    def fake_read_one(stdscr):
        try:
            return stdscr.get_wch()
        except FakeTimeout:
            return None

    monkeypatch.setattr(utils_module, "_read_one", fake_read_one)


def test_bracketed_paste_is_returned_as_one_block(monkeypatch) -> None:
    _patch_timeout(monkeypatch)
    screen = FakeScreen(list(ESC + "[200~") + list("alpha beta") + list(ESC + "[201~"))

    key = read_key(screen, input_timeout=120)

    assert isinstance(key, PastedText)
    assert key.text == "alpha beta"
    assert screen.timeouts[-1] == 120


def test_a_lone_escape_still_closes_the_editor(monkeypatch) -> None:
    _patch_timeout(monkeypatch)
    screen = FakeScreen([ESC, None])

    key = read_key(screen, input_timeout=120)

    assert key == 27
    assert screen.timeouts[-1] == 120


def test_unrecognised_escape_sequences_are_replayed(monkeypatch) -> None:
    _patch_timeout(monkeypatch)
    screen = FakeScreen(list(ESC + "[9z") + [None])

    first = read_key(screen, input_timeout=120)
    second = read_key(screen, input_timeout=120)
    third = read_key(screen, input_timeout=120)
    fourth = read_key(screen, input_timeout=120)

    assert first == 27
    assert second == "["
    assert third == "9"
    assert fourth == "z"


def _editor(text: str = "alpha beta gamma") -> FullEditor:
    editor = FullEditor(FakeScreen([]), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer(text)
    return editor


def test_terminal_paste_inserts_multiple_lines() -> None:
    editor = _editor("one")
    editor.buffer.set_cursor(0, 3)

    editor._insert_paste("two\r\nthree")

    assert editor.buffer.to_text() == "onetwo\nthree"


def test_terminal_paste_repairs_a_truncated_clipboard(monkeypatch) -> None:
    monkeypatch.setattr(
        editor_module,
        "copy_to_clipboard",
        lambda text: ClipboardResult(True, "copied", "pbpaste"),
    )
    editor = _editor("alpha beta gamma")
    editor.buffer.set_cursor(0, len("alpha beta gamma"))
    editor._remember_clipboard("alpha beta gamma")

    editor._insert_paste("beta")

    assert editor.buffer.to_text() == "alpha beta gammaalpha beta gamma"
    assert "app clipboard" in editor.notice


def test_terminal_paste_replaces_a_selection() -> None:
    editor = _editor("alpha beta gamma")
    editor.selection.start(0, 0)
    editor.selection.extend(0, 5)

    editor._insert_paste("Z")

    assert editor.buffer.to_text() == "Z beta gamma"
    assert not editor.selection.active
