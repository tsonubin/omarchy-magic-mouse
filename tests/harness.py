"""In-process integration harness: the real service loop, fake kernel endpoints.

See docs/TEST_HARNESS.md.  Only transport endpoints are faked — the select
loop, pipeline, engines and config stay real.  Sibling of the docker rig
(test/e2e/), for environments without /dev access or root.
"""

import dataclasses
import os
import signal
import struct
import threading
import time
from unittest import mock

from magicmouse import evdev
from magicmouse.pipeline import KIND_TOUCH_MOUSE, synthetic_info


def fake_info(path="/dev/input/event-fake", product=0x0269):
    """A real-hardware-looking device the daemon will manage (never BUS_VIRTUAL)."""
    return dataclasses.replace(
        synthetic_info(KIND_TOUCH_MOUSE),
        path=path,
        product=product,
        bustype=evdev.BUS_BLUETOOTH,
    )


class FakeKernelDevice:
    """``evdev.Device`` stand-in backed by a pipe of real ``input_event`` bytes.

    ``read_events`` is the real parser: the event framing and the
    EOF -> ENODEV disconnect contract are exercised, not reimplemented.
    Closing ``write_fd`` simulates an unplug.
    """

    read_events = evdev.Device.read_events

    def __init__(self, info):
        self.info = info
        self.grabbed = False
        self.closed = False
        self.fd, self.write_fd = os.pipe()
        os.set_blocking(self.fd, False)

    def grab(self):
        self.grabbed = True

    def fileno(self):
        return self.fd

    def emit(self, *events):
        blob = b"".join(
            struct.pack(evdev.EVENT_FMT, 0, 0, event.type, event.code, event.value)
            for event in events
        )
        os.write(self.write_fd, blob)

    def close(self):
        for fd in (self.fd, self.write_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        self.closed = True


class ServiceRunner:
    """Runs ``Service.run_forever`` on a thread and drives its real signal paths.

    ``signal.signal`` cannot be called off the main thread, so the harness
    captures the handlers the service registers: ``stop()`` then invokes the
    real SIGTERM handler, i.e. the loop's own shutdown path.
    """

    def __init__(self, service):
        self.service = service
        self.handlers = {}
        self.error = None
        self._thread = None

    def start(self, timeout=5.0):
        def capture(sig, handler):
            self.handlers[sig] = handler

        def run():
            try:
                with mock.patch("signal.signal", capture):
                    self.service.run_forever()
            except BaseException as error:  # surfaced by start()/stop()
                self.error = error

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and signal.SIGTERM not in self.handlers:
            if self.error is not None:
                break
            time.sleep(0.005)
        if self.error is not None:
            raise AssertionError(f"service loop failed to start: {self.error!r}")
        if signal.SIGTERM not in self.handlers:
            raise AssertionError("service loop did not register its signal handlers")

    def stop(self, timeout=5.0):
        handler = self.handlers.get(signal.SIGTERM)
        if handler is not None:
            handler(signal.SIGTERM, None)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise AssertionError("service loop did not stop")
        if self.error is not None:
            raise AssertionError(f"service loop failed: {self.error!r}")
