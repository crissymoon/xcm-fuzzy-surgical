"""Tests for edits made outside the TUI and for save conflicts."""

from fuzzy_surgical.indexer import FileIndexer


def _index(tmp_path, name="app.py", text="one\n"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    indexer = FileIndexer()
    indexer.index_directory(tmp_path)
    return path, indexer


def test_indexer_refreshes_changed_file(tmp_path):
    path, indexer = _index(tmp_path)

    path.write_text("one\ntwo\n", encoding="utf-8")
    changed = indexer.reindex_changed()

    assert str(path) in changed
    assert indexer.file_lines[str(path)] == [(1, "one"), (2, "two")]
    assert indexer.total_lines == 2


def test_indexer_drops_deleted_file(tmp_path):
    path, indexer = _index(tmp_path)
    path.unlink()

    changed = indexer.reindex_changed()

    assert str(path) in changed
    assert str(path) not in indexer.file_lines


def test_saved_file_watch_warns_when_another_program_rewrites_it(tmp_path):
    import time

    from fuzzy_surgical.file_writer import file_digest
    from fuzzy_surgical.tui_controller import TUIController

    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")
    indexer = FileIndexer()
    indexer.index_directory(tmp_path)
    controller = TUIController(indexer, root_dir=tmp_path)
    controller.saved_path = str(path)
    controller.saved_digest = file_digest(str(path))
    controller.saved_at = time.monotonic()

    path.write_text("rewritten elsewhere\n", encoding="utf-8")
    controller._check_saved_file([str(path)])

    assert "Another program changed" in controller.status_notice
    assert controller.saved_path == ""


def test_saved_file_watch_ignores_matching_content(tmp_path):
    import time

    from fuzzy_surgical.file_writer import file_digest
    from fuzzy_surgical.tui_controller import TUIController

    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")
    indexer = FileIndexer()
    indexer.index_directory(tmp_path)
    controller = TUIController(indexer, root_dir=tmp_path)
    controller.saved_path = str(path)
    controller.saved_digest = file_digest(str(path))
    controller.saved_at = time.monotonic()

    controller._check_saved_file([str(path)])

    assert controller.status_notice == ""
    assert controller.saved_path == str(path)
