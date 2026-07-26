#!/usr/bin/env python3
"""Plot and summarize the seed13 n-MRC routing/cooldown matrix."""

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


VARIANTS = (
    "graded_selective",
    "graded_full",
    "graded_none",
    "graded_delta025",
    "fixed05",
    "delta025",
)
VARIANT_LABELS = {
    "graded_selective": "Graded · selective CD",
    "graded_full": "Graded · full CD",
    "graded_none": "Graded · no CD",
    "graded_delta025": "Graded Δ≥0.25 · full CD",
    "fixed05": "Fixed 0.5",
    "delta025": "Raw Δ≥0.25",
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    return parser.parse_args()


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["primary_us"] = float(row["primary_us"])
        row["size_mib"] = int(row["size_mib"])
        row["parallel"] = int(row["parallel"])
        row["websearch_load_pct"] = int(row["websearch_load_pct"])
        row["degraded"] = int(row["degraded"])
        row["diagnostics"] = json.loads(row["diagnostics_json"])
    return rows


def a2a_order(rows):
    available = {row["scenario"] for row in rows if row["kind"] == "all_to_all"}
    result = []
    for size in (64, 256, 1024):
        for prefix in ("healthy_alltoall", "asymmetric_alltoall_background"):
            for parallel in (4, 8, 16):
                name = f"{prefix}_{size}mib_p{parallel}"
                if name in available:
                    result.append(name)
    return result


def websearch_order(rows):
    available = {
        row["scenario"] for row in rows if row["traffic"] == "websearch"}
    result = []
    for prefix in ("healthy_p2p", "asymmetric_p2p"):
        for load in (40, 60, 80, 100):
            name = f"{prefix}_websearch_{load}pct"
            if name in available:
                result.append(name)
    return result


def scenario_label(name):
    if "alltoall" in name:
        condition = "Healthy" if name.startswith("healthy") else "Asym+BG"
        size = name.split("_")[-2].replace("mib", " MiB")
        parallel = name.split("_")[-1].upper()
        return f"{size}\n{condition} · {parallel}"
    condition = "Healthy" if name.startswith("healthy") else "Asym"
    load = name.split("_")[-1].replace("pct", "%")
    return f"{condition}\n{load}"


def matrix(rows, scenarios):
    lookup = {
        (row["variant"], row["scenario"]): row["primary_us"]
        for row in rows}
    return np.asarray([
        [lookup[(variant, scenario)] for scenario in scenarios]
        for variant in VARIANTS
    ], dtype=float)


def annotate(ax, values, fmt, color_threshold=None):
    for row in range(values.shape[0]):
        for column in range(values.shape[1]):
            value = values[row, column]
            color = "#ffffff" if (
                color_threshold is not None and
                value >= color_threshold) else "#20242a"
            ax.text(
                column, row, fmt(value), ha="center", va="center",
                fontsize=7.5, color=color)


def draw_heatmap(values, scenarios, title, subtitle, path, ratio):
    width = max(11.0, len(scenarios) * 0.72 + 3.2)
    fig, ax = plt.subplots(figsize=(width, 5.3), constrained_layout=True)
    if ratio:
        shown = values / np.min(values, axis=0, keepdims=True)
        vmax = max(1.05, min(1.35, math.ceil(shown.max() * 20) / 20))
        image = ax.imshow(
            shown, aspect="auto", cmap="OrRd", vmin=1.0, vmax=vmax)
        annotate(
            ax, shown, lambda value: f"{value:.3f}",
            color_threshold=1.0 + 0.72 * (vmax - 1.0))
        color_label = "Ratio to best variant in each column (lower is better)"
    else:
        shown = values
        vmin = float(np.min(shown))
        vmax = float(np.max(shown))
        image = ax.imshow(
            shown, aspect="auto", cmap="YlOrBr", vmin=vmin, vmax=vmax)
        annotate(
            ax, shown, lambda value: f"{value:.1f}",
            color_threshold=vmin + 0.72 * (vmax - vmin))
        color_label = "µs (lower is better)"

    ax.set_yticks(range(len(VARIANTS)))
    ax.set_yticklabels([VARIANT_LABELS[item] for item in VARIANTS])
    ax.set_xticks(range(len(scenarios)))
    ax.set_xticklabels(
        [scenario_label(item) for item in scenarios],
        rotation=45, ha="right")
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_color("#30343b")
        spine.set_linewidth(0.8)
    ax.set_title(title, loc="left", fontsize=15, pad=26, color="#20242a")
    ax.text(
        0, 1.025, subtitle, transform=ax.transAxes,
        ha="left", va="bottom", fontsize=10.5, color="#505761")
    colorbar = fig.colorbar(image, ax=ax, fraction=0.025, pad=0.02)
    colorbar.set_label(color_label, color="#30343b")
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_matrix_csv(path, values, scenarios):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["variant", *scenarios])
        for variant, row in zip(VARIANTS, values):
            writer.writerow([variant, *[f"{value:.6f}" for value in row]])


def aggregate(rows, predicate):
    selected = [row for row in rows if predicate(row)]
    grouped = {}
    for variant in VARIANTS:
        values = [
            row["primary_us"] for row in selected
            if row["variant"] == variant]
        grouped[variant] = math.exp(
            sum(math.log(value) for value in values) / len(values))
    best = min(grouped.values())
    return {
        variant: {
            "geomean_primary_us": grouped[variant],
            "ratio_to_best": grouped[variant] / best,
            "cell_count": len([
                row for row in selected if row["variant"] == variant]),
        }
        for variant in VARIANTS
    }


def main():
    args = parse_args()
    result_dir = args.result_dir.resolve()
    rows = read_rows(result_dir / "results.csv")
    if len(rows) != 156:
        raise ValueError(f"expected 156 rows, got {len(rows)}")
    figures = result_dir / "figures"
    figures.mkdir(exist_ok=True)

    a2a = a2a_order(rows)
    websearch = websearch_order(rows)
    a2a_values = matrix(rows, a2a)
    websearch_values = matrix(rows, websearch)
    if a2a_values.shape != (6, 18) or websearch_values.shape != (6, 8):
        raise ValueError(
            f"unexpected shapes: A2A={a2a_values.shape}, "
            f"WebSearch={websearch_values.shape}")

    draw_heatmap(
        a2a_values, a2a,
        "All-to-all CCT · n-MRC routing/cooldown variants",
        "128 nodes · seed 13 · 18 archived traffic matrices",
        figures / "a2a_cct_absolute_us.png", False)
    draw_heatmap(
        a2a_values, a2a,
        "All-to-all CCT · normalized per scenario",
        "128 nodes · seed 13 · each column divided by its best variant",
        figures / "a2a_cct_ratio_to_best.png", True)
    draw_heatmap(
        websearch_values, websearch,
        "WebSearch P99 FCT · n-MRC routing/cooldown variants",
        "128 nodes · seed 13 · offered load 40–100%",
        figures / "websearch_p99_absolute_us.png", False)
    draw_heatmap(
        websearch_values, websearch,
        "WebSearch P99 FCT · normalized per scenario",
        "128 nodes · seed 13 · each column divided by its best variant",
        figures / "websearch_p99_ratio_to_best.png", True)
    write_matrix_csv(
        result_dir / "a2a_cct_matrix_us.csv", a2a_values, a2a)
    write_matrix_csv(
        result_dir / "websearch_p99_matrix_us.csv",
        websearch_values, websearch)

    summary = {
        "a2a_all": aggregate(rows, lambda row: row["kind"] == "all_to_all"),
        "a2a_healthy": aggregate(
            rows, lambda row:
            row["kind"] == "all_to_all" and not row["degraded"]),
        "a2a_asymmetric_background": aggregate(
            rows, lambda row:
            row["kind"] == "all_to_all" and row["degraded"]),
        "websearch_all": aggregate(
            rows, lambda row: row["traffic"] == "websearch"),
        "websearch_healthy": aggregate(
            rows, lambda row:
            row["traffic"] == "websearch" and not row["degraded"]),
        "websearch_asymmetric": aggregate(
            rows, lambda row:
            row["traffic"] == "websearch" and row["degraded"]),
    }
    (result_dir / "aggregate_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"wrote figures and summaries under {result_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
