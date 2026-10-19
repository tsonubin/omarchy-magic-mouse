"""Virtual input source devices for the E2E rig.

Creates real kernel input devices through ``/dev/uinput`` — the same path a
physical Magic Mouse's ``hid-magicmouse`` driver takes — so the daemon under
test consumes genuine evdev events from the genuine input core.

Reuses the ioctl plumbing from :mod:`magicmouse.evdev`; the uinput *abs*
setup is rig-only (the product's virtual mouse is relative-only).
"""

import fcntl
import os
import struct
import time

from magicmouse.evdev import (
    ABS_MT_POSITION_X,
    ABS_MT_POSITION_Y,
    ABS_MT_SLOT,
    ABS_MT_TOUCH_MAJOR,
    ABS_MT_TOUCH_MINOR,
    ABS_MT_TRACKING_ID,
    BTN_LEFT,
    BTN_RIGHT,
    BUS_BLUETOOTH,
    BUS_USB,
    BUS_VIRTUAL,
    EV_ABS,
    EV_KEY,
    EV_REL,
    EV_SYN,
    REL_HWHEEL,
    REL_HWHEEL_HI_RES,
    REL_WHEEL,
    REL_WHEEL_HI_RES,
    REL_X,
    REL_Y,
    SYN_REPORT,
    UI_DEV_CREATE,
    UI_DEV_DESTROY,
    UI_DEV_SETUP,
    UI_SET_ABSBIT,
    UI_SET_EVBIT,
    UI_SET_KEYBIT,
    UI_SET_RELBIT,
    UINPUT_PATH,
    _IOW,
)

UI_ABS_SETUP = _IOW("U", 4, struct.calcsize("Iiiiiii"))

APPLE_VENDOR = 0x05AC
MAGIC_MOUSE_2 = 0x0269

# hid-magicmouse's Magic Mouse axis geometry (drivers/hid/hid-magicmouse.c)
MOUSE_MIN_X, MOUSE_MAX_X, MOUSE_RES_X = -1100, 1258, 26
MOUSE_MIN_Y, MOUSE_MAX_Y, MOUSE_RES_Y = -1589, 2047, 71

MAGIC_MOUSE_NAME = "Apple Magic Mouse (e2e)"
WHEEL_MOUSE_NAME = "Ordinary Wheel Mouse (e2e)"


class UInputSource:
    """A virtual input device with configurable capabilities."""

    def __init__(self, name, bustype, vendor, product, with_touch):
        self.name = name
        self.fd = os.open(UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)
        self._tracking = {}
        self._next_tracking_id = 1

        self._ioctl(UI_SET_EVBIT, EV_KEY)
        self._ioctl(UI_SET_EVBIT, EV_REL)
        self._ioctl(UI_SET_EVBIT, EV_SYN)
        for code in (BTN_LEFT, BTN_RIGHT):
            self._ioctl(UI_SET_KEYBIT, code)
        for code in (
            REL_X,
            REL_Y,
            REL_WHEEL,
            REL_HWHEEL,
            REL_WHEEL_HI_RES,
            REL_HWHEEL_HI_RES,
        ):
            self._ioctl(UI_SET_RELBIT, code)

        if with_touch:
            self._ioctl(UI_SET_EVBIT, EV_ABS)
            for code in (
                ABS_MT_SLOT,
                ABS_MT_TRACKING_ID,
                ABS_MT_POSITION_X,
                ABS_MT_POSITION_Y,
                ABS_MT_TOUCH_MAJOR,
                ABS_MT_TOUCH_MINOR,
            ):
                self._ioctl(UI_SET_ABSBIT, code)
            self._abs_setup(ABS_MT_SLOT, minimum=0, maximum=15)
            self._abs_setup(ABS_MT_TRACKING_ID, minimum=-1, maximum=65535)
            self._abs_setup(
                ABS_MT_POSITION_X,
                minimum=MOUSE_MIN_X,
                maximum=MOUSE_MAX_X,
                fuzz=4,
                resolution=MOUSE_RES_X,
            )
            self._abs_setup(
                ABS_MT_POSITION_Y,
                minimum=MOUSE_MIN_Y,
                maximum=MOUSE_MAX_Y,
                fuzz=4,
                resolution=MOUSE_RES_Y,
            )

        setup = struct.pack("HHHH80sI", bustype, vendor, product, 1, name.encode(), 0)
        self._ioctl(UI_DEV_SETUP, setup)
        self._ioctl(UI_DEV_CREATE, 0)
        time.sleep(0.1)  # let the kernel publish the device node

    def _ioctl(self, request, payload):
        # int payload: passed by value (UI_SET_*BIT); bytes: passed as pointer
        fcntl.ioctl(self.fd, request, payload)

    def _abs_setup(
        self, code, value=0, minimum=0, maximum=0, fuzz=0, flat=0, resolution=0
    ):
        raw = struct.pack("Iiiiiii", code, value, minimum, maximum, fuzz, flat, resolution)
        self._ioctl(UI_ABS_SETUP, raw)

    # --- emitting ------------------------------------------------------------

    def _write(self, ev_type, code, value):
        now = time.monotonic()
        sec = int(now)
        usec = int((now - sec) * 1_000_000)
        os.write(self.fd, struct.pack("llHHi", sec, usec, ev_type, code, value))

    def syn(self):
        self._write(EV_SYN, SYN_REPORT, 0)

    def pointer(self, dx, dy):
        if dx:
            self._write(EV_REL, REL_X, dx)
        if dy:
            self._write(EV_REL, REL_Y, dy)
        self.syn()

    def button(self, down, code=BTN_LEFT):
        self._write(EV_KEY, code, 1 if down else 0)
        self.syn()

    def wheel_detents(self, detents):
        """A physical wheel click: paired legacy + hi-res like the kernel does."""
        self._write(EV_REL, REL_WHEEL, detents)
        self._write(EV_REL, REL_WHEEL_HI_RES, detents * 120)
        self.syn()

    def touch_down(self, slot, x_units, y_units):
        self._write(EV_ABS, ABS_MT_SLOT, slot)
        self._tracking[slot] = self._next_tracking_id
        self._next_tracking_id += 1
        self._write(EV_ABS, ABS_MT_TRACKING_ID, self._tracking[slot])
        self._write(EV_ABS, ABS_MT_POSITION_X, x_units)
        self._write(EV_ABS, ABS_MT_POSITION_Y, y_units)
        self.syn()

    def touch_move(self, slot, x_units, y_units):
        self._write(EV_ABS, ABS_MT_SLOT, slot)
        self._write(EV_ABS, ABS_MT_POSITION_X, x_units)
        self._write(EV_ABS, ABS_MT_POSITION_Y, y_units)
        self.syn()

    def touch_up(self, slot):
        self._write(EV_ABS, ABS_MT_SLOT, slot)
        self._write(EV_ABS, ABS_MT_TRACKING_ID, -1)
        self.syn()

    def close(self):
        try:
            self._ioctl(UI_DEV_DESTROY, 0)
        except OSError:
            pass
        os.close(self.fd)


def magic_mouse():
    return UInputSource(MAGIC_MOUSE_NAME, BUS_BLUETOOTH, APPLE_VENDOR, MAGIC_MOUSE_2, True)


def wheel_mouse():
    # BUS_USB like real hardware: the daemon refuses BUS_VIRTUAL devices so it
    # never hijacks other userspace virtual input tools
    return UInputSource(WHEEL_MOUSE_NAME, BUS_USB, 0x0001, 0x0001, False)
