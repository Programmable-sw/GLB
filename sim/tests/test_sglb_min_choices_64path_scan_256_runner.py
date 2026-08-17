#!/usr/bin/env python3

import importlib.util
import math
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = (
    ROOT / "experiments/n-mrc/"
    "run_sglb_min_choices_64path_scan_256.py"
)


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "sglb_min_choices_scan", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalized_command(spec):
    command = list(spec.command)
    for option in ("-o", "-sglb_min_choices"):
        index = command.index(option)
        del command[index:index + 2]
    return tuple(command)


def test_default_matrix(runner):
    with tempfile.TemporaryDirectory() as temporary:
        args = runner.parse_args([
            "--out", temporary,
            "--dry-run",
        ])
        specs = runner.make_specs(args)
    assert runner.MIN_CHOICES == (16, 20, 24, 28, 32, 36, 40)
    assert runner.SEEDS == (13, 29, 47)
    assert len(runner.WORKLOADS) == 4
    assert len(specs) == 84
    assert {spec.min_choices for spec in specs} == set(runner.MIN_CHOICES)
    assert {spec.seed for spec in specs} == set(runner.SEEDS)
    assert {spec.workload for spec in specs} == set(runner.WORKLOADS)
    runner.validate_specs(specs, args)

    blocks = {}
    for item in specs:
        blocks.setdefault((item.workload, item.seed), []).append(item)
    assert len(blocks) == 12
    for items in blocks.values():
        assert len(items) == 7
        assert len({item.traffic_sha256 for item in items}) == 1
        assert len({normalized_command(item) for item in items}) == 1


def synthetic_rows(runner):
    rows = []
    a2a_cct = {
        16: 98.0,
        20: 90.0,
        24: 100.0,
        28: 92.0,
        32: 94.0,
        36: 97.0,
        40: 101.0,
    }
    for seed in runner.SEEDS:
        for candidate in runner.MIN_CHOICES:
            for workload in runner.WORKLOADS:
                p99 = 100.0
                if workload == "healthy_websearch_100" and candidate == 20:
                    p99 = 102.0
                elif workload in (
                        "healthy_permutation_16mib",
                        "healthy_websearch_100") and candidate == 28:
                    p99 = 100.5
                cct = (
                    a2a_cct[candidate]
                    if workload == "periodic_background_a2a_p16_256mib"
                    else 100.0
                )
                rows.append({
                    "workload": workload,
                    "seed": seed,
                    "min_choices": candidate,
                    "cct_us": cct,
                    "mean_fct_us": cct / 2.0,
                    "p95_fct_us": p99 - 1.0,
                    "p99_fct_us": p99,
                    "p999_fct_us": p99 + 1.0,
                    "max_fct_us": p99 + 2.0,
                    "retransmissions": candidate * 10,
                    "rtos": candidate,
                    "trims": candidate * 5,
                    "ecn_marks": candidate * 20,
                    "queue_p99_fraction": candidate / 100.0,
                    "spine_queue_cv": 1.0 / candidate,
                    "avg_candidate_choices": float(candidate),
                    "nonbest_fraction": candidate / 64.0,
                    "avoid_fraction": candidate / 128.0,
                })
    return rows


def test_guardrails_and_ranking(runner):
    summary, seed_ratios = runner.summarize(synthetic_rows(runner))
    ranked, rankings, pareto = runner.rank_candidates(summary)
    by_candidate = {row["min_choices"]: row for row in ranked}
    assert not by_candidate[20]["eligible"]
    assert math.isclose(
        by_candidate[20]["healthy_websearch_p99_ratio"], 1.02)
    assert by_candidate[28]["eligible"]
    assert by_candidate[28]["selected"]
    assert len(seed_ratios) == 84
    assert {row["min_choices"] for row in rankings} == set(
        runner.MIN_CHOICES)
    assert {row["metric"] for row in rankings} == set(
        runner.RANKING_METRICS)
    assert {row["min_choices"] for row in pareto} == set(
        runner.MIN_CHOICES)
    assert all("dominated_by" in row for row in pareto)


def main():
    runner = load_runner()
    test_default_matrix(runner)
    test_guardrails_and_ranking(runner)


if __name__ == "__main__":
    main()
