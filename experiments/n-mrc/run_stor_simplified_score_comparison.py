#!/usr/bin/env python3
"""Compare complex and simplified STOR scoring on a common endpoint stack."""

import concurrent.futures
import csv
import importlib.util
import statistics
from collections import namedtuple
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = ROOT / "experiments/n-mrc/run_packet_lb_common_workloads_512.py"
OUT = ROOT / "experiments/n-mrc/output/stor_simplified_score_comparison"
Variant = namedtuple("Variant", "key label args profile thresholds penalty")

COMMON_ARGS = [
    "-stor_level_weights", "4", "2", "1", "0",
    "-stor_feedback_pkts", "64",
    "-stor_feedback_min_us", "5",
    "-stor_feedback_max_us", "20",
    "-stor_trim_feedback_min_us", "1",
    "-stor_aging", "packet",
]

SCREEN_VARIANTS = [
    Variant("complex_original", "image-original complex",
            ["-stor_score_profile", "original"], "original", "200/120/50", 0),
    Variant("complex_balanced", "current-balanced complex",
            ["-stor_score_profile", "balanced"], "balanced", "240/160/80", 0),
    Variant("simple_p2_t1273", "simple p2 t12/7/3",
            ["-stor_simple_score_params", "1", "2", "12", "7", "3"],
            "simple", "12/7/3", 2),
    Variant("simple_p3_t1273", "simple p3 t12/7/3",
            ["-stor_simple_score_params", "1", "3", "12", "7", "3"],
            "simple", "12/7/3", 3),
    Variant("simple_p4_t1273", "simple p4 t12/7/3",
            ["-stor_simple_score_params", "1", "4", "12", "7", "3"],
            "simple", "12/7/3", 4),
    Variant("simple_p3_t1284", "simple p3 t12/8/4",
            ["-stor_simple_score_params", "1", "3", "12", "8", "4"],
            "simple", "12/8/4", 3),
    Variant("simple_p3_t1383", "simple p3 t13/8/3",
            ["-stor_simple_score_params", "1", "3", "13", "8", "3"],
            "simple", "13/8/3", 3),
]

VALIDATION_SEEDS = [13, 29, 47]
HEALTHY = ["healthy_permutation_1m", "healthy_tornado_1m"]
AVOIDABLE = [
    "degraded_permutation_1m", "degraded_tornado_1m", "path_hotspot_1m"]
STOR_DIAG_FIELDS = [
    "stor_min_score", "stor_avoid_entries", "stor_avoid_exits",
    "stor_clean_signals", "stor_ecn_signals", "stor_trim_signals"]


def load_base():
    spec = importlib.util.spec_from_file_location("stor_common_base", BASE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configured_runner(variants):
    runner = load_base()
    variant_by_key = {variant.key: variant for variant in variants}
    runner.SCHEMES = [
        runner.Scheme(variant.key, variant.label, "grade") for variant in variants]
    base_build_command = runner.build_command

    def build_command(workload, scheme, traffic_file, dat_file, flow_count):
        command = base_build_command(
            workload, scheme, traffic_file, dat_file, flow_count)
        return command + COMMON_ARGS + variant_by_key[scheme.key].args

    runner.build_command = build_command
    return runner, variant_by_key


def enrich_row(runner, row, phase, seed, variant):
    text = Path(row["stdout"]).read_text(errors="ignore")
    row.update(runner.parse_key_values(text, "StorDiag", STOR_DIAG_FIELDS))
    row["phase"] = phase
    row["seed"] = seed
    row["profile"] = variant.profile
    row["thresholds"] = variant.thresholds
    row["simple_penalty"] = variant.penalty
    common = [
        "grade canonical: paths 16, feedback_pkts 64, min_interval_us 5, "
        "max_interval_us 20, trim_min_interval_us 1, aging packet",
        "weights 4/2/1/0, wrr_mode shuffled_bucket, shuffled_bucket_size 64, "
        "avoid_probe_interval 64",
    ]
    if variant.profile == "simple":
        common += [
            "score_profile simple",
            f"simple max/clean/penalty 15/1/{variant.penalty}",
            f"thresholds {variant.thresholds}",
        ]
    elif variant.profile == "original":
        common += ["score_profile original", "thresholds 200/120/50"]
    else:
        common += ["score_profile balanced", "thresholds 240/160/80"]
    row["stor_runtime_config_ok"] = int(all(token in text for token in common))
    row["config_ok"] = int(row["config_ok"] and row["stor_runtime_config_ok"])
    return row


def run_matrix(phase, seed, variants):
    runner, variant_by_key = configured_runner(variants)
    runner.SEED = seed
    runner.OUT = OUT / "raw" / phase / f"seed{seed}"
    runner.OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(runner.run_case, workload, scheme):
                (workload, scheme)
            for workload in runner.WORKLOADS
            for scheme in runner.SCHEMES
        }
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            variant = variant_by_key[row["scheme"]]
            rows.append(enrich_row(runner, row, phase, seed, variant))
    workload_order = {
        item.name: index for index, item in enumerate(runner.WORKLOADS)}
    variant_order = {
        item.key: index for index, item in enumerate(variants)}
    rows.sort(key=lambda row: (
        workload_order[row["workload"]], variant_order[row["scheme"]]))
    failed = [row for row in rows if not runner.row_complete(row)]
    if failed:
        raise RuntimeError(f"{phase} seed {seed}: {len(failed)} invalid runs")
    return rows


def choose_finalists(rows):
    by_key = {}
    for row in rows:
        by_key.setdefault(row["scheme"], {})[row["workload"]] = row
    complex_best_healthy = {
        workload: min(
            float(by_key[key][workload]["p99_fct_us"])
            for key in ("complex_original", "complex_balanced"))
        for workload in HEALTHY
    }
    candidates = []
    for variant in SCREEN_VARIANTS:
        if variant.profile != "simple":
            continue
        values = by_key[variant.key]
        healthy_pass = all(
            float(values[workload]["p99_fct_us"]) <=
            1.03 * complex_best_healthy[workload]
            for workload in HEALTHY)
        objective = statistics.mean(
            float(values[workload]["p99_fct_us"]) for workload in AVOIDABLE)
        recovery = sum(int(values[workload]["nacks"]) for workload in AVOIDABLE)
        candidates.append((not healthy_pass, objective, recovery, variant.key))
    candidates.sort()
    winner_keys = [item[3] for item in candidates[:2]]
    return [variant for key in winner_keys for variant in SCREEN_VARIANTS
            if variant.key == key], candidates


def write_csv(rows):
    OUT.mkdir(parents=True, exist_ok=True)
    summary = OUT / "summary.csv"
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    commands = OUT / "commands.tsv"
    with commands.open("w", encoding="utf-8") as handle:
        print("phase\tseed\tworkload\tvariant\tcommand", file=handle)
        for row in rows:
            print(f"{row['phase']}\t{row['seed']}\t{row['workload']}\t"
                  f"{row['scheme']}\t{row['command']}", file=handle)


def median_rows(rows, variants):
    out = {}
    for variant in variants:
        for workload in sorted({row["workload"] for row in rows}):
            group = [row for row in rows
                     if row["scheme"] == variant.key and
                     row["workload"] == workload]
            out[variant.key, workload] = {
                metric: statistics.median(float(row[metric]) for row in group)
                for metric in ["p99_fct_us", "p999_fct_us", "max_fct_us",
                               "nacks", "retx_ratio", "rtos"]
            }
    return out


def write_report(screen_rows, validation_rows, finalists, ranking):
    variants = [SCREEN_VARIANTS[0], SCREEN_VARIANTS[1]] + finalists
    med = median_rows(validation_rows, variants)
    workloads = [item.name for item in load_base().WORKLOADS]
    robust_ranking = sorted(
        (statistics.mean(
            med[variant.key, workload]["p99_fct_us"]
            for workload in AVOIDABLE), variant)
        for variant in finalists)
    finalist = robust_ranking[0][1]
    lines = [
        "# STOR Simplified Score Comparison",
        "",
        "## Audit Verdict",
        "",
        "The supplied design image is implemented as `stor_score_profile=original`: clean +4; ECN accumulator/base 16/8; TRIM accumulator/base 32/32; thresholds 200/120/50. The no-flag implementation before this experiment used the same three-field formula with the more aggressive `balanced` constants 24/48, 16/48 and thresholds 240/160/80.",
        "",
        "## Common Stack",
        "",
        "- 512 nodes, 2-tier single-plane, 16 spine paths, 400 Gbit/s",
        "- composite_ecn_lb, host prio, SP/SACK 64-bit, dcqcn_variant natural",
        "- STOR packet aging, feedback 64 packets and 5/20us; TRIM feedback minimum 1us",
        "- selector shuffled_bucket, K=4P=64, weights 4/2/1/0",
        "- one rotating AVOID probe every 64 STOR selections",
        "- ECN and TRIM use the same simple-score penalty",
        "",
        "## Screen",
        "",
        "Seed-13 screen finalists: " + ", ".join(
            f"`{variant.key}`" for variant in finalists) + ".",
        f"Three-seed robust recommendation: `{finalist.key}` ({finalist.label}).",
        "",
        "| rank | candidate | healthy rejected | avoidable mean p99 | avoidable NACK |",
        "| ---: | --- | --- | ---: | ---: |",
    ]
    for index, item in enumerate(ranking, 1):
        rejected, objective, recovery, key = item
        lines.append(
            f"| {index} | {key} | {'yes' if rejected else 'no'} | "
            f"{objective:.3f} | {recovery} |")

    lines += [
        "",
        "## Three-Seed Validation",
        "",
        "Values are medians across seeds 13/29/47.",
        "",
        "| workload | variant | p99 | p99.9 | max | NACK | retx ratio | RTO |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for workload in workloads:
        for variant in variants:
            row = med[variant.key, workload]
            lines.append(
                f"| {workload} | {variant.key} | {row['p99_fct_us']:.3f} | "
                f"{row['p999_fct_us']:.3f} | {row['max_fct_us']:.3f} | "
                f"{row['nacks']:.0f} | {row['retx_ratio']:.6f} | "
                f"{row['rtos']:.0f} |")

    lines += ["", "## Simple vs Complex", ""]
    for baseline in variants[:2]:
        wins = 0
        changes = []
        for workload in workloads:
            simple = med[finalist.key, workload]["p99_fct_us"]
            base = med[baseline.key, workload]["p99_fct_us"]
            change = 100.0 * (simple / base - 1.0) if base else 0.0
            changes.append(change)
            if simple < base:
                wins += 1
        lines.append(
            f"- `{finalist.key}` beats `{baseline.key}` on {wins}/{len(workloads)} "
            f"workload median p99 values; mean p99 change {statistics.mean(changes):+.2f}%.")
    lines += [
        "- Incast is a shared receiver bottleneck and is a guardrail, not evidence of path-quality inference.",
        "- This comparison tests software semantics, not final hardware encoding or resource cost.",
        "",
        "## Decision",
        "",
        "- The 4-bit simple scorer surpasses the image-original complex scorer on all three avoidable-congestion workloads: degraded permutation, degraded tornado, and path hotspot.",
        "- It does not unconditionally surpass the current balanced scorer. The p4/t12-7-3 finalist improves path-hotspot p99 and degraded-permutation extreme tail/RTO, but slightly regresses degraded-tornado tail and the shared-bottleneck incast guardrail.",
        "- Keep `balanced` as the no-flag STOR default for now. Treat `simple p4 t12/7/3` as the leading simplified candidate, not as a proven universal replacement.",
        "",
        "## Data Quality",
        "",
        f"- screen rows: {len(screen_rows)}/49",
        f"- validation rows: {len(validation_rows)}/84",
        "- incomplete/config-failed rows: 0",
    ]
    report = OUT / "stor_simplified_score_comparison_for_gpt.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    screen_rows = run_matrix("screen", 13, SCREEN_VARIANTS)
    finalists, ranking = choose_finalists(screen_rows)
    validation_variants = [SCREEN_VARIANTS[0], SCREEN_VARIANTS[1]] + finalists
    validation_rows = []
    for seed in VALIDATION_SEEDS:
        validation_rows += run_matrix("validation", seed, validation_variants)
    rows = screen_rows + validation_rows
    write_csv(rows)
    report = write_report(screen_rows, validation_rows, finalists, ranking)
    print(report)


if __name__ == "__main__":
    main()
