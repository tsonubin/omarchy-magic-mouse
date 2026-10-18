"""Per-device pipeline: input events in, virtual-mouse output + actions out.

Device classes (classification is capability based, Apple IDs are hints):

- ``apple_touch_mouse`` — Magic Mouse: MT surface drives scroll and gestures,
  the kernel's own wheel emulation is dropped, pointer/buttons pass through.
- ``wheel_mouse``       — ordinary wheel mouse: detents are animated into
  smooth hi-res scrolling, everything else passes through.
- ``apple_trackpad`` / ``other`` — recognized for the ``devices`` listing but
  never managed; libinput already treats them well.
"""

import time

from . import evdev
from .config import resolve_actions
from .scrolling import ScrollEngine
from .touch import Gesture, ScrollDelta, ScrollEnd, Tap, TouchEngine

KIND_TOUCH_MOUSE = "apple_touch_mouse"
KIND_TRACKPAD = "apple_trackpad"
KIND_WHEEL_MOUSE = "wheel_mouse"
KIND_OTHER = "other"

APPLE_VENDOR = 0x05AC
MAGIC_MOUSE_PRODUCTS = {0x030D, 0x0269, 0x0323}  # MM1, MM2, MM2 USB-C
MAGIC_TRACKPAD_PRODUCTS = {0x030E, 0x0265, 0x0324}  # MT1, MT2, MT2 USB-C

_PASSTHROUGH_BUTTONS = {
    evdev.BTN_LEFT,
    evdev.BTN_RIGHT,
    evdev.BTN_MIDDLE,
    evdev.BTN_SIDE,
    evdev.BTN_EXTRA,
}

_MT_CODES = {
    evdev.ABS_MT_SLOT,
    evdev.ABS_MT_TRACKING_ID,
    evdev.ABS_MT_POSITION_X,
    evdev.ABS_MT_POSITION_Y,
}


def classify(info):
    if info.vendor == APPLE_VENDOR and info.product in MAGIC_MOUSE_PRODUCTS:
        return KIND_TOUCH_MOUSE
    if info.vendor == APPLE_VENDOR and info.product in MAGIC_TRACKPAD_PRODUCTS:
        return KIND_TRACKPAD
    has_mt = evdev.ABS_MT_POSITION_X in info.abs_bits
    if has_mt and evdev.REL_X in info.rel_bits and evdev.BTN_LEFT in info.key_bits:
        return KIND_TOUCH_MOUSE
    if has_mt and evdev.BTN_TOOL_FINGER in info.key_bits:
        return KIND_TRACKPAD
    if evdev.REL_WHEEL in info.rel_bits and evdev.BTN_LEFT in info.key_bits:
        return KIND_WHEEL_MOUSE
    return KIND_OTHER


def synthetic_info(kind):
    """A fabricated :class:`magicmouse.evdev.DeviceInfo` for simulate/tests."""
    if kind == KIND_TOUCH_MOUSE:
        return evdev.DeviceInfo(
            path="virtual:magic-mouse",
            name="Apple Magic Mouse (simulated)",
            bustype=evdev.BUS_VIRTUAL,
            vendor=APPLE_VENDOR,
            product=0x0269,
            version=1,
            ev_bits=frozenset({evdev.EV_KEY, evdev.EV_REL, evdev.EV_ABS}),
            rel_bits=frozenset(
                {
                    evdev.REL_X,
                    evdev.REL_Y,
                    evdev.REL_WHEEL,
                    evdev.REL_HWHEEL,
                    evdev.REL_WHEEL_HI_RES,
                    evdev.REL_HWHEEL_HI_RES,
                }
            ),
            key_bits=frozenset({evdev.BTN_LEFT, evdev.BTN_RIGHT}),
            abs_bits={
                evdev.ABS_MT_SLOT: evdev.AbsInfo(0, 0, 15),
                evdev.ABS_MT_TRACKING_ID: evdev.AbsInfo(-1, -1, 65535),
                evdev.ABS_MT_POSITION_X: evdev.AbsInfo(0, -1100, 1258, resolution=26),
                evdev.ABS_MT_POSITION_Y: evdev.AbsInfo(0, -1589, 2047, resolution=71),
            },
        )
    if kind == KIND_WHEEL_MOUSE:
        return evdev.DeviceInfo(
            path="virtual:wheel-mouse",
            name="Ordinary wheel mouse (simulated)",
            bustype=evdev.BUS_VIRTUAL,
            vendor=0x0001,
            product=0x0001,
            version=1,
            ev_bits=frozenset({evdev.EV_KEY, evdev.EV_REL}),
            rel_bits=frozenset(
                {
                    evdev.REL_X,
                    evdev.REL_Y,
                    evdev.REL_WHEEL,
                    evdev.REL_HWHEEL,
                    evdev.REL_WHEEL_HI_RES,
                    evdev.REL_HWHEEL_HI_RES,
                }
            ),
            key_bits=frozenset({evdev.BTN_LEFT, evdev.BTN_RIGHT, evdev.BTN_MIDDLE}),
            abs_bits={},
        )
    raise ValueError(f"no synthetic device for kind {kind!r}")


class DevicePipeline:
    def __init__(self, info, cfg, sink, dispatcher, clock=time.monotonic):
        self.info = info
        self.kind = classify(info)
        self.sink = sink
        self.dispatcher = dispatcher
        self._clock = clock
        self._actions = resolve_actions(cfg)

        self.scroll = ScrollEngine(cfg["scroll"], sink.scroll)
        self.touch = None
        if self.kind == KIND_TOUCH_MOUSE and (
            cfg["gestures"]["enabled"] or cfg["scroll"]["touch"]["enabled"]
        ):
            res_x = res_y = 0
            abs_x = info.abs_bits.get(evdev.ABS_MT_POSITION_X)
            abs_y = info.abs_bits.get(evdev.ABS_MT_POSITION_Y)
            if abs_x is not None:
                res_x = abs_x.resolution
            if abs_y is not None:
                res_y = abs_y.resolution
            self.touch = TouchEngine(cfg["gestures"], res_x, res_y)

    def process(self, event):
        """Route one input event; output goes to the sink, actions dispatch."""
        now = self._clock()
        ev_type, code, value = event.type, event.code, event.value

        if ev_type == evdev.EV_REL:
            if code in (evdev.REL_X, evdev.REL_Y):
                self.sink.write(ev_type, code, value)
            elif code in (evdev.REL_WHEEL, evdev.REL_HWHEEL):
                if self.kind == KIND_WHEEL_MOUSE:
                    detents_x = value if code == evdev.REL_HWHEEL else 0
                    detents_y = value if code == evdev.REL_WHEEL else 0
                    self.scroll.wheel(now, detents_x, detents_y)
                # apple_touch_mouse: kernel wheel emulation is superseded by
                # the touch surface; dropping it is the point of managing it.
            # hi-res companions are dropped: the engine emits its own pair.
            return

        if ev_type == evdev.EV_KEY:
            if code in _PASSTHROUGH_BUTTONS:
                if value == 1:
                    self.scroll.cancel()
                self.sink.write(ev_type, code, value)
            return

        if ev_type == evdev.EV_ABS:
            if self.touch is not None and code in _MT_CODES:
                self.touch.abs_event(code, value)
            return

        if ev_type == evdev.EV_SYN and code == evdev.SYN_REPORT:
            if self.touch is not None:
                self._frame(now)

    def _frame(self, now):
        for touch_event in self.touch.frame(now):
            if isinstance(touch_event, ScrollDelta):
                self.scroll.touch_scroll(now, touch_event.dx_mm, touch_event.dy_mm)
            elif isinstance(touch_event, ScrollEnd):
                self.scroll.end_touch(now)
            elif isinstance(touch_event, (Gesture, Tap)):
                self.scroll.cancel()
                name = touch_event.name if isinstance(touch_event, Gesture) else "tap"
                spec = self._actions.get(name)
                if spec is not None:
                    self.dispatcher.invoke(spec)

    def needs_ticks(self):
        return self.scroll.is_active()

    def tick(self, now):
        """Advance smooth/inertial scrolling one frame."""
        if self.scroll.is_active():
            self.scroll.tick(now)
