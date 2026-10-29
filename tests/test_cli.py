"""CLI contract: an explicit --config path must exist (no silent defaults)."""

import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CliContractTest(unittest.TestCase):
    def test_missing_explicit_config_is_an_error(self):
        home = tempfile.mkdtemp(prefix="mm-cli-test-")
        missing = os.path.join(home, "no-such-config.json")
        commands = (
            ("run",),
            ("simulate",),
            ("config", "--show"),  # --show reads; bare config creates it
        )
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, "-m", "magicmouse.cli", *command, "--config", missing],
                    capture_output=True,
                    text=True,
                    env={**os.environ, "HOME": home, "PYTHONPATH": REPO},
                    cwd=REPO,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("config file not found", result.stderr)

    def test_doctor_completes_when_systemctl_never_answers(self):
        home = tempfile.mkdtemp(prefix="mm-cli-wedge-")
        shim = os.path.join(home, "bin")
        os.makedirs(shim)
        with open(os.path.join(shim, "systemctl"), "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nsleep 60\n")
        os.chmod(os.path.join(shim, "systemctl"), 0o755)
        result = subprocess.run(
            [sys.executable, "-m", "magicmouse.cli", "doctor"],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": home, "PATH": shim + os.pathsep + os.environ["PATH"], "PYTHONPATH": REPO},
            cwd=REPO,
            timeout=30,
        )
        self.assertIn(
            "systemctl --user is not responding",
            result.stdout,
            "a wedged session bus must produce a hint, not a hang",
        )

    def test_simulate_rejects_backwards_timestamps(self):
        home = tempfile.mkdtemp(prefix="mm-cli-replay-")
        result = subprocess.run(
            [sys.executable, "-m", "magicmouse.cli", "simulate"],
            input="100 EV_REL REL_X 1\n50 EV_REL REL_X 2\n",
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": home, "PYTHONPATH": REPO},
            cwd=REPO,
            timeout=30,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("timestamps must not go backwards", result.stderr)

    def test_simulate_requires_redirected_stdin(self):
        import pty

        home = tempfile.mkdtemp(prefix="mm-cli-tty-")
        master, slave = pty.openpty()
        try:
            result = subprocess.run(
                [sys.executable, "-m", "magicmouse.cli", "simulate"],
                stdin=slave,
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": home, "PYTHONPATH": REPO},
                cwd=REPO,
                timeout=30,
            )
        finally:
            os.close(slave)
            os.close(master)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("stdin", result.stderr)


if __name__ == "__main__":
    unittest.main()
