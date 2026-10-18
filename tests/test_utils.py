"""Tests for safe path helpers and terminal input helpers."""

import tempfile
from pathlib import Path

from fuzzy_surgical.utils import (
    is_backspace,
    is_enter,
    printable_char,
    safe_directory,
    safe_start_directory,
)


def test_safe_directory_rejects_missing_and_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        file_path = root / "file.txt"
        file_path.write_text("hello")

        assert safe_directory(root) == root.resolve()
        assert safe_directory(file_path) is None
        assert safe_directory(root / "missing") is None


def test_safe_start_directory_uses_preferred_then_fallback():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        assert safe_start_directory(root) == root.resolve()
        fallback = safe_start_directory(Path(tmpdir) / "missing")
        assert fallback.is_dir()


def test_key_helpers():
    assert is_enter("\n")
    assert is_enter("\r")
    assert not is_enter("a")
    assert is_backspace(127)
    assert is_backspace(8)
    assert printable_char("a") == "a"
    assert printable_char(ord("a")) == "a"
    assert printable_char("ab") is None
    assert printable_char(260) is None


def test_read_key_normalises_control_characters():
    from fuzzy_surgical.utils import read_key

    class WideReader:
        def get_wch(self):
            return "\x11"

    class NarrowReader:
        def getch(self):
            return 27

    class PrintableReader:
        def get_wch(self):
            return "a"

    assert read_key(WideReader()) == 17
    assert read_key(NarrowReader()) == 27
    assert read_key(PrintableReader()) == "a"


def test_enter_accepts_the_keypad_enter_code():
    import curses

    assert is_enter(curses.KEY_ENTER)
