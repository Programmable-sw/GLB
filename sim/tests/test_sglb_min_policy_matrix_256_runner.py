#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/n-mrc/run_sglb_min_policy_matrix_256.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("sglb_matrix", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def option(command, name):
    return command[command.index(name) + 1]


def test_complete_matrix(runner):
    with tempfile.TemporaryDirectory() as temporary:
        args = runner.parse_args(["--out", temporary, "--dry-run"])
        specs = runner.make_specs(args)
        assert len(specs) == 84
        assert {spec.scenario for spec in specs} == set(runner.SCENARIOS)
        assert {spec.policy for spec in specs} == set(runner.POLICIES)
        assert {spec.min_choices for spec in specs} == set(runner.MIN_CHOICES)
        assert {spec.connections for spec in specs} == {65280}
        assert len({spec.traffic_sha256 for spec in specs}) == 2
        assert len({(spec.scenario, spec.policy, spec.min_choices)
                    for spec in specs}) == 84
        by_load = {}
        for spec in specs:
            by_load.setdefault(spec.load, set()).add(spec.traffic_sha256)
        assert {load: len(hashes) for load, hashes in by_load.items()} == {
            "medium": 1, "high": 1}
        for spec in specs:
            assert option(spec.command, "-sglb_candidate_policy") == spec.policy
            assert option(spec.command, "-sglb_min_choices") == str(
                spec.min_choices)
            assert ("-sglb_background" in spec.command) == spec.asymmetric
            assert spec.parallel == (16 if spec.load == "medium" else 32)
        runner.validate_specs(specs)


def synthetic_row(runner, scenario, policy, choice, cct):
    row = {
        "scenario": scenario, "policy": policy, "min_choices": choice,
        "cct_us": cct, "p99_fct_us": cct - 1,
        "trims": choice, "retransmissions": choice, "ecn_marks": choice,
    }
    for metric in runner.RANKING_METRICS:
        row.setdefault(metric, 0)
    return row


def test_each_policy_is_tuned_before_comparison(runner):
    rows = []
    optima = {"strict_k": 16, "whole_grade_min": 24, "exact_min": 32}
    for scenario in runner.SCENARIOS:
        for policy in runner.POLICIES:
            for choice in runner.MIN_CHOICES:
                base = 100 if scenario == "symmetric" else 200
                rows.append(synthetic_row(
                    runner, scenario, policy, choice,
                    base + abs(choice - optima[policy])))
    tuned = runner.select_policy_optima(rows)
    assert len(tuned) == 6
    assert {(row["policy"], row["selected_min"])
            for row in tuned} == set(optima.items())


def main():
    runner = load_runner()
    test_complete_matrix(runner)
    test_each_policy_is_tuned_before_comparison(runner)


if __name__ == "__main__":
    main()
