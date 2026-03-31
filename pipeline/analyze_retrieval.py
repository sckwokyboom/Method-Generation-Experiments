"""Diagnostic analysis: compare no_augmentation vs retrieval_augmentation per sample.

Answers:
- Where does retrieval help vs hurt?
- Which retrieval metrics correlate with generation improvement?
- What patterns distinguish helped vs hurt samples?

Usage:
    python -m pipeline.analyze_retrieval ./results
    python -m pipeline.analyze_retrieval ./results --output analysis.html
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
from dataclasses import dataclass
from pathlib import Path

from pipeline.report import load_sample_result
from pipeline.models import SampleResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


@dataclass
class PairedSample:
    index: int
    method_id: str
    baseline: SampleResult
    retrieval: SampleResult

    @property
    def es_delta(self) -> float:
        return self.retrieval.metrics.es - self.baseline.metrics.es

    @property
    def iou_delta(self) -> float:
        return self.retrieval.metrics.iou - self.baseline.metrics.iou

    @property
    def lcs_delta(self) -> float:
        return self.retrieval.metrics.lcs_ratio - self.baseline.metrics.lcs_ratio

    @property
    def codebleu_delta(self) -> float | None:
        if self.retrieval.metrics.codebleu is None or self.baseline.metrics.codebleu is None:
            return None
        return self.retrieval.metrics.codebleu - self.baseline.metrics.codebleu

    @property
    def compilable_delta(self) -> int | None:
        bc = self.baseline.compilability
        rc = self.retrieval.compilability
        if bc is None or rc is None:
            return None
        return int(rc.success) - int(bc.success)

    @property
    def recall(self) -> float:
        return self.retrieval.metrics.recall_at_k or 0.0

    @property
    def api_coverage(self) -> float:
        return self.retrieval.metrics.api_coverage_at_k or 0.0

    @property
    def precision(self) -> float:
        return self.retrieval.metrics.retrieval_precision_at_k or 0.0

    @property
    def ndcg(self) -> float:
        return self.retrieval.metrics.retrieval_ndcg_at_k or 0.0

    @property
    def owner_recall(self) -> float:
        return self.retrieval.metrics.owner_type_recall or 0.0

    @property
    def n_invocations(self) -> int:
        return len(self.baseline.invocations_ordered)

    @property
    def n_retrieved(self) -> int:
        return len(self.retrieval.retrieval_results) if self.retrieval.retrieval_results else 0

    def category(self, metric: str = "es", threshold: float = 0.02) -> str:
        d = getattr(self, f"{metric}_delta")
        if d is None:
            return "unknown"
        if d > threshold:
            return "helped"
        elif d < -threshold:
            return "hurt"
        return "neutral"


def load_paired_samples(results_dir: Path) -> list[PairedSample]:
    baseline_dir = results_dir / "no_augmentation" / "samples"
    retrieval_dir = results_dir / "retrieval_augmentation" / "samples"

    if not baseline_dir.exists():
        raise SystemExit(f"No baseline samples at {baseline_dir}")
    if not retrieval_dir.exists():
        raise SystemExit(f"No retrieval samples at {retrieval_dir}")

    pairs = []
    for bp in sorted(baseline_dir.glob("sample_*.json")):
        rp = retrieval_dir / bp.name
        if not rp.exists():
            continue
        b = load_sample_result(bp)
        r = load_sample_result(rp)
        idx = int(bp.stem.split("_")[1])
        pairs.append(PairedSample(index=idx, method_id=b.method_id, baseline=b, retrieval=r))

    return pairs


def print_terminal_report(pairs: list[PairedSample]) -> None:
    n = len(pairs)
    print(f"\n{'='*80}")
    print(f"RETRIEVAL DIAGNOSTIC ANALYSIS — {n} paired samples")
    print(f"{'='*80}")

    # 1. Win/Loss/Tie breakdown
    for metric in ["es", "iou", "lcs", "codebleu"]:
        threshold = 0.02
        helped = [p for p in pairs if p.category(metric, threshold) == "helped"]
        hurt = [p for p in pairs if p.category(metric, threshold) == "hurt"]
        neutral = [p for p in pairs if p.category(metric, threshold) == "neutral"]
        unknown = n - len(helped) - len(hurt) - len(neutral)

        deltas = [getattr(p, f"{metric}_delta") for p in pairs if getattr(p, f"{metric}_delta") is not None]
        avg = statistics.mean(deltas) if deltas else 0
        print(f"\n  {metric.upper():>10s}:  helped={len(helped):3d}  hurt={len(hurt):3d}  neutral={len(neutral):3d}"
              f"  | mean delta={avg:+.4f}")

        if helped:
            avg_recall_h = statistics.mean([p.recall for p in helped])
            avg_prec_h = statistics.mean([p.precision for p in helped])
            avg_invoc_h = statistics.mean([p.n_invocations for p in helped])
            print(f"    {'helped':>10s}: avg recall={avg_recall_h:.2f}, precision={avg_prec_h:.2f}, "
                  f"invocations={avg_invoc_h:.1f}")

        if hurt:
            avg_recall_u = statistics.mean([p.recall for p in hurt])
            avg_prec_u = statistics.mean([p.precision for p in hurt])
            avg_invoc_u = statistics.mean([p.n_invocations for p in hurt])
            print(f"    {'hurt':>10s}: avg recall={avg_recall_u:.2f}, precision={avg_prec_u:.2f}, "
                  f"invocations={avg_invoc_u:.1f}")

    # 2. Correlation: retrieval quality vs generation delta
    print(f"\n{'─'*80}")
    print("CORRELATION: retrieval metrics → ES delta")
    print(f"{'─'*80}")

    es_deltas = [p.es_delta for p in pairs]
    for ret_metric in ["recall", "api_coverage", "precision", "ndcg", "owner_recall"]:
        ret_values = [getattr(p, ret_metric) for p in pairs]
        corr = _pearson(ret_values, es_deltas)
        print(f"  {ret_metric:>20s} → ES delta:  r={corr:+.3f}")

    # 3. Top helped and hurt samples
    print(f"\n{'─'*80}")
    print("TOP 5 HELPED (by ES delta)")
    print(f"{'─'*80}")
    by_es = sorted(pairs, key=lambda p: p.es_delta, reverse=True)
    for p in by_es[:5]:
        print(f"  [{p.index:3d}] {p.method_id:60s}  ES: {p.es_delta:+.4f}  "
              f"recall={p.recall:.2f} prec={p.precision:.2f} invoc={p.n_invocations}")

    print(f"\n{'─'*80}")
    print("TOP 5 HURT (by ES delta)")
    print(f"{'─'*80}")
    for p in by_es[-5:]:
        print(f"  [{p.index:3d}] {p.method_id:60s}  ES: {p.es_delta:+.4f}  "
              f"recall={p.recall:.2f} prec={p.precision:.2f} invoc={p.n_invocations}")

    # 4. Retrieval quality buckets → generation delta
    print(f"\n{'─'*80}")
    print("RETRIEVAL QUALITY BUCKETS → mean ES delta")
    print(f"{'─'*80}")

    for metric_name, attr in [("recall", "recall"), ("precision", "precision"), ("owner_recall", "owner_recall")]:
        buckets = {"0.0": [], "0.0-0.3": [], "0.3-0.6": [], "0.6+": []}
        for p in pairs:
            v = getattr(p, attr)
            if v == 0:
                buckets["0.0"].append(p)
            elif v < 0.3:
                buckets["0.0-0.3"].append(p)
            elif v < 0.6:
                buckets["0.3-0.6"].append(p)
            else:
                buckets["0.6+"].append(p)

        print(f"\n  {metric_name}:")
        for bucket_name, bucket_samples in buckets.items():
            if bucket_samples:
                avg_delta = statistics.mean([p.es_delta for p in bucket_samples])
                avg_cb_delta = statistics.mean([d for d in [p.codebleu_delta for p in bucket_samples] if d is not None] or [0])
                print(f"    {bucket_name:>8s}: n={len(bucket_samples):3d}  "
                      f"ES_delta={avg_delta:+.4f}  CodeBLEU_delta={avg_cb_delta:+.4f}")

    # 5. Summary insight
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")

    zero_recall = [p for p in pairs if p.recall == 0]
    has_recall = [p for p in pairs if p.recall > 0]
    if zero_recall:
        avg_es_zero = statistics.mean([p.es_delta for p in zero_recall])
        print(f"  Samples with recall=0 (n={len(zero_recall)}): mean ES delta = {avg_es_zero:+.4f}")
    if has_recall:
        avg_es_has = statistics.mean([p.es_delta for p in has_recall])
        print(f"  Samples with recall>0 (n={len(has_recall)}): mean ES delta = {avg_es_has:+.4f}")

    high_recall = [p for p in pairs if p.recall >= 0.5]
    if high_recall:
        avg_es_high = statistics.mean([p.es_delta for p in high_recall])
        avg_cb_high = statistics.mean([d for d in [p.codebleu_delta for p in high_recall] if d is not None] or [0])
        print(f"  Samples with recall>=0.5 (n={len(high_recall)}): "
              f"ES delta = {avg_es_high:+.4f}, CodeBLEU delta = {avg_cb_high:+.4f}")


def generate_html_report(pairs: list[PairedSample], output_path: Path) -> None:
    """Generate an interactive HTML report for per-sample analysis."""
    samples_data = []
    for p in pairs:
        samples_data.append({
            "index": p.index,
            "method_id": p.method_id,
            "es_delta": round(p.es_delta, 4),
            "iou_delta": round(p.iou_delta, 4),
            "lcs_delta": round(p.lcs_delta, 4),
            "codebleu_delta": round(p.codebleu_delta, 4) if p.codebleu_delta is not None else None,
            "compilable_delta": p.compilable_delta,
            "baseline_es": round(p.baseline.metrics.es, 4),
            "retrieval_es": round(p.retrieval.metrics.es, 4),
            "baseline_codebleu": round(p.baseline.metrics.codebleu, 4) if p.baseline.metrics.codebleu else None,
            "retrieval_codebleu": round(p.retrieval.metrics.codebleu, 4) if p.retrieval.metrics.codebleu else None,
            "recall": round(p.recall, 4),
            "api_coverage": round(p.api_coverage, 4),
            "precision": round(p.precision, 4),
            "ndcg": round(p.ndcg, 4),
            "owner_recall": round(p.owner_recall, 4),
            "n_invocations": p.n_invocations,
            "n_retrieved": p.n_retrieved,
            "category_es": p.category("es"),
            "category_codebleu": p.category("codebleu"),
            "baseline_generated": p.baseline.generated[:500],
            "retrieval_generated": p.retrieval.generated[:500],
            "ground_truth": p.baseline.ground_truth[:500],
        })

    data_json = json.dumps(samples_data, ensure_ascii=False, default=str)
    data_json = data_json.replace("</", "<\\/")
    html = _HTML_TEMPLATE.replace('"__DATA__"', data_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    log.info("HTML report written to %s", output_path)


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx = sum((xi - mx) ** 2 for xi in x) ** 0.5
    dy = sum((yi - my) ** 2 for yi in y) ** 0.5
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


_HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Retrieval Diagnostic Analysis</title>
<style>
:root{--bg:#1e1e2e;--surface:#313244;--text:#cdd6f4;--sub:#a6adc8;--green:#a6e3a1;--red:#f38ba8;--yellow:#f9e2af;--blue:#89b4fa;--border:#585b70;--accent:#f5c2e7}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'SF Mono',monospace;font-size:12px;background:var(--bg);color:var(--text);padding:20px}
h1{color:var(--accent);margin-bottom:16px;font-size:16px}
h2{color:var(--blue);margin:20px 0 10px;font-size:14px}
table{border-collapse:collapse;width:100%;margin:8px 0}
th,td{padding:6px 10px;border:1px solid var(--border);text-align:left;font-size:11px}
th{background:var(--surface);color:var(--accent)}
.pos{color:var(--green);font-weight:bold}
.neg{color:var(--red);font-weight:bold}
.neu{color:var(--sub)}
.bar{display:inline-block;height:12px;border-radius:2px;vertical-align:middle}
.bar-pos{background:var(--green)}
.bar-neg{background:var(--red)}
.filters{margin:10px 0;display:flex;gap:8px;flex-wrap:wrap}
.filter-btn{padding:4px 10px;background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:4px;cursor:pointer;font-size:11px}
.filter-btn.active{background:var(--blue);color:#1e1e2e}
.sort-header{cursor:pointer;user-select:none}
.sort-header:hover{color:var(--yellow)}
.summary-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin:12px 0}
.summary-card{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:12px}
.summary-card .label{color:var(--sub);font-size:10px;text-transform:uppercase}
.summary-card .value{font-size:18px;font-weight:bold;margin-top:4px}
</style>
</head>
<body>
<h1>Retrieval Diagnostic: Per-Sample Analysis</h1>

<div id="summary"></div>

<h2>Per-Sample Table</h2>
<div class="filters">
  <button class="filter-btn active" data-filter="all">All</button>
  <button class="filter-btn" data-filter="helped">Helped (ES)</button>
  <button class="filter-btn" data-filter="hurt">Hurt (ES)</button>
  <button class="filter-btn" data-filter="neutral">Neutral (ES)</button>
</div>
<table id="main-table">
  <thead><tr>
    <th class="sort-header" data-col="index">#</th>
    <th>Method</th>
    <th class="sort-header" data-col="es_delta">ES Δ</th>
    <th class="sort-header" data-col="codebleu_delta">CB Δ</th>
    <th class="sort-header" data-col="recall">Recall</th>
    <th class="sort-header" data-col="precision">Prec.</th>
    <th class="sort-header" data-col="owner_recall">Owner</th>
    <th class="sort-header" data-col="n_invocations">Invoc.</th>
    <th class="sort-header" data-col="n_retrieved">Retr.</th>
    <th>ES bar</th>
  </tr></thead>
  <tbody id="tbody"></tbody>
</table>

<script>
const DATA = "__DATA__";
let sortCol = 'es_delta', sortAsc = true, filterCat = 'all';

function fmt(v, digits=4) {
  if (v === null || v === undefined) return 'N/A';
  const s = Number(v).toFixed(digits);
  return v > 0.001 ? '+'+s : s;
}
function cls(v) { return v > 0.02 ? 'pos' : v < -0.02 ? 'neg' : 'neu'; }
function bar(v) {
  const w = Math.min(Math.abs(v) * 500, 100);
  const c = v >= 0 ? 'bar-pos' : 'bar-neg';
  return `<span class="bar ${c}" style="width:${w}px"></span>`;
}

function renderSummary() {
  const n = DATA.length;
  const helped = DATA.filter(d => d.category_es === 'helped').length;
  const hurt = DATA.filter(d => d.category_es === 'hurt').length;
  const neutral = n - helped - hurt;
  const avgES = DATA.reduce((s,d) => s + d.es_delta, 0) / n;
  const avgCB = DATA.filter(d=>d.codebleu_delta!==null).reduce((s,d) => s + (d.codebleu_delta||0), 0) / DATA.filter(d=>d.codebleu_delta!==null).length;

  document.getElementById('summary').innerHTML = `
    <div class="summary-grid">
      <div class="summary-card"><div class="label">Samples</div><div class="value">${n}</div></div>
      <div class="summary-card"><div class="label">Helped (ES)</div><div class="value pos">${helped}</div></div>
      <div class="summary-card"><div class="label">Hurt (ES)</div><div class="value neg">${hurt}</div></div>
      <div class="summary-card"><div class="label">Neutral</div><div class="value neu">${neutral}</div></div>
      <div class="summary-card"><div class="label">Mean ES Δ</div><div class="value ${cls(avgES)}">${fmt(avgES)}</div></div>
      <div class="summary-card"><div class="label">Mean CB Δ</div><div class="value ${cls(avgCB)}">${fmt(avgCB)}</div></div>
    </div>`;
}

function renderTable() {
  let rows = [...DATA];
  if (filterCat !== 'all') rows = rows.filter(d => d.category_es === filterCat);
  rows.sort((a,b) => {
    const va = a[sortCol] ?? -999, vb = b[sortCol] ?? -999;
    return sortAsc ? va - vb : vb - va;
  });

  document.getElementById('tbody').innerHTML = rows.map(d => `<tr>
    <td>${d.index}</td>
    <td style="font-size:10px;max-width:300px;overflow:hidden;text-overflow:ellipsis">${d.method_id}</td>
    <td class="${cls(d.es_delta)}">${fmt(d.es_delta)}</td>
    <td class="${cls(d.codebleu_delta)}">${fmt(d.codebleu_delta)}</td>
    <td>${(d.recall*100).toFixed(0)}%</td>
    <td>${(d.precision*100).toFixed(0)}%</td>
    <td>${(d.owner_recall*100).toFixed(0)}%</td>
    <td>${d.n_invocations}</td>
    <td>${d.n_retrieved}</td>
    <td>${bar(d.es_delta)}</td>
  </tr>`).join('');
}

document.querySelectorAll('.sort-header').forEach(th => {
  th.onclick = () => {
    const col = th.dataset.col;
    if (sortCol === col) sortAsc = !sortAsc;
    else { sortCol = col; sortAsc = false; }
    renderTable();
  };
});
document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    filterCat = btn.dataset.filter;
    renderTable();
  };
});

renderSummary();
sortAsc = true;
renderTable();
</script>
</body>
</html>'''


def main():
    parser = argparse.ArgumentParser(description="Diagnostic analysis of retrieval vs baseline")
    parser.add_argument("results_dir", type=Path, help="Results directory with no_augmentation/ and retrieval_augmentation/")
    parser.add_argument("--output", type=Path, default=None, help="HTML report output path")
    parser.add_argument("--threshold", type=float, default=0.02, help="Delta threshold for helped/hurt (default: 0.02)")
    args = parser.parse_args()

    pairs = load_paired_samples(args.results_dir)
    log.info("Loaded %d paired samples", len(pairs))

    print_terminal_report(pairs)

    output = args.output or (args.results_dir / "retrieval_analysis.html")
    generate_html_report(pairs, output)
    print(f"\nHTML report: {output}")


if __name__ == "__main__":
    main()
