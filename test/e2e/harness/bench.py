"""Benchmark: latency and CPU per event through the real daemon.

Runs inside the emulated Omarchy container next to the E2E suite:

    test/e2e/run.sh bench

Method: a virtual device emits through uinput (like hid-magicmouse), the
daemon under test grabs and re-emits, and the harness stamps both sides with
the same monotonic clock.  CPU numbers come from /proc/<pid>/stat of the
daemon process only (utime+stime).
"""

import cProfile
import os
import pstats
import statistics
import struct
import sys
import time

from magicmouse import evdev
from magicmouse.config import default_config
from magicmouse.pipeline import DevicePipeline
from magicmouse.sink import RecordingSink

from harness import observe, virtdev
from harness.daemonctl import Daemon

SC_CLK_TCK = os.sysconf("SC_CLK_TCK")
INTERVAL = 1 / 60


def cpu_seconds(pid):
    with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
        tail = handle.read().rsplit(")", 1)[1].split()
    return (int(tail[11]) + int(tail[12])) / SC_CLK_TCK  # utime + stime


def percentile(values, pct):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]


def stats(deltas_ms):
    return (
        f"n={len(deltas_ms)}  mean={statistics.mean(deltas_ms):.2f}ms  "
        f"p50={percentile(deltas_ms, 50):.2f}ms  p95={percentile(deltas_ms, 95):.2f}ms  "
        f"max={max(deltas_ms):.2f}ms"
    )


def paced_pointer_events(src, rec, count, gap):
    """Send unique REL_X values at a fixed pace; return observed latencies."""
    sent = {}
    for seq in range(1, count + 1):
        now = time.monotonic()
        src.pointer(seq, 1)
        sent[seq] = now
        time.sleep(gap)
    time.sleep(0.5)
    deltas = []
    for (t, ev_type, code, value) in rec.events:
        if ev_type == evdev.EV_REL and code == evdev.REL_X and value in sent:
            deltas.append((t - sent[value]) * 1000)
    return deltas


def bench_latency():
    # floor: source -> observer, no daemon (harness noise)
    src = virtdev.magic_mouse()
    node = observe.ensure_node(virtdev.MAGIC_MOUSE_NAME)
    rec = observe.DeviceRecorder(node)
    floor = paced_pointer_events(src, rec, 100, 0.005)
    rec.stop()
    src.close()
    time.sleep(0.3)

    # through the daemon: source -> daemon -> sink observer
    src = virtdev.magic_mouse()
    observe.ensure_node(virtdev.MAGIC_MOUSE_NAME)
    daemon = Daemon(label="bench-lat")
    sink_node = observe.ensure_node("Omarchy Magic Mouse")
    daemon.wait_log("managing /dev/input")
    rec = observe.DeviceRecorder(sink_node)
    through = paced_pointer_events(src, rec, 100, 0.005)
    rec.stop()
    daemon.stop()
    src.close()
    return floor, through


def bench_cpu_pointer(frames=20000):
    src = virtdev.magic_mouse()
    observe.ensure_node(virtdev.MAGIC_MOUSE_NAME)
    daemon = Daemon(label="bench-cpu")
    observe.ensure_node("Omarchy Magic Mouse")
    daemon.wait_log("managing /dev/input")

    # prebuild the uinput stream: REL_X seq, REL_Y 1, SYN per frame
    chunk_frames = 1000
    events_per_frame = 3
    start_cpu = cpu_seconds(daemon.proc.pid)
    start = time.monotonic()
    sent = 0
    while sent < frames:
        blob = b""
        for seq in range(sent, min(sent + chunk_frames, frames)):
            stamp = int(time.monotonic())
            blob += struct.pack(
                evdev.EVENT_FMT, stamp, 0, evdev.EV_REL, evdev.REL_X, seq
            )
            blob += struct.pack(
                evdev.EVENT_FMT, stamp, 0, evdev.EV_REL, evdev.REL_Y, 1
            )
            blob += struct.pack(
                evdev.EVENT_FMT, stamp, 0, evdev.EV_SYN, evdev.SYN_REPORT, 0
            )
        os.write(src.fd, blob)
        sent += chunk_frames
    time.sleep(1.0)  # let the daemon drain
    wall = time.monotonic() - start
    cpu = cpu_seconds(daemon.proc.pid) - start_cpu
    daemon.stop()
    src.close()
    total_events = frames * events_per_frame
    return cpu, wall, total_events


def bench_cpu_touch(frames=5000):
    src = virtdev.magic_mouse()
    observe.ensure_node(virtdev.MAGIC_MOUSE_NAME)
    daemon = Daemon(label="bench-touch")
    observe.ensure_node("Omarchy Magic Mouse")
    daemon.wait_log("managing /dev/input")

    src.touch_down(0, 0, 0)
    start_cpu = cpu_seconds(daemon.proc.pid)
    start = time.monotonic()
    y = 0
    for _ in range(frames):
        y += 71  # 1mm per frame: the MT stream a real drag produces
        for chunk in (
            struct.pack(evdev.EVENT_FMT, 0, 0, evdev.EV_ABS, evdev.ABS_MT_POSITION_Y, y),
            struct.pack(evdev.EVENT_FMT, 0, 0, evdev.EV_SYN, evdev.SYN_REPORT, 0),
        ):
            os.write(src.fd, chunk)
    time.sleep(1.0)
    wall = time.monotonic() - start
    cpu = cpu_seconds(daemon.proc.pid) - start_cpu
    src.touch_up(0)
    time.sleep(0.2)
    daemon.stop()
    src.close()
    return cpu, wall, frames * 2


def bench_idle(seconds=5.0):
    src = virtdev.magic_mouse()
    observe.ensure_node(virtdev.MAGIC_MOUSE_NAME)
    daemon = Daemon(label="bench-idle")
    observe.ensure_node("Omarchy Magic Mouse")
    daemon.wait_log("managing /dev/input")
    start_cpu = cpu_seconds(daemon.proc.pid)
    time.sleep(seconds)
    cpu = cpu_seconds(daemon.proc.pid) - start_cpu
    daemon.stop()
    src.close()
    return cpu, seconds


def bench_momentum():
    src = virtdev.magic_mouse()
    observe.ensure_node(virtdev.MAGIC_MOUSE_NAME)
    daemon = Daemon(label="bench-mom")
    observe.ensure_node("Omarchy Magic Mouse")
    daemon.wait_log("managing /dev/input")
    src.touch_down(0, 0, 0)
    for index in range(1, 7):
        time.sleep(0.008)
        src.touch_move(0, 0, index * 71)
    time.sleep(0.008)
    src.touch_up(0)  # inertia starts here (60Hz ticking)
    start_cpu = cpu_seconds(daemon.proc.pid)
    time.sleep(2.0)
    cpu = cpu_seconds(daemon.proc.pid) - start_cpu
    daemon.stop()
    src.close()
    return cpu


def bench_profile(repeats=20000):
    """Pure-pipeline profile: the code the daemon runs per event, no kernel."""
    cfg = default_config()
    sink = RecordingSink()

    class NoDispatch:
        def invoke(self, spec):
            pass

    from magicmouse.pipeline import synthetic_info

    clock = lambda: 0.0  # noqa: E731
    pipeline = DevicePipeline(synthetic_info("apple_touch_mouse"), cfg, sink, NoDispatch(), clock)
    frames = []
    for index in range(repeats):
        frames.append((evdev.EV_REL, evdev.REL_X, index & 0xFFFF))
        frames.append((evdev.EV_REL, evdev.REL_Y, 1))
        frames.append((evdev.EV_SYN, evdev.SYN_REPORT, 0))

    def run():
        for ev_type, code, value in frames:
            pipeline.process(evdev.InputEvent(ev_type, code, value))

    start = time.perf_counter()  # uninstrumented: the honest per-event cost
    run()
    raw = time.perf_counter() - start

    profiler = cProfile.Profile()
    profiler.enable()
    run()
    profiler.disable()
    return profiler, len(frames), raw


def main():
    print("omarchy-magic-mouse benchmark")
    print(f"kernel: {os.uname().release} ({os.uname().machine})")
    with open("/proc/cpuinfo", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("model name"):
                print(f"cpu:    {line.split(':', 1)[1].strip()}")
                break

    floor, through = bench_latency()
    print("\n-- latency: pointer passthrough (100 events paced at 5ms) --")
    print(f"  harness floor (no daemon): {stats(floor)}")
    print(f"  through the daemon:        {stats(through)}")

    cpu, wall, events = bench_cpu_pointer()
    print("\n-- cpu: pointer burst --")
    print(f"  {events} events in {wall:.2f}s = {events / wall:,.0f} events/s wall")
    print(f"  daemon cpu {cpu * 1000:.0f}ms = {cpu / events * 1e6:.1f}us per event")

    cpu, wall, events = bench_cpu_touch()
    print("\n-- cpu: touch-frame burst --")
    print(f"  {events} events in {wall:.2f}s = {events / wall:,.0f} events/s wall")
    print(f"  daemon cpu {cpu * 1000:.0f}ms = {cpu / events * 1e6:.1f}us per event")

    cpu, seconds = bench_idle()
    print("\n-- cpu: idle --")
    print(f"  {cpu * 1000:.1f}ms cpu over {seconds:.0f}s of no input ({cpu / seconds * 100:.2f}% )")

    cpu = bench_momentum()
    print("\n-- cpu: inertial scrolling (60Hz ticking) --")
    print(f"  {cpu * 1000:.1f}ms cpu over 2.0s of momentum ({cpu / 2.0 * 100:.2f}% )")

    profiler, count, raw = bench_profile()
    print(f"\n-- pure pipeline: {count} synthetic events --")
    print(f"  uninstrumented: {raw:.3f}s = {raw / count * 1e6:.2f}us per event")
    print("  profile (top cumtime):")
    pstats.Stats(profiler, stream=sys.stdout).strip_dirs().sort_stats("cumulative").print_stats(12)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
