"""Gesture action dispatch.

An action spec is ``{"name": ..., "command": [...] | None}`` as produced by
:func:`magicmouse.config.resolve_actions`.  Commands run off the input path:
``invoke`` returns immediately and completion is reported from a worker
thread, so a slow or hung action can never stall scrolling or the pointer.
Failures are logged, never raised.
"""

import subprocess
import threading

DEFAULT_RUN_TIMEOUT = 2.0


class ActionDispatcher:
    def __init__(self, dry_run=False, log=None):
        self._dry_run = dry_run
        self._log = log or (lambda message: None)

    def invoke(self, spec):
        """Run a resolved action spec; returns True when a command was spawned."""
        name = spec.get("name", "custom")
        command = spec.get("command")
        if not command:
            return False
        self._log(f"action {name}: {command}")
        if self._dry_run:
            return False
        try:
            self._spawn(command)
        except Exception as error:  # noqa: BLE001 - the show must go on
            self._log(f"action {name} failed: {error}")
            return False
        return True

    def _spawn(self, command):
        proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        def reap():
            try:
                _out, stderr = proc.communicate(timeout=DEFAULT_RUN_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                _out, stderr = proc.communicate()
            if proc.returncode != 0:
                detail = (stderr or b"").decode("utf-8", "replace").strip()
                self._log(f"command {' '.join(command)} exited {proc.returncode}: {detail}")

        threading.Thread(target=reap, daemon=True).start()
