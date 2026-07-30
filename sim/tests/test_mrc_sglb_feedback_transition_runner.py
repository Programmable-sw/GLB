import importlib.util
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/n-mrc/run_mrc_sglb_feedback_transition.py"
SPEC = importlib.util.spec_from_file_location("feedback_transition", RUNNER)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def command_options(command):
    return {
        command[index]: command[index + 1]
        for index in range(1, len(command), 2)
    }


def test_transition_traffic_has_existing_sizes_and_adaptive_counts():
    flows = runner.build_transition_flows(
        seed=13, sample_scale=0.01, arrival_window_us=500)

    assert {row["flow_size"] for row in flows} == set(runner.SIZE_COUNTS)
    assert {
        size: sum(row["flow_size"] == size for row in flows)
        for size in runner.SIZE_COUNTS
    } == {
        size: max(1, math.ceil(count * 0.01))
        for size, count in runner.SIZE_COUNTS.items()
    }
    assert min(row["start_us"] for row in flows) >= 100
    assert max(row["start_us"] for row in flows) <= 105
    assert all(row["src"] != row["dst"] for row in flows)
    assert [row["flow_id"] for row in flows] == list(
        range(1, len(flows) + 1))


def test_sample_scaling_preserves_aggregate_arrival_intensity():
    pilot = runner.build_transition_flows(
        seed=13, sample_scale=0.05, arrival_window_us=500)
    full = runner.build_transition_flows(
        seed=13, sample_scale=1.0, arrival_window_us=500)

    def input_rate(flows):
        bits = sum(row["flow_size"] for row in flows) * 8
        window_s = (
            max(row["start_us"] for row in flows) -
            min(row["start_us"] for row in flows)
        ) * 1e-6
        return bits / window_s

    assert 0.8 <= input_rate(pilot) / input_rate(full) <= 1.2


def test_three_commands_differ_only_by_lb_and_output(tmp_path):
    commands = {
        scheme: runner.build_command(
            Path("/sim/htsim_roce"), scheme, Path("/traffic.cm"),
            tmp_path / (scheme + ".dat"), 100, 13, 340,
            hotspot_spines=5, hotspot_on_us=1000)
        for scheme in runner.SCHEMES
    }
    normalized = []
    for scheme, command in commands.items():
        options = command_options(command)
        assert options.pop("-lb") == scheme
        options.pop("-o")
        normalized.append(options)

    assert normalized[0] == normalized[1] == normalized[2]
    assert normalized[0]["-path_hotspot_spines"] == "5"
    assert normalized[0]["-path_hotspot_bg_rate_gbps"] == "340"
    assert normalized[0]["-path_hotspot_bg_on_us"] == "1000"
    assert normalized[0]["-end"] == "40000"


def test_phase_parser_requires_selection_timing_fields():
    text = (
        "MrcFlowDiag flow_id=7 src=1 dst=9 dst_tor=1 flow_size=6144 "
        "start_us=100 finish_us=108 new_data_selections=8 "
        "unique_active_evs=6 full_sweeps=0 unused_active_evs=2 "
        "quality_feedback_before_done=2 effective_state_updates=1 "
        "first_full_sweep_us=-1 first_state_update_us=104 "
        "packets_before_first_update=2 new_selections_after_first_update=6 "
        "actionable_feedback=1 feedback_age_sum_us=2 feedback_age_max_us=2 "
        "cooldown_starts=1 failure_starts=0 forced_cooling_uses=0 "
        "max_simultaneous_cooling=1 replacement_congestion=0 "
        "post_cooldown_first_clean=0 shared_updates_published=0 "
        "shared_updates_consumed=0 shared_updates_from_other_qps=0 "
        "redundant_discoveries=0 post_shared_bad_ev_sends=0\n"
    )

    parsed = runner.parse_mrc_phase_diags(text)

    assert parsed[7]["new_data_selections"] == 8
    assert parsed[7]["packets_before_first_update"] == 2
    assert parsed[7]["new_selections_after_first_update"] == 6
    assert parsed[7]["actionable_feedback"] == 1


def test_strict_pairing_computes_both_ratios_and_phase_fraction():
    traffic = {
        1: {
            "flow_id": 1, "src": 1, "dst": 2, "start_us": 100.0,
            "flow_size": 6144,
        },
        2: {
            "flow_id": 2, "src": 3, "dst": 4, "start_us": 101.0,
            "flow_size": 6144,
        },
    }
    completions = {
        "mrc": {
            1: {"flow_id": 1, "src": 1, "dst": 2, "finish_us": 108.0},
            2: {"flow_id": 2, "src": 3, "dst": 4, "finish_us": 109.0},
        },
        "sglb": {
            1: {"flow_id": 1, "src": 1, "dst": 2, "finish_us": 104.0},
            2: {"flow_id": 2, "src": 3, "dst": 4, "finish_us": 105.0},
        },
        "rr": {
            1: {"flow_id": 1, "src": 1, "dst": 2, "finish_us": 110.0},
            2: {"flow_id": 2, "src": 3, "dst": 4, "finish_us": 105.0},
        },
    }
    diags = {
        1: {
            "src": 1, "dst": 2, "flow_size": 6144,
            "new_data_selections": 8, "packets_before_first_update": 2,
            "new_selections_after_first_update": 6,
            "actionable_feedback": 1, "quality_feedback_before_done": 1,
            "effective_state_updates": 1, "unique_active_evs": 6,
            "full_sweeps": 0,
        },
        2: {
            "src": 3, "dst": 4, "flow_size": 6144,
            "new_data_selections": 4, "packets_before_first_update": 4,
            "new_selections_after_first_update": 0,
            "actionable_feedback": 0, "quality_feedback_before_done": 0,
            "effective_state_updates": 0, "unique_active_evs": 4,
            "full_sweeps": 0,
        },
    }

    paired = runner.pair_flows(13, traffic, completions, diags)
    seed_rows = runner.aggregate_by_seed_and_size(paired)
    summary = runner.aggregate_across_seeds(paired, seed_rows)

    assert paired[0]["mrc_sglb_ratio"] == 2.0
    assert paired[0]["mrc_rr_ratio"] == 0.8
    assert paired[0]["post_update_selection_fraction"] == 0.75
    assert paired[1]["post_update_selection_fraction"] == 0.0
    assert math.isclose(
        seed_rows[0]["mrc_sglb_ratio_geomean"], math.sqrt(4.0))
    assert math.isclose(
        seed_rows[0]["mrc_rr_ratio_geomean"], math.sqrt(0.8 * 2.0))
    assert seed_rows[0]["actionable_fraction"] == 0.5
    assert seed_rows[0]["post_update_selection_fraction_mean"] == 0.375
    assert summary[0]["flow_count"] == 2
