"""Daemon: device discovery, grabbing, the event loop, and hotplug.

One shared virtual mouse aggregates all managed devices.  Managed devices are
grabbed (``EVIOCGRAB``) so the compositor never sees the superseded raw
stream; if the daemon dies the kernel releases every grab, so the hardware
reverts to stock behaviour instead of going dead.
"""

import os
import select
import signal
import socket
import sys
import time

from . import evdev
from .actions import ActionDispatcher
from .config import MANAGEABLE_KINDS
from .pipeline import DevicePipeline, classify, should_manage

RESCAN_INTERVAL = 2.0
TICK_INTERVAL = 1 / 60


def sd_notify(message):
    """Tell systemd about startup state (no-op outside a notify unit)."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            if address.startswith("@"):
                address = "\0" + address[1:]
            sock.connect(address)
            sock.send(message.encode())
    except OSError:
        pass


class Service:
    def __init__(self, cfg, sink, dispatcher=None, log=None, clock=time.monotonic):
        self.cfg = cfg
        self.sink = sink
        self.dispatcher = dispatcher or ActionDispatcher(log=log or _stderr)
        self.log = log or _stderr
        self.clock = clock
        self._devices = {}  # path -> (Device, DevicePipeline)
        self._stopping = False

    # --- device management ----------------------------------------------------

    def scan(self):
        wanted = set(self.cfg["devices"]["manage"]) & set(MANAGEABLE_KINDS)
        present = set()
        for info in evdev.list_devices():
            if not should_manage(info, wanted):
                continue
            kind = classify(info)
            present.add(info.path)
            if info.path in self._devices:
                continue
            try:
                device = evdev.Device(info)
                device.grab()
            except OSError as error:
                self.log(f"cannot manage {info.path} ({info.name}): {error}")
                continue
            pipeline = DevicePipeline(info, self.cfg, self.sink, self.dispatcher, self.clock)
            self._devices[info.path] = (device, pipeline)
            self.log(f"managing {info.path}: {info.name} [{kind}]")
        for path in list(self._devices):
            if path not in present:
                self._drop(path, "disappeared")

    def _drop(self, path, reason):
        device, _pipeline = self._devices.pop(path)
        device.close()
        self.log(f"stopped managing {path}: {reason}")

    def list_managed(self):
        return [
            (path, device.info, pipeline.kind)
            for path, (device, pipeline) in sorted(self._devices.items())
        ]

    # --- main loop -------------------------------------------------------------

    def run_forever(self):
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)  # action subprocesses reap

        self.scan()
        sd_notify("READY=1")
        last_scan = self.clock()
        try:
            while not self._stopping:
                now = self.clock()
                ticking = any(pipeline.needs_ticks() for _, pipeline in self._devices.values())
                timeout = (
                    TICK_INTERVAL
                    if ticking
                    else max(RESCAN_INTERVAL - (now - last_scan), 0.01)
                )
                fds = [device.fileno() for device, _ in self._devices.values()]
                try:
                    ready, _, _ = select.select(fds, [], [], timeout)
                except InterruptedError:
                    continue
                for fd in ready:
                    self._pump(fd)
                now = self.clock()
                for _, pipeline in self._devices.values():
                    pipeline.tick(now)
                if now - last_scan >= RESCAN_INTERVAL:
                    self.scan()
                    last_scan = now
        finally:
            sd_notify("STOPPING=1")
            for path in list(self._devices):
                self._drop(path, "shutdown")
            self.sink.close()

    def _pump(self, fd):
        for path, (device, pipeline) in list(self._devices.items()):
            if device.fileno() != fd:
                continue
            try:
                events = device.read_events()
            except OSError as error:
                self._drop(path, str(error))
                return
            for event in events:
                pipeline.process(event)
            return

    def _handle_stop(self, _signum, _frame):
        self._stopping = True


def _stderr(message):
    print(f"omarchy-magic-mouse: {message}", file=sys.stderr, flush=True)
