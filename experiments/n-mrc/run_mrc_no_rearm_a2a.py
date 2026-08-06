#!/usr/bin/env python3
"""Run the default non-rearming MRC implementation on the archived A2A matrix."""

import argparse
import concurrent.futures
import json
from pathlib import Path

import run_final_512_comparison as base


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "experiments/n-mrc/output/n-mrc-results"
DEFAULT_OUT = (
    ROOT / "experiments/n-mrc/output/mrc_one_cycle_no_rearm_a2a")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", default="13")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--sim", type=Path, default=base.DEFAULT_SIM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def selected_scenarios():
    return tuple(
        scenario for scenario in base.scenario_catalog(128)
        if scenario.kind == "all_to_all" and scenario.family in (
            "healthy_alltoall", "asymmetric_alltoall_background"))


def archived_artifact(scenario, seed):
    case_dir = (
        BASELINE / "raw" / scenario.name / "mrc" / f"seed_{seed}")
    archived = json.loads(
        (case_dir / "summary.json").read_text(encoding="utf-8"))
    traffic = (
        BASELINE / "traffic" / f"n128_{scenario.name}_seed{seed}.cm")
    traffic_sha = base.file_sha256(traffic)
    if traffic_sha != archived["traffic_sha256"]:
        raise ValueError(
            f"traffic hash mismatch for {scenario.name} seed {seed}")
    return base.TrafficArtifact(
        path=traffic,
        connections=int(archived["expected_flows"]),
        starts_by_flowid={},
        sha256=traffic_sha,
        serialized_bytes=traffic.stat().st_size,
    )


def validate(rows, specs):
    expected = {
        (spec.scenario.name, spec.scheme, spec.seed) for spec in specs}
    actual = {
        (row["scenario"], row["scheme"], row["seed"]) for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise ValueError(
            f"result matrix mismatch: missing={expected - actual}, "
            f"extra={actual - expected}")
    bad = [
        row for row in rows
        if row["returncode"] != 0 or not row["config_ok"] or
        not row["all_flows_completed"] or row["primary_us"] <= 0]
    if bad:
        raise ValueError(
            "invalid cells: " + ", ".join(
                f"{row['scenario']}:seed{row['seed']}" for row in bad))


def main():
    args = parse_args()
    args.out = args.out.resolve()
    args.sim = args.sim.resolve()
    seeds = tuple(int(value) for value in args.seeds.split(",") if value)
    if not seeds or args.workers < 1 or args.timeout < 1:
        raise ValueError("seeds, workers, and timeout must be positive")
    unknown = set(seeds) - set(base.CANONICAL_SEEDS)
    if unknown:
        raise ValueError(f"unsupported seeds: {sorted(unknown)}")

    args.out.mkdir(parents=True, exist_ok=True)
    output_lock = base.acquire_output_lock(args.out)
    scenarios = selected_scenarios()
    specs = tuple(
        base.CaseSpec(scenario, "mrc", seed)
        for scenario in scenarios for seed in seeds)
    artifacts = {
        (scenario.name, seed): archived_artifact(scenario, seed)
        for scenario in scenarios for seed in seeds
    }
    manifest = {
        "nodes": 128,
        "paths": 64,
        "seeds": seeds,
        "scheme": "mrc",
        "cooldown_mode": "one_cycle",
        "cooldown_rearm": False,
        "scenario_count": len(scenarios),
        "case_count": len(specs),
        "baseline": str(BASELINE),
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    rows = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(args.workers, len(specs))) as executor:
        futures = [
            executor.submit(
                base.run_case, spec,
                artifacts[(spec.scenario.name, spec.seed)], args, 128)
            for spec in specs
        ]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())
            base.write_csv(args.out / "results.partial.csv", rows)

    validate(rows, specs)
    rows.sort(key=lambda row: (row["scenario"], row["seed"]))
    base.write_csv(args.out / "results.csv", rows)
    (args.out / "COMPLETE").write_text(
        f"{len(rows)} validated cells\n", encoding="ascii")
    print(f"validated {len(rows)} cells; results={args.out / 'results.csv'}")
    output_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
