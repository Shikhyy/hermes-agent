"""Plugin surface: registration, per-call workspace, Hermes-guarded merges, session-scoped cleanup."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _util import CALC_FILES, make_repo

PLUGIN_INIT = Path(__file__).resolve().parent.parent / "__init__.py"
PKG = "chronos_plugin_pkg"
VERIFIER = "python3 -m unittest test_calc.py"


def load_plugin():
    """Load the plugin the way Hermes does: as a package, so its relative imports resolve."""
    spec = importlib.util.spec_from_file_location(PKG, PLUGIN_INIT, submodule_search_locations=[str(PLUGIN_INIT.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    """A ctx whose dispatch_tool mimics Hermes' terminal / read_file / write_file (incl. a write denylist)."""

    def __init__(self, cwd="", settings=None):
        self.cwd = cwd
        self.settings = settings or {}
        self.tools = {}
        self.hooks = {}
        self.dispatched = []
        self._read = set()

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def session_cwd(self):
        return self.cwd

    def dispatch_tool(self, name, args, **kw):
        self.dispatched.append((name, dict(args), kw))
        if name == "terminal":
            proc = subprocess.run(args["command"], shell=True, cwd=args["workdir"], capture_output=True, text=True, check=False)
            return json.dumps({"output": proc.stdout + proc.stderr, "exit_code": proc.returncode})
        path = Path(args["path"])
        if name == "read_file":
            if not path.exists():
                return json.dumps({"error": "not found"})
            self._read.add(str(path))
            return json.dumps({"content": path.read_text()})
        if name == "write_file":
            if path.name == ".env":
                return json.dumps({"error": "write denied: protected path"})
            if path.exists() and str(path) not in self._read:
                return json.dumps({"error": "read the file first"})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"])
            self._read.add(str(path))
            return json.dumps({"success": True})
        return json.dumps({"error": f"unknown tool {name}"})

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (schema, handler)

    def register_hook(self, name, callback, **kw):
        self.hooks[name] = callback

    def names(self, tool):
        return [a for n, a, _ in self.dispatched if n == tool]


class PluginCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.home = self.base / "home"
        self._env = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def live_worktrees(self):
        found = (self.home / "plugin-data" / "chronos").glob("*/worktrees/branch-*")
        return [p for p in found if p.is_dir()]  # the glob also matches the .chronos-owned marker files


class TestPlugin(PluginCase):
    def test_registers_five_tools_and_only_lifecycle_hooks(self):
        ctx = FakeCtx()
        load_plugin().register(ctx)
        self.assertEqual(
            sorted(ctx.tools),
            ["chronos_branch", "chronos_evaluate", "chronos_merge", "chronos_rewind", "chronos_status"],
        )
        self.assertEqual(sorted(ctx.hooks), ["on_session_finalize", "on_session_start"])

    def test_plugin_uses_relative_imports_and_does_not_touch_sys_path(self):
        before = list(sys.path)
        load_plugin()
        self.assertEqual(sys.path, before)
        self.assertIn(f"{PKG}.chronos.manager", sys.modules)  # resolved inside the plugin package

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

        ctx.hooks["on_session_start"]()
        a_nodes = json.loads(status({}))["total_nodes"]
        ctx.cwd = str(ws_b)
        b_nodes = json.loads(status({}))["total_nodes"]
        ctx.cwd = str(ws_a)

        self.assertEqual(a_nodes, 2)  # root + session-start checkpoint
        self.assertEqual(b_nodes, 1)  # project B has its own, untouched DAG
        self.assertEqual(json.loads(status({}))["total_nodes"], 2)

    def test_engines_are_keyed_by_hermes_home_as_well_as_cwd(self):
        ws = make_repo(self.base / "ws", CALC_FILES)
        ctx = FakeCtx(cwd=str(ws))
        load_plugin().register(ctx)
        status = ctx.tools["chronos_status"][1]
        ctx.hooks["on_session_start"]()
        self.assertEqual(json.loads(status({}))["total_nodes"], 2)

        with mock.patch.dict(os.environ, {"HERMES_HOME": str(self.base / "other-profile")}):
            self.assertEqual(json.loads(status({}))["total_nodes"], 1)  # other profile: its own plugin-data
        self.assertEqual(json.loads(status({}))["total_nodes"], 2)

    def test_no_session_cwd_is_an_error_not_a_guess(self):
        ctx = FakeCtx(cwd="")
        load_plugin().register(ctx)
        self.assertIn("error", json.loads(ctx.tools["chronos_status"][1]({})))

    def test_branch_in_non_git_directory_errors_and_does_not_init(self):
        plain = self.base / "plain"
        plain.mkdir()
        ctx = FakeCtx(cwd=str(plain))
        load_plugin().register(ctx)
        out = json.loads(ctx.tools["chronos_branch"][1]({"hypotheses": [{"title": "x", "files": {"a.py": "x"}}]}))
        self.assertIn("not a git repository", out["error"])
        self.assertFalse((plain / ".git").exists())


class TestGuardedMerge(PluginCase):
    def _ctx(self, files=None):
        ws = make_repo(self.base / "ws", {**CALC_FILES, **(files or {})})
        ctx = FakeCtx(cwd=str(ws), settings={"verifier_command": VERIFIER})
        load_plugin().register(ctx)
        return ws, ctx

    def test_merge_writes_through_read_file_then_write_file_with_the_session_ids(self):
        ws, ctx = self._ctx()
        out = json.loads(ctx.tools["chronos_branch"][1](
            {"hypotheses": [{"title": "fix", "files": {"calc.py": "def add(a, b): return a + b\n"}}]},
            task_id="t1", session_id="s1",
        ))
        self.assertTrue(out["auto_merged"], out)
        self.assertEqual(ctx.names("terminal")[0]["command"], VERIFIER)
        self.assertEqual([a["path"] for a in ctx.names("read_file")], [str(ws.resolve() / "calc.py")])
        self.assertEqual([a["path"] for a in ctx.names("write_file")], [str(ws.resolve() / "calc.py")])
        order = [n for n, _, _ in ctx.dispatched if n != "terminal"]
        self.assertEqual(order, ["read_file", "write_file"])  # read before write, as write_file requires
        self.assertTrue(all(kw == {"task_id": "t1", "session_id": "s1"} for _, _, kw in ctx.dispatched))
        self.assertIn("return a + b", (ws / "calc.py").read_text())

    def test_a_write_the_core_guard_denies_blocks_the_whole_merge(self):
        ws, ctx = self._ctx()
        out = json.loads(ctx.tools["chronos_branch"][1]({"hypotheses": [{
            "title": "fix + dotenv",
            "files": {"calc.py": "def add(a, b): return a + b\n", ".env": "TOKEN=abc\n"},
        }]}))
        self.assertFalse(out["auto_merged"])
        self.assertIn("protected path", out["merge_error"])
        self.assertIn("a - b", (ws / "calc.py").read_text())  # restored
        self.assertFalse((ws / ".env").exists())

    def test_merge_refuses_without_a_dispatcher(self):
        ws = make_repo(self.base / "ws", CALC_FILES)
        ctx = FakeCtx(cwd=str(ws), settings={"verifier_command": VERIFIER})
        ctx.dispatch_tool = None  # a host with no tool dispatch: chronos must not write the workspace itself
        load_plugin().register(ctx)
        out = json.loads(ctx.tools["chronos_branch"][1]({"hypotheses": [{"title": "x", "files": {"calc.py": "x = 1\n"}}]}))
        self.assertFalse(out.get("auto_merged"))
        self.assertIn("a - b", (ws / "calc.py").read_text())


class TestSessionScopedFinalize(PluginCase):
    def test_finalize_removes_only_that_sessions_worktrees(self):
        ws = make_repo(self.base / "ws", CALC_FILES)
        ctx = FakeCtx(cwd=str(ws), settings={"verifier_command": VERIFIER})
        load_plugin().register(ctx)
        wrong = {"title": "wrong", "files": {"calc.py": "def add(a, b): return 0\n"}}
        for sid in ("s1", "s2"):
            ctx.tools["chronos_branch"][1]({"hypotheses": [wrong], "auto_merge_best": False}, session_id=sid)
        self.assertEqual(len(self.live_worktrees()), 2)

        ctx.hooks["on_session_finalize"](session_id="s1")
        self.assertEqual(len(self.live_worktrees()), 1)

        ctx.hooks["on_session_finalize"](session_id="unknown")  # nothing owned: nothing removed
        ctx.hooks["on_session_finalize"]()  # no id at all: nothing removed
        self.assertEqual(len(self.live_worktrees()), 1)

        ctx.hooks["on_session_finalize"](session_id="s2")
        self.assertEqual(self.live_worktrees(), [])


if __name__ == "__main__":
    unittest.main()
