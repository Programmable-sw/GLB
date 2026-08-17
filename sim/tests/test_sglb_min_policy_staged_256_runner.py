#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/n-mrc/run_sglb_min_policy_staged_256.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("staged_sglb", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def option(command, name):
    return command[command.index(name) + 1]


def test_stage_one_matrix(runner):
    with tempfile.TemporaryDirectory() as temporary:
        args = runner.parse_args(["--out", temporary, "--dry-run"])
        specs = runner.make_min_specs(args)
        assert len(specs) == 14
        assert {spec.scenario for spec in specs} == {"symmetric", "asymmetric"}
        assert {spec.min_choices for spec in specs} == set(runner.MIN_CHOICES)
        assert {spec.policy for spec in specs} == {"exact_min"}
        assert {spec.connections for spec in specs} == {65280}
        assert len({spec.traffic_sha256 for spec in specs}) == 1
        for spec in specs:
            assert option(spec.command, "-sglb_candidate_policy") == "exact_min"
            assert ("-sglb_background" in spec.command) == (
                spec.scenario == "asymmetric")
        runner.validate_specs(specs)


def row(scenario, min_choices, cct, policy="exact_min", p99=90.0):
    return {
        "scenario": scenario, "min_choices": min_choices, "policy": policy,
        "cct_us": cct, "p99_fct_us": p99, "trims": 0,
        "retransmissions": 0, "ecn_marks": 0,
    }


def test_min_selection_uses_guardrail_then_asymmetric_cct(runner):
    rows = []
    for choice in runner.MIN_CHOICES:
        symmetric = 100.0
        asymmetric = 200.0 + abs(choice - 32)
        if choice == 40:
            symmetric = 101.01
            asymmetric = 150.0
        rows.extend((row("symmetric", choice, symmetric),
                     row("asymmetric", choice, asymmetric)))
    selected, decisions = runner.select_min(rows)
    assert selected == 32
    assert not next(item for item in decisions if item["min_choices"] == 40)[
        "symmetric_guardrail_pass"]


def test_stage_two_fixes_selected_min(runner):
    with tempfile.TemporaryDirectory() as temporary:
        args = runner.parse_args(["--out", temporary, "--dry-run"])
        specs = runner.make_policy_specs(args, 28)
        assert len(specs) == 6
        assert {spec.min_choices for spec in specs} == {28}
        assert {spec.policy for spec in specs} == set(runner.POLICIES)
        assert {spec.scenario for spec in specs} == {"symmetric", "asymmetric"}
        for spec in specs:
            assert option(spec.command, "-sglb_candidate_policy") == spec.policy


def main():
    runner = load_runner()
    test_stage_one_matrix(runner)
    test_min_selection_uses_guardrail_then_asymmetric_cct(runner)
    test_stage_two_fixes_selected_min(runner)


if __name__ == "__main__":
    main()
