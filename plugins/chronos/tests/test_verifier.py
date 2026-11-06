"""
Unit tests for Chronos Verifier and fitness scoring.
"""

import tempfile
import unittest
from pathlib import Path

from chronos.verifier import BranchVerifier


class TestBranchVerifier(unittest.TestCase):
    def setUp(self):
        self.verifier = BranchVerifier(timeout_seconds=10)

    def test_parse_pytest_output(self):
        output = "==== 5 passed, 1 failed in 0.23s ===="
        passed, failed = self.verifier._parse_test_counts(output)
        self.assertEqual(passed, 5)
        self.assertEqual(failed, 1)

    def test_parse_unittest_output(self):
        output = "FAILED (failures=2, errors=1)"
        ran_output = "Ran 10 tests in 0.05s\n" + output
        passed, failed = self.verifier._parse_test_counts(ran_output)
        self.assertEqual(passed, 7)
        self.assertEqual(failed, 3)

    def test_compute_score_all_pass(self):
        score = self.verifier._compute_score(
            exit_code=0,
            passed_tests=10,
            failed_tests=0,
            duration_ms=100,
            lines_changed=10,
        )
        self.assertGreater(score, 0.90)

    def test_compute_score_partial_failure(self):
        score = self.verifier._compute_score(
            exit_code=1,
            passed_tests=5,
            failed_tests=5,
            duration_ms=100,
            lines_changed=10,
        )
        self.assertLess(score, 0.50)

    def test_detect_command_python(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "pytest.ini").write_text("[pytest]\n")
            cmd = self.verifier.detect_verifier_command(p)
            self.assertIn("pytest", cmd)


if __name__ == "__main__":
    unittest.main()
