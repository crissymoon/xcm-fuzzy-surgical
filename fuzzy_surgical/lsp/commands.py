"""Resolve tool commands without spawning anything.

Language server and linter templates may use the ``{python}`` token to mean the
interpreter that is running this program. That makes tools installed inside the
project virtual environment discoverable even when their console script is not
on PATH. Templates may also contain ``~`` or a glob, which is how the bundled
Eclipse JDT server inside a VS Code extension is found.
"""

from __future__ import annotations

import glob
import importlib.util
import os
import shutil
import sys
from typing import Optional, Sequence

PYTHON_TOKEN = "{python}"
CACHE_TOKEN = "{cache}"
MAX_GLOB_MATCHES = 25
EXECUTABLE_BITS = os.R_OK | os.X_OK


def cache_root() -> str:
    """Per-user cache directory, matching the platform convention."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache"
        )
    return os.path.join(base, "fuzzy-surgical")


def python_executable() -> str:
    """Return the interpreter that should run module style tools."""
    return sys.executable or shutil.which("python3") or "python3"


def module_available(name: str) -> bool:
    """True when the running interpreter can import ``name``."""
    if not name:
        return False
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _module_index(parts: Sequence[str]) -> int:
    try:
        return list(parts).index("-m")
    except ValueError:
        return -1


def _executable_path(token: str) -> Optional[str]:
    if not token or not token.strip():
        return None
    expanded = os.path.expanduser(token.strip())
    if any(char in expanded for char in "*?["):
        matches = sorted(glob.glob(expanded))[:MAX_GLOB_MATCHES]
        for match in reversed(matches):
            if os.path.isfile(match) and os.access(match, EXECUTABLE_BITS):
                return match
        return None
    if os.path.isabs(expanded):
        if os.path.isfile(expanded) and os.access(expanded, EXECUTABLE_BITS):
            return expanded
        return None
    return shutil.which(expanded)


def resolve(template: Optional[Sequence[str]]) -> Optional[list]:
    """Return a runnable argv for a template, or None when it is unusable."""
    if not template:
        return None
    parts = [python_executable() if part == PYTHON_TOKEN else part for part in template]
    parts = [
        part.replace(CACHE_TOKEN, cache_root()) if isinstance(part, str) else part
        for part in parts
    ]
    if not parts or not isinstance(parts[0], str) or not parts[0].strip():
        return None

    if PYTHON_TOKEN in template:
        index = _module_index(parts)
        if index >= 0:
            if index + 1 >= len(parts) or not module_available(parts[index + 1]):
                return None
            return [str(part) for part in parts]
        executable = _executable_path(parts[0])
        if executable is None:
            return None
        parts[0] = executable
        return [str(part) for part in parts]

    executable = _executable_path(parts[0])
    if executable is None:
        return None
    parts[0] = executable
    return [str(part) for part in parts]


def describe(template: Optional[Sequence[str]]) -> str:
    """Readable command text, with the Python token spelled out."""
    if not template:
        return ""
    parts = [python_executable() if part == PYTHON_TOKEN else part for part in template]
    parts = [
        part.replace(CACHE_TOKEN, cache_root()) if isinstance(part, str) else part
        for part in parts
    ]
    return " ".join(str(part) for part in parts)
