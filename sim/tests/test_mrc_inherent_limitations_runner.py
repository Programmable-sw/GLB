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


class RunnerContractTest(unittest.TestCase):
    def test_full_catalog_has_controlled_case_counts(self):
        runner = load_runner()
        cases = runner.case_catalog((13, 29, 47))
        by_experiment = {}
        for case in cases:
            by_experiment[case.experiment] = (
                by_experiment.get(case.experiment, 0) + 1
            )
        self.assertEqual(
            by_experiment, {"flow_lifetime": 48,
                            "per_qp_sharing": 18,
                            "active_ev_count": 36})
        self.assertEqual({case.seed for case in cases}, {13, 29, 47})

    def test_paired_cases_share_traffic_and_commands_change_only_controls(self):
        runner = load_runner()
        cases = runner.case_catalog((13,))
        rr = next(case for case in cases
                  if case.experiment == "active_ev_count"
                  and case.load_pct == 40 and case.active_evs == 4
                  and case.scheme == "rr")
        mrc = rr._replace(scheme="mrc")
        self.assertEqual(runner.traffic_key(rr), runner.traffic_key(mrc))
        rr_cmd = runner.build_command(
            rr, Path("/sim"), Path("/traffic"), Path("/case"), 10)
        mrc_cmd = runner.build_command(
            mrc, Path("/sim"), Path("/traffic"), Path("/case"), 10)
        self.assertIn("rr", rr_cmd)
        self.assertIn("mrc", mrc_cmd)
        self.assertEqual(
            rr_cmd[rr_cmd.index("-mrc_active_evs") + 1], "4")
        self.assertEqual(
            mrc_cmd[mrc_cmd.index("-mrc_active_evs") + 1], "4")

    def test_slowdown_classification_is_ordered_and_exclusive(self):
        runner = load_runner()
        base = {
            "new_selections_after_first_update": 10,
            "post_cooldown_first_clean": 0,
            "max_simultaneous_cooling": 0,
            "replacement_congestion": 0,
            "forced_cooling_uses": 0,
        }
        self.assertEqual(
            runner.classify_slowdown({
                **base, "new_selections_after_first_update": 1,
                "post_cooldown_first_clean": 1,
            }), "too_late")
        self.assertEqual(
            runner.classify_slowdown({
                **base, "post_cooldown_first_clean": 1,
            }), "stale_consistent")
        self.assertEqual(
            runner.classify_slowdown({
                **base, "max_simultaneous_cooling": 2,
                "replacement_congestion": 1,
            }), "capacity_removal")
        self.assertEqual(
            runner.classify_slowdown({
                **base, "forced_cooling_uses": 1,
            }), "fallback_pressure")
        self.assertEqual(runner.classify_slowdown(base), "unexplained")

    def test_pairing_and_complete_matrix_reject_missing_cells(self):
        runner = load_runner()
        rows = [
            {"scenario": "s", "seed": 13, "scheme": "rr",
             "flow_id": 1, "src": 0, "dst": 1, "flow_size": 10,
             "fct_us": 2.0},
            {"scenario": "s", "seed": 13, "scheme": "mrc",
             "flow_id": 1, "src": 0, "dst": 1, "flow_size": 10,
             "fct_us": 3.0,
             "new_selections_after_first_update": 0,
             "post_cooldown_first_clean": 0,
             "max_simultaneous_cooling": 0,
             "replacement_congestion": 0,
             "forced_cooling_uses": 0},
        ]
        paired = runner.pair_flow_rows(rows, "rr", "mrc")
        self.assertEqual(len(paired), 1)
        self.assertAlmostEqual(paired[0]["fct_ratio"], 1.5)
        self.assertEqual(paired[0]["exclusive_class"], "too_late")
        with self.assertRaisesRegex(ValueError, "missing"):
            runner.validate_complete_cells(
                [{"identity": "a", "valid": 1}], {"a", "b"})

    def test_geomean_and_seed_range(self):
        runner = load_runner()
        self.assertAlmostEqual(runner.geometric_mean((1.0, 4.0)), 2.0)
        stats = runner.seed_summary(
            [{"seed": 13, "value": 3.0},
             {"seed": 29, "value": 1.0},
             {"seed": 47, "value": 2.0}], "value")
        self.assertEqual(stats["min"], 1.0)
        self.assertEqual(stats["max"], 3.0)


if __name__ == "__main__":
    unittest.main()
