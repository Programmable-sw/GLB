#!/usr/bin/env python3
# 测试 SGLB 五因子权重在默认 128 节点 3 层拓扑、tornado/permutation 流量和 32MiB 流下的表现。
import csv
import math
import os
import random
import re
import shutil
import statistics
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "sim" / "datacenter" / "htsim_roce"
OUT = Path(os.environ.get(
    "SGLB_FACTOR_OUT",
    str(Path(__file__).resolve().parent / "output_sglb_factor_compare" / "scenario_compare"),
)).resolve()

NODES = int(os.environ.get("SGLB_FACTOR_NODES", "128"))
TIERS = int(os.environ.get("SGLB_FACTOR_TIERS", "3"))
LINKSPEED_MBPS = int(os.environ.get("SGLB_FACTOR_LINKSPEED_MBPS", "400000"))
MTU = int(os.environ.get("SGLB_FACTOR_MTU", "4096"))
PATHS = int(os.environ.get("SGLB_FACTOR_PATHS", str(NODES)))
SEED = int(os.environ.get("SGLB_FACTOR_SEED", "13"))
FLOW_SIZE = int(os.environ.get("SGLB_FACTOR_FLOW_SIZE", str(32 * 1024 * 1024)))
END_US = int(os.environ.get("SGLB_FACTOR_END_US", "12000"))
CC_MODE = os.environ.get("SGLB_FACTOR_CC", "dcqcn_variant")
SCENARIO = os.environ.get("SGLB_FACTOR_SCENARIO", "tornado")
KEEP_RAW = os.environ.get("KEEP_RAW_OUTPUT") == "1"
NORMALIZE_SGLB = os.environ.get("SGLB_FACTOR_NORMALIZE") == "1"
QUALITY_BUCKET = float(os.environ.get(
    "SGLB_FACTOR_QUALITY_BUCKET",
    "0.125" if NORMALIZE_SGLB else "20",
))
SGLB_LOCAL_UPDATE_US = float(os.environ.get("SGLB_FACTOR_LOCAL_UPDATE_US", "1"))
SGLB_REMOTE_UPDATE_US = float(os.environ.get("SGLB_FACTOR_REMOTE_UPDATE_US", "15"))
SGLB_REMOTE_AGING_US = float(os.environ.get("SGLB_FACTOR_REMOTE_AGING_US", "30"))

FINISH_RE = re.compile(r"Flow Roce_(\d+)_(\d+) \d+ finished at ([0-9.]+)")

FACTOR_SETS = [
    {
        "label": "local-only",
        "short_label": "Local only",
        "factors": (1.00, 0.10, 0.00, 0.00, 0.00),
    },
    {
        "label": "local-leaning",
        "short_label": "Local leaning",
        "factors": (0.70, 0.07, 0.30, 0.03, 0.10),
    },
    {
        "label": "balanced-current",
        "short_label": "Balanced",
        "factors": (0.50, 0.05, 0.50, 0.05, 0.20),
    },
    {
        "label": "remote-heavy",
        "short_label": "Remote heavy",
        "factors": (0.30, 0.03, 0.70, 0.07, 0.30),
    },
]


def fmt_us(value):
    return f"{value:g}"


def pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, int(round((len(values) - 1) * p)))
    return values[idx]


def flow(src, dst, size, start_us=0.0):
    return {
        "src": src,
        "dst": dst,
        "size": size,
        "start_us": start_us,
        "start_ps": int(start_us * 1_000_000),
    }


def tornado_flows(nodes, size):
    shift = nodes // 2
    return [flow(src, (src + shift) % nodes, size) for src in range(nodes)]


def permutation_flows(nodes, size, seed):
    rng = random.Random(seed)
    srcs = list(range(nodes))
    dsts = list(range(nodes))
    rng.shuffle(srcs)
    rng.shuffle(dsts)
    for i in range(nodes):
        if srcs[i] == dsts[i]:
            j = (i + 1) % nodes
            dsts[i], dsts[j] = dsts[j], dsts[i]
    return [flow(srcs[i], dsts[i], size) for i in range(nodes)]


def scenario_flows():
    if SCENARIO == "tornado":
        return tornado_flows(NODES, FLOW_SIZE)
    if SCENARIO == "permutation":
        return permutation_flows(NODES, FLOW_SIZE, SEED)
    raise ValueError(f"unsupported SGLB_FACTOR_SCENARIO={SCENARIO}")


def write_tm(path, flows):
    with path.open("w") as fh:
        print("Nodes", NODES, file=fh)
        print("Connections", len(flows), file=fh)
        for flow_id, item in enumerate(flows, 1):
            print(
                f"{item['src']}->{item['dst']} id {flow_id} "
                f"start {item['start_ps']} size {item['size']}",
                file=fh,
            )


def parse_stdout(stdout_file, flow_map):
    rows = []
    for line in stdout_file.read_text(errors="ignore").splitlines():
        match = FINISH_RE.search(line)
        if not match:
            continue
        src = int(match.group(1))
        dst = int(match.group(2))
        finish_us = float(match.group(3))
        meta = flow_map.get((src, dst))
        if not meta:
            continue
        rows.append({
            "src": src,
            "dst": dst,
            "start_us": meta["start_us"],
            "finish_us": finish_us,
            "fct_us": finish_us - meta["start_us"],
        })
    return rows


def metrics_for_rows(rows, total):
    fcts = [row["fct_us"] for row in rows]
    return {
        "completed": len(rows),
        "completion_pct": 100.0 * len(rows) / total if total else 0.0,
        "avg_fct_us": statistics.mean(fcts) if fcts else 0.0,
        "p50_fct_us": pct(fcts, 0.50),
        "p95_fct_us": pct(fcts, 0.95),
        "p99_fct_us": pct(fcts, 0.99),
        "p999_fct_us": pct(fcts, 0.999),
        "max_fct_us": max(fcts) if fcts else 0.0,
    }


def slow_tor_uplinks():
    return max(1, math.ceil(NODES * 0.03))


def factors_for_run(item):
    factors = item["factors"]
    if not NORMALIZE_SGLB:
        return factors

    total = sum(factors)
    if total <= 0:
        return factors
    return tuple(value / total for value in factors)


def command_for(item, tm, dat_file, flow_count):
    lq, lu, rq, ru, rb = factors_for_run(item)
    cmd = [
        str(SIM),
        "-o", str(dat_file),
        "-tm", str(tm),
        "-nodes", str(NODES),
        "-conns", str(flow_count),
        "-tiers", str(TIERS),
        "-linkspeed", str(LINKSPEED_MBPS),
        "-queue_type", "lossless_input_ecn",
        "-host_queue_type", "prio",
        "-mtu", str(MTU),
        "-end", str(END_US),
        "-paths", str(PATHS),
        "-seed", str(SEED),
        "-lb", "sglb",
        "-sglb_score_mode", "legacy",
        "-cc", CC_MODE,
        "-hop_latency", "0.5",
        "-switch_latency", "0.5",
        "-slow_tor_uplinks", str(slow_tor_uplinks()),
        "-slow_tor_uplink_divisor", "2",
        "-sglb_update_us", fmt_us(SGLB_LOCAL_UPDATE_US),
        "-sglb_gcn_update_us", fmt_us(SGLB_REMOTE_UPDATE_US),
        "-sglb_gcn_aging_us", fmt_us(SGLB_REMOTE_AGING_US),
        "-sglb_quality_bucket", str(QUALITY_BUCKET),
        "-sglb_downstream_weight", "1.0",
        "-sglb_factors", str(lq), str(lu), str(rq), str(ru), str(rb),
    ]
    if NORMALIZE_SGLB:
        cmd.append("-sglb_normalize")
    return cmd


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_summary(rows, path):
    labels = [row["short_label"] for row in rows]
    metrics = [
        ("avg_fct_us", "Avg"),
        ("p99_fct_us", "p99"),
        ("p999_fct_us", "p99.9"),
    ]

    x = list(range(len(rows)))
    width = 0.24
    fig, ax = plt.subplots(figsize=(10, 5.8))
    colors = ["#3274A1", "#E1812C", "#3A923A"]

    for idx, (metric, name) in enumerate(metrics):
        offset = (idx - 1) * width
        values = [row[metric] for row in rows]
        bars = ax.bar([pos + offset for pos in x], values, width, label=name, color=colors[idx])
        ax.bar_label(bars, fmt="%.0f", padding=3, fontsize=8)

    ax.set_ylabel("FCT (us)")
    ax.set_xlabel("sglb five-factor coefficient set")
    mode = "normalized" if NORMALIZE_SGLB else "raw"
    ax.set_title(f"sglb FCT under different quality coefficients ({SCENARIO}, {CC_MODE}, {mode})", pad=14)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=12, ha="right")
    ax.grid(axis="y", linestyle="--", linewidth=0.6, alpha=0.45)
    ax.legend(ncol=3, loc="upper right", frameon=False)

    ymax = max(row["p999_fct_us"] for row in rows) * 1.15
    ax.set_ylim(0, ymax)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def write_report(rows, path, chart_name):
    lines = [
        "# sglb Five-Factor Sweep",
        "",
        f"Scenario: `{SCENARIO}`, CC: `{CC_MODE}`, nodes: `{NODES}`, flow size: `{FLOW_SIZE}` bytes.",
        f"sglb score mode: `{'normalized' if NORMALIZE_SGLB else 'raw'}`, quality bucket: `{QUALITY_BUCKET}`.",
        f"sglb update intervals: local quality `{fmt_us(SGLB_LOCAL_UPDATE_US)}us`, remote GCN `{fmt_us(SGLB_REMOTE_UPDATE_US)}us`, aging `{fmt_us(SGLB_REMOTE_AGING_US)}us`.",
        "",
        f"![sglb factor FCT bars]({chart_name})",
        "",
        "| label | Q(L) | L(L) | Q(R) | L(R) | B(R) | avg FCT us | p99 FCT us | p99.9 FCT us |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {label} | {lq:.3f} | {lu:.3f} | {rq:.3f} | {ru:.3f} | {rb:.3f} | "
            "{avg:.3f} | {p99:.3f} | {p999:.3f} |".format(
                label=row["label"],
                lq=row["alpha_local_queue"],
                lu=row["alpha_local_util"],
                rq=row["alpha_remote_queue"],
                ru=row["alpha_remote_util"],
                rb=row["alpha_remote_busy"],
                avg=row["avg_fct_us"],
                p99=row["p99_fct_us"],
                p999=row["p999_fct_us"],
            )
        )
    path.write_text("\n".join(lines) + "\n")


def main():
    if not SIM.exists():
        raise SystemExit(f"missing simulator binary: {SIM}")
    if OUT.exists() and not KEEP_RAW:
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    run_dir = OUT / "raw"
    run_dir.mkdir(parents=True, exist_ok=True)

    flows = scenario_flows()
    tm = run_dir / f"sglb_factor_{SCENARIO}.cm"
    write_tm(tm, flows)
    flow_map = {(item["src"], item["dst"]): item for item in flows}

    rows = []
    per_flow_rows = []
    for item in FACTOR_SETS:
        label = item["label"]
        stdout_file = run_dir / f"{label}.stdout"
        dat_file = run_dir / f"{label}.dat"
        cmd = command_for(item, tm, dat_file, len(flows))
        (run_dir / f"{label}.cmd").write_text(" ".join(cmd) + "\n")

        with stdout_file.open("w") as fh:
            subprocess.run(cmd, cwd=run_dir, stdout=fh, stderr=subprocess.STDOUT, check=True)

        parsed = parse_stdout(stdout_file, flow_map)
        for row in parsed:
            per_flow_rows.append({"label": label, **row})

        lq, lu, rq, ru, rb = factors_for_run(item)
        summary = {
            "scenario": SCENARIO,
            "label": label,
            "short_label": item["short_label"],
            "normalized": NORMALIZE_SGLB,
            "quality_bucket": QUALITY_BUCKET,
            "alpha_local_queue": lq,
            "alpha_local_util": lu,
            "alpha_remote_queue": rq,
            "alpha_remote_util": ru,
            "alpha_remote_busy": rb,
            "nodes": NODES,
            "total_flows": len(flows),
            "cc": CC_MODE,
            **metrics_for_rows(parsed, len(flows)),
        }
        rows.append(summary)
        print(
            "{label:18s} done={completed:3d}/{total_flows:<3d} "
            "avg={avg_fct_us:8.3f} p99={p99_fct_us:8.3f} p99.9={p999_fct_us:8.3f}".format(**summary)
        )

    if not KEEP_RAW:
        shutil.rmtree(run_dir)

    summary_csv = OUT / "summary.csv"
    per_flow_csv = OUT / "per_flow.csv"
    chart = OUT / "sglb_factor_fct_bars.png"
    report = OUT / "comparison_report.md"
    write_csv(summary_csv, rows)
    write_csv(per_flow_csv, per_flow_rows)
    plot_summary(rows, chart)
    write_report(rows, report, chart.name)

    print(summary_csv)
    print(per_flow_csv)
    print(chart)
    print(report)


if __name__ == "__main__":
    main()
