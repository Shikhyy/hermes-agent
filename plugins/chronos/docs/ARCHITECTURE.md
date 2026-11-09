# Chronos Architecture

## Flow

1. The model calls `chronos_branch` with N hypotheses (title + `{relative path: full contents}`).
2. All file maps are validated up front (no absolute paths, `..`, `.git`, symlink escapes).
3. For each hypothesis a **detached git worktree** is created under
   `<HERMES_HOME>/plugin-data/chronos/<project-hash>/worktrees/`, and the validated files are written
   there (and remembered as the branch's `files`).
4. The operator-configured (or auto-detected) verifier runs in the worktree through Hermes' `terminal`
   tool; results are scored and recorded in the DAG.
5. If requested, the best **passing** branch's `files` are written to the workspace one by one through
   Hermes' `read_file` + `write_file` tools (core's write guards apply), only if each target is still
   exactly as it was when the branch was cut. Any refusal aborts the merge and restores earlier
   writes. Sibling worktrees are then removed.

## Modules

| Module | Responsibility |
| :--- | :--- |
| `chronos/manager.py` | Resolves the calling session's working directory on every call; one engine per project. |
| `chronos/engine.py` | Rollout, evaluation, merge, rewind; file-key validation. |
| `chronos/hermes_io.py` | `read_file`/`write_file` through `ctx.dispatch_tool`; how merges write the workspace. |
| `chronos/isolation.py` | Worktree lifecycle, marker-gated deletion, base-file reads. Never runs `git init`; never writes the workspace. |
| `chronos/verifier.py` | argv-only verification through a pluggable runner (Hermes `terminal` tool in production), scoring. |
| `chronos/config.py` | Settings from `config.yaml` (`ctx.get_config`) only; storage path under `HERMES_HOME`. |
| `chronos/dag.py` | The decision DAG (`dag.json` in the plugin-data directory). |
| `chronos/tools.py`, `chronos/hooks.py`, `__init__.py` | Tool schemas/handlers, session hooks, `register(ctx)`. |

## DAG

Each node records `id`, `parent_id`, `status` (`pending|running|passed|failed|merged|reverted|pruned`),
`diff`, `metrics`, and `commit_sha`. The root checkpoint is anchored to the commit the project was at
when Chronos first saw it; `chronos_rewind` checks out a node's `commit_sha` and refuses on a dirty tree.

## Invariants

1. The workspace changes only through an explicit merge (Hermes' file tools, so core's write guards
   apply) or a rewind of a clean tree. Uncommitted work is never overwritten.
2. The model cannot choose the verification command, a storage path, or write outside a worktree.
3. Chronos deletes only directories it created (marker files); it never `rmtree`s its storage root.
4. A branch with no verifier, a failing verifier, or no live worktree is never merged.
