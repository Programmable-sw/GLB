#!/usr/bin/env python3
"""Run the current canonical packet-LB schemes on the 512-node matrix."""

import concurrent.futures
import importlib.util
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "experiments/n-mrc/run_packet_lb_common_workloads_512.py"
OUT = Path(os.environ.get(
    "PACKET_LB_COMMON_OUT",
    str(ROOT / (
        "experiments/n-mrc/output/"
        "nmrc_packet_lb_common_workloads_current_512")),
)).resolve()
SCHEME_KEYS = {"ecmp_rr", "ops", "reps", "mrc", "avail", "grade", "netaware"}


def load_runner():
    spec = importlib.util.spec_from_file_location("packet_lb_common", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    runner = load_runner()
    runner.OUT = OUT
    runner.SCHEMES = [
        scheme for scheme in runner.SCHEMES if scheme.key in SCHEME_KEYS]
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
