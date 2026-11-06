/**
 * Chronos Hermes Desktop Extension
 * Interactive Decision Tree DAG, Live Speculative Branch Explorer & Diff Viewer.
 */

export default function activate(desktop) {
  // Register Chronos Pane in Hermes Desktop
  desktop.registerPane({
    id: "chronos-dag-explorer",
    title: "Chronos DAG",
    icon: "git-branch",
    render: (container) => {
      container.innerHTML = `
        <div class="chronos-container">
          <div class="chronos-header">
            <div class="chronos-title-group">
              <span class="chronos-icon">⏳</span>
              <span class="chronos-title">CHRONOS SPECULATIVE REASONING</span>
              <span class="chronos-badge" id="chronos-status-badge">MCTS Active</span>
            </div>
            <div class="chronos-actions">
              <button class="chronos-btn" id="chronos-refresh-btn">🔄 Refresh Tree</button>
              <button class="chronos-btn chronos-btn-primary" id="chronos-branch-btn">🌿 New Speculative Branch</button>
            </div>
          </div>
          <div class="chronos-layout">
            <div class="chronos-dag-canvas" id="chronos-canvas">
              <svg id="chronos-svg-lines" style="position: absolute; width: 100%; height: 100%; pointer-events: none;"></svg>
              <div id="chronos-nodes-layer" style="position: relative; width: 100%; height: 100%;"></div>
            </div>
            <div class="chronos-drawer" id="chronos-drawer">
              <div class="chronos-drawer-title" id="drawer-node-title">Select a Node</div>
              <div id="drawer-node-desc" style="font-size: 12px; color: var(--chronos-text-muted); margin-bottom: 12px;">
                Click any checkpoint in the tree to inspect its hypothesis, test results, and file diffs.
              </div>
              <div id="drawer-metrics" style="display: none; margin-bottom: 12px;">
                <div style="font-size: 11px; text-transform: uppercase; color: var(--chronos-text-muted);">Verification Fitness</div>
                <div style="height: 6px; background: #30363d; border-radius: 3px; margin: 6px 0; overflow: hidden;">
                  <div id="drawer-score-bar" style="width: 0%; height: 100%; background: var(--chronos-success);"></div>
                </div>
                <div id="drawer-metrics-text" style="font-size: 11px; color: var(--chronos-text);">Score: 0.00 | 0 passed | 0 failed</div>
              </div>
              <div id="drawer-diff-section" style="display: none;">
                <div style="font-size: 11px; text-transform: uppercase; color: var(--chronos-text-muted); margin-bottom: 4px;">Candidate Diff</div>
                <div class="chronos-diff-block" id="drawer-diff-content"></div>
              </div>
              <div id="drawer-actions" style="margin-top: 16px; display: none; gap: 8px;">
                <button class="chronos-btn" id="drawer-rewind-btn" style="flex: 1; justify-content: center; background: #8250df; color: #fff;">
                  ⏪ Rewind Here
                </button>
              </div>
            </div>
          </div>
        </div>
      `;

      // State and rendering logic
      let currentDag = null;
      let selectedNodeId = null;

      async function fetchDAG() {
        try {
          const res = await desktop.callTool("chronos_status", {});
          if (res) {
            currentDag = typeof res === "string" ? JSON.parse(res) : res;
            renderTree(currentDag);
          }
        } catch (err) {
          console.warn("[Chronos] Could not load DAG via RPC:", err);
        }
      }

      function renderTree(dag) {
        if (!dag || !dag.nodes) return;
        const nodesLayer = container.querySelector("#chronos-nodes-layer");
        const svgLines = container.querySelector("#chronos-svg-lines");
        nodesLayer.innerHTML = "";
        svgLines.innerHTML = "";

        const nodesList = dag.nodes;
        const levelMap = new Map();
        const yOffset = 80;
        const xOffset = 260;

        // Simple topological level assignment
        nodesList.forEach((n, idx) => {
          const level = n.parent_id ? 1 + (idx % 3) : 0;
          const col = Math.floor(idx / 3);
          const top = 40 + (idx % 3) * yOffset;
          const left = 40 + col * xOffset;

          const el = document.createElement("div");
          el.className = `chronos-node status-${n.status} ${n.id === dag.active_node_id ? "status-active" : ""}`;
          el.style.top = `${top}px`;
          el.style.left = `${left}px`;

          const score = (n.metrics && n.metrics.score) ? n.metrics.score.toFixed(2) : "0.00";
          el.innerHTML = `
            <div class="chronos-node-header">
              <span class="chronos-node-id">#${n.id}</span>
              <span style="font-size: 10px; text-transform: uppercase;">${n.status}</span>
            </div>
            <div class="chronos-node-title">${n.title || "Branch " + n.id}</div>
            <div class="chronos-node-metrics">
              <span>Score: ${score}</span>
              <span>${n.branch_name || "branch"}</span>
            </div>
          `;

          el.addEventListener("click", () => selectNode(n));
          nodesLayer.appendChild(el);
        });
      }

      function selectNode(node) {
        selectedNodeId = node.id;
        container.querySelector("#drawer-node-title").textContent = `[#${node.id}] ${node.title}`;
        container.querySelector("#drawer-node-desc").textContent = node.description || "No description provided.";

        const metricsSection = container.querySelector("#drawer-metrics");
        const diffSection = container.querySelector("#drawer-diff-section");
        const actionsSection = container.querySelector("#drawer-actions");

        metricsSection.style.display = "block";
        actionsSection.style.display = "flex";

        const score = (node.metrics && node.metrics.score) ? node.metrics.score : 0;
        container.querySelector("#drawer-score-bar").style.width = `${Math.round(score * 100)}%`;
        container.querySelector("#drawer-metrics-text").textContent =
          `Score: ${score.toFixed(2)} | ${node.metrics?.tests_passed || 0} passed | ${node.metrics?.tests_failed || 0} failed`;

        if (node.diff) {
          diffSection.style.display = "block";
          const diffContent = container.querySelector("#drawer-diff-content");
          diffContent.innerHTML = node.diff
            .split("\n")
            .map(line => {
              if (line.startsWith("+")) return `<span class="diff-add">${escapeHtml(line)}</span>`;
              if (line.startsWith("-")) return `<span class="diff-del">${escapeHtml(line)}</span>`;
              return escapeHtml(line);
            })
            .join("\n");
        } else {
          diffSection.style.display = "none";
        }

        const rewindBtn = container.querySelector("#drawer-rewind-btn");
        rewindBtn.onclick = async () => {
          await desktop.callTool("chronos_rewind", { target_node_id: node.id });
          fetchDAG();
        };
      }

      function escapeHtml(str) {
        return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      }

      container.querySelector("#chronos-refresh-btn").addEventListener("click", fetchDAG);
      fetchDAG();
    },
  });
}
