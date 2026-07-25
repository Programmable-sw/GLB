#!/usr/bin/env python3
"""Run seed-13 P=4 A2A cells for graded n-MRC selective cooldown."""

import argparse
import concurrent.futures
import json
from pathlib import Path

import run_final_512_comparison as base


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "experiments/n-mrc/output/n-mrc-results"
DEFAULT_OUT = (
    ROOT / "experiments/n-mrc/output/"
    "nmrc_graded_selective_cooldown_p4_seed13_20260725")
SCHEMES = ("ops", "reps", "mrc", "sglb", "n-mrc")
DIAG_FIELDS = (
    "HybridNmrcDiag.reroutes",
    "HybridNmrcDiag.graded_cooldown_requested",
    "HybridNmrcDiag.graded_cooldown_suppressed",
    "HybridNmrcDiag.cooldown_starts",
    "HybridNmrcDiag.cooling_skips",
    "HybridNmrcDiag.all_cooling_fallbacks",
    "QueueDiag.composite_ecn_marks",
    "QueueDiag.composite_trims",
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--sim", type=Path, default=base.DEFAULT_SIM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--list-only", action="store_true")
    return parser.parse_args()


def selected_scenarios():
    selected = tuple(
        scenario for scenario in base.scenario_catalog(128)
        if scenario.kind == "all_to_all" and scenario.parallel == 4 and
        scenario.family in (
            "healthy_alltoall", "asymmetric_alltoall_background"))
    if len(selected) != 6:
        raise ValueError(f"expected six P=4 A2A scenarios, got {len(selected)}")
    return selected


def archived_summary(scenario, scheme, seed):
    path = (
        BASELINE / "raw" / scenario.name / scheme /
        f"seed_{seed}" / "summary.json")
    return json.loads(path.read_text(encoding="utf-8"))


def archived_artifact(scenario, seed):
    archived = archived_summary(scenario, "n-mrc", seed)
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
        (row["scenario"], row["scheme"], int(row["seed"])) for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise ValueError(
            f"result matrix mismatch: missing={expected - actual}, "
            f"extra={actual - expected}")
    bad = [
        row for row in rows
        if int(row["returncode"]) != 0 or not int(row["config_ok"]) or
        not int(row["all_flows_completed"]) or
        int(row["completed"]) != 16256 or float(row["primary_us"]) <= 0
    ]
    if bad:
        raise ValueError(
            "invalid cells: " + ", ".join(
                f"{row['scenario']}:seed{row['seed']}" for row in bad))


def write_comparison(path, rows, scenarios, seed):
    current = {row["scenario"]: row for row in rows}
    output = []
    for scenario in scenarios:
        new = current[scenario.name]
        new_cct = float(new["primary_us"])
        diagnostics = json.loads(new["diagnostics_json"])
        item = {
            "scenario": scenario.name,
            "family": scenario.family,
            "size_mib": scenario.size_mib,
            "parallel": scenario.parallel,
            "seed": seed,
            "selective_cooldown_cct_us": new_cct,
        }
        for scheme in SCHEMES:
            old = archived_summary(scenario, scheme, seed)
            old_cct = float(old["primary_us"])
            item[f"archived_{scheme}_cct_us"] = old_cct
            item[f"vs_{scheme}_pct"] = (new_cct / old_cct - 1.0) * 100.0
            if scheme == "n-mrc":
                old_diagnostics = json.loads(old["diagnostics_json"])
        for field in DIAG_FIELDS:
            current_value = diagnostics.get(field, 0)
            item[field] = current_value
            old_value = old_diagnostics.get(field, 0)
            item[f"archived_{field}"] = old_value
            item[f"{field}_vs_archived_pct"] = (
                (current_value / old_value - 1.0) * 100.0
                if old_value else 0.0)
        requested = int(
            diagnostics.get(
                "HybridNmrcDiag.graded_cooldown_requested", 0))
        suppressed = int(
            diagnostics.get(
                "HybridNmrcDiag.graded_cooldown_suppressed", 0))
        item["cooldown_request_share"] = (
            requested / (requested + suppressed)
            if requested + suppressed else 0.0)
        output.append(item)
    base.write_csv(path, output)


def main():
    args = parse_args()
    if args.seed not in base.CANONICAL_SEEDS:
        raise ValueError(f"unsupported seed {args.seed}")
    if args.workers < 1 or args.timeout < 1:
        raise ValueError("workers and timeout must be positive")
    args.out = args.out.resolve()
    args.sim = args.sim.resolve()
    scenarios = selected_scenarios()
    specs = tuple(
        base.CaseSpec(scenario, "n-mrc", args.seed)
        for scenario in scenarios)
    artifacts = {
        scenario.name: archived_artifact(scenario, args.seed)
        for scenario in scenarios
    }

    manifest = {
        "nodes": 128,
        "paths": 8,
        "seed": args.seed,
        "scheme": "n-mrc",
        "parallel": 4,
        "graded_reroute": "better_ge3",
        "cooldown_gap_threshold": 0.25,
        "scenario_count": len(scenarios),
        "case_count": len(specs),
        "baseline": str(BASELINE),
        "sim": str(args.sim),
        "traffic_sha256": {
            name: artifact.sha256 for name, artifact in artifacts.items()},
    }
    if args.list_only:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    output_lock = base.acquire_output_lock(args.out)
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    rows = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(args.workers, len(specs))) as executor:
        futures = [
            executor.submit(
                base.run_case, spec, artifacts[spec.scenario.name],
                args, 128)
            for spec in specs
        ]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())
            base.write_csv(args.out / "results.partial.csv", rows)

    validate(rows, specs)
    rows.sort(key=lambda row: (row["size_mib"], row["family"]))
    base.write_csv(args.out / "results.csv", rows)
    write_comparison(
        args.out / "comparison.csv", rows, scenarios, args.seed)
    (args.out / "COMPLETE").write_text(
        f"{len(rows)} validated cells\n", encoding="ascii")
    print(f"validated {len(rows)} cells; output={args.out}", flush=True)
    output_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
