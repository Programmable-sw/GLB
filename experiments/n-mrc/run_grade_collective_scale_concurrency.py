#!/usr/bin/env python3
"""Sweep grade weights across All-to-All flow size and concurrency."""

import argparse
import concurrent.futures
import csv
import json
import math
from pathlib import Path
import shlex
import subprocess
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import run_final_512_comparison as base  # noqa: E402


SEEDS = (13, 29, 47)
OUT = HERE / "output/grade_collective_scale_concurrency_20260726"
WEIGHTS = {
    "equal": ("4", "4", "4", "0"),
    "gentle": ("4", "3", "2", "0"),
    "default": ("4", "2", "1", "0"),
    "sharp": ("8", "2", "1", "0"),
}


def scenario_name(parallel, flow_kib):
    return f"collective_p{parallel}_flow{flow_kib}kib"


SCENARIO_META = {}
for parallel in (1, 2, 4, 8, 16, 32):
    SCENARIO_META[scenario_name(parallel, 512)] = {
        "axis": "parallel", "parallel": parallel, "flow_kib": 512,
    }
for flow_kib in (128, 512, 2048, 8192):
    SCENARIO_META.setdefault(scenario_name(4, flow_kib), {
        "axis": "flow_size", "parallel": 4, "flow_kib": flow_kib,
    })

SCENARIOS = tuple(
    base.Scenario(
        name, "healthy_alltoall", "all_to_all", "alltoall",
        size_mib=meta["flow_kib"] * 128 // 1024,
        parallel=meta["parallel"])
    for name, meta in SCENARIO_META.items()
)


class Spec:
    def __init__(self, scenario, variant, seed):
        self.scenario = scenario
        self.scheme = variant
        self.seed = seed


def build_command(spec, sim, artifact, case_dir):
    proxy = base.CaseSpec(spec.scenario, "n-mrc", spec.seed)
    command = base.build_command(
        proxy, sim, artifact.path, case_dir, 128, artifact.connections)
    command[command.index("-lb") + 1] = "grade"
    del command[-len(base.SCHEME_ARGS["n-mrc"]):]
    command.extend(("-stor_level_weights", *WEIGHTS[spec.scheme]))
    return command


def config_ok(text, spec, returncode):
    expected = "weights " + "/".join(WEIGHTS[spec.scheme])
    return int(
        returncode == 0 and "lb mode grade" in text and
        expected in text and "StorProfileDiag " in text and
        "BoundedRecoveryDiag " in text and
        "RoceTransportConfig semantics=mrc_exact_bounded" in text)


def run(spec, artifact, args):
    case_dir = (
        args.out / "raw" / spec.scenario.name / spec.scheme /
        f"seed_{spec.seed}")
    case_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(spec, args.sim, artifact, case_dir)
    command_text = shlex.join(command)
    sim_sha256 = base.file_sha256(args.sim)
    summary_path = case_dir / "summary.json"
    if summary_path.exists():
        cached = json.loads(summary_path.read_text())
        if (
                cached.get("traffic_sha256") == artifact.sha256 and
                cached.get("command") == command_text and
                cached.get("sim_sha256") == sim_sha256 and
                cached.get("config_ok") and
                cached.get("all_flows_completed")):
            print(spec.scenario.name, spec.scheme, spec.seed, "cached",
                  flush=True)
            return cached

    process = subprocess.run(
        command, cwd=case_dir, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=args.timeout,
        check=False)
    text = process.stdout
    (case_dir / "stdout.log").write_text(text, encoding="utf-8")
    parsed = base.parse_completions(
        text, artifact.starts_by_flowid, artifact.connections, False)
    _lines, diag = base.parse_diagnostics(text)
    meta = SCENARIO_META[spec.scenario.name]
    row = {
        "scenario": spec.scenario.name,
        "axis": meta["axis"],
        "parallel": meta["parallel"],
        "flow_kib": meta["flow_kib"],
        "per_source_mib": meta["flow_kib"] * 127 / 1024,
        "variant": spec.scheme,
        "weights": "/".join(WEIGHTS[spec.scheme]),
        "seed": spec.seed,
        "traffic_sha256": artifact.sha256,
        "sim_sha256": sim_sha256,
        "primary_metric": "all_to_all_cct_us",
        "primary_us": parsed["max_fct_us"],
        "returncode": process.returncode,
        "config_ok": config_ok(text, spec, process.returncode),
        "command": command_text,
        **parsed,
        **diag,
    }
    summary_path.write_text(
        json.dumps(row, indent=2, sort_keys=True) + "\n")
    print(spec.scenario.name, spec.scheme, spec.seed,
          row["primary_us"], flush=True)
    return row


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def validate_specs(specs):
    expected = {
        (scenario.name, variant, seed)
        for scenario in SCENARIOS
        for variant in WEIGHTS
        for seed in SEEDS
    }
    actual = {(spec.scenario.name, spec.scheme, spec.seed) for spec in specs}
    if len(specs) != 108 or actual != expected:
        raise ValueError("command matrix is not the expected 108 cells")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", type=Path,
                        default=ROOT / "sim/datacenter/htsim_roce")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.sim = args.sim.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    specs = []
    for scenario in SCENARIOS:
        for seed in SEEDS:
            artifact = base.materialize_traffic(
                args.out / "traffic", scenario, seed, 128)
            artifacts[(scenario.name, seed)] = artifact
            specs.extend(Spec(scenario, variant, seed) for variant in WEIGHTS)
    validate_specs(specs)
    if args.dry_run:
        print("validated 108 paired commands")
        return

    rows = []
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        futures = [
            pool.submit(
                run, spec, artifacts[(spec.scenario.name, spec.seed)], args)
            for spec in specs
        ]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: (
        row["scenario"], row["variant"], row["seed"]))
    invalid = [
        row for row in rows
        if row["returncode"] != 0 or not row["config_ok"] or
        not row["all_flows_completed"] or
        not math.isfinite(row["primary_us"]) or row["primary_us"] <= 0]
    write_csv(args.out / "results.csv", rows)
    if invalid:
        raise RuntimeError(f"{len(invalid)} invalid cells")
    print(args.out / "results.csv")


if __name__ == "__main__":
    main()
