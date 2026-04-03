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
<link id="hljs-theme" rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/diff2html@3.4.47/bundles/css/diff2html.min.css">
<style>
:root, [data-theme="dark"] {
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
  --diff-del-bg: #2a1520;
  --diff-ins-bg: #152a1a;
  --diff-del-line: #f38ba8;
  --diff-ins-line: #a6e3a1;
  --diff-del-highlight: rgba(243,139,168,0.25);
  --diff-ins-highlight: rgba(166,227,161,0.25);
  --diff-text: #cdd6f4;
  --diff-gutter: #1e1e2e;
  --diff-gutter-text: #6c7086;
  --diff-info-bg: #2d2d44;
  --hljs-theme: "atom-one-dark";
}

[data-theme="light"] {
  --bg: #eff1f5;
  --bg-surface: #ffffff;
  --bg-elevated: #e6e9ef;
  --bg-hover: #dce0e8;
  --text: #4c4f69;
  --text-dim: #7c7f93;
  --text-bright: #1e1e2e;
  --border: #ccd0da;
  --accent: #1e66f5;
  --mode-none: #7c7f93;
  --mode-ordered: #40a02b;
  --mode-shuffled: #fe640b;
  --em-yes: #40a02b;
  --em-no: #d20f39;
  --bar-bg: #ccd0da;
  --scrollbar: #bcc0cc;
  --scrollbar-hover: #acb0be;
  --diff-del-bg: #fce4ec;
  --diff-ins-bg: #e8f5e9;
  --diff-del-line: #d20f39;
  --diff-ins-line: #40a02b;
  --diff-del-highlight: rgba(210,15,57,0.15);
  --diff-ins-highlight: rgba(64,160,43,0.15);
  --diff-text: #4c4f69;
  --diff-gutter: #e6e9ef;
  --diff-gutter-text: #9ca0b0;
  --diff-info-bg: #e6e9ef;
  --hljs-theme: "atom-one-light";
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
.mode-tag-rag { background: var(--accent); color: var(--bg); }

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

/* Override diff2html colors — themed */
.d2h-wrapper { background: transparent !important; }
.d2h-file-wrapper { border: 1px solid var(--border) !important; border-radius: 6px !important; overflow: hidden !important; }
.d2h-file-header { display: none !important; }
.d2h-file-collapse, .d2h-file-list-wrapper { display: none !important; }

.d2h-code-linenumber { color: var(--diff-gutter-text) !important; background: var(--diff-gutter) !important; border-color: var(--border) !important; }
.d2h-info { background: var(--diff-info-bg) !important; color: var(--text-dim) !important; border-color: var(--border) !important; }
.d2h-code-side-line, .d2h-code-line { font-family: 'JetBrains Mono', 'Fira Code', monospace !important; }

/* Unchanged lines */
.d2h-code-side-linenumber { color: var(--diff-gutter-text) !important; background: var(--diff-gutter) !important; border-color: var(--border) !important; }
.d2h-cntx { background: var(--bg) !important; color: var(--diff-text) !important; }
.d2h-cntx .d2h-code-side-line, .d2h-cntx .d2h-code-line { color: var(--diff-text) !important; }

/* Deleted lines — use left-border accent instead of heavy bg */
.d2h-del { background: var(--diff-del-bg) !important; border-color: var(--border) !important; }
.d2h-del .d2h-code-side-line, .d2h-del .d2h-code-line { color: var(--diff-text) !important; }
.d2h-del .d2h-code-side-linenumber { color: var(--diff-del-line) !important; background: var(--diff-del-bg) !important; }
.d2h-code-line del, .d2h-code-side-line del { background: var(--diff-del-highlight) !important; color: var(--diff-del-line) !important; text-decoration: none !important; border-radius: 2px; padding: 1px 0; }

/* Inserted lines */
.d2h-ins { background: var(--diff-ins-bg) !important; border-color: var(--border) !important; }
.d2h-ins .d2h-code-side-line, .d2h-ins .d2h-code-line { color: var(--diff-text) !important; }
.d2h-ins .d2h-code-side-linenumber { color: var(--diff-ins-line) !important; background: var(--diff-ins-bg) !important; }
.d2h-code-line ins, .d2h-code-side-line ins { background: var(--diff-ins-highlight) !important; color: var(--diff-ins-line) !important; text-decoration: none !important; border-radius: 2px; padding: 1px 0; }

/* Ensure tables and cells inherit properly */
.d2h-diff-table { border-collapse: collapse !important; }
.d2h-diff-tbody tr td { border-color: var(--border) !important; }

/* Invocations tab */
.inv-comparison-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 16px; }
.inv-mode-card { background: var(--bg-elevated); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
.inv-mode-header { margin-bottom: 12px; }
.inv-summary { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
.inv-metric { display: flex; flex-direction: column; align-items: center; }
.inv-metric label { font-size: 11px; color: var(--muted); text-transform: uppercase; }
.inv-metric-val { font-size: 16px; font-weight: 600; color: var(--text); }
.inv-columns { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 12px; }
.inv-col { padding: 8px; border-radius: 6px; }
.inv-col-gt-only { background: rgba(243,139,168,0.1); border: 1px solid rgba(243,139,168,0.3); }
.inv-col-both { background: rgba(166,227,161,0.1); border: 1px solid rgba(166,227,161,0.3); }
.inv-col-gen-only { background: rgba(249,226,175,0.1); border: 1px solid rgba(249,226,175,0.3); }
.inv-col-title { font-size: 12px; font-weight: 600; color: var(--muted); margin-bottom: 6px; }
.inv-count { font-weight: 400; opacity: 0.7; }
.inv-item { font-size: 13px; padding: 2px 0; }
.inv-empty { font-size: 12px; color: var(--muted); font-style: italic; }
.inv-details { margin-top: 4px; }
.inv-details summary { font-size: 12px; color: var(--muted); cursor: pointer; }
.inv-extracted { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.inv-tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-family: monospace; }
.inv-tag-match { background: rgba(166,227,161,0.2); color: #a6e3a1; }
.inv-tag-extra { background: rgba(249,226,175,0.2); color: #f9e2af; }

/* Coverage display */
.cov-line { display: flex; font-family: monospace; font-size: 13px; line-height: 1.5; }
.cov-line-nr { width: 40px; text-align: right; padding-right: 8px; color: var(--muted); user-select: none; flex-shrink: 0; }
.cov-line-src { flex: 1; white-space: pre; padding: 0 4px; }
.cov-covered { background: rgba(166,227,161,0.15); }
.cov-missed { background: rgba(243,139,168,0.15); }
.cov-container { border: 1px solid var(--border); border-radius: 6px; padding: 8px; overflow-x: auto; max-height: 400px; overflow-y: auto; }
.cov-summary { margin-bottom: 8px; font-size: 14px; }
.cov-summary strong { color: var(--accent); }
.cov-test-files { margin-top: 8px; }
.cov-test-files code { font-size: 12px; }

@media (max-width: 900px) {
  #sidebar { width: 220px; min-width: 220px; }
  .code-grid { grid-template-columns: 1fr; }
  #metrics-row { grid-template-columns: 1fr; }
  .inv-comparison-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div id="app">
  <aside id="sidebar">
    <div id="sidebar-header">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
        <h2 style="margin-bottom:0">Samples</h2>
        <button id="theme-toggle" title="Toggle light/dark theme" style="background:var(--bg-elevated);border:1px solid var(--border);border-radius:6px;padding:4px 8px;cursor:pointer;color:var(--text);font-size:14px;">&#9789;</button>
      </div>
      <input id="search-input" type="text" placeholder="Search by method ID... ( / )">
      <div id="filters">
        <button class="filter-chip" data-filter="em">EM Only</button>
        <button class="filter-chip" data-filter="compilable">Compilable</button>
        <button class="filter-chip" data-filter="errors">Has Errors</button>
        <button class="filter-chip" data-filter="tested">Tested</button>
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
      <button class="tab-btn" data-tab="retrieval">Retrieval <span class="tab-hint">4</span></button>
      <button class="tab-btn" data-tab="meta">Meta <span class="tab-hint">5</span></button>
      <button class="tab-btn" data-tab="invocations">Invocations <span class="tab-hint">6</span></button>
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
  filters: { em: false, compilable: false, errors: false, tested: false },
  diffPair: null,
  diffFormat: 'side-by-side',
  diffIdentOnly: false,
};

// --- Helpers ---
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function fullMethod(sample, body) {
  const sig = sample?.method_signature || '';
  const b = body ?? '';
  return sig + ' {\n' + b + '\n}';
}

function stripToIdentifiers(code) {
  // Remove comments
  let s = code.replace(/\/\/[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, '');
  // Collapse whitespace per line, drop empty
  s = s.split('\n').map(l => l.split(/\s+/).join(' ').trim()).filter(l => l).join('\n');
  return s;
}

// Palette for dynamically-named rag_* modes
const RAG_PALETTE = ['#89b4fa','#74c7ec','#94e2d5','#f5c2e7','#cba6f7','#f9e2af','#eba0ac'];
let _ragColorIdx = 0;

function getModeColor(mode) {
  if (MODE_COLORS[mode]) return MODE_COLORS[mode];
  if (mode.startsWith('rag_') || mode === 'retrieval_augmentation') {
    // Assign a stable color on first access
    const color = RAG_PALETTE[_ragColorIdx % RAG_PALETTE.length];
    _ragColorIdx++;
    MODE_COLORS[mode] = { tag: 'mode-tag-rag', color };
    return MODE_COLORS[mode];
  }
  return { tag: 'mode-tag-none', color: '#9399b2' };
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
    if (state.filters.tested) {
      const data = getSampleData(key);
      const hasTested = MODES.some(m => data[m]?.test_eval || data[m]?.test_file_paths?.length > 0);
      if (!hasTested) return false;
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
  html += codeBlock('Ground Truth', 'mode-tag-none', fullMethod(any, any.ground_truth));
  html += '</div>';

  html += '<div class="code-section"><div class="code-section-title">Generated Code</div>';
  html += '<div class="code-grid">';
  for (const mode of MODES) {
    const s = data[mode];
    const mc = getModeColor(mode);
    html += codeBlock(getModeLabel(mode), mc.tag, s ? fullMethod(s, s.generated) : undefined);
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
        a: { name: 'Ground Truth', code: fullMethod(any, any.ground_truth || '') },
        b: { name: `Generated (${getModeLabel(mode)})`, code: fullMethod(data[mode], data[mode].generated || '') },
      });
    }
  }

  for (let i = 0; i < MODES.length; i++) {
    for (let j = i + 1; j < MODES.length; j++) {
      if (data[MODES[i]] && data[MODES[j]]) {
        pairs.push({
          label: `${getModeLabel(MODES[i])} vs ${getModeLabel(MODES[j])}`,
          a: { name: getModeLabel(MODES[i]), code: fullMethod(data[MODES[i]], data[MODES[i]].generated || '') },
          b: { name: getModeLabel(MODES[j]), code: fullMethod(data[MODES[j]], data[MODES[j]].generated || '') },
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
    <label style="margin-left:16px;font-size:12px;cursor:pointer;user-select:none;color:var(--text-dim)">
      <input type="checkbox" id="diff-ident-only" ${state.diffIdentOnly ? 'checked' : ''}
        style="margin-right:4px;vertical-align:middle">
      Identifiers only (strip comments &amp; whitespace)
    </label>
  </div>`;
  html += '<div id="diff-output"></div>';
  return html;
}

function computeDiff() {
  const pairs = buildDiffPairs();
  if (!pairs.length) return;
  const pair = pairs[state.diffPair ?? 0];
  if (!pair) return;

  let aCode = pair.a.code, bCode = pair.b.code;
  if (state.diffIdentOnly) {
    aCode = stripToIdentifiers(aCode);
    bCode = stripToIdentifiers(bCode);
  }

  const diffStr = Diff.createTwoFilesPatch(
    pair.a.name, pair.b.name,
    aCode, bCode,
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

  // Test Coverage
  if (any?.coverage_ratio != null || any?.line_coverage || any?.test_file_paths) {
    html += '<div class="code-section-title" style="margin-top:24px;">Test Coverage</div>';

    // Coverage ratio
    if (any.coverage_ratio != null) {
      const pct = (any.coverage_ratio * 100).toFixed(1);
      const color = any.coverage_ratio >= 0.8 ? '#a6e3a1' : any.coverage_ratio >= 0.5 ? '#f9e2af' : '#f38ba8';
      html += `<div class="cov-summary">Line coverage: <strong style="color:${color}">${pct}%</strong></div>`;
    }

    // Per-line coverage visualization
    if (any.line_coverage && any.line_coverage.length > 0) {
      const covered = any.line_coverage.filter(l => l.covered).length;
      const total = any.line_coverage.length;
      html += `<div class="cov-summary">${covered} / ${total} executable lines covered</div>`;
      html += '<div class="cov-container">';
      for (const lc of any.line_coverage) {
        const cls = lc.covered ? 'cov-covered' : 'cov-missed';
        html += `<div class="cov-line ${cls}"><span class="cov-line-nr">${lc.line}</span><span class="cov-line-src">${esc(lc.source)}</span></div>`;
      }
      html += '</div>';
    }

    // Test file paths
    if (any.test_file_paths && any.test_file_paths.length > 0) {
      html += '<div class="cov-test-files"><div class="code-section-title" style="margin-top:12px;">Test Files Referencing This Method</div>';
      html += '<ul>';
      for (const p of any.test_file_paths) {
        html += `<li><code>${esc(p)}</code></li>`;
      }
      html += '</ul></div>';
    }
  }

  return html;
}

// --- Tab: Retrieval ---
function renderRetrievalTab() {
  const key = SAMPLE_KEYS[state.currentIdx];
  const data = getSampleData(key);

  // Find all retrieval modes (rag_* or retrieval_augmentation)
  const retModes = MODES.filter(m => m.startsWith('rag_') || m === 'retrieval_augmentation');
  if (!retModes.length) return '<div class="not-available">No retrieval modes in this experiment.</div>';

  let html = '';

  // Augmentation blocks side-by-side
  const modesWithAug = retModes.filter(m => data[m]?.augmentation_block || data[m]?.retrieval_results);
  if (modesWithAug.length > 0) {
    html += '<div class="code-section-title">Augmentation Blocks (injected into prompt)</div>';
    html += '<div class="code-grid">';
    for (const mode of modesWithAug) {
      const s = data[mode];
      const mc = getModeColor(mode);
      const aug = s?.augmentation_block || '(no augmentation block)';
      html += codeBlock(getModeLabel(mode), mc.tag, aug);
    }
    html += '</div>';
  }

  // Retrieval results per mode
  for (const mode of retModes) {
    const s = data[mode];
    if (!s?.retrieval_results?.length) continue;
    const mc = getModeColor(mode);

    html += `<div class="code-section-title" style="margin-top:24px">
      Retrieved Methods <span class="mode-tag ${mc.tag}">${getModeLabel(mode)}</span>
      <span style="font-size:11px;color:var(--text-dim);margin-left:8px">${s.retrieval_results.length} results</span>
    </div>`;

    // Retrieval metrics
    const retMetrics = ['recall_at_k', 'api_coverage_at_k', 'mrr', 'retrieval_precision_at_k', 'retrieval_ndcg_at_k', 'owner_type_recall'];
    const hasRetMetrics = retMetrics.some(rm => s.metrics?.[rm] !== undefined && s.metrics?.[rm] !== null);
    if (hasRetMetrics) {
      html += '<div style="display:flex;gap:12px;flex-wrap:wrap;margin:8px 0 16px">';
      for (const rm of retMetrics) {
        const v = s.metrics?.[rm];
        if (v === undefined || v === null) continue;
        html += `<div style="background:var(--bg-elevated);padding:6px 12px;border-radius:6px;font-size:12px">
          <span style="color:var(--text-dim)">${rm}:</span> <strong>${Number(v).toFixed(4)}</strong></div>`;
      }
      html += '</div>';
    }

    // Query debug
    if (s.retrieval_query) {
      html += `<details style="margin-bottom:12px">
        <summary style="font-size:12px;color:var(--text-dim);cursor:pointer">Search Query</summary>
        <div style="background:var(--bg-elevated);padding:8px 12px;border-radius:6px;margin-top:4px;font-size:11px;font-family:monospace;white-space:pre-wrap;color:var(--text-dim)">${esc(s.retrieval_query)}</div>
      </details>`;
    }

    // Results table
    html += '<table class="meta-table"><thead><tr><th>#</th><th>Score</th><th>Method</th><th>File</th></tr></thead><tbody>';
    for (let rank = 0; rank < s.retrieval_results.length; rank++) {
      const r = s.retrieval_results[rank];
      const sig = r.signature || r.id || '(unknown)';
      const fp = r.filePath || r.file_path || '';
      const score = typeof r.score === 'number' ? r.score.toFixed(4) : (r.score ?? '');
      html += `<tr><td>${rank + 1}</td><td>${score}</td><td><code>${esc(sig)}</code></td><td style="font-size:11px;color:var(--text-dim)">${esc(fp)}</td></tr>`;
    }
    html += '</tbody></table>';

    // Expandable method bodies
    html += '<details style="margin-top:8px"><summary style="font-size:12px;color:var(--text-dim);cursor:pointer">Show retrieved method bodies</summary>';
    for (let rank = 0; rank < s.retrieval_results.length; rank++) {
      const r = s.retrieval_results[rank];
      const sig = r.signature || r.id || '(unknown)';
      const body = r.content || r.methodBody || r.method_body || '';
      const classFqn = r.classFqn || r.class_fqn || '';
      const label = classFqn ? `${classFqn} :: ${sig}` : sig;
      html += `<div style="margin-top:12px">
        <div style="font-size:12px;color:var(--accent);margin-bottom:4px">#${rank + 1} ${esc(label)}</div>`;
      if (body) {
        const fullCode = sig + ' {\n' + body + '\n}';
        html += `<div class="code-block"><pre><code class="language-java">${esc(fullCode)}</code></pre></div>`;
      } else {
        html += '<div style="font-size:11px;color:var(--text-dim)">(no body available)</div>';
      }
      html += '</div>';
    }
    html += '</details>';
  }

  // Also show oracle augmentation blocks for comparison
  const oracleModes = MODES.filter(m => m === 'ordered_augmentation' || m === 'shuffled_augmentation');
  const oracleWithAug = oracleModes.filter(m => data[m]?.augmentation_block);
  if (oracleWithAug.length > 0) {
    html += '<div class="code-section-title" style="margin-top:32px">Oracle Augmentation (for comparison)</div>';
    html += '<div class="code-grid">';
    for (const mode of oracleWithAug) {
      const s = data[mode];
      const mc = getModeColor(mode);
      html += codeBlock(getModeLabel(mode), mc.tag, s.augmentation_block);
    }
    html += '</div>';
  }

  return html || '<div class="not-available">No retrieval data for this sample.</div>';
}

// --- Invocation helpers ---
function parseSimpleName(signature) {
  // "com.example.Foo::bar(int) -> void" → "bar"
  // "UNRESOLVED<name>" → "name"
  // "obj.name" (tree-sitter) → "name"
  if (signature.startsWith('UNRESOLVED<')) {
    return signature.slice(11, -1);
  }
  const colons = signature.indexOf('::');
  if (colons >= 0) {
    const rest = signature.substring(colons + 2);
    const paren = rest.indexOf('(');
    return paren > 0 ? rest.substring(0, paren) : rest;
  }
  // tree-sitter format: "obj.method" or just "method"
  const dot = signature.lastIndexOf('.');
  return dot >= 0 ? signature.substring(dot + 1) : signature;
}

function invocationSignatures(invocations) {
  // Returns Set of full signatures for EXACT invocations
  const sigs = new Set();
  for (const inv of (invocations || [])) {
    if (inv.resolution_mode === 'EXACT' || inv.resolutionMode === 'EXACT') {
      sigs.add(inv.signature);
    }
  }
  return sigs;
}

function invocationSimpleNames(invocations) {
  const names = new Set();
  for (const inv of (invocations || [])) {
    names.add(parseSimpleName(inv.signature || ''));
  }
  return names;
}

function setIntersection(a, b) { return new Set([...a].filter(x => b.has(x))); }
function setDifference(a, b) { return new Set([...a].filter(x => !b.has(x))); }
function setUnion(a, b) { return new Set([...a, ...b]); }

function resolutionBadge(mode) {
  if (mode === 'EXACT') return '<span class="badge badge-yes">EXACT</span>';
  if (mode === 'TREESITTER') return '<span class="badge badge-na">TREESITTER</span>';
  return '<span class="badge badge-no">UNRESOLVED</span>';
}

// --- Tab: Invocations ---
function renderInvocationsTab() {
  const key = SAMPLE_KEYS[state.currentIdx];
  const data = getSampleData(key);
  const any = getAnySample(key);
  if (!any) return '<div class="not-available">No data</div>';

  const gtInvocations = any.invocations_ordered || [];

  let html = '';

  // --- GT invocations table ---
  html += '<div class="code-section-title">Ground Truth Invocations (extracted via JDT AST)</div>';
  if (gtInvocations.length === 0) {
    html += '<div class="not-available">No invocations in ground truth</div>';
  } else {
    html += '<table class="meta-table"><thead><tr><th>#</th><th>Signature</th><th>Simple Name</th><th>Resolution</th></tr></thead><tbody>';
    for (const inv of gtInvocations) {
      const sn = parseSimpleName(inv.signature);
      html += `<tr><td>${inv.order_index ?? ''}</td><td><code>${esc(inv.signature)}</code></td><td><code>${esc(sn)}</code></td><td>${resolutionBadge(inv.resolution_mode)}</td></tr>`;
    }
    html += '</tbody></table>';
  }

  // --- Per-mode comparison ---
  html += '<div class="code-section-title" style="margin-top:24px;">Invocation Comparison (per mode)</div>';
  html += '<div class="inv-comparison-grid">';

  for (const mode of MODES) {
    const s = data[mode];
    if (!s) continue;
    const mc = getModeColor(mode);

    const genInvocations = s.generated_invocations || [];
    const hasPrecomputed = s.generated_invocations != null;

    if (!hasPrecomputed) {
      html += `<div class="inv-mode-card">`;
      html += `<div class="inv-mode-header"><span class="mode-tag ${mc.tag}">${getModeLabel(mode)}</span></div>`;
      html += '<div class="not-available">No pre-computed invocations. Run <code>python -m pipeline.enrich_samples</code> with <code>--extractor-jar</code> to extract.</div>';
      html += '</div>';
      continue;
    }

    // Try full-signature comparison first (EXACT vs EXACT)
    const gtExact = invocationSignatures(gtInvocations);
    const genExact = invocationSignatures(genInvocations);
    const exactInter = setIntersection(gtExact, genExact);
    const exactGtOnly = setDifference(gtExact, genExact);
    const exactGenOnly = setDifference(genExact, gtExact);
    const exactUnion = setUnion(gtExact, genExact);
    const exactIou = exactUnion.size > 0 ? (exactInter.size / exactUnion.size) : (gtExact.size === 0 && genExact.size === 0 ? 1.0 : 0.0);
    const exactRecall = gtExact.size > 0 ? (exactInter.size / gtExact.size) : 1.0;

    // Also simple-name comparison (includes UNRESOLVED and TREESITTER)
    const gtNames = invocationSimpleNames(gtInvocations);
    const genNames = invocationSimpleNames(genInvocations);
    const nameInter = setIntersection(gtNames, genNames);
    const nameGtOnly = setDifference(gtNames, genNames);
    const nameGenOnly = setDifference(genNames, gtNames);
    const nameUnion = setUnion(gtNames, genNames);
    const nameIou = nameUnion.size > 0 ? (nameInter.size / nameUnion.size) : (gtNames.size === 0 && genNames.size === 0 ? 1.0 : 0.0);
    const nameRecall = gtNames.size > 0 ? (nameInter.size / gtNames.size) : 1.0;

    // Determine which comparison to show prominently
    const hasExact = genInvocations.some(i => i.resolutionMode === 'EXACT');
    const primaryIou = hasExact ? exactIou : nameIou;
    const primaryRecall = hasExact ? exactRecall : nameRecall;

    html += `<div class="inv-mode-card">`;
    html += `<div class="inv-mode-header"><span class="mode-tag ${mc.tag}">${getModeLabel(mode)}</span></div>`;

    // Summary metrics
    html += '<div class="inv-summary">';
    if (hasExact) {
      html += `<div class="inv-metric"><label>Sig IoU</label><span class="inv-metric-val">${(exactIou * 100).toFixed(1)}%</span></div>`;
      html += `<div class="inv-metric"><label>Sig Recall</label><span class="inv-metric-val">${(exactRecall * 100).toFixed(1)}%</span></div>`;
    }
    html += `<div class="inv-metric"><label>Name IoU</label><span class="inv-metric-val">${(nameIou * 100).toFixed(1)}%</span></div>`;
    html += `<div class="inv-metric"><label>Name Recall</label><span class="inv-metric-val">${(nameRecall * 100).toFixed(1)}%</span></div>`;
    html += `<div class="inv-metric"><label>|GT|</label><span class="inv-metric-val">${gtInvocations.length}</span></div>`;
    html += `<div class="inv-metric"><label>|Gen|</label><span class="inv-metric-val">${genInvocations.length}</span></div>`;
    html += '</div>';

    // Three columns — use simple name comparison for the visual
    html += '<div class="inv-columns">';

    html += '<div class="inv-col inv-col-gt-only"><div class="inv-col-title">GT Only <span class="inv-count">' + nameGtOnly.size + '</span></div>';
    for (const n of [...nameGtOnly].sort()) html += `<div class="inv-item"><code>${esc(n)}</code></div>`;
    if (nameGtOnly.size === 0) html += '<div class="inv-empty">none</div>';
    html += '</div>';

    html += '<div class="inv-col inv-col-both"><div class="inv-col-title">Both <span class="inv-count">' + nameInter.size + '</span></div>';
    for (const n of [...nameInter].sort()) html += `<div class="inv-item"><code>${esc(n)}</code></div>`;
    if (nameInter.size === 0) html += '<div class="inv-empty">none</div>';
    html += '</div>';

    html += '<div class="inv-col inv-col-gen-only"><div class="inv-col-title">Gen Only <span class="inv-count">' + nameGenOnly.size + '</span></div>';
    for (const n of [...nameGenOnly].sort()) html += `<div class="inv-item"><code>${esc(n)}</code></div>`;
    if (nameGenOnly.size === 0) html += '<div class="inv-empty">none</div>';
    html += '</div>';

    html += '</div>'; // inv-columns

    // Generated invocations table for manual verification
    html += `<details class="inv-details"><summary>Generated Invocations (${genInvocations.length})</summary>`;
    html += '<table class="meta-table"><thead><tr><th>#</th><th>Signature</th><th>Resolution</th><th>In GT?</th></tr></thead><tbody>';
    for (const inv of genInvocations) {
      const sn = parseSimpleName(inv.signature || '');
      const inGt = gtNames.has(sn);
      const fullMatch = gtExact.has(inv.signature);
      const matchIcon = fullMatch ? '&#10004;&#10004;' : inGt ? '&#10004;' : '&#10008;';
      const matchColor = fullMatch ? '#a6e3a1' : inGt ? '#f9e2af' : '#f38ba8';
      html += `<tr><td>${inv.orderIndex ?? ''}</td><td><code>${esc(inv.signature || '')}</code></td><td>${resolutionBadge(inv.resolutionMode)}</td><td style="color:${matchColor}">${matchIcon}</td></tr>`;
    }
    html += '</tbody></table></details>';

    html += '</div>'; // inv-mode-card
  }

  html += '</div>'; // inv-comparison-grid

  return html;
}

// --- Main Render ---
function renderTabContent() {
  const c = document.getElementById('tab-content');
  switch (state.activeTab) {
    case 'code': c.innerHTML = renderCodeTab(); break;
    case 'diff': c.innerHTML = renderDiffTab(); break;
    case 'prompt': c.innerHTML = renderPromptTab(); break;
    case 'retrieval': c.innerHTML = renderRetrievalTab(); break;
    case 'meta': c.innerHTML = renderMetaTab(); break;
    case 'invocations': c.innerHTML = renderInvocationsTab(); break;
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
  const identCb = document.getElementById('diff-ident-only');
  if (identCb) {
    identCb.addEventListener('change', () => {
      state.diffIdentOnly = identCb.checked;
      computeDiff();
    });
  }
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
  } else if (e.key >= '1' && e.key <= '6') {
    const tabs = ['code', 'diff', 'prompt', 'retrieval', 'meta', 'invocations'];
    const tab = tabs[parseInt(e.key) - 1];
    if (tab) {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelector(`.tab-btn[data-tab="${tab}"]`).classList.add('active');
      state.activeTab = tab;
      renderTabContent();
    }
  }
});

// --- Theme Toggle ---
function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  const hljsLink = document.getElementById('hljs-theme');
  if (theme === 'light') {
    hljsLink.href = 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-light.min.css';
    document.getElementById('theme-toggle').innerHTML = '&#9728;';
  } else {
    hljsLink.href = 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css';
    document.getElementById('theme-toggle').innerHTML = '&#9789;';
  }
  localStorage.setItem('viewer-theme', theme);
  // Re-highlight visible code blocks
  document.querySelectorAll('#tab-content pre code.language-java').forEach(el => {
    el.removeAttribute('data-highlighted');
    if (typeof hljs !== 'undefined') hljs.highlightElement(el);
  });
}

document.getElementById('theme-toggle').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  setTheme(current === 'dark' ? 'light' : 'dark');
});

// Restore saved theme
const savedTheme = localStorage.getItem('viewer-theme') || 'dark';
if (savedTheme !== 'dark') setTheme(savedTheme);

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
