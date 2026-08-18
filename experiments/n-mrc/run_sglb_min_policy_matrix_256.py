#!/usr/bin/env python3
"""Complete 7-min x 3-policy x 2-scenario SGLB A2A matrix."""

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
from dataclasses import dataclass


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import experiment_metrics as metrics  # noqa: E402
import run_sglb_min_choices_64path_scan_256 as base  # noqa: E402
import run_sglb_min_policy_staged_256 as staged  # noqa: E402


MIN_CHOICES = base.MIN_CHOICES
POLICIES = staged.POLICIES
SCENARIOS = (
    "healthy_medium", "healthy_high",
    "asymmetric_medium", "asymmetric_high",
)
CADENCES = ("synchronized", "independent")
RANKING_METRICS = staged.RANKING_METRICS
SEED = 13
WORKLOAD = staged.WORKLOAD
PER_SOURCE_MIB = {"medium": 64, "high": 256}
DEFAULT_SIM = ROOT / "sim/datacenter/htsim_roce"
DEFAULT_OUT = SCRIPT_DIR / "output/sglb_min_policy_matrix_256"


@dataclass(frozen=True)
class CellSpec:
    workload: str
    scenario: str
    policy: str
    seed: int
    min_choices: int
    traffic: Path
    traffic_sha256: str
    connections: int
    flows_data: object
    case_dir: Path
    output: Path
    command: tuple
    load: str
    asymmetric: bool
    parallel: int
    per_source_mib: int
    cadence: str


def _materialize_traffic(args, load):
    traffic_dir = args.out / "traffic"
    traffic_dir.mkdir(parents=True, exist_ok=True)
    parallel = 16 if load == "medium" else 32
    per_source_mib = PER_SOURCE_MIB[load]
    traffic = traffic_dir / f"a2a_p{parallel}_{per_source_mib}mib_seed13.cm"
    flow_size = (per_source_mib * base.common.MIB) // base.NODES
    plan = base.common.alltoall_plan(
        base.NODES, base.NODES, parallel, flow_size, 0, SEED)
    if not traffic.exists():
        base.common.write_alltoall_matrix(traffic, plan)
    return (traffic, metrics.file_sha256(traffic), plan.connection_count,
            None, parallel, per_source_mib)


def _command(args, traffic, output, connections, choice, scenario, policy,
             cadence):
    load = "medium" if scenario.endswith("_medium") else "high"
    sample_scale = PER_SOURCE_MIB[load] / 256
    command = list(base.build_command(
        args.sim, traffic, output, connections, SEED, WORKLOAD,
        choice, sample_scale))
    asymmetric = scenario.startswith("asymmetric_")
    if not asymmetric:
        command = command[:command.index("-sglb_background")]
    elif scenario.endswith("_medium"):
        rate_index = command.index("-sglb_bg_rate_gbps") + 1
        command[rate_index] = "300"
        command[command.index("-sglb_bg_on_us") + 1] = "200"
        command[command.index("-sglb_bg_off_us") + 1] = "200"
    command.extend((
        "-sglb_candidate_policy", policy,
        "-sglb_gcn_cadence", cadence))
    return tuple(command)


def make_specs(args):
    args.out = Path(args.out).resolve()
    args.sim = Path(args.sim).resolve()
    materials = {
        load: _materialize_traffic(args, load) for load in ("medium", "high")}
    specs = []
    for cadence in CADENCES:
      for scenario in SCENARIOS:
        load = "medium" if scenario.endswith("_medium") else "high"
        asymmetric = scenario.startswith("asymmetric_")
        (traffic, digest, connections, flows, parallel,
         per_source_mib) = materials[load]
        for policy in POLICIES:
          for choice in MIN_CHOICES:
                case_dir = (args.out / "runs" / cadence / scenario / policy /
                            f"min{choice}")
                output = case_dir / "logout.dat"
                specs.append(CellSpec(
                    WORKLOAD, scenario, policy, SEED, choice, traffic, digest,
                    connections, flows, case_dir, output,
                    _command(args, traffic, output, connections, choice,
                             scenario, policy, cadence), load, asymmetric,
                    parallel, per_source_mib, cadence))
    return specs


def validate_specs(specs):
    if len(specs) != 168:
        raise ValueError("complete matrix must contain 168 cells")
    seen = set()
    traffic_hashes_by_load = {"medium": set(), "high": set()}
    for spec in specs:
        key = (spec.cadence, spec.scenario, spec.policy, spec.min_choices)
        if key in seen:
            raise ValueError(f"duplicate cell {key}")
        seen.add(key)
        traffic_hashes_by_load[spec.load].add(spec.traffic_sha256)
        command = list(spec.command)
        expected = {
            "-nodes": "256", "-paths": "64", "-conns": "65280",
            "-end": "25000" if spec.load == "medium" else "100000",
            "-sglb_min_choices": str(spec.min_choices),
            "-sglb_candidate_policy": spec.policy,
            "-sglb_gcn_cadence": spec.cadence,
        }
        for option, value in expected.items():
            if command[command.index(option) + 1] != value:
                raise ValueError(f"cell {key}: incorrect {option}")
        has_background = "-sglb_background" in command
        if has_background != spec.asymmetric:
            raise ValueError(f"cell {key}: background mismatch")
        if has_background:
            for option, value in {
                    "-sglb_bg_links_per_direction": "2",
                    "-sglb_bg_rate_gbps":
                        "300" if spec.load == "medium" else "350",
                    "-sglb_bg_on_us":
                        "200" if spec.load == "medium" else "400",
                    "-sglb_bg_off_us":
                        "200" if spec.load == "medium" else "400"}.items():
                if command[command.index(option) + 1] != value:
                    raise ValueError(f"cell {key}: incorrect {option}")
    if any(len(hashes) != 1 for hashes in traffic_hashes_by_load.values()):
        raise ValueError("foreground traffic differs within a load level")


def select_policy_optima(rows):
    indexed = {
        (row["cadence"], row["scenario"], row["policy"],
         int(row["min_choices"])): row
        for row in rows
    }
    optima = []
    for cadence in CADENCES:
     for load in ("medium", "high"):
      for policy in POLICIES:
        healthy_name = f"healthy_{load}"
        asymmetric_name = f"asymmetric_{load}"
        healthy_best = min(
            indexed[(cadence, healthy_name, policy, choice)]["cct_us"]
            for choice in MIN_CHOICES)
        eligible = []
        for choice in MIN_CHOICES:
            healthy = indexed[(cadence, healthy_name, policy, choice)]
            asymmetric = indexed[(cadence, asymmetric_name, policy, choice)]
            regression = healthy["cct_us"] / healthy_best - 1.0
            if regression <= 0.01 + 1e-12:
                eligible.append((
                    asymmetric["cct_us"], asymmetric["p99_fct_us"],
                    asymmetric["trims"], asymmetric["retransmissions"],
                    asymmetric["ecn_marks"], choice))
        choice = min(eligible)[-1]
        healthy = indexed[(cadence, healthy_name, policy, choice)]
        asymmetric = indexed[(cadence, asymmetric_name, policy, choice)]
        optima.append({
            "cadence": cadence, "load": load, "policy": policy,
            "selected_min": choice,
            "healthy_cct_us": healthy["cct_us"],
            "healthy_regression_vs_policy_best":
                healthy["cct_us"] / healthy_best - 1.0,
            "asymmetric_cct_us": asymmetric["cct_us"],
            "asymmetric_p99_fct_us": asymmetric["p99_fct_us"],
            "asymmetric_trims": asymmetric["trims"],
            "asymmetric_retransmissions": asymmetric["retransmissions"],
            "asymmetric_ecn_marks": asymmetric["ecn_marks"],
            "healthy_avg_candidate_choices":
                healthy.get("avg_candidate_choices", 0),
            "asymmetric_avg_candidate_choices":
                asymmetric.get("avg_candidate_choices", 0),
        })
    return sorted(optima, key=lambda row: (
        row["asymmetric_cct_us"], row["asymmetric_p99_fct_us"],
        row["asymmetric_trims"], row["asymmetric_retransmissions"],
        row["asymmetric_ecn_marks"], row["load"], row["policy"]))


def make_gate_specs(args):
    return [spec for spec in make_specs(args)
            if spec.policy == "exact_min" and spec.min_choices == 24]


def evaluate_pressure_gate(rows):
    reasons = []
    if len(rows) != 8:
        reasons.append(f"expected 8 gate rows, got {len(rows)}")
    for row in rows:
        scenario = row["cadence"] + "/" + row["scenario"]
        if (not row.get("config_ok") or not row.get("all_flows_completed")
                or int(row.get("gcn_stale", 0)) != 0):
            reasons.append(f"{scenario}: invalid or incomplete")
        if float(row.get("cct_us", 0)) < 1000:
            reasons.append(f"{scenario}: CCT below sustained-pressure floor")
        if row["scenario"].startswith("asymmetric_"):
            if float(row.get("avg_best_quality_choices", 64)) >= 40:
                reasons.append(f"{scenario}: best-quality set did not fall below 40")
            if float(row.get("avg_candidate_choices", 64)) >= 48:
                reasons.append(f"{scenario}: candidate set did not fall below 48")
    return not reasons, reasons


def _write_report(path, optima):
    lines = [
        "# SGLB min × candidate-policy complete matrix", "",
        "Single seed 13; 256-host global A2A; medium=p16/64 MiB per source, "
        "high=p32/256 MiB per source; "
        "64 paths; async GCN. Each policy is tuned independently per load.", "",
        "Healthy CCT must be within 1% of that policy/load's healthy optimum; "
        "the eligible min with the lowest asymmetric CCT is selected.", "",
        "## Policy optima", "",
        base._markdown_table(optima, (
            "load", "policy", "selected_min", "healthy_cct_us",
            "asymmetric_cct_us", "asymmetric_p99_fct_us",
            "healthy_avg_candidate_choices",
            "asymmetric_avg_candidate_choices", "asymmetric_trims",
            "asymmetric_retransmissions", "asymmetric_ecn_marks")), "",
        "This one-seed focused matrix is not a universal optimum.",
    ]
    base.atomic_write_text(path, "\n".join(lines) + "\n")


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", type=Path, default=DEFAULT_SIM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gate", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.out = Path(args.out).resolve()
    args.sim = Path(args.sim).resolve()
    if args.workers < 1 or args.timeout < 1:
        raise ValueError("workers and timeout must be positive")
    specs = make_specs(args)
    validate_specs(specs)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = staged._manifest(args, specs)
    manifest.update({
        "matrix_cells": 168, "parallel_per_source": [16, 32],
        "cadences": list(CADENCES), "candidate_dispatch": "random",
        "per_source_mib": PER_SOURCE_MIB,
        "background_rate_gbps": {"medium": 300, "high": 350},
    })
    base.atomic_write_text(
        args.out / "manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    base.write_csv(args.out / "commands.tsv", [{
        "scenario": spec.scenario, "policy": spec.policy,
        "cadence": spec.cadence,
        "min_choices": spec.min_choices,
        "traffic_sha256": spec.traffic_sha256,
        "command": shlex.join(spec.command),
    } for spec in specs])
    if args.dry_run:
        print("validated 168 matrix cells")
        return 0
    if not args.sim.exists():
        raise FileNotFoundError(args.sim)
    if args.gate:
        rows = staged.run_specs(make_gate_specs(args), args, lambda specs: None)
        base.write_csv(args.out / "gate_cells.csv", rows)
        passed, reasons = evaluate_pressure_gate(rows)
        base.atomic_write_text(
            args.out / "gate.txt",
            ("PASS\n" if passed else "FAIL\n") + "\n".join(reasons) + "\n")
        if not passed:
            for reason in reasons:
                print(reason, file=sys.stderr)
            return 2
        print("pressure gate passed")
        return 0
    rows = staged.run_specs(specs, args, validate_specs)
    base.write_csv(args.out / "cells.csv", rows)
    base.write_csv(args.out / "rankings.csv", staged._rank(rows))
    optima = select_policy_optima(rows)
    base.write_csv(args.out / "policy_optima.csv", optima)
    _write_report(args.out / "report.md", optima)
    print("matrix completed; best tuned policy " + optima[0]["policy"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
