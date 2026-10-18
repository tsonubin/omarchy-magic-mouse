import unittest

from magicmouse.actions import ActionDispatcher


class RecordingRunner:
    def __init__(self, returncode=0, stderr=""):
        self.calls = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, command):
        self.calls.append(list(command))
        return self.returncode, self.stderr


class ActionDispatcherTest(unittest.TestCase):
    def test_builtin_command_runs(self):
        runner = RecordingRunner()
        log = []
        dispatcher = ActionDispatcher(runner=runner, log=log.append)
        spec = {"name": "workspace_next", "command": ["hyprctl", "dispatch", "workspace", "e+1"]}
        self.assertTrue(dispatcher.invoke(spec))
        self.assertEqual(runner.calls, [["hyprctl", "dispatch", "workspace", "e+1"]])
        self.assertIn("action workspace_next", log[0])

    def test_none_action_is_a_no_op(self):
        runner = RecordingRunner()
        dispatcher = ActionDispatcher(runner=runner, log=lambda m: None)
        self.assertFalse(dispatcher.invoke({"name": "none", "command": None}))
        self.assertEqual(runner.calls, [])

    def test_custom_command(self):
        runner = RecordingRunner()
        dispatcher = ActionDispatcher(runner=runner, log=lambda m: None)
        dispatcher.invoke({"name": "custom", "command": ["notify-send", "zap"]})
        self.assertEqual(runner.calls, [["notify-send", "zap"]])

    def test_dry_run_never_runs(self):
        runner = RecordingRunner()
        log = []
        dispatcher = ActionDispatcher(runner=runner, dry_run=True, log=log.append)
        self.assertFalse(dispatcher.invoke({"name": "app_grid", "command": ["omarchy-shell"]}))
        self.assertEqual(runner.calls, [])
        self.assertIn("app_grid", log[0])

    def test_failure_is_logged_not_raised(self):
        runner = RecordingRunner(returncode=1, stderr="boom")
        log = []
        dispatcher = ActionDispatcher(runner=runner, log=log.append)
        self.assertTrue(dispatcher.invoke({"name": "x", "command": ["false"]}))
        self.assertTrue(any("exited 1" in line and "boom" in line for line in log))

    def test_exception_is_logged_not_raised(self):
        def broken(_command):
            raise OSError("nope")

        log = []
        dispatcher = ActionDispatcher(runner=broken, log=log.append)
        self.assertFalse(dispatcher.invoke({"name": "x", "command": ["true"]}))
        self.assertTrue(any("failed" in line for line in log))


if __name__ == "__main__":
    unittest.main()
