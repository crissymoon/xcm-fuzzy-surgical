"""Soft-wrap screen math for the whole-file editor.

Long lines wrap into several screen segments instead of scrolling sideways.
Every screen row therefore maps to one buffer position, and the same mapping
must serve drawing, the caret, the mouse and the wheel. Keeping that math in
one module is what stops those four from disagreeing.

All cells here are display cells: tabs already expanded, control characters
replaced the same way the renderer does it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from fuzzy_surgical.renderer import index_for_display_column, sanitize


@dataclass(frozen=True)
class Segment:
    """One screen row of a possibly wrapped logical line."""

    row: int
    part: int
    start_cell: int
    cells: int


def display_length(line: str) -> int:
    return len(sanitize(line))


def segment_count(line: str, width: int) -> int:
    """Return how many screen rows this line needs at the given width."""
    width = max(1, width)
    return max(1, (display_length(line) + width - 1) // width)


def caret_segment(line: str, column: int, width: int) -> Tuple[int, int]:
    """Return the caret's segment index and its cell inside that segment."""
    width = max(1, width)
    column = max(0, min(column, len(line)))
    cell = len(sanitize(line[:column]))
    count = segment_count(line, width)
    part = min(cell // width, count - 1)
    return part, cell - part * width


def caret_display_cell(line: str, column: int) -> int:
    """Return the caret's column measured in display cells from line start."""
    column = max(0, min(column, len(line)))
    return len(sanitize(line[:column]))


def visible_segments(
    lines: Sequence[str],
    top_row: int,
    top_part: int,
    rows: int,
    width: int,
) -> List[Segment]:
    """Return the screen segments from the viewport top, at most ``rows``."""
    if not lines or rows <= 0:
        return []
    width = max(1, width)
    row = max(0, min(top_row, len(lines) - 1))
    part = max(0, min(top_part, segment_count(lines[row], width) - 1))
    segments: List[Segment] = []
    while row < len(lines) and len(segments) < rows:
        count = segment_count(lines[row], width)
        length = display_length(lines[row])
        while part < count and len(segments) < rows:
            start = part * width
            segments.append(
                Segment(
                    row=row,
                    part=part,
                    start_cell=start,
                    cells=max(0, min(width, length - start)),
                )
            )
            part += 1
        row += 1
        part = 0
    return segments


def segment_index(
    segments: Sequence[Segment], row: int, part: int
) -> Optional[int]:
    """Return the screen index of one buffer segment, if it is visible."""
    for index, segment in enumerate(segments):
        if segment.row == row and segment.part == part:
            return index
    return None


def screen_offset(
    lines: Sequence[str],
    top_row: int,
    top_part: int,
    row: int,
    part: int,
    width: int,
    limit: int,
) -> Optional[int]:
    """Return how far below the viewport top a segment sits.

    Returns -1 when the segment is above the viewport and ``None`` when it is
    farther below the last counted row. Only ``limit`` rows are walked, so the
    cost does not depend on the file size.
    """
    if not lines or limit <= 0:
        return None
    width = max(1, width)
    row = max(0, min(row, len(lines) - 1))
    part = max(0, min(part, segment_count(lines[row], width) - 1))
    if (row, part) < (top_row, top_part):
        return -1
    current_row = max(0, min(top_row, len(lines) - 1))
    current_part = max(
        0, min(top_part, segment_count(lines[current_row], width) - 1)
    )
    offset = 0
    while offset < limit and current_row < len(lines):
        if current_row == row and current_part == part:
            return offset
        current_part += 1
        if current_part >= segment_count(lines[current_row], width):
            current_part = 0
            current_row += 1
        offset += 1
    return None


def top_for_caret(
    lines: Sequence[str],
    row: int,
    part: int,
    want_rows: int,
    width: int,
) -> Tuple[int, int]:
    """Return the viewport top that puts a segment ``want_rows`` down.

    At the start of the file there may be fewer rows above the segment, so the
    result is the earliest reachable top instead of a negative offset.
    """
    if not lines:
        return 0, 0
    width = max(1, width)
    row = max(0, min(row, len(lines) - 1))
    part = max(0, min(part, segment_count(lines[row], width) - 1))
    remaining = max(0, want_rows)
    while remaining > 0:
        if part > 0:
            part -= 1
        elif row > 0:
            row -= 1
            part = segment_count(lines[row], width) - 1
        else:
            break
        remaining -= 1
    return row, part


def next_segment(
    lines: Sequence[str], row: int, part: int, width: int
) -> Optional[Tuple[int, int]]:
    """Return the segment after one position, or None at the document end."""
    if not lines:
        return None
    width = max(1, width)
    row = max(0, min(row, len(lines) - 1))
    count = segment_count(lines[row], width)
    part = max(0, min(part, count - 1))
    if part + 1 < count:
        return row, part + 1
    if row + 1 < len(lines):
        return row + 1, 0
    return None


def move_display_rows(
    lines: Sequence[str],
    row: int,
    column: int,
    delta: int,
    width: int,
) -> Tuple[int, int]:
    """Move a buffer position by screen rows, keeping its visual column."""
    if not lines or delta == 0:
        return max(0, min(row, len(lines) - 1)), column
    width = max(1, width)
    row = max(0, min(row, len(lines) - 1))
    column = max(0, min(column, len(lines[row])))
    part, inner = caret_segment(lines[row], column, width)
    step = 1 if delta > 0 else -1
    for _ in range(abs(delta)):
        if step > 0:
            if part + 1 < segment_count(lines[row], width):
                part += 1
            elif row + 1 < len(lines):
                row += 1
                part = 0
            else:
                break
        else:
            if part > 0:
                part -= 1
            elif row > 0:
                row -= 1
                part = segment_count(lines[row], width) - 1
            else:
                break
    target_cell = part * width + inner
    return row, index_for_display_column(lines[row], target_cell)
