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

SCROLL_LINES = 4
SCROLL_TRACKPAD_STEP = 0.35
SCROLL_MAX_RATE = 50.0
SCROLL_BURST_GAP = 0.045
DOUBLE_CLICK_SECONDS = 0.45
DEDUPE_SECONDS = 0.20
CLICK_ECHO_SECONDS = 0.35
CLICK_ECHO_DISTANCE = 2
MOUSE_KEY = getattr(curses, "KEY_MOUSE", -1)
_LAST_SCROLL = [float("-inf"), 0, 0, 0]


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
)
# Horizontal wheel ticks are reported as buttons 2 and 3 by older ncurses
# builds. They are not clicks and must not move a selection.
_PRIMARY_CLICK_BITS = _bits("BUTTON1_PRESSED", "BUTTON1_CLICKED")
# Older ncurses builds report horizontal wheel ticks as buttons 2 and 3. They
# stay in the mask so the terminal encodes them as themselves instead of a
# position report, which would otherwise look like wheel down.
_IGNORED_BUTTON_BITS = _bits(
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

    mask = _PRIMARY_CLICK_BITS | _IGNORED_BUTTON_BITS | _SCROLL_UP_BITS | _SCROLL_DOWN_BITS
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
    if _LEGACY_SCROLL_DOWN and (state & _REPORT_BIT) and not (state & _PRIMARY_CLICK_BITS):
        return MouseEvent(x, y, "scroll", 1)
    if state & _PRIMARY_CLICK_BITS:
        return MouseEvent(x, y, "click")
    return None


def reset_input_state() -> None:
    """Forget the last scroll, so a new view starts with a clean guard."""
    global _LAST_SCROLL
    _LAST_SCROLL = [float("-inf"), 0, 0, 0]


def normalise_event(
    x: int, y: int, state: int, now: Optional[float] = None
) -> Optional[MouseEvent]:
    """Map one raw curses state to a click or scroll.

    Old ncurses builds cannot represent wheel down. Repeated wheel ticks at
    one cell are then decoded as ``REPORT_MOUSE_POSITION`` and, after a few
    ticks, as a synthetic button one press at the same cell. Without a guard
    that synthetic press moves the caret to the pointer, which looks like the
    scroll ignoring the caret. A click at the cell that was just scrolled,
    inside ``CLICK_ECHO_SECONDS``, is treated as another tick in the same
    direction.
    """
    event = event_from_state(x, y, state)
    if event is None:
        return None

    when = time.monotonic() if now is None else now
    if event.is_scroll:
        _LAST_SCROLL[:] = [when, event.x, event.y, event.direction]
        return event

    previous = _LAST_SCROLL
    if (
        event.is_click
        and previous[3] != 0
        and when - previous[0] <= CLICK_ECHO_SECONDS
        and abs(event.x - previous[1]) <= CLICK_ECHO_DISTANCE
        and abs(event.y - previous[2]) <= CLICK_ECHO_DISTANCE
    ):
        previous[0] = when
        return MouseEvent(event.x, event.y, "scroll", previous[3])
    return event


def read_mouse(stdscr=None) -> Optional[MouseEvent]:
    """Pop one pending mouse event, or None when nothing usable is queued."""
    if not mouse_available():
        return None
    try:
        _ident, x, y, _z, state = curses.getmouse()
    except (curses.error, AttributeError):
        return None
    return normalise_event(x, y, state)


class ScrollGate:
    """Convert wheel events into a measured number of rows.

    A mouse wheel sends one event per notch, so a notch moves ``SCROLL_LINES``
    rows and is applied at once. A trackpad emits a dense stream instead. Each
    event in that stream contributes a fraction of a row and the fractions are
    carried between events, so the view moves on every few events instead of
    dropping them. Movement is capped by ``SCROLL_MAX_RATE`` so momentum cannot
    race to the end of the file, and a direction change is applied at once so
    reversing the fingers feels immediate.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last = float("-inf")
        self._direction = 0
        self._carry = 0.0

    def reset(self) -> None:
        """Forget timing and carry, for example when the view changes."""
        self._last = float("-inf")
        self._direction = 0
        self._carry = 0.0

    def lines(self, direction: int, per_notch: int = SCROLL_LINES) -> int:
        """Return signed rows to move for one wheel event, or 0 to wait."""
        if direction == 0:
            return 0
        now = self._clock()
        gap = now - self._last
        self._last = now
        sign = 1 if direction > 0 else -1
        step = max(1, per_notch)
        if gap < 0 or sign != self._direction or gap >= SCROLL_BURST_GAP:
            self._direction = sign
            self._carry = 0.0
            return sign * step
        self._carry += min(SCROLL_TRACKPAD_STEP, gap * SCROLL_MAX_RATE)
        whole = int(self._carry)
        if whole <= 0:
            return 0
        self._carry -= whole
        return sign * whole


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
