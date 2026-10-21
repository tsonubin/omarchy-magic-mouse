import dataclasses
import struct
import unittest

from magicmouse import evdev
from magicmouse.config import default_config
from magicmouse.pipeline import (
    KIND_TOUCH_MOUSE,
    KIND_WHEEL_MOUSE,
    DevicePipeline,
    classify,
    should_manage,
    synthetic_info,
)
from magicmouse.sink import RecordingSink

from tests.helpers import FakeClock, TouchBuilder, feed


class FakeDispatcher:
    def __init__(self):
        self.calls = []

    def invoke(self, spec):
        self.calls.append(spec)


def make_pipeline(kind=KIND_TOUCH_MOUSE, natural=True):
    cfg = default_config()
    cfg["scroll"]["natural"] = natural
    sink = RecordingSink()
    dispatcher = FakeDispatcher()
    clock = FakeClock()
    pipeline = DevicePipeline(synthetic_info(kind), cfg, sink, dispatcher, clock)
    return pipeline, sink, dispatcher, clock


def run(pipeline, clock, events, at):
    clock.now = at
    for event in events:
        pipeline.process(event)


class ClassificationTest(unittest.TestCase):
    def test_synthetic_devices_classify_as_expected(self):
        hi_res_only = dataclasses.replace(
            synthetic_info(KIND_WHEEL_MOUSE),
            rel_bits=frozenset({evdev.REL_X, evdev.REL_Y, evdev.REL_WHEEL_HI_RES}),
        )
        cases = (
            (synthetic_info(KIND_TOUCH_MOUSE), KIND_TOUCH_MOUSE),
            (synthetic_info(KIND_WHEEL_MOUSE), KIND_WHEEL_MOUSE),
            (hi_res_only, KIND_WHEEL_MOUSE),  # wheels advertised hi-res only
        )
        for info, expected in cases:
            with self.subTest(device=info.name, rel=sorted(info.rel_bits)):
                self.assertEqual(classify(info), expected)

    def test_only_real_hardware_is_managed(self):
        wanted = {KIND_TOUCH_MOUSE, KIND_WHEEL_MOUSE}
        cases = (
            # real hardware reports USB/Bluetooth buses
            (dataclasses.replace(synthetic_info(KIND_TOUCH_MOUSE), bustype=evdev.BUS_BLUETOOTH), True),
            (dataclasses.replace(synthetic_info(KIND_TOUCH_MOUSE), name=evdev.VIRTUAL_MOUSE_NAME), False),
            (dataclasses.replace(synthetic_info(KIND_WHEEL_MOUSE), bustype=evdev.BUS_VIRTUAL), False),
        )
        for info, expected in cases:
            with self.subTest(device=info.name, bustype=hex(info.bustype)):
                self.assertEqual(should_manage(info, wanted), expected)


class TouchMousePipelineTest(unittest.TestCase):
    def test_pointer_and_buttons_pass_through(self):
        pipeline, sink, _, clock = make_pipeline()
        run(pipeline, clock, [evdev.InputEvent(evdev.EV_REL, evdev.REL_X, 5)], 0.0)
        run(pipeline, clock, [evdev.InputEvent(evdev.EV_KEY, evdev.BTN_LEFT, 1)], 0.1)
        self.assertIn((evdev.EV_REL, evdev.REL_X, 5), sink.writes)
        self.assertIn((evdev.EV_KEY, evdev.BTN_LEFT, 1), sink.writes)

    def test_frame_marker_is_forwarded(self):
        # without SYN_REPORT on the output stream the kernel input core
        # buffers events and no consumer ever sees them
        pipeline, sink, _, clock = make_pipeline()
        run(
            pipeline,
            clock,
            [
                evdev.InputEvent(evdev.EV_REL, evdev.REL_X, 5),
                evdev.InputEvent(evdev.EV_SYN, evdev.SYN_REPORT, 0),
            ],
            0.0,
        )
        self.assertEqual(
            sink.writes,
            [
                (evdev.EV_REL, evdev.REL_X, 5),
                (evdev.EV_SYN, evdev.SYN_REPORT, 0),
            ],
        )

    def test_kernel_wheel_emulation_is_dropped(self):
        pipeline, sink, _, clock = make_pipeline()
        run(
            pipeline,
            clock,
            [
                evdev.InputEvent(evdev.EV_REL, evdev.REL_WHEEL, 1),
                evdev.InputEvent(evdev.EV_REL, evdev.REL_WHEEL_HI_RES, 120),
                evdev.InputEvent(evdev.EV_SYN, evdev.SYN_REPORT, 0),
            ],
            0.0,
        )
        self.assertEqual(sink.scrolls, [])
        # scroll events are dropped, but the frame marker still passes through
        self.assertEqual(sink.writes, [(evdev.EV_SYN, evdev.SYN_REPORT, 0)])

    def test_touch_drag_scrolls_with_natural_sign(self):
        pipeline, sink, _, clock = make_pipeline(natural=True)
        b = TouchBuilder()
        run(pipeline, clock, b.down(0, 0, 0) + b.syn(), 0.0)
        run(pipeline, clock, b.move(0, 0, 3) + b.syn(), 0.008)
        self.assertEqual(len(sink.scrolls), 1)
        hr_x, hr_y = sink.scrolls[0]
        self.assertAlmostEqual(hr_x, 0.0)
        self.assertAlmostEqual(hr_y, 3 * 1.2 * 120)  # mm * detents/mm * hi-res

    def test_lift_starts_momentum_that_decays(self):
        pipeline, sink, _, clock = make_pipeline(natural=True)
        b = TouchBuilder()
        run(pipeline, clock, b.down(0, 0, 0) + b.syn(), 0.0)
        run(pipeline, clock, b.move(0, 0, 2) + b.syn(), 0.008)
        run(pipeline, clock, b.move(0, 0, 4) + b.syn(), 0.016)
        run(pipeline, clock, b.up(0) + b.syn(), 0.024)
        self.assertTrue(pipeline.needs_ticks())

        before = len(sink.scrolls)
        now = 0.024
        while pipeline.needs_ticks() and now < 5.0:
            now += 1 / 60
            pipeline.tick(now)
        self.assertGreater(len(sink.scrolls), before)
        self.assertFalse(pipeline.needs_ticks())

    def test_swipe_dispatches_mapped_action(self):
        pipeline, sink, dispatcher, clock = make_pipeline(natural=True)
        b = TouchBuilder()
        run(pipeline, clock, b.down(0, 0, 0) + b.syn(), 0.0)
        run(pipeline, clock, b.down(1, 0, 2) + b.syn(), 0.008)
        run(pipeline, clock, b.move(0, -6, 0) + b.move(1, -6, 2) + b.syn(), 0.016)
        self.assertEqual(len(dispatcher.calls), 1)
        self.assertEqual(dispatcher.calls[0]["name"], "workspace_next")
        self.assertEqual(sink.scrolls, [])

    def test_tap_dispatches_none_action(self):
        pipeline, _, dispatcher, clock = make_pipeline()
        b = TouchBuilder()
        run(pipeline, clock, b.down(0, 0, 0) + b.syn(), 0.0)
        run(pipeline, clock, b.up(0) + b.syn(), 0.1)
        self.assertEqual(dispatcher.calls, [{"name": "none", "command": None}])

    def test_button_press_cancels_momentum(self):
        pipeline, sink, _, clock = make_pipeline(natural=True)
        b = TouchBuilder()
        run(pipeline, clock, b.down(0, 0, 0) + b.syn(), 0.0)
        run(pipeline, clock, b.move(0, 0, 2) + b.syn(), 0.008)
        run(pipeline, clock, b.move(0, 0, 4) + b.syn(), 0.016)
        run(pipeline, clock, b.up(0) + b.syn(), 0.024)
        self.assertTrue(pipeline.needs_ticks())
        run(pipeline, clock, [evdev.InputEvent(evdev.EV_KEY, evdev.BTN_LEFT, 1)], 0.03)
        self.assertFalse(pipeline.needs_ticks())
        self.assertIn((evdev.EV_KEY, evdev.BTN_LEFT, 1), sink.writes)


class WheelMousePipelineTest(unittest.TestCase):
    def test_wheel_is_smoothed_to_exactly_one_detent(self):
        pipeline, sink, _, clock = make_pipeline(KIND_WHEEL_MOUSE, natural=False)
        run(pipeline, clock, [evdev.InputEvent(evdev.EV_REL, evdev.REL_WHEEL, 1)], 0.0)
        self.assertEqual(sink.scrolls, [], "smooth mode animates, it does not pass through")
        self.assertTrue(pipeline.needs_ticks())

        now = 0.0
        while pipeline.needs_ticks() and now < 1.0:
            now += 1 / 60
            pipeline.tick(now)
        total = sum(hr_y for _, hr_y in sink.scrolls)
        self.assertAlmostEqual(total, 120.0, places=6)

    def test_hi_res_companions_are_not_double_counted(self):
        pipeline, sink, _, clock = make_pipeline(KIND_WHEEL_MOUSE, natural=False)
        run(
            pipeline,
            clock,
            [
                evdev.InputEvent(evdev.EV_REL, evdev.REL_WHEEL, 1),
                evdev.InputEvent(evdev.EV_REL, evdev.REL_WHEEL_HI_RES, 120),
            ],
            0.0,
        )
        now = 0.0
        while pipeline.needs_ticks() and now < 1.0:
            now += 1 / 60
            pipeline.tick(now)
        total = sum(hr_y for _, hr_y in sink.scrolls)
        self.assertAlmostEqual(total, 120.0, places=6)

    def test_hi_res_only_wheel_scrolls(self):
        info = dataclasses.replace(
            synthetic_info(KIND_WHEEL_MOUSE),
            rel_bits=frozenset({evdev.REL_X, evdev.REL_Y, evdev.REL_WHEEL_HI_RES}),
        )
        cfg = default_config()
        cfg["scroll"]["natural"] = False
        sink, clock = RecordingSink(), FakeClock()
        pipeline = DevicePipeline(info, cfg, sink, FakeDispatcher(), clock)
        run(pipeline, clock, [evdev.InputEvent(evdev.EV_REL, evdev.REL_WHEEL_HI_RES, 60)] * 2, 0.0)
        now = 0.0
        while pipeline.needs_ticks() and now < 1.0:
            now += 1 / 60
            pipeline.tick(now)
        self.assertAlmostEqual(sum(hr_y for _, hr_y in sink.scrolls), 120.0, places=6)

    def test_horizontal_wheel(self):
        pipeline, sink, _, clock = make_pipeline(KIND_WHEEL_MOUSE, natural=False)
        run(pipeline, clock, [evdev.InputEvent(evdev.EV_REL, evdev.REL_HWHEEL, -1)], 0.0)
        now = 0.0
        while pipeline.needs_ticks() and now < 1.0:
            now += 1 / 60
            pipeline.tick(now)
        total = sum(hr_x for hr_x, _ in sink.scrolls)
        self.assertAlmostEqual(total, -120.0, places=6)


class FeedHelperTest(unittest.TestCase):
    def test_feed_routes_syn_frames(self):
        from magicmouse.touch import ScrollDelta, TouchEngine

        engine = TouchEngine(default_config()["gestures"], 26, 71)
        b = TouchBuilder()
        self.assertEqual(feed(engine, b.down(0, 0, 0) + b.syn(), 0.0), [])
        events = feed(engine, b.move(0, 0, 3) + b.syn(), 0.008)
        self.assertTrue(all(isinstance(e, ScrollDelta) for e in events))


if __name__ == "__main__":
    unittest.main()
