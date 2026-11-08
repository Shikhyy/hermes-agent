#!/usr/bin/env python3
"""
Chronos Interactive Live Demo:
Demonstrates Speculative Tree-Search Reasoning, Multi-Branch Worktree Isolation,
Automated Test Verification, Candidate Auto-Merging, and Deterministic Rewind.
"""

import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chronos.config import ChronosConfig
from chronos.engine import ChronosEngine

VERIFIER = "python3 -m unittest test_auth.py"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=demo", "-c", "user.email=demo@example.invalid", *args],
        cwd=str(cwd), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def print_step(title: str):
    print("\n" + "=" * 70)
    print(f"  ⚡ {title}")
    print("=" * 70)


def main():
    print("""
   ██████╗██╗  ██╗██████╗  ██████╗ ███╗   ██╗ ██████╗ ███████╗
  ██╔════╝██║  ██║██╔══██╗██╔═══██╗████╗  ██║██╔═══██╗██╔════╝
  ██║     ███████║██████╔╝██║   ██║██╔██╗ ██║██║   ██║███████╗
  ██║     ██╔══██║██╔══██╗██║   ██║██║╚██╗██║██║   ██║╚════██║
  ╚██████╗██║  ██║██║  ██║╚██████╔╝██║ ╚████║╚██████╔╝███████║
   ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝ ╚══════╝
   Speculative Tree-Search Reasoning & Rewind for Hermes Agent
    """)

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "demo_repo"
        workspace.mkdir()

        # Step 1: Create a buggy repository
        print_step("STEP 1: Setting up Buggy Production Codebase")
        auth_file = workspace / "auth.py"
        auth_file.write_text(
            'def parse_auth_header(header: str) -> str:\n'
            '    # BUG: Assumes exactly "Bearer <token>" without checking parts length\n'
            '    parts = header.split(" ")\n'
            '    token = parts[1]  # Raises IndexError on malformed or empty header!\n'
            '    return token.strip()\n'
        )

        test_file = workspace / "test_auth.py"
        test_file.write_text(
            'import unittest\n'
            'from auth import parse_auth_header\n\n'
            'class TestAuthHeader(unittest.TestCase):\n'
            '    def test_valid_bearer(self):\n'
            '        self.assertEqual(parse_auth_header("Bearer secret_jwt_xyz"), "secret_jwt_xyz")\n\n'
            '    def test_malformed_header_no_space(self):\n'
            '        self.assertEqual(parse_auth_header("BearerSecret"), "")\n\n'
            '    def test_empty_header(self):\n'
            '        self.assertEqual(parse_auth_header(""), "")\n\n'
            'if __name__ == "__main__":\n'
            '    unittest.main()\n'
        )
        print("📁 Workspace files initialized: 'auth.py' and 'test_auth.py'")

        # Chronos only works inside an existing git repository, so the demo makes its own throwaway one.
        git(workspace, "init", "-q")
        git(workspace, "add", "-A")
        git(workspace, "commit", "-q", "-m", "buggy baseline")

        # Initialize Chronos Engine (state and worktrees live outside the project directory).
        # The verifier command comes from configuration, never from the model.
        engine = ChronosEngine(
            workspace_root=workspace,
            config=ChronosConfig(verifier_command=VERIFIER),
            storage_root=Path(tmpdir) / "chronos-state",
        )
        print(f"🌿 Initial Checkpoint Created: [{engine.dag.active_node_id}]")

        # Step 2: Show initial failing tests
        print_step("STEP 2: Running Baseline Test Suite (Fails on IndexErrors)")
        eval_before = engine._verifier().verify_workspace(workspace)
        print(f"❌ Test Status: FAILED (Passed: {eval_before.tests_passed}, Failed: {eval_before.tests_failed})")
        print(f"   Fitness Score: {eval_before.score:.2f}")

        # Step 3: Trigger Chronos Speculative MCTS Rollout
        print_step("STEP 3: Triggering Chronos Speculative MCTS Rollout across 3 Isolated Branches")
        print("Spawning 3 parallel isolated worktrees...")

        hypotheses = [
            {
                "title": "Hypothesis A: Catch Exception & return None (Type violation)",
                "description": "Naive try/except block returning None instead of string",
                "files": {
                    "auth.py": (
                        'def parse_auth_header(header: str) -> str:\n'
                        '    try:\n'
                        '        return header.split(" ")[1]\n'
                        '    except Exception:\n'
                        '        return None  # Breaks str return contract!\n'
                    )
                },
            },
            {
                "title": "Hypothesis B: Strict Regex Parser with missing edge-case handling",
                "description": "Regex matching only alphanumeric tokens",
                "files": {
                    "auth.py": (
                        'import re\n'
                        'def parse_auth_header(header: str) -> str:\n'
                        '    m = re.match(r"^Bearer\\s+([a-zA-Z0-9]+)$", header)\n'
                        '    return m.group(1) if m else None\n'
                    )
                },
            },
            {
                "title": "Hypothesis C: Defensive bounds checking with empty string fallback",
                "description": "Checks header string, splits with maxsplit=1, validates prefix",
                "files": {
                    "auth.py": (
                        'def parse_auth_header(header: str) -> str:\n'
                        '    if not header or not isinstance(header, str):\n'
                        '        return ""\n'
                        '    parts = header.strip().split(" ", 1)\n'
                        '    if len(parts) == 2 and parts[0].lower() == "bearer":\n'
                        '        return parts[1].strip()\n'
                        '    return ""\n'
                    )
                },
            },
        ]

        start_time = time.time()
        rollout_result = engine.auto_rollout(hypotheses=hypotheses, auto_merge_best=True)
        duration = time.time() - start_time

        print(f"⏱️  Explored {rollout_result['total_branches_explored']} candidate branches in {duration:.2f}s")
        for b in rollout_result["branches"]:
            status_symbol = "✅" if b["status"] == "passed" else "❌"
            print(f"  {status_symbol} [{b['node_id']}] {b['title']}")
            print(f"      Score: {b['score']:.2f} | Tests: {b['tests_passed']} passed, {b['tests_failed']} failed")

        # Step 4: Show the live Decision DAG
        print_step("STEP 4: Live Chronos Decision DAG")
        print(engine.dag.render_ascii_tree())

        # Step 5: Verify the winning branch in the actual workspace
        print_step("STEP 5: Verifying Workspace After Automatic Merge of Winning Branch")
        eval_after = engine._verifier().verify_workspace(workspace)
        print(f"✅ Workspace Test Status: PASSED (Passed: {eval_after.tests_passed}, Failed: {eval_after.tests_failed})")
        print(f"   Fitness Score: {eval_after.score:.2f}")
        print("\n📄 Verified auth.py contents:")
        print((workspace / "auth.py").read_text())

        # Step 6: Instant Rewind Demo
        print_step("STEP 6: Deterministic Time-Warp Rewind Demonstration")
        root_id = engine.dag.root_id
        # Rewind refuses to discard uncommitted work, so commit the merged fix first.
        git(workspace, "add", "-A")
        git(workspace, "commit", "-q", "-m", "apply verified fix")
        print(f"Rewinding workspace back to root checkpoint [{root_id}]...")
        engine.rewind_to(root_id)
        print("⏪ Rewind completed!")
        print(engine.dag.render_ascii_tree())
        print("\n✨ Chronos Demo Finished Successfully!")


if __name__ == "__main__":
    main()
