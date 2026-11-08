"""
Chronos Configuration.

Settings come ONLY from the operator's Hermes ``config.yaml`` (the keys declared in this plugin's
``config_schema``, read through ``ctx.get_config``). Nothing is read from the project being edited
and there are no environment-variable overrides: a cloned repository must never be able to choose
what command the host runs.
"""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional

PLUGIN_DATA_NAME = "chronos"

_MAX_BRANCHES_CAP = 8
_MAX_TIMEOUT_CAP = 600


def _as_int(value: Any, default: int, lo: int, hi: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


@dataclass
class ChronosConfig:
    max_branches: int = 3
    verifier_command: str = "auto"
    timeout_seconds: int = 60

    @classmethod
    def from_getter(cls, get: Optional[Callable[..., Any]]) -> ChronosConfig:
        """Build from ``ctx.get_config``-style ``get(key, default)``; bad values fall back to defaults."""
        if get is None:
            return cls()
        defaults = cls()

        def read(key: str, default: Any) -> Any:
            try:
                return get(key, default)
            except Exception:
                return default

        command = read("verifier_command", defaults.verifier_command)
        if not isinstance(command, str) or not command.strip():
            command = defaults.verifier_command
        return cls(
            max_branches=_as_int(read("max_branches", defaults.max_branches), defaults.max_branches, 1, _MAX_BRANCHES_CAP),
            verifier_command=command.strip(),
            timeout_seconds=_as_int(
                read("timeout_seconds", defaults.timeout_seconds), defaults.timeout_seconds, 1, _MAX_TIMEOUT_CAP
            ),
        )

    def verifier_argv(self) -> Optional[list[str]]:
        """The operator-configured verifier as an argv list, or None for auto-detection."""
        if self.verifier_command.lower() == "auto":
            return None
        argv = shlex.split(self.verifier_command)
        return argv or None


def project_key(workspace_root: Path | str) -> str:
    """Stable short hash of a project's resolved path (names its plugin-data directory)."""
    resolved = str(Path(workspace_root).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def storage_root_for(workspace_root: Path | str) -> Path:
    """``<HERMES_HOME>/plugin-data/chronos/<project-hash>/`` — never inside the project."""
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "plugin-data" / PLUGIN_DATA_NAME / project_key(workspace_root)
