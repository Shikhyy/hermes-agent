"""Verifier: argv-only execution, no shell, minimal env, terminal-tool runner, scoring."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from chronos.verifier import BranchVerifier, RunOutput, subprocess_runner, terminal_runner


class TestScoringAndParsing(unittest.TestCase):
    def setUp(self):
        self.verifier = BranchVerifier(timeout_seconds=10)

    def test_parse_pytest_output(self):
        self.assertEqual(self.verifier._parse_test_counts("==== 5 passed, 1 failed in 0.23s ===="), (5, 1))

    def test_parse_unittest_output(self):
        out = "Ran 10 tests in 0.05s\nFAILED (failures=2, errors=1)"
        self.assertEqual(self.verifier._parse_test_counts(out), (7, 3))

    def test_score_all_pass(self):
        self.assertGreater(self.verifier._compute_score(0, 10, 0, 100, 10), 0.90)

    def test_score_partial_failure(self):
        self.assertLess(self.verifier._compute_score(1, 5, 5, 100, 10), 0.50)

    def test_detect_argv_python_is_a_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "pytest.ini").write_text("[pytest]\n")
            self.assertEqual(self.verifier.detect_verifier_argv(Path(tmp)), ["pytest", "-q"])

    def test_no_detectable_suite_fails_instead_of_passing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.verifier.verify_workspace(Path(tmp))
        self.assertFalse(result.passed)
        self.assertEqual(result.score, 0.0)


class TestExecution(unittest.TestCase):
    def test_configured_argv_is_passed_to_runner_verbatim(self):
        seen = {}

        def runner(argv, cwd, timeout):
            seen.update(argv=argv, cwd=cwd, timeout=timeout)
            return RunOutput(0, "3 passed in 0.1s")

        verifier = BranchVerifier(custom_argv=["pytest", "-q", "tests/x; rm -rf ~"], timeout_seconds=9, runner=runner)
        with tempfile.TemporaryDirectory() as tmp:
            result = verifier.verify_workspace(Path(tmp))
        self.assertTrue(result.passed)
        self.assertEqual(seen["argv"], ["pytest", "-q", "tests/x; rm -rf ~"])  # one inert argument, not a shell line
        self.assertEqual(seen["timeout"], 9)

    def test_subprocess_runner_has_no_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "pwned"
            run = subprocess_runner([sys.executable, "-c", "print('ok')", f"; touch {marker}"], Path(tmp), 20)
            self.assertEqual(run.exit_code, 0)
            self.assertFalse(marker.exists())

    def test_subprocess_runner_env_is_minimal(self):
        os.environ["CHRONOS_TEST_SECRET"] = "hunter2"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                run = subprocess_runner(
                    [sys.executable, "-c", "import os; print(os.environ.get('CHRONOS_TEST_SECRET', 'absent'))"],
                    Path(tmp), 20,
                )
        finally:
            del os.environ["CHRONOS_TEST_SECRET"]
        self.assertEqual(run.output.strip(), "absent")

    def test_subprocess_runner_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = subprocess_runner([sys.executable, "-c", "import time; time.sleep(30)"], Path(tmp), 1)
        self.assertTrue(run.timed_out)

    def test_terminal_runner_dispatches_hermes_terminal_tool(self):
        calls = []

        def dispatch(name, args, **kw):
            calls.append((name, args, kw))
            return json.dumps({"output": "2 passed in 0.1s", "exit_code": 0})

        run = terminal_runner(dispatch, task_id="t1")(["pytest", "-q", "a b"], Path("/tmp"), 30)
        self.assertEqual(run.exit_code, 0)
        name, args, kw = calls[0]
        self.assertEqual(name, "terminal")
        self.assertEqual(args["command"], "pytest -q 'a b'")  # shell-quoted from argv
        self.assertEqual(args["workdir"], "/tmp")
        self.assertEqual(args["timeout"], 30)
        self.assertEqual(kw, {"task_id": "t1"})

    def test_terminal_runner_reports_failure(self):
        def dispatch(name, args, **kw):
            return json.dumps({"output": "boom", "exit_code": 2})

        self.assertEqual(terminal_runner(dispatch)(["x"], Path("/tmp"), 5).exit_code, 2)


if __name__ == "__main__":
    unittest.main()
