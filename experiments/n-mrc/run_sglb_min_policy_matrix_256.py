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
RANKING_METRICS = staged.RANKING_METRICS
SEED = 13
WORKLOAD = staged.WORKLOAD
SAMPLE_SCALE = 1 / 16
PER_SOURCE_MIB = 16
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


def _materialize_traffic(args, load):
    traffic_dir = args.out / "traffic"
    traffic_dir.mkdir(parents=True, exist_ok=True)
    parallel = 16 if load == "medium" else 32
    traffic = traffic_dir / f"a2a_p{parallel}_16mib_seed13.cm"
    flow_size = (PER_SOURCE_MIB * base.common.MIB) // base.NODES
    plan = base.common.alltoall_plan(
        base.NODES, base.NODES, parallel, flow_size, 0, SEED)
    if not traffic.exists():
        base.common.write_alltoall_matrix(traffic, plan)
    return (traffic, metrics.file_sha256(traffic), plan.connection_count,
            None, parallel)


def _command(args, traffic, output, connections, choice, scenario, policy):
    command = list(base.build_command(
        args.sim, traffic, output, connections, SEED, WORKLOAD,
        choice, SAMPLE_SCALE))
    asymmetric = scenario.startswith("asymmetric_")
    if not asymmetric:
        command = command[:command.index("-sglb_background")]
    elif scenario.endswith("_medium"):
        rate_index = command.index("-sglb_bg_rate_gbps") + 1
        command[rate_index] = "250"
    command.extend(("-sglb_candidate_policy", policy))
    return tuple(command)


def make_specs(args):
    args.out = Path(args.out).resolve()
    args.sim = Path(args.sim).resolve()
    materials = {
        load: _materialize_traffic(args, load) for load in ("medium", "high")}
    specs = []
    for scenario in SCENARIOS:
        load = "medium" if scenario.endswith("_medium") else "high"
        asymmetric = scenario.startswith("asymmetric_")
        traffic, digest, connections, flows, parallel = materials[load]
        for policy in POLICIES:
            for choice in MIN_CHOICES:
                case_dir = args.out / "runs" / scenario / policy / f"min{choice}"
                output = case_dir / "logout.dat"
                specs.append(CellSpec(
                    WORKLOAD, scenario, policy, SEED, choice, traffic, digest,
                    connections, flows, case_dir, output,
                    _command(args, traffic, output, connections, choice,
                             scenario, policy), load, asymmetric, parallel))
    return specs


def validate_specs(specs):
    if len(specs) != 84:
        raise ValueError("complete matrix must contain 84 cells")
    seen = set()
    traffic_hashes_by_load = {"medium": set(), "high": set()}
    for spec in specs:
        key = (spec.scenario, spec.policy, spec.min_choices)
        if key in seen:
            raise ValueError(f"duplicate cell {key}")
        seen.add(key)
        traffic_hashes_by_load[spec.load].add(spec.traffic_sha256)
        command = list(spec.command)
        expected = {
            "-nodes": "256", "-paths": "64", "-conns": "65280",
            "-end": "6250", "-sglb_min_choices": str(spec.min_choices),
            "-sglb_candidate_policy": spec.policy,
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
                        "250" if spec.load == "medium" else "350",
                    "-sglb_bg_on_us": "400",
                    "-sglb_bg_off_us": "400"}.items():
                if command[command.index(option) + 1] != value:
                    raise ValueError(f"cell {key}: incorrect {option}")
    if any(len(hashes) != 1 for hashes in traffic_hashes_by_load.values()):
        raise ValueError("foreground traffic differs within a load level")


def select_policy_optima(rows):
    indexed = {
        (row["scenario"], row["policy"], int(row["min_choices"])): row
        for row in rows
    }
    optima = []
    for load in ("medium", "high"):
      for policy in POLICIES:
        healthy_name = f"healthy_{load}"
        asymmetric_name = f"asymmetric_{load}"
        healthy_best = min(
            indexed[(healthy_name, policy, choice)]["cct_us"]
            for choice in MIN_CHOICES)
        eligible = []
        for choice in MIN_CHOICES:
            healthy = indexed[(healthy_name, policy, choice)]
            asymmetric = indexed[(asymmetric_name, policy, choice)]
            regression = healthy["cct_us"] / healthy_best - 1.0
            if regression <= 0.01 + 1e-12:
                eligible.append((
                    asymmetric["cct_us"], asymmetric["p99_fct_us"],
                    asymmetric["trims"], asymmetric["retransmissions"],
                    asymmetric["ecn_marks"], choice))
        choice = min(eligible)[-1]
        healthy = indexed[(healthy_name, policy, choice)]
        asymmetric = indexed[(asymmetric_name, policy, choice)]
        optima.append({
            "load": load, "policy": policy, "selected_min": choice,
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


def _write_report(path, optima):
    lines = [
        "# SGLB min × candidate-policy complete matrix", "",
        "Single seed 13; 256-host global p16/p32 A2A; 16 MiB per source; "
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
        "matrix_cells": 84, "parallel_per_source": [16, 32],
        "per_source_mib": PER_SOURCE_MIB, "sample_scale": SAMPLE_SCALE,
        "background_rate_gbps": {"medium": 250, "high": 350},
    })
    base.atomic_write_text(
        args.out / "manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    base.write_csv(args.out / "commands.tsv", [{
        "scenario": spec.scenario, "policy": spec.policy,
        "min_choices": spec.min_choices,
        "traffic_sha256": spec.traffic_sha256,
        "command": shlex.join(spec.command),
    } for spec in specs])
    if args.dry_run:
        print("validated 84 matrix cells")
        return 0
    if not args.sim.exists():
        raise FileNotFoundError(args.sim)
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
