#!/usr/bin/env python3
"""Two-stage single-seed proof for SGLB min choices and candidate policies."""

import argparse
import concurrent.futures
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
import run_sglb_min_choices_pressure_a2a_256 as pressure  # noqa: E402


MIN_CHOICES = base.MIN_CHOICES
POLICIES = ("strict_k", "whole_grade_min", "exact_min")
SCENARIOS = ("symmetric", "asymmetric")
SEED = 13
WORKLOAD = "periodic_background_a2a_p16_256mib"
DEFAULT_SIM = ROOT / "sim/datacenter/htsim_roce"
DEFAULT_OUT = SCRIPT_DIR / "output/sglb_min_policy_staged_256"
RANKING_METRICS = pressure.RANKING_METRICS


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


def _command(sim, traffic, output, connections, min_choices, scenario, policy):
    command = list(base.build_command(
        sim, traffic, output, connections, SEED, WORKLOAD, min_choices, 1.0))
    if scenario == "symmetric":
        command = command[:command.index("-sglb_background")]
    command.extend(("-sglb_candidate_policy", policy))
    return tuple(command)


def _materials(args):
    args.out = Path(args.out).resolve()
    args.sim = Path(args.sim).resolve()
    traffic, connections, flows = base.materialize_traffic(
        args.out, WORKLOAD, SEED, 1.0)
    return traffic, metrics.file_sha256(traffic), connections, flows


def _specs(args, choices, policies):
    traffic, digest, connections, flows = _materials(args)
    specs = []
    for scenario in SCENARIOS:
        for choice in choices:
            for policy in policies:
                label = f"{scenario}_min{choice}_{policy}"
                case_dir = args.out / "runs" / label
                output = case_dir / "logout.dat"
                specs.append(CellSpec(
                    WORKLOAD, scenario, policy, SEED, choice, traffic, digest,
                    connections, flows, case_dir, output,
                    _command(args.sim, traffic, output, connections, choice,
                             scenario, policy)))
    return specs


def make_min_specs(args):
    return _specs(args, MIN_CHOICES, ("exact_min",))


def make_policy_specs(args, selected_min):
    if selected_min not in MIN_CHOICES:
        raise ValueError("selected min is outside the registered scan")
    return _specs(args, (selected_min,), POLICIES)


def validate_specs(specs):
    seen = set()
    traffic_hashes = set()
    for spec in specs:
        key = (spec.scenario, spec.min_choices, spec.policy)
        if key in seen:
            raise ValueError(f"duplicate cell {key}")
        seen.add(key)
        traffic_hashes.add(spec.traffic_sha256)
        command = list(spec.command)
        expected = {
            "-nodes": "256", "-paths": "64", "-conns": "65280",
            "-sglb_min_choices": str(spec.min_choices),
            "-sglb_candidate_policy": spec.policy, "-end": "100000",
        }
        for option, value in expected.items():
            if command[command.index(option) + 1] != value:
                raise ValueError(f"cell {key}: incorrect {option}")
        has_background = "-sglb_background" in command
        if has_background != (spec.scenario == "asymmetric"):
            raise ValueError(f"cell {key}: background mismatch")
        if has_background:
            for option, value in {
                    "-sglb_bg_links_per_direction": "2",
                    "-sglb_bg_rate_gbps": "350",
                    "-sglb_bg_on_us": "400",
                    "-sglb_bg_off_us": "400"}.items():
                if command[command.index(option) + 1] != value:
                    raise ValueError(f"cell {key}: incorrect {option}")
    if traffic_hashes and len(traffic_hashes) != 1:
        raise ValueError("foreground traffic differs across cells")


def select_min(rows):
    indexed = {(row["scenario"], int(row["min_choices"])): row for row in rows}
    if len(indexed) != 2 * len(MIN_CHOICES):
        raise ValueError("stage-one matrix is incomplete")
    symmetric_best = min(indexed[("symmetric", choice)]["cct_us"]
                         for choice in MIN_CHOICES)
    decisions = []
    eligible = []
    for choice in MIN_CHOICES:
        symmetric = indexed[("symmetric", choice)]
        asymmetric = indexed[("asymmetric", choice)]
        regression = symmetric["cct_us"] / symmetric_best - 1.0
        passed = regression <= 0.01 + 1e-12
        decision = {
            "min_choices": choice,
            "symmetric_cct_us": symmetric["cct_us"],
            "symmetric_regression_vs_best": regression,
            "symmetric_guardrail_pass": passed,
            "asymmetric_cct_us": asymmetric["cct_us"],
            "asymmetric_p99_fct_us": asymmetric["p99_fct_us"],
            "asymmetric_trims": asymmetric["trims"],
            "asymmetric_retransmissions": asymmetric["retransmissions"],
            "asymmetric_ecn_marks": asymmetric["ecn_marks"],
        }
        decisions.append(decision)
        if passed:
            eligible.append((
                asymmetric["cct_us"], asymmetric["p99_fct_us"],
                asymmetric["trims"], asymmetric["retransmissions"],
                asymmetric["ecn_marks"], choice))
    selected = min(eligible)[-1]
    for decision in decisions:
        decision["selected"] = decision["min_choices"] == selected
    return selected, decisions


def _rank(rows):
    rankings = []
    for scenario in SCENARIOS:
        candidates = [row for row in rows if row["scenario"] == scenario]
        for metric in RANKING_METRICS:
            ordered = sorted(candidates, key=lambda row: (
                float(row[metric]), row["min_choices"], row["policy"]))
            for rank, row in enumerate(ordered, 1):
                rankings.append({
                    "scenario": scenario, "metric": metric, "rank": rank,
                    "min_choices": row["min_choices"], "policy": row["policy"],
                    "value": row[metric],
                })
    return rankings


def _run_cell(spec, args, simulator_sha):
    row = base.run_cell(spec, args, simulator_sha)
    row.update({"scenario": spec.scenario, "policy": spec.policy})
    if hasattr(spec, "cadence"):
        row["cadence"] = spec.cadence
    base.atomic_write_text(
        spec.case_dir / "parsed.json",
        json.dumps(row, indent=2, sort_keys=True) + "\n")
    return row


def run_specs(specs, args, validator=validate_specs):
    validator(specs)
    simulator_sha = metrics.file_sha256(args.sim)
    rows = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(args.workers, len(specs))) as executor:
        futures = {executor.submit(
            _run_cell, spec, args, simulator_sha): spec for spec in specs}
        for future in concurrent.futures.as_completed(futures):
            spec = futures[future]
            try:
                row = future.result()
                rows.append(row)
                if (not base._valid_row(row) or row["gcn_stale"] != 0):
                    errors.append(f"invalid {spec.scenario}/min{spec.min_choices}/{spec.policy}")
            except Exception as error:
                errors.append(f"{spec.scenario}/min{spec.min_choices}/{spec.policy}: {error!r}")
    rows.sort(key=lambda row: (row["scenario"], row["min_choices"], row["policy"]))
    if errors:
        base.atomic_write_text(args.out / "errors.txt", "\n".join(errors) + "\n")
        raise RuntimeError("; ".join(errors))
    (args.out / "errors.txt").unlink(missing_ok=True)
    return rows


def _manifest(args, specs):
    return {
        "schema": 1, "seed": SEED, "nodes": 256, "paths": 64,
        "connections": 65280, "min_choices": list(MIN_CHOICES),
        "policies": list(POLICIES), "scenarios": list(SCENARIOS),
        "symmetric_cct_guardrail": 0.01,
        "traffic_sha256": specs[0].traffic_sha256,
        "simulator": str(args.sim),
        "simulator_sha256": metrics.file_sha256(args.sim) if args.sim.exists() else "dry-run",
        "source_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, check=False).stdout.strip(),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", type=Path, default=DEFAULT_SIM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stage", choices=("min", "policy", "all"), default="all")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--selected-min", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.out = Path(args.out).resolve()
    args.sim = Path(args.sim).resolve()
    if args.workers < 1 or args.timeout < 1:
        raise ValueError("workers and timeout must be positive")
    min_specs = make_min_specs(args)
    validate_specs(min_specs)
    args.out.mkdir(parents=True, exist_ok=True)
    base.atomic_write_text(
        args.out / "manifest.json",
        json.dumps(_manifest(args, min_specs), indent=2, sort_keys=True) + "\n")
    if args.dry_run:
        policy_specs = make_policy_specs(args, args.selected_min or 24)
        validate_specs(policy_specs)
        print(f"validated {len(min_specs)} stage-one and {len(policy_specs)} stage-two cells")
        return 0
    if not args.sim.exists():
        raise FileNotFoundError(args.sim)

    selected = args.selected_min
    if args.stage in ("min", "all"):
        min_rows = run_specs(min_specs, args)
        base.write_csv(args.out / "min_cells.csv", min_rows)
        selected, decisions = select_min(min_rows)
        base.write_csv(args.out / "min_decisions.csv", decisions)
        base.write_csv(args.out / "min_rankings.csv", _rank(min_rows))
        base.atomic_write_text(args.out / "selected_min.txt", f"{selected}\n")
        print(f"stage one selected min{selected}", flush=True)
    if args.stage in ("policy", "all"):
        if selected is None:
            selected_path = args.out / "selected_min.txt"
            if not selected_path.exists():
                raise ValueError("policy stage requires --selected-min or stage-one result")
            selected = int(selected_path.read_text().strip())
        policy_rows = run_specs(make_policy_specs(args, selected), args)
        base.write_csv(args.out / "policy_cells.csv", policy_rows)
        base.write_csv(args.out / "policy_rankings.csv", _rank(policy_rows))
        print(f"stage two completed at min{selected}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
