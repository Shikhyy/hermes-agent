"""
Unit tests for Chronos Workspace Isolation and worktree management.
"""

import tempfile
import unittest
from pathlib import Path

from chronos.isolation import WorkspaceIsolation


class TestWorkspaceIsolation(unittest.TestCase):
    def test_isolation_creation_and_prune(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            (workspace / "main.py").write_text("print('hello')\n")

            isolation = WorkspaceIsolation(workspace_root=workspace)
            session = isolation.create_isolated_worktree("test-branch-1")

            self.assertTrue(session.worktree_path.exists())
            self.assertTrue((session.worktree_path / "main.py").exists())

            # Modify file in isolated branch
            (session.worktree_path / "main.py").write_text("print('modified')\n")

            # Verify main workspace is untouched
            self.assertEqual((workspace / "main.py").read_text(), "print('hello')\n")

            # Apply to workspace
            isolation.apply_to_workspace(session)
            self.assertEqual((workspace / "main.py").read_text(), "print('modified')\n")

            # Prune
            isolation.remove_worktree(session)
            self.assertFalse(session.worktree_path.exists())


if __name__ == "__main__":
    unittest.main()
