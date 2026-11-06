# ⏳ Chronos: Speculative Tree-Search Reasoning & Rewind Engine for Hermes Agent

[![Hermes Agent Plugin](https://img.shields.io/badge/Hermes_Agent-Plugin-blueviolet.svg)](https://nousresearch.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests Passing](https://img.shields.io/badge/Tests-14%2F14%20Passing-brightgreen.svg)]()
[![Catalog Compatible](https://img.shields.io/badge/Catalog-Ready-success.svg)]()

> **AlphaZero for Code:** Monte Carlo Tree Search (MCTS) Speculative Rollouts, Isolated Git Worktrees, Multi-Factor Verification & Instant Time-Travel Rewinds for [Nous Research Hermes Agent](https://nousresearch.com).

---

## 🌪️ The Problem: The Linear Agent Trap

Every existing coding agent operates strictly linearly:

$$\tau = (s_0, a_0, s_1, a_1, \dots, s_T, a_T)$$

When an agent breaks code, it makes subsequent edits on top of broken code. This compounds hallucinations, creates sprawling messy diffs, and pollutes your git history. 

Humans do not write code this way. We formulate hypotheses, test them in isolated branches or sandbox environments, discard bad ideas, and commit only verified solutions.

---

## ⚡ The Solution: Chronos

**Chronos** upgrades Hermes Agent from a linear single-path executor into a **Tree-Search Reasoning & Speculative Execution Engine**.

```
                               ┌─── [Branch A: Hypothesis 1] ──▶ Verifier (Fail: 0.35) ──▶ Discarded 🗑️
                               │
[State S_t: Test Failure] ─────┼─── [Branch B: Hypothesis 2] ──▶ Verifier (Fail: 0.15) ──▶ Discarded 🗑️
                               │
                               └─── [Branch C: Hypothesis 3] ──▶ Verifier (Pass: 1.00) ──▶ AUTO-MERGED 🌿
                                                                                               │
                                                                                 [Hermes Desktop DAG]
```

### Key Capabilities
1. **Parallel Isolated Worktrees (`chronos.isolation`):** Spawns ephemeral candidate branches in isolated Git worktrees. Commands and file mutations execute in zero-copy sandboxes without touching your host workspace.
2. **Multi-Factor Fitness Scoring (`chronos.verifier`):** Scores candidates continuously across test pass ratios ($60\%$), exit code success ($25\%$), surgical diff cleanliness ($10\%$), and execution efficiency ($5\%$).
3. **Automatic Pruning & Merging (`chronos.engine`):** Automatically selects and merges the highest-scoring verified candidate into your working directory, while cleanly pruning dead branches.
4. **Hermes Desktop DAG Explorer (`desktop/plugin.js`):** Interactive visual decision tree rendered natively in Hermes Desktop with syntax-highlighted diff viewers and node inspection.
5. **Deterministic Time-Travel Rewind:** Rewinds repository state, working tree, and agent cognition back to any historical node in milliseconds.

---

## 📦 Installation

### From the Hermes Plugin Catalog
```bash
hermes plugins install chronos
```

### Local Development Installation
Clone this repository into your local Hermes plugins directory:
```bash
git clone https://github.com/NousResearch/hermes-chronos ~/.hermes/plugins/chronos
hermes plugins enable chronos
```

To validate the plugin against the official catalog suite:
```bash
hermes plugins validate ~/.hermes/plugins/chronos --install-deps
```

---

## 🛠️ Model-Callable Tools

Chronos registers 5 tools directly with Hermes Agent:

| Tool | Description |
| :--- | :--- |
| `chronos_branch` | Spawns $1-3$ speculative candidate branches in isolated worktrees, applies candidate file modifications, runs the project test suite, and scores candidates without mutating the active workspace. |
| `chronos_evaluate` | Runs test verification in a specific branch worktree and returns fitness metrics and diffs. |
| `chronos_merge` | Merges a verified candidate branch into the active workspace and updates the DAG ledger. |
| `chronos_rewind` | Instantly rolls back the workspace and decision DAG to any historical checkpoint. |
| `chronos_status` | Returns the complete decision DAG, active checkpoint, and rollout tree visualization. |

---

## 🪝 Lifecycle Hooks

Chronos subscribes to the Hermes Agent lifecycle to ensure continuous safety:
* **`pre_tool_call`:** Records clean checkpoints before mutating tool calls (file writes, shell modifications).
* **`post_tool_call`:** Detects test failures, tracebacks, or non-zero exit codes. Proactively advises Hermes to explore isolated candidate branches rather than making destructive host edits.
* **`on_session_start`:** Initializes repository DAG, verifies git invariants, and cleans up stale worktrees.
* **`on_session_end`:** Persists the execution DAG and flushes all ephemeral sandboxes.

---

## 🖥️ Hermes Desktop UI Integration

Chronos includes a native extension for **Hermes Desktop** (`desktop/plugin.js`):
* **Live Decision DAG Canvas:** Real-time visual node graph showing active branches, pass/fail status, and topological parent-child relationships.
* **Syntax-Highlighted Diff Drawer:** Click any node to inspect the exact additions and deletions made in that candidate branch.
* **1-Click Rewind Button:** One-touch rollback to any historical point in the session.

---

## 🚀 Running the Interactive Demo

Experience Chronos in action on a real buggy authentication service:
```bash
python3 demo/run_demo.py
```

### Output Preview:
```text
=== Chronos Decision DAG ===
└── [f00dc3af] ✅ (main) Initial Checkpoint [score: 0.00]
    ├── [c36e4348] 🗑️ (spec-1) Hypothesis A: Catch Exception & return None [score: 0.35]
    ├── [22607f2b] 🗑️ (spec-2) Hypothesis B: Strict Regex Parser [score: 0.15]
    └── [9585d4ef] 🌿 (spec-3) Hypothesis C: Defensive bounds checking [score: 1.00] ◀ [ACTIVE]
```

---

## 🧪 Running the Test Suite

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

---

## 🏆 Hackathon Catalog Submission

To submit Chronos to the official Hermes catalog:
1. Copy `plugin-catalog/chronos.yaml` into your fork of `NousResearch/hermes-agent`:
   ```bash
   cp plugin-catalog/chronos.yaml <hermes-agent-fork>/plugin-catalog/chronos.yaml
   ```
2. Open a Pull Request against `NousResearch/hermes-agent`.
3. Submit demo video on X tagging `@NousResearch`.
4. Complete the official Typeform: `http://form.typeform.com/to/FwJLvOln`.

---

## 📄 License

MIT © Nous Research Community
