"""
Chronos DAG: Directed Acyclic Graph data structure for speculative execution trees.
Tracks checkpoints, speculative branches, test metrics, diffs, and rewind histories.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    MERGED = "merged"
    REVERTED = "reverted"
    PRUNED = "pruned"


@dataclass
class NodeMetrics:
    score: float = 0.0
    tests_passed: int = 0
    tests_failed: int = 0
    duration_ms: int = 0
    exit_code: int = 0
    lines_added: int = 0
    lines_removed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DAGNode:
    id: str
    parent_id: Optional[str]
    branch_name: str
    title: str
    description: str = ""
    status: NodeStatus = NodeStatus.PENDING
    created_at: float = field(default_factory=time.time)
    commit_sha: Optional[str] = None
    diff: Optional[str] = None
    metrics: NodeMetrics = field(default_factory=NodeMetrics)
    error_message: Optional[str] = None
    children: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["metrics"] = self.metrics.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DAGNode:
        metrics_data = data.get("metrics", {})
        metrics = NodeMetrics(**metrics_data) if isinstance(metrics_data, dict) else NodeMetrics()
        return cls(
            id=data["id"],
            parent_id=data.get("parent_id"),
            branch_name=data.get("branch_name", "main"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            status=NodeStatus(data.get("status", NodeStatus.PENDING.value)),
            created_at=data.get("created_at", time.time()),
            commit_sha=data.get("commit_sha"),
            diff=data.get("diff"),
            metrics=metrics,
            error_message=data.get("error_message"),
            children=data.get("children", []),
        )


class ChronosDAG:
    """Manages the full execution graph for Hermes Agent."""

    def __init__(self, root_id: Optional[str] = None):
        self.nodes: Dict[str, DAGNode] = {}
        self.active_node_id: Optional[str] = None

        if root_id is None:
            root_id = self._generate_id()
        self.root_id = root_id
        root_node = DAGNode(
            id=root_id,
            parent_id=None,
            branch_name="main",
            title="Initial Checkpoint",
            description="Base workspace state before agent exploration.",
            status=NodeStatus.PASSED,
        )
        self.nodes[root_id] = root_node
        self.active_node_id = root_id

    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex[:8]

    def add_node(
        self,
        title: str,
        branch_name: str,
        parent_id: Optional[str] = None,
        description: str = "",
        status: NodeStatus = NodeStatus.PENDING,
        diff: Optional[str] = None,
        commit_sha: Optional[str] = None,
        metrics: Optional[NodeMetrics] = None,
    ) -> DAGNode:
        if parent_id is None:
            parent_id = self.active_node_id or self.root_id

        if parent_id not in self.nodes:
            raise ValueError(f"Parent node '{parent_id}' does not exist in DAG.")

        node_id = self._generate_id()
        node = DAGNode(
            id=node_id,
            parent_id=parent_id,
            branch_name=branch_name,
            title=title,
            description=description,
            status=status,
            diff=diff,
            commit_sha=commit_sha,
            metrics=metrics or NodeMetrics(),
        )

        self.nodes[node_id] = node
        self.nodes[parent_id].children.append(node_id)
        return node

    def update_node(
        self,
        node_id: str,
        status: Optional[NodeStatus] = None,
        diff: Optional[str] = None,
        commit_sha: Optional[str] = None,
        metrics: Optional[NodeMetrics] = None,
        error_message: Optional[str] = None,
    ) -> DAGNode:
        if node_id not in self.nodes:
            raise ValueError(f"Node '{node_id}' not found.")
        node = self.nodes[node_id]
        if status is not None:
            node.status = status
        if diff is not None:
            node.diff = diff
        if commit_sha is not None:
            node.commit_sha = commit_sha
        if metrics is not None:
            node.metrics = metrics
        if error_message is not None:
            node.error_message = error_message
        return node

    def set_active(self, node_id: str) -> None:
        if node_id not in self.nodes:
            raise ValueError(f"Cannot set active to non-existent node '{node_id}'.")
        self.active_node_id = node_id

    def get_active(self) -> DAGNode:
        return self.nodes[self.active_node_id or self.root_id]

    def get_ancestors(self, node_id: str) -> List[DAGNode]:
        """Returns ordered list of ancestors from root down to node_id."""
        path: List[DAGNode] = []
        curr = self.nodes.get(node_id)
        while curr is not None:
            path.append(curr)
            curr = self.nodes.get(curr.parent_id) if curr.parent_id else None
        path.reverse()
        return path

    def get_sibling_branches(self, parent_id: str) -> List[DAGNode]:
        """Returns all children of a given parent node."""
        parent = self.nodes.get(parent_id)
        if not parent:
            return []
        return [self.nodes[child_id] for child_id in parent.children if child_id in self.nodes]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_id": self.root_id,
            "active_node_id": self.active_node_id,
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ChronosDAG:
        dag = cls(root_id=data.get("root_id"))
        dag.active_node_id = data.get("active_node_id")
        nodes_data = data.get("nodes", {})
        dag.nodes = {nid: DAGNode.from_dict(ndata) for nid, ndata in nodes_data.items()}
        return dag

    def save_to_file(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_from_file(cls, path: Path | str) -> ChronosDAG:
        p = Path(path)
        if not p.exists():
            return cls()
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def render_ascii_tree(self) -> str:
        """Renders an ASCII tree of the decision DAG."""
        lines: List[str] = ["=== Chronos Decision DAG ==="]

        def _traverse(node_id: str, prefix: str = "", is_last: bool = True):
            node = self.nodes.get(node_id)
            if not node:
                return

            marker = "└── " if is_last else "├── "
            active_marker = " ◀ [ACTIVE]" if node_id == self.active_node_id else ""
            status_icon = {
                NodeStatus.PASSED: "✅",
                NodeStatus.FAILED: "❌",
                NodeStatus.RUNNING: "⏳",
                NodeStatus.PENDING: "⚪",
                NodeStatus.MERGED: "🌿",
                NodeStatus.REVERTED: "⏪",
                NodeStatus.PRUNED: "🗑️",
            }.get(node.status, "•")

            lines.append(
                f"{prefix}{marker}[{node.id}] {status_icon} ({node.branch_name}) {node.title}"
                f" [score: {node.metrics.score:.2f}]{active_marker}"
            )

            new_prefix = prefix + ("    " if is_last else "│   ")
            child_count = len(node.children)
            for i, child_id in enumerate(node.children):
                _traverse(child_id, new_prefix, i == child_count - 1)

        _traverse(self.root_id)
        return "\n".join(lines)
