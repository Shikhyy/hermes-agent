"""
ChronosManager: per-call workspace resolution.

A Hermes plugin is registered once per process but its tools and hooks run for many sessions, each
with its own working directory. The manager therefore never captures a directory at register time:
every call resolves the calling session's cwd (``ctx.session_cwd``) and returns the engine for that
project, creating it on first use.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from chronos.config import ChronosConfig
from chronos.engine import ChronosEngine
from chronos.verifier import Runner, subprocess_runner, terminal_runner


class ChronosError(RuntimeError):
    """The call cannot be served (no session working directory, etc.)."""


class ChronosManager:
    def __init__(
        self,
        get_config: Optional[Callable[..., Any]] = None,
        session_cwd: Optional[Callable[[], str]] = None,
        dispatch_tool: Optional[Callable[..., str]] = None,
    ):
        self._get_config = get_config
        self._session_cwd = session_cwd
        self._dispatch_tool = dispatch_tool
        self._engines: dict[str, ChronosEngine] = {}
        self._lock = threading.Lock()

    def load_config(self) -> ChronosConfig:
        return ChronosConfig.from_getter(self._get_config)

    def _runner(self, **dispatch_kwargs: Any) -> Runner:
        if self._dispatch_tool is None:
            return subprocess_runner
        return terminal_runner(self._dispatch_tool, **dispatch_kwargs)

    def _resolve_cwd(self) -> Optional[Path]:
        if self._session_cwd is None:
            return None
        try:
            cwd = (self._session_cwd() or "").strip()
        except Exception:
            return None
        if not cwd:
            return None
        path = Path(cwd).expanduser()
        return path.resolve() if path.is_dir() else None

    def engine_for_session_or_none(self, **dispatch_kwargs: Any) -> Optional[ChronosEngine]:
        cwd = self._resolve_cwd()
        if cwd is None:
            return None
        key = str(cwd)
        with self._lock:
            engine = self._engines.get(key)
            if engine is None:
                engine = ChronosEngine(workspace_root=cwd, config=self.load_config())
                self._engines[key] = engine
        # Fresh operator config and a runner bound to this call's session on every use.
        engine.config = self.load_config()
        engine.runner = self._runner(**self._dispatch_kwargs(dispatch_kwargs))
        return engine

    def engine_for_session(self, **dispatch_kwargs: Any) -> ChronosEngine:
        engine = self.engine_for_session_or_none(**dispatch_kwargs)
        if engine is None:
            raise ChronosError("Could not determine this session's working directory, so chronos cannot pick a project.")
        return engine

    @staticmethod
    def _dispatch_kwargs(kw: dict[str, Any]) -> dict[str, Any]:
        """Forward only the session identifiers the terminal tool understands."""
        return {k: v for k, v in kw.items() if k in ("task_id", "session_id") and v}

    def cleanup_all(self) -> None:
        with self._lock:
            engines = list(self._engines.values())
        for engine in engines:
            engine.cleanup_sessions()
