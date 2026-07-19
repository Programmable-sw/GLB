#!/usr/bin/env python3
"""Build cross-scheme point-to-point and All-to-All performance evidence."""

import argparse
import csv
import importlib.util
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


SCHEMES = (
    "ecmp_rr", "ops", "reps", "mrc", "sglb", "ar", "drill",
    "avail", "grade", "netaware",
)
SUBJECTS = ("avail", "grade", "netaware")
ENDPOINT_BASELINES = ("reps", "mrc")
NETWORK_BASELINES = ("sglb", "ar", "drill")
STATELESS_BASELINES = ("ecmp_rr", "ops")
BASELINES = STATELESS_BASELINES + ENDPOINT_BASELINES + NETWORK_BASELINES
SEEDS = (13, 29, 47)

POINT_TO_POINT_SCENARIOS = (
    "healthy_permutation_4mib", "healthy_tornado_4mib",
    "incast_8to1_4mib", "asymmetric_permutation_4mib",
    "asymmetric_tornado_4mib", "healthy_permutation_8mib",
    "healthy_tornado_8mib", "incast_8to1_8mib",
    "asymmetric_permutation_8mib", "asymmetric_tornado_8mib",
    "healthy_permutation_16mib", "healthy_tornado_16mib",
    "incast_8to1_16mib", "asymmetric_permutation_16mib",
    "asymmetric_tornado_16mib",
    "diagnostic_single_slow_link_permutation_32mib",
    "mixed_fixed_background_target_4mib",
    "persistent_hotspot_target_4mib",
    "periodic_hotspot_target_4mib",
    "mixed_fixed_background_target_16mib",
    "persistent_hotspot_target_16mib",
    "periodic_hotspot_target_16mib",
    "standard_websearch_proxy_40pct",
    "standard_websearch_proxy_60pct",
    "standard_websearch_proxy_80pct",
)
ALLTOALL_SCENARIOS = tuple(
    f"full_global_p{parallel}_{size}mib_background_{background}"
    for parallel in (4, 8, 16)
    for size in (64, 256, 1024)
    for background in ("off", "on")
)

AGGREGATE_METRICS = (
    "avg_fct_us", "p50_fct_us", "p95_fct_us", "p99_fct_us",
    "p999_fct_us", "max_fct_us", "all_to_all_cct_us",
    "nacks", "nacks_ooo", "nacks_trim", "nacks_loss", "rtos",
    "new_packets", "retx_packets", "retx_ratio",
    "composite_trims", "composite_drops", "composite_ecn_marks",
    "lossy_drops", "queue_cv", "queue_cv_spine_queue_count",
    "feedback_count", "feedback_bytes",
    "feedback_bandwidth_mbps", "target_flows", "target_completed",
    "background_flows", "background_completed",
)

ALLTOALL_RE = re.compile(
    r"^full_global_p(?P<parallel>\d+)_(?P<size>\d+)mib_"
    r"background_(?P<background>off|on)$"
)


def workload_family(scenario):
    if scenario.startswith("healthy_"):
        return "healthy"
    if scenario.startswith("incast_"):
        return "incast"
    if scenario.startswith("asymmetric_"):
        return "asymmetric"
    if scenario.startswith("diagnostic_single_slow_link_"):
        return "single_slow_link"
    if scenario.startswith("mixed_fixed_background_"):
        return "mixed_background"
    if scenario.startswith("persistent_hotspot_"):
        return "persistent_hotspot"
    if scenario.startswith("periodic_hotspot_"):
        return "periodic_hotspot"
    if scenario.startswith("standard_websearch_proxy_"):
        return "websearch"
    raise ValueError(f"unclassified point-to-point scenario: {scenario}")


def parse_alltoall_mode(scenario):
    match = ALLTOALL_RE.fullmatch(scenario)
    if not match:
        raise ValueError(f"invalid All-to-All scenario name: {scenario}")
    return {
        "parallel": int(match.group("parallel")),
        "message_size_mib": int(match.group("size")),
        "background": match.group("background"),
    }


def aggregate_seed_rows(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(
            int(row["nodes"]), row["scenario"], row["scheme"], row["cadence"],
        )].append(row)
    output = []
    for (nodes, scenario, scheme, cadence), group in sorted(groups.items()):
        result = {
            "nodes": nodes,
            "scenario": scenario,
            "scheme": scheme,
            "cadence": cadence,
            "seed_count": len(group),
        }
        for metric in AGGREGATE_METRICS:
            values = [float(row.get(metric, 0)) for row in group]
            result[f"{metric}_median"] = statistics.median(values)
            result[f"{metric}_min"] = min(values)
            result[f"{metric}_max"] = max(values)
        output.append(result)
    return output


def normalized_scheme_rows(summary, metric):
    value_field = f"{metric}_median"
    groups = defaultdict(dict)
    for row in summary:
        groups[(int(row["nodes"]), row["scenario"])][row["scheme"]] = row
    output = []
    for (nodes, scenario), schemes in sorted(groups.items()):
        missing = set(SCHEMES) - set(schemes)
        if missing:
            raise ValueError(
                f"missing schemes for {nodes}/{scenario}: {sorted(missing)}"
            )
        baseline_values = {
            scheme: float(schemes[scheme][value_field]) for scheme in BASELINES
        }
        if any(value <= 0 or not math.isfinite(value)
               for value in baseline_values.values()):
            raise ValueError(f"non-positive baseline for {nodes}/{scenario}/{metric}")
        best_baseline_scheme = min(baseline_values, key=baseline_values.get)
        best_endpoint = min(float(schemes[item][value_field])
                            for item in ENDPOINT_BASELINES)
        best_network = min(float(schemes[item][value_field])
                           for item in NETWORK_BASELINES)
        best_stateless = min(float(schemes[item][value_field])
                             for item in STATELESS_BASELINES)
        for scheme in SCHEMES:
            value = float(schemes[scheme][value_field])
            if value <= 0 or not math.isfinite(value):
                raise ValueError(
                    f"non-positive value for {nodes}/{scenario}/{scheme}/{metric}"
                )
            output.append({
                "nodes": nodes,
                "scenario": scenario,
                "scheme": scheme,
                "metric": metric,
                "value": value,
                "ratio_to_reps": value / float(schemes["reps"][value_field]),
                "ratio_to_best_endpoint": value / best_endpoint,
                "ratio_to_best_network": value / best_network,
                "ratio_to_best_stateless": value / best_stateless,
                "ratio_to_best_baseline": value / baseline_values[
                    best_baseline_scheme
                ],
                "best_baseline_scheme": best_baseline_scheme,
            })
    return output


def validate_complete_matrix(rows, scenarios, seeds):
    expected = {
        (scenario, scheme, seed)
        for scenario in scenarios for scheme in SCHEMES for seed in seeds
    }
    observed = {
        (row["scenario"], row["scheme"], int(row["seed"])) for row in rows
    }
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing or unexpected:
        raise ValueError(
            f"missing matrix cells={missing}; unexpected matrix cells={unexpected}"
        )
    missing_queue_samples = sorted(
        (row["scenario"], row["scheme"], int(row["seed"]))
        for row in rows
        if int(row.get("queue_cv_spine_queue_count", 0)) <= 0
    )
    if missing_queue_samples:
        raise ValueError(
            "queue CV sampling is unavailable for matrix cells="
            f"{missing_queue_samples}"
        )


def select_matrix_rows(rows, alltoall, scenarios):
    selected = []
    seen = set()
    for row in rows:
        if bool(row.get("is_alltoall")) != bool(alltoall):
            continue
        if row.get("scenario") not in scenarios or row.get("scheme") not in SCHEMES:
            continue
        expected_cadence = "fixed5" if row["scheme"] in SUBJECTS else "none"
        if row.get("cadence") != expected_cadence:
            continue
        key = (row["scenario"], row["scheme"], int(row["seed"]))
        if key in seen:
            raise ValueError(f"duplicate selected matrix cell: {key}")
        seen.add(key)
        selected.append(row)
    return selected


METRIC_DICTIONARY = (
    ("avg_fct_us", "us", "Mean flow completion time; throughput-efficiency context, not the primary tail rank."),
    ("p50_fct_us", "us", "Median flow completion time; typical-flow cost and healthy-workload guardrail."),
    ("p99_fct_us", "us", "Primary point-to-point tail metric; median across three seed-level aggregate p99 values."),
    ("p999_fct_us", "us", "Deep-tail sensitivity metric; less stable than p99 and interpreted with max and seed range."),
    ("max_fct_us", "us", "Worst completed flow in a run; diagnostic for stragglers, never ranked alone."),
    ("all_to_all_cct_us", "us", "Collective completion time from collective start until the last All-to-All flow finishes."),
    ("nacks_ooo", "packets", "OOO recovery signals; indicates reordering pressure under SP/SACK, not physical loss."),
    ("nacks_trim", "packets", "Trim-triggered recovery signals caused by payload trimming under queue pressure."),
    ("nacks_loss", "packets", "Loss NACKs; must remain distinct from OOO and TRIM feedback."),
    ("rtos", "events", "Timeout recovery events; high-cost transport-stability guardrail."),
    ("retx_ratio", "ratio", "Retransmitted packets divided by new packets; recovery-cost guardrail."),
    ("queue_cv", "ratio", "CV across the per-queue time-average occupancy of spine-to-leaf output queues sampled every 100 us; lower means more even occupancy."),
    ("feedback_bandwidth_mbps", "Mbps", "Estimated feedback payload bytes times eight divided by simulated duration."),
)


def _metric_dictionary(seeds):
    p99_interpretation = (
        f"Primary point-to-point tail metric; seed {seeds[0]} run-level p99."
        if len(seeds) == 1 else
        "Primary point-to-point tail metric; median across seed-level "
        f"aggregate p99 values from seeds {', '.join(map(str, seeds))}."
    )
    return [
        (metric, unit, p99_interpretation if metric == "p99_fct_us" else text)
        for metric, unit, text in METRIC_DICTIONARY
    ]


def _geomean(values):
    values = list(values)
    if not values or any(value <= 0 or not math.isfinite(value) for value in values):
        raise ValueError("geometric mean requires finite positive values")
    return math.exp(math.fsum(math.log(value) for value in values) / len(values))


def _write_csv(path, rows):
    rows = list(rows)
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    fields = list(rows[0])
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _save_figure(fig, output, stem):
    metadata = {"Creator": "csg-htsim communication performance report"}
    fig.savefig(output / f"{stem}.png", dpi=220, bbox_inches="tight",
                facecolor="white", metadata=metadata)
    fig.savefig(output / f"{stem}.pdf", bbox_inches="tight",
                facecolor="white", metadata=metadata)
    plt.close(fig)


def _scenario_label(scenario):
    return (scenario.replace("standard_websearch_proxy_", "websearch_")
            .replace("diagnostic_single_slow_link_permutation_", "slow_link_")
            .replace("_background_target", "")
            .replace("_permutation", "_perm")
            .replace("_mib", "M")
            .replace("pct", "%"))


def _heatmap(rows, column_order, value_field, title, colorbar_label,
             output, stem, figsize):
    lookup = {(row["scheme"], row["scenario"]): float(row[value_field])
              for row in rows}
    matrix = [[lookup[(scheme, scenario)] for scenario in column_order]
              for scheme in SCHEMES]
    values = [value for line in matrix for value in line]
    spread = max(max(values) - 1.0, 1.0 - min(values), 0.05)
    fig, axis = plt.subplots(figsize=figsize, layout="constrained")
    image = axis.imshow(
        matrix, aspect="auto", cmap="RdBu_r",
        vmin=max(0, 1.0 - spread), vmax=1.0 + spread,
    )
    axis.set_xticks(range(len(column_order)))
    axis.set_xticklabels([_scenario_label(item) for item in column_order],
                         rotation=38, ha="right", fontsize=7)
    axis.set_yticks(range(len(SCHEMES)))
    axis.set_yticklabels(SCHEMES)
    axis.set_xlabel("Workload mode")
    axis.set_ylabel("Load-balancing scheme")
    axis.set_title(title)
    for row_index, line in enumerate(matrix):
        for column_index, value in enumerate(line):
            axis.text(column_index, row_index, f"{value:.2f}",
                      ha="center", va="center", fontsize=5.7,
                      color="white" if abs(value - 1) > spread * 0.44 else "#111111")
    bar = fig.colorbar(image, ax=axis, pad=0.015)
    bar.set_label(colorbar_label)
    _save_figure(fig, output, stem)


def _family_metric_rows(normalized):
    groups = defaultdict(list)
    for row in normalized:
        groups[(row["metric"], workload_family(row["scenario"]),
                row["scheme"])].append(float(row["ratio_to_best_baseline"]))
    return [{
        "metric": metric,
        "family": family,
        "scheme": scheme,
        "ratio": _geomean(values),
        "cell_count": len(values),
    } for (metric, family, scheme), values in sorted(groups.items())]


def _seed_export_rows(rows):
    fields = (
        "nodes", "scenario", "scheme", "cadence", "seed",
    ) + AGGREGATE_METRICS
    return [{field: row.get(field, 0) for field in fields} for row in rows]


def _alltoall_mode_rows(normalized):
    groups = defaultdict(list)
    for row in normalized:
        mode = parse_alltoall_mode(row["scenario"])
        groups[(
            row["scheme"], mode["message_size_mib"], mode["background"]
        )].append(float(row["ratio_to_best_baseline"]))
    return [{
        "scheme": scheme,
        "message_size_mib": size,
        "background": background,
        "cct_ratio_to_best_baseline_geomean": _geomean(values),
        "parallel_mode_count": len(values),
    } for (scheme, size, background), values in sorted(groups.items())]


def _feedback_overhead_rows(point_rows, alltoall_rows):
    groups = defaultdict(list)
    for family, rows in (("point_to_point", point_rows),
                         ("alltoall", alltoall_rows)):
        for row in rows:
            if row["scheme"] in SUBJECTS:
                groups[(family, row["scheme"])].append(
                    float(row["feedback_bandwidth_mbps"])
                )
    return [{
        "communication_mode": family,
        "scheme": scheme,
        "feedback_bandwidth_mbps_median": statistics.median(values),
        "feedback_bandwidth_mbps_min": min(values),
        "feedback_bandwidth_mbps_max": max(values),
        "seed_cell_count": len(values),
    } for (family, scheme), values in sorted(groups.items())]


def _plot_tail_depth(rows, output):
    metrics = ("p99_fct_us", "p999_fct_us", "max_fct_us")
    families = sorted({row["family"] for row in rows})
    lookup = {(row["metric"], row["family"], row["scheme"]): row["ratio"]
              for row in rows}
    fig, axes = plt.subplots(
        1, 3, figsize=(16, 5.2), sharey=True, layout="constrained"
    )
    all_values = list(lookup.values())
    spread = max(max(all_values) - 1, 1 - min(all_values), 0.05)
    for axis, metric in zip(axes, metrics):
        matrix = [[lookup[(metric, family, scheme)] for family in families]
                  for scheme in SCHEMES]
        image = axis.imshow(matrix, aspect="auto", cmap="RdBu_r",
                            vmin=max(0, 1 - spread), vmax=1 + spread)
        axis.set_xticks(range(len(families)))
        axis.set_xticklabels(families, rotation=40, ha="right", fontsize=7)
        axis.set_yticks(range(len(SCHEMES)))
        axis.set_yticklabels(SCHEMES)
        axis.set_title(metric.replace("_fct_us", "").replace("p999", "p99.9"))
        axis.set_xlabel("Point-to-point workload family")
    axes[0].set_ylabel("Load-balancing scheme")
    bar = fig.colorbar(image, ax=axes, pad=0.015, shrink=0.82)
    bar.set_label("Geometric-mean ratio to best baseline in each cell")
    fig.suptitle("Point-to-point tail depth by workload family")
    _save_figure(fig, output, "p2p_tail_depth")


def _plot_recovery(point_summary, output):
    groups = defaultdict(list)
    for row in point_summary:
        cost = float(row["nacks_median"]) + float(row["retx_packets_median"])
        groups[(workload_family(row["scenario"]), row["scheme"])].append(cost + 1)
    families = sorted({key[0] for key in groups})
    matrix = [[math.log10(_geomean(groups[(family, scheme)]))
               for family in families] for scheme in SCHEMES]
    fig, axis = plt.subplots(figsize=(10.5, 5.5), layout="constrained")
    image = axis.imshow(matrix, aspect="auto", cmap="YlOrBr")
    axis.set_xticks(range(len(families)))
    axis.set_xticklabels(families, rotation=38, ha="right", fontsize=8)
    axis.set_yticks(range(len(SCHEMES)))
    axis.set_yticklabels(SCHEMES)
    axis.set_xlabel("Point-to-point workload family")
    axis.set_ylabel("Load-balancing scheme")
    axis.set_title("Recovery cost by workload family")
    bar = fig.colorbar(image, ax=axis, pad=0.015)
    bar.set_label("log10 geometric mean of (NACKs + retransmitted packets + 1)")
    _save_figure(fig, output, "recovery_cost")


def _plot_feedback(point_rows, alltoall_rows, output):
    point = defaultdict(list)
    collective = defaultdict(list)
    for row in point_rows:
        if row["scheme"] in SUBJECTS:
            point[row["scheme"]].append(float(row["feedback_bandwidth_mbps"]))
    for row in alltoall_rows:
        if row["scheme"] in SUBJECTS:
            collective[row["scheme"]].append(float(row["feedback_bandwidth_mbps"]))
    x = list(range(len(SUBJECTS)))
    width = 0.34
    fig, axis = plt.subplots(figsize=(7.2, 4.6), layout="constrained")
    axis.bar([item - width / 2 for item in x],
             [statistics.median(point[item]) for item in SUBJECTS], width,
             label="point-to-point", color="#4477AA", edgecolor="#222222")
    axis.bar([item + width / 2 for item in x],
             [statistics.median(collective[item]) for item in SUBJECTS], width,
             label="All-to-All", color="#EE6677", edgecolor="#222222")
    axis.set_xticks(x)
    axis.set_xticklabels(SUBJECTS)
    axis.set_ylabel("Estimated feedback bandwidth (Mbps)")
    axis.set_xlabel("State-feedback scheme")
    axis.set_title("Feedback overhead by communication mode")
    axis.grid(True, axis="y", alpha=0.25)
    axis.legend(frameon=False)
    _save_figure(fig, output, "feedback_overhead")


def _scenario_rows(summary, normalized, metric):
    normalized_lookup = {
        (row["scenario"], row["scheme"]): row
        for row in normalized if row["metric"] == metric
    }
    groups = defaultdict(list)
    order = {scheme: index for index, scheme in enumerate(SCHEMES)}
    for row in summary:
        result = dict(row)
        ratios = normalized_lookup[(row["scenario"], row["scheme"])]
        for field in (
            "ratio_to_reps", "ratio_to_best_endpoint",
            "ratio_to_best_network", "ratio_to_best_stateless",
            "ratio_to_best_baseline", "best_baseline_scheme",
        ):
            result[field] = ratios[field]
        groups[row["scenario"]].append(result)
    for scenario in groups:
        groups[scenario].sort(key=lambda row: order[row["scheme"]])
    return groups


def _per_thousand(numerator, denominator):
    return 1000.0 * float(numerator) / max(float(denominator), 1.0)


def _plot_scenario_comparison(scenario, rows, collective, output):
    labels = [row["scheme"] for row in rows]
    x = list(range(len(rows)))
    width = 0.32
    fig, axes = plt.subplots(2, 2, figsize=(15.5, 9.2), layout="constrained")

    latency = axes[0][0]
    latency.bar(
        [item - width / 2 for item in x],
        [float(row["p99_fct_us_median"]) for row in rows], width,
        label="p99", color="#4477AA", edgecolor="#222222",
    )
    latency.bar(
        [item + width / 2 for item in x],
        [float(row["p999_fct_us_median"]) for row in rows], width,
        label="p99.9", color="#EE6677", edgecolor="#222222",
    )
    latency.plot(
        x, [float(row["max_fct_us_median"]) for row in rows],
        marker="D", color="#228833", linewidth=1.3, label="max",
    )
    latency.set_yscale("log")
    latency.set_ylabel("Flow completion time (us, log scale)")
    latency.set_title("Flow-latency tail")
    latency.grid(True, axis="y", alpha=0.25)
    latency.legend(frameon=False, ncol=3, fontsize=8)

    recovery = axes[0][1]
    bottoms = [0.0] * len(rows)
    for field, label, color in (
        ("nacks_ooo_median", "OOO NACK", "#66CCEE"),
        ("nacks_trim_median", "TRIM NACK", "#CCBB44"),
        ("nacks_loss_median", "LOSS NACK", "#AA3377"),
    ):
        values = [
            _per_thousand(row[field], row["new_packets_median"])
            for row in rows
        ]
        recovery.bar(x, values, bottom=bottoms, label=label, color=color,
                     edgecolor="#222222")
        bottoms = [left + value for left, value in zip(bottoms, values)]
    recovery.plot(
        x, [_per_thousand(row["retx_packets_median"],
                          row["new_packets_median"]) for row in rows],
        marker="o", color="#332288", label="retx packets",
    )
    recovery.plot(
        x, [_per_thousand(row["rtos_median"],
                          row["target_flows_median"]) for row in rows],
        marker="x", color="#CC3311", label="RTO / flows",
    )
    recovery.set_ylabel("Events per 1,000 packets (RTO per 1,000 flows)")
    recovery.set_title("Transport recovery")
    recovery.grid(True, axis="y", alpha=0.25)
    recovery.legend(frameon=False, ncol=2, fontsize=7.5)

    queue = axes[1][0]
    queue_bottoms = [0.0] * len(rows)
    for field, label, color in (
        ("composite_ecn_marks_median", "ECN", "#44AA99"),
        ("composite_trims_median", "TRIM", "#DDCC77"),
        ("composite_drops_median", "drop", "#CC6677"),
    ):
        values = [
            _per_thousand(row[field], row["new_packets_median"])
            for row in rows
        ]
        queue.bar(x, values, bottom=queue_bottoms, label=label, color=color,
                  edgecolor="#222222")
        queue_bottoms = [left + value for left, value in zip(queue_bottoms, values)]
    queue_cv = queue.twinx()
    queue_cv.plot(
        x, [float(row["queue_cv_median"]) for row in rows],
        marker="s", color="#117733", label="queue CV",
    )
    queue.set_ylabel("Queue signals per 1,000 new packets")
    queue_cv.set_ylabel("Queue CV")
    queue.set_title("Queue pressure and balance")
    queue.grid(True, axis="y", alpha=0.25)
    queue.legend(frameon=False, loc="upper left", fontsize=7.5)
    queue_cv.legend(frameon=False, loc="upper right", fontsize=7.5)

    outcome = axes[1][1]
    ratios = [float(row["ratio_to_best_baseline"]) for row in rows]
    outcome.bar(x, ratios, color="#88CCEE", edgecolor="#222222")
    outcome.axhline(1.0, color="#222222", linestyle="--", linewidth=1)
    feedback = outcome.twinx()
    feedback.plot(
        x, [float(row["feedback_bandwidth_mbps_median"]) for row in rows],
        marker="o", color="#AA4499", label="feedback bandwidth",
    )
    primary = "CCT" if collective else "p99 FCT"
    outcome.set_ylabel(f"{primary} / best baseline")
    feedback.set_ylabel("Feedback bandwidth (Mbps)")
    outcome.set_title("Primary outcome and feedback cost")
    outcome.grid(True, axis="y", alpha=0.25)
    feedback.legend(frameon=False, loc="upper right", fontsize=7.5)

    for axis in axes.flat:
        axis.set_xticks(x)
        axis.set_xticklabels(labels, rotation=38, ha="right", fontsize=8)
        axis.set_xlabel("Load-balancing scheme")
    mode = "All-to-All" if collective else "Point-to-point"
    fig.suptitle(f"{mode}: {scenario}", fontsize=13)
    prefix = "alltoall" if collective else "p2p"
    _save_figure(fig, output, f"{prefix}_{scenario}")


def _classification(ratio):
    if ratio < 0.97:
        return "better"
    if ratio <= 1.03:
        return "near"
    return "slower"


def _scenario_analysis_section(scenario, rows, collective):
    primary_field = (
        "all_to_all_cct_us_median" if collective else "p99_fct_us_median"
    )
    primary_label = "CCT" if collective else "p99 FCT"
    best = min(rows, key=lambda row: float(row[primary_field]))
    baselines = [row for row in rows if row["scheme"] in BASELINES]
    best_baseline = min(baselines, key=lambda row: float(row[primary_field]))
    deep_tail = min(rows, key=lambda row: float(row["p999_fct_us_median"]))
    recovery = min(
        rows,
        key=lambda row: (
            float(row["retx_ratio_median"]), float(row["rtos_median"]),
            float(row["nacks_median"]),
        ),
    )
    queue = min(rows, key=lambda row: float(row["queue_cv_median"]))
    subject_rows = []
    for scheme in SUBJECTS:
        row = next(item for item in rows if item["scheme"] == scheme)
        ratio_reps = float(row["ratio_to_reps"])
        ratio_best = float(row["ratio_to_best_baseline"])
        subject_rows.append((
            scheme, f"{float(row[primary_field]):.3f}",
            f"{ratio_reps:.3f}x ({_classification(ratio_reps)})",
            f"{ratio_best:.3f}x ({_classification(ratio_best)})",
            f"{float(row['retx_ratio_median']):.6f}",
            f"{float(row['queue_cv_median']):.3f}",
            f"{float(row['feedback_bandwidth_mbps_median']):.3f}",
        ))
    table = _markdown_table(
        ("Scheme", primary_label, "/ REPS", "/ best baseline",
         "retx ratio", "queue CV", "feedback Mbps"),
        subject_rows,
    )
    return f"""## {scenario}

- **Best overall:** `{best['scheme']}` at `{float(best[primary_field]):.3f} us`.
- **Best baseline:** `{best_baseline['scheme']}` at `{float(best_baseline[primary_field]):.3f} us`.
- **Deep tail:** `{deep_tail['scheme']}` has the lowest p99.9 FCT at `{float(deep_tail['p999_fct_us_median']):.3f} us`.
- **Recovery:** `{recovery['scheme']}` has the lowest lexicographic retx/RTO/NACK cost (`retx_ratio={float(recovery['retx_ratio_median']):.6f}`, `RTO={float(recovery['rtos_median']):.0f}`, `NACK={float(recovery['nacks_median']):.0f}`).
- **Queue balance:** `{queue['scheme']}` has the lowest valid time-average queue CV (`{float(queue['queue_cv_median']):.3f}`).

{table}
"""


def _write_per_scenario_artifacts(point_summary, point_normalized,
                                  alltoall_summary, alltoall_normalized,
                                  output, seeds):
    figure_dir = output / "scenario_figures"
    data_dir = output / "scenario_data"
    figure_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    point_groups = _scenario_rows(
        point_summary, point_normalized, "p99_fct_us"
    )
    collective_groups = _scenario_rows(
        alltoall_summary, alltoall_normalized, "all_to_all_cct_us"
    )
    sections = [
        "# Per-Scenario Communication Analysis",
        "",
        (f"This is a seed {seeds[0]} single-seed preview; directional ratios "
         "are descriptive and not a robustness result."
         if len(seeds) == 1 else
         f"Each cell aggregates seeds {', '.join(map(str, seeds))}."),
        "",
        "Ratios below `0.97` are labelled better, `0.97-1.03` near, and above "
        "`1.03` slower. These labels are descriptive, not significance tests.",
        "",
    ]
    for collective, groups in ((False, point_groups), (True, collective_groups)):
        sections.extend((
            "# All-to-All Modes" if collective else "# Point-to-Point Modes",
            "",
        ))
        prefix = "alltoall" if collective else "p2p"
        for scenario, rows in sorted(groups.items()):
            _write_csv(data_dir / f"{prefix}_{scenario}.csv", rows)
            _plot_scenario_comparison(
                scenario, rows, collective, figure_dir
            )
            sections.append(_scenario_analysis_section(
                scenario, rows, collective
            ))
    (output / "scenario_analysis.md").write_text(
        "\n".join(sections), encoding="utf-8"
    )


def _subject_findings(normalized, metric):
    rows = [row for row in normalized if row["metric"] == metric]
    findings = []
    for scheme in SUBJECTS:
        selected = [row for row in rows if row["scheme"] == scheme]
        ratios_reps = [float(row["ratio_to_reps"]) for row in selected]
        ratios_best = [float(row["ratio_to_best_baseline"]) for row in selected]
        findings.append({
            "scheme": scheme,
            "cells": len(selected),
            "geomean_to_reps": _geomean(ratios_reps),
            "geomean_to_best_baseline": _geomean(ratios_best),
            "improved_vs_best": sum(value < 0.97 for value in ratios_best),
            "near_best": sum(value <= 1.03 for value in ratios_best),
            "regressed_vs_best": sum(value > 1.03 for value in ratios_best),
        })
    return findings


def _markdown_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(map(str, row)) + " |" for row in rows)
    return "\n".join(lines)


def _build_report(point_summary, point_normalized, alltoall_summary,
                  alltoall_normalized, seeds):
    point_findings = _subject_findings(point_normalized, "p99_fct_us")
    collective_findings = _subject_findings(
        alltoall_normalized, "all_to_all_cct_us"
    )
    display_names = {"avail": "Avail", "grade": "Grade", "netaware": "n-MRC"}
    point_reps_summary = ", ".join(
        f"{display_names[row['scheme']]} `{row['geomean_to_reps']:.3f}x`"
        for row in point_findings
    )
    collective_best_summary = ", ".join(
        f"{display_names[row['scheme']]} "
        f"`{row['geomean_to_best_baseline']:.3f}x`"
        for row in collective_findings
    )
    point_table = _markdown_table(
        ("Scheme", "Cells", "Geo p99 / REPS", "Geo p99 / best baseline",
         "<0.97", "<=1.03", ">1.03"),
        [(row["scheme"], row["cells"], f"{row['geomean_to_reps']:.3f}",
          f"{row['geomean_to_best_baseline']:.3f}", row["improved_vs_best"],
          row["near_best"], row["regressed_vs_best"])
         for row in point_findings],
    )
    collective_table = _markdown_table(
        ("Scheme", "Modes", "Geo CCT / REPS", "Geo CCT / best baseline",
         "<0.97", "<=1.03", ">1.03"),
        [(row["scheme"], row["cells"], f"{row['geomean_to_reps']:.3f}",
          f"{row['geomean_to_best_baseline']:.3f}", row["improved_vs_best"],
          row["near_best"], row["regressed_vs_best"])
         for row in collective_findings],
    )
    metric_table = _markdown_table(
        ("Metric", "Unit", "Interpretation"), _metric_dictionary(seeds)
    )
    family_groups = defaultdict(list)
    for row in point_normalized:
        if row["metric"] == "p99_fct_us" and row["scheme"] in SUBJECTS:
            family_groups[(
                row["scheme"], workload_family(row["scenario"])
            )].append(float(row["ratio_to_best_baseline"]))
    family_table = _markdown_table(
        ("Scheme", "Family", "Geo p99 / best baseline", "Cells"),
        [(scheme, family, f"{_geomean(values):.3f}", len(values))
         for (scheme, family), values in sorted(family_groups.items())],
    )
    collective_groups = defaultdict(list)
    for row in alltoall_normalized:
        if row["scheme"] not in SUBJECTS:
            continue
        mode = parse_alltoall_mode(row["scenario"])
        collective_groups[(
            row["scheme"], mode["message_size_mib"], mode["background"]
        )].append(float(row["ratio_to_best_baseline"]))
    collective_mode_table = _markdown_table(
        ("Scheme", "Message Size (MiB)", "Background",
         "Geo CCT / best baseline", "Parallel modes"),
        [(scheme, size, background, f"{_geomean(values):.3f}", len(values))
         for (scheme, size, background), values
         in sorted(collective_groups.items())],
    )
    notable_rows = []
    for scheme in SUBJECTS:
        rows = [row for row in point_normalized
                if row["metric"] == "p99_fct_us" and row["scheme"] == scheme]
        best = min(rows, key=lambda row: row["ratio_to_best_baseline"])
        worst = max(rows, key=lambda row: row["ratio_to_best_baseline"])
        notable_rows.append((
            scheme,
            f"{best['scenario']} ({best['ratio_to_best_baseline']:.3f}x)",
            f"{worst['scenario']} ({worst['ratio_to_best_baseline']:.3f}x)",
        ))
    notable_table = _markdown_table(
        ("Scheme", "Best Mode", "Worst Mode"), notable_rows
    )
    single_seed = len(seeds) == 1
    title = (
        "128-Node Communication Performance: Single-Seed Preview"
        if single_seed else "128-Node Communication Performance Analysis"
    )
    seed_description = (
        f"Each cell contains seed {seeds[0]}; this is a single-seed preview, "
        "not a robustness result."
        if single_seed else
        f"Each cell is the median of seeds {', '.join(map(str, seeds))}; "
        "min/max seed ranges remain in the CSV tables."
    )
    matrix_description = "single-seed matrix" if single_seed else "three-seed matrix"
    return f"""# {title}

## Technical Summary

This report compares ten packet-level load-balancing schemes on a single-plane,
two-tier 128-node Clos. Avail, Grade, and n-MRC use the frozen fixed-5us
feedback cadence. {seed_description} Ratios below one are better. A 3% neutral
band is used for descriptive stability (`0.97` to `1.03`), not as a significance
test.

Point-to-point p99 geometric means versus REPS are {point_reps_summary}.
All-to-All CCT geometric means versus the best baseline are
{collective_best_summary}. The point-to-point result therefore supports an
n-MRC advantage over REPS, but not broad superiority over the strongest
per-scenario baseline. None of the three subject schemes is broadly competitive
with the best baseline on this All-to-All matrix.

## Point-to-Point Results

{point_table}

The primary ranking metric is p99 FCT. p99.9 and max are shown separately to
identify deep-tail or single-straggler failures that a p99-only ranking can hide.
Incast is a receiver-bottleneck guardrail, not evidence of path-awareness gain.

- [Per-scenario p99 heatmap](p2p_p99_heatmap.png)
- [Tail-depth family comparison](p2p_tail_depth.png)
- [Recovery-cost comparison](recovery_cost.png)
- [Per-scenario data interpretation](scenario_analysis.md)
- [Per-scenario four-panel figures](scenario_figures/)

### Workload-Family Analysis

{family_table}

The family table uses equal-cell geometric means. It exposes specialization:
a strong hotspot result cannot conceal a healthy, incast, or WebSearch
regression. Exact per-scenario medians and seed ranges remain in
`point_to_point_summary.csv`.

{notable_table}

## All-to-All Results

{collective_table}

All-to-All is ranked by collective completion time (CCT), not by per-flow p99.
Parallelism, per-rank message size, and periodic background state are separate
mode dimensions and remain explicit in `alltoall_summary.csv`. The reported
message size is the total remote payload sent by each rank; each rank-to-rank
flow carries that amount divided by the 128-rank collective group. Background-on
uses the fixed-link periodic workload at 50% duty cycle.

- [All-to-All CCT heatmap](alltoall_cct_heatmap.png)

### All-to-All Mode Analysis

{collective_mode_table}

Message size and background state are separated before aggregating over
parallelism. This prevents a large-message win from masking a background-on
regression.

## Metric Interpretation

{metric_table}

`p99` is the primary point-to-point tail outcome; `p99.9` and `max` are
sensitivity checks. NACK split, RTO, and retransmission ratio are transport
correctness/stability guardrails and should not be combined into an FCT score.
Queue CV diagnoses balance but does not prove lower completion time. Feedback
bandwidth measures payload volume over simulated duration and does not include a
separate reliable control channel.

- [Feedback overhead](feedback_overhead.png)

## Methodology

All rows are validated against their manifest, exact command, simulator hash,
traffic hash, expected flow count, SP/SACK receive mode, composite queue, and
{matrix_description} before aggregation. Point-to-point ratios use the same
scenario and metric. “Best baseline” is the minimum of ECMP_RR, OPS, REPS, MRC,
SGLB, AR, and DRILL in that cell. Family summaries are equal-cell geometric
means, so each workload mode has equal weight.

## Limitations and Robustness

These are descriptive 128-node simulation results, not confidence intervals or
cross-scale proof. A mode can be near the best baseline overall while still
having an unacceptable p99.9/max or recovery outlier. Global 2048-node
All-to-All would contain about 4.19 million flows and requires a separate
resource gate; silently shrinking its group would not be equivalent.

## Next Steps

1. Repeat the frozen matrix at 512 nodes and compare normalized direction by mode.
2. Use measured 512-node runtime and memory to decide which 2048-node All-to-All cells are feasible.
3. Keep fixed5 explicit in commands; do not retune scoring or weights during scale validation.
"""


def build_artifacts(point_rows, alltoall_rows, output, seeds=SEEDS):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    point_scenarios = {row["scenario"] for row in point_rows}
    collective_scenarios = {row["scenario"] for row in alltoall_rows}
    validate_complete_matrix(point_rows, point_scenarios, set(seeds))
    validate_complete_matrix(alltoall_rows, collective_scenarios, set(seeds))

    point_summary = aggregate_seed_rows(point_rows)
    alltoall_summary = aggregate_seed_rows(alltoall_rows)
    point_normalized = []
    for metric in ("p99_fct_us", "p999_fct_us", "max_fct_us"):
        point_normalized.extend(normalized_scheme_rows(point_summary, metric))
    alltoall_normalized = normalized_scheme_rows(
        alltoall_summary, "all_to_all_cct_us"
    )
    family_rows = _family_metric_rows(point_normalized)
    alltoall_mode_rows = _alltoall_mode_rows(alltoall_normalized)
    feedback_rows = _feedback_overhead_rows(point_rows, alltoall_rows)

    _write_csv(output / "point_to_point_summary.csv", point_summary)
    _write_csv(output / "point_to_point_normalized.csv", point_normalized)
    _write_csv(output / "point_to_point_seed_metrics.csv",
               _seed_export_rows(point_rows))
    _write_csv(output / "point_to_point_family_summary.csv", family_rows)
    _write_csv(output / "alltoall_summary.csv", alltoall_summary)
    _write_csv(output / "alltoall_normalized.csv", alltoall_normalized)
    _write_csv(output / "alltoall_seed_metrics.csv",
               _seed_export_rows(alltoall_rows))
    _write_csv(output / "alltoall_mode_summary.csv", alltoall_mode_rows)
    _write_csv(output / "feedback_overhead_summary.csv", feedback_rows)
    _write_csv(output / "metric_dictionary.csv", [
        {"metric": metric, "unit": unit, "interpretation": interpretation}
        for metric, unit, interpretation in _metric_dictionary(seeds)
    ])

    p99_rows = [row for row in point_normalized if row["metric"] == "p99_fct_us"]
    point_order = sorted(point_scenarios, key=lambda item: (
        workload_family(item), item
    ))
    _heatmap(
        p99_rows, point_order, "ratio_to_best_baseline",
        "Point-to-point p99 FCT by workload mode",
        "Ratio to best baseline in the same cell", output,
        "p2p_p99_heatmap", (18, 6.2),
    )
    _plot_tail_depth(family_rows, output)

    alltoall_order = sorted(collective_scenarios, key=lambda item: (
        parse_alltoall_mode(item)["message_size_mib"],
        parse_alltoall_mode(item)["background"],
        parse_alltoall_mode(item)["parallel"],
    ))
    _heatmap(
        alltoall_normalized, alltoall_order, "ratio_to_best_baseline",
        "All-to-All collective completion time by communication mode",
        "CCT ratio to best baseline in the same cell", output,
        "alltoall_cct_heatmap", (15, 6.2),
    )
    _plot_recovery(point_summary, output)
    _plot_feedback(point_rows, alltoall_rows, output)
    _write_per_scenario_artifacts(
        point_summary, point_normalized,
        alltoall_summary, alltoall_normalized,
        output, seeds,
    )

    report = _build_report(
        point_summary, point_normalized,
        alltoall_summary, alltoall_normalized,
        seeds,
    )
    (output / "communication_performance_report.md").write_text(
        report, encoding="utf-8"
    )
    return {
        "point_summary": point_summary,
        "point_normalized": point_normalized,
        "alltoall_summary": alltoall_summary,
        "alltoall_normalized": alltoall_normalized,
    }


def _load_validation_module():
    path = Path(__file__).with_name("build_feedback_cadence_report.py")
    spec = importlib.util.spec_from_file_location("cadence_validation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validated_rows(path):
    module = _load_validation_module()
    loaded = module.load_rows(Path(path))
    serious = [
        item for item in loaded.rejections
        if not item["reason"].startswith("duplicate_dedup:")
    ]
    if serious:
        reasons = "; ".join(item["reason"] for item in serious[:20])
        raise ValueError(f"validated input contains rejected evidence: {reasons}")
    return list(loaded), loaded.rejections


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--point-input", type=Path, required=True)
    parser.add_argument("--alltoall-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", default=SEEDS, type=_parse_seeds)
    return parser.parse_args(argv)


def _parse_seeds(value):
    if isinstance(value, tuple):
        return value
    try:
        seeds = tuple(dict.fromkeys(
            int(item.strip()) for item in value.split(",") if item.strip()
        ))
    except ValueError as error:
        raise argparse.ArgumentTypeError("--seeds must be comma-separated integers") from error
    if not seeds:
        raise argparse.ArgumentTypeError("--seeds must not be empty")
    return seeds


def main(argv=None):
    args = parse_args(argv)
    point_source, point_audits = _validated_rows(args.point_input)
    collective_source, collective_audits = _validated_rows(args.alltoall_input)
    point_rows = select_matrix_rows(
        point_source, alltoall=False, scenarios=set(POINT_TO_POINT_SCENARIOS)
    )
    alltoall_rows = select_matrix_rows(
        collective_source, alltoall=True, scenarios=set(ALLTOALL_SCENARIOS)
    )
    point_rows = [row for row in point_rows if int(row["seed"]) in args.seeds]
    alltoall_rows = [
        row for row in alltoall_rows if int(row["seed"]) in args.seeds
    ]
    build_artifacts(
        point_rows, alltoall_rows, args.output, seeds=args.seeds
    )
    _write_csv(args.output / "source_audit.csv", [{
        "point_input": str(args.point_input.resolve()),
        "alltoall_input": str(args.alltoall_input.resolve()),
        "point_rows": len(point_rows),
        "alltoall_rows": len(alltoall_rows),
        "point_duplicate_dedup_audits": len(point_audits),
        "alltoall_duplicate_dedup_audits": len(collective_audits),
        "seeds": ",".join(map(str, args.seeds)),
        "schemes": ",".join(SCHEMES),
    }])
    print(args.output / "communication_performance_report.md")


if __name__ == "__main__":
    main()
