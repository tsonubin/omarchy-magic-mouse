"""Lifecycle control for the daemon under test and headless Hyprland."""

import glob
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time

from harness import observe

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
DAEMON = os.path.join(REPO_ROOT, "bin", "omarchy-magic-mouse")


class Daemon:
    """Runs the real daemon with a scenario-specific config file."""

    def __init__(self, config=None, label="daemon", env=None):
        self.dir = tempfile.mkdtemp(prefix=f"mm-e2e-{label}-")
        self.config_path = os.path.join(self.dir, "magic-mouse.json")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(config or _defaults(), handle, indent=2)
        self.log_path = os.path.join(self.dir, "daemon.log")
        self._log = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            ["python3", DAEMON, "run", "--config", self.config_path],
            stdout=self._log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1", **(env or {})},
        )

    def _log_text(self):
        self._log.flush()
        with open(self.log_path, encoding="utf-8") as handle:
            return handle.read()

    def wait_log(self, pattern, timeout=6.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pattern in self._log_text():
                return True
            time.sleep(0.02)
        return pattern in self._log_text()

    def alive(self):
        return self.proc.poll() is None

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self._log.close()
        try:
            _wait_gone(evdev_name="Omarchy Magic Mouse")
        except OSError:
            pass  # teardown races must never mask a scenario's result

    def kill9(self):
        self.proc.kill()
        self.proc.wait(timeout=5)
        self._log.close()
        try:
            _wait_gone(evdev_name="Omarchy Magic Mouse")
        except OSError:
            pass


def _wait_gone(evdev_name, timeout=5.0):
    """Wait until the kernel drops a named device (uinput cleanup on exit)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(name == evdev_name for _, name, _, _ in observe._sys_input_devices()):
            return
        time.sleep(0.02)


class HeadlessHyprland:
    """A real Hyprland instance with no outputs or GPU (HYPRLAND_HEADLESS_ONLY)."""

    def __init__(self, label="hyprland"):
        # keep the runtime path short: Hyprland's IPC socket path has a
        # 108-byte unix-socket limit and the instance dir eats a lot of it
        self.dir = tempfile.mkdtemp(prefix="hn", dir="/tmp")
        self.runtime = os.path.join(self.dir, "runtime")
        os.makedirs(self.runtime, mode=0o700)
        self.config_home = os.path.join(self.dir, "config")
        os.makedirs(os.path.join(self.config_home, "hypr"))
        self.config_path = os.path.join(self.config_home, "hypr", "hyprland.conf")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            handle.write(MINIMAL_CONFIG)
        self.log_path = os.path.join(self.dir, "hyprland.log")
        self.proc = None

    def start(self):
        self._log = open(self.log_path, "w", encoding="utf-8")
        subprocess.run(
            [
                "dbus-daemon",
                "--session",
                "--address=unix:path=" + os.path.join(self.runtime, "bus"),
                "--fork",
            ],
            check=False,
        )
        env = {
            **os.environ,
            "XDG_RUNTIME_DIR": self.runtime,
            "XDG_CONFIG_HOME": self.config_home,
            "XDG_CURRENT_DESKTOP": "Hyprland",
            "HYPRLAND_HEADLESS_ONLY": "1",
            "LIBGL_ALWAYS_SOFTWARE": "1",
            # the rig container is root and owns its devices: skip seat
            # management (logind/seatd/VTs do not exist in a container)
            "LIBSEAT_BACKEND": "noop",
        }
        self.proc = subprocess.Popen(
            [
                "Hyprland",
                "--config",
                self.config_path,
                # the rig container runs as root; Hyprland's own opt-out flag
                *(["--i-am-really-stupid"] if os.geteuid() == 0 else []),
            ],
            stdout=self._log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if self._instance_signature() and self.hyprctl("version")[0] == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError(f"Hyprland did not come up; log tail:\n{self._log_tail()}")
        # an extra output in case the DRM card exposes no connectors
        self.hyprctl("output", "create", "headless", "HEADLESS")
        return self

    def hyprctl(self, *args):
        result = subprocess.run(
            ["hyprctl", *args],
            capture_output=True,
            text=True,
            env=self.session_env(),
            timeout=10,
        )
        return result.returncode, result.stdout.strip()

    def session_env(self):
        """Environment for clients (the daemon, omarchy-shell) of this session."""
        return {
            **os.environ,
            "XDG_RUNTIME_DIR": self.runtime,
            "XDG_CONFIG_HOME": self.config_home,
            "XDG_CURRENT_DESKTOP": "Hyprland",
            "WAYLAND_DISPLAY": self._wayland_socket(),
            "HYPRLAND_INSTANCE_SIGNATURE": self._instance_signature(),
            "OMARCHY_PATH": os.environ.get("OMARCHY_PATH", "/usr/share/omarchy"),
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + os.path.join(self.runtime, "bus"),
        }

    def _wayland_socket(self):
        if getattr(self, "_wayland", None):
            return self._wayland
        try:
            for name in sorted(os.listdir(self.runtime)):
                if name.startswith("wayland") and not name.endswith(".lock"):
                    self._wayland = name
                    return name
        except OSError:
            pass
        return "wayland-1"

    def _instance_signature(self):
        # Hyprland publishes its socket under XDG_RUNTIME_DIR/hypr/<signature>
        if getattr(self, "_sig", None):
            return self._sig
        hypr_dir = os.path.join(self.runtime, "hypr")
        try:
            entries = [e for e in os.listdir(hypr_dir) if e not in (".", "..")]
        except OSError:
            return ""
        if entries:
            self._sig = entries[0]
            return self._sig
        return ""

    def _log_tail(self):
        self._log.flush()
        parts = []
        with open(self.log_path, encoding="utf-8", errors="replace") as handle:
            parts.extend(handle.read().splitlines()[-12:])
        instance = glob.glob(os.path.join(self.runtime, "hypr", "*", "hyprland.log"))
        if instance:
            with open(instance[0], encoding="utf-8", errors="replace") as handle:
                parts.append("--- instance log ---")
                parts.extend(handle.read().splitlines()[-12:])
        return "\n".join(parts)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.hyprctl("dispatch", "exit")
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if hasattr(self, "_log"):
            self._log.close()


MINIMAL_CONFIG = """
monitor = , preferred, auto, 1
general {
    gaps_in = 0
    gaps_out = 0
}
input {
    natural_scroll = false
}
misc {
    disable_hyprland_qt = true
}
debug {
    disable_logs = false
    enable_stdout_logs = true
}
"""


def _defaults():
    from magicmouse.config import default_config

    return default_config()
