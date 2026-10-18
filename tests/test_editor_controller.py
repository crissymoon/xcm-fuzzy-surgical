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
