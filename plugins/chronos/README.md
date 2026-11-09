# Chronos: speculative branching for Hermes Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

When a fix attempt fails, an agent usually keeps editing the same broken tree. Chronos lets Hermes
instead try **several candidate fixes in isolated git worktrees**, run the project's tests in each,
score them, and merge only a candidate that passes.

```
                  ┌─ hypothesis A ─▶ worktree ─▶ tests ─▶ fail  (score 0.35) ─▶ discarded
[failing project] ┼─ hypothesis B ─▶ worktree ─▶ tests ─▶ fail  (score 0.15) ─▶ discarded
                  └─ hypothesis C ─▶ worktree ─▶ tests ─▶ pass  (score 1.00) ─▶ patch merged
```

## Tools

| Tool | What it does |
| :--- | :--- |
| `chronos_branch` | Writes each hypothesis's files into its own worktree, runs verification there, scores the results, and (optionally) merges the best passing one. |
| `chronos_evaluate` | Re-runs verification for one live branch. |
| `chronos_merge` | Writes the files of a **passed** branch that still has a live worktree into the workspace through Hermes' file tools. |
| `chronos_rewind` | Checks out the git commit recorded at a checkpoint (refuses if the workspace has uncommitted changes). |
| `chronos_status` | Shows the decision tree for the current project. |

Hooks: `on_session_start` (prune stale chronos worktrees, record a baseline checkpoint) and
`on_session_finalize` (remove that session's worktrees). There are no tool-call hooks.

## Safety model

* **The model never chooses what runs.** There is no `verifier_command` tool argument. Verification
  uses the operator's `verifier_command` setting (or an auto-detected default), split into an argv
  list and run through **Hermes' `terminal` tool**, so the configured terminal backend and approval
  policy apply. The model only supplies candidate *file contents*.
* **Settings come from `config.yaml` only** (the keys declared in `plugin.yaml`'s `config_schema`,
  under `plugins.entries.chronos.settings`). Nothing is read from the project being edited, and there
  are no environment-variable overrides, so a cloned repo cannot pick a command or a storage path.
* **File keys are validated.** Absolute paths, drive letters, `..` segments, anything under `.git`,
  and paths that resolve outside the worktree (including through symlinks) are rejected before any
  file is written.
* **State lives outside the project:** `<HERMES_HOME>/plugin-data/chronos/<project-hash>/`
  (worktrees and `dag.json`). Chronos only deletes directories it created, identified by marker
  files; the storage root itself is never removed.
* **Merges go through Hermes' write guard.** A merge writes only the files the model supplied (never
  a diff recomputed after a test run, so test by-products can't slip in), one at a time through
  Hermes' own `read_file` + `write_file` tools. Core's write denylist, `HERMES_WRITE_SAFE_ROOT` and
  write approvals therefore apply. If any call is refused the whole merge is refused and files
  already written are restored. It also refuses if there is no live worktree, the branch did not
  pass, or a target file changed in the workspace since the branch was created, so uncommitted work
  is never overwritten. With no tool dispatch available, merges are refused rather than writing directly.
* **Cleanup is per session.** Finalizing a session removes only the worktrees that session created;
  engines are keyed by `HERMES_HOME` + working directory, so profiles keep separate plugin-data.
* **Git is required, and never initialised for you.** Chronos works only in an existing repository
  that has at least one commit.
* **The workspace is resolved per call** from the calling session's working directory.

Limits: verification runs in worktrees on the Hermes host, so use a terminal backend that can see
local paths (the default local backend does). If no test runner can be detected and
`verifier_command` is `auto`, branches fail verification rather than passing by default.

## Configuration

```yaml
# ~/.hermes/config.yaml
plugins:
  entries:
    chronos:
      settings:
        max_branches: 3            # 1-8 candidates per chronos_branch call
        verifier_command: "pytest -q"   # or "auto" to detect pytest / unittest / npm / cargo / go
        timeout_seconds: 60        # per-branch verification limit (1-600)
```

## Install

```bash
hermes plugins install chronos      # once it is in the catalog
```

or, for local development, copy `plugins/chronos/` from
[Shikhyy/hermes-agent](https://github.com/Shikhyy/hermes-agent) into `~/.hermes/plugins/chronos`
and run `hermes plugins enable chronos`.

## Demo and tests

```bash
python3 demo/run_demo.py        # builds a throwaway git repo and runs a rollout end to end
python3 -m unittest discover -s tests -p 'test_*.py'
```

The tests import `hermes_constants`; run them from a hermes-agent checkout (or put it on
`PYTHONPATH`).

## Scoring

`S = 0.60·tests_passed_ratio + 0.25·exit_code_ok + 0.10·small_diff + 0.05·speed`, in `[0, 1]`.
Only a branch whose verification command exits 0 is eligible to merge.

## License

MIT © Shikhyy
