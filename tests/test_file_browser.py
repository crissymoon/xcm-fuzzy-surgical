"""Tests for the file browser view without starting a real terminal."""

from __future__ import annotations

from pathlib import Path

import curses

from fuzzy_surgical import file_browser as browser_module
from fuzzy_surgical.colors import Theme
from fuzzy_surgical.file_browser import FileBrowser


class FakeScreen:
    """Minimal curses window used to exercise drawing code."""

    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.height = height
        self.width = width

    def getmaxyx(self):
        return self.height, self.width

    def erase(self) -> None:
        pass

    def refresh(self) -> None:
        pass

    def move(self, _y: int, _x: int) -> None:
        pass

    def addstr(self, *_args) -> None:
        pass


def test_enter_opens_the_selected_file(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "app.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    opened = []

    class StubEditor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def edit(self, path):
            opened.append(str(path))
            return True

    keys = iter([curses.KEY_DOWN, "\n", 27])
    monkeypatch.setattr(browser_module, "FullEditor", StubEditor)
    monkeypatch.setattr(browser_module, "read_key", lambda _screen: next(keys))

    browser = FileBrowser(FakeScreen(), Theme(), tmp_path)
    changed = browser.run()

    assert opened == [str(source)]
    assert changed == str(source)


def test_binary_file_error_is_reported(tmp_path: Path, monkeypatch) -> None:
    binary = tmp_path / "notes.txt"
    binary.write_bytes(b"\x00\x01\x02")

    keys = iter([curses.KEY_DOWN, "\n", 27])
    monkeypatch.setattr(browser_module, "read_key", lambda _screen: next(keys))

    browser = FileBrowser(FakeScreen(), Theme(), tmp_path)
    changed = browser.run()

    assert changed is None
    assert "binary" in browser.error.lower()


def test_draw_is_safe_in_a_tiny_terminal(tmp_path: Path) -> None:
    browser = FileBrowser(FakeScreen(height=6, width=20), Theme(), tmp_path)

    browser._draw()


def test_missing_start_folder_falls_back_safely(tmp_path: Path) -> None:
    browser = FileBrowser(FakeScreen(), Theme(), tmp_path / "missing")

    assert browser.nav.cwd.is_dir()
    browser._draw()
