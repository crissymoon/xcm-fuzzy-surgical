"""Tests for the file browser view without starting a real terminal."""

from __future__ import annotations

from pathlib import Path

import curses

from fuzzy_surgical import file_browser as browser_module
from fuzzy_surgical.colors import Theme
from fuzzy_surgical.file_browser import FileBrowser
from fuzzy_surgical.mouse import ClickTracker, MouseEvent


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

    def stub_edit_file(_screen, _theme, _history, _devbox, path, start_line=0, **_kwargs):
        opened.append((str(path), start_line))
        return True

    keys = iter([curses.KEY_DOWN, "\n", 27])
    monkeypatch.setattr(browser_module, "edit_file", stub_edit_file)
    monkeypatch.setattr(browser_module, "read_key", lambda _screen: next(keys))

    browser = FileBrowser(FakeScreen(), Theme(), tmp_path)
    changed = browser.run()

    assert opened == [(str(source), 0)]
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


def test_mouse_click_selects_a_row_and_wheel_scrolls(tmp_path: Path) -> None:
    for name in ("a.py", "b.py", "c.py", "d.py", "e.py"):
        (tmp_path / name).write_text("print('x')\n", encoding="utf-8")
    browser = FileBrowser(FakeScreen(), Theme(), tmp_path)
    target = next(
        index
        for index, entry in enumerate(browser.entries)
        if entry.path.name == "c.py"
    )

    browser._handle_mouse(MouseEvent(10, 4 + target, "click"))
    assert browser.selected == target

    before = browser.selected
    browser._handle_mouse(MouseEvent(10, 10, "scroll", 1))
    assert browser.selected > before


def test_double_click_activates_the_row(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('x')\n", encoding="utf-8")
    browser = FileBrowser(FakeScreen(), Theme(), tmp_path)
    now = [0.0]
    browser.click_tracker = ClickTracker(clock=lambda: now[0])
    opened = []
    browser._activate = lambda: opened.append(browser.selected)
    target = next(
        index
        for index, entry in enumerate(browser.entries)
        if entry.path.name == "a.py"
    )
    event = MouseEvent(10, 4 + target, "click")

    browser._handle_mouse(event)
    assert opened == []

    now[0] = 0.3
    browser._handle_mouse(event)
    assert opened == [target]


def test_ctrl_n_creates_a_file_and_opens_the_editor(tmp_path: Path, monkeypatch) -> None:
    opened = []

    def stub_edit_file(_screen, _theme, _history, _devbox, path, start_line=0, **_kwargs):
        opened.append(str(path))
        return True

    keys = iter([14, "n", "e", "w", ".", "p", "y", "\n", 27])
    monkeypatch.setattr(browser_module, "edit_file", stub_edit_file)
    monkeypatch.setattr(browser_module, "read_key", lambda _screen: next(keys))

    browser = FileBrowser(FakeScreen(), Theme(), tmp_path)
    changed = browser.run()

    created = tmp_path / "new.py"
    assert created.is_file()
    assert opened == [str(created)]
    assert changed == str(created)


def test_ctrl_n_escape_cancels_without_creating(tmp_path: Path, monkeypatch) -> None:
    keys = iter([14, "a", "b", 27, 27])
    monkeypatch.setattr(browser_module, "read_key", lambda _screen: next(keys))

    browser = FileBrowser(FakeScreen(), Theme(), tmp_path)
    changed = browser.run()

    assert changed is None
    assert browser.creating is False
    assert list(tmp_path.iterdir()) == []


def test_ctrl_n_never_overwrites_an_existing_file(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "taken.py").write_text("x\n", encoding="utf-8")
    keys = iter([14, "t", "a", "k", "e", "n", ".", "p", "y", "\n", 27, 27])
    monkeypatch.setattr(browser_module, "read_key", lambda _screen: next(keys))

    browser = FileBrowser(FakeScreen(), Theme(), tmp_path)
    changed = browser.run()

    assert changed is None
    assert browser.creating is False
    assert (tmp_path / "taken.py").read_text(encoding="utf-8") == "x\n"
