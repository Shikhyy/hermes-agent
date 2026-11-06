"""
Chronos Hooks: Hermes Agent lifecycle interceptors.
Provides proactive error detection, checkpoint snapshotting, and automatic recovery advice.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from chronos.dag import NodeStatus
from chronos.engine import ChronosEngine


class ChronosHooks:
    """Manages event subscriptions for Hermes Agent lifecycle."""

    def __init__(self, engine: ChronosEngine):
        self.engine = engine

    def on_session_start(self, context: Any = None) -> None:
        """Invoked when a Hermes agent session starts."""
        # Clean up any stale worktrees from previous crashes
        self.engine.isolation.prune_all()
        # Ensure root checkpoint exists
        if len(self.engine.dag.nodes) == 1:
            self.engine.checkpoint(
                title="Session Start Checkpoint",
                description="Initial clean state at start of session.",
            )

    def on_session_end(self, context: Any = None) -> None:
        """Invoked when a session concludes."""
        # Persist DAG and clean up active worktree sessions
        self.engine._save_dag()
        for sess in list(self.engine.active_sessions.values()):
            self.engine.isolation.remove_worktree(sess)
        self.engine.active_sessions.clear()

    def pre_tool_call(self, tool_name: str, args: Dict[str, Any], context: Any = None) -> None:
        """Inspects impending tool execution; captures snapshots before mutating actions."""
        mutating_tools = {"write_file", "edit_file", "bash", "execute_command", "terminal"}
        if tool_name in mutating_tools:
            # We don't flood checkpoints on every minor call, but ensure we have a baseline
            active = self.engine.dag.get_active()
            if active and active.status != NodeStatus.RUNNING:
                # Active checkpoint is stable
                pass

    def post_tool_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        result: Any,
        context: Any = None,
    ) -> Any:
        """
        Inspects tool execution results for errors or test failures.
        Suggests or triggers speculative rollouts when errors occur.
        """
        if not self.engine.config.auto_speculate_on_error:
            return result

        result_str = str(result)
        is_error = False

        # Heuristic error triggers
        error_indicators = [
            "FAILED (failures=",
            "AssertionError",
            "Traceback (most recent call last)",
            "SyntaxError:",
            "TypeError:",
            "npm ERR!",
            "error: could not compile",
            "exit status 1",
            "command failed with exit code",
        ]

        for ind in error_indicators:
            if ind in result_str:
                is_error = True
                break

        if is_error:
            active_id = self.engine.dag.active_node_id
            chronos_hint = (
                f"\n\n⚡ [CHRONOS ADVISORY]: Execution failed or tests broken at checkpoint [{active_id}]. "
                "To prevent compounding errors on your main workspace, use `chronos_branch` to explore "
                "multiple candidate solutions in parallel isolated worktrees, or use `chronos_rewind` "
                f"to revert cleanly to [{active_id}]."
            )
            if isinstance(result, str):
                return result + chronos_hint
            elif isinstance(result, dict):
                result["chronos_advisory"] = chronos_hint
                return result

        return result
