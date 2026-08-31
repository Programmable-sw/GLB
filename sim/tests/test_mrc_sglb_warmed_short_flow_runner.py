import importlib.util
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/n-mrc/run_mrc_sglb_warmed_short_flow.py"
SPEC = importlib.util.spec_from_file_location("warmed_short_flow", RUNNER)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_balanced_traffic_reuses_existing_sizes_and_warm_start():
    flows = runner.build_balanced_flows(seed=13, flows_per_size=3)

    assert len(flows) == 18
    assert {size: sum(f["flow_size"] == size for f in flows)
            for size in runner.FLOW_SIZES} == {
                size: 3 for size in runner.FLOW_SIZES
            }
    assert min(f["start_us"] for f in flows) >= 100
    assert max(f["start_us"] for f in flows) <= 600
    assert all(f["src"] != f["dst"] for f in flows)


def test_commands_are_identical_except_lb_and_output(tmp_path):
    traffic = tmp_path / "traffic.cm"
    mrc = runner.build_command(
        Path("/sim/htsim_roce"), "mrc", traffic, tmp_path / "mrc.dat", 18, 13)
    sglb = runner.build_command(
        Path("/sim/htsim_roce"), "sglb", traffic, tmp_path / "sglb.dat", 18, 13)

    def options(command):
        return {
            command[i]: command[i + 1]
            for i in range(1, len(command), 2)
        }

    left, right = options(mrc), options(sglb)
    assert left.pop("-lb") == "mrc"
    assert right.pop("-lb") == "sglb"
    left.pop("-o")
    right.pop("-o")
    assert left == right
    assert left["-path_hotspot_spines"] == "16"
    assert left["-path_hotspot_bg_rate_gbps"] == "380"
    assert left["-path_hotspot_bg_on_us"] == "1000"
    assert left["-end"] == "10000"


def test_parsers_extract_mrc_and_sglb_mechanism_diagnostics():
    mrc_text = (
        "MrcFlowDiag flow_id=7 src=1 dst=9 dst_tor=1 flow_size=6144 "
        "start_us=100 finish_us=108 new_data_selections=2 "
        "unique_active_evs=2 full_sweeps=0 unused_active_evs=6 "
        "quality_feedback_before_done=1 effective_state_updates=1 "
        "first_full_sweep_us=-1 first_state_update_us=107 "
        "packets_before_first_update=2 new_selections_after_first_update=0 "
        "actionable_feedback=0 feedback_age_sum_us=2 feedback_age_max_us=2 "
        "cooldown_starts=1 failure_starts=0 forced_cooling_uses=0 "
        "max_simultaneous_cooling=1 replacement_congestion=0 "
        "post_cooldown_first_clean=0\n"
    )
    sglb_text = (
        "SglbRouteDiag route_calls=100 avg_available_choices=8 "
        "avg_candidate_choices=3.2 avg_best_quality_choices=3 "
        "avg_distinct_qualities=2 all_same_quality_calls=10 "
        "all_zero_quality_calls=8 selected_nonbest_quality=0 "
        "avg_score_spread=0.7 observed_good=300 observed_degraded=100 "
        "observed_bad=200 observed_avoid=200 selected_good=100 "
        "selected_degraded=0 selected_bad=0 selected_avoid=0 "
        "remote_snapshot_used=100 remote_snapshot_missing=0\n"
    )

    mrc = runner.parse_mrc_flow_diags(mrc_text)
    sglb = runner.parse_sglb_route_diag(sglb_text)

    assert mrc[7]["actionable_feedback"] == 0
    assert mrc[7]["unique_active_evs"] == 2
    assert mrc[7]["quality_feedback_before_done"] == 1
    assert mrc[7]["effective_state_updates"] == 1
    assert mrc[7]["new_selections_after_first_update"] == 0
    assert sglb["route_calls"] == 100
    assert sglb["all_zero_fraction"] == 0.08
    assert sglb["avg_candidate_choices"] == 3.2


def test_strict_pairing_and_summary_use_per_flow_geometric_mean():
    traffic = {
        1: {"flow_id": 1, "src": 1, "dst": 2, "start_us": 100.0,
            "flow_size": 6144},
        2: {"flow_id": 2, "src": 3, "dst": 4, "start_us": 101.0,
            "flow_size": 6144},
    }
    completions = {
        "mrc": {
            1: {"flow_id": 1, "src": 1, "dst": 2, "finish_us": 108.0},
            2: {"flow_id": 2, "src": 3, "dst": 4, "finish_us": 105.0},
        },
        "sglb": {
            1: {"flow_id": 1, "src": 1, "dst": 2, "finish_us": 104.0},
            2: {"flow_id": 2, "src": 3, "dst": 4, "finish_us": 109.0},
        },
    }
    diags = {
        1: {"src": 1, "dst": 2, "flow_size": 6144,
            "actionable_feedback": 0, "unique_active_evs": 2,
            "full_sweeps": 0, "quality_feedback_before_done": 1,
            "effective_state_updates": 1,
            "new_selections_after_first_update": 0},
        2: {"src": 3, "dst": 4, "flow_size": 6144,
            "actionable_feedback": 0, "unique_active_evs": 2,
            "full_sweeps": 0, "quality_feedback_before_done": 0,
            "effective_state_updates": 0,
            "new_selections_after_first_update": 0},
    }

    paired = runner.pair_flows(13, traffic, completions, diags)
    summary = runner.aggregate_by_seed_and_size(paired)

    assert [row["fct_ratio"] for row in paired] == [2.0, 0.5]
    assert math.isclose(summary[0]["fct_ratio_geomean"], 1.0)
    assert summary[0]["actionable_fraction"] == 0.0
    assert summary[0]["ev_coverage_mean"] == 0.25
    assert summary[0]["quality_feedback_fraction"] == 0.5
    assert summary[0]["effective_updates_mean"] == 0.5
    assert summary[0]["new_selections_after_update_mean"] == 0.0
