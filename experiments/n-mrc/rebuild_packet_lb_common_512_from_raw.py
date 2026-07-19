#!/usr/bin/env python3
"""Rebuild the historical non-dToR packet-LB summary from preserved raw logs."""

import csv
import importlib.util
import shlex
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "experiments/n-mrc/run_packet_lb_common_workloads_512.py"
OUT = ROOT / "experiments/n-mrc/output/nmrc_packet_lb_common_workloads_512"


def load_runner():
    spec = importlib.util.spec_from_file_location("packet_lb_common", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    runner = load_runner()
    runner.MRC_FIELDS = [
        "adaptive_ecn_events", "adaptive_ecn_escalation_events",
        "adaptive_clean_decay_events", "adaptive_max_strike_observed",
        "window_scaled_ecn_events", "window_scaled_duplicate_ecn_ignored",
        "forced_cooling_use", "cooling_skip_selection_avg",
    ]
    runner.SCHEMES = [
        runner.Scheme("ecmp_rr", "ECMP-RR", "ecmp_rr"),
        runner.Scheme("ops", "OPS", "ops"),
        runner.Scheme("reps", "REPS", "reps"),
        runner.Scheme("mrc", "MRC", "mrc"),
        runner.Scheme("mrc_adaptive", "MRC-adaptive", "mrc"),
        runner.Scheme("mrc_window_scaled", "MRC-window-scaled", "mrc"),
        runner.Scheme("netaware", "n-MRC", "netaware"),
    ]

    rows = []
    for workload in runner.WORKLOADS:
        flows = runner.make_flows(workload)
        for scheme in runner.SCHEMES:
            case_dir = OUT / "raw" / workload.name / scheme.key
            command = shlex.split((case_dir / "run.cmd").read_text())
            returncode = int((case_dir / "returncode.txt").read_text().strip())
            row = runner.parse_run(
                workload, scheme, flows, case_dir / "run.stdout",
                case_dir / "idmap.txt", returncode, command)
            text = (case_dir / "run.stdout").read_text(errors="replace")
            row["config_ok"] = int(
                returncode == 0 and
                "FinalCcMrcConfig dcqcn_variant_inflate=natural "
                "mrc_trim_soft_skip=one_cycle" in text)
            rows.append(row)

    report = runner.write_outputs(rows, OUT)
    report_text = report.read_text()
    report_text = report_text.replace(
        "- schemes: ecmp_rr, ops, reps, mrc, mrc_one_cycle, n-mrc",
        "- historical schemes: ecmp_rr, ops, reps, mrc, mrc_adaptive, "
        "mrc_window_scaled, n-mrc")
    report_text = report_text.replace(
        "- mrc defaults to cwnd-scaled ECN cooldown; mrc_one_cycle is the only "
        "explicit cooldown reference",
        "- historical MRC rows predate the final uniform ECN/TRIM penalty")
    report_text = report_text.replace(
        "- default mrc uses ceil(100/active_evs) rotations: 7 rotations and 112 "
        "selections for this 16-EV topology",
        "- mrc is one-cycle; mrc_window_scaled uses scaled ECN and one-cycle TRIM")
    report.write_text(report_text)

    with (OUT / "summary.csv").open(newline="") as handle:
        restored = list(csv.DictReader(handle))
    if len(restored) != 49:
        raise SystemExit(f"expected 49 restored rows, got {len(restored)}")
    print(f"restored {len(restored)} rows: {report}")


if __name__ == "__main__":
    main()
