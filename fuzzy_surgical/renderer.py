"""Low level, crash safe rendering helpers for curses windows."""

from __future__ import annotations

from typing import Iterable, Optional, Set

import curses

from fuzzy_surgical.colors import Theme
from fuzzy_surgical.syntax_highlighter import highlight_line


def sanitize(text: str) -> str:
    """Replace control characters and expand tabs consistently."""
    out = []
    for char in text:
        if char == "\t":
            out.append("    ")
        elif char in ("\n", "\r", "\x00"):
            continue
        elif char.isprintable():
            out.append(char)
        else:
            out.append(" ")
    return "".join(out)


def display_cell_width(char: str) -> int:
    """Return how many screen cells one source character occupies."""
    if char == "\t":
        return 4
    if char in ("\n", "\r", "\x00"):
        return 0
    return 1


def index_for_display_column(text: str, column: int) -> int:
    """Map a rendered column back to an index in the source line.

    Tabs render as four spaces, so a click in the middle of a tab belongs to
    that tab character. Columns at or past the end map to the line length.
    """
    if column <= 0:
        return 0
    cells = 0
    for index, char in enumerate(text):
        width = display_cell_width(char)
        if cells + width > column:
            return index
        cells += width
    return len(text)


def safe_addstr(win, y: int, x: int, text: str, attr: int = 0) -> None:
    """Write text, clipping to the window and never raising curses.error."""
    try:
        max_y, max_x = win.getmaxyx()
    except curses.error:
        return
    if y < 0 or y >= max_y or x < 0 or x >= max_x:
        return

    text = sanitize(text)
    room = max_x - x
    if y == max_y - 1:
        room -= 1
    if room <= 0 or not text:
        return
    try:
        win.addstr(y, x, text[:room], attr)
    except curses.error:
        pass


def fill_row(win, y: int, attr: int = 0, width: Optional[int] = None) -> None:
    """Paint a full row so reverse video selections look solid."""
    try:
        _, max_x = win.getmaxyx()
    except curses.error:
        return
    room = max_x if width is None else min(width, max_x)
    if room > 1:
        safe_addstr(win, y, 0, " " * (room - 1), attr)


def shorten(text: str, limit: int) -> str:
    """Trim from the left so the most specific part of a path stays visible."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[-limit:]
    return "..." + text[-(limit - 3) :]


def middle_shorten(text: str, limit: int) -> str:
    """Elide the middle so both the label and the path tail stay visible."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 5:
        return text[:limit]
    head = max(1, limit // 3)
    tail = limit - head - 3
    return text[:head] + "..." + text[-tail:]


def match_offset(positions: Optional[Iterable[int]], width: int, margin: int = 6) -> int:
    """Return a horizontal offset that brings the first match into view.

    Without this the preview always starts at column zero, so a match that
    lives further along a long line is never visible.
    """
    if not positions or width <= 0:
        return 0
    first = min(positions)
    if first < max(0, width - margin):
        return 0
    return max(0, first - margin)


def draw_code_line(
    win,
    y: int,
    x: int,
    text: str,
    filename: str,
    theme: Theme,
    *,
    highlight_positions: Optional[Iterable[int]] = None,
    base_attr: int = 0,
    max_width: Optional[int] = None,
    x_offset: int = 0,
) -> None:
    """Draw one syntax highlighted line with optional horizontal scrolling."""
    text = sanitize(text)
    if not text:
        return

    if max_width is None:
        try:
            _, max_x = win.getmaxyx()
        except curses.error:
            return
        max_width = max(0, max_x - x - 1)
    if max_width <= 0:
        return

    positions: Set[int] = set(highlight_positions or ())
    spans = highlight_line(filename, text)

    if not positions:
        # Fast path: slice whole token spans, no per character work.
        col = 0
        for span_text, category in spans:
            start = col
            end = col + len(span_text)
            col = end
            if end <= x_offset:
                continue
            local = max(0, x_offset - start)
            available = max_width - max(0, (start + local) - x_offset)
            if available <= 0:
                break
            chunk = span_text[local : local + available]
            if chunk:
                safe_addstr(
                    win,
                    y,
                    x + max(0, start + local - x_offset),
                    chunk,
                    theme.attr(category) | base_attr,
                )
        return

    # Highlighted path: split spans so matched characters can be underlined.
    col = 0
    run: list = []
    run_category = ""
    run_underlined = False

    def flush() -> None:
        if run and run_category:
            safe_addstr(
                win,
                y,
                x + run[0][1],
                "".join(char for char, _ in run),
                theme.attr(run_category, underline=run_underlined) | base_attr,
            )

    for span_text, category in spans:
        for char in span_text:
            position = col
            col += 1
            if position < x_offset:
                continue
            if position >= x_offset + max_width:
                break
            underlined = position in positions
            if run and (category != run_category or underlined != run_underlined):
                flush()
                run = []
            if not run:
                run_category = category
                run_underlined = underlined
            run.append((char, position - x_offset))
        if col >= x_offset + max_width:
            break
    flush()


def draw_box(
    win,
    top: int,
    left: int,
    height: int,
    width: int,
    title: str,
    theme: Theme,
    attr: int = 0,
) -> None:
    """Draw an ASCII box and return nothing; text is added by the caller."""
    if height < 3 or width < 4:
        return
    horizontal = "-" * (width - 2)
    safe_addstr(win, top, left, "+" + horizontal + "+", attr)
    for row in range(1, height - 1):
        safe_addstr(win, top + row, left, "|" + " " * (width - 2) + "|", attr)
    safe_addstr(win, top + height - 1, left, "+" + horizontal + "+", attr)
    if title:
        label = f" {title} "
        pair = theme.attr("ui.overlay")
        safe_addstr(win, top, left + 2, label[: width - 4], pair | curses.A_BOLD)


def window_size(win) -> tuple:
    """Return (height, width) or (0, 0) when curses cannot report it."""
    try:
        height, width = win.getmaxyx()
        return height, width
    except curses.error:
        return 0, 0
