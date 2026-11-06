"""
Unit tests for Chronos Hermes Lifecycle Hooks.
"""

import tempfile
import unittest
from pathlib import Path

from chronos.engine import ChronosEngine
from chronos.hooks import ChronosHooks


class TestChronosHooks(unittest.TestCase):
    def test_post_tool_call_error_interception(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ChronosEngine(workspace_root=tmpdir)
            hooks = ChronosHooks(engine)

            # Normal success output
            clean_res = hooks.post_tool_call("bash", {"command": "ls"}, "file1.txt\nfile2.txt")
            self.assertEqual(clean_res, "file1.txt\nfile2.txt")

            # Error output
            err_res = hooks.post_tool_call(
                "bash",
                {"command": "pytest"},
                "FAILED (failures=2)\nAssertionError: expected 5 got 3",
            )
            self.assertIn("CHRONOS ADVISORY", err_res)
            self.assertIn("chronos_branch", err_res)


if __name__ == "__main__":
    unittest.main()
