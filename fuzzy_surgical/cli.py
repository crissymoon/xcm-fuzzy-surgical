"""Command line entry point for FuzzySurgical."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from fuzzy_surgical import __version__
from fuzzy_surgical.history import DEFAULT_PER_FILE_LIMIT, SnapshotStore
from fuzzy_surgical.indexer import FileIndexer, IndexLimits
from fuzzy_surgical.utils import safe_directory, safe_start_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fuzzy-surgical",
        description=(
            "Fuzzy finder with syntax highlighting and surgical line replacement. "
            "Run without a directory to pick one with fuzzy navigation."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show the version and exit",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=None,
        help="Directory to search (omit it to choose one inside the TUI)",
    )
    parser.add_argument(
        "-i",
        "--ignore",
        nargs="*",
        default=None,
        help="Directory names or glob patterns to ignore",
    )
    parser.add_argument(
        "-e",
        "--extensions",
        nargs="*",
        default=None,
        help="File extensions to include, for example .py or py",
    )
    parser.add_argument(
        "--no-picker",
        action="store_true",
        help="Skip the directory picker and search the current directory",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="Do not store SQLite snapshots of edited files",
    )
    parser.add_argument(
        "--history-db",
        default=None,
        metavar="PATH",
        help="Path to the SQLite history database",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=DEFAULT_PER_FILE_LIMIT,
        help=f"Snapshots kept per file (default: {DEFAULT_PER_FILE_LIMIT})",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=IndexLimits.max_files,
        help=f"Maximum files to index (default: {IndexLimits.max_files})",
    )
    parser.add_argument(
        "--max-file-size",
        type=int,
        default=IndexLimits.max_file_size,
        help=f"Maximum bytes per file (default: {IndexLimits.max_file_size})",
    )
    parser.add_argument(
        "--max-total-lines",
        type=int,
        default=IndexLimits.max_total_lines,
        help=f"Maximum lines indexed in total (default: {IndexLimits.max_total_lines})",
    )
    parser.add_argument(
        "--max-lines-per-file",
        type=int,
        default=IndexLimits.max_lines_per_file,
        help=("Maximum lines indexed per file " f"(default: {IndexLimits.max_lines_per_file})"),
    )
    return parser


def _positive(value: int, name: str) -> Optional[int]:
    if value <= 0:
        print(f"fuzzy-surgical: {name} must be greater than zero", file=sys.stderr)
        return None
    return value


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    limits = IndexLimits(
        max_files=_positive(args.max_files, "--max-files") or 1,
        max_file_size=_positive(args.max_file_size, "--max-file-size") or 1,
        max_lines_per_file=_positive(args.max_lines_per_file, "--max-lines-per-file") or 1,
        max_total_lines=_positive(args.max_total_lines, "--max-total-lines") or 1,
    )
    if (
        args.max_files <= 0
        or args.max_file_size <= 0
        or args.max_lines_per_file <= 0
        or args.max_total_lines <= 0
        or args.history_limit <= 0
    ):
        print("fuzzy-surgical: --history-limit must be greater than zero", file=sys.stderr)
        return 2

    root: Optional[Path] = None
    if args.directory is not None:
        root = safe_directory(args.directory)
        if root is None:
            print(
                f"fuzzy-surgical: '{args.directory}' is not a readable directory",
                file=sys.stderr,
            )
            return 2
    elif args.no_picker:
        root = safe_start_directory(Path.cwd())
        if root is None:
            print("fuzzy-surgical: no readable directory found", file=sys.stderr)
            return 2

    try:
        from fuzzy_surgical.tui_controller import TUIController
    except ImportError as exc:
        print(
            "fuzzy-surgical: the curses module is unavailable. "
            "On Windows install it with: pip install windows-curses",
            file=sys.stderr,
        )
        print(f"Details: {exc}", file=sys.stderr)
        return 1

    history = SnapshotStore(
        database_path=args.history_db,
        per_file_limit=args.history_limit,
        enabled=not args.no_history,
    )
    if not args.no_history and not history.available:
        print(f"fuzzy-surgical: {history.error}", file=sys.stderr)

    indexer = FileIndexer(limits=limits)
    controller = TUIController(
        indexer,
        root_dir=root,
        ignore_dirs=args.ignore,
        extensions=args.extensions,
        history=history,
    )

    start_dir = safe_start_directory(Path.cwd()) if root is None else root
    if start_dir is None:
        print("fuzzy-surgical: no readable starting directory", file=sys.stderr)
        return 2

    try:
        controller.run(start_dir=start_dir)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # keep the terminal usable for the next command
        print(f"fuzzy-surgical: {exc}", file=sys.stderr)
        return 1
    finally:
        history.close()
    return 0
