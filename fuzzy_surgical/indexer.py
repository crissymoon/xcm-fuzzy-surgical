"""Safe, bounded directory indexing.

The indexer deliberately refuses to follow directory symlinks and treats every
filesystem error as a skip. Large folders are capped so opening a desktop or
documents folder cannot exhaust memory or lock the UI.
"""

from __future__ import annotations

import fnmatch
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from fuzzy_surgical.utils import is_binary_file

DEFAULT_IGNORE_DIRS = (
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    ".tox",
    ".eggs",
    "*.egg-info",
    "htmlcov",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "Library",
    "AppData",
    ".Trash",
    ".cache",
)


@dataclass(frozen=True)
class IndexLimits:
    """Hard safety limits applied while walking a directory."""

    max_files: int = 8000
    max_file_size: int = 1_000_000
    max_lines_per_file: int = 5000
    max_total_lines: int = 500_000


class FileIndexer:
    """Indexes text files and extracts line context."""

    def __init__(self, limits: Optional[IndexLimits] = None) -> None:
        self.limits = limits or IndexLimits()
        self.file_lines: Dict[str, List[tuple]] = {}
        self.signatures: Dict[str, tuple] = {}
        self.total_lines = 0
        self.skipped_files = 0

    def index_directory(
        self,
        root_dir: Path,
        ignore_dirs: Optional[List[str]] = None,
        extensions: Optional[List[str]] = None,
    ) -> Dict[str, List[tuple]]:
        """Recursively index files below root_dir.

        Returns {file_path: [(line_num, line_text), ...]}. Errors never escape.
        """
        patterns = tuple(ignore_dirs) if ignore_dirs is not None else DEFAULT_IGNORE_DIRS
        suffixes = self._normalise_extensions(extensions)

        self.file_lines.clear()
        self.signatures.clear()
        self.total_lines = 0
        self.skipped_files = 0

        root = Path(root_dir).expanduser()
        try:
            if not root.is_dir():
                return self.file_lines
        except OSError:
            return self.file_lines

        def on_error(_error: OSError) -> None:
            # Unreadable directories are skipped rather than aborting the walk.
            return None

        try:
            walker = os.walk(root, topdown=True, followlinks=False, onerror=on_error)
            for current_root, dirs, files in walker:
                if self._limit_reached():
                    break

                kept_dirs = []
                for name in dirs:
                    full = os.path.join(current_root, name)
                    try:
                        if os.path.islink(full):
                            continue
                    except OSError:
                        continue
                    if self._ignored(name, patterns):
                        continue
                    kept_dirs.append(name)
                dirs[:] = kept_dirs

                for name in sorted(files):
                    if self._limit_reached():
                        break
                    if self._ignored(name, patterns):
                        continue
                    if suffixes and not name.lower().endswith(suffixes):
                        continue
                    self._index_file(Path(current_root) / name)
        except (OSError, RuntimeError):
            # A directory can disappear mid-walk; keep whatever was indexed.
            pass

        return self.file_lines

    def index_file(self, file_path: Path) -> List[tuple]:
        """Index a single file and return its lines."""
        self._index_file(Path(file_path))
        return self.file_lines.get(str(file_path), [])

    def reindex_file(self, file_path: str) -> None:
        """Refresh one file after an edit without re-walking the tree."""
        self._index_file(Path(file_path))

    def reindex_changed(self) -> List[str]:
        """Refresh files whose size or modification time changed on disk.

        Returns the paths that were refreshed so the caller can redraw only
        when something actually changed.
        """
        changed: List[str] = []
        for file_path, signature in list(self.signatures.items()):
            try:
                info = os.stat(file_path)
            except OSError:
                self._index_file(Path(file_path))
                changed.append(file_path)
                continue
            if (info.st_mtime_ns, info.st_size) == signature:
                continue
            self._index_file(Path(file_path))
            changed.append(file_path)
        return changed

    def _index_file(self, file_path: Path) -> None:
        key = str(file_path)
        previous = self.file_lines.pop(key, None)
        if previous is not None:
            self.total_lines = max(0, self.total_lines - len(previous))
        self.signatures.pop(key, None)

        try:
            info = file_path.stat()
        except OSError:
            self.skipped_files += 1
            return

        if not stat.S_ISREG(info.st_mode):
            return
        if info.st_size > self.limits.max_file_size:
            self.skipped_files += 1
            return
        if len(self.file_lines) >= self.limits.max_files:
            return
        if self.total_lines >= self.limits.max_total_lines:
            return
        if is_binary_file(file_path):
            self.skipped_files += 1
            return

        lines: List[tuple] = []
        remaining = self.limits.max_total_lines - self.total_lines
        line_budget = min(self.limits.max_lines_per_file, remaining)
        if line_budget <= 0:
            return

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                for number, line in enumerate(handle, 1):
                    if number > line_budget:
                        break
                    lines.append((number, line.rstrip("\n").replace("\x00", "").rstrip("\r")))
        except (OSError, UnicodeError, IOError):
            self.skipped_files += 1
            return

        self.file_lines[key] = lines
        self.total_lines += len(lines)
        self.signatures[key] = (info.st_mtime_ns, info.st_size)

    def get_file_context(
        self,
        file_path: str,
        line_num: int,
        context_lines: int = 3,
    ) -> List[tuple]:
        """Get indexed lines around a specific line number."""
        lines = self.file_lines.get(file_path)
        if not lines:
            return []

        start = max(1, line_num - context_lines)
        end = min(len(lines), line_num + context_lines)
        return [(number, text) for number, text in lines[start - 1 : end]]

    def _limit_reached(self) -> bool:
        return (
            len(self.file_lines) >= self.limits.max_files
            or self.total_lines >= self.limits.max_total_lines
        )

    @staticmethod
    def _ignored(name: str, patterns: tuple) -> bool:
        lowered = name.lower()
        return any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in patterns)

    @staticmethod
    def _normalise_extensions(extensions: Optional[List[str]]) -> tuple:
        if not extensions:
            return ()
        normalised = []
        for extension in extensions:
            value = extension.strip().lower()
            if not value:
                continue
            normalised.append(value if value.startswith(".") else "." + value)
        return tuple(normalised)
