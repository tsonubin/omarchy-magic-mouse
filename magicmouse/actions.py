"""Gesture action dispatch.

An action spec is ``{"name": ..., "command": [...] | None}`` as produced by
:func:`magicmouse.config.resolve_actions`.  Commands run out of the input
path's way as far as possible: failures are logged, never raised, so a broken
command cannot wedge scrolling.
"""

import subprocess

DEFAULT_RUN_TIMEOUT = 2.0


def _run_command(command):
    result = subprocess.run(
        command,
        timeout=DEFAULT_RUN_TIMEOUT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return result.returncode, result.stderr.decode("utf-8", "replace").strip()


class ActionDispatcher:
    def __init__(self, runner=None, dry_run=False, log=None):
        self._runner = runner or _run_command
        self._dry_run = dry_run
        self._log = log or (lambda message: None)

    def invoke(self, spec):
        """Run a resolved action spec; returns True when a command ran."""
        name = spec.get("name", "custom")
        command = spec.get("command")
        if not command:
            return False
        self._log(f"action {name}: {command}")
        if self._dry_run:
            return False
        try:
            returncode, stderr = self._runner(command)
        except Exception as error:  # noqa: BLE001 - the show must go on
            self._log(f"action {name} failed: {error}")
            return False
        if returncode != 0:
            self._log(f"action {name} exited {returncode}: {stderr}")
        return True
