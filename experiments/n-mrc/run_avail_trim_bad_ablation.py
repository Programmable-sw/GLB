#!/usr/bin/env python3
"""Compare ECN-only Avail with ECN+TRIM Avail on common workloads."""

import concurrent.futures
import csv
import importlib.util
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "experiments/n-mrc/run_packet_lb_common_workloads_512.py"
OUT = ROOT / "experiments/n-mrc/output/avail_trim_bad_ablation_512"
SEEDS = [13, 29, 47]
VARIANTS = [
    ("avail_ecn_only", "Avail ECN-only", ["-avail_ecn_only"]),
    ("avail_ecn_trim", "Avail ECN+TRIM", []),
]


def load_runner():
    spec = importlib.util.spec_from_file_location("avail_trim_base", BASE)
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
            signal = ("ecn_trim" if row["scheme"] == "avail_ecn_trim"
                      else "ecn_only")
            row["avail_runtime_ok"] = int(
                "avail canonical: paths 16" in text and
                f"binary_bad_signal {signal}" in text and
                "wrr_mode bitmap" in text)
            row["config_ok"] = int(
                row["config_ok"] and row["avail_runtime_ok"])
            rows.append(row)
    return runner, rows


def write_outputs(runner, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    workload_order = {
        workload.name: index for index, workload in enumerate(runner.WORKLOADS)}
    variant_order = {key: index for index, (key, _label, _args)
                     in enumerate(VARIANTS)}
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

    metrics = [
        "avg_fct_us", "p99_fct_us", "p999_fct_us", "max_fct_us",
        "nacks", "nacks_ooo", "nacks_trim", "nacks_loss", "rtos",
        "retx_packets", "retx_ratio", "composite_trims",
        "composite_drops", "composite_ecn_marks",
    ]
    medians = {}
    for workload in runner.WORKLOADS:
        for key, _label, _args in VARIANTS:
            group = [row for row in rows
                     if row["workload"] == workload.name and
                     row["scheme"] == key]
            medians[workload.name, key] = {
                metric: statistics.median(float(row[metric]) for row in group)
                for metric in metrics
            }

    lines = [
        "# Avail TRIM-as-Bad Ablation",
        "",
        "## Setup",
        "",
        "- 512 nodes, 2-tier single-plane Clos, 16 paths, seeds 13/29/47",
        "- composite_ecn_lb, host prio, SP/SACK64, dcqcn_variant natural",
        "- Avail feedback 32 packets, 5/20us, shared ToR-pair bitmap",
        "- only changed semantic: whether TRIM marks the exact path bad",
        "",
        "## Three-Seed Median Results",
        "",
        "| workload | variant | avg | p99 | p99.9 | max | NACK O/T/L | RTO | retx ratio | queue trim/drop/ECN |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for workload in runner.WORKLOADS:
        for key, label, _args in VARIANTS:
            row = medians[workload.name, key]
            lines.append(
                f"| {workload.name} | {label} | {row['avg_fct_us']:.3f} | "
                f"{row['p99_fct_us']:.3f} | {row['p999_fct_us']:.3f} | "
                f"{row['max_fct_us']:.3f} | {row['nacks_ooo']:.0f}/"
                f"{row['nacks_trim']:.0f}/{row['nacks_loss']:.0f} | "
                f"{row['rtos']:.0f} | {row['retx_ratio']:.6f} | "
                f"{row['composite_trims']:.0f}/{row['composite_drops']:.0f}/"
                f"{row['composite_ecn_marks']:.0f} |")

    lines += [
        "",
        "## ECN+TRIM Relative to ECN-only",
        "",
        "| workload | p99 change | p99.9 change | max change | NACK change | retx change |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for workload in runner.WORKLOADS:
        base = medians[workload.name, "avail_ecn_only"]
        trim = medians[workload.name, "avail_ecn_trim"]

        def change(field):
            old = base[field]
            return 100.0 * (trim[field] / old - 1.0) if old else 0.0

        lines.append(
            f"| {workload.name} | {change('p99_fct_us'):+.2f}% | "
            f"{change('p999_fct_us'):+.2f}% | {change('max_fct_us'):+.2f}% | "
            f"{change('nacks'):+.2f}% | {change('retx_packets'):+.2f}% |")

    invalid = [row for row in rows if not runner.row_complete(row) or
               not row["avail_runtime_ok"]]
    lines += [
        "",
        "## Data Quality",
        "",
        f"- rows: {len(rows)}/42",
        f"- incomplete or config-invalid rows: {len(invalid)}",
        "- incast is an unavoidable receiver-downlink bottleneck and is a guardrail, not path-quality evidence.",
    ]
    report = OUT / "avail_trim_bad_ablation_for_gpt.md"
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
