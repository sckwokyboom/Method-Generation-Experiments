from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pipeline.models import SampleResult
from pipeline.report import aggregate_metrics, load_sample_result
from pipeline.viewer import discover_modes


def load_retrieval_samples(
    results_dir: Path,
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
            grouped.setdefault(idx, {})[mode] = d

    return grouped, mode_names


def generate_retrieval_viewer(results_dir: Path, output_path: Path) -> None:
    grouped, mode_names = load_retrieval_samples(results_dir)

    # Load aggregate data
    aggregates = {}
    for mode in mode_names:
        samples = []
        mode_dir = results_dir / mode / "samples"
        if mode_dir.is_dir():
            for path in sorted(mode_dir.glob("sample_*.json")):
                samples.append(load_sample_result(path))
        aggregates[mode] = aggregate_metrics(samples)

    data = {
        "samples": grouped,
        "modes": mode_names,
        "aggregates": aggregates,
        "generated": datetime.now(timezone.utc).isoformat(),
    }

    data_json = json.dumps(data, ensure_ascii=False, default=str)
    data_json = data_json.replace("</", "<\\/")
    html = _TEMPLATE.replace('"__DATA_JSON__"', data_json)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"Retrieval viewer written to {output_path}")


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Retrieval Experiment Viewer</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/java.min.js"></script>
<style>
:root {
    --bg: #1e1e2e; --surface: #313244; --text: #cdd6f4;
    --subtext: #a6adc8; --accent: #f5c2e7; --green: #a6e3a1;
    --red: #f38ba8; --yellow: #f9e2af; --blue: #89b4fa;
    --border: #45475a; --sidebar-w: 280px;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: 'SF Mono','Cascadia Code',monospace; font-size:13px; background:var(--bg); color:var(--text); display:flex; height:100vh; overflow:hidden; }
#sidebar { width:var(--sidebar-w); background:var(--surface); border-right:1px solid var(--border); display:flex; flex-direction:column; flex-shrink:0; }
#sidebar-header { padding:12px; border-bottom:1px solid var(--border); }
#sidebar-header h2 { font-size:14px; color:var(--accent); margin-bottom:8px; }
#search { width:100%; padding:6px 8px; background:var(--bg); color:var(--text); border:1px solid var(--border); border-radius:4px; font-family:inherit; font-size:12px; }
#sample-list { flex:1; overflow-y:auto; }
.sample-item { padding:8px 12px; cursor:pointer; border-bottom:1px solid var(--border); font-size:11px; }
.sample-item:hover { background:var(--bg); }
.sample-item.active { background:var(--blue); color:#000; }
.sample-item .method-name { font-weight:bold; }
#main { flex:1; display:flex; flex-direction:column; overflow:hidden; }
#tabs { display:flex; border-bottom:1px solid var(--border); background:var(--surface); }
.tab { padding:10px 16px; cursor:pointer; color:var(--subtext); border-bottom:2px solid transparent; font-size:12px; }
.tab.active { color:var(--accent); border-bottom-color:var(--accent); }
#content { flex:1; overflow-y:auto; padding:16px; }
.metrics-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:12px; margin-bottom:20px; }
.metric-card { background:var(--surface); border:1px solid var(--border); border-radius:6px; padding:12px; }
.metric-card .label { font-size:11px; color:var(--subtext); text-transform:uppercase; }
.metric-card .value { font-size:20px; font-weight:bold; color:var(--accent); margin-top:4px; }
.metric-card .detail { font-size:10px; color:var(--subtext); margin-top:2px; }
table { width:100%; border-collapse:collapse; margin-bottom:16px; }
th, td { padding:8px 12px; text-align:left; border:1px solid var(--border); font-size:12px; }
th { background:var(--surface); color:var(--accent); }
pre { background:var(--bg); border:1px solid var(--border); border-radius:4px; padding:12px; overflow-x:auto; margin:8px 0; }
code { font-family:inherit; }
.retrieval-result { background:var(--surface); border:1px solid var(--border); border-radius:6px; padding:12px; margin-bottom:12px; }
.retrieval-result .rank-badge { display:inline-block; background:var(--blue); color:#000; padding:2px 8px; border-radius:10px; font-weight:bold; font-size:11px; margin-right:8px; }
.retrieval-result .score { color:var(--yellow); font-weight:bold; }
.retrieval-result .sig { color:var(--green); }
.section-title { font-size:14px; font-weight:bold; color:var(--accent); margin:16px 0 8px; }
.mode-badge { display:inline-block; padding:2px 6px; border-radius:3px; font-size:10px; font-weight:bold; margin-left:4px; }
.mode-badge.no_augmentation { background:#585b70; color:#cdd6f4; }
.mode-badge.retrieval_augmentation { background:#89b4fa; color:#1e1e2e; }
.mode-badge.ordered_augmentation { background:#a6e3a1; color:#1e1e2e; }
.mode-badge.shuffled_augmentation { background:#f9e2af; color:#1e1e2e; }
.compare-row { display:flex; gap:16px; margin-bottom:16px; }
.compare-col { flex:1; min-width:0; }
.compare-col h4 { margin-bottom:8px; color:var(--subtext); font-size:12px; }
.query-debug { background:var(--bg); border:1px solid var(--border); border-radius:4px; padding:12px; font-size:11px; word-break:break-all; color:var(--subtext); margin:8px 0; max-height:200px; overflow-y:auto; }
</style>
</head>
<body>
<div id="sidebar">
    <div id="sidebar-header">
        <h2>Retrieval Viewer</h2>
        <input id="search" type="text" placeholder="Search methods...">
    </div>
    <div id="sample-list"></div>
</div>
<div id="main">
    <div id="tabs">
        <div class="tab active" data-tab="overview">Overview</div>
        <div class="tab" data-tab="sample">Sample</div>
        <div class="tab" data-tab="retrieval">Retrieval</div>
        <div class="tab" data-tab="code">Code</div>
    </div>
    <div id="content"></div>
</div>
<script>
const DATA = "__DATA_JSON__";
const samples = DATA.samples;
const modes = DATA.modes;
const aggregates = DATA.aggregates;
const sampleKeys = Object.keys(samples).sort((a,b) => parseInt(a)-parseInt(b));
let currentIdx = 0;
let currentTab = 'overview';
let filterText = '';

function el(id) { return document.getElementById(id); }

function renderSidebar() {
    const list = el('sample-list');
    list.innerHTML = '';
    sampleKeys.forEach((key, i) => {
        const s = samples[key];
        const firstMode = Object.keys(s)[0];
        const d = s[firstMode];
        const name = d.method_id || `Sample ${key}`;
        if (filterText && !name.toLowerCase().includes(filterText)) return;
        const item = document.createElement('div');
        item.className = 'sample-item' + (i === currentIdx ? ' active' : '');
        const modesHtml = Object.keys(s).map(m =>
            `<span class="mode-badge ${m}">${m.replace('_augmentation','').replace('no_','none')}</span>`
        ).join('');
        item.innerHTML = `<div class="method-name">${name.split('#').pop()}</div>
            <div style="color:var(--subtext);font-size:10px;margin-top:2px">${name.split('#')[0]}</div>
            <div style="margin-top:4px">${modesHtml}</div>`;
        item.onclick = () => { currentIdx = i; render(); };
        list.appendChild(item);
    });
}

function renderOverview() {
    let html = '<div class="section-title">Aggregate Metrics Comparison</div><table><tr><th>Metric</th>';
    modes.forEach(m => html += `<th>${m}</th>`);
    html += '</tr>';
    const metricNames = ['em','es','iou','lcs_ratio','lcs_no_ident_ratio','codebleu','compilable','recall_at_k','api_coverage_at_k','mrr'];
    metricNames.forEach(name => {
        html += `<tr><td><b>${name}</b></td>`;
        modes.forEach(m => {
            const a = aggregates[m] && aggregates[m][name];
            if (!a) { html += '<td>N/A</td>'; return; }
            html += `<td>${a.mean.toFixed(4)} <span style="color:var(--subtext)">(std=${a.std.toFixed(4)})</span></td>`;
        });
        html += '</tr>';
    });
    html += '</table>';

    // Sample count
    modes.forEach(m => {
        const a = aggregates[m];
        if (a) html += `<div style="color:var(--subtext);font-size:11px">${m}: ${a.sample_count} samples</div>`;
    });

    return html;
}

function getSample() {
    const key = sampleKeys[currentIdx];
    return key ? samples[key] : null;
}

function renderSampleTab() {
    const s = getSample();
    if (!s) return '<div>No sample selected</div>';
    let html = '';

    // Show metrics comparison across modes
    html += '<div class="section-title">Metrics Comparison</div>';
    html += '<div class="metrics-grid">';
    const metricNames = ['em','es','iou','lcs_ratio','codebleu','recall_at_k','api_coverage_at_k','mrr'];
    metricNames.forEach(name => {
        html += '<div class="metric-card"><div class="label">' + name + '</div>';
        Object.keys(s).forEach(mode => {
            const v = s[mode].metrics[name];
            const formatted = v === null || v === undefined ? 'N/A' : (typeof v === 'boolean' ? (v?'1':'0') : Number(v).toFixed(4));
            html += `<div class="detail"><span class="mode-badge ${mode}">${mode.replace('_augmentation','').replace('no_','none')}</span> ${formatted}</div>`;
        });
        html += '</div>';
    });
    html += '</div>';

    // Method info
    const first = s[Object.keys(s)[0]];
    html += `<div class="section-title">Method</div>`;
    html += `<div><b>${first.method_id}</b></div>`;
    html += `<div style="color:var(--subtext)">${first.file_path}</div>`;
    html += `<pre><code class="language-java">${escapeHtml(first.method_signature)}</code></pre>`;

    // Invocations
    if (first.invocations_ordered && first.invocations_ordered.length > 0) {
        html += '<div class="section-title">Oracle Invocations</div><table><tr><th>#</th><th>Signature</th><th>Mode</th></tr>';
        first.invocations_ordered.forEach(inv => {
            html += `<tr><td>${inv.order_index}</td><td style="font-size:11px">${escapeHtml(inv.signature)}</td><td>${inv.resolution_mode}</td></tr>`;
        });
        html += '</table>';
    }

    return html;
}

function renderRetrievalTab() {
    const s = getSample();
    if (!s) return '<div>No sample selected</div>';

    // Find the retrieval_augmentation mode data
    const retData = s['retrieval_augmentation'];
    if (!retData || !retData.retrieval_results) {
        return '<div style="color:var(--subtext)">No retrieval data for this sample. Only available in retrieval_augmentation mode.</div>';
    }

    let html = '';

    // Query debug
    if (retData.retrieval_query) {
        html += '<div class="section-title">Lucene Query</div>';
        html += `<div class="query-debug">${escapeHtml(retData.retrieval_query)}</div>`;
    }

    // Retrieval metrics
    html += '<div class="section-title">Retrieval Metrics</div>';
    html += '<div class="metrics-grid">';
    ['recall_at_k','api_coverage_at_k','mrr'].forEach(name => {
        const v = retData.metrics[name];
        const formatted = v === null || v === undefined ? 'N/A' : Number(v).toFixed(4);
        html += `<div class="metric-card"><div class="label">${name}</div><div class="value">${formatted}</div></div>`;
    });
    html += '</div>';

    // Retrieved results
    html += '<div class="section-title">Retrieved Methods</div>';
    retData.retrieval_results.forEach(r => {
        html += `<div class="retrieval-result">`;
        html += `<span class="rank-badge">#${r.rank}</span>`;
        html += `<span class="score">score=${Number(r.score).toFixed(2)}</span> `;
        html += `<span class="sig">${escapeHtml(r.signature || '')}</span>`;
        html += `<div style="color:var(--subtext);font-size:11px;margin-top:4px">${escapeHtml(r.classFqn || '')} | ${escapeHtml(r.filePath || '')}</div>`;
        if (r.invocationProfile) {
            const lines = r.invocationProfile.split('\\n').filter(l => l.trim() && !l.startsWith('bigram:') && !l.startsWith('trigram:')).slice(0,5);
            if (lines.length > 0) {
                html += `<div style="margin-top:6px;font-size:11px;color:var(--subtext)"><b>Invocations:</b> ${escapeHtml(lines.join(', '))}</div>`;
            }
        }
        if (r.methodBody) {
            const bodyLines = r.methodBody.split('\\n').slice(0,8);
            html += `<pre style="margin-top:6px;font-size:11px"><code class="language-java">${escapeHtml(bodyLines.join('\\n'))}</code></pre>`;
        }
        html += '</div>';
    });

    // Augmentation block
    if (retData.augmentation_block) {
        html += '<div class="section-title">Augmentation Block (as injected)</div>';
        html += `<pre><code class="language-java">${escapeHtml(retData.augmentation_block)}</code></pre>`;
    }

    return html;
}

function renderCodeTab() {
    const s = getSample();
    if (!s) return '<div>No sample selected</div>';
    let html = '';

    // Ground truth
    const first = s[Object.keys(s)[0]];
    html += '<div class="section-title">Ground Truth</div>';
    html += `<pre><code class="language-java">${escapeHtml(first.ground_truth || '')}</code></pre>`;

    // Generated code per mode
    html += '<div class="section-title">Generated Code</div>';
    html += '<div class="compare-row">';
    Object.keys(s).forEach(mode => {
        html += `<div class="compare-col"><h4><span class="mode-badge ${mode}">${mode}</span></h4>`;
        html += `<pre><code class="language-java">${escapeHtml(s[mode].generated || '')}</code></pre></div>`;
    });
    html += '</div>';

    return html;
}

function render() {
    renderSidebar();
    let html = '';
    switch(currentTab) {
        case 'overview': html = renderOverview(); break;
        case 'sample': html = renderSampleTab(); break;
        case 'retrieval': html = renderRetrievalTab(); break;
        case 'code': html = renderCodeTab(); break;
    }
    el('content').innerHTML = html;
    document.querySelectorAll('pre code').forEach(block => hljs.highlightElement(block));
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Event listeners
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        currentTab = tab.dataset.tab;
        render();
    });
});

el('search').addEventListener('input', e => {
    filterText = e.target.value.toLowerCase();
    renderSidebar();
});

document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT') return;
    if (e.key === 'ArrowDown' && currentIdx < sampleKeys.length - 1) { currentIdx++; render(); }
    if (e.key === 'ArrowUp' && currentIdx > 0) { currentIdx--; render(); }
    if (e.key >= '1' && e.key <= '4') {
        const tabs = ['overview','sample','retrieval','code'];
        const idx = parseInt(e.key) - 1;
        if (idx < tabs.length) { currentTab = tabs[idx]; document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active', i===idx)); render(); }
    }
});

render();
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate retrieval experiment HTML viewer")
    parser.add_argument("--results-dir", default="./results", help="Path to results directory")
    parser.add_argument("--output", default=None, help="Output HTML file path")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    output_path = Path(args.output) if args.output else results_dir / "retrieval_viewer.html"

    generate_retrieval_viewer(results_dir, output_path)


if __name__ == "__main__":
    main()
