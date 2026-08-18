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
        assert len(specs) == 168
        assert {spec.scenario for spec in specs} == set(runner.SCENARIOS)
        assert {spec.policy for spec in specs} == set(runner.POLICIES)
        assert {spec.min_choices for spec in specs} == set(runner.MIN_CHOICES)
        assert {spec.connections for spec in specs} == {65280}
        assert len({spec.traffic_sha256 for spec in specs}) == 2
        assert len({(spec.cadence, spec.scenario, spec.policy, spec.min_choices)
                    for spec in specs}) == 168
        by_load = {}
        for spec in specs:
            by_load.setdefault(spec.load, set()).add(spec.traffic_sha256)
        assert {load: len(hashes) for load, hashes in by_load.items()} == {
            "medium": 1, "high": 1}
        for spec in specs:
            assert option(spec.command, "-sglb_candidate_policy") == spec.policy
            assert "-sglb_candidate_dispatch" not in spec.command
            assert option(spec.command, "-sglb_gcn_cadence") == spec.cadence
            assert option(spec.command, "-sglb_min_choices") == str(
                spec.min_choices)
            assert ("-sglb_background" in spec.command) == spec.asymmetric
            assert spec.parallel == (16 if spec.load == "medium" else 32)
            assert spec.per_source_mib == (
                64 if spec.load == "medium" else 256)
            assert option(spec.command, "-end") == (
                "25000" if spec.load == "medium" else "100000")
        runner.validate_specs(specs)


def synthetic_row(runner, cadence, scenario, policy, choice, cct):
    row = {
        "cadence": cadence, "scenario": scenario, "policy": policy,
        "min_choices": choice,
        "cct_us": cct, "p99_fct_us": cct - 1,
        "trims": choice, "retransmissions": choice, "ecn_marks": choice,
    }
    for metric in runner.RANKING_METRICS:
        row.setdefault(metric, 0)
    return row


def test_each_policy_is_tuned_before_comparison(runner):
    rows = []
    optima = {"strict_k": 16, "whole_grade_min": 24, "exact_min": 32}
    for cadence in runner.CADENCES:
        for scenario in runner.SCENARIOS:
            for policy in runner.POLICIES:
                for choice in runner.MIN_CHOICES:
                    base = 100 if scenario.startswith("healthy_") else 200
                    rows.append(synthetic_row(
                        runner, cadence, scenario, policy, choice,
                        base + abs(choice - optima[policy])))
    tuned = runner.select_policy_optima(rows)
    assert len(tuned) == 12
    assert {(row["policy"], row["selected_min"])
            for row in tuned} == set(optima.items())


def test_execution_uses_matrix_validator(runner):
    def matrix_validator(_specs):
        raise RuntimeError("matrix-validator-reached")
    try:
        runner.staged.run_specs([], object(), matrix_validator)
    except RuntimeError as error:
        assert str(error) == "matrix-validator-reached"
    else:
        raise AssertionError("custom execution validator was not called")


def test_pressure_gate(runner):
    with tempfile.TemporaryDirectory() as temporary:
        args = runner.parse_args(["--out", temporary, "--dry-run"])
        gate = runner.make_gate_specs(args)
        assert len(gate) == 8
        assert {spec.policy for spec in gate} == {"exact_min"}
        assert {spec.min_choices for spec in gate} == {24}

    rows = []
    for cadence in runner.CADENCES:
        for scenario in runner.SCENARIOS:
            asymmetric = scenario.startswith("asymmetric_")
            rows.append({
                "cadence": cadence, "scenario": scenario, "config_ok": True,
                "all_flows_completed": True, "gcn_stale": 0,
                "cct_us": 2000,
                "avg_best_quality_choices": 30 if asymmetric else 60,
                "avg_candidate_choices": 30 if asymmetric else 60,
            })
    passed, _ = runner.evaluate_pressure_gate(rows)
    assert passed
    rows[-1]["avg_best_quality_choices"] = 63
    passed, _ = runner.evaluate_pressure_gate(rows)
    assert not passed


def main():
    runner = load_runner()
    test_complete_matrix(runner)
    test_each_policy_is_tuned_before_comparison(runner)
    test_execution_uses_matrix_validator(runner)
    test_pressure_gate(runner)


if __name__ == "__main__":
    main()
