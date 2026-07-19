#!/usr/bin/env python3

import importlib.util
import math
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/n-mrc/build_communication_performance_report.py"


def load_module():
    spec = importlib.util.spec_from_file_location("communication_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_row(scenario, scheme, seed, p99, p999=None, maximum=None,
             alltoall_cct=0.0):
    return {
        "nodes": 128,
        "scenario": scenario,
        "scheme": scheme,
        "cadence": "fixed5" if scheme in {"avail", "grade", "netaware"} else "none",
        "seed": seed,
        "avg_fct_us": p99 * 0.6,
        "p50_fct_us": p99 * 0.5,
        "p95_fct_us": p99 * 0.95,
        "p99_fct_us": p99,
        "p999_fct_us": p999 if p999 is not None else p99 * 1.1,
        "max_fct_us": maximum if maximum is not None else p99 * 1.2,
        "all_to_all_cct_us": alltoall_cct,
        "nacks": seed,
        "nacks_ooo": seed,
        "nacks_trim": 0,
        "nacks_loss": 0,
        "rtos": 0,
        "new_packets": 1000,
        "retx_packets": seed,
        "retx_ratio": seed / 1000,
        "composite_trims": 0,
        "composite_drops": 0,
        "composite_ecn_marks": 10,
        "lossy_drops": 0,
        "queue_cv": 0.1,
        "queue_cv_spine_queue_count": 128,
        "feedback_count": 10 if scheme in {"avail", "grade", "netaware"} else 0,
        "feedback_bytes": 20 if scheme in {"avail", "grade", "netaware"} else 0,
        "feedback_bandwidth_mbps": 0.02 if scheme in {"avail", "grade", "netaware"} else 0,
        "target_flows": 128,
        "target_completed": 128,
        "background_flows": 0,
        "background_completed": 0,
    }


def test_workload_family_and_alltoall_mode_are_explicit():
    module = load_module()
    assert module.workload_family("healthy_permutation_4mib") == "healthy"
    assert module.workload_family("incast_8to1_16mib") == "incast"
    assert module.workload_family("asymmetric_tornado_8mib") == "asymmetric"
    assert module.workload_family(
        "diagnostic_single_slow_link_permutation_32mib"
    ) == "single_slow_link"
    assert module.workload_family(
        "standard_websearch_proxy_80pct"
    ) == "websearch"
    assert module.parse_alltoall_mode(
        "full_global_p16_1024mib_background_on"
    ) == {
        "parallel": 16,
        "message_size_mib": 1024,
        "background": "on",
    }


def test_seed_aggregation_preserves_median_and_range():
    module = load_module()
    rows = [
        seed_row("healthy_permutation_4mib", "reps", 13, 90),
        seed_row("healthy_permutation_4mib", "reps", 29, 100),
        seed_row("healthy_permutation_4mib", "reps", 47, 130),
    ]
    summary = module.aggregate_seed_rows(rows)
    assert len(summary) == 1
    row = summary[0]
    assert row["seed_count"] == 3
    assert row["p99_fct_us_median"] == 100
    assert row["p99_fct_us_min"] == 90
    assert row["p99_fct_us_max"] == 130
    assert row["nacks_median"] == 29
    assert row["queue_cv_spine_queue_count_median"] == 128


def test_normalization_uses_named_and_best_baseline():
    module = load_module()
    rows = []
    values = {
        "ecmp_rr": 140,
        "ops": 130,
        "reps": 100,
        "mrc": 90,
        "sglb": 80,
        "ar": 85,
        "drill": 95,
        "avail": 88,
        "grade": 84,
        "netaware": 76,
    }
    for scheme, value in values.items():
        for seed in (13, 29, 47):
            rows.append(seed_row(
                "asymmetric_permutation_4mib", scheme, seed, value
            ))
    summary = module.aggregate_seed_rows(rows)
    normalized = module.normalized_scheme_rows(summary, "p99_fct_us")
    n_mrc = next(row for row in normalized if row["scheme"] == "netaware")
    assert math.isclose(n_mrc["ratio_to_reps"], 0.76)
    assert math.isclose(n_mrc["ratio_to_best_endpoint"], 76 / 90)
    assert math.isclose(n_mrc["ratio_to_best_network"], 76 / 80)
    assert math.isclose(n_mrc["ratio_to_best_baseline"], 76 / 80)
    assert n_mrc["best_baseline_scheme"] == "sglb"


def test_matrix_validation_requires_three_seeds_and_every_scheme():
    module = load_module()
    rows = [
        seed_row("healthy_permutation_4mib", scheme, seed, 100)
        for scheme in module.SCHEMES
        for seed in (13, 29, 47)
    ]
    module.validate_complete_matrix(
        rows, scenarios={"healthy_permutation_4mib"}, seeds={13, 29, 47}
    )
    removed = rows.pop()
    try:
        module.validate_complete_matrix(
            rows, scenarios={"healthy_permutation_4mib"}, seeds={13, 29, 47}
        )
    except ValueError as error:
        assert "missing matrix cells" in str(error)
    else:
        raise AssertionError("incomplete matrix must be rejected")

    rows.append(removed)
    invalid_queue_sample = dict(rows[0])
    invalid_queue_sample["queue_cv_spine_queue_count"] = 0
    rows[0] = invalid_queue_sample
    try:
        module.validate_complete_matrix(
            rows, scenarios={"healthy_permutation_4mib"}, seeds={13, 29, 47}
        )
    except ValueError as error:
        assert "queue CV" in str(error)
    else:
        raise AssertionError("matrix without queue CV samples must be rejected")


def test_build_artifacts_emits_tables_figures_and_metric_analysis():
    module = load_module()
    point_rows = []
    alltoall_rows = []
    for scenario, scale in (
        ("healthy_permutation_4mib", 1.0),
        ("asymmetric_permutation_4mib", 2.0),
    ):
        for scheme_index, scheme in enumerate(module.SCHEMES):
            for seed in module.SEEDS:
                point_rows.append(seed_row(
                    scenario, scheme, seed, scale * (100 + scheme_index)
                ))
    for scenario, scale in (
        ("full_global_p4_64mib_background_off", 1.0),
        ("full_global_p4_64mib_background_on", 1.5),
    ):
        for scheme_index, scheme in enumerate(module.SCHEMES):
            for seed in module.SEEDS:
                alltoall_rows.append(seed_row(
                    scenario, scheme, seed, 100 + scheme_index,
                    alltoall_cct=scale * (1000 + scheme_index),
                ))

    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir)
        module.build_artifacts(point_rows, alltoall_rows, output)
        expected = {
            "point_to_point_summary.csv",
            "point_to_point_normalized.csv",
            "point_to_point_seed_metrics.csv",
            "point_to_point_family_summary.csv",
            "alltoall_summary.csv",
            "alltoall_normalized.csv",
            "alltoall_seed_metrics.csv",
            "alltoall_mode_summary.csv",
            "feedback_overhead_summary.csv",
            "metric_dictionary.csv",
            "communication_performance_report.md",
            "p2p_p99_heatmap.png",
            "p2p_p99_heatmap.pdf",
            "p2p_tail_depth.png",
            "p2p_tail_depth.pdf",
            "alltoall_cct_heatmap.png",
            "alltoall_cct_heatmap.pdf",
            "recovery_cost.png",
            "recovery_cost.pdf",
            "feedback_overhead.png",
            "feedback_overhead.pdf",
            "scenario_analysis.md",
        }
        assert expected.issubset({path.name for path in output.iterdir()})
        per_scenario = {
            "scenario_figures/p2p_healthy_permutation_4mib.png",
            "scenario_figures/p2p_healthy_permutation_4mib.pdf",
            "scenario_figures/alltoall_full_global_p4_64mib_background_off.png",
            "scenario_figures/alltoall_full_global_p4_64mib_background_off.pdf",
            "scenario_data/p2p_healthy_permutation_4mib.csv",
            "scenario_data/alltoall_full_global_p4_64mib_background_off.csv",
        }
        assert per_scenario.issubset({
            str(path.relative_to(output)) for path in output.rglob("*")
            if path.is_file()
        })
        report = (output / "communication_performance_report.md").read_text()
        assert "p99.9" in report
        assert "All-to-All CCT" in report
        assert "3%" in report
        assert "Point-to-point p99 geometric means versus REPS are" in report
        assert "All-to-All CCT geometric means versus the best baseline are" in report
        assert "Workload-Family Analysis" in report
        assert "Message Size" in report
        assert "Best Mode" in report
        assert "total remote payload sent by each rank" in report
        assert "50% duty cycle" in report
        assert "per-queue time-average" in report
        scenario_analysis = (output / "scenario_analysis.md").read_text()
        assert "healthy_permutation_4mib" in scenario_analysis
        assert "full_global_p4_64mib_background_off" in scenario_analysis
        assert "Best overall" in scenario_analysis


def test_matrix_selection_keeps_only_frozen_subject_cadence():
    module = load_module()
    rows = []
    for scheme in module.SCHEMES:
        for seed in module.SEEDS:
            row = seed_row("healthy_permutation_4mib", scheme, seed, 100)
            row["is_alltoall"] = False
            rows.append(row)
    alternate = dict(rows[-1])
    alternate["cadence"] = "fixed_rtt"
    rows.append(alternate)
    collective = seed_row(
        "full_global_p4_64mib_background_off", "netaware", 13, 100,
        alltoall_cct=1000,
    )
    collective["is_alltoall"] = True
    rows.append(collective)

    selected = module.select_matrix_rows(
        rows, alltoall=False,
        scenarios={"healthy_permutation_4mib"},
    )
    assert len(selected) == len(module.SCHEMES) * len(module.SEEDS)
    assert not any(row["cadence"] == "fixed_rtt" for row in selected)


def test_single_seed_preview_is_explicit_and_complete():
    module = load_module()
    args = module.parse_args([
        "--point-input", "/tmp/point",
        "--alltoall-input", "/tmp/alltoall",
        "--output", "/tmp/output",
        "--seeds", "13",
    ])
    assert args.seeds == (13,)
    point_rows = [
        seed_row("healthy_permutation_4mib", scheme, 13, 100)
        for scheme in module.SCHEMES
    ]
    alltoall_rows = [
        seed_row(
            "full_global_p4_64mib_background_off", scheme, 13, 100,
            alltoall_cct=1000,
        )
        for scheme in module.SCHEMES
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir)
        module.build_artifacts(
            point_rows, alltoall_rows, output, seeds=(13,)
        )
        report = (output / "communication_performance_report.md").read_text()
        assert "Single-Seed Preview" in report
        assert "seed 13" in report
        assert "single-seed matrix" in report
        assert "three-seed matrix" not in report
        assert "not a robustness result" in report
        metric_dictionary = (output / "metric_dictionary.csv").read_text()
        assert "seed 13 run-level p99" in metric_dictionary
        assert "three seed-level" not in metric_dictionary


def main():
    test_workload_family_and_alltoall_mode_are_explicit()
    test_seed_aggregation_preserves_median_and_range()
    test_normalization_uses_named_and_best_baseline()
    test_matrix_validation_requires_three_seeds_and_every_scheme()
    test_build_artifacts_emits_tables_figures_and_metric_analysis()
    test_matrix_selection_keeps_only_frozen_subject_cadence()
    test_single_seed_preview_is_explicit_and_complete()


if __name__ == "__main__":
    main()
