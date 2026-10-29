import unittest

from magicmouse.config import default_config
from magicmouse.touch import Gesture, ScrollDelta, ScrollEnd, Tap, TouchEngine

from tests.helpers import RES_X, RES_Y, TouchBuilder, feed


def make_engine(fingers=2, enabled=True):
    cfg = default_config()["gestures"]
    cfg["fingers"] = fingers
    cfg["enabled"] = enabled
    return TouchEngine(cfg, RES_X, RES_Y)


class ScrollSessionTest(unittest.TestCase):
    def test_one_finger_drag_scrolls_then_ends(self):
        engine = make_engine()
        b = TouchBuilder()
        events = feed(engine, b.down(0, 0, 0) + b.syn(), 0.0)
        self.assertEqual(events, [])

        events = feed(engine, b.move(0, 0, 3) + b.syn(), 0.008)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ScrollDelta)
        self.assertAlmostEqual(events[0].dy_mm, 3.0, places=1)

        events = feed(engine, b.move(0, 0, 5) + b.syn(), 0.016)
        self.assertAlmostEqual(events[0].dy_mm, 2.0, places=1)

        events = feed(engine, b.up(0) + b.syn(), 0.024)
        self.assertEqual(events, [ScrollEnd()])

    def test_sub_slop_wiggle_does_not_scroll(self):
        engine = make_engine()
        b = TouchBuilder()
        feed(engine, b.down(0, 0, 0) + b.syn(), 0.0)
        events = feed(engine, b.move(0, 0.5, 0.5) + b.syn(), 0.008)
        self.assertEqual(events, [])
        events = feed(engine, b.up(0) + b.syn(), 0.016)
        self.assertEqual(events, [Tap()])  # stationary short touch = tap

    def test_kernel_coalesced_stream_still_scrolls(self):
        # the kernel input core drops unchanged ABS values: a touch down at
        # (0, 0) and moves that leave X untouched arrive as sparse events,
        # and the engine must still see the finger
        from magicmouse import evdev as e

        engine = make_engine()
        engine.abs_event(e.ABS_MT_SLOT, 0)  # kernel would drop this (value 0)
        engine.abs_event(e.ABS_MT_TRACKING_ID, 1)
        # POSITION_X/Y == 0 are dropped entirely (unchanged from abs state)
        self.assertEqual(engine.frame(0.0), [])
        engine.abs_event(e.ABS_MT_POSITION_Y, 213)  # X unchanged -> omitted
        events = engine.frame(0.008)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ScrollDelta)
        self.assertAlmostEqual(events[0].dy_mm, 3.0, places=2)
        up = [
            e.InputEvent(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1),
            e.InputEvent(e.EV_SYN, e.SYN_REPORT, 0),
        ]
        events = feed(engine, up, 0.016)
        self.assertEqual(events, [ScrollEnd()])


class TapTest(unittest.TestCase):
    def test_double_tap(self):
        engine = make_engine()
        b = TouchBuilder()
        feed(engine, b.down(0, 0, 0) + b.syn(), 0.0)
        events = feed(engine, b.up(0) + b.syn(), 0.1)
        self.assertEqual(events, [Tap()])

        feed(engine, b.down(0, 0, 0) + b.syn(), 0.2)
        events = feed(engine, b.up(0) + b.syn(), 0.3)
        self.assertEqual(events, [Gesture("double_tap")])

    def test_taps_far_apart_do_not_double(self):
        engine = make_engine()
        b = TouchBuilder()
        feed(engine, b.down(0, 0, 0) + b.syn(), 0.0)
        self.assertEqual(feed(engine, b.up(0) + b.syn(), 0.1), [Tap()])
        feed(engine, b.down(0, 20, 20) + b.syn(), 0.2)
        self.assertEqual(feed(engine, b.up(0) + b.syn(), 0.3), [Tap()])

    def test_two_finger_contact_is_not_a_tap(self):
        engine = make_engine()
        b = TouchBuilder()
        feed(engine, b.down(0, 0, 0) + b.syn(), 0.0)
        feed(engine, b.down(1, 0, 2) + b.syn(), 0.008)
        events = feed(engine, b.up(0) + b.up(1) + b.syn(), 0.1)
        self.assertEqual(events, [])


class SwipeTest(unittest.TestCase):
    def _swipe(self, engine, b, from_xy, to_xy, t0, t_step=0.008):
        feed(engine, b.down(0, *from_xy) + b.syn(), t0)
        feed(engine, b.down(1, from_xy[0], from_xy[1] + 2) + b.syn(), t0 + t_step)
        events = feed(
            engine,
            b.move(0, *to_xy) + b.move(1, to_xy[0], to_xy[1] + 2) + b.syn(),
            t0 + 2 * t_step,
        )
        return events

    def test_horizontal_swipe_fires_once(self):
        engine = make_engine()
        b = TouchBuilder()
        events = self._swipe(engine, b, (0, 0), (-6, 0), 0.0)
        self.assertEqual(events, [Gesture("swipe_left")])
        # continuing the same gesture must not re-fire
        events = feed(engine, b.move(0, -12, 0) + b.move(1, -12, 2) + b.syn(), 0.032)
        self.assertEqual(events, [])
        events = feed(engine, b.up(0) + b.up(1) + b.syn(), 0.04)
        self.assertEqual(events, [])

    def test_vertical_swipe_up(self):
        engine = make_engine()
        b = TouchBuilder()
        events = self._swipe(engine, b, (0, 0), (0, -6), 0.0)
        self.assertEqual(events, [Gesture("swipe_up")])

    def test_diagonal_motion_is_rejected(self):
        engine = make_engine()
        b = TouchBuilder()
        events = self._swipe(engine, b, (0, 0), (-5, -5), 0.0)
        self.assertEqual(events, [])
        events = feed(engine, b.up(0) + b.up(1) + b.syn(), 0.04)
        self.assertEqual(events, [])

    def test_slow_swipe_is_rejected(self):
        engine = make_engine()
        b = TouchBuilder()
        events = self._swipe(engine, b, (0, 0), (-6, 0), 0.0)
        self.assertEqual(events, [Gesture("swipe_left")])

        # a new gesture that takes longer than max_duration must not fire
        feed(engine, b.down(0, 0, 0) + b.syn(), 1.0)
        feed(engine, b.down(1, 0, 2) + b.syn(), 1.008)
        events = feed(engine, b.move(0, -6, 0) + b.move(1, -6, 2) + b.syn(), 1.6)
        self.assertEqual(events, [])

    def test_extra_fingers_abort(self):
        engine = make_engine(fingers=2)
        b = TouchBuilder()
        feed(engine, b.down(0, 0, 0) + b.syn(), 0.0)
        feed(engine, b.down(1, 0, 2) + b.syn(), 0.008)
        feed(engine, b.down(2, 0, 4) + b.syn(), 0.016)
        events = feed(engine, b.move(0, -6, 0) + b.move(1, -6, 2) + b.move(2, -6, 4) + b.syn(), 0.024)
        self.assertEqual(events, [])
        events = feed(engine, b.up(0) + b.up(1) + b.up(2) + b.syn(), 0.032)
        self.assertEqual(events, [])

    def test_three_finger_gestures(self):
        engine = make_engine(fingers=3)
        b = TouchBuilder()
        feed(engine, b.down(0, 0, 0) + b.syn(), 0.0)
        feed(engine, b.down(1, 0, 2) + b.syn(), 0.008)
        # two fingers cannot start a three-finger gesture
        events = feed(engine, b.move(0, -2, 0) + b.move(1, -2, 2) + b.syn(), 0.016)
        self.assertEqual(events, [])
        feed(engine, b.down(2, -2, 4) + b.syn(), 0.024)
        events = feed(
            engine,
            b.move(0, -8, 0) + b.move(1, -8, 2) + b.move(2, -8, 4) + b.syn(),
            0.032,
        )
        self.assertEqual(events, [Gesture("swipe_left")])


class ScrollToGestureTest(unittest.TestCase):
    def test_small_scroll_converts_to_gesture(self):
        engine = make_engine()
        b = TouchBuilder()
        feed(engine, b.down(0, 0, 0) + b.syn(), 0.0)
        events = feed(engine, b.move(0, 0, 2) + b.syn(), 0.008)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ScrollDelta)

        feed(engine, b.down(1, 0, 2) + b.syn(), 0.016)
        events = feed(engine, b.move(0, -6, 2) + b.move(1, -6, 2) + b.syn(), 0.024)
        self.assertEqual(events, [Gesture("swipe_left")])

    def test_real_scroll_aborts_on_second_finger(self):
        engine = make_engine()
        b = TouchBuilder()
        feed(engine, b.down(0, 0, 0) + b.syn(), 0.0)
        events = feed(engine, b.move(0, 0, 5) + b.syn(), 0.008)
        self.assertEqual(len(events), 1)

        feed(engine, b.down(1, 0, 5) + b.syn(), 0.016)
        events = feed(engine, b.move(0, -6, 5) + b.move(1, -6, 5) + b.syn(), 0.024)
        self.assertEqual(events, [])
        events = feed(engine, b.up(0) + b.up(1) + b.syn(), 0.032)
        self.assertEqual(events, [])


class DisabledGesturesTest(unittest.TestCase):
    def test_drag_still_scrolls_but_no_swipes_or_taps(self):
        engine = make_engine(enabled=False)
        b = TouchBuilder()
        feed(engine, b.down(0, 0, 0) + b.syn(), 0.0)
        events = feed(engine, b.move(0, 0, 3) + b.syn(), 0.008)
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ScrollDelta)
        events = feed(engine, b.up(0) + b.syn(), 0.016)
        self.assertEqual(events, [ScrollEnd()])

        feed(engine, b.down(0, 0, 0) + b.syn(), 0.1)
        feed(engine, b.down(1, 0, 2) + b.syn(), 0.108)
        events = feed(engine, b.move(0, -6, 0) + b.move(1, -6, 2) + b.syn(), 0.116)
        self.assertEqual(events, [])
        feed(engine, b.up(0) + b.up(1) + b.syn(), 0.124)

        feed(engine, b.down(0, 0, 0) + b.syn(), 0.2)
        events = feed(engine, b.up(0) + b.syn(), 0.25)
        self.assertEqual(events, [])  # tap is a gesture too


if __name__ == "__main__":
    unittest.main()
