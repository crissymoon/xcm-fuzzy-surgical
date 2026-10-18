"""Tests for editor to dev box integration without a terminal."""

from __future__ import annotations

from pathlib import Path

import curses

from fuzzy_surgical.file_writer import file_digest
from fuzzy_surgical.full_editor import FullEditor
from fuzzy_surgical.lsp.completion import CompletionItem
from fuzzy_surgical.lsp.diagnostics import Diagnostic
from fuzzy_surgical.text_buffer import TextBuffer


class DrawScreen:
    """Minimal curses window so drawing can be exercised without a terminal."""

    def __init__(self, height: int = 24, width: int = 90) -> None:
        self.height = height
        self.width = width
        self.writes = []
        self.timeout_ms = None

    def getmaxyx(self):
        return self.height, self.width

    def addstr(self, y, x, text, attr=0):
        self.writes.append((y, x, text, attr))

    def erase(self) -> None:
        self.writes = []

    def refresh(self) -> None:
        return None

    def move(self, y, x) -> None:
        return None

    def timeout(self, milliseconds) -> None:
        self.timeout_ms = milliseconds


class FakeDevBox:
    def __init__(self) -> None:
        self.events = []
        self.opened = None
        self.completion_args = None
        self.saved = None
        self.linted = []
        self.closed = False

    def open_document(self, path, text) -> None:
        self.opened = (path, text)

    def request_completion(self, text, line, character) -> int:
        self.completion_args = (text, line, character)
        return 5

    def poll(self):
        events = self.events
        self.events = []
        return events

    def close_document(self) -> None:
        self.closed = True

    def document_saved(self, path, text) -> None:
        self.saved = (path, text)

    def request_lint(self, path) -> None:
        self.linted.append(path)


def _editor(tmp_path: Path, devbox: FakeDevBox) -> FullEditor:
    target = tmp_path / "app.py"
    target.write_text("value = obj.me\n", encoding="utf-8")
    editor = FullEditor(None, devbox=devbox)
    editor.path = str(target)
    editor.buffer = TextBuffer("value = obj.me\n")
    editor.buffer.cursor_row = 0
    editor.buffer.cursor_col = len("value = obj.me")
    return editor


def test_completion_request_uses_utf16_position_and_prefix(tmp_path: Path) -> None:
    devbox = FakeDevBox()
    editor = _editor(tmp_path, devbox)

    editor._request_completion()

    assert devbox.completion_args == ("value = obj.me\n", 0, len("value = obj.me"))
    assert editor.completion.requested is True
    assert editor.completion.sequence == 5
    assert editor.completion.prefix == "me"
    assert editor.completion.prefix_start == 12


def test_poll_applies_completions_and_accept_inserts(tmp_path: Path) -> None:
    devbox = FakeDevBox()
    editor = _editor(tmp_path, devbox)
    editor._request_completion()
    devbox.events = [
        ("completions", 5, [CompletionItem("method", "method")]),
        ("status", "LSP fake"),
    ]

    assert editor._poll_assist() is True
    assert editor.completion.visible is True
    assert editor.assist_status == "LSP fake"

    editor._accept_completion()

    assert editor.buffer.to_text() == "value = obj.method\n"
    assert editor.completion.visible is False


def test_poll_filters_diagnostics_to_the_open_file(tmp_path: Path) -> None:
    devbox = FakeDevBox()
    editor = _editor(tmp_path, devbox)
    other = tmp_path / "other.py"
    devbox.events = [
        (
            "diagnostics",
            1,
            [
                Diagnostic(editor.path, 1, 3, "here", "error", "ruff"),
                Diagnostic(str(other), 1, 3, "elsewhere", "error", "ruff"),
            ],
        )
    ]

    editor._poll_assist()

    assert len(editor.diagnostics) == 1
    assert editor.diagnostics[0].message == "here"


def test_diagnostic_jump_moves_the_buffer(tmp_path: Path) -> None:
    devbox = FakeDevBox()
    editor = _editor(tmp_path, devbox)
    editor.buffer = TextBuffer("one\ntwo\nthree\n")
    editor.diagnostics_panel.set_items(
        [Diagnostic(editor.path, 3, 0, "third line", "warning", "flake8")]
    )
    editor.diagnostics_panel.visible = True

    editor._jump_to_diagnostic()

    assert editor.buffer.cursor_row == 2
    assert editor.diagnostics_panel.visible is False


def test_save_notifies_the_dev_box(tmp_path: Path) -> None:
    devbox = FakeDevBox()
    editor = _editor(tmp_path, devbox)
    editor.buffer.cursor_col = 5
    editor.buffer.insert_text("X")
    editor.disk_digest = file_digest(editor.path)

    editor._save()

    assert editor.mode == "saved"
    assert devbox.saved is not None
    assert "value" in devbox.saved[1]
    assert devbox.saved[0] == editor.path


def test_ctrl_n_requests_completion_and_enter_accepts(tmp_path: Path) -> None:
    devbox = FakeDevBox()
    editor = _editor(tmp_path, devbox)

    editor._handle_edit_key(14)

    assert editor.completion.requested is True

    editor.completion.set_result(5, [CompletionItem("method", "method")])
    editor.completion.visible = True
    assert editor._handle_edit_key("\n") is False
    assert editor.buffer.to_text() == "value = obj.method\n"


def test_f9_opens_the_issues_panel(tmp_path: Path) -> None:
    devbox = FakeDevBox()
    editor = _editor(tmp_path, devbox)
    editor.diagnostics_panel.set_items(
        [Diagnostic(editor.path, 1, 0, "a problem", "error", "ruff")]
    )

    editor._handle_edit_key(curses.KEY_F9)

    assert editor.diagnostics_panel.visible is True

    editor._handle_edit_key(12)
    assert editor.diagnostics_panel.visible is False


def test_drawing_with_popup_and_panel_does_not_raise(tmp_path: Path) -> None:
    screen = DrawScreen()
    devbox = FakeDevBox()
    editor = _editor(tmp_path, devbox)
    editor.stdscr = screen
    editor.completion.open(12, "me", 5)
    editor.completion.set_result(5, [CompletionItem("method", "method", "method", "def")])
    editor.diagnostics_panel.set_items(
        [Diagnostic(editor.path, 1, 4, "short message", "warning", "flake8")]
    )
    editor.diagnostics_panel.visible = True

    editor._draw()

    text = " ".join(item[2] for item in screen.writes)
    assert "Editing" in text
    assert "Completions" in text
    assert "Issues" in text


class ScriptedScreen(DrawScreen):
    """Window stub that replays a fixed key sequence."""

    def __init__(self, keys, height: int = 24, width: int = 90) -> None:
        super().__init__(height, width)
        self.keys = list(keys)

    def get_wch(self):
        if self.keys:
            return self.keys.pop(0)
        raise KeyboardInterrupt("editor did not exit")


class PollingDevBox(FakeDevBox):
    def __init__(self) -> None:
        super().__init__()
        self.polls = 0

    def poll(self):
        self.polls += 1
        if self.polls == 2:
            return [("completions", 5, [CompletionItem("alpha", "alpha")])]
        return []


def test_editor_loop_requests_accepts_and_saves(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("one\ntwo\n", encoding="utf-8")
    devbox = PollingDevBox()
    keys = [0, -1, -1, "\n", 19, curses.ascii.ESC, curses.ascii.ESC]
    screen = ScriptedScreen(keys)
    editor = FullEditor(screen, devbox=devbox)

    saved = editor.edit(target)

    assert saved is True
    assert target.read_text(encoding="utf-8").startswith("alphaone")
    assert devbox.closed is True
