"""
Chronos Tools: model-facing tool schemas and handlers.

The workspace is resolved on EVERY call from the calling session's working directory (the plugin
registers once per process but serves many sessions). The model cannot choose a verification
command: that comes from the operator's config only.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Tuple

from .manager import ChronosManager


def _error(exc: Exception) -> str:
    return json.dumps({"error": str(exc)})


def get_tool_definitions(manager: ChronosManager) -> dict[str, tuple[dict[str, Any], Callable]]:
    """Returns mapping of tool_name -> (schema, handler)."""

    branch_schema = {
        "name": "chronos_branch",
        "description": (
            "Try several candidate fixes in isolated git worktrees outside the project: each hypothesis's "
            "files are written into its own worktree, the project's test suite is run there, and the "
            "candidates are scored. The active workspace is only changed if auto_merge_best is true and a "
            "candidate passes verification. Use after a fix attempt fails, instead of piling edits onto a "
            "broken tree. Requires a git repository with at least one commit."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hypotheses": {
                    "type": "array",
                    "description": "Candidate approaches to test in isolated worktrees.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Short title for this candidate approach."},
                            "description": {"type": "string", "description": "Technical hypothesis explanation."},
                            "files": {
                                "type": "object",
                                "description": (
                                    "Mapping of project-relative file paths to full proposed contents. "
                                    "Absolute paths, '..' and .git paths are rejected."
                                ),
                                "additionalProperties": {"type": "string"},
                            },
                        },
                        "required": ["title", "files"],
                    },
                },
                "auto_merge_best": {
                    "type": "boolean",
                    "description": "If true, merge the highest-scoring passing candidate into the workspace.",
                    "default": True,
                },
            },
            "required": ["hypotheses"],
        },
    }

    def handle_branch(args: dict[str, Any], **kw: Any) -> str:
        hypotheses = args.get("hypotheses") or []
        if not hypotheses:
            return json.dumps({"error": "No hypotheses provided for speculative branching."})
        try:
            engine = manager.engine_for_session(**kw)
            return json.dumps(
                engine.auto_rollout(
                    hypotheses=hypotheses,
                    auto_merge_best=bool(args.get("auto_merge_best", True)),
                    owner=manager.session_owner(kw),
                ),
                indent=2,
            )
        except Exception as exc:
            return _error(exc)

    eval_schema = {
        "name": "chronos_evaluate",
        "description": "Re-run verification for one live speculative branch and return fitness metrics.",
        "parameters": {
            "type": "object",
            "properties": {"branch_id": {"type": "string", "description": "Node ID of the branch to evaluate."}},
            "required": ["branch_id"],
        },
    }

    def handle_evaluate(args: dict[str, Any], **kw: Any) -> str:
        branch_id = args.get("branch_id")
        if not branch_id:
            return json.dumps({"error": "branch_id is required."})
        try:
            engine = manager.engine_for_session(**kw)
            res = engine.evaluate_branch(branch_id)
            return json.dumps({
                "branch_id": branch_id,
                "passed": res.passed,
                "score": res.score,
                "tests_passed": res.tests_passed,
                "tests_failed": res.tests_failed,
                "duration_ms": res.duration_ms,
                "stdout": res.stdout[-500:],
                "stderr": res.stderr[-500:],
            }, indent=2)
        except Exception as exc:
            return _error(exc)

    merge_schema = {
        "name": "chronos_merge",
        "description": (
            "Merge a passed speculative branch that still has a live worktree into the workspace as a patch. "
            "Refuses (changing nothing) if the branch has no live worktree, did not pass, or does not apply cleanly."
        ),
        "parameters": {
            "type": "object",
            "properties": {"branch_id": {"type": "string", "description": "Node ID of the branch to merge."}},
            "required": ["branch_id"],
        },
    }

    def handle_merge(args: dict[str, Any], **kw: Any) -> str:
        branch_id = args.get("branch_id")
        if not branch_id:
            return json.dumps({"error": "branch_id is required."})
        try:
            engine = manager.engine_for_session(**kw)
            changed = engine.merge_branch(branch_id)
            return json.dumps({
                "success": True,
                "changes_applied": changed,
                "merged_branch_id": branch_id,
                "active_node_id": engine.dag.active_node_id,
            })
        except Exception as exc:
            return _error(exc)

    rewind_schema = {
        "name": "chronos_rewind",
        "description": (
            "Check out the git commit recorded at a historical checkpoint. Refuses if the workspace has "
            "uncommitted changes."
        ),
        "parameters": {
            "type": "object",
            "properties": {"target_node_id": {"type": "string", "description": "Node ID to rewind to."}},
            "required": ["target_node_id"],
        },
    }

    def handle_rewind(args: dict[str, Any], **kw: Any) -> str:
        target_id = args.get("target_node_id")
        if not target_id:
            return json.dumps({"error": "target_node_id is required."})
        try:
            engine = manager.engine_for_session(**kw)
            engine.rewind_to(target_id)
            return json.dumps({"success": True, "rewound_to_node": target_id, "active_node_id": engine.dag.active_node_id})
        except Exception as exc:
            return _error(exc)

    status_schema = {
        "name": "chronos_status",
        "description": "Return the Chronos decision DAG, the active checkpoint and the rollout tree for this project.",
        "parameters": {"type": "object", "properties": {}},
    }

    def handle_status(args: dict[str, Any], **kw: Any) -> str:
        try:
            return json.dumps(manager.engine_for_session(**kw).get_status_summary(), indent=2)
        except Exception as exc:
            return _error(exc)

    return {
        "chronos_branch": (branch_schema, handle_branch),
        "chronos_evaluate": (eval_schema, handle_evaluate),
        "chronos_merge": (merge_schema, handle_merge),
        "chronos_rewind": (rewind_schema, handle_rewind),
        "chronos_status": (status_schema, handle_status),
    }
