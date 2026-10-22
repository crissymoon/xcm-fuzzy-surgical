"""Find and jump to text inside the whole-file editor.

Ctrl+F (or a Command chord when the terminal forwards it) opens a one-row
prompt. Typing searches the buffer as it changes and moves the caret onto the
first match at or after the position where the prompt opened, so the view
always lands on the text instead of only counting matches. Enter or Down goes
to the next match, Up goes to the previous one and Esc closes the prompt.

The prompt is a state object with the matching and highlighting helpers next
to it; the editor mixin only wires keys to those helpers so the behavior can
be tested without a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

import curses

from fuzzy_surgical.renderer import safe_addstr, sanitize
from fuzzy_surgical.utils import is_backspace, is_enter, printable_char

MAX_QUERY_CHARS = 200
# A pathological query on a large file must not freeze the editor.
MAX_MATCHES = 20_000

CTRL_F = 6

TYPED = "typed"
IGNORED = "ignored"
CANCEL = "cancel"
NEXT = "next"
PREVIOUS = "previous"
CLEAR = "clear"

Match = Tuple[int, int, int]


def _smart_case(query: str) -> bool:
    """Return True when the query is all lowercase and matches loosely."""
    return not any(char.isupper() for char in query)


def find_matches(lines: Sequence[str], query: str) -> List[Match]:
    """Return non-overlapping (row, start, end) matches in file order."""
    if not query:
        return []
    loose = _smart_case(query)
    matches: List[Match] = []
    for row, line in enumerate(lines):
        haystack = line.lower() if loose else line
        needle = query.lower() if loose else query
        # Lowercasing can change the length for a few scripts; searching the
        # original line is safer than reporting columns that do not exist.
        if len(haystack) != len(line):
            haystack = line
            needle = query
        start = 0
        while len(matches) < MAX_MATCHES:
            found = haystack.find(needle, start)
            if found < 0:
                break
            matches.append((row, found, found + len(query)))
            start = found + max(1, len(query))
        if len(matches) >= MAX_MATCHES:
            break
    return matches


@dataclass
class FindPrompt:
    """State for one find session."""

    active: bool = False
    query: str = ""
    matches: List[Match] = field(default_factory=list)
    index: int = -1
    origin: Tuple[int, int] = (0, 0)
    by_row: Dict[int, List[Match]] = field(default_factory=dict)

    def open(self, row: int, column: int) -> None:
        """Open the prompt, keeping the last query for a quick repeat."""
        self.active = True
        self.origin = (row, column)
        self.matches = []
        self.by_row = {}
        self.index = -1

    def close(self) -> None:
        """Close the prompt and drop the highlights."""
        self.active = False
        self.matches = []
        self.by_row = {}
        self.index = -1

    def reset(self) -> None:
        """Forget the query as well, used when another file is opened."""
        self.close()
        self.query = ""
        self.origin = (0, 0)

    @property
    def current(self) -> Match:
        """Return the match under the caret, or a safe placeholder."""
        if 0 <= self.index < len(self.matches):
            return self.matches[self.index]
        return (0, 0, 0)

    def matches_on(self, row: int) -> Sequence[Match]:
        """Return the matches that start on one buffer line."""
        return self.by_row.get(row, ())

    def load(self, matches: List[Match]) -> None:
        """Store a fresh match list and index it by row for drawing."""
        self.matches = matches
        self.by_row = {}
        for match in matches:
            self.by_row.setdefault(match[0], []).append(match)

    def press(self, key) -> str:
        """Handle one key and return the action the editor should take."""
        if key == curses.ascii.ESC:
            return CANCEL
        if key == 21:  # Ctrl+U clears the query without closing the prompt
            self.query = ""
            return CLEAR
        if is_backspace(key):
            self.query = self.query[:-1]
            return TYPED
        if is_enter(key) or key == curses.KEY_DOWN or key == curses.KEY_F3:
            return NEXT
        if key == curses.KEY_UP:
            return PREVIOUS
        char = printable_char(key)
        if char is not None:
            if len(self.query) < MAX_QUERY_CHARS:
                self.query += char
            return TYPED
        return IGNORED

    def hint(self) -> str:
        """Return the prompt row shown under the buffer."""
        if not self.query:
            return (
                "Find: type the text to find. Enter next, Up previous, "
                "Esc closes."
            )
        if not self.matches:
            return f"Find: {self.query}   no matches. Esc closes."
        row = self.current[0] + 1
        return (
            f"Find: {self.query}   {self.index + 1}/{len(self.matches)} "
            f"on line {row}   Enter next, Up previous, Esc closes."
        )

    def caret_column(self) -> int:
        """Return the screen column where typing continues the query."""
        return len("Find: ") + len(self.query)


class EditorFindMixin:
    """Find actions for the whole-file editor."""

    def _open_find_prompt(self) -> None:
        """Open the prompt, or step to the next match when it is already open."""
        if self.find.active:
            self._find_advance(1)
            return
        self.find.open(self.buffer.cursor_row, self.buffer.cursor_col)
        self.error = ""
        self.notice = ""
        self.completion.close()
        self.diagnostics_panel.close()
        self.jump.close()
        if self.find.query:
            self._find_refresh()

    def _find_paste(self, text: str) -> None:
        """Append a pasted line to the query instead of editing the file."""
        cleaned = "".join(
            char for char in text if char.isprintable() and char not in "\r\n"
        )
        if not cleaned:
            return
        room = MAX_QUERY_CHARS - len(self.find.query)
        self.find.query += cleaned[:room]
        self._find_refresh()

    def _handle_find_key(self, key) -> None:
        """Interpret a key while the find prompt is open."""
        if key == CTRL_F:
            self._find_advance(1)
            return
        action = self.find.press(key)
        if action == CANCEL:
            self.find.close()
            self.notice = "Find closed."
            return
        if action == IGNORED:
            return
        if action == CLEAR:
            self.find.load([])
            self.find.index = -1
            self.find.active = True
            self.notice = ""
            return
        if action == TYPED:
            self._find_refresh()
        elif action == NEXT:
            self._find_advance(1)
        elif action == PREVIOUS:
            self._find_advance(-1)

    def _find_refresh(self) -> None:
        """Search the buffer again and land on the first match near the origin."""
        query = self.find.query
        if not query:
            self.find.load([])
            self.notice = ""
            return
        matches = find_matches(self.buffer.lines, query)
        self.find.load(matches)
        if not matches:
            self.find.index = -1
            self.notice = f"No matches for '{query}'."
            return
        row, column = self.find.origin
        self.find.index = self._find_index(
            matches, (row, column), strict=False
        )
        self._land_on_match()

    @staticmethod
    def _find_index(
        matches: List[Match],
        position: Tuple[int, int],
        *,
        strict: bool,
        step: int = 1,
    ) -> int:
        """Return the first match at or after (or before) a buffer position."""
        if step < 0:
            for index in range(len(matches) - 1, -1, -1):
                here = matches[index][:2]
                if here < position or (not strict and here == position):
                    return index
            return len(matches) - 1
        for index, (match_row, start, _end) in enumerate(matches):
            here = (match_row, start)
            if here > position or (not strict and here == position):
                return index
        return 0

    def _find_advance(self, step: int) -> None:
        """Move to the next or previous match, wrapping at the ends."""
        if not self.find.query:
            self.notice = "Type the text to find first."
            return
        if not self.find.matches:
            # The prompt was closed and its highlights dropped. Rebuild them
            # from the caret so F3 continues after the match it is sitting on.
            matches = find_matches(self.buffer.lines, self.find.query)
            self.find.load(matches)
            if not matches:
                self.find.index = -1
                self.notice = f"No matches for '{self.find.query}'."
                return
            position = (self.buffer.cursor_row, self.buffer.cursor_col)
            self.find.index = self._find_index(
                matches, position, strict=True, step=step
            )
            self._land_on_match()
            return
        count = len(self.find.matches)
        self.find.index = (self.find.index + step) % count
        self._land_on_match()

    def _land_on_match(self) -> None:
        """Put the caret on the selected match and report where it landed."""
        row, start, _end = self.find.current
        self.buffer.set_cursor(row, start)
        self.selection.clear()
        self.completion.close()
        self.error = ""
        self.notice = (
            f"Match {self.find.index + 1} of {len(self.find.matches)} "
            f"on line {row + 1}."
        )


def draw_match(
    win,
    screen_row: int,
    gutter: int,
    line: str,
    start: int,
    end: int,
    x_offset: int,
    available: int,
    theme,
    *,
    current: bool,
) -> None:
    """Paint one match over the syntax colours of a rendered line."""
    if available <= 0 or end <= start:
        return
    display = sanitize(line)
    start_cell = len(sanitize(line[:start]))
    end_cell = len(sanitize(line[:end]))
    first = max(start_cell, x_offset)
    last = min(end_cell, x_offset + available)
    if last <= first:
        return
    chunk = display[first:last]
    if not chunk:
        return
    attr = theme.attr("ui.find", bold=True, reverse=current)
    safe_addstr(win, screen_row, gutter + (first - x_offset), chunk, attr)
