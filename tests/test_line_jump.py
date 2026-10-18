"""Tests for ``filename:line`` jump queries."""

import tempfile
from pathlib import Path

from fuzzy_surgical.indexer import FileIndexer
from fuzzy_surgical.line_jump import LineJumpResolver
from fuzzy_surgical.query_parser import parse_line_reference
from fuzzy_surgical.tui_controller import TUIController

from tests.test_tui_focus import FakeWindow


def _file_lines():
    root = Path("/root")
    files = {
        str(root / "pkg" / "app.py"): [
            (number, f"line {number}") for number in range(1, 51)
        ],
        str(root / "pkg" / "app_utils.py"): [(1, "helper")],
        str(root / "deep" / "nested" / "app.py"): [(1, "nested")],
    }
    return root, files


def test_parse_line_reference_variants():
    reference = parse_line_reference("src/app.py:42")
    assert reference is not None
    assert reference.path == "src/app.py"
    assert reference.line == 42
    assert reference.label == "src/app.py:42"

    windows = parse_line_reference(r"C:\work\app.py:12")
    assert windows is not None
    assert windows.path == "work/app.py"
    assert windows.line == 12

    assert parse_line_reference("app.py:") is None
    assert parse_line_reference("app.py:0") is None
    assert parse_line_reference(":42") is None
    assert parse_line_reference("plain query") is None
    assert parse_line_reference("https://example.com:443") is None


def test_resolver_selects_the_requested_line():
    root, files = _file_lines()
    resolver = LineJumpResolver()
    reference = parse_line_reference("app.py:42")

    jump = resolver.resolve(reference, files, root)

    assert jump is not None
    assert jump.file_path == str(root / "pkg" / "app.py")
    assert jump.selected_line == 42
    assert jump.results[jump.selected_line - 1][1] == 42
    assert jump.notice == ""


def test_resolver_prefers_shorter_relative_path_on_ties():
    root, files = _file_lines()
    reference = parse_line_reference("app.py:1")

    jump = LineJumpResolver().resolve(reference, files, root)

    assert jump is not None
    assert jump.file_path == str(root / "pkg" / "app.py")


def test_resolver_accepts_path_suffix_and_partial_name():
    root, files = _file_lines()
    resolver = LineJumpResolver()

    suffix = resolver.resolve(parse_line_reference("deep/nested/app.py:1"), files, root)
    assert suffix is not None
    assert suffix.file_path == str(root / "deep" / "nested" / "app.py")

    partial = resolver.resolve(parse_line_reference("app_ut.py:1"), files, root)
    assert partial is not None
    assert partial.file_path == str(root / "pkg" / "app_utils.py")


def test_resolver_clamps_past_the_indexed_end():
    root, files = _file_lines()

    jump = LineJumpResolver().resolve(parse_line_reference("app.py:9000"), files, root)

    assert jump is not None
    assert jump.selected_line == 50
    assert "50 indexed lines" in jump.notice


def test_resolver_limits_window_around_the_target():
    root, files = _file_lines()

    jump = LineJumpResolver().resolve(
        parse_line_reference("app.py:42"),
        files,
        root,
        limit=10,
    )

    assert jump is not None
    assert len(jump.results) == 10
    assert jump.results[0][1] <= 42
    assert any(number == 42 for _, number, _, _ in jump.results)
    assert jump.selected_line == 42


def test_resolver_uses_the_preferred_line_after_an_edit():
    root, files = _file_lines()

    jump = LineJumpResolver().resolve(
        parse_line_reference("app.py:42"),
        files,
        root,
        prefer_file=str(root / "pkg" / "app.py"),
        prefer_line=20,
    )

    assert jump is not None
    assert jump.selected_line == 20


def test_resolver_ignores_unknown_files_and_plain_words():
    root, files = _file_lines()
    resolver = LineJumpResolver()

    assert resolver.resolve(parse_line_reference("missing.py:12"), files, root) is None
    assert resolver.resolve(parse_line_reference("time:30"), files, root) is None


def _controller(directory: Path) -> TUIController:
    indexer = FileIndexer()
    controller = TUIController(indexer, root_dir=directory)
    controller.stdscr = FakeWindow()
    controller.file_lines = indexer.index_directory(directory)
    return controller


def test_typed_jump_query_selects_and_draws_the_line():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        lines = [f"value {number}" for number in range(1, 31)]
        (directory / "target.py").write_text("\n".join(lines) + "\n")

        controller = _controller(directory)
        controller.query = "target.py:17"
        controller._update_results()
        controller._draw_ui()

        selected = controller.results[controller.selected_idx]
        assert selected[1] == 17
        assert selected[0].endswith("target.py")
        assert controller.jump_active is True
        assert "value 17" in controller.stdscr.text()


def test_jump_keeps_the_preferred_line_after_an_edit():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        lines = [f"value {number}" for number in range(1, 31)]
        file_path = directory / "target.py"
        file_path.write_text("\n".join(lines) + "\n")

        controller = _controller(directory)
        controller.query = "target.py:17"
        controller._update_results()
        indexed_path = controller.results[0][0]
        controller._update_results(preserve=(indexed_path, 24))

        selected = controller.results[controller.selected_idx]
        assert selected[1] == 24


def test_colon_number_stays_a_line_search_without_a_file_match():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        (directory / "notes.txt").write_text("retry at time:30 sharp\n")

        controller = _controller(directory)
        controller.query = "time:30"
        controller._update_results()

        assert controller.jump_active is False
        assert controller.results
        assert controller.results[0][2] == "retry at time:30 sharp"
