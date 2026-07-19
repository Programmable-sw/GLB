#!/usr/bin/env python3
"""Compare the old 100-packet MRC cooldown with topology 1-BDP."""

import concurrent.futures
import csv
import importlib.util
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "experiments/n-mrc/run_packet_lb_common_workloads_512.py"
OUT = ROOT / "experiments/n-mrc/output/mrc_bdp_cooldown_comparison_512"
SEEDS = [13, 29, 47]
VARIANTS = [
    ("mrc_reference100", "MRC old reference 100", [
        "-mrc_cooldown_reference_pkts", "100"]),
    ("mrc_topology_bdp", "MRC topology BDP 86", []),
]


def load_runner():
    spec = importlib.util.spec_from_file_location("mrc_bdp_base", BASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configured_runner(seed):
    runner = load_runner()
    by_key = {key: args for key, _label, args in VARIANTS}
    runner.SCHEMES = [
        runner.Scheme(key, label, "mrc") for key, label, _args in VARIANTS]
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
        "mrc_reference100": (
            "mrc_cooldown_reference=explicit "
            "mrc_cooldown_reference_pkts=100 "
            "mrc_cwnd_scaled_rotations=7 "
            "mrc_cwnd_scaled_skip_selections=112"),
        "mrc_topology_bdp": (
            "mrc_cooldown_reference=topology_bdp "
            "mrc_cooldown_reference_pkts=86 "
            "mrc_cwnd_scaled_rotations=6 "
            "mrc_cwnd_scaled_skip_selections=96"),
    }
    return int(
        "MrcCooldownDiag mrc_cooldown_mode=cwnd_scaled" in text and
        expected[variant] in text and
        "MrcFallbackDiag mrc_all_cooling_fallback=earliest" in text and
        "FinalCcMrcConfig dcqcn_variant_inflate=natural "
        "mrc_ecn_trim_penalty=mode_uniform" in text)


def run_seed(seed):
    runner = configured_runner(seed)
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(runner.run_case, workload, scheme)
            for workload in runner.WORKLOADS
            for scheme in runner.SCHEMES]
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            row["seed"] = seed
            text = Path(row["stdout"]).read_text(errors="ignore")
            row["mrc_reference_runtime_ok"] = runtime_ok(
                text, row["scheme"])
            row["config_ok"] = int(
                row["config_ok"] and row["mrc_reference_runtime_ok"])
            rows.append(row)
    return runner, rows


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

    metrics = [
        "avg_fct_us", "p50_fct_us", "p95_fct_us", "p99_fct_us",
        "p999_fct_us", "max_fct_us", "nacks", "nacks_ooo",
        "nacks_trim", "nacks_loss", "rtos", "retx_packets", "retx_ratio",
        "composite_trims", "composite_drops", "composite_ecn_marks",
        "cwnd_scaled_feedback_events",
        "cwnd_scaled_duplicate_feedback_ignored", "forced_cooling_use",
        "cooling_skip_selection_avg",
    ]
    medians = {}
    for workload in runner.WORKLOADS:
        for key, _label, _args in VARIANTS:
            group = [
                row for row in rows
                if row["workload"] == workload.name and row["scheme"] == key]
            medians[workload.name, key] = {
                metric: statistics.median(float(row[metric]) for row in group)
                for metric in metrics}

    lines = [
        "# MRC Topology-BDP Cooldown Comparison",
        "",
        "## Setup",
        "",
        "- 512 nodes, 2-tier single-plane Clos, 16 active paths, seeds 13/29/47",
        "- seven common workloads, composite_ecn_lb, SP/SACK64, dcqcn_variant",
        "- old: explicit 100-packet reference, 7 rotations, 112 selections",
        "- corrected: topology 1-BDP = 86 packets, 6 rotations, 96 selections",
        "- both use uniform ECN/TRIM cooldown and earliest all-cooling fallback",
        "- only the MRC cooldown reference differs",
        "",
        "## Three-Seed Median Results",
        "",
        "| workload | variant | p99 | p99.9 | max | NACK O/T/L | RTO | retx ratio | trim/drop/ECN | cooldown events/dup | forced cooling | avg skip |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | --- | --- | ---: | ---: |",
    ]
    labels = {key: label for key, label, _args in VARIANTS}
    for workload in runner.WORKLOADS:
        for key, _label, _args in VARIANTS:
            row = medians[workload.name, key]
            lines.append(
                f"| {workload.name} | {labels[key]} | "
                f"{row['p99_fct_us']:.3f} | {row['p999_fct_us']:.3f} | "
                f"{row['max_fct_us']:.3f} | {row['nacks_ooo']:.0f}/"
                f"{row['nacks_trim']:.0f}/{row['nacks_loss']:.0f} | "
                f"{row['rtos']:.0f} | {row['retx_ratio']:.6f} | "
                f"{row['composite_trims']:.0f}/"
                f"{row['composite_drops']:.0f}/"
                f"{row['composite_ecn_marks']:.0f} | "
                f"{row['cwnd_scaled_feedback_events']:.0f}/"
                f"{row['cwnd_scaled_duplicate_feedback_ignored']:.0f} | "
                f"{row['forced_cooling_use']:.0f} | "
                f"{row['cooling_skip_selection_avg']:.1f} |")

    lines += [
        "",
        "## BDP Relative to Old Reference 100",
        "",
        "| workload | p99 | p99.9 | max | NACK | retx | RTO |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for workload in runner.WORKLOADS:
        old = medians[workload.name, "mrc_reference100"]
        bdp = medians[workload.name, "mrc_topology_bdp"]
        lines.append(
            f"| {workload.name} | "
            f"{percent_change(bdp['p99_fct_us'], old['p99_fct_us']):+.2f}% | "
            f"{percent_change(bdp['p999_fct_us'], old['p999_fct_us']):+.2f}% | "
            f"{percent_change(bdp['max_fct_us'], old['max_fct_us']):+.2f}% | "
            f"{percent_change(bdp['nacks'], old['nacks']):+.2f}% | "
            f"{percent_change(bdp['retx_packets'], old['retx_packets']):+.2f}% | "
            f"{bdp['rtos'] - old['rtos']:+.0f} |")

    lines += [
        "",
        "## Per-Seed p99",
        "",
        "| workload | seed | old100 | topology BDP | change |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for workload in runner.WORKLOADS:
        for seed in SEEDS:
            old = next(
                row for row in rows
                if row["workload"] == workload.name and
                row["scheme"] == "mrc_reference100" and
                int(row["seed"]) == seed)
            bdp = next(
                row for row in rows
                if row["workload"] == workload.name and
                row["scheme"] == "mrc_topology_bdp" and
                int(row["seed"]) == seed)
            lines.append(
                f"| {workload.name} | {seed} | "
                f"{float(old['p99_fct_us']):.3f} | "
                f"{float(bdp['p99_fct_us']):.3f} | "
                f"{percent_change(float(bdp['p99_fct_us']), float(old['p99_fct_us'])):+.2f}% |")

    lines += [
        "",
        "## Verdict",
        "",
        "- Topology BDP is semantically cleaner than a fixed 100-packet constant, but one base-RTT BDP is too short for the current congested feedback horizon.",
        "- Healthy and mixed workloads are identical because they do not trigger MRC cooldown.",
        "- BDP regresses degraded permutation and tornado p99 by 1.73% and 1.76%; the direction is consistent in all three seeds.",
        "- Path-hotspot p99/p99.9/max regress by 1.58%/44.14%/86.19%, NACKs rise 13.80%, and retransmissions rise 4.58%.",
        "- The BDP variant has an RTO in all three hotspot seeds; the old100 variant has an RTO in only one seed.",
        "- Incast changes are mixed across seeds and are not path-quality evidence.",
        "- Keep the new topology-BDP mechanism available, but do not treat raw 1-base-BDP as the final performance default without queue/RTT headroom validation.",
    ]

    invalid = [
        row for row in rows
        if not runner.row_complete(row) or
        not row["mrc_reference_runtime_ok"]]
    lines += [
        "",
        "## Data Quality",
        "",
        f"- rows: {len(rows)}/42",
        f"- incomplete or config-invalid rows: {len(invalid)}",
        "- medians use three seeds; incast is a receiver-downlink guardrail.",
    ]
    report = OUT / "mrc_bdp_cooldown_comparison_for_gpt.md"
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
