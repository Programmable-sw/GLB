#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/n-mrc/run_sglb_min_choices_two_a2a_256.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("two_a2a_scan", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    runner = load_runner()
    with tempfile.TemporaryDirectory() as temporary:
        args = runner.parse_args([
            "--out", temporary, "--dry-run", "--sample-scale", "0.05",
        ])
        specs = runner.make_specs(args)
        assert len(specs) == 28
        assert {item.scenario for item in specs} == set(runner.SCENARIOS)
        assert {item.seed for item in specs} == {13, 29}
        assert {item.min_choices for item in specs} == set(runner.MIN_CHOICES)
        for seed in runner.SEEDS:
            subset = [item for item in specs if item.seed == seed]
            assert len({item.traffic_sha256 for item in subset}) == 1

    rows = []
    for seed in runner.SEEDS:
        for choice in runner.MIN_CHOICES:
            for scenario in runner.SCENARIOS:
                rows.append({
                    "seed": seed, "min_choices": choice,
                    "scenario": scenario,
                    "cct_us": abs(choice - 28) + (2 if scenario.startswith("asymmetric") else 1),
                    "mean_fct_us": choice, "p95_fct_us": choice + 1,
                    "p99_fct_us": choice + 2, "max_fct_us": choice + 3,
                    "retransmissions": choice, "rtos": choice,
                    "trims": choice, "ecn_marks": choice,
                    "queue_p99_fraction": choice / 100,
                    "spine_queue_cv": 1 / choice,
                    "avg_candidate_choices": choice,
                    "nonbest_fraction": 0, "avoid_fraction": 0,
                })
    summary, rankings = runner.summarize(rows)
    assert len(summary) == 7
    assert next(row for row in summary if row["selected"])["min_choices"] == 28
    assert {row["metric"] for row in rankings} == set(runner.RANKING_METRICS)

    tied_rows = [dict(row, cct_us=100.0, mean_fct_us=50.0,
                      p95_fct_us=90.0, p99_fct_us=95.0,
                      max_fct_us=100.0, retransmissions=0, rtos=0,
                      trims=0, ecn_marks=0, queue_p99_fraction=0.02,
                      spine_queue_cv=0.5, avg_candidate_choices=63.5,
                      nonbest_fraction=0, avoid_fraction=0)
                 for row in rows]
    tied_summary, tied_rankings = runner.summarize(tied_rows)
    assert not any(row["selected"] for row in tied_summary)
    assert {row["selection_status"] for row in tied_summary} == {
        "indistinguishable"}
    assert {row["rank"] for row in tied_rankings
            if row["metric"] == "overall_cct_us"} == {1}


if __name__ == "__main__":
    main()
