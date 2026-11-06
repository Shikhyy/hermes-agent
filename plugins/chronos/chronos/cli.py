"""
Chronos CLI: Terminal interface for managing speculative trees, inspecting DAGs,
and testing worktree isolation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from chronos.engine import ChronosEngine


def main():
    parser = argparse.ArgumentParser(
        prog="chronos",
        description="Chronos: Speculative Tree-Search Reasoning & Rewind Engine for Hermes Agent",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to execute")

    # tree
    subparsers.add_parser("tree", help="Render ASCII decision tree of current DAG")

    # status
    subparsers.add_parser("status", help="Print summary JSON of current Chronos state")

    # checkpoint
    cp_parser = subparsers.add_parser("checkpoint", help="Record an explicit checkpoint")
    cp_parser.add_argument("title", help="Checkpoint title")

    # rewind
    rewind_parser = subparsers.add_parser("rewind", help="Rewind workspace to a historical node")
    rewind_parser.add_argument("node_id", help="Target node ID")

    # prune
    subparsers.add_parser("prune", help="Clean up all active and stale isolated worktrees")

    args = parser.parse_args()

    engine = ChronosEngine(workspace_root=Path.cwd())

    if args.subcommand == "tree":
        print(engine.dag.render_ascii_tree())
    elif args.subcommand == "status":
        import json
        print(json.dumps(engine.get_status_summary(), indent=2))
    elif args.subcommand == "checkpoint":
        node = engine.checkpoint(args.title)
        print(f"✅ Checkpoint created: [{node.id}] {node.title}")
    elif args.subcommand == "rewind":
        try:
            engine.rewind_to(args.node_id)
            print(f"⏪ Successfully rewound workspace to [{args.node_id}]")
        except Exception as e:
            print(f"❌ Error rewinding: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.subcommand == "prune":
        engine.isolation.prune_all()
        print("🧹 All speculative worktrees pruned cleanly.")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
