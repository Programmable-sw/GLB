#!/usr/bin/env python3
"""Compare legacy SGLB with nested 4/8-level n-MRC score quantization."""

import argparse
import concurrent.futures
import csv
import math
from pathlib import Path
import shlex
import subprocess
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_exact_bounded_representative_schemes as exact_runner  # noqa: E402
import experiment_metrics as metrics  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = (
    ROOT / "experiments/n-mrc/output/sglb_nmrc_quantized_topk_128_seed13"
)
SCENARIOS = exact_runner.SCENARIOS
P2P_SCENARIOS = exact_runner.P2P_SCENARIOS
VARIANTS = {
    "legacy_topk": ("legacy", 4),
    "noisy_or_topk4": ("nmrc_quantized_topk", 4),
    "noisy_or_topk8": ("nmrc_quantized_topk", 8),
}
SGLB_FIELDS = (
    "route_calls", "avg_available_choices", "avg_candidate_choices",
    "avg_best_quality_choices", "avg_distinct_qualities",
    "all_same_quality_calls", "all_zero_quality_calls",
    "selected_nonbest_quality", "avg_score_spread",
    "observed_good", "observed_degraded", "observed_bad", "observed_avoid",
    "selected_good", "selected_degraded", "selected_bad", "selected_avoid",
    "remote_snapshot_used", "remote_snapshot_missing",
)
QUEUE_CV_FIELDS = ("spine_queue_cv", "spine_queue_avg", "spine_queue_count")


def write_csv(path, rows, delimiter=","):
    rows = list(rows)
    if not rows:
        raise ValueError(f"no rows for {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]),
                                delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def make_specs(args):
    specs = []
    for scenario in SCENARIOS:
        for variant, (score_mode, nmrc_levels) in VARIANTS.items():
            case_dir = args.out / "raw" / scenario / variant
            argv = exact_runner.source_command(scenario, "sglb")
            argv[0] = str(args.sim)
            exact_runner.set_option(argv, "-o", case_dir / "logout.dat")
            exact_runner.set_option(argv, "-cc", "dcqcn_variant")
            exact_runner.set_option(
                argv, "-roce_transport_semantics", "mrc_exact_bounded")
            exact_runner.set_option(argv, "-roce_trim_recovery", "exact")
            exact_runner.set_option(argv, "-sglb_update_us", "1")
            exact_runner.set_option(argv, "-sglb_gcn_update_us", "5")
            exact_runner.set_option(argv, "-sglb_quality_bucket", "20")
            exact_runner.set_option(argv, "-sglb_min_choices", "3")
            exact_runner.strip_option(argv, "-sglb_score_mode")
            exact_runner.strip_option(argv, "-sglb_nmrc_levels")
            exact_runner.strip_option(argv, "-sglb_nmrc_q_range", 2)
            exact_runner.strip_option(
                argv, "-sglb_nmrc_level_thresholds", 3)
            argv.extend((
                "-sglb_score_mode", score_mode,
                "-sglb_nmrc_levels", str(nmrc_levels),
                "-sglb_nmrc_q_range", "0.2", "0.8",
                "-sglb_nmrc_level_thresholds", "0.1", "0.4", "0.6",
            ))
            specs.append({
                "scenario": scenario,
                "kind": (
                    "point_to_point" if scenario in P2P_SCENARIOS
                    else "all_to_all"
                ),
                "scheme": "sglb",
                "lb_name": "sglb",
                "variant": variant,
                "score_mode": score_mode,
                "nmrc_levels": nmrc_levels,
                "min_choices": 3,
                "cc_mode": "dcqcn_variant",
                "inflate_diag": "disabled",
                "trim_mode": "exact",
                "case_dir": case_dir,
                "command": argv,
                "traffic_file": Path(argv[argv.index("-tm") + 1]),
                "expected_flows": int(argv[argv.index("-conns") + 1]),
                "flows_data": (
                    exact_runner.legacy_runner.point_flows(scenario)
                    if scenario in P2P_SCENARIOS else None
                ),
            })
    return specs


def validate_specs(specs):
    expected = len(SCENARIOS) * len(VARIANTS)
    if len(specs) != expected:
        raise ValueError(f"expected {expected} specs, got {len(specs)}")
    for spec in specs:
        argv = spec["command"]
        required = {
            "-lb": "sglb",
            "-cc": "dcqcn_variant",
            "-roce_rx_mode": "sp",
            "-roce_sack_bitmap_bits": "64",
            "-queue_type": "composite_ecn_lb",
            "-host_queue_type": "prio",
            "-roce_transport_semantics": "mrc_exact_bounded",
            "-roce_trim_recovery": "exact",
            "-sglb_update_us": "1",
            "-sglb_gcn_update_us": "5",
            "-sglb_score_mode": spec["score_mode"],
            "-sglb_nmrc_levels": str(spec["nmrc_levels"]),
            "-sglb_min_choices": "3",
        }
        for flag, value in required.items():
            if flag not in argv or argv[argv.index(flag) + 1] != value:
                raise ValueError(
                    f"{spec['scenario']}:{spec['variant']} missing "
                    f"{flag}={value}")
        q_index = argv.index("-sglb_nmrc_q_range")
        if argv[q_index + 1:q_index + 3] != ["0.2", "0.8"]:
            raise ValueError("unexpected SGLB n-MRC q range")


def run_spec(spec, args):
    row = exact_runner.run_spec(spec, args)
    text = Path(row["stdout"]).read_text(errors="ignore")
    row.update(metrics.parse_key_values(
        text, "SglbRouteDiag", SGLB_FIELDS))
    row.update(metrics.parse_key_values(
        text, "QueueCvDiag", QUEUE_CV_FIELDS))
    row["nmrc_levels"] = spec["nmrc_levels"]
    row["config_ok"] = int(
        row["config_ok"] and
        f"SGLB effective config: score mode {spec['score_mode']}" in text and
        f"nmrc_levels {spec['nmrc_levels']}" in text and
        "local quality update 1us, GCN update 5us" in text and
        "min choices 3" in text)
    return row


def pct_change(new, old):
    return 100.0 * (new / old - 1.0) if old else float("nan")


def geometric_mean(values):
    values = [value for value in values if value > 0]
    return math.exp(sum(math.log(value) for value in values) / len(values))


def build_report(rows, revision):
    lookup = {(row["scenario"], row["variant"]): row for row in rows}
    lines = [
        "# SGLB n-MRC Quantized Top-K Comparison",
        "",
        "128 nodes, seed 13, SP/SACK, Composite ECN LB and "
        "Exact+Bounded. P2P cells use p99 FCT; All-to-All cells use "
        "CCT. Lower is better.",
        "",
        "## Primary Metric (us)",
        "",
        "| Scenario | legacy top-K | noisy-or top-K 4 | noisy-or top-K 8 |",
        "| --- | ---: | ---: | ---: |",
    ]
    ratios = {variant: [] for variant in VARIANTS if variant != "legacy_topk"}
    wins = {variant: 0 for variant in VARIANTS}
    for scenario in SCENARIOS:
        values = {
            variant: lookup[(scenario, variant)]["primary_us"]
            for variant in VARIANTS
        }
        winner = min(values, key=values.get)
        wins[winner] += 1
        for variant in ratios:
            ratios[variant].append(values[variant] / values["legacy_topk"])
        lines.append(
            f"| {scenario} | {values['legacy_topk']:.3f} | "
            f"{values['noisy_or_topk4']:.3f} | "
            f"{values['noisy_or_topk8']:.3f} |")

    lines += [
        "",
        "## Relative to Legacy",
        "",
        "| Scenario | top-K 4 | top-K 8 |",
        "| --- | ---: | ---: |",
    ]
    for scenario in SCENARIOS:
        base = lookup[(scenario, "legacy_topk")]["primary_us"]
        lines.append(
            f"| {scenario} | "
            f"{pct_change(lookup[(scenario, 'noisy_or_topk4')]['primary_us'], base):+.2f}% | "
            f"{pct_change(lookup[(scenario, 'noisy_or_topk8')]['primary_us'], base):+.2f}% |")

    lines += [
        "",
        "## Selection and Recovery Diagnostics",
        "",
        "| Scenario | Variant | candidates | distinct levels | "
        "selected nonbest | RTO | retx% | trims | ECN |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        nonbest = (100.0 * row["selected_nonbest_quality"] /
                   row["route_calls"] if row["route_calls"] else 0.0)
        lines.append(
            f"| {row['scenario']} | {row['variant']} | "
            f"{row['avg_candidate_choices']:.3f} | "
            f"{row['avg_distinct_qualities']:.3f} | {nonbest:.2f}% | "
            f"{int(row['rtos'])} | {100 * row['retx_ratio']:.3f}% | "
            f"{int(row['composite_trims'])} | "
            f"{int(row['composite_ecn_marks'])} |")

    lines += ["", "## Automatic Summary", ""]
    for variant in VARIANTS:
        lines.append(f"- `{variant}` wins `{wins[variant]}/6` cells.")
    for variant, values in ratios.items():
        lines.append(
            f"- `{variant}` geometric-mean latency ratio versus legacy: "
            f"`{geometric_mean(values):.4f}x`.")
    lines += [
        "",
        "Only SGLB scoring and quantization differ. Every mode is explicit "
        "in commands.tsv; n-MRC is unchanged.",
        "",
        f"Source revision: `{revision}`.",
    ]
    return "\n".join(lines) + "\n"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sim", type=Path, default=ROOT / "sim/datacenter/htsim_roce")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    args.sim = args.sim.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    specs = make_specs(args)
    validate_specs(specs)
    write_csv(args.out / "commands.tsv", ({
        "scenario": spec["scenario"],
        "variant": spec["variant"],
        "command": shlex.join(spec["command"]),
    } for spec in specs), delimiter="\t")
    if args.dry_run:
        print(f"validated {len(specs)} commands")
        return

    rows = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(args.workers, len(specs)))) as executor:
        futures = [executor.submit(run_spec, spec, args) for spec in specs]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())
    scenario_order = {name: index for index, name in enumerate(SCENARIOS)}
    variant_order = {name: index for index, name in enumerate(VARIANTS)}
    rows.sort(key=lambda row: (
        scenario_order[row["scenario"]], variant_order[row["variant"]]))
    write_csv(args.out / "summary.csv", rows)

    invalid = [row for row in rows if not (
        row["returncode"] == 0 and row["config_ok"] and
        row["all_flows_completed"] and row["inflight_final"] == 0 and
        row["recovery_inflight_final_bytes"] == 0 and
        row["recovery_inflight_max_bytes"] <= 4096)]
    if invalid:
        raise RuntimeError("invalid runs: " + ", ".join(
            f"{row['scenario']}:{row['variant']}" for row in invalid))
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report = args.out / "sglb_nmrc_quantized_topk_for_gpt.md"
    report.write_text(build_report(rows, revision), encoding="utf-8")
    print(args.out / "summary.csv")
    print(report)


if __name__ == "__main__":
    main()
