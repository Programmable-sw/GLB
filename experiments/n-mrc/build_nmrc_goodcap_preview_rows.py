#!/usr/bin/env python3
"""Replace the preview heatmaps' n-MRC row with fresh GoodCap results."""

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


SCHEMES = (
    "ecmp_rr", "ops", "reps", "mrc", "sglb", "ar", "drill",
    "avail", "grade", "netaware",
)
BASELINES = ("ecmp_rr", "ops", "reps", "mrc", "sglb", "ar", "drill")
CONFIG_MARKERS = (
    "dcqcn_variant_inflate=natural",
    "roce_trim_recovery=cumulative",
    "weight_adaptation good_share_cap",
)


def read_matrix(path):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames[1:], list(reader)


def write_matrix(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("scheme", *columns))
        writer.writeheader()
        writer.writerows(rows)


def read_seed_metrics(path, metric):
    values = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["scheme"] in BASELINES:
                values[(row["scenario"], row["scheme"])] = float(row[metric])
    return values


def load_goodcap_results(root, scenarios, metric):
    found = {}
    audit_rows = []
    for path in root.rglob("result.json"):
        result = json.loads(path.read_text(encoding="utf-8"))
        scenario = result.get("scenario")
        if scenario not in scenarios or result.get("scheme") != "netaware":
            continue
        if scenario in found:
            raise ValueError(f"duplicate GoodCap result for {scenario}")
        if (result.get("returncode") != 0 or result.get("config_ok") is not True
                or result.get("all_flows_completed") is not True):
            raise ValueError(f"incomplete GoodCap result: {path}")
        stdout_path = Path(result["stdout"])
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        missing = [marker for marker in CONFIG_MARKERS if marker not in stdout]
        if missing:
            raise ValueError(f"{path} missing runtime markers: {missing}")
        command = [str(item) for item in result["command"]]
        if "-cc" not in command or command[command.index("-cc") + 1] != "dcqcn_variant":
            raise ValueError(f"{path} did not use natural dcqcn_variant")
        if "-roce_trim_recovery" in command:
            raise ValueError(f"{path} unexpectedly overrides TRIM recovery")
        if "-netaware_weight_adaptation" in command:
            raise ValueError(f"{path} unexpectedly overrides n-MRC adaptation")
        value = float(result[metric])
        if value <= 0 or not math.isfinite(value):
            raise ValueError(f"{path} has invalid {metric}={value}")
        found[scenario] = value
        audit_rows.append({
            "scenario": scenario,
            "metric": metric,
            "value_us": value,
            "target_completed": result["target_completed"],
            "target_flows": result["target_flows"],
            "nacks": result["nacks"],
            "rtos": result["rtos"],
            "retx_packets": result["retx_packets"],
            "result_json": str(path.resolve()),
            "stdout": str(stdout_path.resolve()),
        })
    missing = sorted(set(scenarios) - set(found))
    if missing:
        raise ValueError(f"missing GoodCap scenarios under {root}: {missing}")
    return found, sorted(audit_rows, key=lambda row: row["scenario"])


def replace_nmrc_row(matrix_rows, columns, baseline_values, goodcap_values):
    by_scheme = {row["scheme"]: dict(row) for row in matrix_rows}
    if set(by_scheme) != set(SCHEMES):
        raise ValueError("source matrix scheme set is incomplete")
    replacement = {"scheme": "netaware"}
    for scenario in columns:
        denominator = min(
            baseline_values[(scenario, scheme)] for scheme in BASELINES
        )
        replacement[scenario] = goodcap_values[scenario] / denominator
    by_scheme["netaware"] = replacement
    return [by_scheme[scheme] for scheme in SCHEMES]


def scenario_label(scenario):
    return (scenario.replace("standard_websearch_proxy_", "websearch_")
            .replace("diagnostic_single_slow_link_permutation_", "slow_link_")
            .replace("_background_target", "")
            .replace("_permutation", "_perm")
            .replace("_mib", "M")
            .replace("pct", "%"))


def render_heatmap(path, columns, rows, title, colorbar_label, figsize):
    lookup = {(row["scheme"], scenario): float(row[scenario])
              for row in rows for scenario in columns}
    matrix = [[lookup[(scheme, scenario)] for scenario in columns]
              for scheme in SCHEMES]
    values = [value for line in matrix for value in line]
    spread = max(max(values) - 1.0, 1.0 - min(values), 0.05)
    fig, axis = plt.subplots(figsize=figsize, layout="constrained")
    image = axis.imshow(
        matrix, aspect="auto", cmap="RdBu_r",
        vmin=max(0, 1.0 - spread), vmax=1.0 + spread,
    )
    axis.set_xticks(range(len(columns)))
    axis.set_xticklabels(
        [scenario_label(item) for item in columns],
        rotation=38, ha="right", fontsize=10,
    )
    axis.set_yticks(range(len(SCHEMES)))
    axis.set_yticklabels(SCHEMES, fontsize=12)
    axis.set_xlabel("Workload mode", fontsize=13)
    axis.set_ylabel("Load-balancing scheme", fontsize=13)
    axis.set_title(title, fontsize=15)
    for row_index, line in enumerate(matrix):
        for column_index, value in enumerate(line):
            axis.text(
                column_index, row_index, f"{value:.2f}",
                ha="center", va="center", fontsize=9,
                color=(
                    "white" if abs(value - 1) > spread * 0.44
                    else "#111111"
                ),
            )
    bar = fig.colorbar(image, ax=axis, pad=0.015)
    bar.set_label(colorbar_label, fontsize=12)
    bar.ax.tick_params(labelsize=10)
    fig.savefig(
        path, dpi=220, bbox_inches="tight", facecolor="white",
        metadata={"Creator": "csg-htsim n-MRC GoodCap row rerun"},
    )
    plt.close(fig)


def write_audit(path, rows):
    fields = (
        "scenario", "metric", "value_us", "target_completed",
        "target_flows", "nacks", "rtos", "retx_packets", "result_json",
        "stdout",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--p2p-results", type=Path, required=True)
    parser.add_argument("--alltoall-results", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    preview = args.preview.resolve()

    p2p_columns, p2p_source = read_matrix(
        preview / "p2p_p99_heatmap_cap15_partial_matrix.csv"
    )
    p2p_baselines = read_seed_metrics(
        preview / "point_to_point_seed_metrics.csv", "p99_fct_us"
    )
    p2p_goodcap, p2p_audit = load_goodcap_results(
        args.p2p_results.resolve(), p2p_columns, "p99_fct_us"
    )
    p2p_rows = replace_nmrc_row(
        p2p_source, p2p_columns, p2p_baselines, p2p_goodcap
    )
    p2p_stem = "p2p_p99_heatmap_goodcap_natural_cumulative"
    write_matrix(preview / f"{p2p_stem}_matrix.csv", p2p_columns, p2p_rows)
    write_audit(preview / f"{p2p_stem}_nmrc_metrics.csv", p2p_audit)
    render_heatmap(
        preview / f"{p2p_stem}_large_numbers.png",
        p2p_columns, p2p_rows,
        "Point-to-point p99 FCT by workload mode",
        "Ratio to best baseline in the same cell", (18, 6.2),
    )

    alltoall_columns, alltoall_source = read_matrix(
        preview / "alltoall_cct_heatmap_cap15_matrix.csv"
    )
    alltoall_baselines = read_seed_metrics(
        preview / "alltoall_seed_metrics.csv", "all_to_all_cct_us"
    )
    alltoall_goodcap, alltoall_audit = load_goodcap_results(
        args.alltoall_results.resolve(), alltoall_columns,
        "all_to_all_cct_us",
    )
    alltoall_rows = replace_nmrc_row(
        alltoall_source, alltoall_columns,
        alltoall_baselines, alltoall_goodcap,
    )
    alltoall_stem = "alltoall_cct_heatmap_goodcap_natural_cumulative"
    write_matrix(
        preview / f"{alltoall_stem}_matrix.csv",
        alltoall_columns, alltoall_rows,
    )
    write_audit(
        preview / f"{alltoall_stem}_nmrc_metrics.csv", alltoall_audit
    )
    render_heatmap(
        preview / f"{alltoall_stem}_large_numbers.png",
        alltoall_columns, alltoall_rows,
        "All-to-All collective completion time by communication mode",
        "CCT ratio to best baseline in the same cell", (15, 6.2),
    )


if __name__ == "__main__":
    main()
