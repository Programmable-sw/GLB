#!/usr/bin/env python3
"""Plot and summarize the seed13 n-MRC routing/cooldown matrix."""

import argparse
import csv
import gzip
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


NMRC_VARIANTS = (
    "graded_selective",
    "graded_full",
    "graded_none",
    "graded_delta025",
    "fixed05",
    "delta025",
)
PLOT_VARIANTS = ("mrc", *NMRC_VARIANTS)
VARIANT_LABELS = {
    "mrc": "MRC baseline",
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
        row["mean_fct_us"] = float(row["mean_fct_us"])
        row["p50_fct_us"] = float(row["p50_fct_us"])
        row["p99_fct_us"] = float(row["p99_fct_us"])
        row["size_mib"] = int(row["size_mib"])
        row["parallel"] = int(row["parallel"])
        row["websearch_load_pct"] = int(row["websearch_load_pct"])
        row["degraded"] = int(row["degraded"])
        row["diagnostics"] = json.loads(row["diagnostics_json"])
    return rows


def percentile(values, quantile):
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low = int(position)
    high = min(len(ordered) - 1, low + 1)
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def load_mrc_baseline(result_dir, comparison_rows):
    output_dir = result_dir.parent
    baseline_rows = []
    a2a_path = (
        output_dir /
        "mrc_one_cycle_no_rearm_a2a_seed13_20260725/results.csv")
    with a2a_path.open(newline="", encoding="utf-8") as handle:
        for source in csv.DictReader(handle):
            if source["scheme"] != "mrc" or int(source["seed"]) != 13:
                continue
            row = dict(source)
            row["variant"] = "mrc"
            row["primary_us"] = float(row["primary_us"])
            row["mean_fct_us"] = float(row["mean_fct_us"])
            row["p50_fct_us"] = float(row["p50_fct_us"])
            row["p99_fct_us"] = float(row["p99_fct_us"])
            row["size_mib"] = int(row["size_mib"])
            row["parallel"] = int(row["parallel"])
            row["websearch_load_pct"] = int(row["websearch_load_pct"])
            row["degraded"] = int(row["degraded"])
            row["baseline_source"] = str(a2a_path)
            baseline_rows.append(row)

    web_root = (
        output_dir / "mrc_inherent_limitations_128" /
        "raw/flow_lifetime")
    for condition in ("healthy", "asymmetric"):
        for load in (40, 60, 80, 100):
            archived_scenario = f"exp1_{condition}_websearch_{load}pct"
            run_dir = web_root / archived_scenario / "mrc/seed_13"
            summary_path = run_dir / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if (
                    summary["valid"] != 1 or
                    summary["scheme"] != "mrc" or
                    summary["seed"] != 13):
                raise ValueError(f"invalid MRC baseline: {summary_path}")
            metrics_path = run_dir / "flow_metrics.json.gz"
            with gzip.open(metrics_path, "rt", encoding="utf-8") as handle:
                flow_rows = json.load(handle)
            if len(flow_rows) != summary["connections"]:
                raise ValueError(
                    f"incomplete MRC baseline: {metrics_path}: "
                    f"{len(flow_rows)} != {summary['connections']}")
            fcts = [float(item["fct_us"]) for item in flow_rows]
            mean_fct = sum(fcts) / len(fcts)
            p50_fct = percentile(fcts, 0.50)
            p99_fct = percentile(fcts, 0.99)
            scenario = f"{condition}_p2p_websearch_{load}pct"
            baseline_rows.append({
                "scenario": scenario,
                "family": condition,
                "kind": "point_to_point",
                "traffic": "websearch",
                "size_mib": 0,
                "websearch_load_pct": load,
                "parallel": 0,
                "degraded": int(condition == "asymmetric"),
                "variant": "mrc",
                "seed": 13,
                "primary_metric": "p99_fct_us",
                "primary_us": p99_fct,
                "mean_fct_us": mean_fct,
                "p50_fct_us": p50_fct,
                "p99_fct_us": p99_fct,
                "traffic_sha256": summary["traffic_sha256"],
                "completed": summary["connections"],
                "expected_flows": summary["connections"],
                "all_flows_completed": 1,
                "baseline_source": str(run_dir),
            })

    expected_scenarios = {row["scenario"] for row in comparison_rows}
    actual_scenarios = {row["scenario"] for row in baseline_rows}
    if actual_scenarios != expected_scenarios or len(baseline_rows) != 26:
        raise ValueError(
            "MRC baseline scenario mismatch: "
            f"missing={sorted(expected_scenarios - actual_scenarios)}, "
            f"extra={sorted(actual_scenarios - expected_scenarios)}")
    hashes = {}
    for row in comparison_rows:
        hashes.setdefault(row["scenario"], set()).add(row["traffic_sha256"])
    for row in baseline_rows:
        expected_hashes = hashes[row["scenario"]]
        if expected_hashes != {row["traffic_sha256"]}:
            raise ValueError(
                f"traffic hash mismatch for {row['scenario']}: "
                f"MRC={row['traffic_sha256']}, comparison={expected_hashes}")
    return baseline_rows


def write_mrc_baseline_csv(path, rows):
    fields = (
        "scenario", "variant", "seed", "primary_metric", "primary_us",
        "mean_fct_us", "p50_fct_us", "p99_fct_us", "traffic_sha256",
        "baseline_source")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item["scenario"]):
            writer.writerow({field: row[field] for field in fields})


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
    return metric_matrix(rows, scenarios, "primary_us")


def metric_matrix(rows, scenarios, metric):
    lookup = {
        (row["variant"], row["scenario"]): row[metric]
        for row in rows}
    return np.asarray([
        [lookup[(variant, scenario)] for scenario in scenarios]
        for variant in PLOT_VARIANTS
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

    ax.set_yticks(range(len(PLOT_VARIANTS)))
    ax.set_yticklabels([VARIANT_LABELS[item] for item in PLOT_VARIANTS])
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


def draw_websearch_mean_p99(mean_values, p99_values, scenarios, path):
    width = max(11.0, len(scenarios) * 0.9 + 3.2)
    fig, axes = plt.subplots(
        2, 1, figsize=(width, 9.8), sharex=True)
    fig.subplots_adjust(
        left=0.20, right=0.94, bottom=0.13, top=0.86, hspace=0.30)
    fig.suptitle(
        "WebSearch FCT · mean and P99",
        x=0.015, y=0.975, ha="left", fontsize=16, color="#20242a")
    fig.text(
        0.015, 0.935,
        "MRC baseline + n-MRC variants · 128 nodes · seed 13 · "
        "offered load 40–100% · lower is better",
        ha="left", va="top", fontsize=10.5, color="#505761")

    for ax, values, panel_title, cmap in (
            (axes[0], mean_values, "Mean FCT (µs)", "YlGnBu"),
            (axes[1], p99_values, "P99 FCT (µs)", "YlOrBr")):
        vmin = float(np.min(values))
        vmax = float(np.max(values))
        image = ax.imshow(
            values, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)
        annotate(
            ax, values, lambda value: f"{value:.1f}",
            color_threshold=vmin + 0.72 * (vmax - vmin))
        ax.set_yticks(range(len(PLOT_VARIANTS)))
        ax.set_yticklabels(
            [VARIANT_LABELS[item] for item in PLOT_VARIANTS])
        ax.tick_params(length=0)
        ax.set_title(
            panel_title, loc="left", fontsize=12.5, pad=9,
            color="#20242a")
        for spine in ax.spines.values():
            spine.set_color("#30343b")
            spine.set_linewidth(0.8)
        colorbar = fig.colorbar(
            image, ax=ax, fraction=0.025, pad=0.02)
        colorbar.set_label("µs", color="#30343b")

    axes[1].set_xticks(range(len(scenarios)))
    axes[1].set_xticklabels(
        [scenario_label(item) for item in scenarios],
        rotation=35, ha="right")
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def draw_websearch_mean_p50_p99(
        mean_values, p50_values, p99_values, scenarios, path):
    width = max(11.0, len(scenarios) * 0.9 + 3.2)
    fig, axes = plt.subplots(
        3, 1, figsize=(width, 13.2), sharex=True)
    fig.subplots_adjust(
        left=0.20, right=0.94, bottom=0.10, top=0.88, hspace=0.29)
    fig.suptitle(
        "WebSearch FCT · mean, P50, and P99",
        x=0.015, y=0.978, ha="left", fontsize=16, color="#20242a")
    fig.text(
        0.015, 0.947,
        "MRC baseline + n-MRC variants · 128 nodes · seed 13 · "
        "offered load 40–100% · independent color scales · lower is better",
        ha="left", va="top", fontsize=10.5, color="#505761")

    panels = (
        (axes[0], mean_values, "Mean FCT (µs)"),
        (axes[1], p50_values, "P50 FCT (µs)"),
        (axes[2], p99_values, "P99 FCT (µs)"),
    )
    for ax, values, panel_title in panels:
        vmin = float(np.min(values))
        vmax = float(np.max(values))
        image = ax.imshow(
            values, aspect="auto", cmap="YlGnBu", vmin=vmin, vmax=vmax)
        annotate(
            ax, values, lambda value: f"{value:.1f}",
            color_threshold=vmin + 0.72 * (vmax - vmin))
        ax.set_yticks(range(len(PLOT_VARIANTS)))
        ax.set_yticklabels(
            [VARIANT_LABELS[item] for item in PLOT_VARIANTS])
        ax.tick_params(length=0)
        ax.set_title(
            panel_title, loc="left", fontsize=12.5, pad=9,
            color="#20242a")
        for spine in ax.spines.values():
            spine.set_color("#30343b")
            spine.set_linewidth(0.8)
        colorbar = fig.colorbar(
            image, ax=ax, fraction=0.025, pad=0.02)
        colorbar.set_label("µs", color="#30343b")

    axes[2].set_xticks(range(len(scenarios)))
    axes[2].set_xticklabels(
        [scenario_label(item) for item in scenarios],
        rotation=35, ha="right")
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_matrix_csv(path, values, scenarios):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["variant", *scenarios])
        for variant, row in zip(PLOT_VARIANTS, values):
            writer.writerow([variant, *[f"{value:.6f}" for value in row]])


def aggregate(rows, predicate):
    selected = [row for row in rows if predicate(row)]
    grouped = {}
    for variant in NMRC_VARIANTS:
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
        for variant in NMRC_VARIANTS
    }


def main():
    args = parse_args()
    result_dir = args.result_dir.resolve()
    rows = read_rows(result_dir / "results.csv")
    if len(rows) != 156:
        raise ValueError(f"expected 156 rows, got {len(rows)}")
    mrc_rows = load_mrc_baseline(result_dir, rows)
    write_mrc_baseline_csv(
        result_dir / "mrc_baseline_seed13.csv", mrc_rows)
    rows.extend(mrc_rows)
    figures = result_dir / "figures"
    figures.mkdir(exist_ok=True)

    a2a = a2a_order(rows)
    websearch = websearch_order(rows)
    a2a_values = matrix(rows, a2a)
    websearch_values = matrix(rows, websearch)
    websearch_mean_values = metric_matrix(
        rows, websearch, "mean_fct_us")
    websearch_p50_values = metric_matrix(
        rows, websearch, "p50_fct_us")
    websearch_p99_values = metric_matrix(
        rows, websearch, "p99_fct_us")
    if a2a_values.shape != (7, 18) or websearch_values.shape != (7, 8):
        raise ValueError(
            f"unexpected shapes: A2A={a2a_values.shape}, "
            f"WebSearch={websearch_values.shape}")

    draw_heatmap(
        a2a_values, a2a,
        "All-to-all CCT · MRC baseline + n-MRC variants",
        "128 nodes · seed 13 · 18 matched archived traffic matrices",
        figures / "a2a_cct_absolute_us.png", False)
    draw_heatmap(
        a2a_values, a2a,
        "All-to-all CCT · normalized per scenario",
        "MRC baseline + n-MRC · seed 13 · each column / column best",
        figures / "a2a_cct_ratio_to_best.png", True)
    draw_heatmap(
        websearch_values, websearch,
        "WebSearch P99 FCT · MRC baseline + n-MRC variants",
        "128 nodes · seed 13 · offered load 40–100%",
        figures / "websearch_p99_absolute_us.png", False)
    draw_heatmap(
        websearch_values, websearch,
        "WebSearch P99 FCT · normalized per scenario",
        "MRC baseline + n-MRC · seed 13 · each column / column best",
        figures / "websearch_p99_ratio_to_best.png", True)
    draw_websearch_mean_p99(
        websearch_mean_values, websearch_p99_values, websearch,
        figures / "websearch_mean_p99_absolute_us.png")
    draw_websearch_mean_p50_p99(
        websearch_mean_values, websearch_p50_values,
        websearch_p99_values, websearch,
        figures / "websearch_mean_p50_p99_absolute_us.png")
    write_matrix_csv(
        result_dir / "a2a_cct_matrix_us.csv", a2a_values, a2a)
    write_matrix_csv(
        result_dir / "websearch_p99_matrix_us.csv",
        websearch_p99_values, websearch)
    write_matrix_csv(
        result_dir / "websearch_mean_matrix_us.csv",
        websearch_mean_values, websearch)
    write_matrix_csv(
        result_dir / "websearch_p50_matrix_us.csv",
        websearch_p50_values, websearch)

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
