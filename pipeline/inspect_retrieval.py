"""
Visual inspection of retrieval results and FIM prompts before running LLM.

Generates a self-contained HTML file with:
- Full FIM prompts with syntax highlighting and FIM token markers
- Lucene query visualization
- Retrieved methods with all fields (method card, invocation profile, body, score)
- Augmentation block as inserted into the prompt
- Search request details (what signals went into the query)

Usage:
    python -m pipeline.inspect_retrieval --config config.yaml [--n 10] [--output inspection.html]
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import re

from pipeline.config import Config
from pipeline.dataset import build_dataset
from pipeline.models import ExtractedMethod, RetrievalResult
from pipeline.prompt import build_fim_prompt
from pipeline.retrieval import (
    build_index,
    build_search_request,
    filter_leakage,
    format_retrieval_augmentation,
    search_batch,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Z][a-z]+|[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)")


def _extract_simple_types(text: str) -> set[str]:
    """Extract uppercase-starting tokens as type-like names from any text."""
    if not text:
        return set()
    types = set()
    for token in re.split(r"[^a-zA-Z0-9]+", text):
        if len(token) >= 3 and token[0].isupper():
            types.add(token.lower())
    return types


def _extract_invocation_owners(invocation_profile: str) -> set[str]:
    """Extract owner class simple names from invocation profile text."""
    if not invocation_profile:
        return set()
    owners = set()
    for line in invocation_profile.split("\n"):
        line = line.strip()
        if not line or line.startswith("bigram:") or line.startswith("trigram:"):
            continue
        # "com.foo.Bar::method(args) -> ret" → "Bar"
        dcolon = line.find("::")
        if dcolon > 0:
            fqn = line[:dcolon]
            simple = fqn.rsplit(".", 1)[-1]
            if len(simple) >= 3:
                owners.add(simple.lower())
    return owners


def _iou(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union) if union else 0.0


def compute_overlap_analysis(
    method: ExtractedMethod,
    result: RetrievalResult,
    query_request: dict,
) -> dict:
    """Compute interpretable overlap metrics between query and a single result."""

    # 1. Type overlap: query types vs result typeProfile
    query_types = set()
    for imp in (method.imports or []):
        query_types.add(imp.rsplit(".", 1)[-1].lower())
    for f in (method.class_fields or []):
        query_types.add(f.type_fqn.rsplit(".", 1)[-1].lower())
    for pt in (method.parameter_types or []):
        for tok in re.split(r"[<>,\s]+", pt):
            s = tok.rsplit(".", 1)[-1]
            if len(s) >= 3 and s[0].isupper():
                query_types.add(s.lower())
    if method.return_type:
        for tok in re.split(r"[<>,\s]+", method.return_type):
            s = tok.rsplit(".", 1)[-1]
            if len(s) >= 3 and s[0].isupper():
                query_types.add(s.lower())
    query_types -= {"string", "object", "list", "map", "set", "optional", "integer",
                    "boolean", "void", "exception", "override"}

    result_types = _extract_simple_types(result.type_profile)
    shared_types = sorted(query_types & result_types)
    type_iou = _iou(query_types, result_types)

    # 2. Invocation owner overlap: sibling owner types vs result invocation owners
    query_owners = set()
    for owner in query_request.get("siblingOwnerTypes", []):
        query_owners.add(owner.lower())

    result_owners = _extract_invocation_owners(result.invocation_profile)
    shared_owners = sorted(query_owners & result_owners)
    owner_iou = _iou(query_owners, result_owners)

    # 3. Oracle invocation coverage: which oracle invocations appear in the result
    oracle_found = []
    oracle_missed = []
    for inv in (method.invocations or []):
        if inv.resolution_mode != "EXACT":
            continue
        parts = inv.signature.split("::", 1)
        if len(parts) < 2:
            continue
        owner_simple = parts[0].rsplit(".", 1)[-1].lower()
        mname = parts[1].split("(")[0].lower()
        combined = result.invocation_profile or ""
        combined_lower = combined.lower()
        if owner_simple in combined_lower and mname in combined_lower:
            oracle_found.append(f"{parts[0].rsplit('.', 1)[-1]}::{parts[1].split('(')[0]}")
        else:
            oracle_missed.append(f"{parts[0].rsplit('.', 1)[-1]}::{parts[1].split('(')[0]}")

    # 4. Signature term overlap
    query_sig_terms = set(t.lower() for t in re.split(r"[^a-zA-Z0-9]+", method.method_signature or "") if len(t) >= 3)
    result_sig_terms = set(t.lower() for t in re.split(r"[^a-zA-Z0-9]+", result.signature or "") if len(t) >= 3)
    common_terms = {"public", "private", "protected", "static", "final", "void", "int", "long",
                    "double", "float", "boolean", "string", "new", "return"}
    query_sig_terms -= common_terms
    result_sig_terms -= common_terms
    shared_sig = sorted(query_sig_terms & result_sig_terms)

    return {
        "type_iou": round(type_iou, 3),
        "shared_types": shared_types,
        "query_types_count": len(query_types),
        "result_types_count": len(result_types),
        "owner_iou": round(owner_iou, 3),
        "shared_owners": shared_owners,
        "shared_sig_terms": shared_sig,
        "oracle_found": oracle_found,
        "oracle_missed": oracle_missed[:10],
        "oracle_recall_this_result": len(oracle_found) / max(len(oracle_found) + len(oracle_missed), 1),
    }


def main():
    parser = argparse.ArgumentParser(description="Visual inspection of retrieval + FIM prompts")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--n", type=int, default=None, help="Number of samples (default: all)")
    parser.add_argument("--output", default=None, help="Output HTML path")
    parser.add_argument("--skip-extraction", action="store_true")
    parser.add_argument("--retrieval-mode", default=None,
                        help="Retrieval mode to inspect (e.g. rag_lucene). Default: first available.")
    args = parser.parse_args()

    config = Config.load(args.config)

    # Resolve which retrieval config to use
    from pipeline.prompt import is_retrieval_mode
    from pipeline.run import _retrieval_config_for_mode
    ret_mode = args.retrieval_mode
    if ret_mode is None:
        # Auto-detect: first rag_* mode from config, or fallback to retrieval_augmentation
        candidates = [m for m in config.experiment.modes if is_retrieval_mode(m)]
        if candidates:
            ret_mode = candidates[0]
        elif config.retrieval is not None:
            ret_mode = "retrieval_augmentation"
        else:
            raise SystemExit("Config must have a 'retrieval' or 'retrievers' section")
    ret_cfg = _retrieval_config_for_mode(config, ret_mode)
    if ret_cfg is None:
        raise SystemExit(f"No retrieval config found for mode '{ret_mode}'")
    log.info("Inspecting retrieval mode: %s (type=%s)", ret_mode, ret_cfg.retriever_type)

    # Swap config.retrieval for retrieval.py calls
    original_retrieval = config.retrieval
    config.retrieval = ret_cfg

    methods, classpath = build_dataset(config)
    count = min(args.n, len(methods)) if args.n else len(methods)
    methods = methods[:count]

    log.info("Building index...")
    build_index(config)

    log.info("Building search requests for %d samples...", len(methods))
    requests = [build_search_request(m, config) for m in methods]

    log.info("Running batch search...")
    responses = search_batch(requests, config)

    config.retrieval = original_retrieval  # restore

    log.info("Building inspection data...")
    samples = []
    for i, (method, req, resp) in enumerate(zip(methods, requests, responses)):
        resp = filter_leakage(resp, method)
        aug = format_retrieval_augmentation(resp, ret_cfg)
        fim_ret = build_fim_prompt(method, ret_mode, config.experiment.shuffle_seed, aug)
        fim_no = build_fim_prompt(method, "no_augmentation", config.experiment.shuffle_seed)

        samples.append({
            "index": i,
            "method_id": f"{method.class_fqn}#{method.method_name}",
            "file_path": method.file_path,
            "class_fqn": method.class_fqn,
            "method_signature": method.method_signature,
            "return_type": method.return_type,
            "parameter_types": method.parameter_types,
            "ground_truth": fim_ret.ground_truth,
            "invocations": [
                {"signature": inv.signature, "resolution_mode": inv.resolution_mode, "order_index": inv.order_index}
                for inv in sorted(method.invocations, key=lambda x: x.order_index)
            ],
            "imports": method.imports,
            "class_fields": [{"name": f.name, "typeFqn": f.type_fqn} for f in method.class_fields],
            "supertypes": method.supertypes,
            "sibling_count": len(method.sibling_methods),
            "search_request": req["request"],
            "retrieval": {
                "results": [
                    {**r.to_dict(), "overlap": compute_overlap_analysis(method, r, req["request"])}
                    for r in resp.results
                ],
                "total_hits": resp.total_hits,
                "search_time_ms": resp.search_time_ms,
                "query_debug": resp.query_debug,
            },
            "augmentation_block": aug,
            "prompt_retrieval": fim_ret.full_prompt,
            "prompt_no_aug": fim_no.full_prompt,
            "prompt_retrieval_len": len(fim_ret.full_prompt),
            "prompt_no_aug_len": len(fim_no.full_prompt),
        })

    data_json = json.dumps(samples, ensure_ascii=False, default=str)
    html = _TEMPLATE.replace('"__DATA__"', data_json)

    output_path = Path(args.output) if args.output else Path(config.output.dir) / "retrieval_inspection.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    log.info("Wrote inspection to %s (%d samples)", output_path, len(samples))


_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Retrieval Inspection</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/java.min.js"></script>
<style>
:root {
  --bg:#1e1e2e; --surface:#313244; --surface2:#45475a; --text:#cdd6f4;
  --sub:#a6adc8; --accent:#f5c2e7; --green:#a6e3a1; --red:#f38ba8;
  --yellow:#f9e2af; --blue:#89b4fa; --teal:#94e2d5; --mauve:#cba6f7;
  --peach:#fab387; --border:#585b70;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'JetBrains Mono','SF Mono','Cascadia Code',monospace;font-size:12px;background:var(--bg);color:var(--text);display:flex;height:100vh;overflow:hidden}

/* Sidebar */
#sidebar{width:300px;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0}
#sidebar-header{padding:12px;border-bottom:1px solid var(--border)}
#sidebar-header h2{font-size:13px;color:var(--accent);margin-bottom:8px}
#search{width:100%;padding:6px 8px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:4px;font-family:inherit;font-size:11px}
#sample-list{flex:1;overflow-y:auto}
.si{padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border);font-size:11px}
.si:hover{background:var(--bg)}
.si.active{background:var(--blue);color:#1e1e2e}
.si .mn{font-weight:bold;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.si .cls{color:var(--sub);font-size:10px;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.si .stats{color:var(--sub);font-size:10px;margin-top:2px}

/* Main */
#main{flex:1;display:flex;flex-direction:column;overflow:hidden}
#tabs{display:flex;border-bottom:1px solid var(--border);background:var(--surface);flex-wrap:wrap}
.tab{padding:8px 14px;cursor:pointer;color:var(--sub);border-bottom:2px solid transparent;font-size:11px;white-space:nowrap}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
#content{flex:1;overflow-y:auto;padding:16px}

/* Components */
.section{margin-bottom:20px}
.section-title{font-size:12px;font-weight:bold;color:var(--accent);margin-bottom:8px;display:flex;align-items:center;gap:8px}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:bold}
.badge-blue{background:var(--blue);color:#1e1e2e}
.badge-green{background:var(--green);color:#1e1e2e}
.badge-yellow{background:var(--yellow);color:#1e1e2e}
.badge-red{background:var(--red);color:#1e1e2e}
.badge-mauve{background:var(--mauve);color:#1e1e2e}

pre.code-block{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;overflow-x:auto;margin:4px 0;font-size:11px;line-height:1.5;white-space:pre}
pre.code-block code{font-family:inherit}

.fim-prefix{color:var(--green)}
.fim-suffix{color:var(--peach)}
.fim-middle{color:var(--red);font-weight:bold}
.fim-token{background:var(--surface2);color:var(--yellow);padding:1px 4px;border-radius:3px;font-weight:bold}

.kv-grid{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:11px;margin:4px 0}
.kv-grid .k{color:var(--sub);text-align:right;white-space:nowrap}
.kv-grid .v{color:var(--text);word-break:break-all}

.result-card{background:var(--surface);border:1px solid var(--border);border-radius:6px;margin-bottom:12px;overflow:hidden}
.result-header{padding:10px 12px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;cursor:pointer}
.result-header:hover{background:var(--surface2)}
.result-body{padding:12px;display:none}
.result-body.open{display:block}
.rank{font-size:14px;font-weight:bold;color:var(--blue);min-width:24px}
.score{color:var(--yellow);font-weight:bold;font-size:11px}
.sig{color:var(--green);font-size:11px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.query-box{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;font-size:11px;word-break:break-all;color:var(--sub);max-height:300px;overflow-y:auto;line-height:1.6}

table.inv-table{width:100%;border-collapse:collapse;font-size:11px;margin:4px 0}
table.inv-table th{background:var(--surface);color:var(--accent);padding:4px 8px;text-align:left;border:1px solid var(--border)}
table.inv-table td{padding:4px 8px;border:1px solid var(--border);font-size:10px}
table.inv-table tr:nth-child(even){background:var(--surface)}

.tag-list{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0}
.tag{background:var(--surface2);color:var(--text);padding:2px 6px;border-radius:3px;font-size:10px}
</style>
</head>
<body>

<div id="sidebar">
  <div id="sidebar-header">
    <h2>Retrieval Inspection</h2>
    <input id="search" type="text" placeholder="Search methods...">
  </div>
  <div id="sample-list"></div>
</div>

<div id="main">
  <div id="tabs">
    <div class="tab active" data-t="prompt">Full Prompt</div>
    <div class="tab" data-t="retrieval">Retrieved Methods</div>
    <div class="tab" data-t="query">Search Query</div>
    <div class="tab" data-t="augmentation">Augmentation Block</div>
    <div class="tab" data-t="method">Target Method</div>
    <div class="tab" data-t="compare">Compare Prompts</div>
  </div>
  <div id="content"></div>
</div>

<script>
const DATA = "__DATA__";
let cur = 0, tab = 'prompt', filter = '';

const $ = id => document.getElementById(id);

function esc(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function sidebar() {
  const el = $('sample-list');
  el.innerHTML = '';
  DATA.forEach((s, i) => {
    const name = s.method_id;
    if (filter && !name.toLowerCase().includes(filter)) return;
    const d = document.createElement('div');
    d.className = 'si' + (i === cur ? ' active' : '');
    const parts = name.split('#');
    const hits = s.retrieval.results.length;
    d.innerHTML = `<div class="mn">${esc(parts[1] || parts[0])}</div>
      <div class="cls">${esc(parts[0])}</div>
      <div class="stats">${hits} results | ${s.retrieval.search_time_ms}ms | ${s.invocations.length} invocations</div>`;
    d.onclick = () => { cur = i; render(); };
    el.appendChild(d);
  });
}

function fmtPrompt(prompt) {
  // Split by FIM tokens and colorize
  let html = esc(prompt);
  html = html
    .replace(/&lt;\|fim_prefix\|&gt;/g, '<span class="fim-token">&lt;|fim_prefix|&gt;</span>\n')
    .replace(/&lt;\|fim_suffix\|&gt;/g, '\n<span class="fim-token">&lt;|fim_suffix|&gt;</span>\n')
    .replace(/&lt;\|fim_middle\|&gt;/g, '\n<span class="fim-token">&lt;|fim_middle|&gt;</span>');
  return html;
}

function renderPrompt() {
  const s = DATA[cur];
  return `
    <div class="section">
      <div class="section-title">Full FIM Prompt (with retrieval)
        <span class="badge badge-blue">${s.prompt_retrieval_len} chars</span>
        <span class="badge badge-mauve">~${Math.round(s.prompt_retrieval_len/4)} tokens</span>
      </div>
      <pre class="code-block">${fmtPrompt(s.prompt_retrieval)}</pre>
    </div>
    <div class="section">
      <div class="section-title">Ground Truth (expected generation)</div>
      <pre class="code-block"><code class="language-java">${esc(s.ground_truth)}</code></pre>
    </div>`;
}

function renderOverlap(o) {
  let html = '<div style="margin-top:12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px">';
  html += '<div class="section-title" style="margin:0 0 8px">Overlap Analysis</div>';

  // Metric badges row
  html += '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">';
  html += `<span class="badge badge-blue">Type IoU: ${o.type_iou.toFixed(3)}</span>`;
  html += `<span class="badge badge-green">Owner IoU: ${o.owner_iou.toFixed(3)}</span>`;
  html += `<span class="badge badge-yellow">Oracle Recall: ${(o.oracle_recall_this_result*100).toFixed(0)}%</span>`;
  html += `<span class="badge badge-mauve">Types: ${o.shared_types.length}/${o.query_types_count} query, ${o.result_types_count} result</span>`;
  html += '</div>';

  // Shared types
  if (o.shared_types.length > 0) {
    html += '<div style="margin:6px 0"><span style="color:var(--sub);font-size:10px">Shared types: </span>';
    html += o.shared_types.map(t => '<span class="tag" style="background:var(--blue);color:#1e1e2e">'+esc(t)+'</span>').join(' ');
    html += '</div>';
  }

  // Shared invocation owners
  if (o.shared_owners.length > 0) {
    html += '<div style="margin:6px 0"><span style="color:var(--sub);font-size:10px">Shared inv. owners: </span>';
    html += o.shared_owners.map(t => '<span class="tag" style="background:var(--green);color:#1e1e2e">'+esc(t)+'</span>').join(' ');
    html += '</div>';
  }

  // Shared signature terms
  if (o.shared_sig_terms.length > 0) {
    html += '<div style="margin:6px 0"><span style="color:var(--sub);font-size:10px">Shared sig terms: </span>';
    html += o.shared_sig_terms.map(t => '<span class="tag" style="background:var(--mauve);color:#1e1e2e">'+esc(t)+'</span>').join(' ');
    html += '</div>';
  }

  // Oracle invocations found in this result
  if (o.oracle_found.length > 0) {
    html += '<div style="margin:6px 0"><span style="color:var(--sub);font-size:10px">Oracle invocations FOUND: </span>';
    html += o.oracle_found.map(t => '<span class="tag" style="background:var(--green);color:#1e1e2e">'+esc(t)+'</span>').join(' ');
    html += '</div>';
  }
  if (o.oracle_missed.length > 0) {
    html += '<div style="margin:6px 0"><span style="color:var(--sub);font-size:10px">Oracle invocations MISSED: </span>';
    html += o.oracle_missed.map(t => '<span class="tag" style="background:var(--surface2);color:var(--sub)">'+esc(t)+'</span>').join(' ');
    html += '</div>';
  }

  html += '</div>';
  return html;
}

function renderRetrieval() {
  const s = DATA[cur];
  const r = s.retrieval;
  let html = `
    <div class="section">
      <div class="section-title">Search Results
        <span class="badge badge-blue">${r.results.length} returned</span>
        <span class="badge badge-green">${r.total_hits} total hits</span>
        <span class="badge badge-yellow">${r.search_time_ms}ms</span>
      </div>`;

  r.results.forEach((res, i) => {
    const id = 'res-' + i;
    html += `
      <div class="result-card">
        <div class="result-header" onclick="document.getElementById('${id}').classList.toggle('open')">
          <span class="rank">#${res.rank}</span>
          <span class="score">score=${Number(res.score).toFixed(2)}</span>
          <span class="sig">${esc(res.signature)}</span>
          ${res.overlap ? '<span class="badge badge-blue" style="flex-shrink:0">T:'+res.overlap.type_iou.toFixed(2)+'</span><span class="badge badge-green" style="flex-shrink:0">O:'+res.overlap.shared_owners.length+'</span><span class="badge badge-yellow" style="flex-shrink:0">R:'+(res.overlap.oracle_recall_this_result*100).toFixed(0)+'%</span>' : ''}
          <span class="badge badge-mauve" style="flex-shrink:0">${esc(res.classFqn.split('.').pop())}</span>
        </div>
        <div class="result-body" id="${id}">
          <div class="kv-grid">
            <span class="k">ID:</span><span class="v">${esc(res.id)}</span>
            <span class="k">Class:</span><span class="v">${esc(res.classFqn)}</span>
            <span class="k">File:</span><span class="v">${esc(res.filePath)}</span>
            <span class="k">Score:</span><span class="v">${Number(res.score).toFixed(4)}</span>
          </div>

          ${res.overlap ? renderOverlap(res.overlap) : ''}

          ${res.explain ? '<div class="section-title" style="margin-top:12px">Lucene Score Explain</div><pre class="code-block" style="font-size:10px;max-height:250px;overflow-y:auto">' + esc(res.explain) + '</pre>' : ''}

          <div class="section-title" style="margin-top:12px">Type Profile</div>
          <pre class="code-block" style="font-size:10px;max-height:120px;overflow-y:auto">${esc(res.typeProfile || '(empty)')}</pre>

          <div class="section-title" style="margin-top:12px">Method Card</div>
          <pre class="code-block" style="font-size:10px;max-height:200px;overflow-y:auto">${esc(res.methodCard)}</pre>

          <div class="section-title" style="margin-top:12px">Invocation Profile</div>
          <pre class="code-block" style="font-size:10px;max-height:200px;overflow-y:auto">${esc(res.invocationProfile || '(empty)')}</pre>

          <div class="section-title" style="margin-top:12px">Method Body</div>
          <pre class="code-block"><code class="language-java">${esc(res.methodBody || '(empty)')}</code></pre>
        </div>
      </div>`;
  });

  html += '</div>';
  return html;
}

function renderQuery() {
  const s = DATA[cur];
  const req = s.search_request;
  let html = `
    <div class="section">
      <div class="section-title">Lucene Query (parsed)</div>
      <div class="query-box">${esc(s.retrieval.query_debug)}</div>
    </div>

    <div class="section">
      <div class="section-title">Search Request Fields</div>
      <div class="kv-grid">
        <span class="k">methodSignature:</span><span class="v">${esc(req.methodSignature)}</span>
        <span class="k">classFqn:</span><span class="v">${esc(req.classFqn)}</span>
        <span class="k">targetFilePath:</span><span class="v">${esc(req.targetFilePath)}</span>
        <span class="k">targetBodyStartOffset:</span><span class="v">${req.targetBodyStartOffset}</span>
        <span class="k">topK:</span><span class="v">${req.topK}</span>
        <span class="k">nearDuplicateThreshold:</span><span class="v">${req.nearDuplicateThreshold}</span>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Supertypes</div>
      <div class="tag-list">${(req.supertypes||[]).map(t => '<span class="tag">'+esc(t)+'</span>').join('') || '<span class="tag" style="color:var(--sub)">(none)</span>'}</div>
    </div>

    <div class="section">
      <div class="section-title">Imports (sent to query)</div>
      <div class="tag-list">${(req.imports||[]).map(t => '<span class="tag">'+esc(t)+'</span>').join('') || '<span class="tag" style="color:var(--sub)">(none)</span>'}</div>
    </div>

    <div class="section">
      <div class="section-title">Class Fields (types sent to query)</div>
      <div class="tag-list">${(req.classFields||[]).map(t => '<span class="tag">'+esc(t)+'</span>').join('') || '<span class="tag" style="color:var(--sub)">(none)</span>'}</div>
    </div>

    <div class="section">
      <div class="section-title">Sibling Signatures</div>
      <div style="font-size:11px">${(req.siblingSignatures||[]).map(t => '<div style="margin:2px 0;color:var(--green)">'+esc(t)+'</div>').join('') || '<span style="color:var(--sub)">(none)</span>'}</div>
    </div>

    <div class="section">
      <div class="section-title">Sibling Invocations (sent to query)</div>
      <div style="font-size:10px;max-height:200px;overflow-y:auto">${(req.siblingInvocations||[]).map(t => '<div style="margin:1px 0">'+esc(t)+'</div>').join('') || '<span style="color:var(--sub)">(none)</span>'}</div>
    </div>

    <div class="section">
      <div class="section-title">Sibling Used Types</div>
      <div class="tag-list">${(req.siblingUsedTypes||[]).map(t => '<span class="tag">'+esc(t)+'</span>').join('') || '<span style="color:var(--sub)">(none)</span>'}</div>
    </div>

    <div class="section">
      <div class="section-title">FIM Prefix (last 500 chars, sent to context query)</div>
      <pre class="code-block" style="font-size:10px;max-height:200px;overflow-y:auto">${esc(req.fimPrefix)}</pre>
    </div>

    <div class="section">
      <div class="section-title">FIM Suffix (first 500 chars, sent to context query)</div>
      <pre class="code-block" style="font-size:10px;max-height:200px;overflow-y:auto">${esc(req.fimSuffix)}</pre>
    </div>`;

  return html;
}

function renderAugmentation() {
  const s = DATA[cur];
  return `
    <div class="section">
      <div class="section-title">Augmentation Block (as inserted into prompt)
        <span class="badge badge-blue">${s.augmentation_block.length} chars</span>
        <span class="badge badge-mauve">~${Math.round(s.augmentation_block.length/4)} tokens</span>
      </div>
      <pre class="code-block"><code class="language-java">${esc(s.augmentation_block)}</code></pre>
    </div>
    <div class="section">
      <div class="section-title">Ground Truth (what the model should generate)</div>
      <pre class="code-block"><code class="language-java">${esc(s.ground_truth)}</code></pre>
    </div>`;
}

function renderMethod() {
  const s = DATA[cur];
  let html = `
    <div class="section">
      <div class="section-title">Target Method</div>
      <div class="kv-grid">
        <span class="k">ID:</span><span class="v">${esc(s.method_id)}</span>
        <span class="k">File:</span><span class="v">${esc(s.file_path)}</span>
        <span class="k">Class:</span><span class="v">${esc(s.class_fqn)}</span>
        <span class="k">Signature:</span><span class="v" style="color:var(--green)">${esc(s.method_signature)}</span>
        <span class="k">Return type:</span><span class="v">${esc(s.return_type || '(void)')}</span>
        <span class="k">Param types:</span><span class="v">${esc((s.parameter_types||[]).join(', ') || '(none)')}</span>
        <span class="k">Siblings:</span><span class="v">${s.sibling_count} methods in same class</span>
      </div>
    </div>

    <div class="section">
      <div class="section-title">Supertypes</div>
      <div class="tag-list">${(s.supertypes||[]).map(t => '<span class="tag">'+esc(t)+'</span>').join('') || '<span class="tag" style="color:var(--sub)">(none)</span>'}</div>
    </div>

    <div class="section">
      <div class="section-title">Imports</div>
      <div class="tag-list">${(s.imports||[]).map(t => '<span class="tag">'+esc(t)+'</span>').join('') || '<span class="tag" style="color:var(--sub)">(none)</span>'}</div>
    </div>

    <div class="section">
      <div class="section-title">Class Fields</div>`;
  if (s.class_fields && s.class_fields.length) {
    html += '<table class="inv-table"><tr><th>Name</th><th>Type</th></tr>';
    s.class_fields.forEach(f => { html += `<tr><td>${esc(f.name)}</td><td>${esc(f.typeFqn)}</td></tr>`; });
    html += '</table>';
  } else {
    html += '<span style="color:var(--sub)">(none)</span>';
  }

  html += `
    </div>
    <div class="section">
      <div class="section-title">Oracle Invocations <span class="badge badge-yellow">${s.invocations.length}</span></div>`;
  if (s.invocations.length) {
    html += '<table class="inv-table"><tr><th>#</th><th>Signature</th><th>Mode</th></tr>';
    s.invocations.forEach(inv => {
      const color = inv.resolution_mode === 'EXACT' ? 'var(--green)' : 'var(--red)';
      html += `<tr><td>${inv.order_index}</td><td style="font-size:10px">${esc(inv.signature)}</td><td style="color:${color}">${inv.resolution_mode}</td></tr>`;
    });
    html += '</table>';
  }

  html += `
    </div>
    <div class="section">
      <div class="section-title">Ground Truth Body</div>
      <pre class="code-block"><code class="language-java">${esc(s.ground_truth)}</code></pre>
    </div>`;
  return html;
}

function renderCompare() {
  const s = DATA[cur];
  return `
    <div class="section">
      <div class="section-title">Prompt WITHOUT augmentation
        <span class="badge badge-blue">${s.prompt_no_aug_len} chars</span>
        <span class="badge badge-mauve">~${Math.round(s.prompt_no_aug_len/4)} tokens</span>
      </div>
      <pre class="code-block" style="max-height:45vh;overflow-y:auto">${fmtPrompt(s.prompt_no_aug)}</pre>
    </div>
    <div class="section">
      <div class="section-title">Prompt WITH retrieval augmentation
        <span class="badge badge-blue">${s.prompt_retrieval_len} chars</span>
        <span class="badge badge-mauve">~${Math.round(s.prompt_retrieval_len/4)} tokens</span>
        <span class="badge badge-green">+${s.prompt_retrieval_len - s.prompt_no_aug_len} chars</span>
      </div>
      <pre class="code-block" style="max-height:45vh;overflow-y:auto">${fmtPrompt(s.prompt_retrieval)}</pre>
    </div>`;
}

const RENDERERS = {
  prompt: renderPrompt,
  retrieval: renderRetrieval,
  query: renderQuery,
  augmentation: renderAugmentation,
  method: renderMethod,
  compare: renderCompare,
};

function render() {
  sidebar();
  $('content').innerHTML = RENDERERS[tab]();
  document.querySelectorAll('pre code.language-java').forEach(b => hljs.highlightElement(b));
}

document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    tab = t.dataset.t;
    render();
  });
});
$('search').addEventListener('input', e => { filter = e.target.value.toLowerCase(); sidebar(); });
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowDown' && cur < DATA.length - 1) { cur++; render(); }
  if (e.key === 'ArrowUp' && cur > 0) { cur--; render(); }
  const tabs = ['prompt','retrieval','query','augmentation','method','compare'];
  const n = parseInt(e.key);
  if (n >= 1 && n <= tabs.length) {
    tab = tabs[n-1];
    document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active', i===n-1));
    render();
  }
});

// Auto-open first result on retrieval tab
render();
</script>
</body>
</html>'''


if __name__ == "__main__":
    main()
