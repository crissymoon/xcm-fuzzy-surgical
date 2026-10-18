"""Tests for the full file editor buffer, loading and saving."""

from __future__ import annotations

from pathlib import Path

import pytest

from fuzzy_surgical.file_writer import file_digest
from fuzzy_surgical.full_editor import (
    EditorOpenError,
    FullEditor,
    TextBuffer,
    detect_newline,
    read_editor_file,
)
from fuzzy_surgical.history import SnapshotStore


def test_detect_newline_prefers_the_dominant_ending() -> None:
    assert detect_newline("a\r\nb\r\n") == "\r\n"
    assert detect_newline("a\nb\n") == "\n"
    assert detect_newline("a\rb\r") == "\r"
    assert detect_newline("") == "\n"


def test_buffer_insert_undo_and_redo() -> None:
    buffer = TextBuffer("hello")
    buffer.cursor_col = 5

    buffer.insert_text(" world")
    assert buffer.to_text() == "hello world"
    assert buffer.dirty is True

    assert buffer.undo() is True
    assert buffer.to_text() == "hello"
    assert buffer.redo() is True
    assert buffer.to_text() == "hello world"


def test_buffer_splits_and_joins_lines() -> None:
    buffer = TextBuffer("one two")
    buffer.cursor_col = 3

    buffer.insert_newline()
    assert buffer.to_text() == "one\n two"

    buffer.move_up()
    buffer.move_end()
    buffer.delete()
    assert buffer.to_text() == "one two"

    buffer.cursor_col = 0
    buffer.backspace()
    assert buffer.to_text() == "one two"


def test_buffer_backspace_at_start_of_line_joins_upward() -> None:
    buffer = TextBuffer("one\ntwo")
    buffer.cursor_row = 1
    buffer.cursor_col = 0

    buffer.backspace()

    assert buffer.to_text() == "onetwo"
    assert buffer.cursor_row == 0
    assert buffer.cursor_col == 3


def test_buffer_preserves_crlf_and_missing_final_newline() -> None:
    buffer = TextBuffer("one\r\ntwo")
    buffer.cursor_row = 1
    buffer.cursor_col = 3
    buffer.insert_text("!")

    assert buffer.to_text() == "one\r\ntwo!"

    with_newline = TextBuffer("one\n")
    with_newline.cursor_col = 3
    with_newline.insert_text("!")
    assert with_newline.to_text() == "one!\n"


def test_buffer_restore_is_undoable() -> None:
    buffer = TextBuffer("current")
    buffer.replace_text("snapshot")
    assert buffer.to_text() == "snapshot"
    assert buffer.undo() is True
    assert buffer.to_text() == "current"


def test_read_editor_file_rejects_binary_and_directories(tmp_path: Path) -> None:
    binary = tmp_path / "binary.bin"
    binary.write_bytes(b"\x00\x01\x02")
    with pytest.raises(EditorOpenError):
        read_editor_file(str(binary))

    with pytest.raises(EditorOpenError):
        read_editor_file(str(tmp_path))

    with pytest.raises(EditorOpenError):
        read_editor_file(str(tmp_path / "missing.txt"))


def test_editor_save_writes_the_file_and_stores_a_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("one\ntwo\n", encoding="utf-8")
    store = SnapshotStore(database_path=tmp_path / "history.sqlite3")
    editor = FullEditor(None, history=store)
    editor.path = str(source)
    editor.buffer = TextBuffer(read_editor_file(str(source)))
    editor.buffer.cursor_row = 1
    editor.buffer.cursor_col = 3
    editor.buffer.insert_text(" changed")
    editor.disk_digest = file_digest(str(source))

    try:
        editor._save()

        assert editor.mode == "saved"
        assert editor.last_save is not None
        assert source.read_text(encoding="utf-8") == "one\ntwo changed\n"
        assert editor.saved_proof == "two changed"
        assert "SQLite" in editor.history_note
        revisions = store.list_revisions(source)
        assert len(revisions) == 1
        assert store.read_text(revisions[0].id) == "one\ntwo\n"
    finally:
        store.close()


def test_editor_refuses_to_overwrite_an_external_change(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("one\n", encoding="utf-8")
    editor = FullEditor(None)
    editor.path = str(source)
    editor.buffer = TextBuffer("one\n")
    editor.buffer.insert_text("!")
    editor.disk_digest = file_digest(str(source))

    source.write_text("changed outside\n", encoding="utf-8")
    editor._save()

    assert "changed on disk" in editor.error
    assert source.read_text(encoding="utf-8") == "changed outside\n"


def test_editor_skips_the_write_when_nothing_changed(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("one\n", encoding="utf-8")
    editor = FullEditor(None)
    editor.path = str(source)
    editor.buffer = TextBuffer("one\n")

    editor._save()

    assert editor.notice == "No changes to save."
    assert editor.last_save is None
    assert editor.saved_any is False


def test_history_overlay_restores_a_snapshot_into_the_buffer(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("current\n", encoding="utf-8")
    store = SnapshotStore(database_path=tmp_path / "history.sqlite3")
    try:
        revision = store.record_text(source, "older\n")
        assert revision is not None

        editor = FullEditor(None, history=store)
        editor.path = str(source)
        editor.buffer = TextBuffer("current\n")
        editor._open_history()

        assert editor.mode == "history"
        assert len(editor.history_entries) == 1

        editor._restore_selected()

        assert editor.mode == "edit"
        assert editor.buffer.to_text() == "older\n"
        assert editor.buffer.dirty is True
        assert source.read_text(encoding="utf-8") == "current\n"
    finally:
        store.close()


def test_history_overlay_explains_when_there_is_nothing(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("current\n", encoding="utf-8")
    store = SnapshotStore(database_path=tmp_path / "history.sqlite3")
    try:
        editor = FullEditor(None, history=store)
        editor.path = str(source)
        editor.buffer = TextBuffer("current\n")

        editor._open_history()

        assert editor.mode == "edit"
        assert "No snapshots" in editor.notice
    finally:
        store.close()
