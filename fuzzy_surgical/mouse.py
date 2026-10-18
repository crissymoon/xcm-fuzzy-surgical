"""Normalised mouse input for the curses views.

Terminals do not agree on how a scroll wheel tick is reported. ncurses 6.1 and
newer expose ``BUTTON5_PRESSED`` for wheel down. The ncurses that ships with
macOS is older, has no button 5 constant, and hands the same wheel tick back as
a ``REPORT_MOUSE_POSITION`` state. Both are mapped to one ``MouseEvent`` here so
the views only deal with ``click`` and ``scroll``.

Mouse support is optional. Every helper degrades to a no-op when the curses
module or the terminal does not provide it, so the keyboard remains enough.
"""

from __future__ import annotations

import curses
import time
from dataclasses import dataclass
from typing import Callable, Optional

SCROLL_LINES = 3
DOUBLE_CLICK_SECONDS = 0.45
DEDUPE_SECONDS = 0.20
MOUSE_KEY = getattr(curses, "KEY_MOUSE", -1)


def _bits(*names: str) -> int:
    mask = 0
    for name in names:
        mask |= int(getattr(curses, name, 0))
    return mask


_CLICK_BITS = _bits(
    "BUTTON1_PRESSED",
    "BUTTON1_CLICKED",
    "BUTTON1_DOUBLE_CLICKED",
    "BUTTON1_TRIPLE_CLICKED",
    "BUTTON2_PRESSED",
    "BUTTON2_CLICKED",
    "BUTTON3_PRESSED",
    "BUTTON3_CLICKED",
)
_SCROLL_UP_BITS = _bits("BUTTON4_PRESSED", "BUTTON4_CLICKED")
_SCROLL_DOWN_BITS = _bits("BUTTON5_PRESSED", "BUTTON5_CLICKED")
_REPORT_BIT = _bits("REPORT_MOUSE_POSITION")
_LEGACY_SCROLL_DOWN = _SCROLL_DOWN_BITS == 0 and _REPORT_BIT != 0
_ACTIVE = False


@dataclass(frozen=True)
class MouseEvent:
    """One normalised terminal mouse event."""

    x: int
    y: int
    kind: str
    direction: int = 0

    @property
    def is_click(self) -> bool:
        return self.kind == "click"

    @property
    def is_scroll(self) -> bool:
        return self.kind == "scroll"


def mouse_available() -> bool:
    """Return True when the curses build exposes mouse functions."""
    return (
        hasattr(curses, "mousemask")
        and hasattr(curses, "getmouse")
        and MOUSE_KEY >= 0
    )


def enable_mouse(stdscr) -> bool:
    """Ask the terminal to report clicks and wheel ticks.

    Returns True when at least one requested event type is available. The
    result is only a capability hint; read_mouse() still guards every call.
    """
    global _ACTIVE
    if not mouse_available():
        return False

    mask = _CLICK_BITS | _SCROLL_UP_BITS | _SCROLL_DOWN_BITS
    if _LEGACY_SCROLL_DOWN:
        # Enabling the position report is safe here because this ncurses build
        # maps wheel down to it and does not switch the terminal into motion
        # tracking mode for the request.
        mask |= _REPORT_BIT

    try:
        result = curses.mousemask(mask)
        try:
            # We act on button presses, so skip the click delay.
            curses.mouseinterval(0)
        except (curses.error, AttributeError):
            pass
    except (curses.error, AttributeError):
        return False

    available = result[0] if isinstance(result, tuple) else result
    _ACTIVE = bool(available & mask)
    return _ACTIVE


def disable_mouse() -> None:
    """Stop terminal mouse reporting before the curses session ends."""
    global _ACTIVE
    if not mouse_available():
        return
    try:
        curses.mousemask(0)
    except (curses.error, AttributeError):
        pass
    _ACTIVE = False


def mouse_enabled() -> bool:
    """Return True when enable_mouse() succeeded."""
    return _ACTIVE


def event_from_state(x: int, y: int, state: int) -> Optional[MouseEvent]:
    """Translate a curses bit state into a click or scroll event.

    Kept separate from getmouse() so the mapping is testable without a
    terminal, including the old ncurses wheel representation.
    """
    if state & _SCROLL_UP_BITS:
        return MouseEvent(x, y, "scroll", -1)
    if _SCROLL_DOWN_BITS and state & _SCROLL_DOWN_BITS:
        return MouseEvent(x, y, "scroll", 1)
    if _LEGACY_SCROLL_DOWN and (state & _REPORT_BIT) and not (state & _CLICK_BITS):
        return MouseEvent(x, y, "scroll", 1)
    if state & _CLICK_BITS:
        return MouseEvent(x, y, "click")
    return None


def read_mouse(stdscr=None) -> Optional[MouseEvent]:
    """Pop one pending mouse event, or None when nothing usable is queued."""
    if not mouse_available():
        return None
    try:
        _ident, x, y, _z, state = curses.getmouse()
    except (curses.error, AttributeError):
        return None
    return event_from_state(x, y, state)


class ClickTracker:
    """Deduplicate terminal click pairs and classify double clicks.

    A terminal can report both a press and a synthesized click for one physical
    click. The short dedupe window swallows the duplicate, while a second press
    at the same position inside the double click window becomes ``double``.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._position: Optional[tuple] = None
        self._at = 0.0

    def reset(self) -> None:
        self._position = None
        self._at = 0.0

    def classify(self, event: MouseEvent) -> Optional[str]:
        """Return single, double or None for a click event."""
        if not event.is_click:
            return None
        now = self._clock()
        position = (event.x, event.y)
        if (
            self._position == position
            and now - self._at <= DEDUPE_SECONDS
        ):
            self._at = now
            return None
        double = (
            self._position == position
            and now - self._at <= DOUBLE_CLICK_SECONDS
        )
        self._position = position
        self._at = now
        return "double" if double else "single"
