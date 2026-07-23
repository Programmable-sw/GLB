#!/usr/bin/env python3
"""Build three-seed N-MRC4 heatmaps against the original three-seed baselines."""

import csv
import gzip
import json
import math
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import TwoSlopeNorm

REPO = Path("/home/user/workspace/csg-htsim")
ORIGINAL = REPO / "experiments/n-mrc/output/final_packet_lb_comparison_20260720"
SEED13 = REPO / "experiments/n-mrc/output/nmrc4_heatmap_seed13_20260722"
RUNS = REPO / "experiments/n-mrc/output/nmrc4_heatmap_three_seed_20260722"
OUT = REPO / "experiments/n-mrc/output/nmrc_nmrc4_three_seed_comparison_20260722"
SEEDS = (13, 29, 47)
SCHEMES = ("ops", "reps", "mrc", "sglb", "n-mrc4")
FINISH = re.compile(
    r"^Flow Roce_(\d+)_(\d+)\s+(\d+) finished at ([0-9.]+)"
    r" total bytes \d+(?: bg traffic ([01]) flowid (\d+))?"
)
MATRIX = re.compile(r"^(\d+)->(\d+) id (\d+) start (\d+) size (\d+)")
IDMAP = re.compile(r"^(\d+)\s+Roce_(\d+)_(\d+)$")


def read_csv(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows):
    fields = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_log(path):
    data = path.read_bytes()
    if data.startswith(b"\x1f\x8b"):
        return gzip.decompress(data).decode("utf-8", "replace")
    return data.split(b"\x1f\x8b", 1)[0].decode("utf-8", "replace")


def traffic_flows(path):
    result = []
    for line in path.read_text().splitlines():
        match = MATRIX.match(line)
        if match:
            result.append({"src": int(match.group(1)), "dst": int(match.group(2)),
                           "start_us": int(match.group(4)) / 1e6})
    return result


def percentile(values, quantile):
    values = sorted(values)
    position = (len(values) - 1) * quantile
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] * (high - position) + values[high] * (position - low)


def parse_case(scenario, seed):
    case = (SEED13 if seed == 13 else RUNS / ("seed%d" % seed)) / scenario
    traffic = ORIGINAL / "traffic" / ("n128_%s_seed%d.cm" % (scenario, seed))
    declared = next(int(line.split()[1]) for line in traffic.read_text().splitlines()
                    if line.startswith("Connections "))
    starts = {}
    if "_p2p_" in scenario:
        flows = traffic_flows(traffic)
        identifiers = []
        seen = set()
        for line in (case / "idmap.txt").read_text().splitlines():
            match = IDMAP.match(line)
            if match and int(match.group(1)) not in seen:
                seen.add(int(match.group(1)))
                identifiers.append((int(match.group(1)), int(match.group(2)),
                                    int(match.group(3))))
        if len(identifiers) != len(flows):
            raise ValueError("idmap mismatch %s seed %d" % (scenario, seed))
        starts = {source: flow["start_us"]
                  for (source, src, dst), flow in zip(identifiers, flows)
                  if src == flow["src"] and dst == flow["dst"]}
    samples = []
    for line in read_log(case / "stdout.log.gz").splitlines():
        match = FINISH.match(line)
        if not match:
            continue
        finish = float(match.group(4))
        fct = finish - starts[int(match.group(3))] if starts else finish
        background = int(match.group(5)) if match.group(5) is not None else None
        samples.append((fct, background))
    if len(samples) != declared:
        raise ValueError("completion mismatch %s seed %d: %d/%d" %
                         (scenario, seed, len(samples), declared))
    row = {"scenario": scenario, "seed": seed, "completed": len(samples)}
    if "_p2p_" in scenario:
        row["p99_fct_us"] = percentile([x[0] for x in samples], .99)
    elif scenario.startswith("mixed_deployment"):
        main = [fct for fct, bg in samples if bg == 0]
        background = [fct for fct, bg in samples if bg == 1]
        row["main_max_fct_us"] = max(main)
        row["ecmp_background_max_fct_us"] = max(background)
    else:
        row["all_to_all_cct_us"] = max(x[0] for x in samples)
    return row


def geomean(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))


def heatmap(ax, matrix, columns, title, schemes):
    spread = max(float(matrix.max()) - 1, 1 - float(matrix.min()), .05)
    image = ax.imshow(
        matrix, cmap="RdBu_r", aspect="auto",
        norm=TwoSlopeNorm(vmin=max(0, 1 - spread), vcenter=1, vmax=1 + spread),
    )
    ax.set_xticks(range(len(columns)), columns, rotation=38, ha="right")
    ax.set_yticks(range(len(schemes)),
                  [scheme.upper() if scheme != "n-mrc4" else "N-MRC4"
                   for scheme in schemes])
    ax.set_title(title, loc="left")
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            color = "white" if abs(matrix[i, j] - 1) > .58 * spread else "#303030"
            ax.text(j, i, "%.2f" % matrix[i, j], ha="center", va="center",
                    fontsize=8, color=color)
    return image


def panel_matrix(source, metrics, figure, panel):
    panel_rows = [row for row in source
                  if row["figure"] == figure and row["panel"] == panel]
    scenarios = []
    labels = []
    for row in panel_rows:
        if row["scenario"] not in scenarios:
            scenarios.append(row["scenario"])
            labels.append(row["column_label"])
    original = {(row["scenario"], row["scheme"]): row for row in panel_rows}
    schemes = (["ecmp"] if figure == "p2p" and panel == "Healthy" else []) + list(SCHEMES)
    matrix = []
    source_rows = []
    for scheme in schemes:
        values = []
        for scenario in scenarios:
            reference = original[(scenario, "n-mrc" if scheme == "n-mrc4" else scheme)]
            if scheme == "n-mrc4":
                metric = reference["metric"]
                seed_values = [float(metrics[(scenario, seed)][metric]) for seed in SEEDS]
                absolute = geomean(seed_values)
                baseline = float(reference["baseline_us"])
                ratio = absolute / baseline
            else:
                metric = reference["metric"]
                absolute = float(reference["geomean_us"])
                baseline = float(reference["baseline_us"])
                ratio = float(reference["ratio"])
                seed_values = []
            values.append(ratio)
            source_rows.append({
                "figure": figure, "panel": panel, "scenario": scenario,
                "column_label": reference["column_label"], "scheme": scheme,
                "metric": metric, "geomean_us": absolute,
                "baseline_us": baseline, "ratio": ratio, "seed_count": 3,
                "seed_values_us": ";".join("%d:%.9f" % pair
                    for pair in zip(SEEDS, seed_values)) if seed_values else "original_3_seed",
            })
        matrix.append(values)
    return schemes, scenarios, labels, np.asarray(matrix), source_rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source = read_csv(ORIGINAL / "figures/heatmap_source.csv")
    scenarios = sorted(set(row["scenario"] for row in source))
    parsed = [parse_case(scenario, seed) for seed in SEEDS for scenario in scenarios]
    metrics = {(row["scenario"], row["seed"]): row for row in parsed}
    write_csv(OUT / "nmrc4_seed_metrics.csv", parsed)
    all_source = []

    p2p_panels = [panel_matrix(source, metrics, "p2p", panel)
                  for panel in ("Healthy", "Asymmetric")]
    fig, axes = plt.subplots(2, 1, figsize=(21, 11), layout="constrained")
    for ax, panel, packed in zip(axes, ("Healthy", "Asymmetric"), p2p_panels):
        schemes, scenarios_, labels, matrix, rows_ = packed
        image = heatmap(ax, matrix, labels, panel + " · lower is better", schemes)
        all_source.extend(rows_)
    fig.suptitle("Point-to-point p99 FCT · 128 nodes · geometric mean of seeds 13/29/47")
    fig.colorbar(image, ax=axes, pad=.018).set_label("Slowdown to best established scheme")
    fig.savefig(OUT / "p2p_p99_heatmap_nmrc4_three_seed.png", dpi=220, bbox_inches="tight")
    fig.savefig(OUT / "p2p_p99_heatmap_nmrc4_three_seed.pdf", bbox_inches="tight")
    plt.close(fig)

    alltoall_packed = panel_matrix(
        source, metrics, "alltoall", "All-to-All")
    schemes, scenarios_, labels, matrix, rows_ = alltoall_packed
    all_source.extend(rows_)
    fig, ax = plt.subplots(figsize=(20, 7), layout="constrained")
    image = heatmap(ax, matrix, labels,
                    "All-to-All CCT · 128 nodes · geometric mean of seeds 13/29/47",
                    schemes)
    fig.colorbar(image, ax=ax, pad=.018).set_label("Slowdown to best established scheme")
    fig.savefig(OUT / "alltoall_cct_heatmap_nmrc4_three_seed.png", dpi=220, bbox_inches="tight")
    fig.savefig(OUT / "alltoall_cct_heatmap_nmrc4_three_seed.pdf", bbox_inches="tight")
    plt.close(fig)

    mixed_panels = [panel_matrix(source, metrics, "mixed", panel)
                    for panel in ("Tested traffic (bg=0)", "ECMP traffic (bg=1)")]
    fig, axes = plt.subplots(1, 2, figsize=(20, 8), layout="constrained")
    for ax, panel, packed in zip(
            axes, ("Tested traffic (bg=0)", "ECMP traffic (bg=1)"), mixed_panels):
        schemes, scenarios_, labels, matrix, rows_ = packed
        image = heatmap(ax, matrix, labels, panel + " · lower is better", schemes)
        all_source.extend(rows_)
    fig.suptitle("Mixed deployment maximum FCT · 128 nodes · geometric mean of seeds 13/29/47")
    fig.colorbar(image, ax=axes, pad=.018).set_label("Slowdown to best established scheme")
    fig.savefig(OUT / "mixed_deployment_max_fct_heatmap_nmrc4_three_seed.png",
                dpi=220, bbox_inches="tight")
    fig.savefig(OUT / "mixed_deployment_max_fct_heatmap_nmrc4_three_seed.pdf",
                bbox_inches="tight")
    plt.close(fig)

    write_csv(OUT / "heatmap_source.csv", all_source)
    for figure, panels in (("p2p", p2p_panels), ("mixed", mixed_panels)):
        matrix_rows = []
        for packed in panels:
            schemes, scenarios_, labels, matrix, panel_source = packed
            matrix_rows.extend({"panel": panel_source[0]["panel"], "scheme": scheme,
                                **dict(zip(scenarios_, matrix[index]))}
                               for index, scheme in enumerate(schemes))
        write_csv(OUT / (figure + "_heatmap_matrix.csv"), matrix_rows)
    schemes, scenarios_, labels, matrix, _ = alltoall_packed
    write_csv(OUT / "alltoall_heatmap_matrix.csv", [
        {"scheme": scheme, **dict(zip(scenarios_, matrix[index]))}
        for index, scheme in enumerate(schemes)
    ])
    (OUT / "validation.json").write_text(json.dumps({
        "seeds": list(SEEDS), "scenario_count": len(scenarios),
        "parsed_run_count": len(parsed),
        "all_runs_complete": all(row["completed"] > 0 for row in parsed),
        "nmrc4_source_cells": sum(row["scheme"] == "n-mrc4" for row in all_source),
        "baseline_schemes": ["ops", "reps", "mrc", "sglb"],
        "aggregation": "geometric_mean_before_normalization",
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
