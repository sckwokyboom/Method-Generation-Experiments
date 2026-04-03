#!/usr/bin/env python3
"""Minimalist visualization of comparison_table.txt for slide presentations."""

import sys
import re
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np

METRIC_LABELS = {
    "em": "Exact Match",
    "es": "Edit Similarity",
    "iou": "IoU",
    "lcs_ratio": "LCS Ratio",
    "lcs_no_ident_ratio": "LCS (no ident)",
    "codebleu": "CodeBLEU",
    "compilable": "Compilable",
    "test_pass": "Test Pass",
    "recall_at_k": "Recall@k",
    "api_coverage_at_k": "API Cov@k",
    "mrr": "MRR",
    "retrieval_precision_at_k": "Precision@k",
    "retrieval_ndcg_at_k": "NDCG@k",
    "retrieval_type_iou": "Type IoU",
    "owner_type_recall": "Owner Recall",
}


def parse_table(text: str):
    lines = [l for l in text.strip().splitlines() if not l.startswith("+")]
    header_line, *data_lines = lines

    cols = [c.strip() for c in header_line.split("|")[1:-1]]
    headers = cols[1:]  # skip "Metric"

    metrics, means, stds = [], [], []
    for line in data_lines:
        cells = [c.strip() for c in line.split("|")[1:-1]]
        raw_name = cells[0]
        label = METRIC_LABELS.get(raw_name, raw_name)
        metrics.append(label)

        row_mean, row_std = [], []
        for cell in cells[1:]:
            m = re.match(r"([\d.]+)\s*\(std=([\d.]+)\)", cell)
            if m:
                row_mean.append(float(m.group(1)))
                row_std.append(float(m.group(2)))
            else:
                row_mean.append(None)
                row_std.append(None)
        means.append(row_mean)
        stds.append(row_std)

    return headers, metrics, means, stds


def render(headers, metrics, means, stds, output_path: Path):
    n_rows = len(metrics)
    n_cols = len(headers)

    mean_arr = np.array([[v if v is not None else np.nan for v in row] for row in means])

    # find best value per row for bold highlighting
    row_best = []
    for row in mean_arr:
        if np.all(np.isnan(row)):
            row_best.append(-1)
        else:
            row_best.append(int(np.nanargmax(row)))

    fig_w = max(6, 1.6 * n_cols + 2.4)
    fig_h = max(3, 0.38 * n_rows + 1.0)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_axis_off()
    fig.patch.set_facecolor("white")

    # clean colors
    white = "#ffffff"
    zebra = "#f7f8fa"
    header_bg = "#ffffff"
    text_main = "#1a1a2e"
    text_secondary = "#8b8fa3"
    accent_line = "#4361ee"
    na_color = "#c5c7d0"

    table_data = []
    cell_colors = []

    # header row
    table_data.append([""] + headers)
    cell_colors.append([header_bg] * (n_cols + 1))

    for i, (metric, row_m, row_s) in enumerate(zip(metrics, means, stds)):
        bg = white if i % 2 == 0 else zebra
        row_text = [metric]
        row_colors = [bg]
        for j in range(n_cols):
            if row_m[j] is not None:
                row_text.append(f"{row_m[j]:.4f}")
            else:
                row_text.append("—")
            row_colors.append(bg)
        table_data.append(row_text)
        cell_colors.append(row_colors)

    tbl = ax.table(
        cellText=table_data,
        cellColours=cell_colors,
        cellLoc="center",
        loc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.5)

    for (r, c), cell in tbl.get_celld().items():
        cell.set_linewidth(0)
        cell.set_edgecolor(white)

        if r == 0:
            # header: accent underline, bold
            cell.set_text_props(
                color=text_main, fontweight="bold", fontsize=10,
                fontfamily="sans-serif",
            )
            cell.visible_edges = "B"
            cell.set_edgecolor(accent_line)
            cell.set_linewidth(2)
        elif c == 0:
            # metric labels: left-aligned, medium weight
            cell.set_text_props(
                ha="left", fontweight="medium", fontsize=9,
                color=text_main, fontfamily="sans-serif",
            )
            cell.PAD = 0.04
            cell.visible_edges = ""
        else:
            data_row = r - 1
            is_best = (row_best[data_row] == c - 1)
            is_na = means[data_row][c - 1] is None

            if is_na:
                cell.set_text_props(
                    color=na_color, fontsize=9, fontfamily="sans-serif",
                )
            elif is_best:
                cell.set_text_props(
                    color=accent_line, fontweight="bold", fontsize=9.5,
                    fontfamily="sans-serif",
                )
            else:
                cell.set_text_props(
                    color=text_main, fontsize=9, fontfamily="sans-serif",
                )
            cell.visible_edges = ""

    fig.tight_layout(pad=0.3)
    fig.savefig(output_path, dpi=250, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Saved → {output_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python visualize_table.py <comparison_table.txt>")
        sys.exit(1)

    src = Path(sys.argv[1])
    text = src.read_text()
    headers, metrics, means, stds = parse_table(text)
    out = src.with_suffix(".png")
    render(headers, metrics, means, stds, out)


if __name__ == "__main__":
    main()
