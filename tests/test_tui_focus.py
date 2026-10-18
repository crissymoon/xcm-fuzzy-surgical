"""Headless checks that the result view lands on the matched text."""

import curses
import tempfile
from pathlib import Path

from fuzzy_surgical.indexer import FileIndexer
from fuzzy_surgical.tui_controller import TUIController


class FakeWindow:
    """Minimal curses surface that records drawn text row by row."""

    def __init__(self, height: int = 12, width: int = 100) -> None:
        self.height = height
        self.width = width
        self.rows = [[" "] * width for _ in range(height)]

    def getmaxyx(self):
        return self.height, self.width

    def erase(self) -> None:
        self.rows = [[" "] * self.width for _ in range(self.height)]

    def refresh(self) -> None:
        return None

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        if y < 0 or y >= self.height or x < 0 or x >= self.width:
            raise curses.error()
        for offset, char in enumerate(text):
            if x + offset >= self.width - 1:
                break
            self.rows[y][x + offset] = char

    def move(self, y: int, x: int) -> None:
        return None

    def text(self) -> str:
        return "\n".join("".join(row) for row in self.rows)


def _controller(directory: Path) -> TUIController:
    indexer = FileIndexer()
    controller = TUIController(indexer, root_dir=directory)
    controller.stdscr = FakeWindow()
    controller.file_lines = indexer.index_directory(directory)
    return controller


def test_long_line_scrolls_to_the_match_region():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        content = "a" * 120 + " needle here\n"
        (directory / "long.py").write_text(content)

        controller = _controller(directory)
        controller.query = "needle"
        controller._update_results()
        controller._draw_ui()

        assert "needle here" in controller.stdscr.text()


def test_results_keep_the_edited_line_selected():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        lines = [f"value {number} = needle" for number in range(1, 21)]
        (directory / "many.py").write_text("\n".join(lines) + "\n")

        controller = _controller(directory)
        controller.query = "needle"
        controller._update_results()

        target = controller.results[-1]
        controller._update_results(preserve=(target[0], target[1]))

        selected = controller.results[controller.selected_idx]
        assert (selected[0], selected[1]) == (target[0], target[1])


def test_enter_opens_the_one_editor_at_the_matched_line(monkeypatch):
    import fuzzy_surgical.tui_controller as tui_module

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        (directory / "app.py").write_text("alpha\nneedle\nomega\n")

        controller = _controller(directory)
        controller.query = "needle"
        controller._update_results()
        calls = []

        def stub_edit_file(_screen, _theme, _history, _devbox, path, start_line=0):
            calls.append((path, start_line))
            return False

        monkeypatch.setattr(tui_module, "edit_file", stub_edit_file)
        controller._open_editor()

        assert calls == [(str(directory / "app.py"), 2)]
