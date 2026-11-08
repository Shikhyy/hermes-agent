"""Plugin surface: registration, per-call workspace resolution, no model-supplied command, no tool hooks."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _util import CALC_FILES, make_repo

PLUGIN_INIT = Path(__file__).resolve().parent.parent / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("chronos_plugin_entry", PLUGIN_INIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    def __init__(self, cwd="", settings=None):
        self.cwd = cwd
        self.settings = settings or {}
        self.tools = {}
        self.hooks = {}
        self.dispatched = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def session_cwd(self):
        return self.cwd

    def dispatch_tool(self, name, args, **kw):
        self.dispatched.append((name, args))
        proc = subprocess.run(args["command"], shell=True, cwd=args["workdir"], capture_output=True, text=True, check=False)
        return json.dumps({"output": proc.stdout + proc.stderr, "exit_code": proc.returncode})

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (schema, handler)

    def register_hook(self, name, callback, **kw):
        self.hooks[name] = callback


class TestPlugin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.home = self.base / "home"
        self._env = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_registers_five_tools_and_only_lifecycle_hooks(self):
        ctx = FakeCtx()
        load_plugin().register(ctx)
        self.assertEqual(
            sorted(ctx.tools),
            ["chronos_branch", "chronos_evaluate", "chronos_merge", "chronos_rewind", "chronos_status"],
        )
        self.assertEqual(sorted(ctx.hooks), ["on_session_finalize", "on_session_start"])
        self.assertNotIn("post_tool_call", ctx.hooks)
        self.assertNotIn("pre_tool_call", ctx.hooks)

    def test_model_cannot_supply_a_verifier_command(self):
        ctx = FakeCtx()
        load_plugin().register(ctx)
        for name, (schema, _) in ctx.tools.items():
            self.assertNotIn("verifier_command", json.dumps(schema), name)

    def test_workspace_is_resolved_per_call(self):
        ws_a = make_repo(self.base / "a", CALC_FILES)
        ws_b = make_repo(self.base / "b", {"other.py": "x = 1\n"})
        ctx = FakeCtx(cwd=str(ws_a))
        load_plugin().register(ctx)
        status = ctx.tools["chronos_status"][1]

        # Seed a checkpoint in project A, then switch the session's cwd to project B.
        ctx.hooks["on_session_start"]()
        a_nodes = json.loads(status({}))["total_nodes"]
        ctx.cwd = str(ws_b)
        b_nodes = json.loads(status({}))["total_nodes"]
        ctx.cwd = str(ws_a)

        self.assertEqual(a_nodes, 2)  # root + session-start checkpoint
        self.assertEqual(b_nodes, 1)  # project B has its own, untouched DAG
        self.assertEqual(json.loads(status({}))["total_nodes"], 2)

    def test_no_session_cwd_is_an_error_not_a_guess(self):
        ctx = FakeCtx(cwd="")
        load_plugin().register(ctx)
        out = json.loads(ctx.tools["chronos_status"][1]({}))
        self.assertIn("error", out)

    def test_branch_runs_operator_command_via_terminal_tool_and_merges(self):
        ws = make_repo(self.base / "ws", CALC_FILES)
        ctx = FakeCtx(cwd=str(ws), settings={"verifier_command": "python3 -m unittest test_calc.py"})
        load_plugin().register(ctx)
        out = json.loads(ctx.tools["chronos_branch"][1](
            {"hypotheses": [{"title": "fix", "files": {"calc.py": "def add(a, b): return a + b\n"}}]},
            task_id="t1",
        ))
        self.assertTrue(out["auto_merged"], out)
        self.assertEqual(ctx.dispatched[0][0], "terminal")
        self.assertEqual(ctx.dispatched[0][1]["command"], "python3 -m unittest test_calc.py")
        self.assertIn("return a + b", (ws / "calc.py").read_text())

    def test_branch_in_non_git_directory_errors_and_does_not_init(self):
        plain = self.base / "plain"
        plain.mkdir()
        ctx = FakeCtx(cwd=str(plain))
        load_plugin().register(ctx)
        out = json.loads(ctx.tools["chronos_branch"][1]({"hypotheses": [{"title": "x", "files": {"a.py": "x"}}]}))
        self.assertIn("not a git repository", out["error"])
        self.assertFalse((plain / ".git").exists())

    def test_finalize_removes_live_worktrees(self):
        ws = make_repo(self.base / "ws", CALC_FILES)
        ctx = FakeCtx(cwd=str(ws), settings={"verifier_command": "python3 -m unittest test_calc.py"})
        load_plugin().register(ctx)
        ctx.tools["chronos_branch"][1](
            {"hypotheses": [{"title": "wrong", "files": {"calc.py": "def add(a, b): return 0\n"}}], "auto_merge_best": False}
        )
        def live_worktrees():
            found = (self.home / "plugin-data" / "chronos").glob("*/worktrees/branch-*")
            return [p for p in found if p.is_dir()]  # the glob also matches the .chronos-owned marker files

        self.assertEqual(len(live_worktrees()), 1)
        ctx.hooks["on_session_finalize"]()
        self.assertEqual(live_worktrees(), [])


if __name__ == "__main__":
    unittest.main()
