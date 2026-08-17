#!/usr/bin/env python3
"""Focused SGLB min-choice scan using symmetric and asymmetric A2A."""

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
from dataclasses import dataclass


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import experiment_metrics as metrics  # noqa: E402
import feedback_eval_common as common  # noqa: E402
import run_sglb_min_choices_64path_scan_256 as base  # noqa: E402


NODES = 256
PATHS = 64
TOPOLOGY = common.TOPOLOGIES[NODES]
MIN_CHOICES = base.MIN_CHOICES
SEEDS = (13, 29)
SCENARIOS = (
    "symmetric_a2a_p16",
    "asymmetric_half_capacity_a2a_p16",
)
DEFAULT_SIM = ROOT / "sim/datacenter/htsim_roce"
DEFAULT_OUT = (
    SCRIPT_DIR / "output/sglb_min_choices_two_a2a_256_quick05")
RANKING_METRICS = (
    "overall_cct_us", "symmetric_cct_us", "asymmetric_cct_us",
    "overall_mean_fct_us", "overall_p95_fct_us", "overall_p99_fct_us",
    "overall_max_fct_us", "retransmissions", "rtos", "trims",
    "ecn_marks", "queue_p99_fraction", "spine_queue_cv",
    "avg_candidate_choices", "nonbest_fraction", "avoid_fraction",
)


@dataclass(frozen=True)
class A2ASpec:
    scenario: str
    workload: str
    seed: int
    min_choices: int
    traffic: Path
    traffic_sha256: str
    connections: int
    flows_data: object
    case_dir: Path
    output: Path
    command: tuple


def _selection(raw, allowed, name):
    values = tuple(int(item) for item in raw.split(",") if item)
    if not values or any(item not in allowed for item in values):
        raise ValueError(f"{name} must be selected from {allowed}")
    return values


def _materialize(out, seed, sample_scale):
    traffic_dir = Path(out) / "traffic"
    traffic_dir.mkdir(parents=True, exist_ok=True)
    path = traffic_dir / f"a2a_p16_sample{sample_scale:g}_seed{seed}.cm"
    per_source = max(NODES, int(round(256 * common.MIB * sample_scale)))
    plan = common.alltoall_plan(
        NODES, NODES, 16, per_source // NODES, 0, seed)
    if not path.exists():
        common.write_alltoall_matrix(path, plan)
    return path, metrics.file_sha256(path), plan.connection_count


def _set_option(command, option, value):
    command = list(command)
    command[command.index(option) + 1] = str(value)
    return command


def _build_command(sim, traffic, output, connections, seed, scenario,
                   choice, sample_scale):
    # Use the focused runner's fixed SGLB base without periodic background.
    command = list(base.build_command(
        sim, traffic, output, connections, seed,
        base.WORKLOADS[0], choice, 1.0))
    end_us = 100000 if sample_scale == 1.0 else max(
        5000, int(round(100000 * sample_scale)))
    command = _set_option(command, "-end", end_us)
    if scenario == SCENARIOS[1]:
        command.extend([
            "-slow_tor_uplinks", str(common.slow_uplink_count(TOPOLOGY)),
            "-slow_tor_uplink_divisor", "2",
            "-slow_tor_uplink_select", "random-sparse",
        ])
    return tuple(command)


def make_specs(args):
    seeds = _selection(args.seeds, SEEDS, "seeds")
    choices = _selection(args.min_choices, MIN_CHOICES, "min choices")
    args.out = Path(args.out).resolve()
    args.sim = Path(args.sim).resolve()
    traffic = {
        seed: _materialize(args.out, seed, args.sample_scale)
        for seed in seeds
    }
    specs = []
    for scenario in SCENARIOS:
        for seed in seeds:
            path, digest, connections = traffic[seed]
            for choice in choices:
                case_dir = (
                    args.out / "runs" / scenario /
                    f"seed{seed}_min{choice}")
                output = case_dir / "logout.dat"
                command = _build_command(
                    args.sim, path, output, connections, seed, scenario,
                    choice, args.sample_scale)
                specs.append(A2ASpec(
                    scenario, scenario, seed, choice, path, digest,
                    connections, None, case_dir, output, command))
    return specs


def geometric_mean(values):
    values = list(values)
    if not values:
        return 0.0
    if any(value <= 0 for value in values):
        return sum(values) / len(values)
    return math.exp(sum(math.log(value) for value in values) / len(values))


def summarize(rows):
    rows = list(rows)
    summary = []
    metric_sources = {
        "overall_mean_fct_us": "mean_fct_us",
        "overall_p95_fct_us": "p95_fct_us",
        "overall_p99_fct_us": "p99_fct_us",
        "overall_max_fct_us": "max_fct_us",
        "retransmissions": "retransmissions", "rtos": "rtos",
        "trims": "trims", "ecn_marks": "ecn_marks",
        "queue_p99_fraction": "queue_p99_fraction",
        "spine_queue_cv": "spine_queue_cv",
        "avg_candidate_choices": "avg_candidate_choices",
        "nonbest_fraction": "nonbest_fraction",
        "avoid_fraction": "avoid_fraction",
    }
    for choice in sorted({row["min_choices"] for row in rows}):
        candidate = [row for row in rows if row["min_choices"] == choice]
        symmetric = [row for row in candidate if row["scenario"] == SCENARIOS[0]]
        asymmetric = [row for row in candidate if row["scenario"] == SCENARIOS[1]]
        item = {
            "min_choices": choice,
            "nominal_floor_fraction": choice / PATHS,
            "symmetric_cct_us": geometric_mean(row["cct_us"] for row in symmetric),
            "asymmetric_cct_us": geometric_mean(row["cct_us"] for row in asymmetric),
            "overall_cct_us": geometric_mean(row["cct_us"] for row in candidate),
        }
        for output, source in metric_sources.items():
            item[output] = geometric_mean(row[source] for row in candidate)
        item["selected"] = False
        summary.append(item)
    winner = min(summary, key=lambda row: (
        row["overall_cct_us"], row["overall_p99_fct_us"],
        row["trims"], row["retransmissions"], row["min_choices"]))
    winner["selected"] = True
    summary.sort(key=lambda row: (not row["selected"], row["overall_cct_us"]))
    rankings = []
    for metric in RANKING_METRICS:
        ordered = sorted(summary, key=lambda row: (row[metric], row["min_choices"]))
        for rank, row in enumerate(ordered, 1):
            rankings.append({
                "metric": metric, "rank": rank,
                "min_choices": row["min_choices"], "value": row[metric],
            })
    return summary, rankings


def _report(path, summary, rankings, sample_scale):
    winner = next(row for row in summary if row["selected"])
    lines = [
        "# SGLB min-choice: two-A2A focused scan", "",
        f"Selected **min{winner['min_choices']}** by the geometric-mean CCT "
        "across symmetric and asymmetric A2A.", "",
        f"This is a quick screening scan with `sample_scale={sample_scale:g}`. "
        "Each source sends {:.3g} MiB rather than 256 MiB; topology, 65,280 "
        "ordered pairs, p16 scheduling, and all seven min-choice candidates "
        "are retained.".format(256 * sample_scale), "",
        "The asymmetric case halves the capacity of the canonical 3% "
        f"({common.slow_uplink_count(TOPOLOGY)}/{TOPOLOGY.leaves * TOPOLOGY.spines}) "
        "random-sparse ToR-to-Spine uplinks.", "",
        "## Summary", "",
        base._markdown_table(summary, (
            "min_choices", "selected", "overall_cct_us",
            "symmetric_cct_us", "asymmetric_cct_us",
            "overall_p99_fct_us", "retransmissions", "trims",
            "ecn_marks", "spine_queue_cv", "avg_candidate_choices")), "",
        "## Complete rankings", "",
        base._markdown_table(
            sorted(rankings, key=lambda row: (row["metric"], row["rank"])),
            ("metric", "rank", "min_choices", "value")), "",
        "This focused scan no longer enforces the healthy P2P/WebSearch 1% "
        "guardrail; those workloads were removed at the user's direction.",
    ]
    base.atomic_write_text(path, "\n".join(lines) + "\n")


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", type=Path, default=DEFAULT_SIM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    parser.add_argument("--min-choices", default=",".join(map(str, MIN_CHOICES)))
    parser.add_argument("--sample-scale", type=float, default=0.05)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if (not 0 < args.sample_scale <= 1 or args.workers < 1
            or args.timeout < 1):
        raise ValueError("invalid sample scale, worker count, or timeout")
    args.out = args.out.resolve()
    args.sim = args.sim.resolve()
    specs = make_specs(args)
    commands = [{
        "scenario": spec.scenario, "seed": spec.seed,
        "min_choices": spec.min_choices,
        "traffic_sha256": spec.traffic_sha256,
        "command": shlex.join(spec.command),
    } for spec in specs]
    base.write_csv(args.out / "commands.tsv", commands)
    if args.dry_run:
        print(f"validated {len(specs)} two-A2A cells")
        return 0
    if not args.sim.exists():
        raise FileNotFoundError(args.sim)
    sim_sha = metrics.file_sha256(args.sim)
    manifest = {
        "schema": 1, "scenarios": SCENARIOS, "seeds": sorted({s.seed for s in specs}),
        "min_choices": MIN_CHOICES, "sample_scale": args.sample_scale,
        "per_source_message_mib": 256 * args.sample_scale,
        "connections_per_cell": specs[0].connections,
        "asymmetric_slow_uplinks": common.slow_uplink_count(TOPOLOGY),
        "asymmetric_capacity_divisor": 2,
        "simulator_sha256": sim_sha,
        "source_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, check=False).stdout.strip(),
    }
    base.atomic_write_text(
        args.out / "manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    rows = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(args.workers, len(specs))) as executor:
        futures = {executor.submit(base.run_cell, spec, args, sim_sha): spec
                   for spec in specs}
        for future in concurrent.futures.as_completed(futures):
            spec = futures[future]
            try:
                row = future.result()
                row["scenario"] = spec.scenario
                rows.append(row)
                if not base._valid_row(row):
                    errors.append(f"invalid {spec.scenario}/seed{spec.seed}/min{spec.min_choices}")
            except Exception as error:
                errors.append(
                    f"{spec.scenario}/seed{spec.seed}/min{spec.min_choices}: {error!r}")
    rows.sort(key=lambda row: (row["scenario"], row["seed"], row["min_choices"]))
    base.write_csv(args.out / "cells.csv", rows)
    if errors:
        base.atomic_write_text(args.out / "errors.txt", "\n".join(errors) + "\n")
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    summary, rankings = summarize(rows)
    base.write_csv(args.out / "summary.csv", summary)
    base.write_csv(args.out / "rankings.csv", rankings)
    _report(args.out / "report.md", summary, rankings, args.sample_scale)
    winner = next(row for row in summary if row["selected"])
    print(f"selected min{winner['min_choices']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
