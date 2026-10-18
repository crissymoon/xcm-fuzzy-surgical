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
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

SCROLL_LINES = 4
SCROLL_TRACKPAD_STEP = 0.35
SCROLL_MAX_RATE = 50.0
SCROLL_BURST_GAP = 0.045
DOUBLE_CLICK_SECONDS = 0.45
DEDUPE_SECONDS = 0.06
CLICK_ECHO_SECONDS = 0.35
CLICK_ECHO_DISTANCE = 2
# ncurses on several systems (notably the one shipped with macOS) asks the
# terminal for mode 1000 only. That reports presses and releases but no
# motion, so a click-drag can never grow a selection. Button-event tracking
# (1002) is requested directly, after ncurses has enabled its own mode.
# Mode 1006 is switched off so the terminal keeps the legacy encoding, which
# every ncurses build can decode; SGR reports on such a build would be typed
# into the buffer as text.
BUTTON_EVENT_TRACKING_ON = "\x1b[?1006l\x1b[?1002h"
BUTTON_EVENT_TRACKING_OFF = "\x1b[?1002l"
# Repeated right presses arrive when a terminal reports a horizontal
# trackpad swipe through the same button as the right click. A burst is
# swallowed so a swipe cannot open a menu on every tick.
RIGHT_CLICK_BURST = 0.12
MOUSE_KEY = getattr(curses, "KEY_MOUSE", -1)
_LAST_SCROLL = [float("-inf"), 0, 0, 0]
_LAST_RIGHT = [float("-inf")]


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
_PRESS_BITS = _bits("BUTTON1_PRESSED")
_RELEASE_BITS = _bits("BUTTON1_RELEASED")
# The right button opens the context menu. Middle button presses and older
# horizontal wheel encodings stay ignored so they never move a selection.
_RIGHT_CLICK_BITS = _bits("BUTTON3_PRESSED", "BUTTON3_CLICKED")
# Older ncurses builds report horizontal wheel ticks as buttons 2 and 3. They
# stay in the mask so the terminal encodes them as themselves instead of a
# position report, which would otherwise look like wheel down.
_IGNORED_BUTTON_BITS = _bits(
    "BUTTON2_PRESSED",
    "BUTTON2_CLICKED",
)
_SCROLL_UP_BITS = _bits("BUTTON4_PRESSED", "BUTTON4_CLICKED")
_SCROLL_DOWN_BITS = _bits("BUTTON5_PRESSED", "BUTTON5_CLICKED")
_REPORT_BIT = _bits("REPORT_MOUSE_POSITION")
_LEGACY_SCROLL_DOWN = _SCROLL_DOWN_BITS == 0 and _REPORT_BIT != 0
_ACTIVE = False
_EXTENDED_TRACKING = False
# Old ncurses builds decode a drag as a bare position report, exactly like
# wheel down. The button state is lost, so a press has to be remembered before
# a report can be classed as a drag rather than a wheel tick.
_BUTTON_HELD = False


@dataclass(frozen=True)
class MouseEvent:
    """One normalised terminal mouse event."""

    x: int
    y: int
    kind: str
    direction: int = 0
    phase: str = ""

    @property
    def is_click(self) -> bool:
        return self.kind == "click"

    @property
    def is_scroll(self) -> bool:
        return self.kind == "scroll"

    @property
    def is_drag(self) -> bool:
        return self.kind == "drag"

    @property
    def is_release(self) -> bool:
        return self.kind == "release"

    @property
    def is_right_click(self) -> bool:
        return self.kind == "right_click"


def mouse_available() -> bool:
    """Return True when the curses build exposes mouse functions."""
    return (
        hasattr(curses, "mousemask")
        and hasattr(curses, "getmouse")
        and MOUSE_KEY >= 0
    )


def _write_terminal(sequence: str) -> bool:
    """Write a control sequence straight to the terminal.

    ncurses decides which mouse mode to request, and several builds ask for
    mode 1000 only. Writing the mode here, after curses has drained its own
    state, adds motion reporting without changing anything else. Non-tty
    output is left alone so redirected runs never collect escape codes.
    """
    if os.name == "nt":
        return False
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


def extended_tracking_enabled() -> bool:
    """Return True when button-event tracking was requested from the terminal."""
    return _EXTENDED_TRACKING


def enable_mouse(stdscr) -> bool:
    """Ask the terminal to report clicks, motion and wheel ticks.

    Returns True when at least one requested event type is available. The
    result is only a capability hint; read_mouse() still guards every call.
    """
    global _ACTIVE, _EXTENDED_TRACKING
    if not mouse_available():
        return False

    mask = (
        _CLICK_BITS
        | _RELEASE_BITS
        | _RIGHT_CLICK_BITS
        | _IGNORED_BUTTON_BITS
        | _SCROLL_UP_BITS
        | _SCROLL_DOWN_BITS
        | _REPORT_BIT
    )

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
    if _ACTIVE:
        _EXTENDED_TRACKING = _write_terminal(BUTTON_EVENT_TRACKING_ON)
    return _ACTIVE


def disable_mouse() -> None:
    """Stop terminal mouse reporting before the curses session ends."""
    global _ACTIVE, _EXTENDED_TRACKING
    if not mouse_available() and not _EXTENDED_TRACKING:
        return
    try:
        curses.mousemask(0)
    except (curses.error, AttributeError):
        pass
    if _EXTENDED_TRACKING:
        _write_terminal(BUTTON_EVENT_TRACKING_OFF)
    _ACTIVE = False
    _EXTENDED_TRACKING = False


def mouse_enabled() -> bool:
    """Return True when enable_mouse() succeeded."""
    return _ACTIVE


def event_from_state(x: int, y: int, state: int) -> Optional[MouseEvent]:
    """Translate a curses bit state into a click, drag, release or scroll.

    Kept separate from getmouse() so the mapping is testable without a
    terminal, including the old ncurses wheel representation. Motion events
    carry ``REPORT_MOUSE_POSITION``; when the primary button is still held that
    is a drag, which is what the editor uses to grow a selection.
    """
    global _BUTTON_HELD
    if state & _SCROLL_UP_BITS:
        _BUTTON_HELD = False
        return MouseEvent(x, y, "scroll", -1)
    if _SCROLL_DOWN_BITS and state & _SCROLL_DOWN_BITS:
        _BUTTON_HELD = False
        return MouseEvent(x, y, "scroll", 1)
    if _RIGHT_CLICK_BITS and state & _RIGHT_CLICK_BITS:
        return MouseEvent(x, y, "right_click")

    motion = bool(_REPORT_BIT and state & _REPORT_BIT)
    primary = bool(_PRIMARY_CLICK_BITS and state & _PRIMARY_CLICK_BITS)
    if _RELEASE_BITS and state & _RELEASE_BITS:
        _BUTTON_HELD = False
        return MouseEvent(x, y, "release")
    if motion and primary:
        _BUTTON_HELD = True
        return MouseEvent(x, y, "drag")
    if motion and _BUTTON_HELD:
        # Legacy ncurses lost the button bits on the position report.
        return MouseEvent(x, y, "drag")
    if _LEGACY_SCROLL_DOWN and motion:
        # Old ncurses builds have no button 5 and report wheel down as a bare
        # position report while no button is held.
        return MouseEvent(x, y, "scroll", 1)
    if primary:
        _BUTTON_HELD = True
        return MouseEvent(x, y, "click")
    return None


def reset_input_state() -> None:
    """Forget the last scroll and pressed button, for a clean view start."""
    global _LAST_SCROLL, _BUTTON_HELD, _LAST_RIGHT
    _LAST_SCROLL = [float("-inf"), 0, 0, 0]
    _LAST_RIGHT = [float("-inf")]
    _BUTTON_HELD = False


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
    if event.is_right_click:
        if when - _LAST_RIGHT[0] < RIGHT_CLICK_BURST:
            return None
        _LAST_RIGHT[0] = when
        return event
    if event.is_scroll:
        _LAST_SCROLL[:] = [when, event.x, event.y, event.direction]
        return event
    if event.is_drag:
        # A drag is deliberate; it must not be mistaken for a wheel echo.
        _LAST_SCROLL[:] = [float("-inf"), 0, 0, 0]
        return event
    if event.is_release:
        return event

    previous = _LAST_SCROLL
    if (
        not _EXTENDED_TRACKING
        and event.is_click
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
        self._count = 0

    def reset(self) -> None:
        self._position = None
        self._at = 0.0
        self._count = 0

    def classify(self, event: MouseEvent) -> Optional[str]:
        """Return single, double, triple or None for a click event."""
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
        if (
            self._position == position
            and now - self._at <= DOUBLE_CLICK_SECONDS
        ):
            self._count += 1
        else:
            self._count = 1
        self._position = position
        self._at = now
        if self._count >= 3:
            return "triple"
        return "double" if self._count == 2 else "single"
