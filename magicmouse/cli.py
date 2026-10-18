"""Command line interface for omarchy-magic-mouse."""

import argparse
import json
import os
import shutil
import subprocess
import sys

from . import evdev
from .actions import ActionDispatcher
from .config import (
    CONFIG_PATH,
    ConfigError,
    default_config,
    load_config,
)
from .pipeline import DevicePipeline, classify, synthetic_info
from .service import Service
from .sink import RecordingSink

UNIT = "omarchy-magic-mouse.service"
SYNTHETIC_KINDS = {"magic-mouse": "apple_touch_mouse", "wheel-mouse": "wheel_mouse"}


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _print_sink_line(clock):
    def printer(label, detail):
        print(f"+{clock():7.3f}s  {label:<7} {detail}")

    return printer


def cmd_run(args):
    import time

    cfg = load_config(args.config)
    log = lambda message: print(f"omarchy-magic-mouse: {message}", file=sys.stderr, flush=True)
    clock = time.monotonic
    t0 = time.monotonic()
    display_clock = lambda: time.monotonic() - t0  # noqa: E731
    display_printer = _print_sink_line(display_clock)
    if args.dry_run:
        sink = RecordingSink(printer=display_printer)
        dispatcher = ActionDispatcher(
            dry_run=True, log=lambda message: display_printer("action", message)
        )
        print("omarchy-magic-mouse: dry run — actions and scroll are printed, not applied")
    else:
        try:
            sink = evdev.UInputMouse(clock=clock)
        except OSError as error:
            print(f"cannot open {evdev.UINPUT_PATH}: {error}", file=sys.stderr)
            print("run `omarchy-magic-mouse doctor` to diagnose permissions", file=sys.stderr)
            return 1
        dispatcher = ActionDispatcher(log=log)
    service = Service(cfg, sink, dispatcher=dispatcher, log=log, clock=clock)
    service.run_forever()
    return 0


def _parse_replay_line(line):
    parts = line.split()
    if len(parts) != 4:
        raise ValueError(f"expected 'TIME_MS TYPE CODE VALUE', got: {line!r}")
    t_ms = float(parts[0])
    type_name, code_name, value = parts[1], parts[2], int(parts[3])
    if type_name not in evdev.TYPE_NAME_TO_CODE:
        raise ValueError(f"unknown event type {type_name!r}")
    ev_type = evdev.TYPE_NAME_TO_CODE[type_name]
    if ev_type == evdev.EV_SYN:
        code = evdev.SYN_REPORT
    elif code_name in evdev.NAME_TO_CODE:
        code = evdev.NAME_TO_CODE[code_name]
    else:
        raise ValueError(f"unknown event code {code_name!r}")
    return t_ms / 1000.0, evdev.InputEvent(ev_type, code, value)


def cmd_simulate(args):
    cfg = load_config(args.config)
    kind = SYNTHETIC_KINDS[args.device]
    clock = _Clock()
    printer = _print_sink_line(clock)
    sink = RecordingSink(printer=printer)
    dispatcher = ActionDispatcher(dry_run=True, log=lambda message: printer("action", message))
    pipeline = DevicePipeline(synthetic_info(kind), cfg, sink, dispatcher, clock)

    for number, raw in enumerate(sys.stdin, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            when, event = _parse_replay_line(line)
        except ValueError as error:
            print(f"line {number}: {error}", file=sys.stderr)
            return 2
        # run the frame loop between input bursts, like the daemon does
        while pipeline.needs_ticks() and clock.now + 1 / 60 < when:
            clock.now += 1 / 60
            pipeline.tick(clock.now)
        clock.now = when
        pipeline.process(event)

    # let momentum/smooth scrolling finish, like the daemon's frame loop
    deadline = clock.now + 3.0
    while pipeline.needs_ticks() and clock.now < deadline:
        clock.now += 1 / 60
        pipeline.tick(clock.now)
    return 0


def cmd_devices(_args):
    rows = []
    cfg = load_config()
    wanted = set(cfg["devices"]["manage"])
    for info in evdev.list_devices():
        kind = classify(info)
        rows.append((info.path, info.name, f"{info.vendor:04x}:{info.product:04x}", kind, "yes" if kind in wanted else "-"))
    if not rows:
        print("no readable input devices (need the `input` group or root — see doctor)")
        return 1
    widths = [max(len(str(row[i])) for row in rows + [("PATH", "NAME", "ID", "KIND", "MANAGED")]) for i in range(5)]
    header = ("PATH", "NAME", "ID", "KIND", "MANAGED")
    for row in (header,) + tuple(rows):
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)))
    return 0


def cmd_status(_args):
    state = "unknown"
    if shutil.which("systemctl"):
        result = subprocess.run(
            ["systemctl", "--user", "is-active", UNIT],
            capture_output=True,
            text=True,
            check=False,
        )
        state = result.stdout.strip() or result.stderr.strip() or "unknown"
    print(f"service:  {state} ({UNIT})")
    print(f"config:   {CONFIG_PATH} ({'present' if os.path.exists(CONFIG_PATH) else 'defaults in use'})")
    return 0


def cmd_service(args):
    if not shutil.which("systemctl"):
        print("systemctl not found; start the daemon manually with `omarchy-magic-mouse run`", file=sys.stderr)
        return 1
    result = subprocess.run(["systemctl", "--user", args.action, UNIT], check=False)
    return result.returncode


def cmd_config(args):
    if args.show:
        print(json.dumps(load_config(args.config), indent=2))
        return 0
    path = args.config or CONFIG_PATH
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(default_config(), handle, indent=2)
            handle.write("\n")
        print(f"wrote {path}")
    if args.init:
        return 0
    editor = os.environ.get("EDITOR", "vi")
    os.execvp(editor, [editor, path])


def cmd_log(args):
    command = ["journalctl", "--user", "-u", UNIT]
    if not args.no_follow:
        command.append("-f")
    os.execvp(command[0], command)


def cmd_doctor(_args):
    failures = 0

    def report(ok, label, hint="", required=True):
        nonlocal failures
        mark = " ok " if ok else ("FAIL" if required else "warn")
        line = f"[{mark}] {label}"
        if not ok and hint:
            line += f"\n       -> {hint}"
        print(line)
        if not ok and required:
            failures += 1

    report(os.path.exists(evdev.UINPUT_PATH), "/dev/uinput exists",
           "sudo modprobe uinput; the install script also sets up /etc/modules-load.d")
    writable = os.access(evdev.UINPUT_PATH, os.W_OK) if os.path.exists(evdev.UINPUT_PATH) else False
    report(writable, "/dev/uinput writable",
           "install the udev rule and log out/in (user must be in the `input` group)")
    readable = [p for p in evdev.list_device_paths() if os.access(p, os.R_OK)]
    report(bool(readable), "/dev/input/event* readable",
           "install the udev rule and log out/in (user must be in the `input` group)")
    report(shutil.which("hyprctl") is not None, "hyprctl on PATH",
           "not running inside Omarchy/Hyprland?", required=False)
    report(bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")), "Hyprland session detected",
           "run inside the Omarchy desktop session", required=False)
    report(shutil.which("systemctl") is not None, "systemctl available", required=False)
    try:
        load_config()
        report(True, f"config valid ({CONFIG_PATH})")
    except ConfigError as error:
        report(False, f"config invalid: {error}", "fix the JSON or delete the file to use defaults")
    for info in evdev.list_devices():
        kind = classify(info)
        if kind in ("apple_touch_mouse", "wheel_mouse"):
            print(f"[info] managed-class device: {info.path} {info.name} [{kind}]")
    print("doctor: all required checks passed" if failures == 0 else f"doctor: {failures} required check(s) failed")
    return 0 if failures == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="omarchy-magic-mouse",
        description="macOS Magic Mouse behaviour for Omarchy: inertial scrolling, smooth wheel, swipe gestures.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run the daemon in the foreground")
    p_run.add_argument("--config", default=None)
    p_run.add_argument("--dry-run", action="store_true", help="print actions/scroll instead of applying them")
    p_run.set_defaults(func=cmd_run)

    p_sim = sub.add_parser("simulate", help="feed a scripted input session from stdin")
    p_sim.add_argument("--device", choices=sorted(SYNTHETIC_KINDS), default="magic-mouse")
    p_sim.add_argument("--config", default=None)
    p_sim.set_defaults(func=cmd_simulate)

    p_dev = sub.add_parser("devices", help="list input devices and how they are treated")
    p_dev.set_defaults(func=cmd_devices)

    p_st = sub.add_parser("status", help="show service and config state")
    p_st.set_defaults(func=cmd_status)

    for action in ("start", "stop", "restart"):
        p = sub.add_parser(action, help=f"{action} the user service")
        p.set_defaults(func=cmd_service, action=action)

    p_cfg = sub.add_parser("config", help="create/edit the config file")
    p_cfg.add_argument("--config", default=None)
    p_cfg.add_argument("--init", action="store_true", help="only write the default config")
    p_cfg.add_argument("--show", action="store_true", help="print the merged config")
    p_cfg.set_defaults(func=cmd_config)

    p_log = sub.add_parser("log", help="follow the service journal")
    p_log.add_argument("--no-follow", action="store_true")
    p_log.set_defaults(func=cmd_log)

    p_doc = sub.add_parser("doctor", help="check permissions and integration")
    p_doc.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as error:
        print(f"config error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
