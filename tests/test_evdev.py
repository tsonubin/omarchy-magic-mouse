import struct
import unittest

from magicmouse import evdev


class EvdevAbiTest(unittest.TestCase):
    """The ioctl numbers and struct layout are kernel UAPI, not opinions."""

    def test_input_event_struct_size(self):
        if struct.calcsize("l") == 8:  # 64-bit userspace
            self.assertEqual(evdev.EVENT_SIZE, 24)
        self.assertEqual(struct.calcsize(evdev.EVENT_FMT), evdev.EVENT_SIZE)

    def test_ioctl_numbers_match_kernel_uapi(self):
        self.assertEqual(evdev.EVIOCGVERSION, 0x80044501)
        self.assertEqual(evdev.EVIOCGID, 0x80084502)
        self.assertEqual(evdev.EVIOCGRAB, 0x40044590)
        self.assertEqual(evdev.EVIOCGBIT(evdev.EV_REL, 2), 0x80024522)
        self.assertEqual(evdev.UI_DEV_CREATE, 0x5501)
        self.assertEqual(evdev.UI_DEV_DESTROY, 0x5502)

    def test_event_pack_roundtrip(self):
        raw = struct.pack(evdev.EVENT_FMT, 5, 250000, evdev.EV_REL, evdev.REL_X, -3)
        sec, usec, ev_type, code, value = struct.unpack(evdev.EVENT_FMT, raw)
        self.assertEqual((sec, usec, ev_type, code, value), (5, 250000, evdev.EV_REL, evdev.REL_X, -3))


class ScrollAccumulatorTest(unittest.TestCase):
    def test_hi_res_crossing_emits_one_detent(self):
        acc = evdev.ScrollAccumulator()
        self.assertEqual(acc.add(60, 0), [(evdev.REL_HWHEEL_HI_RES, 60)])
        self.assertEqual(
            acc.add(60, 0),
            [(evdev.REL_HWHEEL, 1), (evdev.REL_HWHEEL_HI_RES, 60)],
        )

    def test_fractional_deltas_carry_over(self):
        acc = evdev.ScrollAccumulator()
        events = []
        for _ in range(4):
            events.append(acc.add(0, 30.5))
        self.assertEqual(events[0], [(evdev.REL_WHEEL_HI_RES, 30)])
        # cumulative hi-res = 122 -> one legacy detent at the fourth frame
        self.assertEqual(events[3], [(evdev.REL_WHEEL, 1), (evdev.REL_WHEEL_HI_RES, 31)])

    def test_negative_crossing(self):
        acc = evdev.ScrollAccumulator()
        self.assertEqual(
            acc.add(0, -120),
            [(evdev.REL_WHEEL, -1), (evdev.REL_WHEEL_HI_RES, -120)],
        )

    def test_sub_unit_delta_emits_nothing(self):
        acc = evdev.ScrollAccumulator()
        self.assertEqual(acc.add(0.4, 0), [])


if __name__ == "__main__":
    unittest.main()
