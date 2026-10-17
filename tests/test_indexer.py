"""
Tests for the indexer module.
"""

import tempfile
import os
from pathlib import Path
from fuzzy_surgical.indexer import FileIndexer


def test_index_file():
    """Test indexing a single file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Line 1\nLine 2\nLine 3\n")
        temp_path = f.name

    try:
        indexer = FileIndexer()
        indexer._index_file(Path(temp_path))

        assert temp_path in indexer.file_lines
        lines = indexer.file_lines[temp_path]
        assert len(lines) == 3
        assert lines[0] == (1, "Line 1")
        assert lines[1] == (2, "Line 2")
        assert lines[2] == (3, "Line 3")
    finally:
        os.unlink(temp_path)


def test_index_directory():
    """Test indexing a directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test files
        file1 = Path(tmpdir) / "test1.txt"
        file2 = Path(tmpdir) / "test2.py"

        file1.write_text("Hello\nWorld\n")
        file2.write_text("print('test')\n")

        indexer = FileIndexer()
        results = indexer.index_directory(Path(tmpdir))

        assert str(file1) in results
        assert str(file2) in results
        assert len(results[str(file1)]) == 2
        assert len(results[str(file2)]) == 1


def test_get_file_context():
    """Test getting context around a line."""
    indexer = FileIndexer()

    # Create dummy file lines
    test_lines = [(i, f"Line {i}") for i in range(1, 11)]
    indexer.file_lines = {"test.txt": test_lines}

    context = indexer.get_file_context("test.txt", 5, 2)

    assert len(context) == 5  # Lines 3-7
    assert context[0][0] == 3
    assert context[2][0] == 5  # Center line
    assert context[4][0] == 7
