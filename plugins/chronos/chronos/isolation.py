"""
Chronos Isolation Engine: Ephemeral Git Worktrees and Filesystem Sandboxes.
Enables parallel speculative branch execution without polluting the main repository.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class IsolationSession:
    branch_id: str
    worktree_path: Path
    is_git_worktree: bool
    base_commit: str


class WorkspaceIsolation:
    """Manages isolated workspaces for speculative execution branches."""

    def __init__(self, workspace_root: Path | str, base_storage_dir: Optional[Path | str] = None):
        self.workspace_root = Path(workspace_root).resolve()
        if base_storage_dir:
            self.storage_dir = Path(base_storage_dir).resolve()
        else:
            self.storage_dir = self.workspace_root / ".hermes" / "chronos" / "worktrees"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _run_git(self, args: List[str], cwd: Optional[Path] = None) -> Tuple[int, str, str]:
        cmd_cwd = str(cwd or self.workspace_root)
        proc = subprocess.run(
            ["git"] + args,
            cwd=cmd_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

    def is_git_repo(self) -> bool:
        code, out, _ = self._run_git(["rev-parse", "--is-inside-work-tree"])
        return code == 0 and out == "true"

    def ensure_git_repo(self) -> None:
        """Initializes a git repository if one does not exist."""
        if not self.is_git_repo():
            self._run_git(["init"])
            self._run_git(["config", "user.name", "Hermes Agent"])
            self._run_git(["config", "user.email", "agent@hermes.nousresearch.com"])
            # Create an initial commit if repository is completely empty
            code, out, _ = self._run_git(["rev-parse", "--verify", "HEAD"])
            if code != 0:
                self._run_git(["add", "-A"])
                self._run_git(["commit", "-m", "chore: initial commit by hermes-chronos", "--allow-empty"])

    def get_head_commit(self) -> str:
        self.ensure_git_repo()
        code, out, _ = self._run_git(["rev-parse", "HEAD"])
        if code != 0:
            return "HEAD"
        return out

    def create_isolated_worktree(self, branch_id: str) -> IsolationSession:
        """
        Creates an isolated workspace for a speculative branch.
        Uses `git worktree` when possible for speed and exact branch tracking;
        falls back to copytree snapshotting if worktrees cannot be created.
        """
        worktree_path = self.storage_dir / f"branch-{branch_id}"
        branch_name = f"chronos-{branch_id}"

        # Clean up any pre-existing directory
        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)

        self.ensure_git_repo()
        base_commit = self.get_head_commit()

        # Try git worktree add
        # First ensure no old branch exists with same name
        self._run_git(["branch", "-D", branch_name])
        code, out, err = self._run_git(
            ["worktree", "add", "-b", branch_name, str(worktree_path), base_commit]
        )

        if code == 0 and worktree_path.exists():
            return IsolationSession(
                branch_id=branch_id,
                worktree_path=worktree_path,
                is_git_worktree=True,
                base_commit=base_commit,
            )

        # Fallback: Filesystem directory snapshot
        def ignore_patterns(path: str, names: List[str]):
            ignored = {".git", ".hermes", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}
            return [n for n in names if n in ignored]

        shutil.copytree(self.workspace_root, worktree_path, ignore=ignore_patterns)
        return IsolationSession(
            branch_id=branch_id,
            worktree_path=worktree_path,
            is_git_worktree=False,
            base_commit=base_commit,
        )

    def compute_diff(self, session: IsolationSession) -> str:
        """Computes the unified diff between the isolated branch and base state."""
        if session.is_git_worktree:
            # Check diff against base commit
            self._run_git(["add", "-A"], cwd=session.worktree_path)
            code, out, _ = self._run_git(["diff", session.base_commit], cwd=session.worktree_path)
            if code == 0:
                return out
        return ""

    def apply_to_workspace(self, session: IsolationSession) -> bool:
        """
        Merges the winning branch back to the main workspace.
        """
        diff = self.compute_diff(session)
        if not diff:
            # No changes or copy fallback: sync files
            self._sync_directories(session.worktree_path, self.workspace_root)
            return True

        # Apply diff via git apply with diff passed to stdin
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn"],
            cwd=str(self.workspace_root),
            input=diff,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            # Fallback to file copy sync
            self._sync_directories(session.worktree_path, self.workspace_root)
        return True

    def _sync_directories(self, src: Path, dst: Path) -> None:
        """Copies modified and new files from source to destination."""
        ignored = {".git", ".hermes", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in ignored]
            rel_dir = Path(root).relative_to(src)
            target_dir = dst / rel_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            for file in files:
                if file in ignored:
                    continue
                src_file = Path(root) / file
                dst_file = target_dir / file
                shutil.copy2(src_file, dst_file)

    def remove_worktree(self, session: IsolationSession) -> None:
        """Safely removes an isolated worktree and deletes its temporary branch."""
        branch_name = f"chronos-{session.branch_id}"
        if session.is_git_worktree:
            self._run_git(["worktree", "remove", "--force", str(session.worktree_path)])
            self._run_git(["branch", "-D", branch_name])

        if session.worktree_path.exists():
            shutil.rmtree(session.worktree_path, ignore_errors=True)

    def prune_all(self) -> None:
        """Removes all stale worktrees in the storage directory."""
        if self.is_git_repo():
            self._run_git(["worktree", "prune"])
        if self.storage_dir.exists():
            shutil.rmtree(self.storage_dir, ignore_errors=True)
            self.storage_dir.mkdir(parents=True, exist_ok=True)
