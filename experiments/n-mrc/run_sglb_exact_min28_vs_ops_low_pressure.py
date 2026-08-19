#!/usr/bin/env python3
"""Compare asynchronous random exact-min28 SGLB with OPS on healthy A2A."""

import argparse
import concurrent.futures
import gzip
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import run_final_512_comparison as base  # noqa: E402
import run_sglb_default_vs_ops_low_pressure as common  # noqa: E402


DEFAULT_OUT = SCRIPT_DIR / "output/sglb_exact_min28_vs_ops_low_pressure"


def make_specs(args):
    return common.make_specs(args)


def command_for(spec, artifacts, args):
    command = common.command_for(spec, artifacts, args)
    if spec.scheme == "sglb":
        command.extend((
            "-sglb_min_choices", "28",
            "-sglb_candidate_policy", "exact_min",
        ))
    return command


def validate_row(row):
    if not (row["returncode"] == 0 and row["config_ok"] and
            row["all_flows_completed"] and row["completed"] == 65280):
        raise ValueError(f"invalid {row['scheme']} seed{row['seed']}")
    if row["scheme"] == "sglb":
        with gzip.open(row["stdout_gz"], "rt", encoding="utf-8") as handle:
            text = handle.read()
        required = (
            "min choices 28, candidate policy exact_min, "
            "candidate dispatch random, GCN cadence independent"
        )
        if required not in text:
            raise ValueError("SGLB effective configuration is not exact-min28")


def write_report(path, rows, summaries):
    common.write_report(path, rows, summaries)
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "# 默认 SGLB 与 OPS 健康低压对比",
        "# exact-min28 SGLB 与 OPS 健康低压对比")
    text = text.replace(
        "默认 SGLB 为异步 GCN、random、min20、整档补足。",
        "SGLB 为异步 GCN、random、min28、精确补足。")
    path.write_text(text, encoding="utf-8")


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", type=Path, default=common.DEFAULT_SIM)
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
        print("validated 6 exact-min28 low-pressure cells")
        return 0
    original_build_command = base.build_command

    def configured_build_command(spec, sim, traffic_file, case_dir, nodes,
                                 connections=None):
        command = original_build_command(
            spec, sim, traffic_file, case_dir, nodes, connections)
        if spec.scheme == "sglb":
            command.extend((
                "-sglb_min_choices", "28",
                "-sglb_candidate_policy", "exact_min",
            ))
        return command

    base.build_command = configured_build_command
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(base.run_case, spec,
                        artifacts[(spec.scenario.name, spec.seed)], args, 256): spec
            for spec in specs}
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            validate_row(row)
            rows.append(common.enrich(row))
    rows.sort(key=lambda row: (row["seed"], row["scheme"]))
    summaries = common.summarize(rows)
    base.write_csv(args.out / "cells.csv", rows)
    base.write_csv(args.out / "summary.csv", summaries)
    write_report(args.out / "report_zh.md", rows, summaries)
    print("completed exact-min28 healthy low-pressure comparison")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
