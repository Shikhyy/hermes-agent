"""
Chronos Engine: Orchestrates MCTS speculative rollouts, branch verification,
automatic candidate selection, and deterministic rewinds.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from chronos.config import ChronosConfig
from chronos.dag import ChronosDAG, DAGNode, NodeMetrics, NodeStatus
from chronos.isolation import IsolationSession, WorkspaceIsolation
from chronos.verifier import BranchVerifier, VerificationResult


class ChronosEngine:
    """Core runtime engine for speculative execution and rollback in Hermes Agent."""

    def __init__(self, workspace_root: Path | str, config: Optional[ChronosConfig] = None):
        self.workspace_root = Path(workspace_root).resolve()
        self.config = config or ChronosConfig.load(self.workspace_root)
        self.isolation = WorkspaceIsolation(
            workspace_root=self.workspace_root,
            base_storage_dir=self.config.storage_dir,
        )
        self.verifier = BranchVerifier(
            custom_command=self.config.verifier_command,
            timeout_seconds=self.config.timeout_seconds,
        )

        self.dag_file = self.workspace_root / ".hermes" / "chronos" / "dag.json"
        if self.dag_file.exists():
            self.dag = ChronosDAG.load_from_file(self.dag_file)
        else:
            self.dag = ChronosDAG()
            self._save_dag()

        self.active_sessions: Dict[str, IsolationSession] = {}

    def _save_dag(self) -> None:
        self.dag.save_to_file(self.dag_file)

    def checkpoint(self, title: str, description: str = "") -> DAGNode:
        """Creates an instantaneous checkpoint of the current workspace state."""
        commit_sha = self.isolation.get_head_commit() if self.isolation.is_git_repo() else None
        node = self.dag.add_node(
            title=title,
            branch_name="main",
            parent_id=self.dag.active_node_id,
            description=description,
            status=NodeStatus.PASSED,
            commit_sha=commit_sha,
        )
        self.dag.set_active(node.id)
        self._save_dag()
        return node

    def spawn_branch(self, title: str, description: str = "") -> Tuple[DAGNode, IsolationSession]:
        """Creates a single speculative branch and isolated worktree."""
        parent_id = self.dag.active_node_id or self.dag.root_id
        node = self.dag.add_node(
            title=title,
            branch_name=f"spec-{len(self.dag.nodes)}",
            parent_id=parent_id,
            description=description,
            status=NodeStatus.RUNNING,
        )
        session = self.isolation.create_isolated_worktree(node.id)
        self.active_sessions[node.id] = session
        self._save_dag()
        return node, session

    def apply_modifications_to_branch(
        self,
        node_id: str,
        files: Dict[str, str],
    ) -> None:
        """Writes candidate file changes directly to the branch's isolated worktree."""
        session = self.active_sessions.get(node_id)
        if not session:
            raise ValueError(f"No active isolation session found for node '{node_id}'.")

        for rel_path, content in files.items():
            target_path = session.worktree_path / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)

    def evaluate_branch(
        self,
        node_id: str,
        verifier_command: Optional[str] = None,
    ) -> VerificationResult:
        """Runs the verifier suite inside the branch worktree and records results."""
        session = self.active_sessions.get(node_id)
        if not session:
            raise ValueError(f"No active session for node '{node_id}'.")

        diff = self.isolation.compute_diff(session)
        lines_added = diff.count("\n+") - diff.count("\n+++")
        lines_removed = diff.count("\n-") - diff.count("\n---")

        result = self.verifier.verify_workspace(
            directory=session.worktree_path,
            verifier_command=verifier_command or self.config.verifier_command,
        )

        status = NodeStatus.PASSED if result.passed else NodeStatus.FAILED
        metrics = result.to_metrics(lines_added=max(0, lines_added), lines_removed=max(0, lines_removed))

        self.dag.update_node(
            node_id=node_id,
            status=status,
            diff=diff,
            metrics=metrics,
            error_message=result.stderr if not result.passed else None,
        )
        self._save_dag()
        return result

    def auto_rollout(
        self,
        hypotheses: List[Dict[str, Any]],
        verifier_command: Optional[str] = None,
        auto_merge_best: bool = True,
    ) -> Dict[str, Any]:
        """
        Executes parallel or sequential speculative rollouts for candidate hypotheses.
        Hypothesis dict format:
          {
            "title": "Fix with Regex Validation",
            "description": "...",
            "files": {"src/parser.py": "..."}
          }
        """
        results: List[Dict[str, Any]] = []
        best_node: Optional[DAGNode] = None
        best_score = -1.0

        limit = min(len(hypotheses), self.config.max_branches)

        for i in range(limit):
            hyp = hypotheses[i]
            title = hyp.get("title", f"Hypothesis {i+1}")
            desc = hyp.get("description", "")
            files = hyp.get("files", {})

            node, session = self.spawn_branch(title=title, description=desc)
            if files:
                self.apply_modifications_to_branch(node.id, files)

            eval_res = self.evaluate_branch(node.id, verifier_command=verifier_command)
            results.append({
                "node_id": node.id,
                "title": title,
                "status": "passed" if eval_res.passed else "failed",
                "score": eval_res.score,
                "tests_passed": eval_res.tests_passed,
                "tests_failed": eval_res.tests_failed,
                "duration_ms": eval_res.duration_ms,
            })

            if eval_res.score > best_score:
                best_score = eval_res.score
                best_node = self.dag.nodes[node.id]

        merged = False
        if auto_merge_best and best_node and best_node.status == NodeStatus.PASSED:
            self.merge_branch(best_node.id)
            merged = True

        return {
            "total_branches_explored": len(results),
            "best_branch": best_node.to_dict() if best_node else None,
            "best_score": best_score,
            "auto_merged": merged,
            "branches": results,
            "dag_ascii": self.dag.render_ascii_tree(),
        }

    def merge_branch(self, node_id: str) -> bool:
        """Merges a passed speculative branch into the active workspace."""
        session = self.active_sessions.get(node_id)
        if not session:
            # Recreate session if needed
            session = self.isolation.create_isolated_worktree(node_id)

        node = self.dag.nodes.get(node_id)
        if not node:
            raise ValueError(f"Node '{node_id}' does not exist.")

        success = self.isolation.apply_to_workspace(session)
        if success:
            self.dag.update_node(node_id=node_id, status=NodeStatus.MERGED)
            self.dag.set_active(node_id)
            self._save_dag()

        # Clean up other child worktrees of the same parent
        parent_id = node.parent_id
        if parent_id:
            for sibling_id in self.dag.nodes[parent_id].children:
                if sibling_id != node_id and sibling_id in self.active_sessions:
                    sib_sess = self.active_sessions.pop(sibling_id)
                    self.isolation.remove_worktree(sib_sess)
                    self.dag.update_node(node_id=sibling_id, status=NodeStatus.PRUNED)

        return success

    def rewind_to(self, target_node_id: str) -> bool:
        """
        Rewinds workspace and active DAG pointer to a target historical node.
        """
        if target_node_id not in self.dag.nodes:
            raise ValueError(f"Target node '{target_node_id}' not found in DAG.")

        target_node = self.dag.nodes[target_node_id]

        # If commit SHA is present and git is available, checkout that commit
        if target_node.commit_sha and self.isolation.is_git_repo():
            self.isolation._run_git(["checkout", target_node.commit_sha])
        elif target_node.diff and self.isolation.is_git_repo():
            # Invert diff or reset
            self.isolation._run_git(["checkout", "HEAD"])

        # Mark rewind checkpoint
        rewind_node = self.dag.add_node(
            title=f"Rewound to [{target_node_id}] {target_node.title}",
            branch_name="main",
            parent_id=target_node_id,
            description=f"Deterministic rewind rollback to historical state {target_node_id}.",
            status=NodeStatus.REVERTED,
        )
        self.dag.set_active(rewind_node.id)
        self._save_dag()
        return True

    def get_status_summary(self) -> Dict[str, Any]:
        """Provides an aggregated summary for tools and the Desktop UI."""
        return {
            "root_id": self.dag.root_id,
            "active_node_id": self.dag.active_node_id,
            "total_nodes": len(self.dag.nodes),
            "active_worktrees": len(self.active_sessions),
            "ascii_tree": self.dag.render_ascii_tree(),
            "nodes": [n.to_dict() for n in self.dag.nodes.values()],
        }
