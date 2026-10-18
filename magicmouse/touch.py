"""Multi-touch interpretation for Magic Mouse style touch surfaces.

Consumes the kernel's MT slot protocol (``ABS_MT_SLOT`` /
``ABS_MT_TRACKING_ID`` / ``ABS_MT_POSITION_*``) and turns frames into:

- :class:`ScrollDelta` — one finger is dragging; scroll by that delta.
- :class:`ScrollEnd`   — the scrolling finger lifted (start inertia).
- :class:`Tap`         — a quick, stationary one-finger tap.
- :class:`Gesture`     — ``swipe_left/right/up/down`` or ``double_tap``.

Session rules (macOS-like):

- one finger drag            -> scroll (fires as soon as it passes the slop)
- ``fingers``-finger drag    -> swipe, judged from the centroid; fires as soon
  as the dominant axis passes the distance threshold, once per touch
- a second finger joining a barely-started scroll converts it into a gesture
  candidate; joining a real scroll aborts until all fingers lift
- more fingers than ``fingers`` aborts until all fingers lift
- a quick stationary one-finger contact is a tap; two in a row is a double tap

Distances are millimetres, converted from device units via the per-axis
resolution the kernel reports (``input_abs_set_res``).
"""

from dataclasses import dataclass

from . import evdev

FALLBACK_RESOLUTION = 26.0  # units/mm, hid-magicmouse Magic Mouse X axis
_GESTURE_CONVERT_SCROLL_MM = 2.0  # small scrolls may still become gestures


@dataclass(frozen=True)
class ScrollDelta:
    dx_mm: float
    dy_mm: float


@dataclass(frozen=True)
class ScrollEnd:
    pass


@dataclass(frozen=True)
class Tap:
    pass


@dataclass(frozen=True)
class Gesture:
    name: str


class _Contact:
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y


class TouchEngine:
    def __init__(self, gestures_cfg, res_x=0, res_y=0):
        self._res_x = res_x or FALLBACK_RESOLUTION
        self._res_y = res_y or FALLBACK_RESOLUTION

        self._enabled = bool(gestures_cfg["enabled"])
        self._fingers = int(gestures_cfg["fingers"])
        swipe = gestures_cfg["swipe"]
        self._swipe_distance = float(swipe["min_distance_mm"])
        self._swipe_ratio = float(swipe["axis_ratio"])
        self._swipe_max_duration = float(swipe["max_duration_ms"]) / 1000
        tap = gestures_cfg["tap"]
        self._tap_max_duration = float(tap["max_duration_ms"]) / 1000
        self._tap_slop = float(tap["slop_mm"])
        self._double_interval = float(tap["double_interval_ms"]) / 1000
        self._double_distance = float(tap["double_distance_mm"])

        self._slots = {}
        self._current_slot = 0
        self._last_abs = {}  # kernel-level per-code abs state (see abs_event)

        # session state
        self._mode = None  # None | "scroll" | "gesture" | "done" | "abort"
        self._start = None  # (centroid_x_mm, centroid_y_mm, time)
        self._last_centroid = None
        self._scrolled_mm = 0.0
        self._last_tap = None  # (time, x_mm, y_mm)
        self._contact_started = False
        self._max_contacts = 0

    # --- MT slot protocol ----------------------------------------------------

    def abs_event(self, code, value):
        # The kernel input core drops unchanged ABS values (fuzz=0 dedup), so
        # consumers see sparse streams: coordinates are per-code persistent
        # state and a new contact inherits the last reported position.
        self._last_abs[code] = value
        if code == evdev.ABS_MT_SLOT:
            self._current_slot = value
        elif code == evdev.ABS_MT_TRACKING_ID:
            if value == -1:
                self._slots.pop(self._current_slot, None)
            else:
                self._slots[self._current_slot] = _Contact(
                    self._last_abs.get(evdev.ABS_MT_POSITION_X, 0),
                    self._last_abs.get(evdev.ABS_MT_POSITION_Y, 0),
                )
        elif code in (evdev.ABS_MT_POSITION_X, evdev.ABS_MT_POSITION_Y):
            contact = self._slots.get(self._current_slot)
            if contact is not None:
                if code == evdev.ABS_MT_POSITION_X:
                    contact.x = value
                else:
                    contact.y = value

    # --- frame ---------------------------------------------------------------

    def frame(self, now):
        """Interpret one SYN_REPORT frame; returns a list of touch events."""
        contacts = [c for c in self._slots.values() if c.x is not None and c.y is not None]
        count = len(contacts)

        if count == 0:
            events = self._end_session(now)
            return events

        self._contact_started = True
        self._max_contacts = max(self._max_contacts, count)
        centroid_x = sum(c.x for c in contacts) / count / self._res_x
        centroid_y = sum(c.y for c in contacts) / count / self._res_y

        if self._start is None:
            self._start = (centroid_x, centroid_y, now)
            self._last_centroid = (centroid_x, centroid_y)
            return []

        events = []
        prev_x, prev_y = self._last_centroid
        self._last_centroid = (centroid_x, centroid_y)

        if count == 1 and self._mode in (None, "scroll"):
            if self._mode is None:
                origin_x, origin_y, _ = self._start
                moved = abs(centroid_x - origin_x) + abs(centroid_y - origin_y)
                if moved <= self._tap_slop:
                    return []
                self._mode = "scroll"
                # scrolling is established: account for the whole drag since
                # touch start, slop included
                prev_x, prev_y = origin_x, origin_y
            self._scrolled_mm += abs(centroid_x - prev_x) + abs(centroid_y - prev_y)
            events.append(ScrollDelta(centroid_x - prev_x, centroid_y - prev_y))
            return events

        if (
            self._enabled
            and count == self._fingers
            and self._mode in (None, "scroll", "gesture")
        ):
            if self._mode == "scroll":
                # a barely-started scroll may still become a swipe
                if self._scrolled_mm > _GESTURE_CONVERT_SCROLL_MM:
                    self._mode = "abort"
                    return []
                self._start = (centroid_x, centroid_y, now)
            elif self._mode is None:
                self._start = (centroid_x, centroid_y, now)
            self._mode = "gesture"
            return self._check_swipe(centroid_x, centroid_y, now)

        # contact count is neither a scroll nor the configured swipe
        if self._mode in ("scroll", "gesture"):
            self._mode = "abort"
        return []

    def _check_swipe(self, centroid_x, centroid_y, now):
        origin_x, origin_y, started = self._start
        dx = centroid_x - origin_x
        dy = centroid_y - origin_y
        elapsed = now - started
        if elapsed > self._swipe_max_duration:
            self._mode = "abort"
            return []
        if abs(dx) >= abs(dy):
            major, minor = dx, dy
        else:
            major, minor = dy, dx
        if abs(major) < self._swipe_distance or abs(minor) > abs(major) / self._swipe_ratio:
            return []
        if abs(dx) >= abs(dy):
            name = "swipe_right" if dx > 0 else "swipe_left"
        else:
            name = "swipe_down" if dy > 0 else "swipe_up"
        self._mode = "done"
        return [Gesture(name)]

    def _end_session(self, now):
        events = []
        if self._mode == "scroll":
            events.append(ScrollEnd())
        elif (
            self._mode is None
            and self._enabled
            and self._contact_started
            and self._start is not None
            and self._max_contacts == 1
        ):
            origin_x, origin_y, started = self._start
            # centroid at this point equals the last seen centroid
            last_x, last_y = self._last_centroid or (origin_x, origin_y)
            moved = abs(last_x - origin_x) + abs(last_y - origin_y)
            if now - started <= self._tap_max_duration and moved <= self._tap_slop:
                if (
                    self._last_tap is not None
                    and now - self._last_tap[0] <= self._double_interval
                    and abs(last_x - self._last_tap[1]) + abs(last_y - self._last_tap[2])
                    <= self._double_distance
                ):
                    self._last_tap = None
                    events.append(Gesture("double_tap"))
                else:
                    self._last_tap = (now, last_x, last_y)
                    events.append(Tap())
        self._mode = None
        self._start = None
        self._last_centroid = None
        self._scrolled_mm = 0.0
        self._contact_started = False
        self._max_contacts = 0
        return events
