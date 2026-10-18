"""Device discovery + observation for the E2E rig.

The container has no udev, and we deliberately do *not* expose the host's
real input devices: the rig mknods only the nodes it owns into its private
``/dev/input`` — the virtual Magic Mouse source and the daemon's virtual
mouse sink — so the daemon can never grab an unrelated device.
"""

import os
import re
import select
import struct
import threading
import time

SYS_INPUT = "/sys/class/input"
DEV_INPUT = "/dev/input"

_EVENT_FMT = "llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FMT)


def _sys_input_devices():
    """Yield ``(event_node_name, device_name, major, minor)`` from sysfs."""
    if not os.path.isdir(SYS_INPUT):
        return
    for entry in sorted(os.listdir(SYS_INPUT)):
        if not re.fullmatch(r"input\d+", entry):
            continue
        base = os.path.join(SYS_INPUT, entry)
        try:
            with open(os.path.join(base, "name"), encoding="utf-8") as handle:
                name = handle.read().strip()
            children = sorted(os.listdir(base))
        except OSError:
            continue  # the device disappeared mid-walk
        for child in children:
            match = re.fullmatch(r"event(\d+)", child)
            if not match:
                continue
            try:
                with open(os.path.join(base, child, "dev"), encoding="utf-8") as handle:
                    major, minor = (int(part) for part in handle.read().strip().split(":"))
            except (OSError, ValueError):
                continue
            yield child, name, major, minor


UDEV_DATA = "/run/udev/data"


def _ensure_udev_entry(path, major, minor):
    """Provide the udev metadata udev would give on a real system.

    libinput refuses devices the udev database has not tagged as input, and
    the rig's containers have no udev daemon.
    """
    os.makedirs(UDEV_DATA, exist_ok=True)
    with open(f"{UDEV_DATA}/c{major}:{minor}", "w", encoding="utf-8") as handle:
        handle.write(
            f"I:0\nE:MAJOR={major}\nE:MINOR={minor}\nE:DEVNAME={path}\n"
            "E:SUBSYSTEM=input\nE:ID_INPUT=1\nE:ID_INPUT_MOUSE=1\nE:ID_BUS=usb\n"
        )


def ensure_node(name, timeout=5.0):
    """mknod ``/dev/input/<event>`` for the kernel device called ``name``.

    Kernel event numbers get recycled across devices, so a node left over from
    an earlier device must be replaced when its major:minor no longer matches.
    """
    deadline = time.monotonic() + timeout
    while True:
        for node, device_name, major, minor in _sys_input_devices():
            if device_name != name:
                continue
            os.makedirs(DEV_INPUT, exist_ok=True)
            path = os.path.join(DEV_INPUT, node)
            want = os.makedev(major, minor)
            try:
                stale = os.stat(path).st_rdev != want
            except FileNotFoundError:
                stale = True
            if stale:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
                os.mknod(path, 0o600 | 0o020000, want)  # S_IFCHR
            _ensure_udev_entry(path, major, minor)
            # probe-open: a node whose backing device vanished yields ENODEV
            try:
                probe = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                os.close(probe)
            except OSError:
                continue
            return path
        if time.monotonic() > deadline:
            visible = sorted({name for _, name, _, _ in _sys_input_devices()})
            raise TimeoutError(
                f"kernel device {name!r} did not appear; visible devices: {visible}"
            )
        time.sleep(0.02)


class DeviceRecorder:
    """Records everything a consumer would read from an evdev node."""

    def __init__(self, path):
        self.path = path
        self.events = []  # (t, type, code, value)
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self.fd], [], [], 0.05)
            except OSError:
                return
            if not ready:
                continue
            try:
                data = os.read(self.fd, _EVENT_SIZE * 256)
            except BlockingIOError:
                continue
            except OSError:
                return
            for offset in range(0, len(data) - _EVENT_SIZE + 1, _EVENT_SIZE):
                _sec, _usec, ev_type, code, value = struct.unpack(
                    _EVENT_FMT, data[offset : offset + _EVENT_SIZE]
                )
                self.events.append((time.monotonic(), ev_type, code, value))

    def clear(self):
        self.events.clear()

    def wait_for(self, predicate, timeout=5.0):
        """Wait until ``predicate(events)`` holds; returns the matching events."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate(self.events)
            if result:
                return result
            time.sleep(0.01)
        return predicate(self.events)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=1)
        try:
            os.close(self.fd)
        except OSError:
            pass
