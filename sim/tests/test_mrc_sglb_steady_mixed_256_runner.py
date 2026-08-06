#!/usr/bin/env python3

import importlib.util
import math
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/n-mrc/run_mrc_sglb_steady_mixed_256.py"


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "mrc_sglb_steady_mixed_256", RUNNER)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def option(command, name):
    assert command.count(name) == 1
    return command[command.index(name) + 1]


def main():
    runner = load_runner()
    args = runner.parse_args([])
    assert args.sample_scale == 1.0
    assert args.cold_out == runner.DEFAULT_COLD_OUT
    assert runner.NODES == 256
    assert runner.SOURCE_LEAF == 0
    assert runner.STEADY_START_US > runner.ARRIVAL_START_US
    assert runner.STEADY_END_US < runner.ARRIVAL_END_US
    assert runner.SHORT_OFFERED_LOAD + runner.LONG_OFFERED_LOAD == 0.66
    assert runner.CONDITIONS == (
        "healthy", "fixed_hotspot", "ecmp_mixed", "slow_uplink")

    flows = runner.build_steady_flows(13, sample_scale=0.01)
    assert flows
    assert runner.TRANSITION_PROBE_SIZES == (
        256 * 1024, 512 * 1024, 667 * 1024,
        1024 * 1024, 1333 * 1024, 2048 * 1024)
    assert runner.TRANSITION_PROBES_PER_SIZE == 16
    assert {flow["cohort"] for flow in flows} == {
        "short", "transition_probe", "long"}
    probe_flows = [
        flow for flow in flows if flow["cohort"] == "transition_probe"]
    assert {flow["flow_size"] for flow in probe_flows} == set(
        runner.TRANSITION_PROBE_SIZES)
    assert all(runner.STEADY_START_US <= flow["start_us"] <=
               runner.STEADY_END_US for flow in probe_flows)
    assert all(flow["src"] < 64 for flow in flows)
    assert all(flow["dst"] >= 64 for flow in flows)
    assert all(runner.ARRIVAL_START_US <= flow["start_us"] <=
               runner.ARRIVAL_END_US for flow in flows)
    assert any(runner.STEADY_START_US <= flow["start_us"] <=
               runner.STEADY_END_US for flow in flows)
    assert len({flow["flow_id"] for flow in flows}) == len(flows)

    mixed_flows = runner.build_ecmp_mixed_flows(13, flows)
    background = [
        flow for flow in mixed_flows
        if flow["cohort"] == "ecmp_background"]
    assert background
    assert len(background) == runner.ECMP_BACKGROUND_FLOWS
    assert all(flow["lb_mode"] == "ecmp" for flow in background)
    assert all("lb_mode" not in flow for flow in mixed_flows
               if flow["cohort"] != "ecmp_background")
    assert all(flow["start_us"] < runner.STEADY_START_US
               for flow in background)
    assert all(flow["flow_size"] >= runner.LONG_DISTRIBUTION[0][0]
               for flow in background)
    assert not ({flow["flow_id"] for flow in background} &
                {flow["flow_id"] for flow in runner.select_steady_flows(
                    mixed_flows)})

    for scheme in runner.SCHEMES:
        command = runner.build_command(
            Path("sim"), scheme, Path("traffic.cm"), Path("out.dat"),
            len(flows), 13, "fixed_hotspot")
        assert option(command, "-nodes") == "256"
        assert option(command, "-paths") == "64"
        assert option(command, "-path_hotspot_spines") == "16"
        assert option(command, "-path_hotspot_bg_rate_gbps") == "390"
        if scheme == "sglb":
            assert option(command, "-sglb_update_us") == "1"
            assert option(command, "-sglb_gcn_update_us") == "15"

        asymmetric = runner.build_command(
            Path("sim"), scheme, Path("traffic.cm"), Path("out.dat"),
            len(flows), 13, "slow_uplink")
        assert option(asymmetric, "-slow_tor_uplinks") == "64"
        assert option(asymmetric, "-slow_tor_uplink_divisor") == "2"
        assert option(asymmetric, "-slow_tor_uplink_select") == "random-sparse"

        mixed = runner.build_command(
            Path("sim"), scheme, Path("traffic.cm"), Path("out.dat"),
            len(mixed_flows), 13, "ecmp_mixed")
        assert "-mixed_lb_traffic" not in mixed

    mixed_stdout = "\n".join([
        "Standard 2-tier leaf-spine: nodes 256 leaves 4 spines 64 ",
        "RoceTransportConfig semantics=mrc_exact_bounded",
        "topology_path_combo=64",
        "RR: stateless_mrc true, physical_path_space 64, active_evs 64",
        f"ExplicitLbDiag ecmp_background_flows="
        f"{runner.ECMP_BACKGROUND_FLOWS}",
        "Flow Roce_0_64 1 finished at 10 total bytes 4096 "
        "bg traffic 0 flowid 1",
        *[
            f"Flow Roce_{index}_64 {index + 2} finished at 20 "
            f"total bytes {runner.ECMP_BACKGROUND_SIZE} bg traffic 1 "
            f"flowid {index + 2}"
            for index in range(runner.ECMP_BACKGROUND_FLOWS)
        ],
    ])
    parsed_mixed = runner.validate_cell(
        mixed_stdout, 0, "rr", "ecmp_mixed",
        1 + runner.ECMP_BACKGROUND_FLOWS)
    assert set(parsed_mixed) == {1}

    steady = runner.select_steady_flows(flows)
    assert steady
    assert all(runner.STEADY_START_US <= flow["start_us"] <=
               runner.STEADY_END_US for flow in steady)

    chosen = [
        next(flow for flow in steady if flow["cohort"] == cohort)
        for cohort in ("short", "long")
    ]
    completions = {}
    for condition, factor in (("healthy", 1.0), ("fixed_hotspot", 2.0),
                              ("ecmp_mixed", 1.8),
                              ("slow_uplink", 1.5)):
        completions[condition] = {}
        for scheme, baseline in (("mrc", 10.0), ("rr", 12.0),
                                 ("sglb", 8.0)):
            completions[condition][scheme] = {
                flow["flow_id"]: {
                    "src": flow["src"], "dst": flow["dst"],
                    "finish_us": flow["start_us"] + baseline * factor,
                }
                for flow in chosen
            }
    diag = SimpleNamespace(
        actionable_feedback=1, quality_feedback_before_done=2,
        effective_state_updates=1, new_data_selections=100,
        new_selections_after_first_update=30,
        packets_before_first_update=70, unique_active_evs=64,
        full_sweeps=1, first_state_update_us=chosen[0]["start_us"] + 7,
        first_full_sweep_us=chosen[0]["start_us"] + 6,
    )
    diagnostics = {
        condition: {flow["flow_id"]: diag for flow in chosen}
        for condition in runner.CONDITIONS
    }
    paired = runner.make_steady_rows(
        13, chosen, completions, diagnostics)
    assert len(paired) == 2
    assert {row["cohort"] for row in paired} == {"short", "long"}
    assert all(row["fixed_mrc_sglb_ratio"] == 1.25 for row in paired)
    assert all(row["fixed_hotspot_mrc_nominal_rotations"] == 100.0 / 64.0
               for row in paired)
    assert all(row["fixed_hotspot_mrc_pre_feedback_selections"] == 70
               for row in paired)
    assert all(row["fixed_hotspot_mrc_post_feedback_selections"] == 30
               for row in paired)
    assert all(row["fixed_hotspot_mrc_actual_full_sweeps"] == 1
               for row in paired)
    summary = runner.summarize_steady(paired)
    assert {row["cohort"] for row in summary} == {"short", "long"}
    assert all(row["mrc_actionable_fraction"] == 1.0 for row in summary)
    one_seed_load = runner._arrival_load(chosen, "short", seed_count=1)
    two_seed_load = runner._arrival_load(
        chosen + chosen, "short", seed_count=2)
    assert one_seed_load[1:] == two_seed_load[1:]
    with tempfile.TemporaryDirectory() as directory:
        traffic_path = Path(directory) / "mixed.cm"
        runner.write_traffic(traffic_path, mixed_flows)
        traffic_text = traffic_path.read_text(encoding="utf-8")
        assert traffic_text.count(" lb ecmp\n") == runner.ECMP_BACKGROUND_FLOWS
        report = runner.write_report(
            summary, paired, chosen, Path(directory), 0.01)
        text = report.read_text(encoding="utf-8")
        assert "15 µs" in text
        assert "稳态窗口" in text
        assert "p99" in text
        assert "skip token" in text
        assert "离散启动" in text
        assert "cold_qp_startup_and_feedback.png" in text
        assert "256/512/667 KiB" in text
        assert "按流大小的控制转折" in text
        plot_rows = runner.flow_size_plot_rows(paired)
        assert {row["flow_size"] for row in plot_rows} == {
            flow["flow_size"] for flow in chosen}
        assert all(row["mrc_degradation_gmean"] == 2.0
                   for row in plot_rows)
        assert all(row["mrc_rr_ratio_gmean"] == 20.0 / 24.0
                   for row in plot_rows)
        assert all(row["mrc_sglb_ratio_gmean"] == 1.25
                   for row in plot_rows)
        assert all(row["mrc_nominal_rotations_mean"] == 100.0 / 64.0
                   for row in plot_rows)
        assert all(row["mrc_actual_full_sweeps_mean"] == 1.0
                   for row in plot_rows)
        assert all(row["mrc_actionable_fraction"] == 1.0
                   for row in plot_rows)
        assert all(row["fixed_hotspot_mrc_fct_us_mean"] == 20.0
                   for row in plot_rows)
        assert all(row["fixed_hotspot_mrc_fct_us_p99"] == 20.0
                   for row in plot_rows)
        assert all(math.isclose(
                       row["ecmp_mixed_mrc_rr_ratio_gmean"], 20.0 / 24.0)
                   for row in plot_rows)
        assert all(row["mrc_coverage_mean"] == 1.0 for row in plot_rows)
        assert all(row["mrc_pre_feedback_selections_mean"] == 70.0
                   for row in plot_rows)
        assert all(row["mrc_post_feedback_selections_mean"] == 30.0
                   for row in plot_rows)
        assert all("rr_sglb" not in key for row in plot_rows for key in row)
        figures = runner.plot_flow_size_focus(paired, Path(directory))
        assert all(path.exists() and path.stat().st_size > 0
                   for path in figures)
        robustness = runner.plot_lifecycle_robustness(
            paired, Path(directory))
        assert all(path.exists() and path.stat().st_size > 0
                   for path in robustness)

        theory_rows = [dict(row) for row in paired]
        short = next(row for row in theory_rows if row["cohort"] == "short")
        long = next(row for row in theory_rows if row["cohort"] == "long")
        short.update({
            "flow_size": 256 * 1024,
            "fixed_hotspot_mrc_actionable": 0,
            "fixed_hotspot_mrc_post_feedback_selections": 0,
            "fixed_hotspot_mrc_nominal_rotations": 1.0,
            "fixed_hotspot_mrc_first_update_delay_us": 7.0,
            "fixed_mrc_rr_ratio": 1.0,
            "fixed_mrc_sglb_ratio": 1.2,
        })
        long.update({
            "flow_size": 3333 * 1024,
            "fixed_hotspot_mrc_actionable": 1,
            "fixed_hotspot_mrc_post_feedback_selections": 30,
            "fixed_hotspot_mrc_nominal_rotations": 10.0,
            "fixed_hotspot_mrc_first_update_delay_us": 7.0,
            "fixed_mrc_rr_ratio": 0.8,
            "fixed_mrc_sglb_ratio": 1.0,
        })
        checks = runner.evaluate_theory_checks(theory_rows, rtt_us=7.0)
        assert all(item["supported"] for item in checks.values())
        assert (checks["short_zero_actionable_rr_equivalence"]
                ["equivalence_margin"] == 0.10)
        checks_path = runner.write_theory_checks(
            checks, Path(directory))
        assert json.loads(checks_path.read_text(encoding="utf-8")) == checks
        evidence_report = runner.write_lifecycle_report(
            summary, theory_rows, chosen, Path(directory), 0.01, 1,
            checks)
        evidence_text = evidence_report.read_text(encoding="utf-8")
        assert "ECMP 大流" in evidence_text
        assert "1 RTT" in evidence_text
        assert "256 KiB" in evidence_text
        assert "逐项理论判定" in evidence_text


if __name__ == "__main__":
    main()
