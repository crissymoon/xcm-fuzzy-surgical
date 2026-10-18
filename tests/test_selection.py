"""Tests for text selection, clipboard fallbacks and editor integration."""

from __future__ import annotations

import base64
import io

from fuzzy_surgical import full_editor as editor_module
from fuzzy_surgical import selection as selection_module
from fuzzy_surgical.full_editor import CTRL_A, CTRL_C, FullEditor, TextBuffer
from fuzzy_surgical.mouse import ClickTracker, MouseEvent
from fuzzy_surgical.selection import Selection, copy_to_clipboard


class FakeScreen:
    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.height = height
        self.width = width

    def getmaxyx(self):
        return self.height, self.width


def test_single_line_selection_text() -> None:
    selected = Selection()
    selected.start(0, 2)
    selected.extend(0, 7)

    assert selected.has_text
    assert selected.text(["alpha beta"]) == "pha b"


def test_multiline_selection_keeps_newlines() -> None:
    selected = Selection()
    selected.start(0, 2)
    selected.extend(2, 1)

    assert selected.text(["alpha", "beta", "gamma"]) == "pha\nbeta\ng"


def test_selection_ending_at_column_zero_includes_newline() -> None:
    selected = Selection()
    selected.start(0, 2)
    selected.extend(1, 0)

    assert selected.text(["alpha", "beta"]) == "pha\n"


def test_span_reports_each_covered_line() -> None:
    selected = Selection()
    selected.start(1, 2)
    selected.extend(3, 1)

    assert selected.span(0, 5) is None
    assert selected.span(1, 5) == (2, 5)
    assert selected.span(2, 4) == (0, 4)
    assert selected.span(3, 5) == (0, 1)
    assert selected.span(4, 5) is None


def test_selection_normalizes_a_backwards_drag() -> None:
    selected = Selection()
    selected.start(2, 4)
    selected.extend(0, 1)

    assert selected.text(["alpha", "beta", "gamma"]) == "lpha\nbeta\ngamm"


def test_word_selection_handles_words_space_and_punctuation() -> None:
    lines = ["value = call(arg_one, 2);"]
    selected = Selection()

    selected.select_word(lines, 0, 2)
    assert selected.text(lines) == "value"

    selected.select_word(lines, 0, 9)
    assert selected.text(lines) == "call"

    selected.select_word(lines, 0, 14)
    assert selected.text(lines) == "arg_one"

    selected.select_word(lines, 0, 5)
    assert selected.text(lines) == " "


def test_select_line_and_select_all() -> None:
    lines = ["alpha", "beta"]
    selected = Selection()

    selected.select_line(lines, 1)
    assert selected.text(lines) == "beta"

    selected.select_all(lines)
    assert selected.text(lines) == "alpha\nbeta"


def test_release_drops_a_zero_width_selection() -> None:
    selected = Selection()
    selected.start(1, 3)

    assert selected.release() is False
    assert not selected.active


def test_editor_drag_selects_across_lines() -> None:
    editor = FullEditor(FakeScreen(), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("alpha\nbeta")
    editor.mode = "edit"
    gutter = editor._gutter_width()

    editor._handle_mouse(MouseEvent(gutter, 2, "click"))
    editor._handle_mouse(MouseEvent(gutter + 2, 3, "drag"))
    editor._handle_mouse(MouseEvent(gutter + 2, 3, "release"))

    assert editor.selection.text(editor.buffer.lines) == "alpha\nbe"
    assert editor.buffer.cursor_row == 1
    assert editor.buffer.cursor_col == 2


def test_double_click_selects_the_word() -> None:
    editor = FullEditor(FakeScreen(), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("value = total")
    editor.mode = "edit"
    gutter = editor._gutter_width()
    now = [0.0]
    editor.click_tracker = ClickTracker(clock=lambda: now[0])

    for when in (0.0, 0.3):
        now[0] = when
        editor._handle_mouse(MouseEvent(gutter + 1, 2, "click"))

    assert editor.selection.text(editor.buffer.lines) == "value"


def test_triple_click_selects_the_line() -> None:
    editor = FullEditor(FakeScreen(), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("alpha\nbeta")
    editor.mode = "edit"
    gutter = editor._gutter_width()
    moments = [0.0, 0.3, 0.6]
    editor.click_tracker = ClickTracker(clock=lambda: moments.pop(0))

    for _ in range(3):
        editor._handle_mouse(MouseEvent(gutter + 1, 2, "click"))

    assert editor.selection.text(editor.buffer.lines) == "alpha"


def test_copy_uses_the_selection_and_reports_success(monkeypatch) -> None:
    editor = FullEditor(FakeScreen(), None)
    editor.buffer = TextBuffer("alpha\nbeta")
    editor.mode = "edit"
    editor.selection.start(0, 0)
    editor.selection.extend(1, 4)

    calls = []

    def fake_copy(text):
        calls.append(text)
        return selection_module.ClipboardResult(True, "Copied 10 characters.", "test")

    monkeypatch.setattr(editor_module, "copy_to_clipboard", fake_copy)

    assert editor._handle_edit_key(CTRL_C) is False
    assert calls == ["alpha\nbeta"]
    assert "Copied" in editor.notice


def test_copy_without_selection_explains_how_to_select(monkeypatch) -> None:
    editor = FullEditor(FakeScreen(), None)
    editor.buffer = TextBuffer("alpha")
    editor.mode = "edit"

    assert editor._handle_edit_key(CTRL_C) is False
    assert "Nothing selected" in editor.notice


def test_ctrl_a_selects_the_whole_file() -> None:
    editor = FullEditor(FakeScreen(), None)
    editor.buffer = TextBuffer("alpha\nbeta")
    editor.mode = "edit"

    assert editor._handle_edit_key(CTRL_A) is False
    assert editor.selection.text(editor.buffer.lines) == "alpha\nbeta"
    assert editor.buffer.cursor_row == 1


def test_native_clipboard_command_is_used_when_available(monkeypatch) -> None:
    commands = []

    class Completed:
        returncode = 0

    def fake_run(command, **kwargs):
        commands.append((command, kwargs["input"]))
        return Completed()

    monkeypatch.setattr(selection_module.subprocess, "run", fake_run)

    result = copy_to_clipboard("hello", platform="darwin")

    assert result.ok
    assert result.method == "pbcopy"
    assert commands[0][0] == ["pbcopy"]
    assert commands[0][1] == b"hello"


def test_osc52_fallback_when_no_native_tool(monkeypatch) -> None:
    def failing_run(command, **kwargs):
        raise OSError("missing")

    monkeypatch.setattr(selection_module.subprocess, "run", failing_run)
    stream = io.StringIO()

    result = copy_to_clipboard("hello", platform="linux", stream=stream)

    assert result.ok
    assert result.method == "osc52"
    payload = stream.getvalue()
    assert payload.startswith("\x1b]52;c;")
    encoded = payload[len("\x1b]52;c;"):-1]
    assert base64.b64decode(encoded).decode() == "hello"


def test_copy_failure_explains_the_terminal_fallback(monkeypatch) -> None:
    def failing_run(command, **kwargs):
        raise OSError("missing")

    monkeypatch.setattr(selection_module.subprocess, "run", failing_run)

    result = copy_to_clipboard("hello", platform="linux", allow_osc52=False)

    assert not result.ok
    assert "clipboard" in result.message.lower()


def test_empty_copy_is_rejected() -> None:
    result = copy_to_clipboard("")

    assert not result.ok
    assert "Nothing selected" in result.message
