#!/usr/bin/env python3
"""Run the MRC cooldown by all-cooling-fallback 2x2 matrix."""

import concurrent.futures
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "experiments/n-mrc/run_packet_lb_common_workloads_512.py"
OUT = ROOT / "experiments/n-mrc/output/nmrc_mrc_cooldown_fallback_2x2_512"


def load_runner():
    spec = importlib.util.spec_from_file_location("packet_lb_common", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    runner = load_runner()
    runner.OUT = OUT
    runner.SCHEMES = [
        runner.Scheme("mrc", "MRC-scaled-earliest", "mrc"),
        runner.Scheme("mrc_round_robin", "MRC-scaled-RR", "mrc"),
        runner.Scheme("mrc_one_cycle", "MRC-one-cycle-earliest", "mrc"),
        runner.Scheme(
            "mrc_one_cycle_round_robin", "MRC-one-cycle-RR", "mrc"),
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(runner.run_case, workload, scheme)
            for workload in runner.WORKLOADS
            for scheme in runner.SCHEMES
        ]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())
    workload_order = {
        item.name: index for index, item in enumerate(runner.WORKLOADS)}
    scheme_order = {
        item.key: index for index, item in enumerate(runner.SCHEMES)}
    rows.sort(key=lambda row: (
        workload_order[row["workload"]], scheme_order[row["scheme"]]))
    report = runner.write_outputs(rows, OUT)
    print(report)
    failed = [row for row in rows if not runner.row_complete(row)]
    if failed:
        raise SystemExit(f"{len(failed)} incomplete or invalid runs")


if __name__ == "__main__":
    main()
