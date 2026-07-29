import importlib.util
import math
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/n-mrc/run_mrc_sglb_pressure_transition.py"
SPEC = importlib.util.spec_from_file_location("mrc_sglb_transition", RUNNER)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


TRAFFIC = """\
Nodes 128
Connections 2
7->19 id 1 start 1000000 size 6144
8->20 id 2 start 2500000 size 136192
"""

COMPLETIONS = """\
Flow Roce_7_19 10 finished at 9.0 total bytes 8192 bg traffic 0 flowid 1
Flow Roce_8_20 11 finished at 15.5 total bytes 139264 bg traffic 0 flowid 2
"""


def test_parse_traffic_and_completions_preserves_pairing_fields():
    traffic = runner.parse_traffic_text(TRAFFIC)
    completions = runner.parse_completion_text(COMPLETIONS)

    assert traffic[1] == {
        "flow_id": 1,
        "src": 7,
        "dst": 19,
        "start_us": 1.0,
        "flow_size": 6144,
    }
    assert completions[2]["finish_us"] == 15.5
    assert completions[2]["src"] == 8
    assert completions[2]["dst"] == 20


def test_pair_flows_computes_fct_and_rejects_identity_mismatch():
    traffic = runner.parse_traffic_text(TRAFFIC)
    completions = runner.parse_completion_text(COMPLETIONS)
    mrc = [
        {
            **traffic[1],
            "fct_us": 7.0,
            "actionable_feedback": 0,
            "quality_feedback_before_done": 0,
        },
        {
            **traffic[2],
            "fct_us": 12.0,
            "actionable_feedback": 3,
            "quality_feedback_before_done": 4,
        },
    ]
    # MrcFlowDiag uses six significant digits, so millisecond-scale start
    # times can differ from the picosecond traffic matrix by about 5 ns.
    mrc[1]["start_us"] += 0.0052

    paired = runner.pair_flows(traffic, mrc, completions)

    assert paired[0]["sglb_fct_us"] == 8.0
    assert paired[0]["fct_ratio"] == 7.0 / 8.0
    assert paired[1]["sglb_fct_us"] == 13.0
    bad = dict(completions)
    bad[2] = {**bad[2], "src": 99}
    with pytest.raises(ValueError, match="identity mismatch"):
        runner.pair_flows(traffic, mrc, bad)


def test_aggregate_pairs_uses_geometric_ratio_and_reports_tail():
    rows = [
        {
            "flow_size": 6144,
            "mrc_fct_us": 8.0,
            "sglb_fct_us": 4.0,
            "fct_ratio": 2.0,
            "actionable_feedback": 0,
            "quality_feedback_before_done": 1,
        },
        {
            "flow_size": 6144,
            "mrc_fct_us": 3.0,
            "sglb_fct_us": 6.0,
            "fct_ratio": 0.5,
            "actionable_feedback": 2,
            "quality_feedback_before_done": 0,
        },
    ]

    summary = runner.aggregate_pairs(rows, load_pct=80)

    assert len(summary) == 1
    assert math.isclose(summary[0]["fct_ratio_geomean"], 1.0)
    assert summary[0]["mrc_fct_mean_us"] == 5.5
    assert summary[0]["sglb_fct_mean_us"] == 5.0
    assert summary[0]["actionable_fraction"] == 0.5
    assert summary[0]["quality_feedback_fraction"] == 0.5
    assert summary[0]["actionable_feedback_mean"] == 1.0


def test_build_command_changes_only_lb_and_output_paths(tmp_path):
    traffic = tmp_path / "traffic.cm"
    output = tmp_path / "logout.dat"
    command = runner.build_sglb_command(
        Path("/sim/htsim_roce"), traffic, output, 11077, seed=13
    )

    assert command[command.index("-lb") + 1] == "sglb"
    assert command[command.index("-tm") + 1] == str(traffic)
    assert command[command.index("-o") + 1] == str(output)
    assert command[command.index("-conns") + 1] == "11077"
    assert command[command.index("-slow_tor_uplinks") + 1] == "4"
    assert command[command.index("-slow_tor_uplink_divisor") + 1] == "2"
