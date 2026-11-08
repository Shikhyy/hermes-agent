"""Isolation: worktrees stay outside the project, deletion is marker-gated, merges never clobber."""

import tempfile
import unittest
from pathlib import Path

from _util import git, make_repo
from chronos.isolation import STORAGE_MARKER, IsolationError, MergeError, WorkspaceIsolation


class TestWorkspaceIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.workspace = make_repo(self.base / "ws", {"main.py": "print('hello')\n"})
        self.storage = self.base / "state" / "worktrees"
        self.iso = WorkspaceIsolation(self.workspace, self.storage)

    def tearDown(self):
        self._tmp.cleanup()

    def test_worktree_is_outside_project_and_merge_applies_patch(self):
        session = self.iso.create_isolated_worktree("abc123")
        self.assertIn(self.storage.resolve(), session.worktree_path.parents)
        self.assertNotIn(self.workspace.resolve(), session.worktree_path.parents)

        (session.worktree_path / "main.py").write_text("print('modified')\n")
        self.assertEqual((self.workspace / "main.py").read_text(), "print('hello')\n")

        self.assertTrue(self.iso.apply_to_workspace(session))
        self.assertEqual((self.workspace / "main.py").read_text(), "print('modified')\n")

        self.iso.remove_worktree(session)
        self.assertFalse(session.worktree_path.exists())
        self.assertTrue(self.storage.exists())

    def test_non_git_directory_is_refused_and_left_untouched(self):
        plain = self.base / "plain"
        plain.mkdir()
        (plain / "notes.txt").write_text("keep me\n")
        iso = WorkspaceIsolation(plain, self.base / "other-state")
        with self.assertRaises(IsolationError):
            iso.create_isolated_worktree("abc123")
        self.assertFalse((plain / ".git").exists())  # no `git init`
        self.assertEqual([p.name for p in plain.iterdir()], ["notes.txt"])

    def test_repo_without_commits_is_refused(self):
        empty = self.base / "empty"
        empty.mkdir()
        git(empty, "init", "-q")
        with self.assertRaises(IsolationError):
            WorkspaceIsolation(empty, self.base / "s2").create_isolated_worktree("abc123")

    def test_prune_only_removes_directories_chronos_created(self):
        session = self.iso.create_isolated_worktree("abc123")
        stranger = self.storage / "user-data"
        stranger.mkdir()
        (stranger / "important.txt").write_text("not ours\n")

        removed = self.iso.prune_all()

        self.assertEqual(removed, 1)
        self.assertFalse(session.worktree_path.exists())
        self.assertTrue((stranger / "important.txt").exists())
        self.assertTrue(self.storage.exists())
        self.assertTrue((self.storage / STORAGE_MARKER).exists())

    def test_prune_keeps_live_sessions(self):
        session = self.iso.create_isolated_worktree("abc123")
        self.assertEqual(self.iso.prune_all(keep=[session.worktree_path]), 0)
        self.assertTrue(session.worktree_path.exists())

    def test_prune_ignores_unmarked_storage_dir(self):
        other = self.base / "not-chronos"
        other.mkdir()
        (other / "branch-x").mkdir()
        (other / "branch-x" / "f").write_text("x")
        iso = WorkspaceIsolation(self.workspace, other)
        (other / STORAGE_MARKER).unlink()
        self.assertEqual(iso.prune_all(), 0)
        self.assertTrue((other / "branch-x" / "f").exists())

    def test_conflicting_merge_is_refused_and_workspace_unchanged(self):
        session = self.iso.create_isolated_worktree("abc123")
        (session.worktree_path / "main.py").write_text("print('from branch')\n")
        # Workspace diverges after the worktree was cut (uncommitted local edit).
        (self.workspace / "main.py").write_text("print('my uncommitted work')\n")

        with self.assertRaises(MergeError):
            self.iso.apply_to_workspace(session)
        self.assertEqual((self.workspace / "main.py").read_text(), "print('my uncommitted work')\n")


if __name__ == "__main__":
    unittest.main()
