"""Shared test helpers."""

import subprocess
import sys
from pathlib import Path

# Make `hermes_constants` importable when the tests are run from inside the plugin directory.
for _parent in Path(__file__).resolve().parents:
    if (_parent / "hermes_constants.py").exists():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=str(cwd), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def make_repo(root, files):
    """Create a git repository at ``root`` with one commit containing ``files``."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "initial")
    return root


CALC_FILES = {
    "calc.py": "def add(a, b): return a - b\n",
    "test_calc.py": (
        "import unittest\nfrom calc import add\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_add(self): self.assertEqual(add(2, 3), 5)\n"
        "if __name__ == '__main__': unittest.main()\n"
    ),
}
