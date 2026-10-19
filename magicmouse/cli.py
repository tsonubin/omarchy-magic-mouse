"""Command line interface for omarchy-magic-mouse."""

import argparse
import grp
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from . import evdev
from .actions import ActionDispatcher
from .config import (
    CONFIG_PATH,
    ConfigError,
    default_config,
    load_config,
)
from .pipeline import (
    KIND_OTHER,
    KIND_TRACKPAD,
    DevicePipeline,
    classify,
    should_manage,
    synthetic_info,
)
from .service import InstanceLock, Service
from .sink import RecordingSink

UNIT = "omarchy-magic-mouse.service"
UDEV_RULE = "99-omarchy-magic-mouse.rules"
SYNTHETIC_KINDS = {"magic-mouse": "apple_touch_mouse", "wheel-mouse": "wheel_mouse"}

EPILOG = """examples:
  omarchy-magic-mouse setup      guided first run (use --dry-run to preview)
  omarchy-magic-mouse doctor     what is missing and how to fix it
  omarchy-magic-mouse devices    input devices and how each is treated
  omarchy-magic-mouse config     create/edit ~/.config/omarchy/magic-mouse.json
  omarchy-magic-mouse log        follow the daemon journal
"""


@dataclass
class Check:
    label: str
    state: str  # "ok" | "warn" | "fail"
    fix: str = ""
    detail: str = ""


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _print_sink_line(clock):
    def printer(label, detail):
        print(f"+{clock():7.3f}s  {label:<7} {detail}")

    return printer


# --- system checks -----------------------------------------------------------


def _probe(command, timeout=5):
    """Run a query command with a bound: a wedged session bus must not hang
    first-run diagnostics.  Returns None when the command fails or times out."""
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _probe_natural_scroll():
    """The value Hyprland applies to every pointer device, including ours."""
    result = _probe(["hyprctl", "getoption", "input:natural_scroll"])
    if result is None:
        return None
    for line in result.stdout.splitlines():
        if line.strip().startswith("int:"):
            try:
                return int(line.split(":", 1)[1].strip()) != 0
            except ValueError:
                return None
    return None


def collect_checks(config_path=None):
    """Every environment check, each with the fix when it is not ok."""
    config_path = config_path or CONFIG_PATH
    checks = []

    uinput_exists = os.path.exists(evdev.UINPUT_PATH)
    checks.append(
        Check(
            "kernel module uinput (/dev/uinput)",
            "ok" if uinput_exists else "fail",
            fix="" if uinput_exists else "sudo modprobe uinput  # persist: install.sh sets up modules-load.d",
        )
    )
    uinput_writable = uinput_exists and os.access(evdev.UINPUT_PATH, os.W_OK)
    checks.append(
        Check(
            "/dev/uinput writable",
            "ok" if uinput_writable else "fail",
            fix="" if uinput_writable else "./install.sh  # installs the udev rule; then log out and back in",
        )
    )

    rule_paths = [f"/etc/udev/rules.d/{UDEV_RULE}", f"/usr/lib/udev/rules.d/{UDEV_RULE}"]
    rule_installed = any(os.path.exists(path) for path in rule_paths)
    checks.append(
        Check(
            "udev rule installed",
            "ok" if rule_installed else "warn",
            fix="" if rule_installed else "./install.sh  # or copy udev/%s to /etc/udev/rules.d/" % UDEV_RULE,
            detail="" if rule_installed else "without it the daemon cannot open devices",
        )
    )

    try:
        input_gid = grp.getgrnam("input").gr_gid
        in_group = input_gid in os.getgroups() or input_gid == os.getgid()
    except KeyError:
        in_group = False
    checks.append(
        Check(
            'membership of the "input" group',
            "ok" if in_group else "warn",
            fix="" if in_group else "sudo usermod -aG input $USER   # then log out and back in",
            detail="" if in_group else "other access (root, ACLs) may still work: see the checks above",
        )
    )

    readable = [p for p in evdev.list_device_paths() if os.access(p, os.R_OK)]
    if readable:
        state, fix = "ok", ""
    elif evdev.list_device_paths():
        state, fix = "fail", "./install.sh  # udev rule + input group; then log out and back in"
    else:
        state, fix = "warn", ""
    checks.append(
        Check(
            "/dev/input/event* readable",
            state,
            fix=fix,
            detail="" if state != "warn" else "no input devices found at all",
        )
    )

    checks.append(
        Check(
            f"python {'.'.join(map(str, sys.version_info[:3]))}",
            "ok" if sys.version_info >= (3, 9) else "fail",
            fix="" if sys.version_info >= (3, 9) else "install python >= 3.9",
        )
    )

    try:
        load_config(config_path)
        checks.append(Check(f"config valid ({config_path})", "ok"))
    except ConfigError as error:
        checks.append(
            Check(
                f"config valid ({config_path})",
                "fail",
                fix=f"fix the JSON, or move the file aside to use defaults: {config_path}",
                detail=str(error),
            )
        )

    if shutil.which("systemctl"):
        enabled_probe = _probe(["systemctl", "--user", "is-enabled", UNIT])
        active_probe = _probe(["systemctl", "--user", "is-active", UNIT])
        if enabled_probe is None or active_probe is None:
            checks.append(
                Check(
                    f"service ({UNIT})",
                    "warn",
                    fix="systemctl --user is not responding — check the session dbus, then `systemctl --user status`",
                    detail="query timed out",
                )
            )
        else:
            enabled = enabled_probe.stdout.strip() or ("not-found" if enabled_probe.returncode else "unknown")
            active = active_probe.stdout.strip() or "inactive"
            if enabled in ("enabled", "static", "alias") and active == "active":
                state, fix = "ok", ""
            elif enabled == "not-found":
                state, fix = "warn", "./install.sh  # installs the user service"
            else:
                state, fix = "warn", f"systemctl --user enable --now {UNIT}"
            checks.append(
                Check(
                    f"service ({UNIT})",
                    state,
                    fix=fix,
                    detail=f"enabled: {enabled}, active: {active}",
                )
            )
    else:
        checks.append(
            Check("service (systemd user)", "warn", fix="run the daemon manually: omarchy-magic-mouse run")
        )

    if shutil.which("hyprctl") and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        natural = _probe_natural_scroll()
        try:
            our_natural = load_config(config_path)["scroll"]["natural"]
        except ConfigError:
            our_natural = False  # the config check above already reports it
        if natural is True and our_natural:
            checks.append(
                Check(
                    "scroll direction flips exactly once",
                    "warn",
                    fix="disable input:natural_scroll for our virtual mouse, or set scroll.natural=false in the config",
                    detail="both Hyprland and the config flip scroll right now",
                )
            )
        else:
            checks.append(Check("Hyprland session", "ok", detail="gesture actions can dispatch"))
    else:
        checks.append(
            Check(
                "Hyprland session",
                "warn",
                fix="run inside the Omarchy desktop session for gesture actions",
                detail="scrolling works without it",
            )
        )
    return checks


def print_checks(checks, heading=None):
    if heading:
        print(heading)
    for check in checks:
        mark = {"ok": " ok ", "warn": "warn", "fail": "FAIL"}[check.state]
        line = f"[{mark}] {check.label}"
        if check.detail:
            line += f"  ({check.detail})"
        print(line)
        if check.state != "ok" and check.fix:
            print(f"       -> {check.fix}")
    return sum(1 for check in checks if check.state == "fail")


# --- commands ----------------------------------------------------------------


def _require_config(path):
    """An explicitly given config must exist: silent defaults would mask typos."""
    if path is not None and not os.path.exists(path):
        raise ConfigError(f"config file not found: {path}")


def _write_default_config(path):
    """Create the default config; returns an error message on failure."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(default_config(), handle, indent=2)
            handle.write("\n")
        return None
    except OSError as error:
        return str(error)


def cmd_run(args):
    _require_config(args.config)
    cfg = load_config(args.config)
    lock = InstanceLock()
    if not lock.acquire():
        if lock.error is not None:
            print(f"cannot acquire instance lock {lock.path}: {lock.error}", file=sys.stderr)
            print(
                "hint: ensure the runtime directory exists and is writable, "
                "then retry",
                file=sys.stderr,
            )
        else:
            print(
                f"another instance is already managing the input devices (lock: {lock.path})",
                file=sys.stderr,
            )
            print(
                "hint: if the user service is running, try `omarchy-magic-mouse stop` "
                "or check `systemctl --user status omarchy-magic-mouse`",
                file=sys.stderr,
            )
        return 1
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
    service = Service(
        cfg,
        sink,
        dispatcher=dispatcher,
        log=log,
        clock=clock,
        config_path=args.config,
        debug=args.log_level == "debug",
    )
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
    if sys.stdin.isatty():
        print(
            "simulate reads a session from stdin (one 'TIME_MS TYPE CODE VALUE' per line);"
            " pipe or redirect one, e.g. `omarchy-magic-mouse simulate < session.txt`",
            file=sys.stderr,
        )
        return 2
    _require_config(args.config)
    cfg = load_config(args.config)
    kind = SYNTHETIC_KINDS[args.device]
    clock = _Clock()
    printer = _print_sink_line(clock)
    sink = RecordingSink(printer=printer)
    actions = []

    def log_action(message):
        printer("action", message)
        actions.append(message)

    dispatcher = ActionDispatcher(dry_run=True, log=log_action)
    pipeline = DevicePipeline(synthetic_info(kind), cfg, sink, dispatcher, clock)

    previous = None
    for number, raw in enumerate(sys.stdin, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            when, event = _parse_replay_line(line)
        except ValueError as error:
            print(f"line {number}: {error}", file=sys.stderr)
            return 2
        if previous is not None and when < previous:
            # a clock that moves backwards manufactures garbage (velocity
            # spikes, negative easing): refuse rather than lie about the feel
            print(
                f"line {number}: timestamps must not go backwards "
                f"({when * 1000:.0f}ms after {previous * 1000:.0f}ms)",
                file=sys.stderr,
            )
            return 2
        previous = when
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
    total_x = sum(x for x, _ in sink.scrolls)
    total_y = sum(y for _, y in sink.scrolls)
    printer(
        "total",
        f"scroll dx={total_x:+.0f} dy={total_y:+.0f} hi-res units "
        f"({total_x / 120:+.1f}/{total_y / 120:+.1f} detents), actions: {len(actions)}",
    )
    return 0


def _manage_verdict(info, wanted):
    """(managed, note) — exactly the daemon's decision, plus why not."""
    if should_manage(info, wanted):
        return "yes", ""
    if info.name == evdev.VIRTUAL_MOUSE_NAME:
        return "-", "our virtual mouse"
    if info.bustype == evdev.BUS_VIRTUAL:
        return "-", "virtual device"
    kind = classify(info)
    if kind == KIND_TRACKPAD:
        return "-", "left to libinput"
    if kind == KIND_OTHER:
        return "-", "unrecognized device"
    return "-", "not in devices.manage"


def cmd_devices(_args):
    rows = []
    cfg = load_config()
    wanted = set(cfg["devices"]["manage"])
    for info in evdev.list_devices():
        managed, note = _manage_verdict(info, wanted)
        rows.append(
            (
                info.path,
                info.name,
                f"{info.vendor:04x}:{info.product:04x}",
                classify(info),
                managed,
                note,
            )
        )
    if not rows:
        print("no readable input devices (need the `input` group or root — see doctor)")
        return 1
    header = ("PATH", "NAME", "ID", "KIND", "MANAGED", "NOTE")
    widths = [max(len(str(row[i])) for row in rows + [header]) for i in range(6)]
    for row in (header,) + tuple(rows):
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)))
    return 0


def cmd_status(_args):
    enabled = active = "unknown"
    if shutil.which("systemctl"):
        enabled_probe = _probe(["systemctl", "--user", "is-enabled", UNIT])
        active_probe = _probe(["systemctl", "--user", "is-active", UNIT])
        if enabled_probe is None or active_probe is None:
            enabled = active = "not responding"
        else:
            enabled = enabled_probe.stdout.strip() or ("not-found" if enabled_probe.returncode else "unknown")
            active = active_probe.stdout.strip() or "inactive"
    present = os.path.exists(CONFIG_PATH)
    print(f"service:  {active} (enabled: {enabled})")
    print(f"config:   {CONFIG_PATH} ({'present' if present else 'defaults in use'})")
    managed = [info for info in evdev.list_devices() if classify(info) in ("apple_touch_mouse", "wheel_mouse")]
    if managed:
        for info in managed:
            print(f"device:   {info.name} [{classify(info)}]")
    else:
        print("device:   none managed right now")
    return 0


def cmd_service(args):
    if not shutil.which("systemctl"):
        print("systemctl not found; start the daemon manually with `omarchy-magic-mouse run`", file=sys.stderr)
        return 1
    try:
        result = subprocess.run(
            ["systemctl", "--user", args.action, UNIT], check=False, timeout=30
        )
    except subprocess.TimeoutExpired:
        print(
            f"systemctl --user {args.action} timed out — the session bus may be wedged",
            file=sys.stderr,
        )
        return 1
    except OSError as error:
        print(f"cannot run systemctl: {error}", file=sys.stderr)
        return 1
    return result.returncode


def cmd_setup(args):
    """Guided first run: do what is possible, explain the rest."""
    print("omarchy-magic-mouse setup" + (" (dry run)" if args.dry_run else ""))

    print("\n1) configuration")
    if os.path.exists(args.config or CONFIG_PATH):
        path = args.config or CONFIG_PATH
        print(f"   present at {path} — never overwritten")
    elif args.dry_run:
        print(f"   would write the default config to {args.config or CONFIG_PATH}")
    else:
        path = args.config or CONFIG_PATH
        error = _write_default_config(path)
        if error:
            print(f"   cannot write {path}: {error}")
            return 1
        print(f"   wrote defaults to {path}")

    print("\n2) permissions (fixes need sudo; nothing is run automatically)")
    checks = [
        c
        for c in collect_checks(args.config)
        if not c.label.startswith(("config valid", "service"))
    ]
    print_checks(checks)

    print("\n3) service")
    unit_paths = (
        os.path.join(os.path.expanduser("~"), ".config/systemd/user", UNIT),
        f"/usr/lib/systemd/user/{UNIT}",  # packaged installs
    )
    if not any(os.path.exists(path) for path in unit_paths):
        print(f"   user unit not installed — run ./install.sh (it also covers step 2)")
    elif not shutil.which("systemctl"):
        print("   systemctl unavailable — start manually: omarchy-magic-mouse run")
    elif args.dry_run:
        print(f"   would run: systemctl --user enable --now {UNIT}")
    else:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "enable", "--now", UNIT], check=False, timeout=30
            )
        except (subprocess.TimeoutExpired, OSError):
            result = None
        if result is not None and result.returncode == 0:
            print("   enabled and started")
        else:
            print(f"   could not enable — run: systemctl --user enable --now {UNIT}")

    print("\nnext: omarchy-magic-mouse doctor | devices | config | log")
    return 0


def cmd_config(args):
    if args.show:
        _require_config(args.config)
        print(json.dumps(load_config(args.config), indent=2))
        return 0
    path = args.config or CONFIG_PATH
    if not os.path.exists(path):
        error = _write_default_config(path)
        if error:
            print(f"cannot write {path}: {error}", file=sys.stderr)
            return 1
        print(f"wrote {path}")
    if args.init:
        return 0
    editor = os.environ.get("EDITOR", "vi")
    try:
        os.execvp(editor, [editor, path])
    except OSError as error:
        print(f"cannot run editor {editor!r}: {error}", file=sys.stderr)
        print(f"edit the file manually: {path}", file=sys.stderr)
        return 1


def cmd_log(args):
    if not shutil.which("journalctl"):
        print(
            "journalctl not found — the daemon logs to stderr; run it in a terminal to see them",
            file=sys.stderr,
        )
        return 1
    command = ["journalctl", "--user", "-u", UNIT, "-n", str(args.lines)]
    if not args.no_follow:
        command.append("-f")
    try:
        os.execvp(command[0], command)
    except OSError as error:
        print(f"cannot run journalctl: {error}", file=sys.stderr)
        return 1


def cmd_doctor(_args):
    failures = print_checks(collect_checks(), heading="omarchy-magic-mouse doctor")
    for info in evdev.list_devices():
        kind = classify(info)
        if kind in ("apple_touch_mouse", "wheel_mouse"):
            print(f"[info] managed-class device: {info.path} {info.name} [{kind}]")
    print(
        "doctor: all required checks passed"
        if failures == 0
        else f"doctor: {failures} required check(s) failed — each has a fix above"
    )
    return 0 if failures == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="omarchy-magic-mouse",
        description=(
            "macOS Magic Mouse behaviour for Omarchy: inertial scrolling, "
            "smooth wheel scrolling, swipe gestures."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser(
        "run", help="run the daemon in the foreground",
        epilog="example: omarchy-magic-mouse run --dry-run   # preview actions live",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_run.add_argument("--config", default=None, help="config file (default: ~/.config/omarchy/magic-mouse.json)")
    p_run.add_argument("--dry-run", action="store_true", help="print actions/scroll instead of applying them")
    p_run.add_argument(
        "--log-level",
        choices=("info", "debug"),
        default="info",
        help="debug also logs device-selection decisions (why devices are ignored)",
    )
    p_run.set_defaults(func=cmd_run)

    p_sim = sub.add_parser(
        "simulate", help="replay a scripted input session from stdin",
        epilog=(
            "input format: one event per line — TIME_MS TYPE CODE VALUE\n"
            "  (blank lines and # comments are ignored)\n"
            "example:\n"
            "  0   EV_ABS ABS_MT_SLOT 0\n"
            "  0   EV_ABS ABS_MT_TRACKING_ID 1\n"
            "  0   EV_ABS ABS_MT_POSITION_X 0\n"
            "  0   EV_ABS ABS_MT_POSITION_Y 0\n"
            "  0   EV_SYN SYN_REPORT 0\n"
            "  16  EV_ABS ABS_MT_POSITION_Y 213\n"
            "  16  EV_SYN SYN_REPORT 0\n"
            "  24  EV_ABS ABS_MT_TRACKING_ID -1\n"
            "  24  EV_SYN SYN_REPORT 0\n"
            "\n"
            "timestamps must not go backwards (equal stamps are one frame);\n"
            "every run ends with a totals line."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_sim.add_argument(
        "--device",
        choices=sorted(SYNTHETIC_KINDS),
        default="magic-mouse",
        help="virtual device to replay against (default: magic-mouse)",
    )
    p_sim.add_argument("--config", default=None, help="config file (default: ~/.config/omarchy/magic-mouse.json)")
    p_sim.set_defaults(func=cmd_simulate)

    p_dev = sub.add_parser("devices", help="list input devices and how they are treated")
    p_dev.set_defaults(func=cmd_devices)

    p_st = sub.add_parser("status", help="show service, config and device state")
    p_st.set_defaults(func=cmd_status)

    for action in ("start", "stop", "restart"):
        p = sub.add_parser(action, help=f"{action} the user service")
        p.set_defaults(func=cmd_service, action=action)

    p_setup = sub.add_parser(
        "setup", help="guided first run: config, permissions, service",
        epilog=(
            "example: omarchy-magic-mouse setup --dry-run   # preview without changing anything\n"
            "readiness gate: `omarchy-magic-mouse doctor` exits non-zero until everything is fixed"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_setup.add_argument("--config", default=None, help="config file to create")
    p_setup.add_argument("--dry-run", action="store_true", help="print planned actions; change nothing")
    p_setup.set_defaults(func=cmd_setup)

    p_cfg = sub.add_parser(
        "config", help="create/edit the config file",
        epilog="example: omarchy-magic-mouse config --show   # print the merged config",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_cfg.add_argument("--config", default=None)
    p_cfg.add_argument("--init", action="store_true", help="only write the default config")
    p_cfg.add_argument("--show", action="store_true", help="print the merged config")
    p_cfg.set_defaults(func=cmd_config)

    p_log = sub.add_parser("log", help="follow the service journal")
    p_log.add_argument("--no-follow", action="store_true", help="print recent entries and exit")
    p_log.add_argument("--lines", type=int, default=200, metavar="N", help="entries to show (default: 200)")
    p_log.set_defaults(func=cmd_log)

    p_doc = sub.add_parser(
        "doctor", aliases=["diagnose"], help="check the environment and show how to fix what is missing",
        epilog="exit code 0 when every required check passes; ready for `omarchy-magic-mouse log`",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_doc.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except ConfigError as error:
        if len(error.problems) > 1:
            print(f"config error: {error.path or 'configuration'}", file=sys.stderr)
            for problem in error.problems:
                print(f"  - {problem}", file=sys.stderr)
        else:
            print(f"config error: {error}", file=sys.stderr)
        print("hint: `omarchy-magic-mouse config --show` prints the merged config", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
