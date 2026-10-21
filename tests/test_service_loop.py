"""Smoke contract: events traverse the real service loop into the sink.

The loop, pipeline and config are real (tests/harness.py fakes only the
kernel endpoints); a regression in fd indexing, batching/flush points or the
pump path loses these events.
"""

import time
import unittest
from unittest import mock

from magicmouse import evdev
from magicmouse.config import default_config
from magicmouse.service import Service
from magicmouse.sink import RecordingSink

from tests.harness import FakeKernelDevice, ServiceRunner, fake_info


class ServiceLoopSmokeTest(unittest.TestCase):
    def test_injected_events_reach_the_sink_through_the_real_loop(self):
        sink = RecordingSink()
        service = Service(default_config(), sink, log=lambda m: None, clock=time.monotonic)
        device = FakeKernelDevice(fake_info())
        self.addCleanup(device.close)

        with mock.patch.object(evdev, "list_devices", return_value=[device.info]), mock.patch.object(
            evdev, "Device", side_effect=lambda info: device
        ):
            runner = ServiceRunner(service)
            runner.start()
            device.emit(
                evdev.InputEvent(evdev.EV_REL, evdev.REL_X, 7),
                evdev.InputEvent(evdev.EV_SYN, evdev.SYN_REPORT, 0),
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and (evdev.EV_REL, evdev.REL_X, 7) not in sink.writes:
                time.sleep(0.01)
            runner.stop()

        self.assertTrue(device.grabbed, "managed devices must be grabbed before use")
        self.assertIn((evdev.EV_REL, evdev.REL_X, 7), sink.writes, "pointer must pass through the loop")
        self.assertIn((evdev.EV_SYN, evdev.SYN_REPORT, 0), sink.writes, "frame markers must be forwarded")
        self.assertTrue(device.closed, "the clean stop path must release the device")


if __name__ == "__main__":
    unittest.main()
