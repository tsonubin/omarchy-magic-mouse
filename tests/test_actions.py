"""Action dispatch contracts at the real subprocess boundary.

``invoke`` must never block the input path (a slow action must not freeze the
pointer), dry-run must not execute, and failures must be logged, never raised.
"""

import os
import tempfile
import time
import unittest

from magicmouse.actions import ActionDispatcher


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class ActionDispatcherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mm-actions-test-")
        self.marker = os.path.join(self.tmp, "ran")
        self.log = []
        self.dispatcher = ActionDispatcher(log=self.log.append)

    def touch_marker(self):
        return ["sh", "-c", f"touch {self.marker}"]

    def test_command_runs_and_invoke_returns_immediately(self):
        slow = ["sh", "-c", f"sleep 1; touch {self.marker}"]
        start = time.monotonic()
        self.assertTrue(self.dispatcher.invoke({"name": "custom", "command": slow}))
        self.assertLess(
            time.monotonic() - start,
            0.5,
            "invoke must not wait for the command: the input path would freeze",
        )
        self.assertTrue(wait_for(lambda: os.path.exists(self.marker)), "command must still run")

    def test_dry_run_and_none_never_execute(self):
        dry = ActionDispatcher(dry_run=True, log=self.log.append)
        dry.invoke({"name": "app_grid", "command": self.touch_marker()})
        self.dispatcher.invoke({"name": "none", "command": None})
        time.sleep(0.3)
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(any("app_grid" in line for line in self.log))

    def test_failures_are_logged_not_raised(self):
        self.dispatcher.invoke(
            {"name": "custom", "command": ["sh", "-c", "echo boom >&2; exit 3"]}
        )
        self.assertTrue(
            wait_for(lambda: any("exited 3" in line and "boom" in line for line in self.log)),
            self.log,
        )
        self.dispatcher.invoke({"name": "custom", "command": ["definitely-not-a-command-xyz"]})
        self.assertTrue(wait_for(lambda: any("failed" in line for line in self.log)), self.log)


if __name__ == "__main__":
    unittest.main()
