#!/usr/bin/env python3
"""Small STOR aging validation matrix.

Runs two compact stress cases:
- long_slow: persistent severe slow ToR uplinks
- burst_recovery: foreground flows plus a short burst that drains later
"""

import csv
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "sim" / "datacenter" / "htsim_roce"
OUT = Path(
    os.environ.get(
        "STOR_AGING_OUT",
        ROOT / "experiments" / "n-mrc" / "output" / "stor_aging_compare",
    )
).resolve()

NODES = int(os.environ.get("STOR_AGING_NODES", "128"))
END_US = int(os.environ.get("STOR_AGING_END_US", "8000"))
SEED = int(os.environ.get("STOR_AGING_SEED", "13"))
FLOW_SIZE = int(os.environ.get("STOR_AGING_FLOW_BYTES", str(4 * 1024 * 1024)))
BURST_SIZE = int(os.environ.get("STOR_AGING_BURST_BYTES", str(512 * 1024)))

AGING_PROFILES = [
    ("packet", ["-stor_aging", "packet"]),
    (
        "time_ewma_2_5_5",
        ["-stor_aging", "time_ewma", "-stor_time_ewma_us", "2", "5", "5"],
    ),
    (
        "time_ewma_20_50_50",
        ["-stor_aging", "time_ewma", "-stor_time_ewma_us", "20", "50", "50"],
    ),
    (
        "time_ewma_50_100_100",
        ["-stor_aging", "time_ewma", "-stor_time_ewma_us", "50", "100", "100"],
    ),
    (
        "hybrid_5_10_16_1",
        [
            "-stor_aging",
            "hybrid",
            "-stor_hybrid_hold_us",
            "5",
            "10",
            "-stor_hybrid_probe",
            "16",
            "1",
        ],
    ),
    (
        "hybrid_10_20_64_2",
        [
            "-stor_aging",
            "hybrid",
            "-stor_hybrid_hold_us",
            "10",
            "20",
            "-stor_hybrid_probe",
            "64",
            "2",
        ],
    ),
    (
        "hybrid_20_50_128_2",
        [
            "-stor_aging",
            "hybrid",
            "-stor_hybrid_hold_us",
            "20",
            "50",
            "-stor_hybrid_probe",
            "128",
            "2",
        ],
    ),
]

FINISH_RE = re.compile(r"Flow Roce_(\d+)_(\d+) \d+ finished at ([0-9.]+)")


def write_tm(path, nodes, mode):
    rows = []
    for src in range(nodes):
        rows.append((src, (src + nodes // 2) % nodes, 0, FLOW_SIZE))

    if mode == "burst_recovery":
        burst_start_ps = int(float(os.environ.get("STOR_AGING_BURST_START_US", "0")) * 1_000_000)
        for src in range(0, nodes, 2):
            rows.append((src, (src + nodes // 2) % nodes, burst_start_ps, BURST_SIZE))

    with path.open("w") as fh:
        print("Nodes", nodes, file=fh)
        print("Connections", len(rows), file=fh)
        for flow_id, (src, dst, start_ps, size) in enumerate(rows, 1):
            print(f"{src}->{dst} id {flow_id} start {start_ps} size {size}", file=fh)
    return len(rows)


def parse_diag(stdout):
    vals = {}
    for line in stdout.read_text(errors="ignore").splitlines():
        if not line.startswith(("RoceDiag ", "QueueDiag ", "StorDiag ")):
            continue
        for item in line.split()[1:]:
            if "=" not in item:
                continue
            key, raw = item.split("=", 1)
            vals[key] = float(raw) if "." in raw else int(raw)
    return vals


def pct(values, percentile):
    if not values:
        return 0.0
    idx = min(len(values) - 1, int(round((len(values) - 1) * percentile)))
    return values[idx]


def parse_fct(stdout):
    fcts = []
    finishes = []
    for line in stdout.read_text(errors="ignore").splitlines():
        match = FINISH_RE.search(line)
        if not match:
            continue
        finish_us = float(match.group(3))
        fcts.append(finish_us)
        finishes.append(finish_us)
    fcts.sort()
    if not fcts:
        return {
            "completed": 0,
            "avg_fct_us": 0.0,
            "p99_fct_us": 0.0,
            "p999_fct_us": 0.0,
            "cct_us": 0.0,
        }
    return {
        "completed": len(fcts),
        "avg_fct_us": sum(fcts) / len(fcts),
        "p99_fct_us": pct(fcts, 0.99),
        "p999_fct_us": pct(fcts, 0.999),
        "cct_us": max(finishes),
    }


def command_for(mode, profile, extra, tm, dat_file, conns):
    cmd = [
        str(SIM),
        "-o",
        str(dat_file),
        "-tm",
        str(tm),
        "-nodes",
        str(NODES),
        "-conns",
        str(conns),
        "-tiers",
        "2",
        "-linkspeed",
        "400000",
        "-queue_type",
        "composite_ecn_lb",
        "-host_queue_type",
        "prio",
        "-mtu",
        "4096",
        "-end",
        str(END_US),
        "-paths",
        str(NODES),
        "-seed",
        str(SEED),
        "-lb",
        "grade",
        "-cc",
        "dcqcn_variant",
        "-roce_rx_mode",
        "sp",
        "-roce_sack_bitmap_bits",
        "64",
        "-stor_feedback_pkts",
        "16",
    ]
    if mode == "long_slow":
        cmd += [
            "-slow_tor_uplinks",
            os.environ.get("STOR_AGING_SLOW_TOR_UPLINKS", "16"),
            "-slow_tor_uplink_divisor",
            os.environ.get("STOR_AGING_SLOW_DIVISOR", "8"),
            "-slow_tor_uplink_select",
            "random-sparse",
        ]
    return cmd + extra


def run_case(mode, profile, extra):
    case_dir = OUT / mode
    case_dir.mkdir(parents=True, exist_ok=True)
    tm = case_dir / f"{mode}.cm"
    conns = write_tm(tm, NODES, mode)
    stdout = case_dir / f"{profile}.stdout"
    dat_file = case_dir / f"{profile}.dat"
    cmd = command_for(mode, profile, extra, tm, dat_file, conns)
    (case_dir / f"{profile}.cmd").write_text(" ".join(cmd) + "\n")
    with stdout.open("w") as fh:
        subprocess.run(cmd, cwd=case_dir, stdout=fh, stderr=subprocess.STDOUT, check=True)

    row = {
        "case": mode,
        "profile": profile,
        "nodes": NODES,
        "connections": conns,
    }
    row.update(parse_fct(stdout))
    row.update(parse_diag(stdout))
    return row


def write_report(rows):
    lines = [
        "# STOR Aging Comparison",
        "",
        "| case | profile | completed | avg FCT us | p99 FCT us | p99.9 FCT us | CCT us | ECN echo | trims | nacks | min score | avoid in/out | selected G/D/B/A |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {case} | {profile} | {completed}/{connections} | {avg_fct_us:.3f} | "
            "{p99_fct_us:.3f} | {p999_fct_us:.3f} | {cct_us:.3f} | "
            "{ecn_echo_acks} | {composite_trims} | {nacks} | {stor_min_score} | "
            "{stor_avoid_entries}/{stor_avoid_exits} | "
            "{stor_selected_good}/{stor_selected_degraded}/{stor_selected_bad}/{stor_selected_avoid} |".format(
                **row
            )
        )
    (OUT / "comparison_report.md").write_text("\n".join(lines) + "\n")


def main():
    if not SIM.exists():
        raise SystemExit(f"missing simulator binary: {SIM}")
    OUT.mkdir(parents=True, exist_ok=True)

    rows = []
    for mode in ["long_slow", "burst_recovery"]:
        for profile, extra in AGING_PROFILES:
            print(f"run {mode} {profile}", flush=True)
            rows.append(run_case(mode, profile, extra))

    fields = sorted({key for row in rows for key in row.keys()})
    summary = OUT / "summary.csv"
    with summary.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    write_report(rows)
    print(summary, flush=True)
    print(OUT / "comparison_report.md", flush=True)


if __name__ == "__main__":
    main()
