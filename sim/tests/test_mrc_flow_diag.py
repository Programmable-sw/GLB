#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments" / "n-mrc" / "run_mrc_inherent_limitations.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("mrc_limit_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIELDS = {
    "flow_id": 1,
    "src": 0,
    "dst": 9,
    "dst_tor": 2,
    "flow_size": 262144,
    "start_us": 0.0,
    "finish_us": 12.5,
    "new_data_selections": 64,
    "unique_active_evs": 8,
    "full_sweeps": 8,
    "first_full_sweep_us": 2.0,
    "unused_active_evs": 0,
    "quality_feedback_before_done": 1,
    "effective_state_updates": 1,
    "first_state_update_us": 3.0,
    "packets_before_first_update": 16,
    "new_selections_after_first_update": 48,
    "actionable_feedback": 1,
    "feedback_age_sum_us": 1.5,
    "feedback_age_max_us": 1.5,
    "cooldown_starts": 1,
    "failure_starts": 0,
    "forced_cooling_uses": 0,
    "max_simultaneous_cooling": 1,
    "replacement_congestion": 0,
    "post_cooldown_first_clean": 0,
    "shared_updates_published": 1,
    "shared_updates_consumed": 0,
    "shared_updates_from_other_qps": 0,
    "redundant_discoveries": 0,
    "post_shared_bad_ev_sends": 0,
}


def diag_line(fields=FIELDS):
    return "MrcFlowDiag " + " ".join(
        f"{key}={value}" for key, value in fields.items()
    )


class MrcFlowDiagParserTest(unittest.TestCase):
    def test_parser_accepts_exact_contract_and_converts_numbers(self):
        runner = load_runner()
        parsed = runner.parse_mrc_flow_diags(diag_line())
        self.assertEqual(len(parsed), 1)
        self.assertEqual(set(parsed[0]), set(FIELDS))
        self.assertEqual(parsed[0]["flow_id"], 1)
        self.assertIsInstance(parsed[0]["flow_id"], int)
        self.assertAlmostEqual(parsed[0]["feedback_age_sum_us"], 1.5)
        self.assertIsInstance(parsed[0]["feedback_age_sum_us"], float)

    def test_parser_rejects_missing_fields_and_duplicate_flow_ids(self):
        runner = load_runner()
        missing = dict(FIELDS)
        missing.pop("actionable_feedback")
        with self.assertRaisesRegex(ValueError, "missing"):
            runner.parse_mrc_flow_diags(diag_line(missing))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            runner.parse_mrc_flow_diags(diag_line() + "\n" + diag_line())


if __name__ == "__main__":
    unittest.main()
