"""Parse ``path:line`` jump queries typed into the search box."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# The path is lazy so ``a/b.py:12`` keeps a single trailing line number while
# names that contain a colon still work as long as the final segment is numeric.
_LINE_REFERENCE = re.compile(r"^(?P<path>.+):(?P<line>\d{1,9})$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass(frozen=True)
class LineReference:
    """A parsed ``path:line`` query."""

    path: str
    line: int
    raw: str

    @property
    def label(self) -> str:
        return f"{self.path}:{self.line}"


def parse_line_reference(query: str) -> Optional[LineReference]:
    """Return a line reference when the query ends with ``:<number>``.

    URLs, empty paths and line zero are ignored so normal searches never turn
    into a jump by accident.
    """
    if not query or "://" in query:
        return None

    match = _LINE_REFERENCE.match(query)
    if match is None:
        return None

    path = match.group("path").strip()
    line = int(match.group("line"))
    if not path or line < 1:
        return None

    return LineReference(path=_normalise_path(path), line=line, raw=query)


def _normalise_path(path: str) -> str:
    """Drop a Windows drive and uniform separators to match relative paths."""
    cleaned = _WINDOWS_DRIVE.sub("", path)
    return cleaned.replace("\\", "/").lstrip("/")
