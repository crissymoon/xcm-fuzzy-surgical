"""Headless end-to-end checks for every platform.

The TUI itself needs a terminal, but indexing, literal search, ``file:line``
jumps, in-place writes, file URIs and SQLite snapshots do not. This script
exercises those paths against a temporary directory and exits non-zero on the
first failure, so a container can prove the core works before a human opens
the interactive screen.

Usage:
    python scripts/headless_smoke.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fuzzy_surgical.file_writer import write_text  # noqa: E402
from fuzzy_surgical.fuzzy_matcher import FuzzyMatcher  # noqa: E402
from fuzzy_surgical.history import SnapshotStore  # noqa: E402
from fuzzy_surgical.indexer import FileIndexer  # noqa: E402
from fuzzy_surgical.line_jump import LineJumpResolver  # noqa: E402
from fuzzy_surgical.lsp.protocol import path_to_uri, uri_to_path  # noqa: E402
from fuzzy_surgical.query_parser import parse_line_reference  # noqa: E402


def check(condition, message: str) -> None:
    if not condition:
        raise SystemExit("headless smoke failed: " + message)
    print("ok  " + message)


def check_indexing(root: Path) -> None:
    package = root / "pkg"
    package.mkdir()
    sample = package / "sample.py"
    sample.write_text("def alpha():\n    return 'needle here'\n", encoding="utf-8")
    notes = root / "notes.txt"
    notes.write_text("needle in notes\n", encoding="utf-8")
    ignored = root / ".git"
    ignored.mkdir()
    (ignored / "hidden.py").write_text("needle hidden\n", encoding="utf-8")

    indexer = FileIndexer()
    lines = indexer.index_directory(root)
    check(len(lines) == 2, "indexed the two visible files")
    check(any(path.endswith("sample.py") for path in lines), "sample.py is indexed")
    check(not any(".git" in path for path in lines), "ignored directories are skipped")

    matcher = FuzzyMatcher()
    results = matcher.search_lines(lines, "needle")
    check(bool(results), "literal search returns results")
    check(all("needle" in row[2].lower() for row in results), "every hit contains the query")
    unique = matcher.search_lines(lines, "needle here")
    check(len(unique) == 1, "a unique literal narrows to one line")
    check(unique[0][2].strip() == "return 'needle here'", "the literal line is selected")

    reference = parse_line_reference("sample.py:2")
    check(reference is not None, "file:line query parses")
    target = LineJumpResolver().resolve(reference, lines, root)
    check(target is not None, "file:line query resolves")
    check(target.selected_line == 2, "file:line selects the requested line")
    starts_at_file = target.results and target.results[0][2].startswith("def alpha")
    check(starts_at_file, "jump starts at the file")


def check_writing(root: Path) -> None:
    path = root / "pkg" / "sample.py"
    original = path.read_text(encoding="utf-8")
    before = path.stat().st_ino
    result = write_text(str(path), "def alpha():\n    return 'updated needle'\n")
    written = path.read_text(encoding="utf-8")
    check("updated needle" in written, "write reaches disk")
    check(result.bytes_written == len(written.encode("utf-8")), "byte count matches disk")
    if os.name != "nt":
        check(path.stat().st_ino == before, "write keeps the inode")
    check(original != written, "write changed the file")

    uri = path_to_uri(str(path))
    check(uri.startswith("file:///"), "file URI has a scheme")
    check(Path(uri_to_path(uri)).resolve() == path.resolve(), "file URI round trips")


def check_history(root: Path) -> None:
    path = root / "pkg" / "sample.py"
    database = root / "history.sqlite3"
    store = SnapshotStore(database_path=database)
    check(store.available, "SQLite history opens")
    revision = store.record_text(path, "original text\n", reason="smoke")
    check(revision is not None, "snapshot is recorded")
    revisions = store.list_revisions(path)
    check(len(revisions) == 1, "snapshot is listed")
    check(store.read_text(revisions[0].id) == "original text\n", "snapshot restores")
    store.close()
    check(database.exists(), "history database exists on disk")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fuzzy-surgical-smoke-") as temp:
        root = Path(temp)
        check_indexing(root)
        check_writing(root)
        check_history(root)
    print("headless smoke passed on " + sys.platform)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
