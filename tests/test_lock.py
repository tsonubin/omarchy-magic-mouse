"""Single-instance contract: a second daemon must fail fast, not fight.

Two daemons cannot share the grabbed input devices; the loser used to fail
every grab and retry forever. This runs the real CLI (dry-run needs no
device access) and checks the second instance's exit and message.
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def start_daemon(home):
    return subprocess.Popen(
        [sys.executable, "-m", "magicmouse.cli", "run", "--dry-run"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "HOME": home, "XDG_RUNTIME_DIR": home, "PYTHONPATH": REPO},
        cwd=REPO,
    )


class InstanceLockTest(unittest.TestCase):
    def test_second_instance_exits_with_hint(self):
        home = tempfile.mkdtemp(prefix="mm-lock-test-")
        first = start_daemon(home)
        self.addCleanup(first.kill)
        time.sleep(1.0)  # let it take the lock and settle into the loop

        second = subprocess.run(
            [sys.executable, "-m", "magicmouse.cli", "run", "--dry-run"],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": home, "XDG_RUNTIME_DIR": home, "PYTHONPATH": REPO},
            cwd=REPO,
            timeout=30,
        )
        self.assertEqual(second.returncode, 1, second.stderr)
        self.assertIn("already managing the input devices", second.stderr)
        self.assertIn("omarchy-magic-mouse stop", second.stderr)
        self.assertIsNone(first.poll(), "the first instance must keep running")

        first.send_signal(signal.SIGTERM)
        first.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
