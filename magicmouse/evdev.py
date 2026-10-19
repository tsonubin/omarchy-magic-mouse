"""Minimal Linux evdev/uinput protocol layer, pure standard library.

Speaks the kernel's ``struct input_event`` / ioctl UAPI directly so the daemon
has no compiled dependencies (no python-evdev).  Constants mirror
``linux/input-event-codes.h``, ``linux/input.h`` and ``linux/uinput.h``
(sources: Linux UAPI, stable ABI; values verified against kernel 6.12 headers).
"""

import errno
import fcntl
import glob
import os
import struct
from collections import namedtuple
from dataclasses import dataclass, field

# --- linux/input-event-codes.h -------------------------------------------------

EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
EV_MSC = 0x04
EV_MAX = 0x1F

SYN_REPORT = 0

REL_X = 0x00
REL_Y = 0x01
REL_HWHEEL = 0x06
REL_WHEEL = 0x08
REL_WHEEL_HI_RES = 0x0B
REL_HWHEEL_HI_RES = 0x0C
REL_MAX = 0x0F

ABS_X = 0x00
ABS_Y = 0x01
ABS_MT_SLOT = 0x2F
ABS_MT_TOUCH_MAJOR = 0x30
ABS_MT_TOUCH_MINOR = 0x31
ABS_MT_ORIENTATION = 0x34
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TOOL_TYPE = 0x37
ABS_MT_TRACKING_ID = 0x39
ABS_MT_PRESSURE = 0x3A
ABS_MAX = 0x3F

BTN_MOUSE = BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112
BTN_SIDE = 0x113
BTN_EXTRA = 0x114
BTN_TOUCH = 0x14A
BTN_TOOL_FINGER = 0x145
KEY_MAX = 0x2FF

EVENT_NAMES = {
    EV_SYN: "EV_SYN",
    EV_KEY: "EV_KEY",
    EV_REL: "EV_REL",
    EV_ABS: "EV_ABS",
    EV_MSC: "EV_MSC",
}

CODE_NAMES = {
    (EV_SYN, SYN_REPORT): "SYN_REPORT",
    (EV_KEY, BTN_LEFT): "BTN_LEFT",
    (EV_KEY, BTN_RIGHT): "BTN_RIGHT",
    (EV_KEY, BTN_MIDDLE): "BTN_MIDDLE",
    (EV_KEY, BTN_SIDE): "BTN_SIDE",
    (EV_KEY, BTN_EXTRA): "BTN_EXTRA",
    (EV_KEY, BTN_TOUCH): "BTN_TOUCH",
    (EV_KEY, BTN_TOOL_FINGER): "BTN_TOOL_FINGER",
    (EV_REL, REL_X): "REL_X",
    (EV_REL, REL_Y): "REL_Y",
    (EV_REL, REL_HWHEEL): "REL_HWHEEL",
    (EV_REL, REL_WHEEL): "REL_WHEEL",
    (EV_REL, REL_WHEEL_HI_RES): "REL_WHEEL_HI_RES",
    (EV_REL, REL_HWHEEL_HI_RES): "REL_HWHEEL_HI_RES",
    (EV_ABS, ABS_X): "ABS_X",
    (EV_ABS, ABS_Y): "ABS_Y",
    (EV_ABS, ABS_MT_SLOT): "ABS_MT_SLOT",
    (EV_ABS, ABS_MT_TOUCH_MAJOR): "ABS_MT_TOUCH_MAJOR",
    (EV_ABS, ABS_MT_TOUCH_MINOR): "ABS_MT_TOUCH_MINOR",
    (EV_ABS, ABS_MT_ORIENTATION): "ABS_MT_ORIENTATION",
    (EV_ABS, ABS_MT_POSITION_X): "ABS_MT_POSITION_X",
    (EV_ABS, ABS_MT_POSITION_Y): "ABS_MT_POSITION_Y",
    (EV_ABS, ABS_MT_TOOL_TYPE): "ABS_MT_TOOL_TYPE",
    (EV_ABS, ABS_MT_TRACKING_ID): "ABS_MT_TRACKING_ID",
    (EV_ABS, ABS_MT_PRESSURE): "ABS_MT_PRESSURE",
}

NAME_TO_TYPE_CODE = {name: (ev, code) for (ev, code), name in CODE_NAMES.items()}
NAME_TO_TYPE_CODE.update({name: (code, None) for code, name in EVENT_NAMES.items()})
NAME_TO_CODE = {
    "SYN_REPORT": SYN_REPORT,
    "BTN_LEFT": BTN_LEFT,
    "BTN_RIGHT": BTN_RIGHT,
    "BTN_MIDDLE": BTN_MIDDLE,
    "BTN_SIDE": BTN_SIDE,
    "BTN_EXTRA": BTN_EXTRA,
    "BTN_TOUCH": BTN_TOUCH,
    "BTN_TOOL_FINGER": BTN_TOOL_FINGER,
    "REL_X": REL_X,
    "REL_Y": REL_Y,
    "REL_HWHEEL": REL_HWHEEL,
    "REL_WHEEL": REL_WHEEL,
    "REL_WHEEL_HI_RES": REL_WHEEL_HI_RES,
    "REL_HWHEEL_HI_RES": REL_HWHEEL_HI_RES,
    "ABS_X": ABS_X,
    "ABS_Y": ABS_Y,
    "ABS_MT_SLOT": ABS_MT_SLOT,
    "ABS_MT_TOUCH_MAJOR": ABS_MT_TOUCH_MAJOR,
    "ABS_MT_TOUCH_MINOR": ABS_MT_TOUCH_MINOR,
    "ABS_MT_ORIENTATION": ABS_MT_ORIENTATION,
    "ABS_MT_POSITION_X": ABS_MT_POSITION_X,
    "ABS_MT_POSITION_Y": ABS_MT_POSITION_Y,
    "ABS_MT_TOOL_TYPE": ABS_MT_TOOL_TYPE,
    "ABS_MT_TRACKING_ID": ABS_MT_TRACKING_ID,
    "ABS_MT_PRESSURE": ABS_MT_PRESSURE,
}
TYPE_NAME_TO_CODE = {name: code for code, name in EVENT_NAMES.items()}


def event_name(ev_type, code):
    return CODE_NAMES.get((ev_type, code), EVENT_NAMES.get(ev_type, str(ev_type)))


# --- ioctl encoding (asm-generic: x86_64, aarch64) ------------------------------

_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2

_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(dir_, type_, nr, size):
    return (dir_ << _IOC_DIRSHIFT) | (ord(type_) << _IOC_TYPESHIFT) | (nr << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT)


def _IO(type_, nr):
    return _IOC(_IOC_NONE, type_, nr, 0)


def _IOR(type_, nr, size):
    return _IOC(_IOC_READ, type_, nr, size)


def _IOW(type_, nr, size):
    return _IOC(_IOC_WRITE, type_, nr, size)


EVIOCGVERSION = _IOR("E", 0x01, 4)
EVIOCGID = _IOR("E", 0x02, 8)


def EVIOCGNAME(length):
    return _IOC(_IOC_READ, "E", 0x06, length)


def EVIOCGBIT(ev_type, length):
    return _IOC(_IOC_READ, "E", 0x20 + ev_type, length)


def EVIOCGABS(abs_code):
    return _IOR("E", 0x40 + abs_code, 24)


EVIOCGRAB = _IOW("E", 0x90, 4)

UI_DEV_CREATE = _IO("U", 1)
UI_DEV_DESTROY = _IO("U", 2)
UI_DEV_SETUP = _IOW("U", 3, struct.calcsize("HHHH80sI"))
UI_SET_EVBIT = _IOW("U", 100, 4)
UI_SET_KEYBIT = _IOW("U", 101, 4)
UI_SET_RELBIT = _IOW("U", 102, 4)
UI_SET_ABSBIT = _IOW("U", 103, 4)

# --- structs -------------------------------------------------------------------

EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)
INPUT_ID_FMT = "HHHH"
UINPUT_SETUP_FMT = "HHHH80sI"
ABSINFO_FMT = "iiiiii"

BUS_USB = 0x03
BUS_BLUETOOTH = 0x05
BUS_VIRTUAL = 0x06

INPUT_DIR = "/dev/input"
UINPUT_PATH = "/dev/uinput"
VIRTUAL_MOUSE_NAME = "Omarchy Magic Mouse"


# namedtuple, not a dataclass: one InputEvent is allocated per kernel event in
# the hot path and this is measurably cheaper (see docs/PERFORMANCE.md)
InputEvent = namedtuple("InputEvent", ("type", "code", "value", "time"), defaults=(0.0,))


@dataclass(frozen=True)
class AbsInfo:
    value: int = 0
    minimum: int = 0
    maximum: int = 0
    fuzz: int = 0
    flat: int = 0
    resolution: int = 0  # units per mm; 0 when the device does not report it


@dataclass(frozen=True)
class DeviceInfo:
    path: str
    name: str
    bustype: int
    vendor: int
    product: int
    version: int
    ev_bits: frozenset = field(default_factory=frozenset)
    rel_bits: frozenset = field(default_factory=frozenset)
    key_bits: frozenset = field(default_factory=frozenset)
    abs_bits: dict = field(default_factory=dict)


def _bitmask(buf, count):
    bits = set()
    for index in range(count):
        byte, bit = divmod(index, 8)
        if byte < len(buf) and buf[byte] & (1 << bit):
            bits.add(index)
    return bits


def _read_bits(fd, ev_type, count):
    buf = fcntl.ioctl(fd, EVIOCGBIT(ev_type, (count + 7) // 8), b"\0" * ((count + 7) // 8))
    return _bitmask(buf, count)


def query_device(path):
    """Return a :class:`DeviceInfo` for ``path``, or ``None`` if unreadable."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        bustype, vendor, product, version = struct.unpack(
            INPUT_ID_FMT, fcntl.ioctl(fd, EVIOCGID, bytes(8))
        )
        name = fcntl.ioctl(fd, EVIOCGNAME(256), bytes(256)).split(b"\0", 1)[0].decode("utf-8", "replace")
        ev_bits = _read_bits(fd, EV_SYN, EV_MAX + 1)
        rel_bits = _read_bits(fd, EV_REL, REL_MAX + 1) if EV_REL in ev_bits else set()
        key_bits = _read_bits(fd, EV_KEY, KEY_MAX + 1) if EV_KEY in ev_bits else set()
        abs_bits = {}
        if EV_ABS in ev_bits:
            for code in _read_bits(fd, EV_ABS, ABS_MAX + 1):
                raw = fcntl.ioctl(fd, EVIOCGABS(code), bytes(24))
                abs_bits[code] = AbsInfo(*struct.unpack(ABSINFO_FMT, raw))
        return DeviceInfo(
            path=path,
            name=name,
            bustype=bustype,
            vendor=vendor,
            product=product,
            version=version,
            ev_bits=frozenset(ev_bits),
            rel_bits=frozenset(rel_bits),
            key_bits=frozenset(key_bits),
            abs_bits=abs_bits,
        )
    except OSError:
        return None
    finally:
        os.close(fd)


def list_device_paths():
    return sorted(glob.glob(os.path.join(INPUT_DIR, "event*")))


def list_devices():
    infos = []
    for path in list_device_paths():
        info = query_device(path)
        if info is not None:
            infos.append(info)
    return infos


class Device:
    """An open evdev node, optionally grabbed away from the compositor."""

    def __init__(self, info):
        self.info = info
        self.fd = os.open(info.path, os.O_RDONLY | os.O_NONBLOCK)
        self._grabbed = False

    def grab(self):
        fcntl.ioctl(self.fd, EVIOCGRAB, struct.pack("i", 1))
        self._grabbed = True

    def read_events(self):
        """Return all pending events; empty when nothing is available.

        A zero-length read means the evdev node is gone (device removed):
        raise so the service drops the device instead of idling on it.
        """
        try:
            data = os.read(self.fd, EVENT_SIZE * 256)
        except BlockingIOError:
            return []
        if not data:
            raise OSError(errno.ENODEV, "input device disconnected")
        return [
            InputEvent(ev_type, code, value, sec + usec / 1_000_000)
            for sec, usec, ev_type, code, value in struct.iter_unpack(EVENT_FMT, data)
        ]

    def fileno(self):
        return self.fd

    def close(self):
        if self._grabbed:
            try:
                fcntl.ioctl(self.fd, EVIOCGRAB, struct.pack("i", 0))
            except OSError:
                pass
            self._grabbed = False
        try:
            os.close(self.fd)
        except OSError:
            pass


class ScrollAccumulator:
    """Turns hi-res scroll deltas into paired legacy + hi-res REL events.

    Kernel contract (Documentation/input/event-codes.rst): 120 hi-res units
    per detent; ``REL_WHEEL`` keeps a running detent count consistent with the
    ``REL_WHEEL_HI_RES`` stream.  Fractional deltas are carried over.
    """

    def __init__(self):
        self._acc_x = 0.0
        self._acc_y = 0.0
        self._emitted_x = 0
        self._emitted_y = 0
        self._legacy_x = 0
        self._legacy_y = 0

    def add(self, hr_x, hr_y):
        """Return ``[(code, value), ...]`` relative events to emit."""
        self._acc_x += hr_x
        self._acc_y += hr_y
        out_x = int(self._acc_x)
        out_y = int(self._acc_y)
        self._acc_x -= out_x
        self._acc_y -= out_y
        events = []
        if out_x:
            self._emitted_x += out_x
            det_x = int(self._emitted_x / 120) - self._legacy_x
            if det_x:
                self._legacy_x += det_x
                events.append((REL_HWHEEL, det_x))
            events.append((REL_HWHEEL_HI_RES, out_x))
        if out_y:
            self._emitted_y += out_y
            det_y = int(self._emitted_y / 120) - self._legacy_y
            if det_y:
                self._legacy_y += det_y
                events.append((REL_WHEEL, det_y))
            events.append((REL_WHEEL_HI_RES, out_y))
        return events


class UInputMouse:
    """A virtual mouse on ``/dev/uinput`` with hi-res wheel support.

    Scroll output pairs ``REL_WHEEL``/``REL_HWHEEL`` with their hi-res
    companions the way the kernel documents (120 hi-res units per detent).
    """

    def __init__(self, name=VIRTUAL_MOUSE_NAME, clock=None):
        self._clock = clock
        self.fd = os.open(UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)
        self._scroll = ScrollAccumulator()
        self._out = bytearray()
        # UI_SET_*BIT takes the bit number by value, not as a pointer.
        for ev_bit, code in [
            (UI_SET_EVBIT, EV_KEY),
            (UI_SET_EVBIT, EV_REL),
            (UI_SET_EVBIT, EV_SYN),
        ]:
            fcntl.ioctl(self.fd, ev_bit, code)
        for code in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        for code in (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES):
            fcntl.ioctl(self.fd, UI_SET_RELBIT, code)
        setup = struct.pack(UINPUT_SETUP_FMT, BUS_VIRTUAL, 0x0001, 0x0001, 1, name.encode(), 0)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE, 0)

    def write(self, ev_type, code, value, time=None):
        """Buffer one event; flush() delivers the batch in a single write."""
        if time is None:
            time = self._clock() if self._clock else 0.0
        sec = int(time)
        usec = int((time - sec) * 1_000_000)
        self._out += struct.pack(EVENT_FMT, sec, usec, ev_type, code, value)

    def flush(self):
        if self._out:
            os.write(self.fd, self._out)
            self._out.clear()

    def write_event(self, event):
        self.write(event.type, event.code, event.value, event.time or None)

    def scroll(self, hr_x, hr_y):
        """Emit scroll in hi-res units (120 = one detent), paired with legacy."""
        events = self._scroll.add(hr_x, hr_y)
        if not events:
            return
        for code, value in events:
            self.write(EV_REL, code, value)
        self.write(EV_SYN, SYN_REPORT, 0)

    def sync(self):
        self.write(EV_SYN, SYN_REPORT, 0)
        self.flush()

    def close(self):
        try:
            self.flush()
        except OSError:
            pass
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
