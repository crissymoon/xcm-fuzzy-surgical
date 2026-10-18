"""Tests for mouse event normalisation and click to caret mapping."""

from __future__ import annotations

from fuzzy_surgical import editor_mouse as editor_mouse_module
from fuzzy_surgical import mouse, tui_controller as tui_module
from fuzzy_surgical.selection import ClipboardResult
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
    monkeypatch.setattr(mouse, "_RELEASE_BITS", 0x20)
    monkeypatch.setattr(mouse, "_SCROLL_UP_BITS", 0x02)
    monkeypatch.setattr(mouse, "_SCROLL_DOWN_BITS", 0x04)
    monkeypatch.setattr(mouse, "_REPORT_BIT", 0x08)
    monkeypatch.setattr(mouse, "_LEGACY_SCROLL_DOWN", False)
    mouse.reset_input_state()

    click = mouse.event_from_state(4, 6, 0x01)
    assert click == MouseEvent(4, 6, "click")
    assert click.is_click and not click.is_scroll

    up = mouse.event_from_state(4, 6, 0x02)
    assert up is not None and up.direction == -1

    down = mouse.event_from_state(4, 6, 0x04)
    assert down is not None and down.direction == 1

    assert mouse.event_from_state(4, 6, 0x10) is None

    release = mouse.event_from_state(4, 6, 0x20)
    assert release is not None and release.is_release

    drag = mouse.event_from_state(4, 6, 0x09)
    assert drag is not None and drag.is_drag


def test_old_ncurses_wheel_down_is_a_position_report(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "_PRIMARY_CLICK_BITS", 0x01)
    monkeypatch.setattr(mouse, "_RELEASE_BITS", 0x20)
    monkeypatch.setattr(mouse, "_SCROLL_UP_BITS", 0x02)
    monkeypatch.setattr(mouse, "_SCROLL_DOWN_BITS", 0)
    monkeypatch.setattr(mouse, "_REPORT_BIT", 0x08)
    monkeypatch.setattr(mouse, "_LEGACY_SCROLL_DOWN", True)
    mouse.reset_input_state()

    event = mouse.event_from_state(10, 5, 0x08)
    assert event is not None
    assert event.is_scroll and event.direction == 1

    click = mouse.event_from_state(10, 5, 0x01)
    assert click is not None and click.is_click

    drag = mouse.event_from_state(10, 5, 0x08)
    assert drag is not None and drag.is_drag

    release = mouse.event_from_state(10, 5, 0x20)
    assert release is not None and release.is_release

    wheel = mouse.event_from_state(10, 5, 0x08)
    assert wheel is not None and wheel.is_scroll and wheel.direction == 1


def test_click_tracker_dedupes_pairs_and_finds_double_clicks() -> None:
    now = [0.0]
    tracker = ClickTracker(clock=lambda: now[0])
    event = MouseEvent(3, 4, "click")

    assert tracker.classify(event) == "single"
    now[0] = 0.02
    assert tracker.classify(event) is None
    now[0] = 0.15
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
    assert controller.selected_idx == 6


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


def test_middle_button_is_ignored_and_right_button_opens_a_menu() -> None:
    import curses

    middle = int(getattr(curses, "BUTTON2_PRESSED", 0))
    if middle:
        assert mouse.event_from_state(1, 1, middle) is None

    right = int(getattr(curses, "BUTTON3_PRESSED", 0))
    if right:
        event = mouse.event_from_state(1, 1, right)
        assert event is not None
        assert event.is_right_click
        assert not event.is_click


def test_right_click_burst_is_suppressed(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "_RIGHT_CLICK_BITS", 0x2000)
    mouse.reset_input_state()

    first = mouse.normalise_event(4, 4, 0x2000, now=10.0)
    second = mouse.normalise_event(4, 4, 0x2000, now=10.05)
    later = mouse.normalise_event(4, 4, 0x2000, now=10.5)

    assert first is not None and first.is_right_click
    assert second is None
    assert later is not None and later.is_right_click


def test_full_editor_scroll_centers_the_caret_on_the_wheel() -> None:
    editor = FullEditor(FakeScreen(24, 80), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer(
        "\n".join(f"line {index:03d} " + "x" * 60 for index in range(100))
    )
    editor.buffer.set_cursor(30, 30)
    editor.scroll_row = 20
    editor.scroll_part = 0

    editor._scroll_view(mouse.SCROLL_LINES)

    center = editor._content_rows(24) // 2
    assert editor.buffer.cursor_row == 30 + mouse.SCROLL_LINES
    assert editor.buffer.cursor_row - editor.scroll_row == center
    assert editor.buffer.cursor_col == 30
    assert editor.scroll_part == 0


def test_full_editor_scroll_stops_with_the_caret_at_the_file_edges() -> None:
    editor = FullEditor(FakeScreen(24, 80), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("\n".join(f"line {index}" for index in range(100)))
    editor.buffer.set_cursor(1, 0)
    editor.scroll_row = 0
    editor.scroll_part = 0

    editor._scroll_view(-mouse.SCROLL_LINES)
    assert editor.buffer.cursor_row == 0
    assert editor.scroll_row == 0

    rows = editor._content_rows(24)
    last = editor.buffer.line_count - 1
    editor.buffer.set_cursor(last, 0)
    editor.scroll_row = max(0, editor.buffer.line_count - rows)
    editor._scroll_view(mouse.SCROLL_LINES)
    assert editor.buffer.cursor_row == last
    assert editor.scroll_row == max(0, editor.buffer.line_count - rows)


def test_end_of_file_fills_the_viewport_without_blank_rows() -> None:
    editor = FullEditor(FakeScreen(24, 80), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("\n".join(f"line {index:03d}" for index in range(300)))
    editor.buffer.goto_line(300)
    editor.scroll_row = 0
    editor.scroll_part = 0

    editor._center_caret(24, 80)

    segments = editor._visible_segments(24, 80)
    assert len(segments) == editor._content_rows(24)
    assert segments[-1].row == editor.buffer.line_count - 1
    assert editor.scroll_row == editor.buffer.line_count - editor._content_rows(24)


def test_long_lines_wrap_into_clickable_continuation_rows() -> None:
    editor = FullEditor(FakeScreen(24, 40), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("abcdefghij" * 10 + "\nsecond")
    editor.scroll_row = 0
    editor.scroll_part = 0
    gutter = editor._gutter_width()
    available = editor._wrap_width(40)

    assert available == 40 - gutter - 1
    assert editor._buffer_position(2, gutter) == (0, 0)
    assert editor._buffer_position(3, gutter) == (0, available)
    assert editor._buffer_position(2, gutter + available - 1) == (0, available - 1)


def test_caret_follows_a_wrapped_segment_and_centers_on_open() -> None:
    editor = FullEditor(FakeScreen(24, 40), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("abcdefghij" * 10)
    editor.buffer.set_cursor(0, 70)
    editor.scroll_row = 0
    editor.scroll_part = 0
    gutter = editor._gutter_width()
    available = editor._wrap_width(40)

    row, column = editor._caret_screen(24, 40)

    assert row == 2 + 70 // available
    assert column == gutter + 70 % available

    editor._pending_center = True
    editor._center_caret(24, 40)
    assert editor.scroll_part == 0
    assert row > 2


def test_shrinking_the_terminal_rewraps_without_losing_the_caret() -> None:
    screen = FakeScreen(24, 80)
    editor = FullEditor(screen, None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("z" * 70)
    editor.buffer.set_cursor(0, 65)
    editor.scroll_row = 0
    editor.scroll_part = 0

    wide_row, wide_column = editor._caret_screen(24, 80)
    screen.width = 40
    narrow_row, narrow_column = editor._caret_screen(24, 40)

    assert wide_row == 2
    assert narrow_row > wide_row
    assert narrow_column < wide_column
    assert editor._buffer_position(narrow_row, narrow_column) == (0, 65)


def test_click_echoing_a_scroll_becomes_a_scroll(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "_PRIMARY_CLICK_BITS", 0x01)
    monkeypatch.setattr(mouse, "_RELEASE_BITS", 0x20)
    monkeypatch.setattr(mouse, "_SCROLL_UP_BITS", 0x02)
    monkeypatch.setattr(mouse, "_SCROLL_DOWN_BITS", 0x04)
    monkeypatch.setattr(mouse, "_REPORT_BIT", 0x08)
    monkeypatch.setattr(mouse, "_LEGACY_SCROLL_DOWN", False)
    mouse.reset_input_state()

    assert mouse.normalise_event(4, 5, 0x04, now=10.0) == MouseEvent(4, 5, "scroll", 1)
    assert mouse.normalise_event(4, 5, 0x01, now=10.2) == MouseEvent(4, 5, "scroll", 1)
    assert mouse.normalise_event(4, 5, 0x01, now=10.6) == MouseEvent(4, 5, "click")
    assert mouse.normalise_event(20, 5, 0x01, now=10.2) == MouseEvent(20, 5, "click")


def test_scroll_gate_moves_a_mouse_notch_at_once() -> None:
    now = [0.0]
    gate = mouse.ScrollGate(clock=lambda: now[0])

    assert gate.lines(1) == mouse.SCROLL_LINES
    assert gate.lines(0) == 0


def test_scroll_gate_accumulates_trackpad_events() -> None:
    now = [0.0]
    gate = mouse.ScrollGate(clock=lambda: now[0])
    total = gate.lines(1)

    for _ in range(9):
        now[0] += 0.012
        total += gate.lines(1)

    # The old gate returned one row every 70 ms, which felt disconnected from
    # the finger. Nine rapid events now produce three rows.
    assert total == mouse.SCROLL_LINES + 3


def test_scroll_gate_caps_trackpad_momentum() -> None:
    now = [0.0]
    gate = mouse.ScrollGate(clock=lambda: now[0])

    gate.lines(1)
    total = 0
    for _ in range(100):
        now[0] += 0.001
        total += gate.lines(1)

    ceiling = now[0] * mouse.SCROLL_MAX_RATE
    assert 0 < total <= ceiling + 1


def test_scroll_gate_applies_a_reversal_at_once() -> None:
    now = [0.0]
    gate = mouse.ScrollGate(clock=lambda: now[0])
    gate.lines(1)

    now[0] = 0.010
    gate.lines(1)
    now[0] = 0.020
    assert gate.lines(-1) == -mouse.SCROLL_LINES

    gate.reset()
    assert gate.lines(1) == mouse.SCROLL_LINES


def test_scroll_gate_uses_one_row_per_notch_when_asked() -> None:
    now = [0.0]
    gate = mouse.ScrollGate(clock=lambda: now[0])

    assert gate.lines(-1, 1) == -1
    now[0] = 1.0
    assert gate.lines(1, 1) == 1


def test_enable_mouse_requests_button_event_tracking(monkeypatch) -> None:
    written = []
    monkeypatch.setattr(mouse, "_write_terminal", lambda seq: written.append(seq) or True)
    monkeypatch.setattr(mouse.curses, "mousemask", lambda mask: (mask, 0))
    monkeypatch.setattr(mouse.curses, "mouseinterval", lambda value: None)
    monkeypatch.setattr(mouse, "_ACTIVE", False)
    monkeypatch.setattr(mouse, "_EXTENDED_TRACKING", False)

    assert mouse.enable_mouse(None) is True
    assert mouse.BUTTON_EVENT_TRACKING_ON in written
    assert "\x1b[?1002h" in mouse.BUTTON_EVENT_TRACKING_ON
    assert "\x1b[?1006l" in mouse.BUTTON_EVENT_TRACKING_ON
    assert mouse.extended_tracking_enabled() is True

    mouse.disable_mouse()
    assert mouse.BUTTON_EVENT_TRACKING_OFF in written
    assert mouse.extended_tracking_enabled() is False


def test_drag_needs_a_held_button_and_a_release_clears_it(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "_PRIMARY_CLICK_BITS", 0x01)
    monkeypatch.setattr(mouse, "_RELEASE_BITS", 0x20)
    monkeypatch.setattr(mouse, "_SCROLL_UP_BITS", 0x02)
    monkeypatch.setattr(mouse, "_SCROLL_DOWN_BITS", 0)
    monkeypatch.setattr(mouse, "_REPORT_BIT", 0x08)
    monkeypatch.setattr(mouse, "_LEGACY_SCROLL_DOWN", True)
    monkeypatch.setattr(mouse, "_RIGHT_CLICK_BITS", 0)
    mouse.reset_input_state()

    assert mouse.event_from_state(3, 4, 0x01) == MouseEvent(3, 4, "click")
    assert mouse.event_from_state(5, 4, 0x08) == MouseEvent(5, 4, "drag")
    assert mouse.event_from_state(5, 4, 0x20) == MouseEvent(5, 4, "release")
    # Motion after the release is a bare position report, which the legacy
    # build uses for wheel down. It must not continue the drag.
    assert mouse.event_from_state(6, 4, 0x08) == MouseEvent(6, 4, "scroll", 1)


def test_wheel_clears_a_stale_held_button(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "_PRIMARY_CLICK_BITS", 0x01)
    monkeypatch.setattr(mouse, "_RELEASE_BITS", 0x20)
    monkeypatch.setattr(mouse, "_SCROLL_UP_BITS", 0x02)
    monkeypatch.setattr(mouse, "_SCROLL_DOWN_BITS", 0)
    monkeypatch.setattr(mouse, "_REPORT_BIT", 0x08)
    monkeypatch.setattr(mouse, "_LEGACY_SCROLL_DOWN", True)
    monkeypatch.setattr(mouse, "_RIGHT_CLICK_BITS", 0)
    mouse.reset_input_state()

    assert mouse.event_from_state(3, 4, 0x01) == MouseEvent(3, 4, "click")
    assert mouse.event_from_state(3, 4, 0x02) == MouseEvent(3, 4, "scroll", -1)
    assert mouse.event_from_state(3, 4, 0x08) == MouseEvent(3, 4, "scroll", 1)


def test_click_echo_guard_is_off_when_motion_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "_PRIMARY_CLICK_BITS", 0x01)
    monkeypatch.setattr(mouse, "_RELEASE_BITS", 0x20)
    monkeypatch.setattr(mouse, "_SCROLL_UP_BITS", 0x02)
    monkeypatch.setattr(mouse, "_SCROLL_DOWN_BITS", 0x04)
    monkeypatch.setattr(mouse, "_REPORT_BIT", 0x08)
    monkeypatch.setattr(mouse, "_LEGACY_SCROLL_DOWN", False)
    monkeypatch.setattr(mouse, "_EXTENDED_TRACKING", True)
    monkeypatch.setattr(mouse, "_RIGHT_CLICK_BITS", 0)
    mouse.reset_input_state()

    assert mouse.normalise_event(4, 5, 0x04, now=10.0) == MouseEvent(4, 5, "scroll", 1)
    assert mouse.normalise_event(4, 5, 0x01, now=10.1) == MouseEvent(4, 5, "click")


def test_f10_opens_the_actions_menu_from_the_keyboard() -> None:
    import curses

    editor = FullEditor(FakeScreen(24, 80), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("alpha zebracorn delta\nsecond line\n")
    editor.buffer.set_cursor(0, 8)

    assert editor._handle_edit_key(curses.KEY_F10) is False
    assert editor.menu.visible
    labels = [item.label for item in editor.menu.items]
    assert "Copy" in labels and "Paste" in labels


def test_release_reports_the_selection_size(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "_EXTENDED_TRACKING", True)
    monkeypatch.setattr(
        editor_mouse_module,
        "copy_to_clipboard",
        lambda text: ClipboardResult(True, f"Copied {len(text)} characters.", "pbpaste"),
    )
    editor = FullEditor(FakeScreen(24, 80), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("alpha zebracorn delta\nsecond line\n")
    editor.scroll_row = 0

    editor._handle_mouse(MouseEvent(6, 2, "click"))
    editor._handle_mouse(MouseEvent(14, 2, "drag"))
    editor._handle_mouse(MouseEvent(14, 2, "release"))

    assert editor.selection.has_text
    assert "characters selected" not in editor.notice
    assert "Copied" in editor.notice
    assert editor.clipboard_text == editor.selection.text(editor.buffer.lines)
    assert editor.clipboard_text
    assert "Ctrl+V" in editor.notice


def test_release_without_a_clipboard_still_reports_the_selection(monkeypatch) -> None:
    monkeypatch.setattr(mouse, "_EXTENDED_TRACKING", True)
    monkeypatch.setattr(
        editor_mouse_module,
        "copy_to_clipboard",
        lambda text: ClipboardResult(False, "No clipboard tool found.", "missing"),
    )
    editor = FullEditor(FakeScreen(24, 80), None)
    editor.path = "/tmp/app.py"
    editor.buffer = TextBuffer("alpha zebracorn delta\nsecond line\n")
    editor.scroll_row = 0

    editor._handle_mouse(MouseEvent(6, 2, "click"))
    editor._handle_mouse(MouseEvent(14, 2, "drag"))
    editor._handle_mouse(MouseEvent(14, 2, "release"))

    assert "Selected" in editor.notice
    assert "Ctrl+C" in editor.notice
