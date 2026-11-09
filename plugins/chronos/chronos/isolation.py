"""
Chronos Isolation: ephemeral detached git worktrees for speculative branches.

Safety rules enforced here:
  * The user's project must already be a git repository with a commit. Chronos never runs
    ``git init`` / ``git add -A`` / ``git commit`` on a directory that is not one.
  * Worktrees live under a storage directory outside the project
    (``<HERMES_HOME>/plugin-data/chronos/<project-hash>/``).
  * Chronos only ever deletes directories it created: each worktree gets a sibling marker file, and
    the storage root carries its own marker. The storage root itself is never removed.
  * Merging never happens here: the engine writes the validated files through Hermes' own file
    tools (see ``hermes_io``) so core's write guards apply. There is no whole-tree copy and no raw
    ``git apply`` onto the user's workspace.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

STORAGE_MARKER = ".chronos-storage"
WORKTREE_MARKER_SUFFIX = ".chronos-owned"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_GIT_TIMEOUT = 120


class IsolationError(RuntimeError):
    """The workspace cannot be isolated (not a git repo, no commit, worktree creation failed)."""


class MergeError(RuntimeError):
    """A branch could not be merged into the workspace; the workspace was left untouched."""


@dataclass
class IsolationSession:
    branch_id: str
    worktree_path: Path
    base_commit: str
    is_git_worktree: bool = True
    # The validated {relative path: content} the model supplied. A merge writes exactly these files, so
    # test/build by-products created while verifying can never be merged.
    files: dict[str, str] = field(default_factory=dict)
    # Hermes session that created this branch; finalize cleans up only its own worktrees.
    owner: str = ""


class WorkspaceIsolation:
    """Manages isolated worktrees for speculative execution branches."""

    def __init__(self, workspace_root: Path | str, storage_dir: Path | str):
        self.workspace_root = Path(workspace_root).resolve()
        self.storage_dir = Path(storage_dir).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        marker = self.storage_dir / STORAGE_MARKER
        if not marker.exists():
            marker.write_text("Directory managed by the Hermes chronos plugin.\n", encoding="utf-8")

    # -- git helpers -----------------------------------------------------------------------

    def _run_git(
        self, args: list[str], cwd: Optional[Path] = None, stdin: Optional[str] = None
    ) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(
                ["git"] + args,
                cwd=str(cwd or self.workspace_root),
                input=stdin,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 1, "", str(exc)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

    def is_git_repo(self) -> bool:
        code, out, _ = self._run_git(["rev-parse", "--is-inside-work-tree"])
        return code == 0 and out == "true"

    def get_head_commit(self) -> Optional[str]:
        """HEAD of the workspace, or None if it is not a repo / has no commits. Never mutates."""
        code, out, _ = self._run_git(["rev-parse", "--verify", "HEAD"])
        return out if code == 0 and out else None

    def is_workspace_clean(self) -> bool:
        code, out, _ = self._run_git(["status", "--porcelain"])
        return code == 0 and not out

    def require_repo(self) -> str:
        if not self.is_git_repo():
            raise IsolationError(
                f"{self.workspace_root} is not a git repository. Chronos will not initialise one; "
                "run `git init` and make a commit yourself first."
            )
        head = self.get_head_commit()
        if head is None:
            raise IsolationError("The repository has no commits yet; make an initial commit first.")
        return head

    # -- ownership / deletion ----------------------------------------------------------------

    def _owned_marker(self, path: Path) -> Path:
        return path.parent / (path.name + WORKTREE_MARKER_SUFFIX)

    def _is_owned_worktree(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return (
            resolved.parent == self.storage_dir
            and (self.storage_dir / STORAGE_MARKER).is_file()
            and self._owned_marker(resolved).is_file()
        )

    def _delete_owned(self, path: Path) -> None:
        """rmtree a directory only if chronos created it (marker present and inside storage_dir)."""
        if self._is_owned_worktree(path):
            shutil.rmtree(path, ignore_errors=True)
            self._owned_marker(path.resolve()).unlink(missing_ok=True)

    # -- worktrees -------------------------------------------------------------------------

    def create_isolated_worktree(self, branch_id: str) -> IsolationSession:
        """Create a detached worktree at the workspace's HEAD. Raises IsolationError, never falls back."""
        if not _ID_RE.fullmatch(branch_id):
            raise IsolationError(f"Invalid branch id {branch_id!r}.")
        base_commit = self.require_repo()

        worktree_path = self.storage_dir / f"branch-{branch_id}"
        if worktree_path.exists() and not self._is_owned_worktree(worktree_path):
            raise IsolationError(f"{worktree_path} already exists and was not created by chronos.")
        self._delete_owned(worktree_path)

        code, _, err = self._run_git(["worktree", "add", "--detach", str(worktree_path), base_commit])
        if code != 0 or not worktree_path.exists():
            raise IsolationError(f"git worktree add failed: {err or 'unknown error'}")
        self._owned_marker(worktree_path).write_text("chronos worktree\n", encoding="utf-8")
        self._exclude_build_artifacts(worktree_path)
        return IsolationSession(branch_id=branch_id, worktree_path=worktree_path, base_commit=base_commit)

    def _exclude_build_artifacts(self, worktree_path: Path) -> None:
        """Keep test/build by-products out of `git add -A` (this worktree only, via its info/exclude)."""
        code, out, _ = self._run_git(["rev-parse", "--git-path", "info/exclude"], cwd=worktree_path)
        if code != 0 or not out:
            return
        exclude = Path(out) if Path(out).is_absolute() else worktree_path / out
        try:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with open(exclude, "a", encoding="utf-8") as fh:
                fh.write("\n__pycache__/\n*.pyc\n.pytest_cache/\nnode_modules/\n")
        except OSError:
            pass

    def compute_diff(self, session: IsolationSession) -> str:
        """Unified diff of the worktree against the commit it was created from."""
        self._run_git(["add", "-A"], cwd=session.worktree_path)
        code, out, _ = self._run_git(["diff", "--cached", "--binary", session.base_commit], cwd=session.worktree_path)
        return out + "\n" if code == 0 and out else ""

    def read_base_file(self, base_commit: str, rel_path: str) -> Optional[str]:
        """Exact text of ``rel_path`` at ``base_commit`` (None if the file did not exist there)."""
        try:
            proc = subprocess.run(
                ["git", "show", f"{base_commit}:{rel_path}"],
                cwd=str(self.workspace_root),
                capture_output=True,
                timeout=_GIT_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        try:
            return proc.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MergeError(f"'{rel_path}' is not a UTF-8 text file; chronos only merges text files.") from exc

    def remove_worktree(self, session: IsolationSession) -> None:
        """Remove one worktree that chronos created."""
        if self._is_owned_worktree(session.worktree_path):
            self._run_git(["worktree", "remove", "--force", str(session.worktree_path)])
            self._delete_owned(session.worktree_path)
        self._run_git(["worktree", "prune"])

    def prune_all(self, keep: Iterable[Path] = ()) -> int:
        """
        Remove stale worktrees chronos created (marker present), except the paths in ``keep``.
        Anything else in the storage directory is left alone. Returns the number removed.
        """
        keep_set = {Path(p).resolve() for p in keep}
        removed = 0
        if self.storage_dir.is_dir() and (self.storage_dir / STORAGE_MARKER).is_file():
            for child in sorted(self.storage_dir.iterdir()):
                if not child.is_dir() or child.resolve() in keep_set or not self._is_owned_worktree(child):
                    continue
                self._run_git(["worktree", "remove", "--force", str(child)])
                self._delete_owned(child)
                removed += 1
        if self.is_git_repo():
            self._run_git(["worktree", "prune"])
        return removed
