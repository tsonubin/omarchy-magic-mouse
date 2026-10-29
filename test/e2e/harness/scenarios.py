"""End-to-end scenarios: real kernel input core, real daemon, real compositor.

Every scenario drives a virtual device through ``/dev/uinput`` (the same API
the hid-magicmouse kernel driver uses), observes the daemon's virtual mouse
output on its evdev node, and asserts on real system state — Hyprland
workspaces via ``hyprctl``, Omarchy's menu surface via the compositor's
layer/client list, or the raw evdev stream a consumer like libinput reads.

Run: ``python3 -m harness.scenarios`` (see entrypoint.sh / run.sh).
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback

from magicmouse import evdev
from magicmouse.config import default_config

from harness import observe, virtdev
from harness.daemonctl import Daemon, HeadlessHyprland

SINK_NAME = "Omarchy Magic Mouse"  # the daemon's virtual mouse
MM = virtdev.MAGIC_MOUSE_NAME
WM = virtdev.WHEEL_MOUSE_NAME

RESULTS = []


class SkipScenario(Exception):
    """Raised when an optional environment piece is absent."""


def scenario(fn):
    RESULTS.append(fn)
    return fn


# --- helpers -----------------------------------------------------------------


def cfg_with(**sections):
    cfg = default_config()
    for path, value in sections.items():
        node = cfg
        keys = path.split("__")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return cfg


def rel_events(events, code):
    return [(t, v) for (t, ty, c, v) in events if ty == evdev.EV_REL and c == code]


def hi_res_sum(events, t_min=0.0):
    total = 0
    for code in (evdev.REL_WHEEL_HI_RES, evdev.REL_HWHEEL_HI_RES):
        total += sum(v for (t, v) in rel_events(events, code) if t >= t_min)
    return total


def legacy_sum(events, t_min=0.0):
    total = 0
    for code in (evdev.REL_WHEEL, evdev.REL_HWHEEL):
        total += sum(v for (t, v) in rel_events(events, code) if t >= t_min)
    return total


def mm_drag(src, frames, step_units=71, frame_s=0.008):
    """One-finger vertical drag (default: 1mm per frame at ~120Hz)."""
    src.touch_down(0, 0, 0)
    for index in range(1, frames + 1):
        time.sleep(frame_s)
        src.touch_move(0, 0, index * step_units)
    time.sleep(frame_s)
    src.touch_up(0)


def mm_swipe_left(src, frames=3, step_units=52, frame_s=0.008):
    """Two-finger horizontal swipe (default: 2mm per frame, 6mm total)."""
    src.touch_down(0, 0, 0)
    src.touch_down(1, 0, 200)
    for index in range(1, frames + 1):
        time.sleep(frame_s)
        src.touch_move(0, -index * step_units, 0)
        src.touch_move(1, -index * step_units, 200)
    time.sleep(frame_s)
    src.touch_up(0)
    src.touch_up(1)


def wait_workspace_change(hypr, want_id, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, out = hypr.hyprctl("activeworkspace", "-j")
        try:
            if json.loads(out)["id"] == want_id:
                return True
        except (ValueError, KeyError):
            pass
        time.sleep(0.05)
    return False


def surface_names(hypr):
    """Names of layer/client surfaces as the compositor reports them."""
    names = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("namespace", "class") and isinstance(value, str):
                    names.add(f"{key}:{value}")
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for command in (("layers", "-j"), ("clients", "-j")):
        rc, out = hypr.hyprctl(*command)
        if rc != 0:
            continue
        try:
            walk(json.loads(out))
        except ValueError:
            continue
    return names


# --- scenarios ---------------------------------------------------------------


@scenario
def pointer_passthrough():
    """Pointer motion and buttons arrive untouched on the virtual mouse."""
    src = virtdev.magic_mouse()
    daemon = Daemon(label="passthrough")
    try:
        observe.ensure_node(MM)
        sink = observe.ensure_node(SINK_NAME)
        assert daemon.wait_log("managing /dev/input"), f"daemon never managed the source:\n{daemon._log_text()}"
        rec = observe.DeviceRecorder(sink)
        src.pointer(5, -3)
        src.button(True)
        src.button(False)
        got = rec.wait_for(
            lambda evs: (evdev.REL_X, 5) in [(c, v) for (_, ty, c, v) in evs if ty == evdev.EV_REL]
            and (evdev.REL_Y, -3) in [(c, v) for (_, ty, c, v) in evs if ty == evdev.EV_REL]
            and (evdev.BTN_LEFT, 1) in [(c, v) for (_, ty, c, v) in evs if ty == evdev.EV_KEY]
            and (evdev.BTN_LEFT, 0) in [(c, v) for (_, ty, c, v) in evs if ty == evdev.EV_KEY],
            timeout=3,
        )
        assert got, f"expected pointer+button passthrough, saw {rec.events}"
        rec.stop()
    finally:
        daemon.stop()
        src.close()


@scenario
def crash_safety_grab_release():
    """SIGKILL must release the device grab — hardware never goes dead."""
    src = virtdev.magic_mouse()
    daemon = Daemon(label="crash")
    try:
        src_node = observe.ensure_node(MM)
        observe.ensure_node(SINK_NAME)
        assert daemon.wait_log("managing /dev/input"), daemon._log_text()
        rec = observe.DeviceRecorder(src_node)
        src.pointer(7, 0)
        time.sleep(0.4)
        assert not rec.events, f"grab failed: events leaked while daemon held the device: {rec.events}"

        daemon.kill9()
        time.sleep(0.3)
        rec.clear()
        src.pointer(9, 0)
        got = rec.wait_for(lambda evs: bool(evs), timeout=3)
        assert got, "device stayed dead after daemon was SIGKILLed"
        rec.stop()
    finally:
        if daemon.alive():
            daemon.stop()
        src.close()


@scenario
def touch_scroll_natural():
    """A surface drag scrolls with natural (macOS) direction, exactly."""
    src = virtdev.magic_mouse()
    daemon = Daemon(cfg_with(scroll__inertia__enabled=False), label="scroll")
    try:
        observe.ensure_node(MM)
        sink = observe.ensure_node(SINK_NAME)
        assert daemon.wait_log("managing /dev/input"), daemon._log_text()
        rec = observe.DeviceRecorder(sink)
        mm_drag(src, frames=3)  # 3 x 1mm down
        rec.wait_for(lambda evs: hi_res_sum(evs) != 0, timeout=3)
        time.sleep(0.3)
        assert hi_res_sum(rec.events) == 432, (  # 3mm * 1.2 det/mm * 120
            f"expected +432 hi-res units, got {hi_res_sum(rec.events)}"
        )
        assert legacy_sum(rec.events) == 3, legacy_sum(rec.events)
        rec.stop()
    finally:
        daemon.stop()
        src.close()


@scenario
def natural_sign_flips():
    """scroll.natural = false must invert the direction."""
    src = virtdev.magic_mouse()
    daemon = Daemon(
        cfg_with(scroll__natural=False, scroll__inertia__enabled=False), label="sign"
    )
    try:
        observe.ensure_node(MM)
        sink = observe.ensure_node(SINK_NAME)
        assert daemon.wait_log("managing /dev/input"), daemon._log_text()
        rec = observe.DeviceRecorder(sink)
        mm_drag(src, frames=2)  # 2 x 1mm down
        rec.wait_for(lambda evs: hi_res_sum(evs) != 0, timeout=3)
        time.sleep(0.2)
        assert hi_res_sum(rec.events) == -288, hi_res_sum(rec.events)
        rec.stop()
    finally:
        daemon.stop()
        src.close()


@scenario
def inertial_scrolling():
    """After a flick the scroll keeps gliding, decays, and stops by itself."""
    src = virtdev.magic_mouse()
    daemon = Daemon(label="inertia")
    try:
        observe.ensure_node(MM)
        sink = observe.ensure_node(SINK_NAME)
        assert daemon.wait_log("managing /dev/input"), daemon._log_text()
        rec = observe.DeviceRecorder(sink)
        mm_drag(src, frames=6, step_units=71, frame_s=0.008)  # 6mm in ~50ms: a flick
        t_lift = time.monotonic()
        time.sleep(2.0)

        post = [(t, v) for (t, v) in rel_events(rec.events, evdev.REL_WHEEL_HI_RES) if t > t_lift + 0.05]
        assert len(post) > 5, f"momentum produced only {len(post)} frames"
        total = sum(v for _, v in post)
        assert 1000 < total < 20000, f"momentum displacement out of range: {total}"
        thirds = [post[i : i + len(post) // 3] for i in range(0, len(post) - len(post) % 3, len(post) // 3)]
        sums = [sum(v for _, v in part) for part in thirds]
        assert sums[0] > sums[-1], f"momentum did not decay: {sums}"
        assert post[-1][0] < t_lift + 1.7, "momentum outlived max_duration"
        rec.stop()
    finally:
        daemon.stop()
        src.close()


@scenario
def wheel_smoothing():
    """A wheel detent animates into hi-res scroll instead of jumping."""
    src = virtdev.wheel_mouse()
    daemon = Daemon(cfg_with(scroll__natural=False), label="wheel")
    try:
        observe.ensure_node(WM)
        sink = observe.ensure_node(SINK_NAME)
        assert daemon.wait_log("managing /dev/input"), daemon._log_text()
        rec = observe.DeviceRecorder(sink)
        src.wheel_detents(1)
        rec.wait_for(lambda evs: hi_res_sum(evs) != 0, timeout=3)
        time.sleep(0.4)
        frames = [v for (t, v) in rel_events(rec.events, evdev.REL_WHEEL_HI_RES)]
        assert sum(frames) == 120, frames
        assert len(frames) >= 3, f"detent was not animated: {frames}"
        assert max(frames) < 120, f"detent jumped: {frames}"
        assert legacy_sum(rec.events) == 1, legacy_sum(rec.events)
        rec.stop()
    finally:
        daemon.stop()
        src.close()


@scenario
def swipe_switches_workspace():
    """A two-finger swipe switches the real Hyprland workspace."""
    hypr = HeadlessHyprland().start()
    src = virtdev.magic_mouse()
    daemon = Daemon(env=hypr.session_env(), label="gesture")
    try:
        observe.ensure_node(MM)
        observe.ensure_node(SINK_NAME)
        assert daemon.wait_log("managing /dev/input"), daemon._log_text()
        hypr.hyprctl("dispatch", "workspace", "2")  # pre-create the neighbour
        hypr.hyprctl("dispatch", "workspace", "1")
        _, out = hypr.hyprctl("activeworkspace", "-j")
        assert json.loads(out)["id"] == 1, out

        mm_swipe_left(src)
        assert wait_workspace_change(hypr, 2), (
            "swipe_left should run `hyprctl dispatch workspace e+1`; "
            f"active workspace still {hypr.hyprctl('activeworkspace', '-j')[1]}"
        )
    finally:
        daemon.stop()
        src.close()
        hypr.stop()


@scenario
def hotplug_attach():
    """A device plugged in after startup gets managed on the next scan."""
    daemon = Daemon(label="hotplug")
    src = None
    try:
        observe.ensure_node(SINK_NAME)
        time.sleep(0.8)  # daemon has had time for its first scan
        assert "managing /dev/input" not in daemon._log_text(), (
            "daemon managed a device that did not exist yet"
        )
        src = virtdev.magic_mouse()
        observe.ensure_node(MM)
        assert daemon.wait_log("managing /dev/input"), daemon._log_text()
        sink = observe.ensure_node(SINK_NAME)
        rec = observe.DeviceRecorder(sink)
        src.pointer(4, 0)
        got = rec.wait_for(lambda evs: bool(evs), timeout=3)
        assert got, "hotplugged device produced no output"
        rec.stop()
    finally:
        daemon.stop()
        if src:
            src.close()


@scenario
def config_reload():
    """SIGHUP applies a new config; an invalid one keeps the running config."""
    src = virtdev.magic_mouse()
    rec = None
    daemon = Daemon(cfg_with(scroll__inertia__enabled=False), label="reload")
    try:
        observe.ensure_node(MM)
        sink = observe.ensure_node(SINK_NAME)
        assert daemon.wait_log("managing /dev/input"), daemon._log_text()
        rec = observe.DeviceRecorder(sink)

        mm_drag(src, frames=2)  # natural=true (default): fingers down -> positive
        rec.wait_for(lambda evs: hi_res_sum(evs) != 0, timeout=3)
        time.sleep(0.2)
        assert hi_res_sum(rec.events) == 288, hi_res_sum(rec.events)

        with open(daemon.config_path, "w", encoding="utf-8") as handle:
            json.dump({"scroll": {"natural": False, "inertia": {"enabled": False}}}, handle)
        daemon.proc.send_signal(signal.SIGHUP)
        assert daemon.wait_log("config reloaded"), daemon._log_text()

        rec.clear()
        mm_drag(src, frames=2)  # same drag must now scroll the other way
        rec.wait_for(lambda evs: hi_res_sum(evs) != 0, timeout=3)
        time.sleep(0.2)
        assert hi_res_sum(rec.events) == -288, hi_res_sum(rec.events)

        with open(daemon.config_path, "w", encoding="utf-8") as handle:
            handle.write('{"scroll": {"natural": NaN}}')
        daemon.proc.send_signal(signal.SIGHUP)
        assert daemon.wait_log("reload failed, keeping the current config"), daemon._log_text()
        assert daemon.alive(), "a bad config must not kill the daemon"
    finally:
        daemon.stop()
        if rec is not None:
            rec.stop()
        src.close()


@scenario
def omarchy_menu_gesture():
    """swipe_down runs the real `omarchy menu toggle root` and the menu
    surface appears in the compositor — the full Omarchy action layer."""
    hypr = HeadlessHyprland().start()
    shell_log = os.path.join(hypr.dir, "omarchy-shell.log")
    # The exact quickshell invocation omarchy-launch-shell runs in production
    # (minus systemd-cat journald supervision).
    shell = subprocess.Popen(
        ["quickshell", "-n", "-p", os.path.join(hypr.session_env()["OMARCHY_PATH"], "shell")],
        stdout=open(shell_log, "w"),
        stderr=subprocess.STDOUT,
        env={**hypr.session_env(), "QS_DISABLE_FILE_WATCHER": "1", "QS_NO_RELOAD_POPUP": "1"},
    )
    src = virtdev.magic_mouse()
    daemon = Daemon(env=hypr.session_env(), label="menu")
    try:
        observe.ensure_node(MM)
        observe.ensure_node(SINK_NAME)
        assert daemon.wait_log("managing /dev/input"), daemon._log_text()
        # wait until the shell answers so its startup surfaces cannot
        # masquerade as the gesture's effect
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            ping = subprocess.run(
                ["omarchy-shell", "shell", "ping"],
                env=hypr.session_env(),
                capture_output=True,
            )
            if ping.returncode == 0:
                break
            time.sleep(0.3)
        else:
            raise AssertionError(
                f"omarchy-shell never answered ping; shell log: {open(shell_log).read()[-2000:]}"
            )
        time.sleep(0.5)
        before = surface_names(hypr)

        src.touch_down(0, 0, 0)
        src.touch_down(1, 0, 200)
        for index in range(1, 4):
            time.sleep(0.008)
            src.touch_move(0, 0, index * 142)  # fingers move down 2mm per frame (6mm total)
            src.touch_move(1, 0, 200 + index * 142)
        src.touch_up(0)
        src.touch_up(1)

        deadline = time.monotonic() + 5
        appeared = set()
        while time.monotonic() < deadline:
            appeared = surface_names(hypr) - before
            if any("menu" in name.lower() for name in appeared):
                break
            time.sleep(0.1)
        assert any("menu" in name.lower() for name in appeared), (
            "swipe_down should summon the Omarchy menu surface; "
            f"new surfaces: {appeared or 'none'}. "
            f"daemon log: {daemon._log_text()}. "
            f"shell log: {open(shell_log).read()[-2000:]}"
        )
    finally:
        daemon.stop()
        src.close()
        shell.terminate()
        shell.wait(timeout=5)
        hypr.stop()


@scenario
def libinput_sees_coherent_scroll():
    """A third-party consumer (libinput) reads a clean scroll stream."""
    if not shutil.which("libinput"):
        raise SkipScenario("libinput CLI (libinput-tools) not installed")
    src = virtdev.magic_mouse()
    daemon = Daemon(cfg_with(scroll__inertia__enabled=False), label="libinput")
    try:
        observe.ensure_node(MM)
        sink = observe.ensure_node(SINK_NAME)
        assert daemon.wait_log("managing /dev/input"), daemon._log_text()
        # libinput block-buffers piped stdout and loses it on SIGTERM: write
        # to a file and stop it the way a user would (SIGINT)
        log_path = os.path.join(daemon.dir, "libinput.log")
        handle = open(log_path, "w")
        proc = subprocess.Popen(
            ["libinput", "debug-events", "--device", sink],
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        time.sleep(0.5)
        mm_drag(src, frames=3)
        time.sleep(0.5)
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=5)
        handle.close()
        out = open(log_path, encoding="utf-8", errors="replace").read()
        assert "scroll" in out.lower(), f"libinput saw no scroll:\n{out[-2000:]}"
    finally:
        daemon.stop()
        src.close()


# --- runner ------------------------------------------------------------------


def main():
    failures = []
    skips = []
    for fn in RESULTS:
        started = time.monotonic()
        try:
            fn()
            status = "PASS"
            detail = ""
        except SkipScenario as reason:
            status = "SKIP"
            detail = f"({reason})"
            skips.append(fn.__name__)
        except Exception as error:  # noqa: BLE001 - report and continue
            status = "FAIL"
            detail = ""
            failures.append((fn.__name__, traceback.format_exc()))
        print(f"[{status}] {fn.__name__:<32} {time.monotonic() - started:5.2f}s {detail}")
    print()
    if failures:
        for name, error in failures:
            print(f"--- {name} ---")
            print(error)
        print(f"{len(failures)}/{len(RESULTS)} scenario(s) failed ({len(skips)} skipped)")
        return 1
    print(f"all {len(RESULTS) - len(skips)} scenarios passed ({len(skips)} skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
