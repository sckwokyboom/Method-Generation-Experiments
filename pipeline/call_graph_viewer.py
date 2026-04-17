"""Interactive Material-UI HTML viewer for a built call-graph.

Renders a self-contained HTML with:
- Summary header (vertex/edge stats)
- Tabbed view: Graph (Cytoscape), Matrix (canvas heatmap), Vertex list
- Side drawer with vertex details + incoming/outgoing edges (clickable)
- Optional method body rendering if extraction JSON is provided
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

import torch

from pipeline.call_graph import load_call_graph


def _package_of(class_fqn: str) -> str:
    idx = class_fqn.rfind(".")
    return class_fqn[:idx] if idx >= 0 else "(default)"


def _build_body_map(extraction_data: dict | None) -> dict[tuple[str, str], str]:
    """Map (class_fqn, method_name) -> method body source, taking first occurrence."""
    if not extraction_data:
        return {}
    bodies: dict[tuple[str, str], str] = {}
    for m in extraction_data.get("methods", []):
        key = (m.get("classFqn", ""), m.get("methodName", ""))
        bodies.setdefault(key, m.get("methodBody", ""))
        for sib in m.get("siblingMethods", []):
            sib_key = (m.get("classFqn", ""), sib.get("methodName", ""))
            bodies.setdefault(sib_key, sib.get("signature", ""))
    return bodies


def build_payload(graph_dir: Path, extraction_json: Path | None) -> dict:
    graph = load_call_graph(graph_dir)
    n = len(graph.vertex_ids)
    adj = graph.adjacency.coalesce()
    indices = adj.indices().tolist() if adj.values().numel() else [[], []]
    values = adj.values().tolist()

    extraction_data = None
    if extraction_json is not None:
        with extraction_json.open("r", encoding="utf-8") as f:
            extraction_data = json.load(f)
    bodies = _build_body_map(extraction_data)

    out_deg = [0] * n
    in_deg = [0] * n
    for s, d, w in zip(indices[0], indices[1], values):
        out_deg[s] += w
        in_deg[d] += w

    vertices = []
    for i in range(n):
        meta = graph.vertex_meta[i]
        class_fqn = meta.get("class_fqn", "")
        method_name = meta.get("method_name", "")
        vertices.append({
            "i": i,
            "id": graph.vertex_ids[i],
            "class_fqn": class_fqn,
            "method_name": method_name,
            "file_path": meta.get("file_path", ""),
            "parameter_types": meta.get("parameter_types", []),
            "return_type": meta.get("return_type", ""),
            "package": _package_of(class_fqn),
            "out_deg": out_deg[i],
            "in_deg": in_deg[i],
            "body": bodies.get((class_fqn, method_name), ""),
        })

    edges = [
        {"s": s, "d": d, "w": w}
        for s, d, w in zip(indices[0], indices[1], values)
    ]

    self_loops = sum(1 for e in edges if e["s"] == e["d"])
    isolated = sum(1 for i in range(n) if out_deg[i] == 0 and in_deg[i] == 0)

    return {
        "stats": {
            "n": n,
            "e_unique": len(edges),
            "weight_sum": int(sum(values)) if values else 0,
            "self_loops": self_loops,
            "isolated": isolated,
            "density": len(edges) / (n * n) if n else 0.0,
        },
        "vertices": vertices,
        "edges": edges,
    }


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Call Graph Viewer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&family=Roboto+Mono:wght@400;500&family=Material+Symbols+Outlined" rel="stylesheet">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github.min.css">
<script src="https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/languages/java.min.js"></script>
<style>
:root {
  --md-primary: #1a73e8;
  --md-primary-container: #d3e3fd;
  --md-on-primary: #fff;
  --md-surface: #fff;
  --md-surface-2: #f8fafd;
  --md-surface-3: #eef3fb;
  --md-on-surface: #1f1f1f;
  --md-on-surface-variant: #444746;
  --md-outline: #c4c7c5;
  --md-outline-variant: #dadce0;
  --md-error: #b3261e;
  --md-secondary: #005ac1;
  --shadow-1: 0 1px 2px 0 rgba(60,64,67,.08), 0 1px 3px 1px rgba(60,64,67,.06);
  --shadow-2: 0 2px 6px 2px rgba(60,64,67,.08), 0 1px 2px 0 rgba(60,64,67,.12);
  --shadow-3: 0 4px 12px 6px rgba(60,64,67,.08), 0 2px 6px 0 rgba(60,64,67,.14);
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0;
  font-family: 'Roboto', system-ui, -apple-system, sans-serif;
  color: var(--md-on-surface);
  background: var(--md-surface-2);
  font-size: 14px;
  line-height: 1.4;
}
.material-symbols-outlined {
  font-family: 'Material Symbols Outlined';
  font-weight: 400;
  font-style: normal;
  font-size: 20px;
  vertical-align: middle;
  user-select: none;
}
.app-bar {
  display: flex; align-items: center; gap: 16px;
  padding: 8px 16px;
  background: var(--md-surface);
  box-shadow: var(--shadow-1);
  position: sticky; top: 0; z-index: 10;
}
.app-bar h1 {
  font-size: 18px; font-weight: 500; margin: 0;
  display: flex; align-items: center; gap: 8px;
}
.chips { display: flex; gap: 8px; flex-wrap: wrap; }
.chip {
  padding: 4px 12px;
  background: var(--md-surface-3);
  border-radius: 16px;
  font-size: 12px;
  color: var(--md-on-surface-variant);
  white-space: nowrap;
  border: 1px solid transparent;
}
.chip.strong { background: var(--md-primary-container); color: var(--md-primary); }
.chip.warn { background: #fff4e5; color: #b15c00; }
.search-input {
  flex: 1;
  max-width: 480px;
  padding: 8px 12px 8px 36px;
  font: inherit; font-size: 14px;
  border-radius: 24px;
  border: 1px solid var(--md-outline-variant);
  background: var(--md-surface-2);
  outline: none;
  transition: border-color .15s, box-shadow .15s;
}
.search-input:focus { border-color: var(--md-primary); box-shadow: 0 0 0 2px var(--md-primary-container); }
.search-wrap { position: relative; flex: 1; max-width: 480px; }
.search-wrap .material-symbols-outlined {
  position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
  color: var(--md-on-surface-variant); pointer-events: none;
  font-size: 20px;
}
.layout {
  display: grid;
  grid-template-columns: 1fr 420px;
  gap: 0;
  height: calc(100vh - 57px);
}
.main { overflow: hidden; display: flex; flex-direction: column; }
.tabs {
  display: flex;
  background: var(--md-surface);
  border-bottom: 1px solid var(--md-outline-variant);
  padding: 0 16px;
  gap: 0;
}
.tab {
  padding: 14px 20px;
  font-weight: 500; font-size: 14px;
  cursor: pointer;
  color: var(--md-on-surface-variant);
  border-bottom: 2px solid transparent;
  transition: color .15s, border-color .15s, background .15s;
  display: flex; align-items: center; gap: 8px;
  border-radius: 8px 8px 0 0;
}
.tab:hover { background: var(--md-surface-2); color: var(--md-on-surface); }
.tab.active { color: var(--md-primary); border-bottom-color: var(--md-primary); }
.tab-panel { flex: 1; display: none; overflow: hidden; position: relative; }
.tab-panel.active { display: flex; flex-direction: column; }
#cy { flex: 1; background: var(--md-surface-2); }
.graph-toolbar {
  display: flex; gap: 8px; padding: 8px 16px;
  background: var(--md-surface); border-bottom: 1px solid var(--md-outline-variant);
  align-items: center; flex-wrap: wrap;
}
.btn {
  padding: 6px 14px;
  background: var(--md-surface);
  border: 1px solid var(--md-outline-variant);
  border-radius: 20px;
  font-family: inherit; font-size: 13px; font-weight: 500;
  color: var(--md-primary);
  cursor: pointer;
  display: inline-flex; align-items: center; gap: 6px;
  transition: background .15s;
}
.btn:hover { background: var(--md-surface-3); }
.btn.toggled { background: var(--md-primary-container); border-color: var(--md-primary-container); }
.btn .material-symbols-outlined { font-size: 18px; }
.matrix-wrap {
  flex: 1; overflow: auto; padding: 16px;
  background: var(--md-surface-2);
}
#matrix-canvas {
  display: block;
  box-shadow: var(--shadow-1);
  background: var(--md-surface);
  image-rendering: pixelated;
  cursor: crosshair;
}
.matrix-controls {
  padding: 8px 16px;
  display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
  background: var(--md-surface); border-bottom: 1px solid var(--md-outline-variant);
}
.matrix-controls label { font-size: 13px; color: var(--md-on-surface-variant); display: flex; align-items: center; gap: 6px; }
.matrix-controls select {
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid var(--md-outline-variant);
  background: var(--md-surface);
  font: inherit; font-size: 13px;
}
.matrix-legend {
  margin-left: auto;
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; color: var(--md-on-surface-variant);
}
.matrix-gradient {
  width: 100px; height: 12px;
  background: linear-gradient(to right, #eaf2ff, #1a73e8, #002c7a);
  border-radius: 2px;
}
.vlist-wrap {
  flex: 1; overflow: auto; background: var(--md-surface);
  display: flex; flex-direction: column;
}
.vlist-header {
  display: grid;
  grid-template-columns: 60px 60px 1fr;
  gap: 8px;
  padding: 12px 16px;
  font-weight: 500; font-size: 12px;
  color: var(--md-on-surface-variant);
  text-transform: uppercase; letter-spacing: .4px;
  border-bottom: 1px solid var(--md-outline-variant);
  position: sticky; top: 0; background: var(--md-surface); z-index: 1;
}
.vlist-header .sortable { cursor: pointer; user-select: none; display: inline-flex; align-items: center; gap: 2px; }
.vlist-header .sortable:hover { color: var(--md-primary); }
.vlist-row {
  display: grid;
  grid-template-columns: 60px 60px 1fr;
  gap: 8px;
  padding: 10px 16px;
  align-items: center;
  border-bottom: 1px solid var(--md-surface-3);
  cursor: pointer;
  transition: background .1s;
}
.vlist-row:hover { background: var(--md-surface-3); }
.vlist-row.selected { background: var(--md-primary-container); }
.vlist-row .deg { font-variant-numeric: tabular-nums; color: var(--md-on-surface-variant); font-size: 13px; }
.vlist-row .id {
  font-family: 'Roboto Mono', monospace; font-size: 12px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.vlist-row .id .method { font-weight: 500; color: var(--md-on-surface); }
.vlist-row .id .class { color: var(--md-on-surface-variant); }
.detail {
  background: var(--md-surface);
  border-left: 1px solid var(--md-outline-variant);
  overflow-y: auto;
  display: flex; flex-direction: column;
}
.detail-empty {
  padding: 64px 32px;
  text-align: center;
  color: var(--md-on-surface-variant);
  margin: auto;
}
.detail-empty .material-symbols-outlined { font-size: 48px; opacity: .3; display: block; margin-bottom: 8px; }
.detail-card { padding: 20px 24px; border-bottom: 1px solid var(--md-outline-variant); }
.detail-card h2 {
  font-size: 12px; font-weight: 500; text-transform: uppercase; letter-spacing: .5px;
  margin: 0 0 12px 0; color: var(--md-on-surface-variant);
  display: flex; align-items: center; gap: 6px;
}
.detail-card .vertex-title {
  font-family: 'Roboto Mono', monospace; font-size: 13px;
  word-break: break-all; line-height: 1.5;
  padding: 10px 12px;
  background: var(--md-surface-2);
  border-radius: 8px;
  border: 1px solid var(--md-outline-variant);
}
.detail-card .vertex-title .method-name { color: var(--md-primary); font-weight: 500; }
.detail-card .meta-row {
  display: grid; grid-template-columns: 100px 1fr;
  gap: 8px; font-size: 13px; margin: 4px 0;
}
.detail-card .meta-row .k { color: var(--md-on-surface-variant); }
.detail-card .meta-row .v { word-break: break-all; }
.detail-card .meta-row .v code {
  font-family: 'Roboto Mono', monospace; font-size: 12px;
  background: var(--md-surface-3); padding: 2px 6px; border-radius: 4px;
}
.edge-table { display: flex; flex-direction: column; gap: 0; }
.edge-row {
  display: grid; grid-template-columns: 48px 1fr;
  gap: 8px; padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
  align-items: center;
  transition: background .1s;
}
.edge-row:hover { background: var(--md-surface-3); }
.edge-row .w {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 28px; height: 22px; padding: 0 8px;
  border-radius: 11px;
  background: var(--md-primary-container);
  color: var(--md-primary);
  font-size: 12px; font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.edge-row .target {
  font-family: 'Roboto Mono', monospace; font-size: 12px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.edge-row .target .method-name { color: var(--md-primary); font-weight: 500; }
.edge-empty {
  padding: 12px 16px; font-size: 13px; color: var(--md-on-surface-variant); font-style: italic;
}
pre.code {
  margin: 0; padding: 12px 16px;
  background: var(--md-surface-2);
  border-radius: 8px;
  border: 1px solid var(--md-outline-variant);
  font-family: 'Roboto Mono', monospace; font-size: 12px;
  line-height: 1.5;
  overflow-x: auto; max-height: 400px;
  white-space: pre;
}
pre.code code.hljs { background: transparent !important; padding: 0; }
.highlighted { animation: flash 1.2s ease-out; }
@keyframes flash {
  0% { background: #fff4a3; }
  100% { background: transparent; }
}
.toast {
  position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
  background: #323232; color: #fff;
  padding: 10px 20px; border-radius: 24px;
  font-size: 14px;
  box-shadow: var(--shadow-2);
  opacity: 0; pointer-events: none; transition: opacity .2s;
  z-index: 100;
}
.toast.visible { opacity: 1; }
.hidden { display: none !important; }
</style>
</head>
<body>
<div class="app-bar">
  <h1><span class="material-symbols-outlined">share</span>Call Graph Viewer</h1>
  <div class="chips" id="stats-chips"></div>
  <div class="search-wrap">
    <span class="material-symbols-outlined">search</span>
    <input id="search" class="search-input" type="text" placeholder="Search methods by class or name…">
  </div>
</div>
<div class="layout">
  <div class="main">
    <div class="tabs" role="tablist">
      <div class="tab active" data-tab="graph"><span class="material-symbols-outlined">hub</span>Graph</div>
      <div class="tab" data-tab="matrix"><span class="material-symbols-outlined">grid_on</span>Matrix</div>
      <div class="tab" data-tab="list"><span class="material-symbols-outlined">list</span>Vertices</div>
    </div>
    <div class="tab-panel active" data-tab="graph">
      <div class="graph-toolbar">
        <button class="btn" id="graph-fit"><span class="material-symbols-outlined">fit_screen</span>Fit</button>
        <button class="btn" id="graph-relayout"><span class="material-symbols-outlined">refresh</span>Re-layout</button>
        <button class="btn toggled" id="graph-labels"><span class="material-symbols-outlined">label</span>Labels</button>
        <button class="btn" id="graph-isolated"><span class="material-symbols-outlined">visibility_off</span>Hide isolated</button>
        <span class="chip" id="graph-info">Force-directed layout · click node for details</span>
      </div>
      <div id="cy"></div>
    </div>
    <div class="tab-panel" data-tab="matrix">
      <div class="matrix-controls">
        <label>Sort:
          <select id="mat-sort">
            <option value="index">Insertion order</option>
            <option value="class" selected>Class FQN</option>
            <option value="out_deg">Out-degree desc</option>
            <option value="in_deg">In-degree desc</option>
          </select>
        </label>
        <label>Scale:
          <select id="mat-scale">
            <option value="log" selected>log(weight)</option>
            <option value="linear">linear</option>
            <option value="binary">binary (A&gt;0)</option>
          </select>
        </label>
        <label>Cell size:
          <select id="mat-cell">
            <option value="2">2 px</option>
            <option value="3" selected>3 px</option>
            <option value="5">5 px</option>
            <option value="8">8 px</option>
          </select>
        </label>
        <div class="matrix-legend">low <span class="matrix-gradient"></span> high</div>
      </div>
      <div class="matrix-wrap">
        <canvas id="matrix-canvas" width="600" height="600"></canvas>
      </div>
    </div>
    <div class="tab-panel" data-tab="list">
      <div class="vlist-wrap">
        <div class="vlist-header">
          <span class="sortable" data-sort="out_deg">out ▾</span>
          <span class="sortable" data-sort="in_deg">in</span>
          <span>vertex</span>
        </div>
        <div id="vlist"></div>
      </div>
    </div>
  </div>
  <aside class="detail" id="detail">
    <div class="detail-empty">
      <span class="material-symbols-outlined">info</span>
      Select a method from the graph, matrix, or list to see details.
    </div>
  </aside>
</div>
<div class="toast" id="toast"></div>
<script id="data" type="application/json">__PAYLOAD__</script>
<script>
(function() {
  const DATA = JSON.parse(document.getElementById('data').textContent);
  const VERTICES = DATA.vertices;
  const EDGES = DATA.edges;
  const STATS = DATA.stats;
  const N = VERTICES.length;

  const byId = {};
  for (const v of VERTICES) byId[v.i] = v;

  const outEdges = Array.from({length: N}, () => []);
  const inEdges = Array.from({length: N}, () => []);
  for (const e of EDGES) {
    outEdges[e.s].push({dst: e.d, w: e.w});
    inEdges[e.d].push({src: e.s, w: e.w});
  }

  // Stats chips
  const chips = document.getElementById('stats-chips');
  const chip = (cls, text) => `<span class="chip ${cls}">${text}</span>`;
  chips.innerHTML = [
    chip('strong', `${STATS.n} vertices`),
    chip('strong', `${STATS.e_unique} edges`),
    chip('', `Σw = ${STATS.weight_sum}`),
    chip(STATS.self_loops ? 'warn' : '', `${STATS.self_loops} self-loops`),
    chip(STATS.isolated ? 'warn' : '', `${STATS.isolated} isolated`),
    chip('', `density ${STATS.density.toFixed(5)}`),
  ].join('');

  // Tabs
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      document.querySelector(`.tab-panel[data-tab="${tab.dataset.tab}"]`).classList.add('active');
      if (tab.dataset.tab === 'matrix') renderMatrix();
      if (tab.dataset.tab === 'graph' && !cyInitialized) initCy();
    });
  });

  // Package → stable HSL color
  function packageColor(pkg) {
    let h = 0;
    for (let i = 0; i < pkg.length; i++) h = (h * 31 + pkg.charCodeAt(i)) | 0;
    return `hsl(${Math.abs(h) % 360}, 60%, 55%)`;
  }

  // Short vertex label for graph nodes
  function shortLabel(v) {
    const cls = v.class_fqn.split('.').pop() || '?';
    return `${cls}.${v.method_name}`;
  }

  function prettyVertexHtml(v) {
    const params = (v.parameter_types || []).join(', ');
    return `<span class="class">${escapeHtml(v.class_fqn)}</span>::<span class="method-name">${escapeHtml(v.method_name)}</span>(${escapeHtml(params)}) -&gt; ${escapeHtml(v.return_type)}`;
  }
  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  // --- Graph (Cytoscape) ---
  let cy = null;
  let cyInitialized = false;
  let showLabels = true;
  let hideIsolated = false;

  function initCy() {
    const minSize = 16, maxSize = 52;
    const maxDeg = Math.max(1, ...VERTICES.map(v => v.out_deg + v.in_deg));

    const nodes = VERTICES
      .filter(v => !hideIsolated || v.out_deg + v.in_deg > 0)
      .map(v => ({
        data: {
          id: String(v.i),
          label: shortLabel(v),
          pkg: v.package,
          color: packageColor(v.package),
          size: minSize + (maxSize - minSize) * Math.sqrt((v.out_deg + v.in_deg) / maxDeg),
        }
      }));
    const maxW = Math.max(1, ...EDGES.map(e => e.w));
    const edges = EDGES.map(e => ({
      data: {
        id: `${e.s}->${e.d}`,
        source: String(e.s),
        target: String(e.d),
        weight: e.w,
        width: 0.5 + 3 * (e.w / maxW),
      }
    }));

    cy = cytoscape({
      container: document.getElementById('cy'),
      elements: [...nodes, ...edges],
      style: [
        { selector: 'node', style: {
          'background-color': 'data(color)',
          'label': 'data(label)',
          'color': '#1f1f1f',
          'font-size': 9,
          'font-family': 'Roboto, sans-serif',
          'text-valign': 'center',
          'text-halign': 'center',
          'text-wrap': 'ellipsis',
          'text-max-width': 100,
          'width': 'data(size)',
          'height': 'data(size)',
          'border-width': 1,
          'border-color': 'rgba(0,0,0,0.2)',
          'transition-property': 'border-width, border-color, background-color',
          'transition-duration': 150,
        }},
        { selector: 'node.selected', style: {
          'border-width': 4,
          'border-color': '#1a73e8',
        }},
        { selector: 'node.faded', style: { 'opacity': 0.18 }},
        { selector: 'edge', style: {
          'line-color': '#9aa0a6',
          'target-arrow-color': '#9aa0a6',
          'target-arrow-shape': 'triangle',
          'arrow-scale': 0.9,
          'width': 'data(width)',
          'curve-style': 'bezier',
          'opacity': 0.55,
        }},
        { selector: 'edge.highlight', style: {
          'line-color': '#1a73e8',
          'target-arrow-color': '#1a73e8',
          'opacity': 1,
          'z-index': 999,
        }},
        { selector: 'edge.faded', style: { 'opacity': 0.06 }},
      ],
      layout: { name: 'cose', idealEdgeLength: 110, nodeRepulsion: 5000, padding: 20, animate: true, animationDuration: 700 },
      wheelSensitivity: 0.25,
    });

    cy.on('tap', 'node', evt => {
      const id = parseInt(evt.target.data('id'), 10);
      select(id, { fromCy: true });
    });
    cy.on('tap', (evt) => {
      if (evt.target === cy) { clearSelection(); }
    });
    updateLabels();
    cyInitialized = true;
  }

  function updateLabels() {
    if (!cy) return;
    cy.style().selector('node').style('label', showLabels ? 'data(label)' : '').update();
  }

  document.getElementById('graph-fit').addEventListener('click', () => cy && cy.fit(null, 30));
  document.getElementById('graph-relayout').addEventListener('click', () => {
    if (cy) cy.layout({ name: 'cose', idealEdgeLength: 110, nodeRepulsion: 5000, padding: 20, animate: true }).run();
  });
  document.getElementById('graph-labels').addEventListener('click', (e) => {
    showLabels = !showLabels;
    e.currentTarget.classList.toggle('toggled', showLabels);
    updateLabels();
  });
  document.getElementById('graph-isolated').addEventListener('click', (e) => {
    hideIsolated = !hideIsolated;
    e.currentTarget.classList.toggle('toggled', hideIsolated);
    if (cy) { cy.destroy(); cy = null; cyInitialized = false; initCy(); }
  });

  // --- Matrix ---
  const mcanvas = document.getElementById('matrix-canvas');
  const mctx = mcanvas.getContext('2d');
  let matrixOrder = null;  // array of vertex indices in display order
  let matrixIdxOf = null;  // inverse map: vertex i → row/col position

  function computeOrder() {
    const mode = document.getElementById('mat-sort').value;
    const order = VERTICES.map(v => v.i);
    if (mode === 'index') { /* already sorted */ }
    else if (mode === 'class') order.sort((a, b) => {
      const va = byId[a], vb = byId[b];
      return va.class_fqn.localeCompare(vb.class_fqn) || va.method_name.localeCompare(vb.method_name);
    });
    else if (mode === 'out_deg') order.sort((a, b) => byId[b].out_deg - byId[a].out_deg);
    else if (mode === 'in_deg') order.sort((a, b) => byId[b].in_deg - byId[a].in_deg);
    matrixOrder = order;
    matrixIdxOf = new Array(N);
    order.forEach((vi, pos) => { matrixIdxOf[vi] = pos; });
  }

  function renderMatrix() {
    computeOrder();
    const cell = parseInt(document.getElementById('mat-cell').value, 10);
    const scale = document.getElementById('mat-scale').value;
    const size = N * cell;
    mcanvas.width = size;
    mcanvas.height = size;
    mctx.fillStyle = '#fff';
    mctx.fillRect(0, 0, size, size);

    const maxW = Math.max(1, ...EDGES.map(e => e.w));
    const logMax = Math.log1p(maxW);

    for (const e of EDGES) {
      const r = matrixIdxOf[e.s];
      const c = matrixIdxOf[e.d];
      let t;
      if (scale === 'binary') t = 1;
      else if (scale === 'log') t = Math.log1p(e.w) / logMax;
      else t = e.w / maxW;
      const rgb = weightColor(t);
      mctx.fillStyle = rgb;
      mctx.fillRect(c * cell, r * cell, cell, cell);
    }

    // Draw class separators if sorted by class
    if (document.getElementById('mat-sort').value === 'class') {
      mctx.strokeStyle = 'rgba(0,0,0,0.08)';
      mctx.lineWidth = 1;
      let prev = null;
      for (let pos = 0; pos < N; pos++) {
        const cls = byId[matrixOrder[pos]].class_fqn;
        if (prev !== null && cls !== prev) {
          const y = pos * cell + 0.5;
          mctx.beginPath(); mctx.moveTo(0, y); mctx.lineTo(size, y); mctx.stroke();
          const x = pos * cell + 0.5;
          mctx.beginPath(); mctx.moveTo(x, 0); mctx.lineTo(x, size); mctx.stroke();
        }
        prev = cls;
      }
    }
  }

  function weightColor(t) {
    // Ramp: #eaf2ff -> #1a73e8 -> #002c7a
    t = Math.min(1, Math.max(0, t));
    const a = [234, 242, 255], b = [26, 115, 232], c = [0, 44, 122];
    let rgb;
    if (t < 0.5) {
      const k = t / 0.5;
      rgb = a.map((v, i) => Math.round(v + (b[i] - v) * k));
    } else {
      const k = (t - 0.5) / 0.5;
      rgb = b.map((v, i) => Math.round(v + (c[i] - v) * k));
    }
    return `rgb(${rgb.join(',')})`;
  }

  mcanvas.addEventListener('click', (evt) => {
    const rect = mcanvas.getBoundingClientRect();
    const x = evt.clientX - rect.left, y = evt.clientY - rect.top;
    const cell = parseInt(document.getElementById('mat-cell').value, 10);
    // Account for CSS scaling: canvas.width may differ from rect.width
    const cx = x * (mcanvas.width / rect.width);
    const cy = y * (mcanvas.height / rect.height);
    const col = Math.floor(cx / cell), row = Math.floor(cy / cell);
    if (row >= 0 && row < N && col >= 0 && col < N) {
      const src = matrixOrder[row];
      const dst = matrixOrder[col];
      const edge = outEdges[src].find(e => e.dst === dst);
      if (edge) {
        select(src);
        showToast(`${shortLabel(byId[src])} -> ${shortLabel(byId[dst])} (w=${edge.w})`);
      } else {
        select(src);
      }
    }
  });
  document.getElementById('mat-sort').addEventListener('change', renderMatrix);
  document.getElementById('mat-scale').addEventListener('change', renderMatrix);
  document.getElementById('mat-cell').addEventListener('change', renderMatrix);

  // --- Vertex list ---
  let vlistSort = 'out_deg';
  let vlistFilter = '';

  function renderVlist() {
    const rows = VERTICES
      .filter(v => !vlistFilter || v.id.toLowerCase().includes(vlistFilter.toLowerCase()))
      .slice()
      .sort((a, b) => {
        if (vlistSort === 'out_deg') return b.out_deg - a.out_deg;
        if (vlistSort === 'in_deg') return b.in_deg - a.in_deg;
        return 0;
      });
    const container = document.getElementById('vlist');
    container.innerHTML = rows.map(v => `
      <div class="vlist-row" data-vi="${v.i}">
        <span class="deg">${v.out_deg}</span>
        <span class="deg">${v.in_deg}</span>
        <span class="id">${prettyVertexHtml(v)}</span>
      </div>`).join('');
    container.querySelectorAll('.vlist-row').forEach(r => {
      r.addEventListener('click', () => select(parseInt(r.dataset.vi, 10)));
    });
  }
  document.querySelectorAll('.vlist-header .sortable').forEach(el => {
    el.addEventListener('click', () => {
      vlistSort = el.dataset.sort;
      document.querySelectorAll('.vlist-header .sortable').forEach(s => {
        s.textContent = s.dataset.sort + (s.dataset.sort === vlistSort ? ' ▾' : '');
      });
      renderVlist();
    });
  });

  // --- Search ---
  document.getElementById('search').addEventListener('input', (e) => {
    vlistFilter = e.target.value;
    renderVlist();
  });

  // --- Selection ---
  let selectedIdx = null;

  function select(i, opts = {}) {
    selectedIdx = i;
    renderDetail(i);
    // Highlight in cy
    if (cy) {
      cy.elements().removeClass('selected highlight faded');
      const node = cy.getElementById(String(i));
      if (node && node.length) {
        node.addClass('selected');
        const connected = node.connectedEdges();
        connected.addClass('highlight');
        cy.elements().difference(node.union(connected).union(connected.connectedNodes())).addClass('faded');
        if (!opts.fromCy) cy.animate({ center: { eles: node }, zoom: Math.max(cy.zoom(), 1.0) }, { duration: 250 });
      }
    }
    // Highlight row in list
    document.querySelectorAll('.vlist-row').forEach(r => {
      r.classList.toggle('selected', parseInt(r.dataset.vi, 10) === i);
    });
  }

  function clearSelection() {
    selectedIdx = null;
    document.getElementById('detail').innerHTML = `
      <div class="detail-empty">
        <span class="material-symbols-outlined">info</span>
        Select a method from the graph, matrix, or list to see details.
      </div>`;
    if (cy) cy.elements().removeClass('selected highlight faded');
  }

  function renderDetail(i) {
    const v = byId[i];
    const out = outEdges[i].slice().sort((a, b) => b.w - a.w);
    const inE = inEdges[i].slice().sort((a, b) => b.w - a.w);

    const hasBody = v.body && v.body.length > 0;
    const params = (v.parameter_types || []).join(', ');

    document.getElementById('detail').innerHTML = `
      <div class="detail-card">
        <h2><span class="material-symbols-outlined">function</span>Vertex</h2>
        <div class="vertex-title">${prettyVertexHtml(v)}</div>
        <div class="meta-row"><span class="k">index</span><span class="v"><code>${v.i}</code></span></div>
        <div class="meta-row"><span class="k">class</span><span class="v"><code>${escapeHtml(v.class_fqn)}</code></span></div>
        <div class="meta-row"><span class="k">file</span><span class="v"><code>${escapeHtml(v.file_path)}</code></span></div>
        <div class="meta-row"><span class="k">params</span><span class="v">${params ? `<code>${escapeHtml(params)}</code>` : '<i>none</i>'}</span></div>
        <div class="meta-row"><span class="k">returns</span><span class="v"><code>${escapeHtml(v.return_type)}</code></span></div>
        <div class="meta-row"><span class="k">out / in</span><span class="v"><code>${v.out_deg}</code> / <code>${v.in_deg}</code></span></div>
      </div>
      <div class="detail-card">
        <h2><span class="material-symbols-outlined">call_made</span>Outgoing (${out.length})</h2>
        <div class="edge-table">
          ${out.length === 0 ? '<div class="edge-empty">No outgoing edges.</div>' :
            out.map(e => `<div class="edge-row" data-target="${e.dst}">
              <span class="w">${e.w}</span>
              <span class="target">${prettyVertexHtml(byId[e.dst])}</span>
            </div>`).join('')}
        </div>
      </div>
      <div class="detail-card">
        <h2><span class="material-symbols-outlined">call_received</span>Incoming (${inE.length})</h2>
        <div class="edge-table">
          ${inE.length === 0 ? '<div class="edge-empty">No incoming edges.</div>' :
            inE.map(e => `<div class="edge-row" data-target="${e.src}">
              <span class="w">${e.w}</span>
              <span class="target">${prettyVertexHtml(byId[e.src])}</span>
            </div>`).join('')}
        </div>
      </div>
      ${hasBody ? `
        <div class="detail-card">
          <h2><span class="material-symbols-outlined">code</span>Method body</h2>
          <pre class="code"><code class="language-java">${escapeHtml(v.body)}</code></pre>
        </div>` : ''}
    `;
    document.querySelectorAll('#detail .edge-row').forEach(r => {
      r.addEventListener('click', () => {
        select(parseInt(r.dataset.target, 10));
      });
    });
    if (hasBody) {
      document.querySelectorAll('#detail pre.code code').forEach(el => {
        try { hljs.highlightElement(el); } catch (err) {}
      });
    }
    // Scroll detail drawer back to top on selection change.
    document.getElementById('detail').scrollTop = 0;
  }

  let toastTimer = null;
  function showToast(msg) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('visible'), 2000);
  }

  // Initial render
  renderVlist();
  initCy();
})();
</script>
</body>
</html>
"""


def render_html(payload: dict) -> str:
    # Embed JSON safely inside <script>.
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Guard against accidental </script> sequences in vertex IDs / bodies.
    blob = blob.replace("</script", "<\\/script")
    return _HTML_TEMPLATE.replace("__PAYLOAD__", blob)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mgx-call-graph-viewer",
        description="Render a Material UI HTML viewer for a built call graph.",
    )
    parser.add_argument("graph_dir", type=Path, help="Directory with adjacency.pt and vertices.json.")
    parser.add_argument("-o", "--out", type=Path, required=True, help="Output .html file.")
    parser.add_argument(
        "--extraction-json",
        type=Path,
        default=None,
        help="Optional extracted_methods.json — enables method-body preview in the detail panel.",
    )
    args = parser.parse_args(argv)

    if not (args.graph_dir / "adjacency.pt").exists():
        print(f"error: {args.graph_dir}/adjacency.pt not found", file=sys.stderr)
        return 2
    if args.extraction_json is not None and not args.extraction_json.exists():
        print(f"error: {args.extraction_json} not found", file=sys.stderr)
        return 2

    payload = build_payload(args.graph_dir, args.extraction_json)
    html_text = render_html(payload)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html_text, encoding="utf-8")
    print(f"viewer: {payload['stats']['n']} vertices, {payload['stats']['e_unique']} edges -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
