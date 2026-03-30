from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pipeline.models import SampleResult
from pipeline.report import load_sample_result


def discover_modes(results_dir: Path) -> dict[str, Path]:
    modes = {}
    if not results_dir.is_dir():
        return modes
    for entry in sorted(results_dir.iterdir()):
        samples_dir = entry / "samples"
        if entry.is_dir() and samples_dir.is_dir():
            modes[entry.name] = samples_dir
    return modes


def load_all_samples(
    results_dir: Path,
    include_prompts: bool = True,
) -> tuple[dict[str, dict[str, dict]], list[str]]:
    modes_map = discover_modes(results_dir)
    if not modes_map:
        raise SystemExit(f"No mode directories with samples/ found in {results_dir}")

    mode_names = list(modes_map.keys())
    grouped: dict[str, dict[str, dict]] = {}

    for mode, samples_dir in modes_map.items():
        for path in sorted(samples_dir.glob("sample_*.json")):
            m = re.search(r"sample_(\d+)", path.stem)
            if not m:
                continue
            idx = m.group(1)
            sample = load_sample_result(path)
            d = sample.to_dict()
            if not include_prompts:
                d.pop("prompt", None)
            grouped.setdefault(idx, {})[mode] = d

    return grouped, mode_names


def load_summary(results_dir: Path) -> dict | None:
    p = results_dir / "summary.json"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def generate_viewer(
    results_dir: Path,
    output_path: Path,
    include_prompts: bool = True,
) -> Path:
    grouped, mode_names = load_all_samples(results_dir, include_prompts)
    summary = load_summary(results_dir)

    viewer_data = {
        "samples": grouped,
        "modes": mode_names,
        "summary": summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    json_str = json.dumps(viewer_data, ensure_ascii=False)
    json_str = json_str.replace("</", "<\\/")

    html = HTML_TEMPLATE.replace('"__DATA_JSON__"', json_str)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=output_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, output_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    print(f"Viewer written to {output_path}")
    return output_path


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Method Generation Experiment Viewer</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/diff2html@3.4.47/bundles/css/diff2html.min.css">
<style>
:root {
  --bg: #1e1e2e;
  --bg-surface: #252536;
  --bg-elevated: #2d2d44;
  --bg-hover: #35354d;
  --text: #cdd6f4;
  --text-dim: #8888a8;
  --text-bright: #ffffff;
  --border: #3d3d5c;
  --accent: #89b4fa;
  --mode-none: #9399b2;
  --mode-ordered: #a6e3a1;
  --mode-shuffled: #fab387;
  --em-yes: #a6e3a1;
  --em-no: #f38ba8;
  --bar-bg: #3d3d5c;
  --scrollbar: #45456a;
  --scrollbar-hover: #5a5a7a;
  --sidebar-w: 300px;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  height: 100vh;
  overflow: hidden;
}

::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--scrollbar); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--scrollbar-hover); }

/* Layout */
#app {
  display: flex;
  height: 100vh;
}

/* Sidebar */
#sidebar {
  width: var(--sidebar-w);
  min-width: var(--sidebar-w);
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

#sidebar-header {
  padding: 16px;
  border-bottom: 1px solid var(--border);
}

#sidebar-header h2 {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-bright);
  margin-bottom: 10px;
}

#search-input {
  width: 100%;
  padding: 8px 10px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-size: 13px;
  outline: none;
}
#search-input:focus { border-color: var(--accent); }
#search-input::placeholder { color: var(--text-dim); }

#filters {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.filter-chip {
  padding: 3px 8px;
  font-size: 11px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-dim);
  cursor: pointer;
  transition: all .15s;
}
.filter-chip.active {
  background: var(--accent);
  color: var(--bg);
  border-color: var(--accent);
}

#sample-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
}

.sample-item {
  display: flex;
  align-items: center;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  gap: 8px;
  transition: background .1s;
  margin-bottom: 2px;
}
.sample-item:hover { background: var(--bg-hover); }
.sample-item.active { background: var(--accent); color: var(--bg); }
.sample-item.active .sample-idx { color: var(--bg); opacity: .7; }
.sample-item.active .sample-id { color: var(--bg); }
.sample-item.active .dot { opacity: .8; }

.sample-idx {
  font-size: 11px;
  color: var(--text-dim);
  min-width: 28px;
  font-variant-numeric: tabular-nums;
}
.sample-id {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dots {
  display: flex;
  gap: 3px;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

#sidebar-footer {
  padding: 10px 16px;
  border-top: 1px solid var(--border);
  font-size: 11px;
  color: var(--text-dim);
}

/* Main content */
#main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

#header {
  padding: 16px 24px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-surface);
}

#method-id {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-bright);
  margin-bottom: 4px;
}

#method-meta {
  font-size: 12px;
  color: var(--text-dim);
}

#method-meta code {
  background: var(--bg-elevated);
  padding: 2px 5px;
  border-radius: 3px;
  font-size: 11px;
}

/* Metrics row */
#metrics-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
  padding: 16px 24px;
  border-bottom: 1px solid var(--border);
}

.metric-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
  border-top: 3px solid var(--mode-none);
}
.metric-card[data-mode="ordered_augmentation"] { border-top-color: var(--mode-ordered); }
.metric-card[data-mode="shuffled_augmentation"] { border-top-color: var(--mode-shuffled); }

.metric-card-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-dim);
  margin-bottom: 10px;
  text-transform: uppercase;
  letter-spacing: .5px;
}

.metric-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.metric-item label {
  font-size: 10px;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: .3px;
}

.metric-value {
  font-size: 16px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.metric-bar {
  height: 4px;
  background: var(--bar-bg);
  border-radius: 2px;
  margin-top: 3px;
  overflow: hidden;
}
.metric-bar-fill {
  height: 100%;
  border-radius: 2px;
  transition: width .3s;
}

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
}
.badge-yes { background: var(--em-yes); color: #1e1e2e; }
.badge-no { background: var(--em-no); color: #1e1e2e; }
.badge-na { background: var(--bar-bg); color: var(--text-dim); }

/* Tabs */
#tab-bar {
  display: flex;
  gap: 0;
  padding: 0 24px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border);
}

.tab-btn {
  padding: 10px 18px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-dim);
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: all .15s;
}
.tab-btn:hover { color: var(--text); }
.tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }

.tab-hint {
  font-size: 10px;
  color: var(--text-dim);
  opacity: .5;
  margin-left: 4px;
}

/* Tab content */
#tab-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
}

/* Code blocks */
.code-section { margin-bottom: 20px; }
.code-section-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.mode-tag {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
}
.mode-tag-none { background: var(--mode-none); color: var(--bg); }
.mode-tag-ordered { background: var(--mode-ordered); color: var(--bg); }
.mode-tag-shuffled { background: var(--mode-shuffled); color: var(--bg); }

.code-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
  gap: 12px;
}

.code-block {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}

.code-block-header {
  padding: 8px 12px;
  font-size: 11px;
  font-weight: 600;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--bg-surface);
}

.code-block pre {
  margin: 0;
  padding: 12px;
  overflow-x: auto;
  font-size: 12.5px;
  line-height: 1.5;
}

.code-block pre code {
  font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
}

.not-available {
  padding: 24px;
  text-align: center;
  color: var(--text-dim);
  font-style: italic;
}

/* Diff tab */
#diff-controls {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  align-items: center;
  flex-wrap: wrap;
}

#diff-controls select,
#diff-controls button {
  padding: 6px 12px;
  font-size: 12px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  cursor: pointer;
}
#diff-controls button.active {
  background: var(--accent);
  color: var(--bg);
  border-color: var(--accent);
}

#diff-output {
  border-radius: 6px;
  overflow: hidden;
}
#diff-output .d2h-wrapper { font-size: 12px; }
#diff-output .d2h-file-header { display: none; }

/* Prompt tab */
.prompt-section {
  margin-bottom: 16px;
}
.prompt-section summary {
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 0;
  color: var(--text);
}
.prompt-section summary:hover { color: var(--text-bright); }

.fim-token {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 700;
  font-family: monospace;
}
.fim-prefix { background: #45475a; color: #89b4fa; }
.fim-suffix { background: #45475a; color: #f9e2af; }
.fim-middle { background: #45475a; color: #a6e3a1; }

/* Meta tab */
.meta-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  margin-bottom: 16px;
}
.meta-table th,
.meta-table td {
  padding: 8px 12px;
  border: 1px solid var(--border);
  text-align: left;
}
.meta-table th {
  background: var(--bg-surface);
  font-weight: 600;
  color: var(--text-dim);
  font-size: 11px;
  text-transform: uppercase;
}
.meta-table td { font-variant-numeric: tabular-nums; }

.error-block {
  background: #2d1b1f;
  border: 1px solid #5c2d33;
  border-radius: 6px;
  padding: 12px;
  margin-top: 8px;
  font-family: monospace;
  font-size: 12px;
  white-space: pre-wrap;
  color: #f38ba8;
}

/* Empty state */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 60vh;
  color: var(--text-dim);
}
.empty-state h3 { font-size: 18px; margin-bottom: 8px; color: var(--text); }

/* Override diff2html colors for dark theme */
.d2h-code-linenumber { color: var(--text-dim) !important; }
.d2h-info { background: var(--bg-elevated) !important; color: var(--text-dim) !important; border-color: var(--border) !important; }
.d2h-file-diff .d2h-del.d2h-change { background: #3d1f2a !important; }
.d2h-file-diff .d2h-ins.d2h-change { background: #1f3d2a !important; }
.d2h-del { background: #2d1b1f !important; }
.d2h-ins { background: #1b2d1f !important; }
.d2h-code-line del { background: #5c2d33 !important; }
.d2h-code-line ins { background: #2d5c33 !important; }
.d2h-code-side-line { font-family: 'JetBrains Mono', 'Fira Code', monospace !important; }
.d2h-file-wrapper { border-color: var(--border) !important; }
.d2h-file-collapse, .d2h-file-list-wrapper { display: none !important; }

@media (max-width: 900px) {
  #sidebar { width: 220px; min-width: 220px; }
  .code-grid { grid-template-columns: 1fr; }
  #metrics-row { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div id="app">
  <aside id="sidebar">
    <div id="sidebar-header">
      <h2>Samples</h2>
      <input id="search-input" type="text" placeholder="Search by method ID... ( / )">
      <div id="filters">
        <button class="filter-chip" data-filter="em">EM Only</button>
        <button class="filter-chip" data-filter="compilable">Compilable</button>
        <button class="filter-chip" data-filter="errors">Has Errors</button>
      </div>
    </div>
    <div id="sample-list"></div>
    <div id="sidebar-footer"></div>
  </aside>
  <div id="main">
    <div id="header">
      <div id="method-id"></div>
      <div id="method-meta"></div>
    </div>
    <div id="metrics-row"></div>
    <div id="tab-bar">
      <button class="tab-btn active" data-tab="code">Code <span class="tab-hint">1</span></button>
      <button class="tab-btn" data-tab="diff">Diff <span class="tab-hint">2</span></button>
      <button class="tab-btn" data-tab="prompt">Prompt <span class="tab-hint">3</span></button>
      <button class="tab-btn" data-tab="meta">Meta <span class="tab-hint">4</span></button>
    </div>
    <div id="tab-content"></div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/java.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jsdiff/5.2.0/diff.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/diff2html@3.4.47/bundles/js/diff2html.min.js"></script>

<script>
const DATA = "__DATA_JSON__";

const MODES = DATA.modes || [];
const SAMPLES = DATA.samples || {};
const SAMPLE_KEYS = Object.keys(SAMPLES).sort((a, b) => parseInt(a) - parseInt(b));

const MODE_COLORS = {
  no_augmentation: { tag: 'mode-tag-none', color: '#9399b2' },
  ordered_augmentation: { tag: 'mode-tag-ordered', color: '#a6e3a1' },
  shuffled_augmentation: { tag: 'mode-tag-shuffled', color: '#fab387' },
};

const state = {
  currentIdx: 0,
  activeTab: 'code',
  searchQuery: '',
  filters: { em: false, compilable: false, errors: false },
  diffPair: null,
  diffFormat: 'side-by-side',
};

// --- Helpers ---
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function getModeColor(mode) {
  return MODE_COLORS[mode] || { tag: 'mode-tag-none', color: '#9399b2' };
}

function getModeLabel(mode) {
  return mode.replace(/_/g, ' ');
}

function getSampleData(key) {
  return SAMPLES[key] || {};
}

function getAnySample(key) {
  const data = getSampleData(key);
  for (const m of MODES) { if (data[m]) return data[m]; }
  return null;
}

function getFilteredKeys() {
  return SAMPLE_KEYS.filter(key => {
    const any = getAnySample(key);
    if (!any) return false;
    if (state.searchQuery) {
      const q = state.searchQuery.toLowerCase();
      if (!any.method_id.toLowerCase().includes(q) &&
          !key.includes(q)) return false;
    }
    if (state.filters.em) {
      const data = getSampleData(key);
      const hasEm = MODES.some(m => data[m]?.metrics?.em);
      if (!hasEm) return false;
    }
    if (state.filters.compilable) {
      const data = getSampleData(key);
      const allComp = MODES.every(m => !data[m] || data[m].metrics?.compilable !== false);
      if (!allComp) return false;
    }
    if (state.filters.errors) {
      const data = getSampleData(key);
      const hasErr = MODES.some(m => data[m]?.metrics?.compile_errors?.length > 0);
      if (!hasErr) return false;
    }
    return true;
  });
}

// --- Render Sidebar ---
function renderSidebar() {
  const keys = getFilteredKeys();
  const list = document.getElementById('sample-list');

  list.innerHTML = keys.map(key => {
    const data = getSampleData(key);
    const any = getAnySample(key);
    const dots = MODES.map(m => {
      const s = data[m];
      if (!s) return `<span class="dot" style="background:#3d3d5c" title="${getModeLabel(m)}: N/A"></span>`;
      const es = s.metrics?.es ?? 0;
      const color = s.metrics?.em ? '#a6e3a1' : es > 0.5 ? '#f9e2af' : '#f38ba8';
      return `<span class="dot" style="background:${color}" title="${getModeLabel(m)}: ES=${es.toFixed(2)}"></span>`;
    }).join('');

    const active = SAMPLE_KEYS[state.currentIdx] === key ? 'active' : '';
    const mid = any?.method_id || key;
    const short = mid.length > 30 ? '...' + mid.slice(-28) : mid;

    return `<div class="sample-item ${active}" data-key="${key}">
      <span class="sample-idx">${key}</span>
      <span class="sample-id" title="${esc(mid)}">${esc(short)}</span>
      <span class="dots">${dots}</span>
    </div>`;
  }).join('');

  document.getElementById('sidebar-footer').textContent =
    `${keys.length} / ${SAMPLE_KEYS.length} samples`;
}

// --- Render Header ---
function renderHeader() {
  const key = SAMPLE_KEYS[state.currentIdx];
  const any = getAnySample(key);
  if (!any) return;

  document.getElementById('method-id').textContent = any.method_id || 'Unknown';
  document.getElementById('method-meta').innerHTML =
    `<code>${esc(any.file_path || '')}</code> &mdash; <code>${esc(any.method_signature || '')}</code>`;
}

// --- Render Metrics ---
function renderMetrics() {
  const key = SAMPLE_KEYS[state.currentIdx];
  const data = getSampleData(key);
  const row = document.getElementById('metrics-row');

  row.innerHTML = MODES.map(mode => {
    const s = data[mode];
    const mc = getModeColor(mode);

    if (!s) {
      return `<div class="metric-card" data-mode="${mode}">
        <div class="metric-card-title">${getModeLabel(mode)}</div>
        <div class="not-available">Not available</div>
      </div>`;
    }

    const m = s.metrics;
    const emBadge = m.em
      ? '<span class="badge badge-yes">MATCH</span>'
      : '<span class="badge badge-no">NO</span>';

    const compBadge = m.compilable === true
      ? '<span class="badge badge-yes">YES</span>'
      : m.compilable === false
        ? '<span class="badge badge-no">NO</span>'
        : '<span class="badge badge-na">N/A</span>';

    function bar(val, color) {
      const pct = Math.round(val * 100);
      return `<div class="metric-bar"><div class="metric-bar-fill" style="width:${pct}%;background:${color}"></div></div>`;
    }

    return `<div class="metric-card" data-mode="${mode}">
      <div class="metric-card-title">${getModeLabel(mode)}</div>
      <div class="metric-grid">
        <div class="metric-item">
          <label>Exact Match</label><br>${emBadge}
        </div>
        <div class="metric-item">
          <label>Compilable</label><br>${compBadge}
        </div>
        <div class="metric-item">
          <label>Edit Similarity</label>
          <div class="metric-value" style="color:${mc.color}">${(m.es ?? 0).toFixed(4)}</div>
          ${bar(m.es ?? 0, mc.color)}
        </div>
        <div class="metric-item">
          <label>Token IoU</label>
          <div class="metric-value" style="color:${mc.color}">${(m.iou ?? 0).toFixed(4)}</div>
          ${bar(m.iou ?? 0, mc.color)}
        </div>
        <div class="metric-item">
          <label>LCS Ratio</label>
          <div class="metric-value" style="color:${mc.color}">${(m.lcs_ratio ?? 0).toFixed(4)}</div>
          ${bar(m.lcs_ratio ?? 0, mc.color)}
        </div>
        <div class="metric-item">
          <label>LCS Length</label>
          <div class="metric-value">${m.lcs_length ?? 0}</div>
        </div>
      </div>
    </div>`;
  }).join('');
}

// --- Code Block ---
function codeBlock(title, tagClass, code) {
  if (!code && code !== '') return `<div class="code-block">
    <div class="code-block-header"><span class="mode-tag ${tagClass}">${title}</span></div>
    <div class="not-available">Not available</div>
  </div>`;

  return `<div class="code-block">
    <div class="code-block-header"><span class="mode-tag ${tagClass}">${title}</span></div>
    <pre><code class="language-java">${esc(code)}</code></pre>
  </div>`;
}

// --- Tab: Code ---
function renderCodeTab() {
  const key = SAMPLE_KEYS[state.currentIdx];
  const data = getSampleData(key);
  const any = getAnySample(key);
  if (!any) return '<div class="not-available">No data</div>';

  let html = '<div class="code-section"><div class="code-section-title">Ground Truth</div>';
  html += codeBlock('Ground Truth', 'mode-tag-none', any.ground_truth);
  html += '</div>';

  html += '<div class="code-section"><div class="code-section-title">Generated Code</div>';
  html += '<div class="code-grid">';
  for (const mode of MODES) {
    const s = data[mode];
    const mc = getModeColor(mode);
    html += codeBlock(getModeLabel(mode), mc.tag, s?.generated);
  }
  html += '</div></div>';

  return html;
}

// --- Tab: Diff ---
function buildDiffPairs() {
  const key = SAMPLE_KEYS[state.currentIdx];
  const data = getSampleData(key);
  const any = getAnySample(key);
  const pairs = [];

  for (const mode of MODES) {
    if (data[mode]) {
      pairs.push({
        label: `${getModeLabel(mode)}: Generated vs Ground Truth`,
        a: { name: 'Ground Truth', code: any.ground_truth || '' },
        b: { name: `Generated (${getModeLabel(mode)})`, code: data[mode].generated || '' },
      });
    }
  }

  for (let i = 0; i < MODES.length; i++) {
    for (let j = i + 1; j < MODES.length; j++) {
      if (data[MODES[i]] && data[MODES[j]]) {
        pairs.push({
          label: `${getModeLabel(MODES[i])} vs ${getModeLabel(MODES[j])}`,
          a: { name: getModeLabel(MODES[i]), code: data[MODES[i]].generated || '' },
          b: { name: getModeLabel(MODES[j]), code: data[MODES[j]].generated || '' },
        });
      }
    }
  }
  return pairs;
}

function renderDiffTab() {
  const pairs = buildDiffPairs();
  if (!pairs.length) return '<div class="not-available">No data for diff</div>';

  if (state.diffPair === null || state.diffPair >= pairs.length) state.diffPair = 0;

  const options = pairs.map((p, i) =>
    `<option value="${i}" ${i === state.diffPair ? 'selected' : ''}>${esc(p.label)}</option>`
  ).join('');

  const fmt = state.diffFormat;
  let html = `<div id="diff-controls">
    <select id="diff-select">${options}</select>
    <button class="diff-fmt ${fmt === 'side-by-side' ? 'active' : ''}" data-fmt="side-by-side">Side by Side</button>
    <button class="diff-fmt ${fmt === 'line-by-line' ? 'active' : ''}" data-fmt="line-by-line">Unified</button>
  </div>`;
  html += '<div id="diff-output"></div>';
  return html;
}

function computeDiff() {
  const pairs = buildDiffPairs();
  if (!pairs.length) return;
  const pair = pairs[state.diffPair ?? 0];
  if (!pair) return;

  const diffStr = Diff.createTwoFilesPatch(
    pair.a.name, pair.b.name,
    pair.a.code, pair.b.code,
    '', '', { context: 5 }
  );

  const container = document.getElementById('diff-output');
  if (!container) return;
  container.innerHTML = Diff2Html.html(diffStr, {
    drawFileList: false,
    matching: 'lines',
    outputFormat: state.diffFormat,
  });
}

// --- Tab: Prompt ---
function renderPromptTab() {
  const key = SAMPLE_KEYS[state.currentIdx];
  const data = getSampleData(key);
  let html = '';

  for (const mode of MODES) {
    const s = data[mode];
    if (!s) continue;

    html += '<div class="prompt-section">';

    if (s.augmentation_block) {
      html += `<div class="code-section-title">Augmentation Block
        <span class="mode-tag ${getModeColor(mode).tag}">${getModeLabel(mode)}</span></div>`;
      html += `<div class="code-block"><pre><code class="language-java">${esc(s.augmentation_block)}</code></pre></div>`;
      html += '<br>';
    }

    if (s.prompt) {
      html += `<details>
        <summary>Full FIM Prompt <span class="mode-tag ${getModeColor(mode).tag}">${getModeLabel(mode)}</span></summary>
        <div class="code-block" style="margin-top:8px">
          <pre><code>${formatPrompt(s.prompt)}</code></pre>
        </div>
      </details>`;
    } else {
      html += `<div style="font-size:12px;color:var(--text-dim);">Prompt not included (use --no-prompts flag was set)
        <span class="mode-tag ${getModeColor(mode).tag}">${getModeLabel(mode)}</span></div>`;
    }
    html += '</div>';
  }

  return html || '<div class="not-available">No prompt data</div>';
}

function formatPrompt(prompt) {
  let s = esc(prompt);
  s = s.replace(/&lt;\|fim_prefix\|&gt;/g, '<span class="fim-token fim-prefix">&lt;|fim_prefix|&gt;</span>');
  s = s.replace(/&lt;\|fim_suffix\|&gt;/g, '<span class="fim-token fim-suffix">&lt;|fim_suffix|&gt;</span>');
  s = s.replace(/&lt;\|fim_middle\|&gt;/g, '<span class="fim-token fim-middle">&lt;|fim_middle|&gt;</span>');
  return s;
}

// --- Tab: Meta ---
function renderMetaTab() {
  const key = SAMPLE_KEYS[state.currentIdx];
  const data = getSampleData(key);
  let html = '';

  // LLM Response table
  html += '<div class="code-section-title">LLM Response</div>';
  html += '<table class="meta-table"><thead><tr><th>Property</th>';
  for (const m of MODES) html += `<th>${getModeLabel(m)}</th>`;
  html += '</tr></thead><tbody>';

  const props = [
    { key: 'finish_reason', label: 'Finish Reason' },
    { key: 'latency_ms', label: 'Latency (ms)', fmt: v => typeof v === 'number' ? v.toFixed(0) : 'N/A' },
  ];

  for (const prop of props) {
    html += `<tr><td>${prop.label}</td>`;
    for (const m of MODES) {
      const s = data[m];
      const v = s?.llm_response?.[prop.key];
      html += `<td>${prop.fmt ? prop.fmt(v) : (v ?? 'N/A')}</td>`;
    }
    html += '</tr>';
  }

  // Usage sub-properties
  for (const ukey of ['prompt_tokens', 'completion_tokens', 'total_tokens']) {
    html += `<tr><td>${ukey}</td>`;
    for (const m of MODES) {
      const s = data[m];
      html += `<td>${s?.llm_response?.usage?.[ukey] ?? 'N/A'}</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';

  // Compilation details
  html += '<div class="code-section-title" style="margin-top:20px;">Compilation Details</div>';
  html += '<table class="meta-table"><thead><tr><th>Property</th>';
  for (const m of MODES) html += `<th>${getModeLabel(m)}</th>`;
  html += '</tr></thead><tbody>';

  html += '<tr><td>Compilable</td>';
  for (const m of MODES) {
    const s = data[m];
    const c = s?.metrics?.compilable;
    html += `<td>${c === true ? '<span class="badge badge-yes">YES</span>' :
      c === false ? '<span class="badge badge-no">NO</span>' : '<span class="badge badge-na">N/A</span>'}</td>`;
  }
  html += '</tr>';

  html += '<tr><td>Exit Code</td>';
  for (const m of MODES) {
    const s = data[m];
    html += `<td>${s?.metrics?.compile_exit_code ?? 'N/A'}</td>`;
  }
  html += '</tr></tbody></table>';

  // Compile errors
  for (const mode of MODES) {
    const s = data[mode];
    const errs = s?.metrics?.compile_errors;
    if (errs && errs.length) {
      html += `<div class="code-section-title" style="margin-top:12px;">Compile Errors
        <span class="mode-tag ${getModeColor(mode).tag}">${getModeLabel(mode)}</span></div>`;
      html += `<div class="error-block">${esc(errs.join('\n'))}</div>`;
    }
  }

  // Invocations
  const any = getAnySample(key);
  if (any?.invocations_ordered?.length) {
    html += '<div class="code-section-title" style="margin-top:24px;">Invocations (Ordered)</div>';
    html += '<table class="meta-table"><thead><tr><th>#</th><th>Signature</th><th>Resolution</th></tr></thead><tbody>';
    for (const inv of any.invocations_ordered) {
      html += `<tr><td>${inv.order_index ?? ''}</td><td><code>${esc(inv.signature)}</code></td><td>${inv.resolution_mode}</td></tr>`;
    }
    html += '</tbody></table>';
  }

  return html;
}

// --- Main Render ---
function renderTabContent() {
  const c = document.getElementById('tab-content');
  switch (state.activeTab) {
    case 'code': c.innerHTML = renderCodeTab(); break;
    case 'diff': c.innerHTML = renderDiffTab(); break;
    case 'prompt': c.innerHTML = renderPromptTab(); break;
    case 'meta': c.innerHTML = renderMetaTab(); break;
  }

  // Highlight code blocks
  c.querySelectorAll('pre code.language-java').forEach(el => {
    if (typeof hljs !== 'undefined') hljs.highlightElement(el);
  });

  // Compute diff after DOM is ready
  if (state.activeTab === 'diff') {
    computeDiff();
    bindDiffControls();
  }
}

function render() {
  renderSidebar();
  renderHeader();
  renderMetrics();
  renderTabContent();
}

function selectSample(key) {
  const idx = SAMPLE_KEYS.indexOf(key);
  if (idx < 0) return;
  state.currentIdx = idx;
  state.diffPair = 0;
  render();

  const active = document.querySelector('.sample-item.active');
  if (active) active.scrollIntoView({ block: 'nearest' });
}

// --- Event Binding ---
document.getElementById('sample-list').addEventListener('click', e => {
  const item = e.target.closest('.sample-item');
  if (item) selectSample(item.dataset.key);
});

document.getElementById('search-input').addEventListener('input', e => {
  state.searchQuery = e.target.value;
  renderSidebar();
});

document.querySelectorAll('.filter-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    const f = chip.dataset.filter;
    state.filters[f] = !state.filters[f];
    chip.classList.toggle('active');
    renderSidebar();
  });
});

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.activeTab = btn.dataset.tab;
    renderTabContent();
  });
});

function bindDiffControls() {
  const sel = document.getElementById('diff-select');
  if (sel) {
    sel.addEventListener('change', () => {
      state.diffPair = parseInt(sel.value);
      computeDiff();
    });
  }
  document.querySelectorAll('.diff-fmt').forEach(btn => {
    btn.addEventListener('click', () => {
      state.diffFormat = btn.dataset.fmt;
      document.querySelectorAll('.diff-fmt').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      computeDiff();
    });
  });
}

// Keyboard nav
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') {
    if (e.key === 'Escape') e.target.blur();
    return;
  }

  const filtered = getFilteredKeys();
  const currentKey = SAMPLE_KEYS[state.currentIdx];
  const currentFiltered = filtered.indexOf(currentKey);

  if (e.key === 'ArrowDown' || e.key === 'j') {
    e.preventDefault();
    if (currentFiltered < filtered.length - 1) selectSample(filtered[currentFiltered + 1]);
  } else if (e.key === 'ArrowUp' || e.key === 'k') {
    e.preventDefault();
    if (currentFiltered > 0) selectSample(filtered[currentFiltered - 1]);
  } else if (e.key === '/') {
    e.preventDefault();
    document.getElementById('search-input').focus();
  } else if (e.key >= '1' && e.key <= '4') {
    const tabs = ['code', 'diff', 'prompt', 'meta'];
    const tab = tabs[parseInt(e.key) - 1];
    if (tab) {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelector(`.tab-btn[data-tab="${tab}"]`).classList.add('active');
      state.activeTab = tab;
      renderTabContent();
    }
  }
});

// --- Init ---
if (SAMPLE_KEYS.length === 0) {
  document.getElementById('main').innerHTML = `<div class="empty-state">
    <h3>No samples loaded</h3>
    <p>Run the experiment first, then regenerate the viewer.</p>
  </div>`;
} else {
  render();
}
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(
        description="Generate an interactive HTML viewer for experiment results"
    )
    parser.add_argument("--results-dir", default="./results",
                        help="Path to results directory (default: ./results)")
    parser.add_argument("--output", default=None,
                        help="Output HTML path (default: {results-dir}/viewer.html)")
    parser.add_argument("--no-prompts", action="store_true",
                        help="Exclude full prompts to reduce file size")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    output = Path(args.output) if args.output else results_dir / "viewer.html"

    generate_viewer(results_dir, output, include_prompts=not args.no_prompts)


if __name__ == "__main__":
    main()
