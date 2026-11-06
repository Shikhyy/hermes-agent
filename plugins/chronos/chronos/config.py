"""
Chronos Configuration: Settings loader and schema validator.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class ChronosConfig:
    auto_speculate_on_error: bool = True
    max_branches: int = 3
    verifier_command: str = "auto"
    timeout_seconds: int = 60
    storage_dir: Optional[Path] = None

    @classmethod
    def load(cls, workspace_root: Path | str) -> ChronosConfig:
        root = Path(workspace_root).resolve()
        config_path = root / ".hermes" / "chronos" / "config.json"

        data: Dict[str, Any] = {}
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass

        # Environment variable overrides
        auto_spec = os.environ.get("CHRONOS_AUTO_SPECULATE")
        if auto_spec is not None:
            data["auto_speculate_on_error"] = auto_spec.lower() in ("true", "1", "yes")

        max_b = os.environ.get("CHRONOS_MAX_BRANCHES")
        if max_b is not None:
            try:
                data["max_branches"] = int(max_b)
            except ValueError:
                pass

        cmd = os.environ.get("CHRONOS_VERIFIER_CMD")
        if cmd is not None:
            data["verifier_command"] = cmd

        timeout = os.environ.get("CHRONOS_TIMEOUT")
        if timeout is not None:
            try:
                data["timeout_seconds"] = int(timeout)
            except ValueError:
                pass

        storage = data.get("storage_dir")
        storage_path = Path(storage) if storage else None

        return cls(
            auto_speculate_on_error=data.get("auto_speculate_on_error", True),
            max_branches=data.get("max_branches", 3),
            verifier_command=data.get("verifier_command", "auto"),
            timeout_seconds=data.get("timeout_seconds", 60),
            storage_dir=storage_path,
        )

    def save(self, workspace_root: Path | str) -> None:
        root = Path(workspace_root).resolve()
        config_path = root / ".hermes" / "chronos" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "auto_speculate_on_error": self.auto_speculate_on_error,
            "max_branches": self.max_branches,
            "verifier_command": self.verifier_command,
            "timeout_seconds": self.timeout_seconds,
            "storage_dir": str(self.storage_dir) if self.storage_dir else None,
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
