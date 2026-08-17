#!/usr/bin/env python3
"""Focused full-volume periodic-background A2A scan for SGLB min choices."""

import argparse
import concurrent.futures
import json
import math
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


MIN_CHOICES = base.MIN_CHOICES
SEED = 13
WORKLOAD = "periodic_background_a2a_p16_256mib"
DEFAULT_SIM = ROOT / "sim/datacenter/htsim_roce"
DEFAULT_OUT = (
    SCRIPT_DIR / "output/sglb_min_choices_pressure_a2a_256")
RANKING_METRICS = (
    "cct_us", "mean_fct_us", "p50_fct_us", "p95_fct_us",
    "p99_fct_us", "p999_fct_us", "max_fct_us", "retransmissions",
    "rtos", "trims", "ecn_marks", "queue_p95_fraction",
    "queue_p99_fraction", "spine_queue_cv", "spine_queue_avg",
    "avg_available_choices", "avg_best_quality_choices",
    "avg_candidate_choices", "nonbest_fraction", "avoid_fraction",
    "gcn_packets", "gcn_bytes", "gcn_deliveries",
    "gcn_profile_updates", "remote_snapshot_missing", "gcn_stale",
)


@dataclass(frozen=True)
class CellSpec:
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


def _choices(raw):
    values = tuple(int(item) for item in raw.split(",") if item)
    if not values or any(item not in MIN_CHOICES for item in values):
        raise ValueError("min choices must be selected from 16,20,24,28,32,36,40")
    return values


def make_specs(args):
    args.out = Path(args.out).resolve()
    args.sim = Path(args.sim).resolve()
    traffic, connections, flows = base.materialize_traffic(
        args.out, WORKLOAD, SEED, 1.0)
    digest = metrics.file_sha256(traffic)
    specs = []
    for choice in _choices(args.min_choices):
        case_dir = args.out / "runs" / f"seed{SEED}_min{choice}"
        output = case_dir / "logout.dat"
        command = base.build_command(
            args.sim, traffic, output, connections, SEED, WORKLOAD,
            choice, 1.0)
        specs.append(CellSpec(
            WORKLOAD, SEED, choice, traffic, digest, connections, flows,
            case_dir, output, command))
    return specs


def validate_specs(specs, args):
    base.validate_specs(specs, args)
    if {spec.connections for spec in specs} != {65280}:
        raise ValueError("pressure A2A must contain 65,280 flows")
    if len({spec.traffic_sha256 for spec in specs}) != 1:
        raise ValueError("all candidates must reuse identical traffic")
    for spec in specs:
        command = list(spec.command)
        required = {
            "-end": "100000",
            "-sglb_bg_links_per_direction": "2",
            "-sglb_bg_rate_gbps": "350",
            "-sglb_bg_on_us": "400",
            "-sglb_bg_off_us": "400",
        }
        if "-sglb_background" not in command:
            raise ValueError("periodic SGLB background is missing")
        for option, expected in required.items():
            if command[command.index(option) + 1] != expected:
                raise ValueError(f"incorrect {option}")


def _same(left, right):
    return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)


def summarize(rows):
    summary = []
    for source in sorted(rows, key=lambda row: row["min_choices"]):
        row = {
            "min_choices": source["min_choices"],
            "nominal_floor_fraction": source["min_choices"] / 64,
            **{metric: source[metric] for metric in RANKING_METRICS},
            "selected": False,
        }
        row["observed_candidate_fraction"] = (
            row["avg_candidate_choices"] / 64)
        row["floor_observed_active"] = int(
            row["avg_candidate_choices"] < 63.5)
        summary.append(row)

    best_key = min((
        row["cct_us"], row["p99_fct_us"], row["trims"],
        row["retransmissions"], row["ecn_marks"])
        for row in summary)
    winners = [row for row in summary if all(_same(value, best) for value, best in zip((
        row["cct_us"], row["p99_fct_us"], row["trims"],
        row["retransmissions"], row["ecn_marks"]), best_key))]
    if len(winners) == 1:
        winners[0]["selected"] = True
    for row in summary:
        row["selection_status"] = (
            "winner" if row["selected"] else
            "indistinguishable" if len(winners) > 1 else "not_selected")
    summary.sort(key=lambda row: (
        not row["selected"], row["cct_us"], row["min_choices"]))

    rankings = []
    for metric in RANKING_METRICS:
        ordered = sorted(summary, key=lambda row: (
            float(row[metric]), row["min_choices"]))
        previous = None
        rank = 0
        for position, row in enumerate(ordered, 1):
            value = float(row[metric])
            if previous is None or not _same(value, previous):
                rank = position
                previous = value
            rankings.append({
                "metric": metric, "rank": rank,
                "min_choices": row["min_choices"], "value": row[metric],
            })
    return summary, rankings


def _write_report(path, summary, rankings):
    winner = next((row for row in summary if row["selected"]), None)
    if winner:
        conclusion = (
            f"Selected **min{winner['min_choices']}** by CCT with p99, TRIM, "
            "retransmission, and ECN tie-breaks.")
    else:
        conclusion = (
            "**No unique winner:** the best candidates remain tied across all "
            "selection fields; retain min24 unless stronger evidence separates them.")
    minimum_candidates = min(
        row["avg_candidate_choices"] for row in summary)
    pressure = (
        "The observed candidate set entered the tested range, so the minimum "
        "can affect at least part of this run."
        if minimum_candidates <= max(MIN_CHOICES) else
        "The observed candidate set stayed above min40; this run does not "
        "exercise the configured lower bound strongly enough.")
    lines = [
        "# SGLB high-pressure A2A min-choice scan", "", conclusion, "",
        "One seed (13), 256 hosts, 64 paths, global p16 A2A, full 256 MiB "
        "per source, and periodic 350 Gbit/s background at 400 us ON/OFF.",
        "", pressure, "", "## Candidate summary", "",
        base._markdown_table(summary, (
            "min_choices", "selection_status", "cct_us", "p99_fct_us",
            "retransmissions", "rtos", "trims", "ecn_marks",
            "spine_queue_cv", "queue_p99_fraction",
            "avg_candidate_choices", "observed_candidate_fraction")),
        "", "## Complete rankings", "",
        base._markdown_table(
            sorted(rankings, key=lambda row: (row["metric"], row["rank"])),
            ("metric", "rank", "min_choices", "value")), "",
        "This is a focused one-seed result, not a universal optimum.",
    ]
    base.atomic_write_text(path, "\n".join(lines) + "\n")


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", type=Path, default=DEFAULT_SIM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-choices", default=",".join(map(str, MIN_CHOICES)))
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gate", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.workers < 1 or args.timeout < 1:
        raise ValueError("workers and timeout must be positive")
    specs = make_specs(args)
    validate_specs(specs, args)
    if args.gate:
        specs = [spec for spec in specs if spec.min_choices == 24]
        if len(specs) != 1:
            raise ValueError("gate requires min24 in the requested choices")
    args.out.mkdir(parents=True, exist_ok=True)
    base.write_csv(args.out / "commands.tsv", [{
        "seed": spec.seed, "min_choices": spec.min_choices,
        "traffic_sha256": spec.traffic_sha256,
        "command": shlex.join(spec.command),
    } for spec in specs])
    sim_sha = metrics.file_sha256(args.sim) if args.sim.exists() else "dry-run"
    manifest = {
        "schema": 1, "workload": WORKLOAD, "seed": SEED,
        "nodes": 256, "paths": 64, "parallel": 16,
        "per_source_message_mib": 256,
        "connections": specs[0].connections,
        "min_choices": [spec.min_choices for spec in specs],
        "background": {
            "links_per_direction": 2, "rate_gbps": 350,
            "on_us": 400, "off_us": 400,
        },
        "simulator": str(args.sim), "simulator_sha256": sim_sha,
        "traffic_sha256": specs[0].traffic_sha256,
        "source_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, check=False).stdout.strip(),
    }
    base.atomic_write_text(
        args.out / "manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if args.dry_run:
        print(f"validated {len(specs)} pressure-A2A cells")
        return 0
    if not args.sim.exists():
        raise FileNotFoundError(args.sim)

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
                rows.append(row)
                if (not base._valid_row(row) or row["gcn_packets"] <= 0
                        or row["gcn_stale"] != 0
                        or row["remote_snapshot_missing"] != 0):
                    errors.append(f"invalid seed{spec.seed}/min{spec.min_choices}")
            except Exception as error:
                errors.append(
                    f"seed{spec.seed}/min{spec.min_choices}: {error!r}")
    rows.sort(key=lambda row: row["min_choices"])
    base.write_csv(args.out / "cells.csv", rows)
    if errors:
        base.atomic_write_text(args.out / "errors.txt", "\n".join(errors) + "\n")
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    (args.out / "errors.txt").unlink(missing_ok=True)
    if args.gate:
        runtime = rows[0]["runtime_s"]
        if runtime > 900:
            print(f"gate exceeded 900s: {runtime:.3f}s", file=sys.stderr)
            return 2
        print(f"gate passed runtime={runtime:.3f}s")
        return 0

    summary, rankings = summarize(rows)
    base.write_csv(args.out / "summary.csv", summary)
    base.write_csv(args.out / "rankings.csv", rankings)
    _write_report(args.out / "report.md", summary, rankings)
    winner = next((row for row in summary if row["selected"]), None)
    print(f"selected min{winner['min_choices']}" if winner else
          "no unique winner; retain min24")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
