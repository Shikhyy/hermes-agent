# Chronos Architecture Specification

> **Speculative Tree-Search Reasoning, Ephemeral Worktree Rollouts & Rewind Engine for Hermes Agent**

---

## 1. Problem Statement

Existing autonomous coding agents operate under a **linear single-trajectory execution model**:
$$\tau = (s_0, a_0, s_1, a_1, \dots, s_T, a_T)$$

When an agent encounters a regression, test failure, or environment error at step $t$, it is forced to execute corrective actions directly on top of the already mutated workspace state $s_t$. This induces compounding hallucinations, code pollution, and destructive git histories.

**Chronos** replaces this linear model with a **Monte Carlo Tree Search (MCTS) Speculative Rollout Engine**, allowing Hermes Agent to hypothesize multiple candidate fixes in parallel isolated worktrees, evaluate them against deterministic verifiers, and commit only mathematically optimal solutions.

---

## 2. Core Architecture

```
                               ┌─── [Branch A: Hypothesis 1] ──▶ Verifier (Fail: 0.35) ──▶ Discarded
                               │
[State S_t: Test Failure] ─────┼─── [Branch B: Hypothesis 2] ──▶ Verifier (Fail: 0.40) ──▶ Discarded
                               │
                               └─── [Branch C: Hypothesis 3] ──▶ Verifier (Pass: 0.98) ──▶ AUTO-MERGED
                                                                                               │
                                                                                 [Hermes Desktop DAG]
```

### 2.1 Workspace Isolation Engine (`chronos.isolation`)
Chronos leverages Git's native worktree subsystem (`git worktree add -b <branch> <path> <commit>`).
* **Zero Host Pollution:** Worktrees share the main `.git` object store, allowing instantaneous hardlinked checkouts without duplicating repository storage or dependencies.
* **Deterministic Isolation:** File modifications made inside `branch-<id>` are completely invisible to the host workspace until an explicit `chronos_merge` is issued.
* **Fallback Directory Engine:** On systems without Git initialized, Chronos gracefully degrades to an atomic copy-on-write directory snapshotter with automatic exclusion of build artifacts (`node_modules`, `.venv`, `.git`).

### 2.2 Multi-Factor Fitness Function (`chronos.verifier`)
Rather than relying solely on boolean exit codes, Chronos scores each candidate branch using a continuous multi-factor fitness function $S \in [0.0, 1.0]$:

$$S = w_{pass} \cdot P + w_{exit} \cdot E + w_{diff} \cdot D + w_{time} \cdot T$$

Where:
* **$P$ (Test Pass Ratio, $w_{pass} = 0.60$):** $\frac{\text{tests\_passed}}{\text{tests\_passed} + \text{tests\_failed}}$
* **$E$ (Exit Code Success, $w_{exit} = 0.25$):** $1.0 \text{ if } \text{exit\_code} == 0 \text{ else } 0.0$
* **$D$ (Diff Cleanliness, $w_{diff} = 0.10$):** Penalizes sprawling, uncontrolled edits in favor of surgical fixes:
  $$D = \max\left(0.2, 1.0 - \frac{\max(0, \text{lines\_changed} - 50)}{500}\right)$$
* **$T$ (Execution Efficiency, $w_{time} = 0.05$):** Incentivizes faster execution before timeouts.

### 2.3 Directed Acyclic Graph (DAG) Ledger (`chronos.dag`)
The state space is tracked as an append-only JSON DAG stored at `.hermes/chronos/dag.json`.
Every node records:
* `id`: Unique 8-character cryptographic token.
* `parent_id`: Pointer to the antecedent checkpoint.
* `status`: `PENDING` | `RUNNING` | `PASSED` | `FAILED` | `MERGED` | `REVERTED` | `PRUNED`.
* `diff`: Unified patch showing exact file modifications.
* `metrics`: Continuous score, test breakdown, and duration.
* `commit_sha`: Git commit hash for deterministic time-travel rollbacks.

---

## 3. Hermes Agent Integration

Chronos adheres strictly to the official Hermes Agent Plugin Specification:

| Surface | File | Description |
| :--- | :--- | :--- |
| **Manifest** | `plugin.yaml` | Declares tools, hooks, config schema, and desktop entry point. |
| **Plugin Init** | `__init__.py` | Implements `register(ctx)` to register tools and subscribe to hooks. |
| **Model Tools** | `chronos/tools.py` | Exposes `chronos_branch`, `chronos_evaluate`, `chronos_merge`, `chronos_rewind`, `chronos_status`. |
| **Lifecycle Hooks** | `chronos/hooks.py` | Subscribes to `pre_tool_call`, `post_tool_call`, `on_session_start`, `on_session_end`. |
| **Hermes Desktop UI** | `desktop/plugin.js` | Visualizes the live interactive DAG, score gauges, diff drawer, and 1-click rewind buttons. |
| **Catalog Entry** | `plugin-catalog/chronos.yaml` | Prepared for upstream PR into `NousResearch/hermes-agent`. |

---

## 4. Safety & Invariants

1. **Workspace Preservation:** The user's active workspace is never mutated during speculative rollouts.
2. **Clean Pruning:** Ephemeral worktrees and temporary branches are automatically pruned upon session termination or candidate merge.
3. **Deterministic Rewind:** Any state can be rolled back without data loss using recorded git commits and unified patches.
