"""Tests for the right-click context menu geometry and selection."""

from __future__ import annotations

from fuzzy_surgical.colors import Theme
from fuzzy_surgical.context_menu import ContextMenu, MenuItem


class FakeWindow:
    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.height = height
        self.width = width
        self.cells = {}

    def getmaxyx(self):
        return self.height, self.width

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        for offset, char in enumerate(text):
            self.cells[(y, x + offset)] = char


def _items():
    return [
        MenuItem("cut", "Cut", "Ctrl+X", False, "x"),
        MenuItem("copy", "Copy", "Ctrl+C", False, "c"),
        MenuItem("paste", "Paste", "Ctrl+V", True, "v"),
        MenuItem("save", "Save", "Ctrl+S", True, "s"),
    ]


def test_open_skips_disabled_items_and_places_below_the_pointer() -> None:
    menu = ContextMenu()

    assert menu.open(4, 4, _items(), 24, 80) is True

    assert menu.visible
    assert menu.box_top == 5
    assert menu.box_left == 5
    assert menu.selected_item() is not None
    assert menu.selected_item().action == "paste"


def test_move_wraps_and_skips_disabled_rows() -> None:
    menu = ContextMenu()
    menu.open(0, 0, _items(), 24, 80)

    menu.move(1)
    assert menu.selected_item().action == "save"
    menu.move(1)
    assert menu.selected_item().action == "paste"
    menu.move(-1)
    assert menu.selected_item().action == "save"


def test_item_at_maps_screen_cells_to_items() -> None:
    menu = ContextMenu()
    menu.open(0, 0, _items(), 24, 80)

    first = menu.item_at(menu.box_left + 2, menu.box_top + 1)
    paste = menu.item_at(menu.box_left + 2, menu.box_top + 3)

    assert first is None  # Cut is disabled
    assert paste is not None and paste.action == "paste"
    assert menu.item_at(0, 20) is None
    assert not menu.contains(0, 20)


def test_menu_flips_up_near_the_bottom_edge() -> None:
    menu = ContextMenu()
    menu.open(4, 23, _items(), 24, 80)

    assert menu.box_top + menu.box_height <= 24
    assert menu.box_top < 23


def test_shortcut_letter_selects_a_row() -> None:
    menu = ContextMenu()
    menu.open(0, 0, _items(), 24, 80)

    assert menu.select_matching("s") is True
    assert menu.selected_item().action == "save"
    assert menu.select_matching("x") is False


def test_draw_puts_labels_on_screen() -> None:
    menu = ContextMenu()
    menu.open(0, 0, _items(), 24, 80)
    window = FakeWindow()

    menu.draw(window, Theme(), 24, 80)
    text = "".join(window.cells.values())

    assert "Paste" in text
    assert "Save" in text
    assert "Actions" in text
