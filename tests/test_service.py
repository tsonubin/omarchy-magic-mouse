"""Device-churn contracts for the daemon: what must survive unplug/replug.

These guard observable behaviour (which devices the daemon manages and that
it releases dead ones), not internal call patterns.
"""

import dataclasses
import time
import unittest
from unittest import mock

from magicmouse import evdev
from magicmouse.config import default_config
from magicmouse.pipeline import KIND_TOUCH_MOUSE, synthetic_info
from magicmouse.service import Service
from magicmouse.sink import RecordingSink


class FakeDevice:
    def __init__(self, info):
        self.info = info
        self.closed = False

    def grab(self):
        pass

    def close(self):
        self.closed = True

    def fileno(self):
        return -1

    def read_events(self):
        return []


def info_at(path, product):
    return dataclasses.replace(synthetic_info(KIND_TOUCH_MOUSE), path=path, product=product)


class DeviceChurnTest(unittest.TestCase):
    def setUp(self):
        self.service = Service(
            default_config(), RecordingSink(), log=lambda m: None, clock=time.monotonic
        )
        self.created = []

    def scan_as(self, infos):
        def factory(info):
            device = FakeDevice(info)
            self.created.append(device)
            return device

        with mock.patch.object(evdev, "list_devices", return_value=list(infos)), mock.patch.object(
            evdev, "Device", side_effect=factory
        ):
            self.service.scan()

    def test_replaced_device_at_managed_path_is_reattached(self):
        self.scan_as([info_at("/dev/input/event9", 0x0269)])
        self.assertEqual([d.info.product for d, _ in self.service._devices.values()], [0x0269])

        self.scan_as([info_at("/dev/input/event9", 0x030D)])
        self.assertTrue(self.created[0].closed, "replaced device must be released")
        self.assertEqual(
            [d.info.product for d, _ in self.service._devices.values()],
            [0x030D],
            "the new device behind a recycled event number must be managed",
        )

    def test_vanished_device_is_dropped(self):
        self.scan_as([info_at("/dev/input/event9", 0x0269)])
        self.assertEqual(len(self.service.list_managed()), 1)

        self.scan_as([])
        self.assertEqual(self.service.list_managed(), [])
        self.assertTrue(self.created[0].closed)


if __name__ == "__main__":
    unittest.main()
