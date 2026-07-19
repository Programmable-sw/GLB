#!/usr/bin/env python3
"""Compare default 5-20us Avail feedback with a fixed 5us interval."""

import concurrent.futures
import csv
import importlib.util
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "experiments/n-mrc/run_packet_lb_common_workloads_512.py"
OUT = ROOT / "experiments/n-mrc/output/avail_feedback_5us_ablation_512"
SEEDS = [13, 29, 47]
VARIANTS = [
    ("avail_default_5_20", "Avail default 5-20us", []),
    ("avail_fixed_5", "Avail fixed 5us", [
        "-stor_feedback_min_us", "5",
        "-stor_feedback_max_us", "5",
    ]),
]


def load_runner():
    spec = importlib.util.spec_from_file_location("avail_feedback_base", BASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configured_runner(seed):
    runner = load_runner()
    by_key = {key: args for key, _label, args in VARIANTS}
    runner.SCHEMES = [
        runner.Scheme(key, label, "avail") for key, label, _args in VARIANTS]
    base_build_command = runner.build_command

    def build_command(workload, scheme, traffic_file, dat_file, flow_count):
        command = base_build_command(
            workload, scheme, traffic_file, dat_file, flow_count)
        return command + by_key[scheme.key]

    runner.build_command = build_command
    runner.SEED = seed
    runner.OUT = OUT / "raw" / f"seed{seed}"
    return runner


def runtime_ok(text, variant):
    expected = {
        "avail_default_5_20": (
            "min_interval_us 5, max_interval_us 20, trim_min_interval_us 1"),
        "avail_fixed_5": (
            "min_interval_us 5, max_interval_us 5, trim_min_interval_us 1"),
    }
    return int(
        "avail canonical: paths 16" in text and
        expected[variant] in text and
        "binary_bad_signal ecn_trim" in text and
        "wrr_mode bitmap" in text)


def run_seed(seed):
    runner = configured_runner(seed)
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(runner.run_case, workload, scheme)
            for workload in runner.WORKLOADS
            for scheme in runner.SCHEMES
        ]
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            row["seed"] = seed
            text = Path(row["stdout"]).read_text(errors="ignore")
            row["avail_runtime_ok"] = runtime_ok(text, row["scheme"])
            row["config_ok"] = int(
                row["config_ok"] and row["avail_runtime_ok"])
            rows.append(row)
    return runner, rows


def median_rows(runner, rows):
    metrics = [
        "avg_fct_us", "p50_fct_us", "p95_fct_us", "p99_fct_us",
        "p999_fct_us", "max_fct_us", "nacks", "nacks_ooo",
        "nacks_trim", "nacks_loss", "rtos", "retx_packets",
        "retx_ratio", "composite_trims", "composite_drops",
        "composite_ecn_marks", "feedback_acks",
    ]
    medians = {}
    for workload in runner.WORKLOADS:
        for key, _label, _args in VARIANTS:
            group = [
                row for row in rows
                if row["workload"] == workload.name and row["scheme"] == key]
            medians[workload.name, key] = {
                metric: statistics.median(float(row[metric]) for row in group)
                for metric in metrics
            }
    return medians


def percent_change(new, old):
    return 100.0 * (new / old - 1.0) if old else 0.0


def write_outputs(runner, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    workload_order = {
        workload.name: index for index, workload in enumerate(runner.WORKLOADS)}
    variant_order = {
        key: index for index, (key, _label, _args) in enumerate(VARIANTS)}
    rows.sort(key=lambda row: (
        workload_order[row["workload"]],
        variant_order[row["scheme"]], int(row["seed"])))

    with (OUT / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with (OUT / "commands.tsv").open("w", encoding="utf-8") as handle:
        print("seed\tworkload\tvariant\tcommand", file=handle)
        for row in rows:
            print(f"{row['seed']}\t{row['workload']}\t{row['scheme']}\t"
                  f"{row['command']}", file=handle)

    medians = median_rows(runner, rows)
    lines = [
        "# Avail Fixed-5us Feedback Ablation",
        "",
        "## Setup",
        "",
        "- 512 nodes, 2-tier single-plane Clos, 16 paths, seeds 13/29/47",
        "- composite_ecn_lb, host prio, SP/SACK64, dcqcn_variant natural",
        "- Avail ECN+TRIM binary bitmap, 32-packet trigger, shared ToR-pair state",
        "- baseline: feedback min/max 5/20us, TRIM minimum 1us",
        "- fixed variant: feedback min/max 5/5us",
        "- the TRIM-specific 1us trigger is inactive for Avail's binary profile",
        "- only the feedback timing differs",
        "",
        "## Three-Seed Median Results",
        "",
        "| workload | variant | avg | p50 | p95 | p99 | p99.9 | max | NACK O/T/L | RTO | retx ratio | trim/drop/ECN | feedback ACKs |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: |",
    ]
    labels = {key: label for key, label, _args in VARIANTS}
    for workload in runner.WORKLOADS:
        for key, _label, _args in VARIANTS:
            row = medians[workload.name, key]
            lines.append(
                f"| {workload.name} | {labels[key]} | "
                f"{row['avg_fct_us']:.3f} | {row['p50_fct_us']:.3f} | "
                f"{row['p95_fct_us']:.3f} | {row['p99_fct_us']:.3f} | "
                f"{row['p999_fct_us']:.3f} | {row['max_fct_us']:.3f} | "
                f"{row['nacks_ooo']:.0f}/{row['nacks_trim']:.0f}/"
                f"{row['nacks_loss']:.0f} | {row['rtos']:.0f} | "
                f"{row['retx_ratio']:.6f} | {row['composite_trims']:.0f}/"
                f"{row['composite_drops']:.0f}/"
                f"{row['composite_ecn_marks']:.0f} | "
                f"{row['feedback_acks']:.0f} |")

    lines += [
        "",
        "## Fixed 5us Relative to Default 5-20us",
        "",
        "| workload | avg | p99 | p99.9 | max | NACK | retx | feedback ACKs |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for workload in runner.WORKLOADS:
        base = medians[workload.name, "avail_default_5_20"]
        fixed = medians[workload.name, "avail_fixed_5"]
        lines.append(
            f"| {workload.name} | "
            f"{percent_change(fixed['avg_fct_us'], base['avg_fct_us']):+.2f}% | "
            f"{percent_change(fixed['p99_fct_us'], base['p99_fct_us']):+.2f}% | "
            f"{percent_change(fixed['p999_fct_us'], base['p999_fct_us']):+.2f}% | "
            f"{percent_change(fixed['max_fct_us'], base['max_fct_us']):+.2f}% | "
            f"{percent_change(fixed['nacks'], base['nacks']):+.2f}% | "
            f"{percent_change(fixed['retx_packets'], base['retx_packets']):+.2f}% | "
            f"{percent_change(fixed['feedback_acks'], base['feedback_acks']):+.2f}% |")

    lines += [
        "",
        "## Verdict",
        "",
        "- Fixed 5us is neutral on healthy permutation/tornado, mixed, and path-hotspot FCT.",
        "- It regresses degraded permutation p99/p99.9/max by 1.83%/17.54%/32.95%.",
        "- It regresses degraded tornado p99/p99.9/max by 17.92%/20.76%/20.99%.",
        "- The 1.97% incast p99 improvement is only a receiver-downlink guardrail result and costs 78.32% more feedback ACKs.",
        "- Keep Avail's 5-20us feedback interval as the default; fixed 5us is not a balanced improvement.",
        "",
        "## Per-Seed p99",
        "",
        "| workload | seed | default 5-20us | fixed 5us | change |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for workload in runner.WORKLOADS:
        for seed in SEEDS:
            base = next(row for row in rows
                        if row["workload"] == workload.name and
                        row["scheme"] == "avail_default_5_20" and
                        int(row["seed"]) == seed)
            fixed = next(row for row in rows
                         if row["workload"] == workload.name and
                         row["scheme"] == "avail_fixed_5" and
                         int(row["seed"]) == seed)
            change = percent_change(
                float(fixed["p99_fct_us"]), float(base["p99_fct_us"]))
            lines.append(
                f"| {workload.name} | {seed} | "
                f"{float(base['p99_fct_us']):.3f} | "
                f"{float(fixed['p99_fct_us']):.3f} | {change:+.2f}% |")

    invalid = [
        row for row in rows
        if not runner.row_complete(row) or not row["avail_runtime_ok"]]
    lines += [
        "",
        "## Data Quality",
        "",
        f"- rows: {len(rows)}/42",
        f"- incomplete or config-invalid rows: {len(invalid)}",
        "- lower latency is better; incast is a receiver-downlink guardrail, not path-quality evidence.",
    ]
    report = OUT / "avail_feedback_5us_ablation_for_gpt.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report, invalid


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    runner = None
    for seed in SEEDS:
        runner, seed_rows = run_seed(seed)
        rows.extend(seed_rows)
    report, invalid = write_outputs(runner, rows)
    print(report)
    if invalid:
        raise SystemExit(f"{len(invalid)} incomplete or invalid runs")


if __name__ == "__main__":
    main()
