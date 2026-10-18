"""Full screen overlays drawn by the whole-file editor.

Kept apart from the editor state machine so the editor file stays focused on
input handling and buffer changes. Every function is a pure draw pass: it
takes the data it needs and never mutates the editor.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Sequence

import curses

from fuzzy_surgical.renderer import draw_box, safe_addstr, sanitize, shorten
from fuzzy_surgical.utils import display_path

HELP_LINES = [
    "Precise movement",
    "  Left / Right   move one character, wrapping lines",
    "  Up / Down      move one line",
    "  Home / End     start or end of line",
    "  PgUp / PgDn    move one page",
    "  Mouse click    place the cursor at the clicked cell",
    "  Mouse wheel    scroll the file with the caret, trackpads follow",
    "",
    "Selecting and copying",
    "  Mouse drag     highlight text, release to keep the selection",
    "  Double click   select the word under the pointer",
    "  Triple click   select the whole line",
    "  Ctrl+A         select the whole file",
    "  Ctrl+C         copy the selection to the clipboard",
    "  Esc            clear the selection before leaving",
    "",
    "Editing",
    "  Enter          split the line",
    "  Backspace      delete backwards, joining lines at column 0",
    "  Delete         delete forwards, joining lines at the end",
    "  Tab            insert four spaces",
    "  Ctrl+Z         undo",
    "  Ctrl+Y         redo",
    "",
    "Dev box (language server and linters)",
    "  Ctrl+N         request completions now (F12 and Ctrl+Space too)",
    "  typing         suggestions appear after two characters",
    "  Up / Down      choose a suggestion, Enter or Tab inserts",
    "  Esc            close the suggestion list without inserting",
    "  F9 / Ctrl+L    show issues, Enter jumps to the line",
    "",
    "Saving and safety",
    "  Ctrl+S / F2    write and verify the disk copy",
    "  F6             browse SQLite snapshots, Enter loads one",
    "  F8             reload the file from disk",
    "  Esc            leave, asking before discarding changes",
    "",
    "Press any key to return.",
]


def _box_size(height: int, width: int, lines: Sequence[str]) -> tuple:
    box_width = min(width - 2, max(len(line) for line in lines) + 4)
    box_height = len(lines) + 2
    top = max(0, (height - box_height) // 2)
    left = max(0, (width - box_width) // 2)
    return box_width, box_height, top, left


def draw_saved(
    win,
    theme,
    height: int,
    width: int,
    *,
    path: str,
    line_count: int,
    byte_count: Optional[int],
    history_note: str,
    saved_line: int,
    saved_proof: str,
) -> None:
    """Show proof of what was written, including the line read from disk."""
    written = f"{byte_count} bytes" if byte_count is not None else "written"
    lines = [
        "Saved to disk",
        "",
        path,
        "",
        f"{line_count} lines, {written}, verified",
        f"History: {history_note}",
        "",
        f"Line {saved_line} now reads on disk:",
        f"  {sanitize(saved_proof)}",
        "",
        "Press any key to keep editing.",
    ]
    box_width, box_height, top, left = _box_size(height, width, lines)
    overlay = theme.attr("ui.overlay")
    draw_box(win, top, left, box_height, box_width, "Saved", theme, overlay)
    for offset, line in enumerate(lines, 1):
        attr = overlay
        if offset == 1:
            attr = theme.attr("ui.accent", bold=True)
        elif offset == 3:
            attr = theme.attr("ui.path")
        elif offset == 5:
            attr = theme.attr("ui.accent")
        elif offset == 9:
            attr = theme.attr("edit.changed", bold=True)
        safe_addstr(win, top + offset, left + 2, line[: box_width - 4], attr)


def draw_discard(win, theme, height: int, width: int, *, path: str) -> None:
    """Ask whether an unsaved buffer should be saved before leaving."""
    safe_addstr(
        win,
        2,
        0,
        "Unsaved changes. s saves and exits, d discards, Esc keeps editing."[: width - 1],
        theme.attr("ui.error", bold=True),
    )
    safe_addstr(
        win,
        3,
        0,
        f"File: {shorten(display_path(Path(path)), max(10, width - 8))}",
        theme.attr("ui.path"),
    )
    safe_addstr(
        win,
        5,
        0,
        "Nothing has been written to disk yet."[: width - 1],
        theme.attr("ui.dim", dim=True),
    )


def draw_history(
    win,
    theme,
    height: int,
    width: int,
    *,
    entries: Sequence,
    selected: int,
    scroll: int,
) -> None:
    """List the stored SQLite snapshots with the selected row highlighted."""
    rows = max(1, height - 6)
    count = len(entries)
    end = min(count, scroll + rows)
    box_height = min(height - 1, rows + 4)
    box_width = min(width - 2, max(48, width - 8))
    top = max(1, (height - box_height) // 2)
    left = max(0, (width - box_width) // 2)
    overlay = theme.attr("ui.overlay")
    draw_box(
        win,
        top,
        left,
        box_height,
        box_width,
        f"SQLite snapshots ({count})",
        theme,
        overlay,
    )
    header = "  when                 size    reason"
    safe_addstr(win, top + 1, left + 2, header[: box_width - 4], overlay)
    for offset, index in enumerate(range(scroll, end), 2):
        revision = entries[index]
        stamp = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(revision.created_at)
        )
        label = f"{stamp}  {revision.original_size:>8}  {revision.reason or 'save'}"
        attr = overlay
        if index == selected:
            attr = theme.attr("ui.accent", bold=True) | curses.A_REVERSE
        safe_addstr(win, top + offset, left + 2, label[: box_width - 4], attr)
    footer = "Enter loads into the buffer  Esc closes"
    safe_addstr(
        win,
        top + box_height - 2,
        left + 2,
        footer[: box_width - 4],
        overlay,
    )


def draw_help(win, theme, height: int, width: int) -> None:
    """Draw the keyboard reference."""
    lines = HELP_LINES
    box_width, box_height, top, left = _box_size(height, width, lines)
    overlay = theme.attr("ui.overlay")
    draw_box(
        win,
        top,
        left,
        box_height,
        box_width,
        "File editor controls",
        theme,
        overlay,
    )
    for offset, line in enumerate(lines, 1):
        safe_addstr(win, top + offset, left + 2, line[: box_width - 4], overlay)
