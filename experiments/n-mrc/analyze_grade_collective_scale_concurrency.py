#!/usr/bin/env python3
"""Aggregate and validate the grade collective scale/concurrency sweep."""

import argparse
import csv
import math
from pathlib import Path
import statistics
import sys


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_grade_collective_scale_concurrency as runner  # noqa: E402


DIAGNOSTICS = (
    "QueueDiag.composite_ecn_marks",
    "QueueDiag.composite_trims",
    "RoceDiag.nacks",
    "StorProfileDiag.stor_level_changes",
    "StorProfileDiag.stor_all_zero_profiles",
    "StorProfileDiag.stor_all_zero_selections",
    "QueueCvDiag.spine_queue_cv",
)


def geomean(values):
    values = tuple(values)
    return math.exp(statistics.fmean(math.log(value) for value in values))


def write_csv(path, rows):
    rows = list(rows)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def validate(rows):
    expected = {
        (scenario.name, variant, str(seed))
        for scenario in runner.SCENARIOS
        for variant in runner.WEIGHTS
        for seed in runner.SEEDS
    }
    identities = [
        (row["scenario"], row["variant"], row["seed"]) for row in rows]
    if len(rows) != 108 or len(set(identities)) != 108:
        raise ValueError("results do not contain 108 unique cells")
    if set(identities) != expected:
        raise ValueError("results differ from expected matrix")
    for row in rows:
        primary = float(row["primary_us"])
        meta = runner.SCENARIO_META[row["scenario"]]
        if (
                row["returncode"] != "0" or row["config_ok"] != "1" or
                row["all_flows_completed"] != "1" or
                not math.isfinite(primary) or primary <= 0):
            raise ValueError("results contain an invalid cell")
        if (
                row["weights"] != "/".join(
                    runner.WEIGHTS[row["variant"]]) or
                row["primary_metric"] != "all_to_all_cct_us" or
                row["axis"] != meta["axis"] or
                int(row["parallel"]) != meta["parallel"] or
                int(row["flow_kib"]) != meta["flow_kib"] or
                not math.isclose(
                    float(row["per_source_mib"]),
                    meta["flow_kib"] * 127 / 1024,
                    rel_tol=1e-12)):
            raise ValueError("result metadata does not match scenario")
        for field in DIAGNOSTICS:
            if field not in row or row[field] == "":
                raise ValueError(f"result lacks diagnostic {field}")
            if not math.isfinite(float(row[field])):
                raise ValueError(f"result has non-finite diagnostic {field}")
    sim_hashes = {row["sim_sha256"] for row in rows}
    if len(sim_hashes) != 1 or not next(iter(sim_hashes)):
        raise ValueError("results do not share one simulator SHA")
    hashes = {}
    for row in rows:
        if not row["traffic_sha256"]:
            raise ValueError("result has an empty traffic hash")
        hashes.setdefault(
            (row["scenario"], row["seed"]), set()).add(
                row["traffic_sha256"])
    if any(len(values) != 1 for values in hashes.values()):
        raise ValueError("traffic differs inside a scenario/seed pair")


def summarize(rows):
    groups = {}
    for row in rows:
        groups.setdefault((row["scenario"], row["variant"]), []).append(row)
    output = []
    for (scenario, variant), selected in sorted(groups.items()):
        selected.sort(key=lambda row: int(row["seed"]))
        meta = runner.SCENARIO_META[scenario]
        item = {
            "scenario": scenario,
            "axis": meta["axis"],
            "parallel": meta["parallel"],
            "flow_kib": meta["flow_kib"],
            "per_source_mib": meta["flow_kib"] * 127 / 1024,
            "variant": variant,
            "weights": selected[0]["weights"],
            "cct_geomean_us": geomean(
                float(row["primary_us"]) for row in selected),
            "seed_cct_us": ";".join(
                f"{row['seed']}:{float(row['primary_us']):.6f}"
                for row in selected),
        }
        for field in DIAGNOSTICS:
            item[field] = statistics.fmean(
                float(row[field]) for row in selected)
        output.append(item)
    if len(output) != 36:
        raise ValueError("summary does not contain 36 groups")
    return output


def winners(summary, rows):
    by_scenario = {}
    raw = {}
    for row in summary:
        by_scenario.setdefault(row["scenario"], []).append(row)
    for row in rows:
        raw[(row["scenario"], row["variant"], row["seed"])] = row
    output = []
    for scenario, selected in sorted(by_scenario.items()):
        winner = min(selected, key=lambda row: row["cct_geomean_us"])
        equal = next(row for row in selected if row["variant"] == "equal")
        seed_wins = sum(
            float(raw[(scenario, winner["variant"], str(seed))]["primary_us"])
            < float(raw[(scenario, "equal", str(seed))]["primary_us"])
            for seed in runner.SEEDS)
        item = {
            "scenario": scenario,
            "axis": winner["axis"],
            "parallel": winner["parallel"],
            "flow_kib": winner["flow_kib"],
            "per_source_mib": winner["per_source_mib"],
            "winner": winner["variant"],
            "winner_cct_geomean_us": winner["cct_geomean_us"],
            "equal_cct_geomean_us": equal["cct_geomean_us"],
            "effect_vs_equal_percent":
                (winner["cct_geomean_us"] /
                 equal["cct_geomean_us"] - 1) * 100,
            "seeds_beating_equal": seed_wins,
            "supported_non_equal": int(
                winner["variant"] != "equal" and seed_wins >= 2),
        }
        for field in DIAGNOSTICS:
            item[f"winner_{field}"] = winner[field]
            item[f"equal_{field}"] = equal[field]
            denominator = equal[field]
            item[f"{field}_effect_percent"] = (
                (winner[field] / denominator - 1) * 100
                if denominator else 0)
        output.append(item)
    if len(output) != 9:
        raise ValueError("winner table does not contain nine scenarios")
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    rows = list(csv.DictReader((args.root / "results.csv").open()))
    validate(rows)
    summary = summarize(rows)
    winner_rows = winners(summary, rows)
    write_csv(args.root / "summary.csv", summary)
    write_csv(args.root / "winners.csv", winner_rows)
    supported = [row for row in winner_rows if row["supported_non_equal"]]
    print(f"validated 108 cells; supported non-equal winners={len(supported)}")
    for row in supported:
        print(
            row["scenario"], row["winner"],
            f"{row['effect_vs_equal_percent']:.3f}%",
            f"{row['seeds_beating_equal']}/3 seeds")


if __name__ == "__main__":
    main()
