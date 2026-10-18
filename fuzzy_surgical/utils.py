"""Shared, dependency free helpers."""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Optional

BRACKETED_PASTE_ON = "\x1b[?2004h"
BRACKETED_PASTE_OFF = "\x1b[?2004l"
_BRACKET_START_BODY = "200"
_BRACKET_END = "\x1b[201~"
_PASTE_READ_TIMEOUT_MS = 200
_MAX_PASTE_CHARS = 1_000_000


@dataclass(frozen=True)
class PastedText:
    """One bracketed-paste block delivered by the terminal as a unit."""

    text: str


_PENDING: Deque[object] = deque()


def _terminal_write(sequence: str) -> bool:
    """Write a control sequence to the terminal without going through curses."""
    if os.name == "nt":
        return False
    import sys

    try:
        descriptor = sys.__stdout__.fileno()
    except (AttributeError, OSError, ValueError):
        return False
    if not os.isatty(descriptor):
        return False
    try:
        os.write(descriptor, sequence.encode("ascii"))
    except (OSError, ValueError):
        return False
    return True


def enable_bracketed_paste() -> bool:
    """Ask the terminal to wrap pasted text in start and end markers."""
    return _terminal_write(BRACKETED_PASTE_ON)


def disable_bracketed_paste() -> None:
    """Stop bracketed paste reporting, restoring ordinary key input."""
    _terminal_write(BRACKETED_PASTE_OFF)


def _push_back(*values: object) -> None:
    for value in values:
        _PENDING.append(value)


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


def _read_one(stdscr):
    """Read one raw key, returning None for a timeout or terminal error."""
    import curses

    try:
        reader = stdscr.get_wch
    except AttributeError:  # older Windows curses builds
        reader = None

    try:
        return reader() if reader is not None else stdscr.getch()
    except curses.error:
        return None


def _read_bracketed_paste(stdscr, restore_timeout: int):
    """Consume a bracketed paste after an ESC, or push the bytes back.

    Returns the pasted text when the full ESC [ 200 ~ ... ESC [ 201 ~ frame is
    present, otherwise None so the ESC is delivered as the Escape key and the
    remaining characters are replayed as ordinary input.
    """
    import curses

    try:
        stdscr.timeout(20)
    except (AttributeError, curses.error):
        return None
    try:
        following = _read_one(stdscr)
        if following != "[":
            if following is not None:
                _push_back(following)
            return None
        prefix = []
        for _ in range(3):
            char = _read_one(stdscr)
            if char is None:
                break
            prefix.append(char)
        if "".join(prefix) != _BRACKET_START_BODY:
            _push_back("[", *prefix)
            return None
        final = _read_one(stdscr)
        if final != "~":
            _push_back("[", *prefix, final)
            return None
        try:
            stdscr.timeout(_PASTE_READ_TIMEOUT_MS)
        except (AttributeError, curses.error):
            pass
        collected = []
        tail = ""
        while len(collected) < _MAX_PASTE_CHARS:
            char = _read_one(stdscr)
            if char is None:
                break
            if not isinstance(char, str):
                char = chr(char) if 0 <= char < 0x110000 else ""
            if not char:
                continue
            collected.append(char)
            tail = (tail + char)[-len(_BRACKET_END) :]
            if tail == _BRACKET_END:
                del collected[-len(_BRACKET_END) :]
                return "".join(collected)
        return "".join(collected)
    finally:
        try:
            stdscr.timeout(restore_timeout)
        except (AttributeError, curses.error):
            pass


def read_key(stdscr, input_timeout: Optional[int] = None):
    """Read a key, returning None instead of raising on terminal errors.

    Single character control keys are normalised to their integer code so
    callers can compare them with curses constants on every platform. When
    ``input_timeout`` is supplied, a leading ESC is checked for a bracketed
    paste frame and returned as ``PastedText`` instead of the Escape key; the
    previous input timeout is restored afterwards.
    """
    if _PENDING:
        return _PENDING.popleft()
    value = _read_one(stdscr)
    if value is None:
        return None
    if value == "\x1b" and input_timeout is not None:
        pasted = _read_bracketed_paste(stdscr, input_timeout)
        if pasted is not None:
            return PastedText(pasted)
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
    """Turn off terminal XON/XOFF and line editing so app keys arrive.

    Clears IXON/IXOFF (Ctrl+S was being eaten as XOFF) and IEXTEN, which is
    what swallows Ctrl+V as the terminal's literal-next character before
    curses ever sees it. Returns the previous termios attributes for
    restore_terminal_mode, or None when the platform has no termios support.
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
        iexten = getattr(termios, "IEXTEN", 0)
        updated[3] = original[3] & ~iexten
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
