"""Tests for mouse event normalisation and click to caret mapping."""

from __future__ import annotations

from fuzzy_surgical import mouse, tui_controller as tui_module
from fuzzy_surgical.editor_controller import EditorController
from fuzzy_surgical.full_editor import FullEditor, TextBuffer
from fuzzy_surgical.indexer import FileIndexer
from fuzzy_surgical.mouse import ClickTracker, MouseEvent
from fuzzy_surgical.renderer import index_for_display_column
from fuzzy_surgical.tui_controller import TUIController


class FakeScreen:
    """Window stub that only needs a size for coordinate mapping."""

    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.height = height
        self.width = width

    def getmaxyx(self):
        return self.height, self.width


def test_click_and_wheel_states_become_events(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "_PRIMARY_CLICK_BITS", 0x01)
    monkeypatch.setattr(mouse, "_SCROLL_UP_BITS", 0x02)
    monkeypatch.setattr(mouse, "_SCROLL_DOWN_BITS", 0x04)
    monkeypatch.setattr(mouse, "_REPORT_BIT", 0x08)
    monkeypatch.setattr(mouse, "_LEGACY_SCROLL_DOWN", False)

    click = mouse.event_from_state(4, 6, 0x01)
    assert click == MouseEvent(4, 6, "click")
    assert click.is_click and not click.is_scroll

    up = mouse.event_from_state(4, 6, 0x02)
    assert up is not None and up.direction == -1

    down = mouse.event_from_state(4, 6, 0x04)
    assert down is not None and down.direction == 1

    assert mouse.event_from_state(4, 6, 0x10) is None


def test_old_ncurses_wheel_down_is_a_position_report(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "_PRIMARY_CLICK_BITS", 0x01)
    monkeypatch.setattr(mouse, "_SCROLL_UP_BITS", 0x02)
    monkeypatch.setattr(mouse, "_SCROLL_DOWN_BITS", 0)
    monkeypatch.setattr(mouse, "_REPORT_BIT", 0x08)
    monkeypatch.setattr(mouse, "_LEGACY_SCROLL_DOWN", True)

    event = mouse.event_from_state(10, 5, 0x08)
    assert event is not None
    assert event.is_scroll and event.direction == 1

    click = mouse.event_from_state(10, 5, 0x09)
    assert click is not None and click.is_click


def test_click_tracker_dedupes_pairs_and_finds_double_clicks() -> None:
    now = [0.0]
    tracker = ClickTracker(clock=lambda: now[0])
    event = MouseEvent(3, 4, "click")

    assert tracker.classify(event) == "single"
    now[0] = 0.10
    assert tracker.classify(event) is None
    now[0] = 0.35
    assert tracker.classify(event) == "double"

    now[0] = 1.0
    assert tracker.classify(MouseEvent(3, 4, "click")) == "single"


def test_click_tracker_ignores_scroll_events() -> None:
    tracker = ClickTracker(clock=lambda: 0.0)
    assert tracker.classify(MouseEvent(1, 1, "scroll", -1)) is None


def test_display_columns_expand_tabs() -> None:
    line = "\tbeta"
    assert index_for_display_column(line, 0) == 0
    assert index_for_display_column(line, 2) == 0
    assert index_for_display_column(line, 4) == 1
    assert index_for_display_column(line, 5) == 2
    assert index_for_display_column(line, 99) == len(line)
    assert index_for_display_column("abc", -1) == 0


def test_full_editor_maps_a_click_to_the_line_and_column() -> None:
    editor = FullEditor(FakeScreen(), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("alpha\n\tbeta")
    editor.scroll_row = 0
    gutter = editor._gutter_width()

    assert editor._buffer_position(2, gutter + 2) == (0, 2)
    assert editor._buffer_position(3, gutter + 5) == (1, 2)
    assert editor._buffer_position(2, 0) == (0, 0)
    assert editor._buffer_position(1, 10) is None
    assert editor._buffer_position(9, 10) is None


def test_line_editor_click_places_caret_and_switches_context() -> None:
    editor = EditorController()
    editor.context_lines = [(10, "one"), (11, "two")]
    editor.active_ctx_idx = 0
    editor.edited_line = "abcdef"
    editor.cursor_pos = 6
    editor.ctx_start = 0
    editor.visible_ctx_count = 2
    editor.edit_row = 6
    editor.edit_view_start = 0

    editor._handle_mouse(MouseEvent(2, 6, "click"))
    assert editor.cursor_pos == 2

    editor._handle_mouse(MouseEvent(5, 3, "click"))
    assert editor.active_ctx_idx == 1
    assert editor.edited_line == "two"

    editor._handle_mouse(MouseEvent(1, 6, "click"))
    assert editor.cursor_pos == 1
    editor._handle_mouse(MouseEvent(20, 6, "click"))
    assert editor.cursor_pos == 3


def test_line_editor_wheel_moves_the_context_line() -> None:
    editor = EditorController()
    editor.context_lines = [(1, "one"), (2, "two"), (3, "three")]
    editor.active_ctx_idx = 0
    editor.edited_line = "one"

    editor._handle_mouse(MouseEvent(0, 0, "scroll", 1))

    assert editor.active_ctx_idx == 1
    assert editor.edited_line == "two"


def test_search_view_click_selects_a_result(monkeypatch) -> None:
    controller = TUIController(FileIndexer())
    controller.stdscr = FakeScreen()
    controller.results = [
        ("/tmp/f%d.py" % index, index, "line", 0.0) for index in range(1, 9)
    ]
    controller.selected_idx = 0
    controller.scroll_offset = 0

    monkeypatch.setattr(
        tui_module, "read_mouse", lambda _screen: MouseEvent(0, 5, "click")
    )
    controller._handle_mouse()
    assert controller.selected_idx == 2

    monkeypatch.setattr(
        tui_module, "read_mouse", lambda _screen: MouseEvent(0, 5, "scroll", 1)
    )
    controller._handle_mouse()
    assert controller.selected_idx == 5


def test_search_view_double_click_opens_the_editor(monkeypatch) -> None:
    controller = TUIController(FileIndexer())
    controller.stdscr = FakeScreen()
    controller.results = [("/tmp/f1.py", 1, "line", 0.0)]
    opened = []
    controller._open_editor = lambda: opened.append(True)
    now = [0.0]
    controller.click_tracker = ClickTracker(clock=lambda: now[0])
    monkeypatch.setattr(
        tui_module, "read_mouse", lambda _screen: MouseEvent(0, 3, "click")
    )

    controller._handle_mouse()
    assert opened == []

    now[0] = 0.3
    controller._handle_mouse()
    assert opened == [True]


def test_horizontal_wheel_ticks_are_not_clicks() -> None:
    import curses

    for name in ("BUTTON2_PRESSED", "BUTTON3_PRESSED"):
        state = int(getattr(curses, name, 0))
        if state:
            assert mouse.event_from_state(1, 1, state) is None


def test_scroll_gate_limits_a_trackpad_burst() -> None:
    now = [0.0]
    gate = mouse.ScrollGate(clock=lambda: now[0])

    assert gate.lines(1) == mouse.SCROLL_LINES
    for step in range(1, 9):
        now[0] = step * 0.008
        assert gate.lines(1) == 0
    now[0] = 0.072
    assert gate.lines(1) == mouse.SCROLL_BURST_LINES
    assert gate.lines(0) == 0


def test_scroll_gate_uses_one_row_per_notch_when_asked() -> None:
    now = [0.0]
    gate = mouse.ScrollGate(clock=lambda: now[0])

    assert gate.lines(-1, 1) == -1
    now[0] = 1.0
    assert gate.lines(1, 1) == 1
