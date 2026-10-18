"""Tests for the soft-wrap layout shared by the editor view and mouse."""

from fuzzy_surgical import editor_layout as layout


def test_segment_counts_handle_empty_lines_and_exact_fits() -> None:
    assert layout.segment_count("", 10) == 1
    assert layout.segment_count("x" * 10, 10) == 1
    assert layout.segment_count("x" * 11, 10) == 2
    assert layout.segment_count("x" * 21, 10) == 3


def test_segment_counts_expand_tabs_before_wrapping() -> None:
    assert layout.display_length("\tbeta") == 8
    assert layout.segment_count("\t" * 3, 4) == 3


def test_caret_segment_moves_to_the_next_row_at_a_wrap_boundary() -> None:
    line = "x" * 100

    assert layout.caret_segment(line, 0, 10) == (0, 0)
    assert layout.caret_segment(line, 9, 10) == (0, 9)
    assert layout.caret_segment(line, 10, 10) == (1, 0)
    assert layout.caret_segment(line, 100, 10) == (9, 10)
    assert layout.caret_segment(line, 999, 10) == (9, 10)


def test_exact_fit_keeps_the_caret_on_the_last_row() -> None:
    assert layout.caret_segment("x" * 10, 10, 10) == (0, 10)


def test_visible_segments_span_wrapped_and_empty_lines() -> None:
    lines = ["a" * 15, "", "b"]

    segments = layout.visible_segments(lines, 0, 0, rows=10, width=10)

    assert [(item.row, item.part, item.start_cell, item.cells) for item in segments] == [
        (0, 0, 0, 10),
        (0, 1, 10, 5),
        (1, 0, 0, 0),
        (2, 0, 0, 1),
    ]


def test_visible_segments_stop_at_the_row_budget() -> None:
    segments = layout.visible_segments(["a" * 50], 0, 0, rows=2, width=10)

    assert [(item.row, item.part) for item in segments] == [(0, 0), (0, 1)]


def test_screen_offset_counts_wrapped_rows() -> None:
    lines = ["a" * 15, "", "b"]

    assert layout.screen_offset(lines, 0, 0, 0, 0, 10, 10) == 0
    assert layout.screen_offset(lines, 0, 0, 0, 1, 10, 10) == 1
    assert layout.screen_offset(lines, 0, 0, 2, 0, 10, 10) == 3
    assert layout.screen_offset(lines, 2, 0, 0, 0, 10, 10) == -1
    assert layout.screen_offset(lines, 0, 0, 2, 0, 10, 2) is None


def test_top_for_caret_never_scrolls_before_the_document_start() -> None:
    lines = ["a" * 15, "", "b"]

    assert layout.top_for_caret(lines, 2, 0, 1, 10) == (1, 0)
    assert layout.top_for_caret(lines, 2, 0, 10, 10) == (0, 0)


def test_move_display_rows_keeps_the_visual_column() -> None:
    lines = ["a" * 15, "bcdef"]

    assert layout.move_display_rows(lines, 0, 2, 1, 10) == (0, 12)
    assert layout.move_display_rows(lines, 0, 12, 1, 10) == (1, 2)
    assert layout.move_display_rows(lines, 1, 2, -1, 10) == (0, 12)
    assert layout.move_display_rows(lines, 0, 2, -1, 10) == (0, 2)
    assert layout.move_display_rows(lines, 1, 5, 1, 10) == (1, 5)


def test_next_segment_stops_at_the_document_end() -> None:
    lines = ["a" * 15, "b"]

    assert layout.next_segment(lines, 0, 0, 10) == (0, 1)
    assert layout.next_segment(lines, 0, 1, 10) == (1, 0)
    assert layout.next_segment(lines, 1, 0, 10) is None
