"""Engine: file-key validation, rollout/merge selection, refusals that protect uncommitted work."""

import os
import tempfile
import unittest
from pathlib import Path

from _util import CALC_FILES, git, make_repo
from chronos.config import ChronosConfig
from chronos.engine import ChronosEngine, validate_file_map
from chronos.hermes_io import FileIOError
from chronos.isolation import MergeError

VERIFIER = "python3 -m unittest test_calc.py"


class FakeIO:
    """Stand-in for Hermes' read_file/write_file. Records every call; `deny` simulates the core write guard."""

    def __init__(self, deny=()):
        self.deny = set(deny)
        self.calls = []

    def prepare(self, path):
        self.calls.append(("read", Path(path)))

    def write(self, path, content):
        path = Path(path)
        self.calls.append(("write", path))
        if path.name in self.deny:
            raise FileIOError(f"write_file refused {path}: protected path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))

    def writes(self):
        return [p for kind, p in self.calls if kind == "write"]


class TestValidateFileMap(unittest.TestCase):
    def test_accepts_relative_paths(self):
        self.assertEqual(validate_file_map({"src/a.py": "x", "./b.py": "y"}), {"src/a.py": "x", "b.py": "y"})

    def test_rejects_escapes(self):
        bad = [
            "/etc/passwd", "C:\\Windows\\evil.txt", "\\\\server\\share\\f", "../outside.txt",
            "a/../../outside.txt", ".git/config", "sub/.GIT/hooks/pre-commit", "", "  ", "a\x00b",
        ]
        for key in bad:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validate_file_map({key: "x"})

    def test_rejects_non_string_content_and_bad_shapes(self):
        with self.assertRaises(ValueError):
            validate_file_map({"a.py": 5})
        with self.assertRaises(ValueError):
            validate_file_map(["a.py"])


class EngineCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.workspace = make_repo(self.base / "ws", CALC_FILES)
        self.engine = ChronosEngine(
            self.workspace,
            config=ChronosConfig(verifier_command=VERIFIER),
            storage_root=self.base / "state",
            file_io=FakeIO(),
        )

    def tearDown(self):
        self._tmp.cleanup()


class TestRollout(EngineCase):
    def test_best_passing_hypothesis_is_merged(self):
        summary = self.engine.auto_rollout([
            {"title": "wrong", "files": {"calc.py": "def add(a, b): return a * b\n"}},
            {"title": "right", "files": {"calc.py": "def add(a, b): return a + b\n"}},
        ])
        self.assertEqual(summary["total_branches_explored"], 2)
        self.assertTrue(summary["auto_merged"])
        self.assertIsNone(summary["merge_error"])
        self.assertEqual(summary["best_branch"]["title"], "right")
        self.assertIn("return a + b", (self.workspace / "calc.py").read_text())

    def test_verifier_byproducts_do_not_block_or_pollute_the_merge(self):
        # A baseline test run leaves untracked __pycache__ in the workspace; the verifier will leave its
        # own inside the worktree. Neither may end up in (or break) the merge.
        self.engine._verifier().verify_workspace(self.workspace)
        self.assertTrue((self.workspace / "__pycache__").exists())

        summary = self.engine.auto_rollout([{"title": "right", "files": {"calc.py": "def add(a, b): return a + b\n"}}])

        self.assertTrue(summary["auto_merged"], summary["merge_error"])
        self.assertNotIn("__pycache__", summary["best_branch"]["diff"])
        self.assertIn("return a + b", (self.workspace / "calc.py").read_text())

    def test_no_passing_branch_means_no_merge(self):
        summary = self.engine.auto_rollout([{"title": "wrong", "files": {"calc.py": "def add(a, b): return 0\n"}}])
        self.assertFalse(summary["auto_merged"])
        self.assertIsNone(summary["best_branch"])
        self.assertIn("a - b", (self.workspace / "calc.py").read_text())

    def test_bad_file_key_fails_before_any_worktree_exists(self):
        with self.assertRaises(ValueError):
            self.engine.auto_rollout([
                {"title": "ok", "files": {"calc.py": "x"}},
                {"title": "evil", "files": {"../../escape.txt": "x"}},
            ])
        self.assertEqual(len(self.engine.active_sessions), 0)
        self.assertFalse((self.base / "escape.txt").exists())

    def test_symlink_in_worktree_cannot_redirect_a_write(self):
        outside = self.base / "outside"
        outside.mkdir()
        node, session = self.engine.spawn_branch("sym")
        os.symlink(outside, session.worktree_path / "link")
        with self.assertRaises(ValueError):
            self.engine.apply_modifications_to_branch(node.id, {"link/pwned.txt": "x"})
        self.assertFalse((outside / "pwned.txt").exists())

    def test_unverifiable_project_never_passes(self):
        bare = make_repo(self.base / "bare", {"readme.txt": "no tests here\n"})
        engine = ChronosEngine(bare, config=ChronosConfig(), storage_root=self.base / "state2")
        summary = engine.auto_rollout([{"title": "x", "files": {"readme.txt": "changed\n"}}])
        self.assertFalse(summary["auto_merged"])
        self.assertEqual(summary["branches"][0]["status"], "failed")


class TestMergeSafety(EngineCase):
    def test_merge_without_live_session_is_refused_and_keeps_uncommitted_work(self):
        node, _ = self.engine.spawn_branch("b")
        self.engine.apply_modifications_to_branch(node.id, {"calc.py": "def add(a, b): return a + b\n"})
        self.engine.evaluate_branch(node.id)
        (self.workspace / "scratch.txt").write_text("uncommitted\n")
        (self.workspace / "calc.py").write_text("# my local edit\n")

        self.engine.active_sessions.clear()  # session ended / process restarted
        with self.assertRaises(MergeError):
            self.engine.merge_branch(node.id)

        self.assertEqual((self.workspace / "calc.py").read_text(), "# my local edit\n")
        self.assertEqual((self.workspace / "scratch.txt").read_text(), "uncommitted\n")

    def test_failed_apply_leaves_workspace_untouched(self):
        node, _ = self.engine.spawn_branch("b")
        self.engine.apply_modifications_to_branch(node.id, {"calc.py": "def add(a, b): return a + b\n"})
        self.engine.evaluate_branch(node.id)
        (self.workspace / "calc.py").write_text("def add(a, b): return 'diverged'\n")

        with self.assertRaises(MergeError):
            self.engine.merge_branch(node.id)
        self.assertEqual((self.workspace / "calc.py").read_text(), "def add(a, b): return 'diverged'\n")

    def test_only_passed_branches_merge(self):
        node, _ = self.engine.spawn_branch("b")
        self.engine.apply_modifications_to_branch(node.id, {"calc.py": "def add(a, b): return 0\n"})
        self.engine.evaluate_branch(node.id)
        with self.assertRaises(MergeError):
            self.engine.merge_branch(node.id)


class TestMergeGuard(EngineCase):
    """A merge writes only model-supplied files, only through Hermes' file tools, and is all-or-nothing."""

    def _passing_branch(self, files):
        node, _ = self.engine.spawn_branch("b")
        self.engine.apply_modifications_to_branch(node.id, files)
        self.assertTrue(self.engine.evaluate_branch(node.id).passed)
        return node

    FIX = "def add(a, b): return a + b\n"

    def test_every_write_goes_through_the_file_tools_with_absolute_workspace_paths(self):
        node = self._passing_branch({"calc.py": self.FIX})
        self.engine.merge_branch(node.id)
        io = self.engine.file_io
        self.assertEqual(io.calls, [("read", self.workspace.resolve() / "calc.py"), ("write", self.workspace.resolve() / "calc.py")])
        self.assertIn("a + b", (self.workspace / "calc.py").read_text())

    def test_new_files_are_written_without_a_read(self):
        node = self._passing_branch({"calc.py": self.FIX, "extra/new.py": "x = 1\n"})
        self.engine.merge_branch(node.id)
        kinds = [(kind, p.name) for kind, p in self.engine.file_io.calls]
        self.assertIn(("write", "new.py"), kinds)
        self.assertNotIn(("read", "new.py"), kinds)

    def test_verifier_byproducts_in_the_worktree_are_never_written(self):
        node = self._passing_branch({"calc.py": self.FIX})
        session = self.engine.active_sessions[node.id]
        (session.worktree_path / "junk.log").write_text("by-product\n")
        (session.worktree_path / "test_calc.py").write_text("# tampered by a test run\n")
        self.engine.merge_branch(node.id)
        self.assertEqual([p.name for p in self.engine.file_io.writes()], ["calc.py"])
        self.assertFalse((self.workspace / "junk.log").exists())
        self.assertIn("unittest", (self.workspace / "test_calc.py").read_text())

    def test_a_guard_refusal_aborts_the_merge_and_restores_earlier_writes(self):
        node = self._passing_branch({"calc.py": self.FIX, "new_a.py": "a = 1\n", ".env": "SECRET=1\n"})
        self.engine.file_io = FakeIO(deny={".env"})
        before = (self.workspace / "calc.py").read_text()

        with self.assertRaises(MergeError) as ctx:
            self.engine.merge_branch(node.id)

        self.assertIn("protected path", str(ctx.exception))
        self.assertEqual((self.workspace / "calc.py").read_text(), before)  # overwritten file restored
        self.assertFalse((self.workspace / "new_a.py").exists())  # newly created file removed
        self.assertFalse((self.workspace / ".env").exists())
        self.assertEqual(self.engine.dag.nodes[node.id].status.value, "passed")  # not recorded as merged
        self.assertIn(node.id, self.engine.active_sessions)

    def test_merge_is_refused_when_no_file_tool_is_available(self):
        node = self._passing_branch({"calc.py": self.FIX})
        self.engine.file_io = None
        before = (self.workspace / "calc.py").read_text()
        with self.assertRaises(MergeError):
            self.engine.merge_branch(node.id)
        self.assertEqual((self.workspace / "calc.py").read_text(), before)

    def test_auto_rollout_reports_the_refusal_instead_of_merging(self):
        self.engine.file_io = FakeIO(deny={"calc.py"})
        summary = self.engine.auto_rollout([{"title": "fix", "files": {"calc.py": self.FIX}}])
        self.assertFalse(summary["auto_merged"])
        self.assertIn("protected path", summary["merge_error"])
        self.assertIn("a - b", (self.workspace / "calc.py").read_text())


class TestSessionScopedCleanup(EngineCase):
    def test_cleanup_removes_only_the_owners_worktrees(self):
        _, sa = self.engine.spawn_branch("a", owner="s1")
        b, sb = self.engine.spawn_branch("b", owner="s2")
        self.engine.cleanup_sessions(owner="s1")
        self.assertFalse(sa.worktree_path.exists())
        self.assertTrue(sb.worktree_path.exists())
        self.assertEqual(list(self.engine.active_sessions), [b.id])


class TestRewind(EngineCase):
    def test_rewind_refuses_dirty_workspace(self):
        root = self.engine.dag.root_id
        (self.workspace / "calc.py").write_text("# dirty\n")
        with self.assertRaises(MergeError):
            self.engine.rewind_to(root)
        self.assertEqual((self.workspace / "calc.py").read_text(), "# dirty\n")

    def test_rewind_to_checkpoint_commit(self):
        root = self.engine.dag.root_id
        (self.workspace / "calc.py").write_text("def add(a, b): return a + b\n")
        git(self.workspace, "commit", "-qam", "fix")
        self.engine.rewind_to(root)
        self.assertIn("a - b", (self.workspace / "calc.py").read_text())
        self.assertIn("Rewound", self.engine.dag.get_active().title)


class TestStorageLocation(EngineCase):
    def test_nothing_is_written_inside_the_project(self):
        self.engine.auto_rollout([{"title": "x", "files": {"calc.py": "def add(a, b): return a + b\n"}}])
        names = sorted(p.name for p in self.workspace.iterdir() if p.name != "__pycache__")
        self.assertEqual(names, [".git", "calc.py", "test_calc.py"])  # no .hermes dir, no worktrees


if __name__ == "__main__":
    unittest.main()
