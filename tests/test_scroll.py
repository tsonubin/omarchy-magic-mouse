import math
import unittest

from magicmouse.config import default_config
from magicmouse.scrolling import HI_RES_PER_DETENT, ScrollEngine


def make_engine(natural=True, inertia=True, smooth=True, emit=None):
    cfg = default_config()
    cfg["scroll"]["natural"] = natural
    cfg["scroll"]["smooth"]["enabled"] = smooth
    cfg["scroll"]["inertia"]["enabled"] = inertia
    cfg["scroll"]["touch"]["detents_per_mm"] = 1.0
    out = emit if emit is not None else []
    engine = ScrollEngine(cfg["scroll"], (lambda x, y: out.append((x, y))) if isinstance(out, list) else out)
    return engine, out


class TouchScrollTest(unittest.TestCase):
    def test_natural_signs_track_the_fingers(self):
        engine, out = make_engine(natural=True)
        engine.touch_scroll(0.0, dx_mm=1.0, dy_mm=1.0)  # fingers right + down
        self.assertEqual(out, [(-120.0, 120.0)])

    def test_classic_signs_are_flipped(self):
        engine, out = make_engine(natural=False)
        engine.touch_scroll(0.0, dx_mm=1.0, dy_mm=1.0)
        self.assertEqual(out, [(120.0, -120.0)])

    def test_disabled_touch_scroll_emits_nothing(self):
        engine, out = make_engine()
        engine.touch_enabled = False
        engine.touch_scroll(0.0, 0.0, 1.0)
        self.assertEqual(out, [])


class MomentumTest(unittest.TestCase):
    def _fling(self, engine, now, frames=10, step_s=1 / 60, dy_mm=1.0):
        for index in range(frames):
            engine.touch_scroll(now + index * step_s, 0.0, dy_mm)

    def test_momentum_continues_then_stops(self):
        engine, out = make_engine()
        self._fling(engine, 0.0)
        engine.end_touch(10 / 60)
        before_lift = len(out)
        self.assertTrue(engine.is_active())

        now = 10 / 60
        deltas = []
        while engine.is_active() and now < 5.0:
            now += 1 / 60
            engine.tick(now)
            if len(out) > len(deltas) + before_lift:
                deltas.append(out[-1][1])

        self.assertTrue(deltas, "momentum produced no scroll after lift")
        self.assertLess(now, 1.5 + 10 / 60 + 1 / 60, "momentum outlived max_duration")
        # exponential decay: successive frame deltas shrink
        for earlier, later in zip(deltas, deltas[1:]):
            self.assertGreaterEqual(earlier, later)
        self.assertFalse(engine.is_active())
        total_after = sum(deltas)
        self.assertGreater(total_after, 0)

    def test_momentum_velocity_is_bounded_by_tau(self):
        engine, out = make_engine()
        self._fling(engine, 0.0, frames=8, dy_mm=2.0)
        engine.end_touch(8 / 60)
        self.assertIsNotNone(engine._momentum)
        v0 = math.hypot(*engine._momentum[:2])
        self.assertGreaterEqual(v0, engine._min_velocity)
        start = 8 / 60
        now = start
        total = 0.0
        while engine.is_active() and now < 5.0:
            now += 1 / 60
            before = len(out)
            engine.tick(now)
            if len(out) > before:
                total += out[-1][1]
        # integral of v0*exp(-t/tau) is v0*tau (detents) = v0*tau*120 hi-res
        expected = v0 * engine._tau * HI_RES_PER_DETENT
        self.assertLess(total, expected * 2)
        self.assertGreater(total, expected * 0.2)

    def test_new_input_cancels_momentum(self):
        engine, out = make_engine()
        self._fling(engine, 0.0)
        engine.end_touch(10 / 60)
        self.assertTrue(engine.is_active())
        engine.touch_scroll(11 / 60, 0.0, 0.1)
        self.assertFalse(engine.is_active())

    def test_cancel_stops_everything(self):
        engine, _ = make_engine()
        self._fling(engine, 0.0)
        engine.end_touch(10 / 60)
        engine.cancel()
        self.assertFalse(engine.is_active())

    def test_slow_lift_starts_no_momentum(self):
        engine, out = make_engine()
        engine.touch_scroll(0.0, 0.0, 0.5)  # single slow sample: velocity ~0
        engine.end_touch(1.0)
        self.assertFalse(engine.is_active())
        engine.tick(1.1)
        self.assertEqual(len(out), 1)

    def test_inertia_disabled(self):
        engine, out = make_engine(inertia=False)
        self._fling(engine, 0.0)
        engine.end_touch(10 / 60)
        self.assertFalse(engine.is_active())
        self.assertEqual(len(out), 10)


class SmoothWheelTest(unittest.TestCase):
    def test_one_detent_animates_to_exactly_120(self):
        engine, out = make_engine(natural=False, smooth=True)
        engine.wheel(0.0, 0, 1)
        self.assertEqual(out, [], "smooth mode must not emit the detent immediately")

        now = 0.0
        while engine.is_active() and now < 1.0:
            now += 1 / 60
            engine.tick(now)
        total = sum(delta for _, delta in out)
        self.assertAlmostEqual(total, HI_RES_PER_DETENT, places=6)
        self.assertTrue(all(delta > 0 for delta in (d for _, d in out)))

    def test_smooth_disabled_emits_immediately(self):
        engine, out = make_engine(natural=False, smooth=False)
        engine.wheel(0.0, 0, 1)
        self.assertEqual(out, [(0.0, 120.0)])
        self.assertFalse(engine.is_active())

    def test_wheel_natural_flips_sign(self):
        engine, out = make_engine(natural=True, smooth=False)
        engine.wheel(0.0, 0, 1)
        self.assertEqual(out, [(0.0, -120.0)])

    def test_second_burst_animates_again(self):
        engine, out = make_engine(natural=False, smooth=True)
        engine.wheel(0.0, 0, 1)
        now = 0.0
        while engine.is_active() and now < 10.0:
            now += 1 / 60
            engine.tick(now)

        before = len(out)
        engine.wheel(now + 0.1, 0, 1)
        now += 0.1
        while engine.is_active() and now < 10.0:
            now += 1 / 60
            engine.tick(now)
        emitted = out[before:]
        self.assertGreater(len(emitted), 1, "a later detent must animate, not jump")
        self.assertLess(
            max(delta for _, delta in emitted), 120, "a burst must not jump a whole detent at once"
        )

    def test_wheel_cancels_momentum(self):
        engine, out = make_engine(natural=False, smooth=False)
        engine.touch_scroll(0.0, 0.0, 1.0)
        engine.touch_scroll(1 / 60, 0.0, 1.0)  # two samples so velocity is real
        engine.end_touch(2 / 60)
        self.assertTrue(engine.is_active())
        engine.wheel(0.2, 0, 1)
        self.assertFalse(engine.is_active())


if __name__ == "__main__":
    unittest.main()
