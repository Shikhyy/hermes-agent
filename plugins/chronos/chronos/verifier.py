"""
Chronos Verifier: test-suite detection, execution and fitness scoring.

The verification command is NEVER supplied by the model. It is either the operator's
``verifier_command`` from ``config.yaml`` or an auto-detected default, always handled as an argv
list. Execution goes through a ``Runner``:

  * inside Hermes, ``terminal_runner`` dispatches Hermes' own ``terminal`` tool, so the user's
    terminal backend and approval policy apply;
  * ``subprocess_runner`` (no shell, minimal environment) is used by the standalone CLI and tests.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from chronos.dag import NodeMetrics

_OUTPUT_CAP = 20000


@dataclass
class RunOutput:
    exit_code: int
    output: str
    timed_out: bool = False


# runner(argv, cwd, timeout_seconds) -> RunOutput
Runner = Callable[[list[str], Path, int], RunOutput]


def _minimal_env() -> dict:
    env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": os.environ.get("LANG", "C.UTF-8")}
    for key in ("HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "USERPROFILE"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def subprocess_runner(argv: list[str], cwd: Path, timeout: int) -> RunOutput:
    """Run argv with no shell, no stdin and a minimal environment."""
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            shell=False,
            env=_minimal_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout if isinstance(exc.stdout, str) else ""
        return RunOutput(-1, partial[-_OUTPUT_CAP:], timed_out=True)
    except OSError as exc:
        return RunOutput(-2, str(exc))
    return RunOutput(proc.returncode, proc.stdout[-_OUTPUT_CAP:])


def terminal_runner(dispatch: Callable[..., str], **dispatch_kwargs: Any) -> Runner:
    """Runner that executes through Hermes' ``terminal`` tool (``ctx.dispatch_tool``)."""

    def run(argv: list[str], cwd: Path, timeout: int) -> RunOutput:
        raw = dispatch(
            "terminal",
            {"command": shlex.join(argv), "workdir": str(cwd), "timeout": int(timeout)},
            **dispatch_kwargs,
        )
        try:
            data = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (ValueError, TypeError):
            return RunOutput(-2, str(raw)[-_OUTPUT_CAP:])
        if not isinstance(data, dict):
            return RunOutput(-2, str(raw)[-_OUTPUT_CAP:])
        output = str(data.get("output") or data.get("error") or "")
        code = data.get("exit_code")
        if not isinstance(code, int):
            code = -2 if data.get("error") else 0
        timed_out = "timed out" in output.lower() and code != 0
        return RunOutput(code, output[-_OUTPUT_CAP:], timed_out=timed_out)

    return run


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
    """Detects, executes and scores test suites inside isolated worktrees."""

    def __init__(
        self,
        custom_argv: Optional[list[str]] = None,
        timeout_seconds: int = 60,
        runner: Optional[Runner] = None,
    ):
        self.custom_argv = custom_argv
        self.timeout_seconds = timeout_seconds
        self.runner: Runner = runner or subprocess_runner

    def detect_verifier_argv(self, directory: Path) -> Optional[list[str]]:
        """The configured argv, else a heuristic default for the project, else None."""
        if self.custom_argv:
            return list(self.custom_argv)
        if (directory / "pytest.ini").exists() or (directory / "pyproject.toml").exists():
            return ["pytest", "-q"]
        if (directory / "tests").exists() or any(directory.glob("test_*.py")):
            return ["python3", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"]
        if (directory / "package.json").exists():
            return ["npm", "test", "--if-present"]
        if (directory / "Cargo.toml").exists():
            return ["cargo", "test", "--quiet"]
        if (directory / "go.mod").exists():
            return ["go", "test", "./..."]
        return None

    def verify_workspace(self, directory: Path) -> VerificationResult:
        """Run verification in ``directory`` and score the outcome."""
        argv = self.detect_verifier_argv(directory)
        if argv is None:
            # Nothing to verify against: an unverifiable branch must never look like a pass.
            return VerificationResult(
                passed=False, exit_code=-3, score=0.0, tests_passed=0, tests_failed=0, duration_ms=0,
                stdout="", stderr="No test suite detected. Set `verifier_command` for the chronos plugin in config.yaml.",
                command="",
            )

        start = time.time()
        run = self.runner(argv, directory, self.timeout_seconds)
        duration_ms = int((time.time() - start) * 1000)
        command = shlex.join(argv)

        if run.timed_out:
            return VerificationResult(
                False, -1, 0.0, 0, 1, duration_ms, run.output,
                f"Execution timed out after {self.timeout_seconds} seconds.", command,
            )
        if run.exit_code < 0:
            return VerificationResult(False, run.exit_code, 0.0, 0, 1, duration_ms, "", run.output, command)

        passed_count, failed_count = self._parse_test_counts(run.output)
        score = self._compute_score(run.exit_code, passed_count, failed_count, duration_ms)
        return VerificationResult(
            passed=(run.exit_code == 0),
            exit_code=run.exit_code,
            score=score,
            tests_passed=passed_count,
            tests_failed=failed_count,
            duration_ms=duration_ms,
            stdout=run.output,
            stderr="",
            command=command,
        )

    def _parse_test_counts(self, output: str) -> tuple[int, int]:
        """Parse test counts from common test runners."""
        pytest_match = re.search(r"(\d+)\s+passed(?:,\s+(\d+)\s+failed)?", output)
        if pytest_match:
            return int(pytest_match.group(1)), int(pytest_match.group(2) or 0)

        ran_match = re.search(r"Ran (\d+) tests?", output)
        if ran_match:
            total = int(ran_match.group(1))
            fail_match = re.search(r"FAILED \((?:failures=(\d+))?(?:, )?(?:errors=(\d+))?\)", output)
            if fail_match:
                failed = int(fail_match.group(1) or 0) + int(fail_match.group(2) or 0)
                return max(0, total - failed), failed
            return total, 0

        jest_match = re.search(r"Tests:\s+(?:(\d+)\s+failed,\s+)?(\d+)\s+passed", output)
        if jest_match:
            return int(jest_match.group(2) or 0), int(jest_match.group(1) or 0)

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
        Multi-factor fitness score S in [0.0, 1.0]:
        60% test pass ratio, 25% exit code, 10% small diff, 5% speed.
        """
        if exit_code != 0 and (passed_tests + failed_tests) == 0:
            return 0.0

        total_tests = passed_tests + failed_tests
        pass_ratio = (passed_tests / total_tests) if total_tests > 0 else (1.0 if exit_code == 0 else 0.0)
        exit_score = 1.0 if exit_code == 0 else 0.0
        diff_score = 1.0 if lines_changed < 50 else max(0.2, 1.0 - (lines_changed - 50) / 500)
        speed_score = max(0.1, 1.0 - (duration_ms / (self.timeout_seconds * 1000)))

        score = 0.60 * pass_ratio + 0.25 * exit_score + 0.10 * diff_score + 0.05 * speed_score
        return round(min(1.0, max(0.0, score)), 4)
