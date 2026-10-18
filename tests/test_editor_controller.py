"""
Tests for the editor controller module.
"""

import tempfile
import os
from fuzzy_surgical.editor_controller import EditorController


def test_apply_changes():
    """Test applying line changes to a file."""
    editor = EditorController()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Line 1\nLine 2\nLine 3\n")
        temp_path = f.name

    try:
        # Mock the editor to return a modified line
        original_line = "Line 2"
        modified_line = "Line 2 modified"

        # Manually test _apply_changes method
        editor.file_path = temp_path
        editor.line_num = 2
        editor.edited_line = modified_line
        editor.original_line = original_line

        result = editor._apply_changes()

        assert result is True

        # Verify file was modified
        with open(temp_path, "r") as f:
            lines = f.readlines()

        assert lines[1] == "Line 2 modified\n"
        assert lines[0] == "Line 1\n"
        assert lines[2] == "Line 3\n"

    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def test_save_leaves_no_backup_file():
    """Test that a save writes the file and leaves no backup behind."""
    editor = EditorController()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Test content\n")
        temp_path = f.name
        backup_path = temp_path + ".fuzzysurgical.bak"

    try:
        editor.file_path = temp_path
        editor.line_num = 1
        editor.edited_line = "Modified content"
        editor.original_line = "Test content"

        result = editor._apply_changes()

        assert result is True
        assert not os.path.exists(backup_path)

        # Verify content
        with open(temp_path, "r") as f:
            content = f.read()

        assert content == "Modified content\n"

    finally:
        # Cleanup
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        if os.path.exists(backup_path):
            os.unlink(backup_path)


def test_apply_changes_records_the_saved_file_and_line():
    editor = EditorController()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as handle:
        handle.write("Line 1\nLine 2\nLine 3\n")
        temp_path = handle.name

    try:
        editor.file_path = temp_path
        editor.line_num = 2
        editor.context_lines = [(1, "Line 1"), (2, "Line 2"), (3, "Line 3")]
        editor.edited_line = "Line 2 changed"
        editor.line_edits = {2: "Line 2 changed"}

        assert editor._apply_changes() is True
        assert editor.last_save is not None
        assert editor.last_save.path == os.path.realpath(temp_path)
        assert editor.last_save.bytes_written == len("Line 1\nLine 2 changed\nLine 3\n")
        assert editor.last_save_line == 2
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def test_save_now_writes_without_the_review_step(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")

    editor = EditorController()
    editor.file_path = str(path)
    editor.line_num = 2
    editor.context_lines = [(1, "one"), (2, "two"), (3, "three")]
    editor.active_ctx_idx = 1
    editor.edited_line = "TWO"

    assert editor._save_now() is True
    assert path.read_text(encoding="utf-8") == "one\nTWO\nthree\n"
    assert editor.last_save is not None
    assert editor.last_save.digest
    assert editor.last_save.requested_path == str(path)
    assert editor.last_save_line == 2


def test_save_now_explains_when_nothing_was_edited(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("one\ntwo\n", encoding="utf-8")

    editor = EditorController()
    editor.file_path = str(path)
    editor.line_num = 1
    editor.context_lines = [(1, "one"), (2, "two")]
    editor.edited_line = "one"

    assert editor._save_now() is False
    assert "No changes to save" in editor.save_error
    assert path.read_text(encoding="utf-8") == "one\ntwo\n"


def test_pending_changes_include_the_active_line_and_saved_lines(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("one\ntwo\n", encoding="utf-8")

    editor = EditorController()
    editor.file_path = str(path)
    editor.line_num = 1
    editor.context_lines = [(1, "one"), (2, "two")]
    editor.active_ctx_idx = 0
    editor.edited_line = "one changed"
    assert editor._has_pending_changes() is True
    assert editor._pending_count() == 1

    editor.line_edits = {2: "two changed"}
    assert editor._pending_count() == 2


def test_save_shows_the_line_read_back_from_disk(tmp_path):
    """The proof screen must show disk text, not the in-memory buffer."""
    path = tmp_path / "app.py"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")

    editor = EditorController()
    editor.file_path = str(path)
    editor.line_num = 2
    editor.context_lines = [(1, "one"), (2, "two"), (3, "three")]
    editor.active_ctx_idx = 1
    editor.edited_line = "TWO"

    editor._save_and_show_proof()

    assert editor.mode == "saved"
    assert editor.saved_line_number == 2
    assert editor.saved_edit_count == 1
    assert editor.saved_proof == "TWO"

    # Corrupt the disk copy after the save and read the proof again: it must
    # come from the file rather than from the edited buffer.
    path.write_text("one\nEXTERNAL\nthree\n", encoding="utf-8")
    assert editor._read_disk_line(2) == "EXTERNAL"


def test_saved_proof_reports_an_unreadable_line_as_empty(tmp_path):
    editor = EditorController()
    editor.file_path = str(tmp_path / "missing.py")
    assert editor._read_disk_line(1) == ""
