"""Safe file and folder listing for the file browser.

The browser navigates with ``os.scandir`` instead of a recursive walk. That
keeps opening a folder fast, and every entry is wrapped in a ``try`` so an
unreadable or vanished file is skipped rather than crashing the TUI.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from fuzzy_surgical.fuzzy_matcher import FuzzyMatcher
from fuzzy_surgical.indexer import DEFAULT_IGNORE_DIRS
from fuzzy_surgical.utils import safe_directory

MAX_BROWSE_ENTRIES = 5000

BINARY_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".o",
    ".a",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".class",
    ".jar",
    ".zip",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".tar",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".pdf",
    ".ttf",
    ".woff",
    ".woff2",
    ".mp3",
    ".mp4",
    ".mov",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".bin",
)


@dataclass(frozen=True)
class BrowserEntry:
    """One row in the file browser."""

    path: Path
    label: str
    kind: str  # parent, dir or file
    size: int = 0


class FileBrowserNavigator:
    """Lists folders and text files below one directory."""

    def __init__(
        self,
        start_dir: Optional[Path],
        ignore_names: Optional[Sequence[str]] = None,
        extensions: Optional[Sequence[str]] = None,
    ) -> None:
        resolved = safe_directory(start_dir)
        if resolved is None:
            resolved = safe_directory(Path.home()) or Path.home()
        self._cwd = resolved
        self.ignore_names = tuple(ignore_names or DEFAULT_IGNORE_DIRS)
        self.extensions = self._normalise_extensions(extensions)
        self.matcher = FuzzyMatcher()
        self.error = ""
        self.truncated = False

    @property
    def cwd(self) -> Path:
        return self._cwd

    def has_parent(self) -> bool:
        try:
            return self._cwd.parent != self._cwd
        except OSError:
            return False

    def change(self, path: Path) -> bool:
        resolved = safe_directory(path)
        if resolved is None:
            self.error = "That folder cannot be read."
            return False
        self._cwd = resolved
        self.error = ""
        return True

    def go_parent(self) -> bool:
        try:
            parent = self._cwd.parent
        except OSError:
            return False
        if parent == self._cwd:
            return False
        return self.change(parent)

    def list_entries(self, query: str = "") -> List[BrowserEntry]:
        """Return the parent row, then folders, then files."""
        parent = self._parent_entry()
        children = self._children()
        if query:
            children = self._filtered(children, query)
        else:
            children.sort(key=lambda entry: (entry.kind != "dir", entry.label.casefold()))
        entries: List[BrowserEntry] = []
        if parent is not None:
            entries.append(parent)
        entries.extend(children)
        return entries

    def navigation_rows(self, query: str) -> int:
        """Rows that should not be auto-selected when typing a filter."""
        return 1 if self.has_parent() and not query else 0

    def _parent_entry(self) -> Optional[BrowserEntry]:
        if not self.has_parent():
            return None
        try:
            parent = self._cwd.parent
        except OSError:
            return None
        return BrowserEntry(parent, "up one level", "parent")

    def _children(self) -> List[BrowserEntry]:
        results: List[BrowserEntry] = []
        self.error = ""
        self.truncated = False
        try:
            with os.scandir(self._cwd) as iterator:
                for entry in iterator:
                    if len(results) >= MAX_BROWSE_ENTRIES:
                        self.truncated = True
                        break
                    parsed = self._entry(entry)
                    if parsed is not None:
                        results.append(parsed)
        except (OSError, PermissionError):
            self.error = "This folder cannot be read. Use Backspace to go up."
        return results

    def _entry(self, entry: os.DirEntry) -> Optional[BrowserEntry]:
        try:
            if entry.name in self.ignore_names or self._ignored(entry.name):
                return None
            if entry.is_symlink():
                # Never walk through directory links: that is how loops start.
                if entry.is_dir(follow_symlinks=True):
                    return None
                if not entry.is_file(follow_symlinks=True):
                    return None
            elif entry.is_dir(follow_symlinks=False):
                return BrowserEntry(Path(entry.path), entry.name, "dir")
            elif not entry.is_file(follow_symlinks=False):
                return None

            if entry.name.lower().endswith(BINARY_SUFFIXES):
                return None
            if self.extensions and not entry.name.lower().endswith(self.extensions):
                return None
            size = 0
            try:
                if not entry.is_symlink():
                    size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                size = 0
            return BrowserEntry(Path(entry.path), entry.name, "file", size)
        except OSError:
            return None

    def _filtered(self, entries: List[BrowserEntry], query: str) -> List[BrowserEntry]:
        scored: List[Tuple[float, int, str, BrowserEntry]] = []
        for entry in entries:
            matched, score, _ = self.matcher.fuzzy_match(query, entry.label)
            if matched:
                rank = 0 if entry.kind == "dir" else 1
                scored.append((score, rank, entry.label.casefold(), entry))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [item[3] for item in scored]

    def _ignored(self, name: str) -> bool:
        lowered = name.lower()
        return any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in self.ignore_names)

    @staticmethod
    def _normalise_extensions(extensions: Optional[Sequence[str]]) -> Tuple[str, ...]:
        if not extensions:
            return ()
        normalised = []
        for extension in extensions:
            value = extension.strip().lower()
            if not value:
                continue
            normalised.append(value if value.startswith(".") else "." + value)
        return tuple(normalised)
