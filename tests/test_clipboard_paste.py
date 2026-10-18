"""Tests for clipboard paste, cut and the editor context menu actions."""

from __future__ import annotations

import subprocess

from fuzzy_surgical import editor_controller as line_editor_module
from fuzzy_surgical import editor_mouse as editor_mouse_module
from fuzzy_surgical import full_editor as editor_module
from fuzzy_surgical import selection as selection_module
from fuzzy_surgical.editor_controller import EditorController
from fuzzy_surgical.full_editor import FullEditor, TextBuffer
from fuzzy_surgical.mouse import MouseEvent
from fuzzy_surgical.selection import ClipboardRead, ClipboardResult, read_clipboard


class FakeScreen:
    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.height = height
        self.width = width

    def getmaxyx(self):
        return self.height, self.width


def test_buffer_replace_range_joins_lines_and_undoes() -> None:
    buffer = TextBuffer("one\ntwo\nthree")

    buffer.replace_range(0, 1, 2, 2, "X\nY")

    assert buffer.to_text() == "oX\nYree"
    assert (buffer.cursor_row, buffer.cursor_col) == (1, 1)
    assert buffer.undo() is True
    assert buffer.to_text() == "one\ntwo\nthree"


def test_buffer_replace_range_reverses_a_backwards_selection() -> None:
    buffer = TextBuffer("alpha beta")

    buffer.replace_range(0, 10, 0, 6, "")

    assert buffer.to_text() == "alpha "


def test_paste_commands_per_platform() -> None:
    assert selection_module.paste_commands("darwin")[0] == ["pbpaste"]
    assert selection_module.paste_commands("win32")[0][0] == "powershell"
    assert selection_module.paste_commands("linux")[0][0] == "wl-paste"


def test_read_clipboard_returns_native_text(monkeypatch) -> None:
    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=b"hi\n", stderr=b"")

    monkeypatch.setattr(selection_module.subprocess, "run", fake_run)

    read = read_clipboard(platform="darwin")

    assert read.ok is True
    assert read.text == "hi\n"
    assert read.method == "pbpaste"


def test_read_clipboard_strips_nul_bytes(monkeypatch) -> None:
    monkeypatch.setattr(
        selection_module, "_read_native", lambda _commands: ("a\x00b", "pbpaste")
    )

    read = read_clipboard(platform="darwin")

    assert read.ok is True
    assert read.text == "ab"


def test_read_clipboard_rejects_oversized_text(monkeypatch) -> None:
    monkeypatch.setattr(
        selection_module, "_read_native", lambda _commands: ("x" * 50, "xclip")
    )

    read = read_clipboard(platform="linux", max_chars=10)

    assert read.ok is False
    assert "larger than" in read.message


def test_read_clipboard_reports_a_missing_tool(monkeypatch) -> None:
    monkeypatch.setattr(selection_module, "_read_native", lambda _commands: ("", ""))

    read = read_clipboard(platform="linux")

    assert read.ok is False
    assert "No clipboard tool" in read.message


def _editor(text: str = "alpha beta") -> FullEditor:
    editor = FullEditor(FakeScreen(), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer(text)
    return editor


def test_cut_removes_the_selection_and_copies_it(monkeypatch) -> None:
    copied = []
    monkeypatch.setattr(
        editor_module,
        "copy_to_clipboard",
        lambda text: copied.append(text)
        or ClipboardResult(True, "copied", "pbpaste"),
    )
    editor = _editor()
    editor.buffer.set_cursor(0, 0)
    editor.selection.start(0, 0)
    editor.selection.extend(0, 5)

    editor._cut_selection()

    assert copied == ["alpha"]
    assert editor.buffer.to_text() == " beta"
    assert "Cut" in editor.notice


def test_paste_replaces_a_selection(monkeypatch) -> None:
    monkeypatch.setattr(
        editor_module,
        "read_clipboard",
        lambda: ClipboardRead(True, "Z", "", "pbpaste"),
    )
    editor = _editor()
    editor.buffer.set_cursor(0, 0)
    editor.selection.start(0, 0)
    editor.selection.extend(0, 5)

    editor._paste_clipboard()

    assert editor.buffer.to_text() == "Z beta"
    assert "Pasted" in editor.notice


def test_paste_inserts_at_the_caret(monkeypatch) -> None:
    monkeypatch.setattr(
        editor_module,
        "read_clipboard",
        lambda: ClipboardRead(True, "!", "", "pbpaste"),
    )
    editor = _editor()
    editor.buffer.set_cursor(0, 11)

    editor._paste_clipboard()

    assert editor.buffer.to_text() == "alpha beta!"


def test_paste_falls_back_when_the_clipboard_holds_one_word(monkeypatch) -> None:
    monkeypatch.setattr(
        editor_module,
        "read_clipboard",
        lambda: ClipboardRead(True, "alpha", "", "pbpaste"),
    )
    editor = _editor("alpha beta gamma")
    editor.buffer.set_cursor(0, len("alpha beta gamma"))
    editor._remember_clipboard("alpha beta gamma")

    editor._paste_clipboard()

    assert editor.buffer.to_text() == "alpha beta gammaalpha beta gamma"
    assert "app clipboard" in editor.notice


def test_paste_still_prefers_a_new_external_clipboard(monkeypatch) -> None:
    monkeypatch.setattr(
        editor_module,
        "read_clipboard",
        lambda: ClipboardRead(True, "external text", "", "pbpaste"),
    )
    editor = _editor("alpha beta gamma")
    editor.buffer.set_cursor(0, len("alpha beta gamma"))
    editor._remember_clipboard("alpha beta gamma")

    editor._paste_clipboard()

    assert editor.buffer.to_text() == "alpha beta gammaexternal text"


def test_paste_uses_the_app_copy_when_the_clipboard_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        editor_module,
        "read_clipboard",
        lambda: ClipboardRead(False, "", "No clipboard tool found.", "missing"),
    )
    editor = _editor("alpha beta gamma")
    editor.buffer.set_cursor(0, len("alpha beta gamma"))
    editor._remember_clipboard("alpha beta gamma")

    editor._paste_clipboard()

    assert editor.buffer.to_text() == "alpha beta gammaalpha beta gamma"
    assert editor.error == ""


def test_mouse_release_copies_the_whole_selection(monkeypatch) -> None:
    copied = []
    monkeypatch.setattr(
        editor_mouse_module,
        "copy_to_clipboard",
        lambda text: copied.append(text)
        or ClipboardResult(True, f"Copied {len(text)} characters with pbcopy.", "pbpaste"),
    )
    editor = _editor("alpha beta gamma")
    editor.selection.start(0, 0)
    editor.selection.extend(0, len("alpha beta"))

    editor._handle_mouse(MouseEvent(0, 0, "release"))

    assert copied == ["alpha beta"]
    assert editor.clipboard_text == "alpha beta"
    assert "Copied 10 characters" in editor.notice


def test_typing_replaces_the_selection() -> None:
    editor = _editor()
    editor.buffer.set_cursor(0, 0)
    editor.selection.start(0, 0)
    editor.selection.extend(0, 5)

    editor._handle_edit_key("X")

    assert editor.buffer.to_text() == "X beta"


def test_backspace_deletes_only_the_selection() -> None:
    editor = _editor()
    editor.buffer.set_cursor(0, 5)
    editor.selection.start(0, 0)
    editor.selection.extend(0, 5)

    editor._handle_edit_key(127)

    assert editor.buffer.to_text() == " beta"
    assert not editor.selection.active


def test_right_click_opens_a_menu_with_cut_enabled(monkeypatch) -> None:
    editor = _editor()
    editor.buffer.set_cursor(0, 0)
    editor.selection.start(0, 0)
    editor.selection.extend(0, 5)

    editor._open_context_menu(3, 3)

    assert editor.menu.visible
    assert editor.menu.selected_item().action == "cut"


def test_menu_action_copy_uses_the_clipboard(monkeypatch) -> None:
    monkeypatch.setattr(
        editor_module,
        "copy_to_clipboard",
        lambda text: ClipboardResult(True, "copied 5 characters", "pbpaste"),
    )
    editor = _editor()
    editor.selection.start(0, 0)
    editor.selection.extend(0, 5)
    editor._open_context_menu(3, 3)

    editor._run_menu_action("copy")

    assert not editor.menu.visible
    assert "copied" in editor.notice


def test_line_editor_pastes_at_the_caret(monkeypatch) -> None:
    monkeypatch.setattr(
        line_editor_module,
        "read_clipboard",
        lambda: ClipboardRead(True, "XY", "", "pbpaste"),
    )
    editor = EditorController()
    editor.edited_line = "abc"
    editor.cursor_pos = 1

    editor._paste_clipboard()

    assert editor.edited_line == "aXYbc"
    assert editor.cursor_pos == 3
    assert "Pasted 2 characters" in editor.notice


def test_line_editor_paste_keeps_one_line(monkeypatch) -> None:
    monkeypatch.setattr(
        line_editor_module,
        "read_clipboard",
        lambda: ClipboardRead(True, "one\ntwo", "", "pbpaste"),
    )
    editor = EditorController()
    editor.edited_line = "abc"
    editor.cursor_pos = 3

    editor._paste_clipboard()

    assert editor.edited_line == "abcone"
    assert "first line" in editor.notice


def test_truncated_fragment_matches_a_middle_word() -> None:
    copied = "alpha beta gamma"

    assert selection_module.is_truncated_fragment("beta", copied) is True
    assert selection_module.is_truncated_fragment("alpha", copied) is True
    assert selection_module.is_truncated_fragment("bet", copied) is False
    assert selection_module.is_truncated_fragment("unrelated", copied) is False
    assert selection_module.is_truncated_fragment(copied, copied) is False


def test_paste_falls_back_when_the_clipboard_holds_a_middle_word(monkeypatch) -> None:
    monkeypatch.setattr(
        editor_module,
        "read_clipboard",
        lambda: ClipboardRead(True, "beta", "", "pbpaste"),
    )
    editor = _editor("alpha beta gamma")
    editor.buffer.set_cursor(0, len("alpha beta gamma"))
    editor._remember_clipboard("alpha beta gamma")

    editor._paste_clipboard()

    assert editor.buffer.to_text() == "alpha beta gammaalpha beta gamma"
    assert "app clipboard" in editor.notice


def test_selection_is_copied_without_a_release_event(monkeypatch) -> None:
    copied = []
    monkeypatch.setattr(
        editor_module,
        "copy_to_clipboard",
        lambda text: copied.append(text)
        or ClipboardResult(True, "Copied 10 characters with pbcopy.", "pbpaste"),
    )
    editor = _editor("alpha beta gamma")
    editor.selection.start(0, 0)
    editor.selection.extend(0, 10)
    editor._auto_copy_at = 0.1

    assert editor._tick_clipboard() is True

    assert copied == ["alpha beta"]
    assert editor.clipboard_text == "alpha beta"


def test_a_terminal_word_replacement_is_repaired(monkeypatch) -> None:
    copied = []
    monkeypatch.setattr(
        editor_module,
        "read_clipboard",
        lambda: ClipboardRead(True, "beta", "", "pbpaste"),
    )
    monkeypatch.setattr(
        editor_module,
        "copy_to_clipboard",
        lambda text: copied.append(text)
        or ClipboardResult(True, "Copied 16 characters with pbcopy.", "pbpaste"),
    )
    editor = _editor("alpha beta gamma")
    editor._remember_clipboard("alpha beta gamma")
    editor._clipboard_guard_next = 0.0

    assert editor._tick_clipboard() is True

    assert copied == ["alpha beta gamma"]
    assert "Restored" in editor.notice


def test_the_guard_leaves_a_new_external_clipboard_alone(monkeypatch) -> None:
    copied = []
    monkeypatch.setattr(
        editor_module,
        "read_clipboard",
        lambda: ClipboardRead(True, "something else", "", "pbpaste"),
    )
    monkeypatch.setattr(
        editor_module,
        "copy_to_clipboard",
        lambda text: copied.append(text)
        or ClipboardResult(True, "copied", "pbpaste"),
    )
    editor = _editor("alpha beta gamma")
    editor._remember_clipboard("alpha beta gamma")
    editor._clipboard_guard_next = 0.0

    assert editor._tick_clipboard() is False

    assert copied == []
