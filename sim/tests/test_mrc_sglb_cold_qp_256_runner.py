#!/usr/bin/env python3

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/n-mrc/run_mrc_sglb_cold_qp_256.py"


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "mrc_sglb_cold_qp_256", RUNNER)
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
    assert runner.TOPOLOGY.nodes == 256
    assert runner.TOPOLOGY.hosts_per_leaf == 64
    assert runner.TOPOLOGY.leaves == 4
    assert runner.TOPOLOGY.spines == 64
    assert runner.TOPOLOGY.paths == 64
    assert runner.ACTIVE_EVS == 64
    assert runner.SGLB_GCN_UPDATE_US == 15
    assert runner.DISCRETE_FIGURE_STEM == "cold_qp_discrete_start_diagnostics"

    flows = runner.build_probe_flows(13, sample_scale=0.05)
    expected_cells = len(runner.START_ANCHORS_US) * len(runner.FLOW_SIZES)
    assert len(flows) >= expected_cells
    assert len({flow["flow_id"] for flow in flows}) == len(flows)
    assert {(flow["start_anchor_us"], flow["flow_size"])
            for flow in flows} == {
        (start, size)
        for start in runner.START_ANCHORS_US
        for size in runner.FLOW_SIZES
    }
    assert all(flow["src"] // 64 != flow["dst"] // 64 for flow in flows)

    fixed = runner.build_command(
        Path("sim"), "mrc", Path("traffic.cm"), Path("out.dat"),
        len(flows), 13, "fixed_hotspot")
    assert option(fixed, "-nodes") == "256"
    assert option(fixed, "-tiers") == "2"
    assert option(fixed, "-paths") == "64"
    assert option(fixed, "-path_hotspot_spines") == "16"
    assert option(fixed, "-path_hotspot_bg_rate_gbps") == "390"
    assert option(fixed, "-mrc_active_evs") == "64"
    assert option(fixed, "-mrc_congestion_policy") == "skip_token"
    assert option(fixed, "-mrc_failure_recovery") == "off"

    fixed_rr = runner.build_command(
        Path("sim"), "rr", Path("traffic.cm"), Path("out.dat"),
        len(flows), 13, "fixed_hotspot")
    assert option(fixed_rr, "-mrc_active_evs") == "64"
    assert "-mrc_congestion_policy" not in fixed_rr

    healthy = runner.build_command(
        Path("sim"), "sglb", Path("traffic.cm"), Path("out.dat"),
        len(flows), 13, "healthy")
    assert "-path_hotspot_spines" not in healthy
    assert option(healthy, "-sglb_update_us") == "1"
    assert option(healthy, "-sglb_gcn_update_us") == "15"

    latest_sglb_stdout = "\n".join([
        "Standard 2-tier leaf-spine: nodes 256 leaves 4 spines 64 ",
        "RoceTransportConfig semantics=mrc_exact_bounded",
        "topology_path_combo=64",
        "SGLB effective config: score mode nmrc_quantized_topk, "
        "OFAT factor real_gcn_raw_linear, local quality update 1us, "
        "GCN update 15us, min choices 24",
        "PaperSglbDiag route_calls=1 gcn_packets=1 gcn_bytes=256 "
        "gcn_deliveries=1 gcn_profile_updates=1 gcn_stale=0",
        "Flow Roce_0_64 1 finished at 2 total bytes 8192 "
        "bg traffic 0 flowid 1",
    ])
    runner.validate_cell(
        latest_sglb_stdout, 0, "sglb", "healthy", 1)
    no_real_gcn = latest_sglb_stdout.replace(
        "gcn_packets=1 gcn_bytes=256 gcn_deliveries=1 "
        "gcn_profile_updates=1",
        "gcn_packets=0 gcn_bytes=0 gcn_deliveries=0 "
        "gcn_profile_updates=0")
    try:
        runner.validate_cell(no_real_gcn, 0, "sglb", "healthy", 1)
    except RuntimeError as error:
        assert "real GCN" in str(error)
    else:
        raise AssertionError("latest SGLB validation accepted zero real GCNs")
    no_min24 = latest_sglb_stdout.replace(
        "min choices 24", "min choices 8")
    try:
        runner.validate_cell(no_min24, 0, "sglb", "healthy", 1)
    except RuntimeError as error:
        assert "min choices 24" in str(error)
    else:
        raise AssertionError("latest SGLB validation accepted non-min24 config")

    spec = flows[0]
    completions = {
        "healthy": {
            "mrc": {spec["flow_id"]: {"src": spec["src"],
                                      "dst": spec["dst"],
                                      "finish_us": spec["start_us"] + 10}},
            "rr": {spec["flow_id"]: {"src": spec["src"],
                                     "dst": spec["dst"],
                                     "finish_us": spec["start_us"] + 11}},
            "sglb": {spec["flow_id"]: {"src": spec["src"],
                                       "dst": spec["dst"],
                                       "finish_us": spec["start_us"] + 9}},
        },
        "fixed_hotspot": {
            "mrc": {spec["flow_id"]: {"src": spec["src"],
                                      "dst": spec["dst"],
                                      "finish_us": spec["start_us"] + 20}},
            "rr": {spec["flow_id"]: {"src": spec["src"],
                                     "dst": spec["dst"],
                                     "finish_us": spec["start_us"] + 22}},
            "sglb": {spec["flow_id"]: {"src": spec["src"],
                                       "dst": spec["dst"],
                                       "finish_us": spec["start_us"] + 10}},
        },
    }
    diag = SimpleNamespace(
        actionable_feedback=2, quality_feedback_before_done=3,
        effective_state_updates=2, new_data_selections=100,
        new_selections_after_first_update=40,
        packets_before_first_update=60, unique_active_evs=64,
        full_sweeps=1, first_state_update_us=spec["start_us"] + 7,
        first_full_sweep_us=spec["start_us"] + 8,
    )
    rows = runner.make_paired_rows(
        13, [spec], completions,
        {"healthy": {spec["flow_id"]: diag},
         "fixed_hotspot": {spec["flow_id"]: diag}},
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["fixed_mrc_sglb_ratio"] == 2.0
    assert row["mrc_degradation"] == 2.0
    assert row["fixed_hotspot_mrc_actionable"] == 1
    assert row["fixed_hotspot_mrc_first_update_delay_us"] == 7
    assert row["fixed_hotspot_mrc_first_sweep_delay_us"] == 8
    assert row["fixed_hotspot_mrc_coverage"] == 1.0


if __name__ == "__main__":
    main()
