"""Safe directory browsing and fuzzy filtering for the picker."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from fuzzy_surgical.fuzzy_matcher import FuzzyMatcher
from fuzzy_surgical.indexer import DEFAULT_IGNORE_DIRS
from fuzzy_surgical.utils import safe_directory

MAX_BROWSE_ENTRIES = 5000


@dataclass(frozen=True)
class DirEntry:
    """One row in the directory picker."""

    path: Path
    label: str
    kind: str  # current, parent or dir


class DirectoryNavigator:
    """Lists child directories without ever following unsafe links."""

    def __init__(
        self,
        start_dir: Optional[Path],
        ignore_names: Optional[Sequence[str]] = None,
    ) -> None:
        resolved = safe_directory(start_dir)
        if resolved is None:
            resolved = safe_directory(Path.home()) or Path.home()
        self._cwd = resolved
        self.ignore_names = tuple(ignore_names or DEFAULT_IGNORE_DIRS)
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
            self.error = "That directory cannot be read."
            return False
        self._cwd = resolved
        self.error = ""
        return True

    def go_parent(self) -> bool:
        try:
            parent = self._cwd.parent
        except OSError:
            return False
        return self.change(parent)

    def list_entries(self, query: str = "") -> List[DirEntry]:
        """Return current, parent, then subdirectories filtered by query."""
        entries = [DirEntry(self._cwd, "search this directory", "current")]
        if self.has_parent():
            try:
                parent = self._cwd.parent
            except OSError:
                parent = self._cwd
            entries.append(DirEntry(parent, "up one level", "parent"))

        children = self._child_directories()
        if query:
            scored = []
            for child in children:
                matched, score, _ = self.matcher.fuzzy_match(query, child.label)
                if matched:
                    scored.append((score, child.label.casefold(), child))
            scored.sort(key=lambda item: (-item[0], item[1]))
            children = [item[2] for item in scored]
        else:
            children.sort(key=lambda child: child.label.casefold())

        entries.extend(children)
        return entries

    def _child_directories(self) -> List[DirEntry]:
        results: List[DirEntry] = []
        self.error = ""
        self.truncated = False
        try:
            with os.scandir(self._cwd) as iterator:
                for entry in iterator:
                    if len(results) >= MAX_BROWSE_ENTRIES:
                        self.truncated = True
                        break
                    try:
                        if entry.name in self.ignore_names:
                            continue
                        if self._ignored(entry.name):
                            continue
                        if entry.is_symlink():
                            continue
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    results.append(DirEntry(Path(entry.path), entry.name, "dir"))
        except (OSError, PermissionError):
            self.error = "This directory cannot be read. Use Backspace to go up."
        return results

    def _ignored(self, name: str) -> bool:
        lowered = name.lower()
        return any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in self.ignore_names)
