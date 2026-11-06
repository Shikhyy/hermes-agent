"""
Unit tests for Chronos Engine (MCTS speculative rollout & rewind).
"""

import tempfile
import unittest
from pathlib import Path

from chronos.engine import ChronosEngine


class TestChronosEngine(unittest.TestCase):
    def test_checkpoint_and_rewind(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            (workspace / "app.py").write_text("v1 = 1\n")

            engine = ChronosEngine(workspace_root=workspace)
            cp1 = engine.checkpoint("Initial Version")
            self.assertEqual(engine.dag.active_node_id, cp1.id)

            # Mutate workspace
            (workspace / "app.py").write_text("v2 = 2\n")
            cp2 = engine.checkpoint("Second Version")
            self.assertEqual(engine.dag.active_node_id, cp2.id)

            # Rewind to cp1
            engine.rewind_to(cp1.id)
            active = engine.dag.get_active()
            self.assertIn("Rewound", active.title)

    def test_auto_rollout_selection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            # Buggy file
            (workspace / "calc.py").write_text("def add(a, b): return a - b\n")
            (workspace / "test_calc.py").write_text(
                "import unittest\nfrom calc import add\n"
                "class TestCalc(unittest.TestCase):\n"
                "    def test_add(self): self.assertEqual(add(2, 3), 5)\n"
                "if __name__ == '__main__': unittest.main()\n"
            )

            engine = ChronosEngine(workspace_root=workspace)

            # Provide two hypotheses:
            # Hyp 1: Still broken
            # Hyp 2: Fixed!
            hypotheses = [
                {
                    "title": "Hypothesis 1: Multiply instead",
                    "description": "Try multiplying",
                    "files": {"calc.py": "def add(a, b): return a * b\n"},
                },
                {
                    "title": "Hypothesis 2: Correct addition",
                    "description": "Fix operator to +",
                    "files": {"calc.py": "def add(a, b): return a + b\n"},
                },
            ]

            summary = engine.auto_rollout(
                hypotheses=hypotheses,
                verifier_command="python3 -m unittest test_calc.py",
                auto_merge_best=True,
            )

            self.assertEqual(summary["total_branches_explored"], 2)
            self.assertTrue(summary["auto_merged"])
            self.assertEqual(summary["best_branch"]["title"], "Hypothesis 2: Correct addition")

            # Verify that main workspace calc.py was updated with winning fix!
            self.assertIn("return a + b", (workspace / "calc.py").read_text())


if __name__ == "__main__":
    unittest.main()
