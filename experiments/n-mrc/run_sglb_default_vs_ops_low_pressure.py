#!/usr/bin/env python3
"""Compare the default SGLB against OPS on healthy low-pressure A2A."""

import argparse
import concurrent.futures
import gzip
import json
import math
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import run_final_512_comparison as base  # noqa: E402


SEEDS = (13, 29, 47)
SCHEMES = ("sglb", "ops")
SCENARIO = base.Scenario(
    "healthy_alltoall_64mib_p4", "healthy_alltoall", "all_to_all",
    "alltoall", size_mib=64, parallel=4)
DEFAULT_SIM = ROOT / "sim/datacenter/htsim_roce"
DEFAULT_OUT = SCRIPT_DIR / "output/sglb_default_vs_ops_low_pressure"


def geometric_mean(values):
    values = list(values)
    return math.exp(sum(math.log(value) for value in values) / len(values))


def make_specs(args):
    args.out = Path(args.out).resolve()
    args.sim = Path(args.sim).resolve()
    artifacts = {}
    specs = []
    for seed in SEEDS:
        artifacts[(SCENARIO.name, seed)] = base.materialize_traffic(
            args.out / "traffic", SCENARIO, seed, 256)
        specs.extend(base.CaseSpec(SCENARIO, scheme, seed) for scheme in SCHEMES)
    return specs, artifacts


def command_for(spec, artifacts, args):
    artifact = artifacts[(spec.scenario.name, spec.seed)]
    case_dir = args.out / "raw" / spec.scenario.name / spec.scheme / f"seed_{spec.seed}"
    return base.build_command(
        spec, args.sim, artifact.path, case_dir, 256, artifact.connections)


def diag(row, name, default=0):
    return json.loads(row["diagnostics_json"]).get(name, default)


def validate_row(row):
    if not (row["returncode"] == 0 and row["config_ok"] and
            row["all_flows_completed"] and row["completed"] == 65280):
        raise ValueError(f"invalid {row['scheme']} seed{row['seed']}")
    if row["scheme"] == "sglb":
        with gzip.open(row["stdout_gz"], "rt", encoding="utf-8") as handle:
            text = handle.read()
        required = (
            "min choices 20, candidate policy whole_grade_min, "
            "candidate dispatch random, GCN cadence independent"
        )
        if required not in text:
            raise ValueError("SGLB effective defaults do not match the decision")


def enrich(row):
    result = dict(row)
    result.update({
        "spine_queue_cv": diag(row, "QueueCvDiag.spine_queue_cv", 0),
        "spine_queue_avg": diag(row, "QueueCvDiag.spine_queue_avg", 0),
        "queue_p99_fraction": diag(
            row, "QueueCvDiag.spine_queue_p99_fraction", 0),
        "avg_available_choices": diag(
            row, "SglbRouteDiag.avg_available_choices", 64),
        "avg_candidate_choices": diag(
            row, "SglbRouteDiag.avg_candidate_choices", 64),
        "avg_best_quality_choices": diag(
            row, "SglbRouteDiag.avg_best_quality_choices", 64),
        "retransmissions": diag(row, "RoceDiag.retx_packets", 0),
        "trims": diag(row, "QueueDiag.composite_trims", 0),
        "ecn_marks": diag(row, "QueueDiag.composite_ecn_marks", 0),
    })
    # OPS does not emit SGLB route diagnostics, but its effective spray set is
    # the complete 64-path plane.
    if row["scheme"] == "ops":
        result["avg_available_choices"] = 64
        result["avg_candidate_choices"] = 64
        result["avg_best_quality_choices"] = 64
    return result


def summarize(rows):
    summaries = []
    by_scheme = {scheme: [row for row in rows if row["scheme"] == scheme]
                 for scheme in SCHEMES}
    for scheme in SCHEMES:
        group = by_scheme[scheme]
        summaries.append({
            "scheme": scheme,
            "cct_us_gmean": geometric_mean(row["max_fct_us"] for row in group),
            "mean_fct_us_gmean": geometric_mean(
                row["mean_fct_us"] for row in group),
            "p99_fct_us_gmean": geometric_mean(
                row["p99_fct_us"] for row in group),
            "spine_queue_cv_mean": sum(row["spine_queue_cv"] for row in group) / 3,
            "queue_p99_fraction_mean": sum(
                row["queue_p99_fraction"] for row in group) / 3,
            "avg_candidate_choices_mean": sum(
                row["avg_candidate_choices"] for row in group) / 3,
            "retransmissions_sum": sum(row["retransmissions"] for row in group),
            "trims_sum": sum(row["trims"] for row in group),
            "ecn_marks_sum": sum(row["ecn_marks"] for row in group),
        })
    ops = next(row for row in summaries if row["scheme"] == "ops")
    for row in summaries:
        for metric in ("cct_us_gmean", "mean_fct_us_gmean", "p99_fct_us_gmean"):
            row[metric + "_ratio_vs_ops"] = row[metric] / ops[metric]
    return summaries


def write_report(path, rows, summaries):
    sglb = next(row for row in summaries if row["scheme"] == "sglb")
    verdict = (
        "通过" if sglb["cct_us_gmean_ratio_vs_ops"] <= 1.01 and
        sglb["p99_fct_us_gmean_ratio_vs_ops"] <= 1.01 else "未通过")
    lines = [
        "# 默认 SGLB 与 OPS 健康低压对比", "",
        f"结论：健康场景 1% guardrail **{verdict}**。", "",
        "场景：256 主机、64 路径、健康对称 p4 A2A、每源 64 MiB、"
        "seeds 13/29/47。默认 SGLB 为异步 GCN、random、min20、整档补足。", "",
        "## 三 seed 汇总", "",
        "| 方案 | CCT几何均值 | mean FCT | p99 FCT | CCT/OPS | p99/OPS | "
        "spine queue CV | 平均候选数 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summaries:
        lines.append(
            f"| {row['scheme']} | {row['cct_us_gmean']:.3f} | "
            f"{row['mean_fct_us_gmean']:.3f} | {row['p99_fct_us_gmean']:.3f} | "
            f"{row['cct_us_gmean_ratio_vs_ops']:.5f} | "
            f"{row['p99_fct_us_gmean_ratio_vs_ops']:.5f} | "
            f"{row['spine_queue_cv_mean']:.5f} | "
            f"{row['avg_candidate_choices_mean']:.3f} |")
    lines.extend(["", "## 每 seed 明细", "",
                  "| seed | 方案 | CCT | mean FCT | p99 FCT | spine queue CV | 候选数 | 重传 | TRIM | ECN |",
                  "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for row in sorted(rows, key=lambda item: (item["seed"], item["scheme"])):
        lines.append(
            f"| {row['seed']} | {row['scheme']} | {row['max_fct_us']:.3f} | "
            f"{row['mean_fct_us']:.3f} | {row['p99_fct_us']:.3f} | "
            f"{row['spine_queue_cv']:.5f} | {row['avg_candidate_choices']:.3f} | "
            f"{int(row['retransmissions'])} | {int(row['trims'])} | "
            f"{int(row['ecn_marks'])} |")
    lines.extend(["", "候选数反映 SGLB 可喷洒路径集合；OPS 固定记为全部64路径。"
                  "spine queue CV 与 FCT 共同用于判断是否存在不均匀或并行度下降。"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", type=Path, default=DEFAULT_SIM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    specs, artifacts = make_specs(args)
    if args.dry_run:
        for spec in specs:
            command_for(spec, artifacts, args)
        print("validated 6 low-pressure cells")
        return 0
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(base.run_case, spec,
                        artifacts[(spec.scenario.name, spec.seed)], args, 256): spec
            for spec in specs}
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            validate_row(row)
            rows.append(enrich(row))
    rows.sort(key=lambda row: (row["seed"], row["scheme"]))
    summaries = summarize(rows)
    base.write_csv(args.out / "cells.csv", rows)
    base.write_csv(args.out / "summary.csv", summaries)
    write_report(args.out / "report_zh.md", rows, summaries)
    print("completed healthy low-pressure SGLB vs OPS comparison")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
