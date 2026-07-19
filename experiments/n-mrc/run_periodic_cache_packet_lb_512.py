#!/usr/bin/env python3
"""Compare seven packet-LB schemes with 1us/5us periodic switch state."""

import concurrent.futures
import csv
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "experiments/n-mrc/run_packet_lb_common_workloads_512.py"
OUT = ROOT / "experiments/n-mrc/output/nmrc_periodic_cache_packet_lb_512"
SCHEME_ORDER = ["ecmp_rr", "ops", "reps", "mrc", "netaware", "sglb", "ar"]
OLD_DIRECT_READ_SUMMARY = ROOT / (
    "experiments/n-mrc/output/"
    "nmrc_packet_lb_common_workloads_nmrc_104060_4210_512/summary.csv")


def load_runner():
    spec = importlib.util.spec_from_file_location("packet_lb_common", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configured_runner():
    runner = load_runner()
    existing = {scheme.key: scheme for scheme in runner.SCHEMES}
    existing["sglb"] = runner.Scheme("sglb", "SGLB", "sglb")
    existing["ar"] = runner.Scheme("ar", "AR", "adaptive-routing")
    runner.OUT = OUT
    runner.SCHEMES = [existing[key] for key in SCHEME_ORDER]
    return runner


def periodic_cache_appendix(rows):
    old_rows = []
    if OLD_DIRECT_READ_SUMMARY.exists():
        with OLD_DIRECT_READ_SUMMARY.open(encoding="utf-8") as handle:
            old_rows = list(csv.DictReader(handle))
    old_nmrc = {
        row["workload"]: row for row in old_rows if row["scheme"] == "netaware"}
    new_nmrc = {
        row["workload"]: row for row in rows if row["scheme"] == "netaware"}

    lines = [
        "",
        "## Periodic-Cache Validation",
        "",
        "- SGLB runtime diagnostics require local quality update 1us and GCN export update 5us.",
        "- n-MRC runtime diagnostics require leaf-local update 1us and spine-export update 5us.",
        "- n-MRC path scoring reads the spine export cache; live remote queues remain visible only to offline trace diagnostics.",
        "- The cache abstraction has zero control-message propagation cost. First use creates a current snapshot on demand; later updates follow the 5us timer.",
    ]
    if not old_nmrc:
        lines.append("- Prior direct-read n-MRC summary was unavailable; no old/new comparison was generated.")
        return "\n".join(lines) + "\n"

    lines += [
        "",
        "### n-MRC Direct Read vs 5us Spine Cache",
        "",
        "| workload | old direct-read p99 | new 5us-cache p99 | change | old/new NACK |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for workload in [item.name for item in configured_runner().WORKLOADS]:
        old = old_nmrc.get(workload)
        new = new_nmrc.get(workload)
        if not old or not new:
            continue
        old_p99 = float(old["p99_fct_us"])
        new_p99 = float(new["p99_fct_us"])
        change = 100.0 * (new_p99 / old_p99 - 1.0) if old_p99 else 0.0
        lines.append(
            f"| {workload} | {old_p99:.3f} | {new_p99:.3f} | "
            f"{change:+.2f}% | {old['nacks']}/{new['nacks']} |")
    lines += [
        "",
        "The controlled path hotspot starts fixed 300-Gbit/s background load at time 0 and delays target flows until 250us. The first n-MRC lookup therefore observes an already established hotspot when it creates the on-demand spine snapshot. Its small old/new difference validates the steady 5us cache cadence for this persistent hotspot, but does not validate detection of a newly appearing sub-5us burst.",
    ]
    return "\n".join(lines) + "\n"


def main():
    runner = configured_runner()
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
    named_report = OUT / "nmrc_periodic_cache_packet_lb_512_for_gpt.md"
    named_report.write_text(
        report.read_text(encoding="utf-8") + periodic_cache_appendix(rows),
        encoding="utf-8")
    print(named_report)

    failed = [row for row in rows if not runner.row_complete(row)]
    if failed:
        raise SystemExit(f"{len(failed)} incomplete or invalid runs")


if __name__ == "__main__":
    main()
