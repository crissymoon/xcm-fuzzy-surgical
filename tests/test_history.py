"""Tests for the SQLite snapshot store."""

from __future__ import annotations

from pathlib import Path

from fuzzy_surgical.history import SnapshotStore


def test_records_and_reads_a_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(database_path=tmp_path / "history.sqlite3")
    try:
        revision = store.record_text("/tmp/app.py", "print('old')\n", reason="line edit")
        assert revision is not None
        assert revision.original_size == len("print('old')\n")
        assert store.count("/tmp/app.py") == 1
        assert store.read_text(revision.id) == "print('old')\n"
    finally:
        store.close()


def test_identical_text_is_not_stored_twice(tmp_path: Path) -> None:
    store = SnapshotStore(database_path=tmp_path / "history.sqlite3")
    try:
        first = store.record_text("/tmp/app.py", "one\n")
        second = store.record_text("/tmp/app.py", "one\n")
        assert first is not None
        assert second is None
        assert store.count("/tmp/app.py") == 1
    finally:
        store.close()


def test_revisions_are_newest_first(tmp_path: Path) -> None:
    store = SnapshotStore(database_path=tmp_path / "history.sqlite3")
    try:
        store.record_text("/tmp/app.py", "one\n")
        store.record_text("/tmp/app.py", "two\n")
        revisions = store.list_revisions("/tmp/app.py")
        assert [store.read_text(item.id) for item in revisions] == ["two\n", "one\n"]
    finally:
        store.close()


def test_per_file_limit_prunes_the_oldest(tmp_path: Path) -> None:
    store = SnapshotStore(database_path=tmp_path / "history.sqlite3", per_file_limit=2)
    try:
        for index in range(4):
            store.record_text("/tmp/app.py", f"version {index}\n")
        revisions = store.list_revisions("/tmp/app.py")
        assert len(revisions) == 2
        assert store.read_text(revisions[0].id) == "version 3\n"
        assert store.read_text(revisions[1].id) == "version 2\n"
    finally:
        store.close()


def test_total_size_limit_keeps_the_newest_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(
        database_path=tmp_path / "history.sqlite3",
        per_file_limit=10,
        max_total_bytes=1,
    )
    try:
        for index in range(6):
            store.record_text(f"/tmp/file{index}.py", f"content {index}\n" * 4)
        revisions = store.list_revisions("/tmp/file5.py")
        assert store.count() == 1
        assert store.read_text(revisions[0].id) is not None
    finally:
        store.close()


def test_disabled_store_is_a_no_op(tmp_path: Path) -> None:
    store = SnapshotStore(database_path=tmp_path / "history.sqlite3", enabled=False)
    assert store.available is False
    assert store.record_text("/tmp/app.py", "one\n") is None
    assert store.list_revisions("/tmp/app.py") == []
    assert store.read_text(1) is None
    store.close()


def test_bad_database_location_is_reported_without_raising(tmp_path: Path) -> None:
    store = SnapshotStore(database_path=tmp_path)
    assert store.available is False
    assert "History is unavailable" in store.error
    assert store.record_text("/tmp/app.py", "one\n") is None
    store.close()


def test_record_current_reads_the_file_and_ignores_missing_paths(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("original\n", encoding="utf-8")
    store = SnapshotStore(database_path=tmp_path / "history.sqlite3")
    try:
        revision = store.record_current(source)
        assert revision is not None
        assert store.read_text(revision.id) == "original\n"
        assert store.record_current(tmp_path / "missing.py") is None
    finally:
        store.close()


def test_snapshot_text_round_trips_surrogates(tmp_path: Path) -> None:
    store = SnapshotStore(database_path=tmp_path / "history.sqlite3")
    text = "naive \udcff text\n"
    try:
        revision = store.record_text("/tmp/odd.txt", text)
        assert revision is not None
        assert store.read_text(revision.id) == text
    finally:
        store.close()
