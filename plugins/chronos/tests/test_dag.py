"""
Unit tests for Chronos DAG data structures and graph traversal.
"""

import tempfile
import unittest
from pathlib import Path

from chronos.dag import ChronosDAG, NodeMetrics, NodeStatus


class TestChronosDAG(unittest.TestCase):
    def test_dag_initialization(self):
        dag = ChronosDAG()
        self.assertIsNotNone(dag.root_id)
        self.assertEqual(dag.active_node_id, dag.root_id)
        self.assertIn(dag.root_id, dag.nodes)
        root = dag.get_active()
        self.assertEqual(root.status, NodeStatus.PASSED)
        self.assertEqual(root.branch_name, "main")

    def test_add_and_update_node(self):
        dag = ChronosDAG()
        child = dag.add_node(
            title="Hypothesis A",
            branch_name="spec-1",
            description="Testing fix for race condition",
            status=NodeStatus.RUNNING,
        )
        self.assertEqual(child.parent_id, dag.root_id)
        self.assertIn(child.id, dag.nodes[dag.root_id].children)

        # Update node
        dag.update_node(
            node_id=child.id,
            status=NodeStatus.PASSED,
            metrics=NodeMetrics(score=0.92, tests_passed=4, tests_failed=0),
        )
        updated = dag.nodes[child.id]
        self.assertEqual(updated.status, NodeStatus.PASSED)
        self.assertEqual(updated.metrics.score, 0.92)

    def test_serialization(self):
        dag = ChronosDAG()
        child = dag.add_node(title="Child Node", branch_name="test")
        data = dag.to_dict()

        restored = ChronosDAG.from_dict(data)
        self.assertEqual(restored.root_id, dag.root_id)
        self.assertIn(child.id, restored.nodes)

    def test_save_and_load_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "dag.json"
            dag = ChronosDAG()
            dag.add_node(title="Saved Node", branch_name="save-test")
            dag.save_to_file(file_path)

            loaded = ChronosDAG.load_from_file(file_path)
            self.assertEqual(len(loaded.nodes), 2)

    def test_ascii_rendering(self):
        dag = ChronosDAG()
        dag.add_node(title="Node 1", branch_name="main")
        tree = dag.render_ascii_tree()
        self.assertIn("Chronos Decision DAG", tree)
        self.assertIn("Node 1", tree)


if __name__ == "__main__":
    unittest.main()
