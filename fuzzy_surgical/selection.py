"""Text selection and clipboard support for the whole-file editor.

The editor draws its own text, so the terminal's native selection is not
available while mouse reporting is active. This module owns the selection
range, the extraction of the selected text and the clipboard hand-off.

Copying tries the platform clipboard tool first and falls back to an OSC 52
terminal escape, which works over SSH and in terminals without a helper. A
missing helper never raises: the caller gets a result it can show in the
status bar.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from fuzzy_surgical.renderer import safe_addstr, sanitize

MAX_OSC52_BYTES = 100_000
CLIPBOARD_TIMEOUT = 2.0
WORD_CHARS = "_"

Span = Tuple[int, int]


@dataclass(frozen=True)
class ClipboardResult:
    """Outcome of a copy attempt, ready to show to the user."""

    ok: bool
    message: str
    method: str = ""


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char in WORD_CHARS


def _classify(char: str) -> str:
    if _is_word_char(char):
        return "word"
    if char.isspace():
        return "space"
    return "punct"


def clipboard_commands(platform: Optional[str] = None) -> List[List[str]]:
    """Return the native clipboard commands for this platform, best first."""
    name = (platform or sys.platform).lower()
    if name.startswith("darwin"):
        return [["pbcopy"]]
    if name.startswith("win") or os.name == "nt":
        return [["clip"]]
    return [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ]


def _run_native(text: str, commands: Sequence[Sequence[str]]) -> str:
    for command in commands:
        try:
            completed = subprocess.run(
                list(command),
                input=text.encode("utf-8", "surrogateescape"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=CLIPBOARD_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            return " ".join(command)
    return ""


def _osc52(text: str, stream) -> bool:
    """Send the text with the OSC 52 escape, returning True when written."""
    if stream is None:
        return False
    payload = base64.b64encode(text.encode("utf-8", "surrogateescape")).decode("ascii")
    if len(payload) > MAX_OSC52_BYTES:
        return False
    try:
        stream.write("\x1b]52;c;" + payload + "\x07")
        stream.flush()
    except (OSError, ValueError):
        return False
    return True


def copy_to_clipboard(
    text: str,
    *,
    platform: Optional[str] = None,
    stream=None,
    allow_osc52: bool = True,
) -> ClipboardResult:
    """Copy text with the platform helper, then OSC 52 as a fallback."""
    if not text:
        return ClipboardResult(False, "Nothing selected to copy.", "empty")

    characters = len(text)
    lines = text.count("\n") + 1
    label = f"{characters} character{'s' if characters != 1 else ''}"
    if lines > 1:
        label = f"{lines} lines, {label}"

    method = _run_native(text, clipboard_commands(platform))
    if method:
        return ClipboardResult(True, f"Copied {label} with {method}.", method)

    if not allow_osc52:
        return ClipboardResult(
            False,
            "No clipboard tool found. Install pbcopy, wl-copy or xclip.",
            "missing",
        )
    target = stream if stream is not None else sys.__stdout__
    if _osc52(text, target):
        return ClipboardResult(
            True,
            f"Copied {label} through the terminal (OSC 52).",
            "osc52",
        )
    return ClipboardResult(
        False,
        "Could not reach a clipboard. Hold Shift and drag to use the terminal selection.",
        "unavailable",
    )


class Selection:
    """One anchor and one moving head in buffer coordinates."""

    def __init__(self) -> None:
        self.active = False
        self.dragging = False
        self.anchor_row = 0
        self.anchor_col = 0
        self.head_row = 0
        self.head_col = 0

    # -- lifecycle -----------------------------------------------------
    def clear(self) -> None:
        self.active = False
        self.dragging = False
        self.anchor_row = self.anchor_col = 0
        self.head_row = self.head_col = 0

    def start(self, row: int, column: int) -> None:
        self.active = True
        self.dragging = True
        self.anchor_row = self.head_row = max(0, row)
        self.anchor_col = self.head_col = max(0, column)

    def extend(self, row: int, column: int) -> None:
        if not self.active:
            self.start(row, column)
            return
        self.dragging = True
        self.head_row = max(0, row)
        self.head_col = max(0, column)

    def release(self) -> bool:
        """Finish a drag and drop a zero width selection."""
        self.dragging = False
        if not self.has_text:
            self.clear()
        return self.active

    # -- range queries -------------------------------------------------
    @property
    def has_text(self) -> bool:
        if not self.active:
            return False
        return (
            self.anchor_row != self.head_row
            or self.anchor_col != self.head_col
        )

    @property
    def head(self) -> Tuple[int, int]:
        return self.head_row, self.head_col

    def ordered(self) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        start = (self.anchor_row, self.anchor_col)
        end = (self.head_row, self.head_col)
        return (start, end) if start <= end else (end, start)

    def span(self, row: int, length: int) -> Optional[Span]:
        """Return the selected [start, end) columns of one line."""
        if not self.has_text:
            return None
        (first, last) = self.ordered()
        if row < first[0] or row > last[0]:
            return None
        start = first[1] if row == first[0] else 0
        end = last[1] if row == last[0] else length
        start = max(0, min(start, length))
        end = max(0, min(end, length))
        if start == end:
            return None
        return start, end

    def text(self, lines: Sequence[str]) -> str:
        """Extract the selection, joining lines with a newline."""
        if not self.has_text:
            return ""
        (first, last) = self.ordered()
        start_row, start_col = first
        end_row, end_col = last
        if not lines or start_row >= len(lines):
            return ""
        end_row = min(end_row, len(lines) - 1)
        if start_row == end_row:
            line = lines[start_row]
            return line[start_col:end_col]
        parts = [lines[start_row][start_col:]]
        for row in range(start_row + 1, end_row):
            parts.append(lines[row])
        parts.append(lines[end_row][:end_col])
        return "\n".join(parts)

    # -- selection builders --------------------------------------------
    def select_word(self, lines: Sequence[str], row: int, column: int) -> None:
        if not lines:
            return
        row = max(0, min(row, len(lines) - 1))
        line = lines[row]
        if not line:
            self._set(row, 0, row, 0)
            return
        index = max(0, min(column, len(line) - 1))
        kind = _classify(line[index])
        start = index
        while start > 0 and _classify(line[start - 1]) == kind:
            start -= 1
        end = index + 1
        while end < len(line) and _classify(line[end]) == kind:
            end += 1
        self._set(row, start, row, end)
        self.dragging = False

    def select_line(self, lines: Sequence[str], row: int) -> None:
        if not lines:
            return
        row = max(0, min(row, len(lines) - 1))
        self._set(row, 0, row, len(lines[row]))
        self.dragging = False

    def select_all(self, lines: Sequence[str]) -> None:
        if not lines:
            return
        last = len(lines) - 1
        self._set(0, 0, last, len(lines[last]))
        self.dragging = False

    def _set(self, start_row: int, start_col: int, end_row: int, end_col: int) -> None:
        self.active = True
        self.anchor_row = start_row
        self.anchor_col = start_col
        self.head_row = end_row
        self.head_col = end_col


def draw_selection(
    win,
    screen_row: int,
    gutter: int,
    line: str,
    span: Optional[Span],
    x_offset: int,
    available: int,
    theme,
) -> None:
    """Paint the selected part of one rendered line over the syntax colors."""
    if span is None or available <= 0:
        return
    start, end = span
    display = sanitize(line)
    start_cell = len(sanitize(line[:start]))
    end_cell = len(sanitize(line[:end]))
    first = max(start_cell, x_offset)
    last = min(end_cell, x_offset + available)
    if last <= first:
        if end_cell == start_cell and not display and x_offset == 0:
            safe_addstr(win, screen_row, gutter, " ", theme.attr("ui.selection"))
        return
    chunk = display[first:last]
    if chunk:
        safe_addstr(
            win,
            screen_row,
            gutter + (first - x_offset),
            chunk,
            theme.attr("ui.selection"),
        )
