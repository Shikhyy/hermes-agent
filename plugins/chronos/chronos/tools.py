"""
Chronos Tools: Model-facing tool schemas and execution handlers for Hermes Agent.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Tuple

from chronos.engine import ChronosEngine


def get_tool_definitions(engine: ChronosEngine) -> Dict[str, Tuple[Dict[str, Any], Callable]]:
    """Returns mapping of tool_name -> (schema, handler)."""

    # 1. chronos_branch
    branch_schema = {
        "name": "chronos_branch",
        "description": (
            "Spawns speculative solution branches in isolated git worktrees, applies candidate file "
            "modifications, runs verification suites, and scores candidates without mutating the active workspace."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hypotheses": {
                    "type": "array",
                    "description": "List of candidate hypotheses to test in parallel isolated branches.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Short title for this candidate approach."},
                            "description": {"type": "string", "description": "Technical hypothesis explanation."},
                            "files": {
                                "type": "object",
                                "description": "Mapping of relative filepaths to proposed file contents.",
                                "additionalProperties": {"type": "string"},
                            },
                        },
                        "required": ["title", "files"],
                    },
                },
                "verifier_command": {
                    "type": "string",
                    "description": "Optional custom command to verify branches (e.g. 'pytest', 'npm test'). Defaults to auto-detection.",
                },
                "auto_merge_best": {
                    "type": "boolean",
                    "description": "If true, automatically merges the highest-scoring passing branch into the active workspace.",
                    "default": True,
                },
            },
            "required": ["hypotheses"],
        },
    }

    def handle_branch(args: Dict[str, Any], context: Any = None) -> str:
        hypotheses = args.get("hypotheses", [])
        verifier_cmd = args.get("verifier_command")
        auto_merge = args.get("auto_merge_best", True)

        if not hypotheses:
            return json.dumps({"error": "No hypotheses provided for speculative branching."})

        result = engine.auto_rollout(
            hypotheses=hypotheses,
            verifier_command=verifier_cmd,
            auto_merge_best=auto_merge,
        )
        return json.dumps(result, indent=2)

    # 2. chronos_evaluate
    eval_schema = {
        "name": "chronos_evaluate",
        "description": "Evaluates a specific speculative branch against the test suite and returns fitness metrics and diff.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch_id": {"type": "string", "description": "The node ID of the branch to evaluate."},
                "verifier_command": {"type": "string", "description": "Optional test verification command override."},
            },
            "required": ["branch_id"],
        },
    }

    def handle_evaluate(args: Dict[str, Any], context: Any = None) -> str:
        branch_id = args.get("branch_id")
        if not branch_id:
            return json.dumps({"error": "branch_id is required."})

        try:
            res = engine.evaluate_branch(branch_id, verifier_command=args.get("verifier_command"))
            return json.dumps({
                "branch_id": branch_id,
                "passed": res.passed,
                "score": res.score,
                "tests_passed": res.tests_passed,
                "tests_failed": res.tests_failed,
                "duration_ms": res.duration_ms,
                "stdout": res.stdout[:500],
                "stderr": res.stderr[:500],
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})

    # 3. chronos_merge
    merge_schema = {
        "name": "chronos_merge",
        "description": "Merges a verified speculative branch into the active workspace and updates the DAG ledger.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch_id": {"type": "string", "description": "The node ID of the branch to merge."},
            },
            "required": ["branch_id"],
        },
    }

    def handle_merge(args: Dict[str, Any], context: Any = None) -> str:
        branch_id = args.get("branch_id")
        if not branch_id:
            return json.dumps({"error": "branch_id is required."})

        try:
            success = engine.merge_branch(branch_id)
            return json.dumps({
                "success": success,
                "merged_branch_id": branch_id,
                "active_node_id": engine.dag.active_node_id,
                "message": f"Branch [{branch_id}] successfully merged into active workspace.",
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    # 4. chronos_rewind
    rewind_schema = {
        "name": "chronos_rewind",
        "description": "Instantly rewinds the workspace and DAG state back to any historical node checkpoint.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_node_id": {
                    "type": "string",
                    "description": "The node ID to rewind back to.",
                },
            },
            "required": ["target_node_id"],
        },
    }

    def handle_rewind(args: Dict[str, Any], context: Any = None) -> str:
        target_id = args.get("target_node_id")
        if not target_id:
            return json.dumps({"error": "target_node_id is required."})

        try:
            success = engine.rewind_to(target_id)
            return json.dumps({
                "success": success,
                "rewound_to_node": target_id,
                "active_node_id": engine.dag.active_node_id,
                "message": f"Workspace successfully rewound to checkpoint [{target_id}].",
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    # 5. chronos_status
    status_schema = {
        "name": "chronos_status",
        "description": "Returns the complete Chronos decision DAG, active checkpoint, and rollout tree visualization.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    }

    def handle_status(args: Dict[str, Any], context: Any = None) -> str:
        return json.dumps(engine.get_status_summary(), indent=2)

    return {
        "chronos_branch": (branch_schema, handle_branch),
        "chronos_evaluate": (eval_schema, handle_evaluate),
        "chronos_merge": (merge_schema, handle_merge),
        "chronos_rewind": (rewind_schema, handle_rewind),
        "chronos_status": (status_schema, handle_status),
    }
