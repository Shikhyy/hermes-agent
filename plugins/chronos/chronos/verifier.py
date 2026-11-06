"""
Chronos Verifier Engine: Automated Test Suite Detection, Execution & Fitness Scoring.
Calculates multi-factor fitness scores across speculative candidate branches.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from chronos.dag import NodeMetrics


@dataclass
class VerificationResult:
    passed: bool
    exit_code: int
    score: float
    tests_passed: int
    tests_failed: int
    duration_ms: int
    stdout: str
    stderr: str
    command: str

    def to_metrics(self, lines_added: int = 0, lines_removed: int = 0) -> NodeMetrics:
        return NodeMetrics(
            score=self.score,
            tests_passed=self.tests_passed,
            tests_failed=self.tests_failed,
            duration_ms=self.duration_ms,
            exit_code=self.exit_code,
            lines_added=lines_added,
            lines_removed=lines_removed,
        )


class BranchVerifier:
    """Detects, executes, and evaluates test suites across isolated workspaces."""

    def __init__(self, custom_command: Optional[str] = None, timeout_seconds: int = 60):
        self.custom_command = custom_command
        self.timeout_seconds = timeout_seconds

    def detect_verifier_command(self, directory: Path) -> str:
        """Heuristically detects test commands based on workspace configuration."""
        if self.custom_command and self.custom_command != "auto":
            return self.custom_command

        # Python pytest or unittest
        if (directory / "pytest.ini").exists() or (directory / "pyproject.toml").exists():
            return "pytest -q"
        if (directory / "tests").exists() or any(directory.glob("test_*.py")):
            return "python3 -m unittest discover -s . -p 'test_*.py'"

        # JavaScript / TypeScript
        if (directory / "package.json").exists():
            return "npm test --if-present"

        # Rust
        if (directory / "Cargo.toml").exists():
            return "cargo test --quiet"

        # Go
        if (directory / "go.mod").exists():
            return "go test ./..."

        return "echo 'No test suite detected; verification skipped'"

    def verify_workspace(self, directory: Path, verifier_command: Optional[str] = None) -> VerificationResult:
        """Executes verification in the given directory and scores the outcome."""
        cmd = verifier_command or self.detect_verifier_command(directory)
        start_time = time.time()

        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(directory),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout_seconds,
            )
            duration_ms = int((time.time() - start_time) * 1000)
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return VerificationResult(
                passed=False,
                exit_code=-1,
                score=0.0,
                tests_passed=0,
                tests_failed=1,
                duration_ms=duration_ms,
                stdout=e.stdout or "" if isinstance(e.stdout, str) else "",
                stderr="Execution timed out after " + str(self.timeout_seconds) + " seconds.",
                command=cmd,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return VerificationResult(
                passed=False,
                exit_code=-2,
                score=0.0,
                tests_passed=0,
                tests_failed=1,
                duration_ms=duration_ms,
                stdout="",
                stderr=str(e),
                command=cmd,
            )

        passed_count, failed_count = self._parse_test_counts(stdout + "\n" + stderr)
        score = self._compute_score(exit_code, passed_count, failed_count, duration_ms)

        return VerificationResult(
            passed=(exit_code == 0),
            exit_code=exit_code,
            score=score,
            tests_passed=passed_count,
            tests_failed=failed_count,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            command=cmd,
        )

    def _parse_test_counts(self, output: str) -> Tuple[int, int]:
        """Parses test counts from common test runners."""
        # pytest: "5 passed, 1 failed in 0.12s"
        pytest_match = re.search(r"(\d+)\s+passed(?:,\s+(\d+)\s+failed)?", output)
        if pytest_match:
            passed = int(pytest_match.group(1))
            failed = int(pytest_match.group(2) or 0)
            return passed, failed

        # unittest: "Ran 4 tests ... OK" or "FAILED (failures=1, errors=2)"
        ran_match = re.search(r"Ran (\d+) tests?", output)
        if ran_match:
            total = int(ran_match.group(1))
            fail_match = re.search(r"FAILED \((?:failures=(\d+))?(?:, )?(?:errors=(\d+))?\)", output)
            if fail_match:
                fails = int(fail_match.group(1) or 0)
                errors = int(fail_match.group(2) or 0)
                failed = fails + errors
                passed = max(0, total - failed)
                return passed, failed
            return total, 0

        # npm/jest: "Tests: 2 failed, 8 passed, 10 total"
        jest_match = re.search(r"Tests:\s+(?:(\d+)\s+failed,\s+)?(\d+)\s+passed", output)
        if jest_match:
            failed = int(jest_match.group(1) or 0)
            passed = int(jest_match.group(2) or 0)
            return passed, failed

        return 0, 0

    def _compute_score(
        self,
        exit_code: int,
        passed_tests: int,
        failed_tests: int,
        duration_ms: int,
        lines_changed: int = 0,
    ) -> float:
        """
        Calculates multi-factor fitness score S in [0.0, 1.0].
        Weights:
          - Test Pass Ratio: 60%
          - Exit Code Success: 25%
          - Minimal Surgical Diff: 10%
          - Speed / Execution Time: 5%
        """
        if exit_code != 0 and (passed_tests + failed_tests) == 0:
            return 0.0

        # 1. Pass ratio
        total_tests = passed_tests + failed_tests
        pass_ratio = (passed_tests / total_tests) if total_tests > 0 else (1.0 if exit_code == 0 else 0.0)

        # 2. Exit code
        exit_score = 1.0 if exit_code == 0 else 0.0

        # 3. Diff cleanliness (penalizes massive diffs)
        diff_score = 1.0 if lines_changed < 50 else max(0.2, 1.0 - (lines_changed - 50) / 500)

        # 4. Speed score (faster is better)
        speed_score = max(0.1, 1.0 - (duration_ms / (self.timeout_seconds * 1000)))

        score = (
            0.60 * pass_ratio
            + 0.25 * exit_score
            + 0.10 * diff_score
            + 0.05 * speed_score
        )
        return round(min(1.0, max(0.0, score)), 4)
