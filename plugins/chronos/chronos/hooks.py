"""
Chronos Hooks: session lifecycle only.

There are deliberately no ``pre_tool_call`` / ``post_tool_call`` hooks: a ``post_tool_call`` hook
cannot change tool results, so the old error "advisory" never reached the model. The guidance on when
to branch lives in the ``chronos_branch`` tool description instead.
"""

from __future__ import annotations

from typing import Any

from chronos.manager import ChronosManager


class ChronosHooks:
    def __init__(self, manager: ChronosManager):
        self.manager = manager

    def on_session_start(self, **kwargs: Any) -> None:
        """Prune stale chronos worktrees for this project and make sure a baseline checkpoint exists."""
        engine = self.manager.engine_for_session_or_none()
        if engine is None:
            return
        engine.isolation.prune_all(keep=[s.worktree_path for s in engine.active_sessions.values()])
        if len(engine.dag.nodes) == 1 and engine.isolation.get_head_commit():
            engine.checkpoint(title="Session Start Checkpoint", description="Clean state at session start.")

    def on_session_finalize(self, **kwargs: Any) -> None:
        """Session torn down: remove the worktrees this process created."""
        self.manager.cleanup_all()
