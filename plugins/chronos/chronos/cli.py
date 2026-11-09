"""
Chronos CLI: inspect the decision DAG and clean up worktrees for the project in the current directory.

State lives under ``<HERMES_HOME>/plugin-data/chronos/<project-hash>/``. Settings still come from
the operator's Hermes config only; the standalone CLI uses the built-in defaults.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ChronosConfig
from .engine import ChronosEngine


def main() -> None:
    parser = argparse.ArgumentParser(prog="chronos", description="Chronos: speculative branching for Hermes Agent")
    subparsers = parser.add_subparsers(dest="subcommand")
    subparsers.add_parser("tree", help="Render the ASCII decision tree")
    subparsers.add_parser("status", help="Print Chronos state as JSON")
    cp_parser = subparsers.add_parser("checkpoint", help="Record a checkpoint at the current commit")
    cp_parser.add_argument("title", help="Checkpoint title")
    rewind_parser = subparsers.add_parser("rewind", help="Check out a checkpoint's commit (refuses on a dirty tree)")
    rewind_parser.add_argument("node_id", help="Target node ID")
    subparsers.add_parser("prune", help="Remove stale worktrees chronos created")

    args = parser.parse_args()
    if not args.subcommand:
        parser.print_help()
        return

    engine = ChronosEngine(workspace_root=Path.cwd(), config=ChronosConfig())

    if args.subcommand == "tree":
        print(engine.dag.render_ascii_tree())
    elif args.subcommand == "status":
        print(json.dumps(engine.get_status_summary(), indent=2))
    elif args.subcommand == "checkpoint":
        node = engine.checkpoint(args.title)
        print(f"Checkpoint created: [{node.id}] {node.title}")
    elif args.subcommand == "rewind":
        try:
            engine.rewind_to(args.node_id)
            print(f"Rewound workspace to [{args.node_id}]")
        except Exception as exc:
            print(f"Error rewinding: {exc}", file=sys.stderr)
            sys.exit(1)
    elif args.subcommand == "prune":
        removed = engine.isolation.prune_all()
        print(f"Removed {removed} stale worktree(s).")


if __name__ == "__main__":
    main()
