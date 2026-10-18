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
    import curses

    key_enter = getattr(curses, "KEY_ENTER", None)
    if key_enter is not None and key == key_enter:
        return True
    return key in ("\n", "\r", 10, 13)


def is_backspace(key) -> bool:
    import curses

    return key in (curses.KEY_BACKSPACE, 8, 127)


def disable_flow_control():
    """Turn off terminal XON/XOFF so Ctrl+S reaches the application.

    Returns the previous termios attributes for restore_terminal_mode, or
    None when the platform has no termios support.
    """
    if os.name == "nt":
        return None
    try:
        import sys
        import termios
    except ImportError:
        return None
    try:
        descriptor = sys.stdin.fileno()
        original = termios.tcgetattr(descriptor)
    except (OSError, ValueError, termios.error):
        return None
    try:
        updated = list(original)
        # IXON and IXOFF live in iflag, not lflag. Clearing only lflag leaves
        # the terminal eating Ctrl+S as XOFF, which made the save key vanish.
        updated[0] = original[0] & ~(termios.IXON | termios.IXOFF)
        termios.tcsetattr(descriptor, termios.TCSADRAIN, updated)
    except (OSError, termios.error):
        return None
    return original


def restore_terminal_mode(original) -> None:
    """Restore attributes captured by disable_flow_control."""
    if original is None or os.name == "nt":
        return
    try:
        import sys
        import termios
    except ImportError:
        return
    try:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original)
    except (OSError, ValueError, termios.error):
        pass


def disable_interrupt_keys():
    """Deliver Ctrl+C and Ctrl+Z to the app instead of the terminal driver.

    Curses keeps ISIG enabled in cbreak mode, so Ctrl+C raises SIGINT and
    Ctrl+Z suspends the process. The full editor needs both as ordinary keys:
    copy and undo. Returns the previous attributes for restore_terminal_mode.
    """
    if os.name == "nt":
        return None
    try:
        import sys
        import termios
    except ImportError:
        return None
    try:
        descriptor = sys.stdin.fileno()
        original = termios.tcgetattr(descriptor)
    except (OSError, ValueError, termios.error):
        return None
    try:
        updated = list(original)
        updated[3] = original[3] & ~termios.ISIG
        termios.tcsetattr(descriptor, termios.TCSADRAIN, updated)
    except (OSError, termios.error):
        return None
    return original
