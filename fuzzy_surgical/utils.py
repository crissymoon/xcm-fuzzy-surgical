"""Shared, dependency free helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def get_default_editor() -> str:
    """Get the default text editor from environment."""
    return os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"


def is_binary_file(file_path: Path) -> bool:
    """Check if a file is binary without trusting the extension."""
    try:
        with open(file_path, "rb") as handle:
            chunk = handle.read(1024)
            return b"\x00" in chunk
    except (OSError, IOError):
        return True


def format_file_size(bytes_size: int) -> str:
    """Format file size in human readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f}{unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f}TB"


def safe_write_file(file_path: str, content: str) -> bool:
    """Safely write content to file with a temporary backup."""
    backup_path = file_path + ".bak"
    try:
        if os.path.exists(file_path):
            os.replace(file_path, backup_path)

        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write(content)

        if os.path.exists(backup_path):
            os.remove(backup_path)
        return True
    except (OSError, IOError, PermissionError):
        if os.path.exists(backup_path) and not os.path.exists(file_path):
            try:
                os.replace(backup_path, file_path)
            except OSError:
                pass
        return False


def safe_directory(path: Optional[os.PathLike]) -> Optional[Path]:
    """Resolve a directory only when it exists and is readable.

    Returns None instead of raising for permission errors, broken links and
    symlink loops. This is the core guard for desktop/documents starts.
    """
    if path is None:
        return None
    try:
        candidate = Path(path).expanduser()
    except (OSError, RuntimeError, ValueError):
        return None

    try:
        if not candidate.is_dir():
            return None
        if not os.access(candidate, os.R_OK | os.X_OK):
            return None
        return candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def safe_start_directory(preferred: Optional[os.PathLike] = None) -> Path:
    """Pick a usable directory, falling back to the users home directory."""
    for candidate in (preferred, Path.cwd(), Path.home()):
        resolved = safe_directory(candidate)
        if resolved is not None:
            return resolved
    return Path.home()


def display_path(path: Path, home: Optional[Path] = None) -> str:
    """Compact a path for display, keeping it readable in narrow terminals."""
    try:
        text = str(path)
        home_text = str(home or Path.home())
    except (OSError, RuntimeError):
        return "."
    if text == home_text:
        return "~"
    if text.startswith(home_text + os.sep):
        return "~" + text[len(home_text) :]
    return text


def relative_path(path: str, root: Optional[Path]) -> str:
    """Return a display friendly path relative to the search root."""
    try:
        if root is not None:
            text = os.path.relpath(path, root)
        else:
            text = path
    except (OSError, ValueError):
        text = path
    return text.replace(os.sep, "/")


def read_key(stdscr):
    """Read a key, returning None instead of raising on terminal errors.

    Single character control keys are normalised to their integer code so
    callers can compare them with curses constants on every platform.
    """
    import curses

    try:
        reader = stdscr.get_wch
    except AttributeError:  # older Windows curses builds
        reader = None

    try:
        value = reader() if reader is not None else stdscr.getch()
    except curses.error:
        return None
    if isinstance(value, str) and len(value) == 1:
        code = ord(value)
        if code < 32 or code == 127:
            return code
    return value


def printable_char(key) -> Optional[str]:
    """Return the typed character for a key event, or None for special keys."""
    if isinstance(key, str) and len(key) == 1 and key.isprintable():
        return key
    if isinstance(key, int) and 32 <= key <= 126:
        return chr(key)
    return None


def is_enter(key) -> bool:
    return key in ("\n", "\r", 10, 13)


def is_backspace(key) -> bool:
    import curses

    return key in (curses.KEY_BACKSPACE, 8, 127)
