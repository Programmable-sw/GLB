#!/usr/bin/env python3
"""Replace MRC in the archived A2A heatmap with three-seed no-rearm data."""

import csv
import hashlib
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import TwoSlopeNorm


ROOT = Path(__file__).resolve().parents[2]
BASELINE_FIGURES = (
    ROOT / "experiments/n-mrc/output/n-mrc-results/figures")
SEED13 = (
    ROOT / "experiments/n-mrc/output/"
    "mrc_one_cycle_no_rearm_a2a_seed13_20260725/results.csv")
SEED29_47 = (
    ROOT / "experiments/n-mrc/output/"
    "mrc_one_cycle_no_rearm_a2a_seed29_47_20260725/results.csv")
OUT = (
    ROOT / "experiments/n-mrc/output/"
    "mrc_one_cycle_no_rearm_a2a_three_seed_20260725/heatmap")
SEEDS = (13, 29, 47)
SCHEMES = ("ops", "reps", "mrc", "sglb", "n-mrc")
ESTABLISHED_BASELINES = ("ops", "reps", "mrc", "sglb")


def read_csv(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows):
    rows = list(rows)
    fields = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def geomean(values):
    values = tuple(values)
    if not values or any(value <= 0 for value in values):
        raise ValueError("geometric mean requires positive values")
    return math.exp(sum(math.log(value) for value in values) / len(values))

def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_new_mrc():
    rows = read_csv(SEED13) + read_csv(SEED29_47)
    lookup = {}
    for row in rows:
        identity = (row["scenario"], int(row["seed"]))
        if identity in lookup:
            raise ValueError(f"duplicate new MRC cell {identity}")
        if (
                int(row["returncode"]) != 0 or
                int(row["config_ok"]) != 1 or
                int(row["all_flows_completed"]) != 1):
            raise ValueError(f"invalid new MRC cell {identity}")
        lookup[identity] = float(row["primary_us"])
    return lookup


def build_source(new_mrc):
    archived = [
        row for row in read_csv(BASELINE_FIGURES / "heatmap_source.csv")
        if row["figure"] == "alltoall" and row["scheme"] in SCHEMES
    ]
    scenarios = []
    labels = {}
    for row in archived:
        scenario = row["scenario"]
        if scenario not in scenarios:
            scenarios.append(scenario)
            labels[scenario] = row["column_label"]
    if len(scenarios) != 18:
        raise ValueError(f"expected 18 A2A scenarios, got {len(scenarios)}")

    by_cell = {(row["scenario"], row["scheme"]): dict(row)
               for row in archived}
    expected = {(scenario, scheme)
                for scenario in scenarios for scheme in SCHEMES}
    if set(by_cell) != expected:
        raise ValueError("archived heatmap source is incomplete")
    old_mrc = {
        scenario: float(by_cell[(scenario, "mrc")]["geomean_us"])
        for scenario in scenarios
    }

    seed_metrics = []
    for scenario in scenarios:
        values = []
        for seed in SEEDS:
            identity = (scenario, seed)
            if identity not in new_mrc:
                raise ValueError(f"missing new MRC cell {identity}")
            value = new_mrc[identity]
            values.append(value)
            seed_metrics.append({
                "scenario": scenario,
                "scheme": "mrc",
                "seed": seed,
                "all_to_all_cct_us": value,
            })
        cell = by_cell[(scenario, "mrc")]
        cell["geomean_us"] = geomean(values)
        cell["seed_count"] = len(values)
        cell["seed_values_us"] = ";".join(
            f"{seed}:{value:.6f}" for seed, value in zip(SEEDS, values))
        cell["implementation"] = "one_cycle_no_rearm"

    source = []
    for scenario in scenarios:
        baseline = min(
            float(by_cell[(scenario, scheme)]["geomean_us"])
            for scheme in ESTABLISHED_BASELINES)
        for scheme in SCHEMES:
            row = by_cell[(scenario, scheme)]
            absolute = float(row["geomean_us"])
            row["baseline_us"] = baseline
            row["ratio"] = absolute / baseline
            row["baseline_schemes"] = ";".join(ESTABLISHED_BASELINES)
            if scheme != "mrc":
                row["implementation"] = "archived"
                row["seed_values_us"] = "archived_three_seed"
            source.append(row)
    comparison = []
    for scenario in scenarios:
        new_value = float(by_cell[(scenario, "mrc")]["geomean_us"])
        comparison.append({
            "scenario": scenario,
            "old_mrc_geomean_us": old_mrc[scenario],
            "new_mrc_no_rearm_geomean_us": new_value,
            "new_vs_old_mrc_pct": (
                (new_value / old_mrc[scenario] - 1.0) * 100.0),
        })
    return scenarios, labels, source, seed_metrics, comparison


def render(scenarios, labels, source):
    lookup = {(row["scheme"], row["scenario"]): float(row["ratio"])
              for row in source}
    matrix = np.asarray([
        [lookup[(scheme, scenario)] for scenario in scenarios]
        for scheme in SCHEMES
    ])
    upper = max(1.05, math.ceil(float(matrix.max()) * 100) / 100)
    norm = TwoSlopeNorm(vmin=0.70, vcenter=1.0, vmax=upper)
    fig, axis = plt.subplots(figsize=(20, 7), layout="constrained")
    image = axis.imshow(
        matrix, cmap="RdBu_r", aspect="auto", norm=norm)
    axis.set_xticks(range(len(scenarios)))
    axis.set_xticklabels(
        [labels[scenario] for scenario in scenarios],
        rotation=38, ha="right", fontsize=9)
    axis.set_yticks(range(len(SCHEMES)))
    axis.set_yticklabels(
        ["OPS", "REPS", "MRC", "SGLB", "n-MRC"], fontsize=11)
    axis.set_title(
        "All-to-All CCT · 128 nodes · three-seed geometric mean\n"
        "MRC uses one-cycle no-rearm; lower is better",
        loc="left", fontsize=16)

    for boundary in (2.5, 8.5, 14.5):
        axis.axvline(boundary, color="#a8adb3", linewidth=0.7)
    for boundary in (5.5, 11.5):
        axis.axvline(boundary, color="#4b4f54", linewidth=1.2)
    for row_index in range(matrix.shape[0]):
        for column_index in range(matrix.shape[1]):
            value = matrix[row_index, column_index]
            color = "white" if value >= 1.19 else "#202124"
            axis.text(
                column_index, row_index, f"{value:.2f}",
                ha="center", va="center", fontsize=8.5, color=color)

    colorbar = fig.colorbar(image, ax=axis, pad=0.018)
    colorbar.set_label(
        "Ratio to best established baseline (lower is better)", fontsize=11)
    fig.savefig(
        OUT / "alltoall_cct_heatmap_mrc_no_rearm.png",
        dpi=220, bbox_inches="tight", facecolor="white")
    fig.savefig(
        OUT / "alltoall_cct_heatmap_mrc_no_rearm.pdf",
        bbox_inches="tight", facecolor="white")
    plt.close(fig)

    write_csv(OUT / "alltoall_cct_heatmap_matrix.csv", [
        {
            "panel": "All-to-All",
            "metric": "all_to_all_cct_us",
            "scheme": scheme,
            **{
                scenario: matrix[index, column]
                for column, scenario in enumerate(scenarios)
            },
        }
        for index, scheme in enumerate(SCHEMES)
    ])
    return matrix


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    new_mrc = load_new_mrc()
    scenarios, labels, source, seed_metrics, comparison = build_source(new_mrc)
    matrix = render(scenarios, labels, source)
    write_csv(OUT / "heatmap_source.csv", source)
    write_csv(OUT / "mrc_no_rearm_seed_metrics.csv", seed_metrics)
    write_csv(OUT / "mrc_no_rearm_vs_old_mrc.csv", comparison)
    validation = {
        "seeds": list(SEEDS),
        "scenario_count": len(scenarios),
        "new_mrc_run_count": len(new_mrc),
        "matrix_shape": list(matrix.shape),
        "schemes": list(SCHEMES),
        "established_baselines": list(ESTABLISHED_BASELINES),
        "aggregation": "geometric_mean_before_normalization",
        "mrc_replaced": True,
        "original_figure": str(
            BASELINE_FIGURES / "alltoall_cct_heatmap.png"),
        "original_figure_sha256": file_sha256(
            BASELINE_FIGURES / "alltoall_cct_heatmap.png"),
        "matrix_min": float(matrix.min()),
        "matrix_max": float(matrix.max()),
    }
    (OUT / "validation.json").write_text(
        json.dumps(validation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps(validation, sort_keys=True))


if __name__ == "__main__":
    main()
