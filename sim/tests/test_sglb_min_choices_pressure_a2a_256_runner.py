#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
RUNNER = (
    ROOT / "experiments/n-mrc/"
    "run_sglb_min_choices_pressure_a2a_256.py"
)


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "pressure_a2a_scan", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def option(command, name):
    index = command.index(name)
    return command[index + 1]


def test_matrix(runner):
    with tempfile.TemporaryDirectory() as temporary:
        args = runner.parse_args(["--out", temporary, "--dry-run"])
        specs = runner.make_specs(args)
        assert runner.MIN_CHOICES == (16, 20, 24, 28, 32, 36, 40)
        assert len(specs) == 7
        assert {item.seed for item in specs} == {13}
        assert {item.connections for item in specs} == {65280}
        assert len({item.traffic_sha256 for item in specs}) == 1
        runner.validate_specs(specs, args)
        for item in specs:
            assert "-sglb_background" in item.command
            assert option(item.command, "-sglb_bg_links_per_direction") == "2"
            assert option(item.command, "-sglb_bg_rate_gbps") == "350"
            assert option(item.command, "-sglb_bg_on_us") == "400"
            assert option(item.command, "-sglb_bg_off_us") == "400"
            assert option(item.command, "-end") == "100000"
            assert option(item.command, "-sglb_min_choices") == str(
                item.min_choices)


def synthetic_rows(runner, unique=False):
    rows = []
    for choice in runner.MIN_CHOICES:
        cct = 100.0 + (abs(choice - 28) if unique else 0)
        row = {
            "seed": 13, "min_choices": choice,
            "cct_us": cct, "mean_fct_us": 50.0,
            "p95_fct_us": 90.0, "p99_fct_us": 95.0,
            "p999_fct_us": 99.0, "max_fct_us": cct,
            "retransmissions": 0, "rtos": 0, "trims": 0,
            "ecn_marks": 0, "queue_p99_fraction": 0.02,
            "spine_queue_cv": 0.5,
            "avg_candidate_choices": 28.0,
            "nonbest_fraction": 0.0, "avoid_fraction": 0.0,
        }
        for metric in runner.RANKING_METRICS:
            row.setdefault(metric, 0)
        rows.append(row)
    return rows


def test_ranking(runner):
    summary, rankings = runner.summarize(synthetic_rows(runner, unique=True))
    assert next(row for row in summary if row["selected"])[
        "min_choices"] == 28
    assert {row["metric"] for row in rankings} == set(
        runner.RANKING_METRICS)

    tied, tied_rankings = runner.summarize(synthetic_rows(runner))
    assert not any(row["selected"] for row in tied)
    assert {row["selection_status"] for row in tied} == {
        "indistinguishable"}
    assert {row["rank"] for row in tied_rankings
            if row["metric"] == "cct_us"} == {1}


def main():
    runner = load_runner()
    test_matrix(runner)
    test_ranking(runner)


if __name__ == "__main__":
    main()
