"""
Chronos Engine: orchestrates speculative rollouts, verification, safe merging and rewinds for ONE
project directory. State (DAG, worktrees) lives under
``<HERMES_HOME>/plugin-data/chronos/<project-hash>/``; the project itself is only ever touched by an
explicit, clean ``git apply`` merge (or a refused-if-dirty rewind).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Optional, Tuple

from chronos.config import ChronosConfig, storage_root_for
from chronos.dag import ChronosDAG, DAGNode, NodeStatus
from chronos.isolation import IsolationError, IsolationSession, MergeError, WorkspaceIsolation
from chronos.verifier import BranchVerifier, Runner, VerificationResult

MAX_FILES_PER_BRANCH = 200
MAX_FILE_BYTES = 2_000_000


def validate_file_map(files: Any) -> dict[str, str]:
    """
    Validate a model-supplied {relative_path: content} mapping BEFORE anything is written.

    Rejects absolute paths (POSIX or Windows), drive letters, ``..`` segments, anything inside a
    ``.git`` directory, NUL bytes, non-string content and oversized payloads.
    """
    if files in (None, {}):
        return {}
    if not isinstance(files, dict):
        raise ValueError("'files' must be an object mapping relative paths to file contents.")
    if len(files) > MAX_FILES_PER_BRANCH:
        raise ValueError(f"Too many files in one branch (max {MAX_FILES_PER_BRANCH}).")
    clean: dict[str, str] = {}
    for key, content in files.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("File keys must be non-empty strings.")
        if "\x00" in key:
            raise ValueError(f"Invalid file key {key!r}: NUL byte.")
        if not isinstance(content, str):
            raise ValueError(f"Content for {key!r} must be a string.")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"Content for {key!r} is too large (max {MAX_FILE_BYTES} bytes).")
        normalised = key.replace("\\", "/")
        if PurePosixPath(normalised).is_absolute() or PureWindowsPath(key).is_absolute() or PureWindowsPath(key).drive:
            raise ValueError(f"Absolute paths are not allowed: {key!r}.")
        parts = [p for p in normalised.split("/") if p not in ("", ".")]
        if not parts:
            raise ValueError(f"Invalid file key {key!r}.")
        if any(p == ".." for p in parts):
            raise ValueError(f"Path escapes the worktree: {key!r}.")
        if any(p.lower() == ".git" for p in parts):
            raise ValueError(f"Writing into .git is not allowed: {key!r}.")
        clean["/".join(parts)] = content
    return clean


class ChronosEngine:
    """Speculative execution and rollback engine for one project."""

    def __init__(
        self,
        workspace_root: Path | str,
        config: Optional[ChronosConfig] = None,
        runner: Optional[Runner] = None,
        storage_root: Optional[Path | str] = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.config = config or ChronosConfig()
        self.runner = runner
        self.storage_root = Path(storage_root) if storage_root else storage_root_for(self.workspace_root)
        self.isolation = WorkspaceIsolation(self.workspace_root, self.storage_root / "worktrees")

        self.dag_file = self.storage_root / "dag.json"
        if self.dag_file.exists():
            try:
                self.dag = ChronosDAG.load_from_file(self.dag_file)
            except Exception:
                self.dag = ChronosDAG()
                self._save_dag()
        else:
            self.dag = ChronosDAG()
            # Anchor the root checkpoint to the commit the project is at right now, so it can be rewound to.
            self.dag.nodes[self.dag.root_id].commit_sha = self.isolation.get_head_commit()
            self._save_dag()

        self.active_sessions: dict[str, IsolationSession] = {}

    # -- plumbing --------------------------------------------------------------------------

    def _save_dag(self) -> None:
        self.dag_file.parent.mkdir(parents=True, exist_ok=True)
        self.dag.save_to_file(self.dag_file)

    def _verifier(self) -> BranchVerifier:
        return BranchVerifier(
            custom_argv=self.config.verifier_argv(),
            timeout_seconds=self.config.timeout_seconds,
            runner=self.runner,
        )

    # -- checkpoints & branches ------------------------------------------------------------

    def checkpoint(self, title: str, description: str = "") -> DAGNode:
        """Record the current workspace commit as a checkpoint. Never mutates the workspace."""
        commit_sha = self.isolation.get_head_commit()
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

    def spawn_branch(self, title: str, description: str = "") -> tuple[DAGNode, IsolationSession]:
        """Create a speculative branch and its isolated worktree (raises IsolationError)."""
        self.isolation.require_repo()
        parent_id = self.dag.active_node_id or self.dag.root_id
        node = self.dag.add_node(
            title=title,
            branch_name=f"spec-{len(self.dag.nodes)}",
            parent_id=parent_id,
            description=description,
            status=NodeStatus.RUNNING,
        )
        try:
            session = self.isolation.create_isolated_worktree(node.id)
        except IsolationError as exc:
            self.dag.update_node(node_id=node.id, status=NodeStatus.FAILED, error_message=str(exc))
            self._save_dag()
            raise
        self.active_sessions[node.id] = session
        self._save_dag()
        return node, session

    def apply_modifications_to_branch(self, node_id: str, files: dict[str, str]) -> None:
        """Write validated candidate files into the branch's worktree (and nowhere else)."""
        session = self.active_sessions.get(node_id)
        if not session:
            raise ValueError(f"No active isolation session found for node '{node_id}'.")
        clean = validate_file_map(files)
        root = session.worktree_path.resolve()

        targets: list[tuple[Path, str]] = []
        for rel, content in clean.items():
            target = root / rel
            resolved = target.resolve()
            # resolve() follows symlinks, so a symlink inside the worktree pointing outside is caught here.
            if resolved != root and root not in resolved.parents:
                raise ValueError(f"Path resolves outside the worktree: {rel!r}.")
            if target.is_symlink():
                raise ValueError(f"Refusing to write through a symlink: {rel!r}.")
            if ".git" in (p.lower() for p in resolved.relative_to(root).parts):
                raise ValueError(f"Writing into .git is not allowed: {rel!r}.")
            targets.append((target, content))

        for target, content in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    # -- evaluation ------------------------------------------------------------------------

    def evaluate_branch(self, node_id: str) -> VerificationResult:
        """Run the operator-configured (or auto-detected) verifier inside the branch worktree."""
        session = self.active_sessions.get(node_id)
        if not session:
            raise ValueError(f"No active session for node '{node_id}'.")

        diff = self.isolation.compute_diff(session)
        session.patch = diff  # what a merge will apply: the candidate's changes, not the verifier's by-products
        lines_added = diff.count("\n+") - diff.count("\n+++")
        lines_removed = diff.count("\n-") - diff.count("\n---")

        result = self._verifier().verify_workspace(session.worktree_path)
        status = NodeStatus.PASSED if result.passed else NodeStatus.FAILED
        metrics = result.to_metrics(lines_added=max(0, lines_added), lines_removed=max(0, lines_removed))
        self.dag.update_node(
            node_id=node_id,
            status=status,
            diff=diff,
            metrics=metrics,
            error_message=(result.stderr or result.stdout[-2000:]) if not result.passed else None,
        )
        self._save_dag()
        return result

    def auto_rollout(self, hypotheses: list[dict[str, Any]], auto_merge_best: bool = True) -> dict[str, Any]:
        """
        Explore candidate hypotheses in isolated worktrees, score them, optionally merge the best.
        Hypothesis format: {"title": str, "description": str, "files": {relative_path: content}}.
        All file maps are validated up front, so a bad hypothesis fails the call before any work.
        """
        if not isinstance(hypotheses, list) or not all(isinstance(h, dict) for h in hypotheses):
            raise ValueError("'hypotheses' must be a list of objects.")
        limit = min(len(hypotheses), self.config.max_branches)
        selected = hypotheses[:limit]
        validated = [validate_file_map(h.get("files", {})) for h in selected]

        results: list[dict[str, Any]] = []
        best_node: Optional[DAGNode] = None
        best_score = -1.0

        for i, (hyp, files) in enumerate(zip(selected, validated)):
            title = str(hyp.get("title") or f"Hypothesis {i + 1}")
            desc = str(hyp.get("description") or "")
            node, _session = self.spawn_branch(title=title, description=desc)
            if files:
                self.apply_modifications_to_branch(node.id, files)
            res = self.evaluate_branch(node.id)
            results.append({
                "node_id": node.id,
                "title": title,
                "status": "passed" if res.passed else "failed",
                "score": res.score,
                "tests_passed": res.tests_passed,
                "tests_failed": res.tests_failed,
                "duration_ms": res.duration_ms,
            })
            if res.passed and res.score > best_score:
                best_score = res.score
                best_node = self.dag.nodes[node.id]

        merged = False
        merge_error: Optional[str] = None
        if auto_merge_best and best_node is not None:
            try:
                merged = self.merge_branch(best_node.id)
            except (MergeError, ValueError) as exc:
                merge_error = str(exc)

        return {
            "total_branches_explored": len(results),
            "best_branch": best_node.to_dict() if best_node else None,
            "best_score": best_score if best_node else None,
            "auto_merged": merged,
            "merge_error": merge_error,
            "branches": results,
            "dag_ascii": self.dag.render_ascii_tree(),
        }

    # -- merge / rewind --------------------------------------------------------------------

    def merge_branch(self, node_id: str) -> bool:
        """
        Merge a PASSED branch that still has a live worktree into the workspace via ``git apply``.
        Refuses (raising) when there is no live session, the branch has not passed, or the patch does
        not apply; in every refusal the workspace is left exactly as it was.
        """
        node = self.dag.nodes.get(node_id)
        if not node:
            raise ValueError(f"Node '{node_id}' does not exist.")
        session = self.active_sessions.get(node_id)
        if session is None:
            raise MergeError(
                f"Branch '{node_id}' has no live worktree (the session ended or Hermes restarted), so it "
                "cannot be merged. Re-run chronos_branch to regenerate it."
            )
        if node.status != NodeStatus.PASSED:
            raise MergeError(f"Branch '{node_id}' has status '{node.status.value}'; only passed branches merge.")

        changed = self.isolation.apply_to_workspace(session)  # raises MergeError, workspace untouched
        self.dag.update_node(node_id=node_id, status=NodeStatus.MERGED)
        self.dag.set_active(node_id)

        self.active_sessions.pop(node_id, None)
        self.isolation.remove_worktree(session)

        parent_id = node.parent_id
        if parent_id and parent_id in self.dag.nodes:
            for sibling_id in list(self.dag.nodes[parent_id].children):
                sibling = self.active_sessions.pop(sibling_id, None) if sibling_id != node_id else None
                if sibling is not None:
                    self.isolation.remove_worktree(sibling)
                    self.dag.update_node(node_id=sibling_id, status=NodeStatus.PRUNED)
        self._save_dag()
        return changed

    def rewind_to(self, target_node_id: str) -> bool:
        """
        Check out the commit recorded at a historical checkpoint and move the DAG pointer there.
        Refuses if the workspace has uncommitted changes (they would be lost) or the node has no commit.
        """
        target = self.dag.nodes.get(target_node_id)
        if target is None:
            raise ValueError(f"Target node '{target_node_id}' not found in DAG.")
        if not target.commit_sha:
            raise ValueError(f"Node '{target_node_id}' has no recorded commit, so it cannot be rewound to.")
        self.isolation.require_repo()
        if not self.isolation.is_workspace_clean():
            raise MergeError("The workspace has uncommitted changes; commit or stash them before rewinding.")
        code, _, err = self.isolation._run_git(["checkout", "--detach", target.commit_sha])
        if code != 0:
            raise MergeError(f"git checkout failed: {err}")

        rewind_node = self.dag.add_node(
            title=f"Rewound to [{target_node_id}] {target.title}",
            branch_name="main",
            parent_id=target_node_id,
            description=f"Rewind to historical state {target_node_id}.",
            status=NodeStatus.REVERTED,
            commit_sha=target.commit_sha,
        )
        self.dag.set_active(rewind_node.id)
        self._save_dag()
        return True

    # -- reporting -------------------------------------------------------------------------

    def get_status_summary(self) -> dict[str, Any]:
        return {
            "root_id": self.dag.root_id,
            "active_node_id": self.dag.active_node_id,
            "total_nodes": len(self.dag.nodes),
            "active_worktrees": len(self.active_sessions),
            "ascii_tree": self.dag.render_ascii_tree(),
            "nodes": [n.to_dict() for n in self.dag.nodes.values()],
        }

    def cleanup_sessions(self) -> None:
        """Remove every live worktree this engine created and persist the DAG."""
        for session in list(self.active_sessions.values()):
            self.isolation.remove_worktree(session)
        self.active_sessions.clear()
        self._save_dag()
