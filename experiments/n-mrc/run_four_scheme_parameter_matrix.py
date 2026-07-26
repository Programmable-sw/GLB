#!/usr/bin/env python3
"""Run paired parameter sweeps for avail, grade, netaware, and n-MRC."""

import argparse
import concurrent.futures
import csv
import json
import math
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import run_final_512_comparison as base  # noqa: E402


SEEDS = (13, 29, 47)
OUT = HERE / "output/four_scheme_parameter_matrix_20260726"
SCENARIOS = (
    base.Scenario("asymmetric_permutation_16mib", "asymmetric_p2p",
                  "point_to_point", "permutation", size_mib=16,
                  degraded=True),
    base.Scenario("standard_websearch_proxy_80pct", "healthy_p2p",
                  "point_to_point", "websearch", websearch_load=80),
    base.Scenario("full_global_p16_256mib_background_off", "healthy_alltoall",
                  "all_to_all", "alltoall", size_mib=256, parallel=16),
    base.Scenario("full_global_p4_64mib_background_off", "healthy_alltoall",
                  "all_to_all", "alltoall", size_mib=64, parallel=4),
)

VARIANTS = {
    "grade_equal": ("grade", ("-stor_level_weights", "4", "4", "4", "0")),
    "grade_gentle": ("grade", ("-stor_level_weights", "4", "3", "2", "0")),
    "grade_default": ("grade", ("-stor_level_weights", "4", "2", "1", "0")),
    "grade_sharp": ("grade", ("-stor_level_weights", "8", "2", "1", "0")),
    "grade_sensitive": (
        "grade", ("-stor_level_weights", "4", "2", "1", "0",
                  "-stor_level_thresholds", "10", "8", "4")),
    "grade_conservative": (
        "grade", ("-stor_level_weights", "4", "2", "1", "0",
                  "-stor_level_thresholds", "6", "3", "1")),
    "netaware_equal": (
        "netaware", ("-netaware_level_weights", "4", "4", "4", "0",
                     "-netaware_weight_adaptation", "off")),
    "netaware_gentle": (
        "netaware", ("-netaware_level_weights", "4", "3", "2", "0",
                     "-netaware_weight_adaptation", "off")),
    "netaware_default_off": (
        "netaware", ("-netaware_level_weights", "4", "2", "1", "0",
                     "-netaware_weight_adaptation", "off")),
    "netaware_sharp": (
        "netaware", ("-netaware_level_weights", "8", "2", "1", "0",
                     "-netaware_weight_adaptation", "off")),
    "netaware_goodcap": (
        "netaware", ("-netaware_level_weights", "4", "2", "1", "0",
                     "-netaware_weight_adaptation", "good_share_cap")),
    "avail_packet": ("avail", ()),
    "avail_ecn_only": ("avail", ("-avail_ecn_only",)),
    "avail_hybrid": ("avail", ("-stor_aging", "hybrid")),
    "avail_time": ("avail", ("-stor_aging", "time_ewma")),
    "nmrc_better_ge3": (
        "n-mrc", ("-nmrc_ev_mode", "encoded", "-nmrc_reroute_policy",
                  "better_ge3", "-nmrc_fastcnp", "on")),
    "nmrc_any_better": (
        "n-mrc", ("-nmrc_ev_mode", "encoded", "-nmrc_reroute_policy",
                  "any_better", "-nmrc_fastcnp", "on")),
}

MATRIX = {
    "asymmetric_permutation_16mib": tuple(VARIANTS),
    "standard_websearch_proxy_80pct": tuple(
        name for name in VARIANTS if not name.startswith("nmrc_")),
    "full_global_p16_256mib_background_off": tuple(
        name for name in VARIANTS if not name.startswith("nmrc_")),
    "full_global_p4_64mib_background_off": tuple(
        name for name in VARIANTS
        if name.startswith(("grade_", "avail_"))),
}


class Spec:
    def __init__(self, scenario, variant, seed):
        self.scenario = scenario
        self.scheme = variant
        self.seed = seed


def build_command(spec, sim, artifact, case_dir):
    lb, extra = VARIANTS[spec.scheme]
    proxy = base.CaseSpec(spec.scenario, "n-mrc", spec.seed)
    command = base.build_command(
        proxy, sim, artifact.path, case_dir, 128, artifact.connections)
    command[command.index("-lb") + 1] = lb
    default = base.SCHEME_ARGS["n-mrc"]
    del command[-len(default):]
    command.extend(extra)
    return command


def config_ok(text, spec, returncode):
    lb = VARIANTS[spec.scheme][0]
    return int(
        returncode == 0 and
        f"lb mode {lb}" in text and
        "RoceTransportConfig semantics=mrc_exact_bounded" in text and
        "BoundedRecoveryDiag " in text and
        (lb not in ("avail", "grade") or "StorProfileDiag " in text) and
        (lb != "netaware" or "NetawareDiag " in text) and
        (lb != "n-mrc" or "HybridNmrcDiag " in text)
    )


def run(spec, artifact, args):
    case_dir = args.out / "raw" / spec.scenario.name / spec.scheme / (
        f"seed_{spec.seed}")
    case_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(spec, args.sim, artifact, case_dir)
    command_text = " ".join(command)
    summary_path = case_dir / "summary.json"
    if summary_path.exists():
        cached = json.loads(summary_path.read_text())
        if (
                cached.get("traffic_sha256") == artifact.sha256 and
                cached.get("command") == command_text and
                cached.get("config_ok") and
                cached.get("all_flows_completed")):
            print(spec.scenario.name, spec.scheme, spec.seed, "cached",
                  flush=True)
            return cached
    process = subprocess.run(
        command, cwd=case_dir, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=args.timeout)
    text = process.stdout
    (case_dir / "stdout.log").write_text(text, encoding="utf-8")
    parsed = base.parse_completions(
        text, artifact.starts_by_flowid, artifact.connections, False)
    _lines, diag = base.parse_diagnostics(text)
    metric, primary = base.primary_metric(spec.scenario, parsed)
    row = {
        "scenario": spec.scenario.name, "variant": spec.scheme,
        "lb": VARIANTS[spec.scheme][0], "seed": spec.seed,
        "traffic_sha256": artifact.sha256, "primary_metric": metric,
        "primary_us": primary, "returncode": process.returncode,
        "config_ok": config_ok(text, spec, process.returncode),
        "command": command_text, **parsed, **diag,
    }
    summary_path.write_text(
        json.dumps(row, indent=2, sort_keys=True) + "\n")
    print(spec.scenario.name, spec.scheme, spec.seed, primary, flush=True)
    return row


def write_csv(path, rows):
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", type=Path,
                        default=ROOT / "sim/datacenter/htsim_roce")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    traffic_dir = args.out / "traffic"
    artifacts = {}
    specs = []
    for scenario in SCENARIOS:
        for seed in SEEDS:
            artifacts[(scenario.name, seed)] = base.materialize_traffic(
                traffic_dir, scenario, seed, 128)
            specs.extend(
                Spec(scenario, variant, seed)
                for variant in MATRIX[scenario.name])
    if args.dry_run:
        print(f"validated {len(specs)} paired commands")
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
    rows.sort(key=lambda item: (
        item["scenario"], item["variant"], item["seed"]))
    invalid = [
        row for row in rows
        if not row["config_ok"] or not row["all_flows_completed"] or
        not math.isfinite(row["primary_us"]) or row["primary_us"] <= 0]
    write_csv(args.out / "results.csv", rows)
    if invalid:
        raise RuntimeError(f"{len(invalid)} invalid cells")
    print(args.out / "results.csv")


if __name__ == "__main__":
    main()
