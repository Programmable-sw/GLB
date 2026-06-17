#!/usr/bin/env python3
# 生成 README 负载均衡图，覆盖健康 2048 节点 2 层和 1024 节点 3 层 3% 非对称场景、4/32MiB 流。
import csv
import concurrent.futures
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "sim" / "datacenter" / "htsim_roce"
EXP_DIR = Path(__file__).resolve().parent
OUT = Path(
    os.environ.get(
        "README_ALL_LB_OUT",
        str(EXP_DIR / "output" / "readme_all_lb_lossless_input_ecn_sp"),
    )
).resolve()

LINKSPEED_MBPS = 400000
MTU = 4096
SEED = int(os.environ.get("README_ALL_LB_SEED", "13"))
END_US = int(os.environ.get("README_ALL_LB_END_US", "10000"))
TRAFFIC = "tornado"
CC_MODE = "dcqcn_variant"
RX_MODE = "sp"
SACK_BITMAP_BITS = int(os.environ.get("README_ALL_LB_SACK_BITMAP_BITS", "64"))
FLOW_SIZES_MIB = [
    int(item.strip())
    for item in os.environ.get("README_ALL_LB_FLOW_SIZES", "4,32").split(",")
    if item.strip()
]
SCHEME_SCOPE = os.environ.get("README_ALL_LB_SCHEME_SCOPE", "all").strip().lower()
KEEP_RAW = os.environ.get("KEEP_RAW_OUTPUT", "1") != "0"
FORCE = os.environ.get("FORCE_RERUN") == "1"
MAX_WORKERS = int(os.environ.get("README_ALL_LB_WORKERS", "4"))
CJK_FONT = None

FINISH_RE = re.compile(r"Flow Roce_(\d+)_(\d+) \d+ finished at ([0-9.]+)")

SCENARIOS = [
    {
        "key": "healthy",
        "name": "healthy_2048n_2tier",
        "title": "健康网络",
        "nodes": 2048,
        "tiers": 2,
        "flow_sizes_mib": FLOW_SIZES_MIB,
        "description": "2048 nodes / 2-tier, all links 400Gbps",
    },
    {
        "key": "asym_tor3pct",
        "name": "asym_tor3pct_1024n_3tier",
        "title": "非对称带宽",
        "nodes": 1024,
        "tiers": 3,
        "flow_sizes_mib": FLOW_SIZES_MIB,
        "slow_tor_uplink_fraction": 0.03,
        "slow_tor_uplink_divisor": 2,
        "description": "1024 nodes / 3-tier, 3% ToR uplinks at half bandwidth",
    },
]

ALL_SCHEMES = [
    ("ecmp", "ECMP", "ecmp", []),
    ("ecmp_rr", "ECMP-RR", "ecmp_rr", []),
    ("ops", "OPS", "ops", []),
    ("rr", "RR", "rr", []),
    ("reps", "REPS", "reps", []),
    ("n-mrc", "N-MRC", "n-mrc", []),
    ("mrc", "MRC", "mrc", []),
    ("conweave", "CONWEAVE", "conweave", []),
    ("adaptive-routing", "AR", "adaptive-routing", ["-ar_granularity", "packet"]),
    ("drill", "DRILL", "drill", []),
    ("glb", "GLB", "glb", []),
]

PER_PACKET_SCHEME_LABELS = [
    "ecmp_rr",
    "ops",
    "rr",
    "reps",
    "n-mrc",
    "mrc",
    "adaptive-routing",
    "drill",
    "glb",
]


def selected_schemes():
    if SCHEME_SCOPE == "all":
        return ALL_SCHEMES
    if SCHEME_SCOPE == "packet":
        return [scheme for scheme in ALL_SCHEMES if scheme[0] in PER_PACKET_SCHEME_LABELS]

    requested = [item.strip() for item in SCHEME_SCOPE.split(",") if item.strip()]
    by_label = {scheme[0]: scheme for scheme in ALL_SCHEMES}
    missing = [label for label in requested if label not in by_label]
    if missing:
        raise ValueError(f"unknown README_ALL_LB_SCHEME_SCOPE labels: {', '.join(missing)}")
    return [by_label[label] for label in requested]


SCHEMES = selected_schemes()
PER_PACKET_SCHEMES = [scheme for scheme in ALL_SCHEMES if scheme[0] in PER_PACKET_SCHEME_LABELS]

METRICS = [
    ("avg_fct_us", "平均FCT"),
    ("p99_fct_us", "p99 FCT"),
    ("p999_fct_us", "p99.9 FCT"),
    ("cct_us", "CCT"),
]
NO_CCT_METRICS = METRICS[:3]

COLORS = {
    "ecmp": "#6b7280",
    "ecmp_rr": "#9ca3af",
    "ops": "#2563eb",
    "rr": "#38bdf8",
    "reps": "#059669",
    "n-mrc": "#dc2626",
    "mrc": "#ea580c",
    "conweave": "#f59e0b",
    "adaptive-routing": "#7c3aed",
    "drill": "#db2777",
    "glb": "#111827",
}


def configure_fonts():
    global CJK_FONT
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            fm.fontManager.addfont(path)
            CJK_FONT = fm.FontProperties(fname=path)
            break
    plt.rcParams["font.family"] = "DejaVu Sans"
    plt.rcParams["axes.unicode_minus"] = False


def cjk_text_kwargs():
    return {"fontproperties": CJK_FONT} if CJK_FONT else {}


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


def write_tm(path, nodes, flows):
    with path.open("w") as fh:
        print("Nodes", nodes, file=fh)
        print("Connections", len(flows), file=fh)
        for flow_id, item in enumerate(flows, 1):
            print(
                f"{item['src']}->{item['dst']} id {flow_id} "
                f"start {item['start_ps']} size {item['size']}",
                file=fh,
            )


def parse_stdout(stdout_file, flow_map):
    rows = []
    if not stdout_file.exists():
        return rows
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
        rows.append(
            {
                "src": src,
                "dst": dst,
                "start_us": meta["start_us"],
                "finish_us": finish_us,
                "fct_us": finish_us - meta["start_us"],
            }
        )
    return rows


def metrics_for(rows, total):
    fcts = [row["fct_us"] for row in rows]
    cct = 0.0
    if rows:
        cct = max(row["finish_us"] for row in rows) - min(row["start_us"] for row in rows)
    return {
        "completed": len(rows),
        "completion_pct": 100.0 * len(rows) / total if total else 0.0,
        "avg_fct_us": sum(fcts) / len(fcts) if fcts else 0.0,
        "p99_fct_us": pct(fcts, 0.99),
        "p999_fct_us": pct(fcts, 0.999),
        "max_fct_us": max(fcts) if fcts else 0.0,
        "cct_us": cct,
    }


def validate_generated_fattree(nodes, tiers):
    generated = 0
    k = 0
    if tiers == 3:
        while generated < nodes:
            k += 1
            generated = k * k * k // 4
    elif tiers == 2:
        while generated < nodes:
            k += 1
            generated = k * k // 2
    else:
        raise ValueError(f"unsupported tiers={tiers}")
    if generated != nodes:
        raise ValueError(f"{nodes} nodes is not supported by generated {tiers}-tier fat-tree")
    return k


def slow_tor_uplinks_for(scenario):
    fraction = scenario.get("slow_tor_uplink_fraction", 0.0)
    if not fraction:
        return 0
    validate_generated_fattree(scenario["nodes"], scenario["tiers"])
    return max(1, math.ceil(scenario["nodes"] * fraction))


def scenario_run_name(scenario, size_mib):
    return f"{scenario['name']}_{size_mib}m"


def command_for(scenario, size_mib, tm, dat_file, scheme, flow_count):
    _label, _display, lb_mode, extra = scheme
    cmd = [
        str(SIM),
        "-o",
        str(dat_file),
        "-tm",
        str(tm),
        "-nodes",
        str(scenario["nodes"]),
        "-conns",
        str(flow_count),
        "-tiers",
        str(scenario["tiers"]),
        "-linkspeed",
        str(LINKSPEED_MBPS),
        "-queue_type",
        "lossless_input_ecn",
        "-host_queue_type",
        "prio",
        "-mtu",
        str(MTU),
        "-end",
        str(END_US),
        "-paths",
        str(scenario["nodes"]),
        "-seed",
        str(SEED),
        "-lb",
        lb_mode,
        "-cc",
        CC_MODE,
        "-roce_rx_mode",
        RX_MODE,
        "-roce_sack_bitmap_bits",
        str(SACK_BITMAP_BITS),
        "-hop_latency",
        "0.5",
        "-switch_latency",
        "0.5",
    ]
    slow_tor_uplinks = slow_tor_uplinks_for(scenario)
    if slow_tor_uplinks:
        cmd += [
            "-slow_tor_uplinks",
            str(slow_tor_uplinks),
            "-slow_tor_uplink_divisor",
            str(scenario["slow_tor_uplink_divisor"]),
        ]
    return cmd + extra


def run_scheme(scenario, size_mib, flows, flow_map, case_dir, tm, scheme):
    label = scheme[0]
    stdout_file = case_dir / f"{label}.stdout"
    dat_file = case_dir / f"{label}.dat"
    cmd_file = case_dir / f"{label}.cmd"
    cmd = command_for(scenario, size_mib, tm, dat_file, scheme, len(flows))
    cmd_text = " ".join(cmd) + "\n"
    cached_cmd_text = cmd_file.read_text() if cmd_file.exists() else ""

    parsed = parse_stdout(stdout_file, flow_map)
    if FORCE or len(parsed) != len(flows) or cached_cmd_text != cmd_text:
        cmd_file.write_text(cmd_text)
        print(
            f"run {scenario['title']} {size_mib}MiB {label} "
            f"({len(parsed)}/{len(flows)} cached)",
            flush=True,
        )
        with stdout_file.open("w") as fh:
            subprocess.run(cmd, cwd=case_dir, stdout=fh, stderr=subprocess.STDOUT, check=True)
        parsed = parse_stdout(stdout_file, flow_map)
    else:
        print(f"skip {scenario['title']} {size_mib}MiB {label} cached", flush=True)

    metrics = metrics_for(parsed, len(flows))
    row = {
        "scenario": scenario["key"],
        "scenario_title": scenario["title"],
        "scenario_name": scenario["name"],
        "nodes": scenario["nodes"],
        "tiers": scenario["tiers"],
        "flow_size_mib": size_mib,
        "scheme": label,
        "scheme_display": scheme[1],
        "traffic": TRAFFIC,
        "cc": CC_MODE,
        "rx_mode": RX_MODE,
        "sack_bitmap_bits": SACK_BITMAP_BITS,
        "queue_type": "lossless_input_ecn",
        "linkspeed_mbps": LINKSPEED_MBPS,
        "mtu": MTU,
        "end_us": END_US,
        "slow_tor_uplinks": slow_tor_uplinks_for(scenario),
        **metrics,
    }
    print(
        "{scenario_title} {flow_size_mib:>2}MiB {scheme:16s} "
        "done={completed:4d}/{total:<4d} avg={avg_fct_us:8.3f} "
        "p99={p99_fct_us:8.3f} p99.9={p999_fct_us:8.3f} cct={cct_us:8.3f}".format(
            total=len(flows), **row
        ),
        flush=True,
    )
    return row


def run_case(scenario, size_mib):
    size_bytes = size_mib * 1024 * 1024
    flows = tornado_flows(scenario["nodes"], size_bytes)
    flow_map = {(item["src"], item["dst"]): item for item in flows}
    case_dir = OUT / "raw" / scenario_run_name(scenario, size_mib)
    case_dir.mkdir(parents=True, exist_ok=True)
    tm = case_dir / f"{scenario_run_name(scenario, size_mib)}.cm"
    write_tm(tm, scenario["nodes"], flows)

    rows = []
    workers = max(1, min(MAX_WORKERS, len(SCHEMES)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(run_scheme, scenario, size_mib, flows, flow_map, case_dir, tm, scheme)
            for scheme in SCHEMES
        ]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())

    rows.sort(key=lambda row: [scheme[0] for scheme in SCHEMES].index(row["scheme"]))

    if not KEEP_RAW:
        shutil.rmtree(case_dir)
    return rows


def normalize_rows(rows):
    out = []
    grouped = {}
    for row in rows:
        grouped.setdefault((row["scenario"], row["flow_size_mib"]), []).append(row)

    for (_scenario, _size_mib), group in grouped.items():
        best = {}
        for metric, _label in METRICS:
            values = [row[metric] for row in group if row[metric] > 0]
            best[metric] = min(values) if values else 0.0
        for row in group:
            normed = dict(row)
            for metric, _label in METRICS:
                denom = best[metric]
                normed[f"norm_{metric}"] = row[metric] / denom if denom else 0.0
            out.append(normed)
    return out


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_scenario(
    scenario,
    rows,
    schemes=SCHEMES,
    metrics=METRICS,
    suffix="all_lb",
    title_scope="全方案",
    horizontal_value_labels=False,
    sparse_value_labels=False,
):
    scenario_rows = [row for row in rows if row["scenario"] == scenario["key"]]
    sizes = scenario["flow_sizes_mib"]
    if sparse_value_labels:
        figsize = (23.5, 5.9)
    elif horizontal_value_labels:
        figsize = (21.5, 6.4)
    else:
        figsize = (17.5, 6.2)
    fig, axes = plt.subplots(1, len(sizes), figsize=figsize, sharey=True)
    if len(sizes) == 1:
        axes = [axes]

    scheme_labels = [scheme[0] for scheme in schemes]
    scheme_display = {scheme[0]: scheme[1] for scheme in schemes}
    x_step = 1.55 if sparse_value_labels else 1.0
    x_positions = [idx * x_step for idx in range(len(metrics))]
    group_width = 1.10 if sparse_value_labels else 0.78
    width = group_width / len(scheme_labels)

    max_y = 1.0
    for ax, size_mib in zip(axes, sizes):
        group = [row for row in scenario_rows if row["flow_size_mib"] == size_mib]
        by_scheme = {row["scheme"]: row for row in group}
        for scheme_idx, scheme in enumerate(scheme_labels):
            row = by_scheme.get(scheme)
            values = [row[f"norm_{metric}"] if row else 0.0 for metric, _label in metrics]
            max_y = max(max_y, *values)
            offset = (scheme_idx - (len(scheme_labels) - 1) / 2) * width
            xs = [x + offset for x in x_positions]
            bars = ax.bar(
                xs,
                values,
                width,
                label=scheme_display[scheme],
                color=COLORS[scheme],
                edgecolor="white",
                linewidth=0.35,
            )
            for bar, value in zip(bars, values):
                if value <= 0:
                    continue
                if horizontal_value_labels:
                    vertical_offset = 4
                    if not sparse_value_labels:
                        vertical_offset += (scheme_idx % 4) * 12
                    ax.annotate(
                        f"{value:.3f}",
                        xy=(bar.get_x() + bar.get_width() / 2, value),
                        xytext=(0, vertical_offset),
                        textcoords="offset points",
                        ha="center",
                        va="bottom",
                        fontsize=6.0 if sparse_value_labels else 5.8,
                        color="#374151",
                        clip_on=False,
                    )
                else:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        value + 0.025,
                        f"{value:.3f}",
                        ha="center",
                        va="bottom",
                        fontsize=5.6,
                        rotation=90,
                        color="#374151",
                    )
        ax.set_title(
            f"{scenario['title']} | {size_mib}MiB",
            fontsize=12,
            pad=12,
            **cjk_text_kwargs(),
        )
        ax.set_xticks(x_positions)
        ax.set_xticklabels([label for _metric, label in metrics], fontsize=9, **cjk_text_kwargs())
        ax.grid(axis="y", color="#d1d5db", linestyle="-", linewidth=0.7, alpha=0.7)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    axes[0].set_ylabel("FCT slowdown", fontsize=11)
    if sparse_value_labels:
        y_limit = max(max_y * 1.20, max_y + 0.18, 1.3)
    elif horizontal_value_labels:
        y_limit = max(max_y * 1.43, max_y + 0.36, 1.45)
    else:
        y_limit = min(max(max_y * 1.22, 1.35), max_y + 2.0)
    for ax in axes:
        ax.set_ylim(0, y_limit)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.0),
        ncol=min(10, len(scheme_labels)),
        frameon=False,
        fontsize=8,
    )
    fig.suptitle(
        (
            f"{scenario['title']} {title_scope} FCT slowdown | "
            "lossless_input_ecn + 出队列ECN + SP bitmap重传"
        ),
        y=0.93,
        fontsize=13,
        **cjk_text_kwargs(),
    )
    fig.text(
        0.5,
        0.02,
        (
            "400Gbps, MTU=4096B, queue=1BDP, ECN/PFC=0.2/0.8 queue, "
            "hop=0.5us, switch=0.5us, RTO=70us, CC=DCQCN_variant, traffic=tornado"
        ),
        ha="center",
        fontsize=8.5,
        color="#4b5563",
    )
    fig.tight_layout(rect=[0.02, 0.07, 1.0, 0.88])

    png = OUT / f"{scenario['key']}_{suffix}_slowdown.png"
    pdf = OUT / f"{scenario['key']}_{suffix}_slowdown.pdf"
    fig.savefig(png, dpi=220)
    fig.savefig(pdf)
    plt.close(fig)
    return png, pdf


def write_report(rows, chart_paths):
    lines = [
        "# README All-LB Lossless/SP Figures",
        "",
        "Parameters: `lossless_input_ecn`, dequeue ECN marking, `roce_rx_mode=sp`, "
        f"`sack_bitmap_bits={SACK_BITMAP_BITS}`, `queue=1BDP`, "
        "ECN/PFC `0.2/0.8 * queue`, RTO `70us`, CC `dcqcn_variant`.",
        "",
        "## Charts",
        "",
    ]
    for scenario in SCENARIOS:
        png, _pdf = chart_paths[scenario["key"]]
        lines.append(f"- {scenario['title']}: ![]({png.name})")

    lines += [
        "",
        "## Completion",
        "",
        "| scenario | size MiB | scheme | completed | avg FCT us | p99 FCT us | p99.9 FCT us | CCT us |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {scenario_title} | {flow_size_mib} | {scheme_display} | "
            "{completed}/{nodes} | {avg_fct_us:.3f} | {p99_fct_us:.3f} | "
            "{p999_fct_us:.3f} | {cct_us:.3f} |".format(**row)
        )
    (OUT / "README_all_lb_report.md").write_text("\n".join(lines) + "\n")


def main():
    configure_fonts()
    if not SIM.exists():
        raise SystemExit(f"missing simulator binary: {SIM}")
    OUT.mkdir(parents=True, exist_ok=True)

    rows = []
    for scenario in SCENARIOS:
        for size_mib in scenario["flow_sizes_mib"]:
            rows.extend(run_case(scenario, size_mib))

    normalized = normalize_rows(rows)
    write_csv(OUT / "summary.csv", rows)
    write_csv(OUT / "normalized.csv", normalized)

    packet_rows = [row for row in rows if row["scheme"] in PER_PACKET_SCHEME_LABELS]
    packet_normalized = normalize_rows(packet_rows)
    write_csv(OUT / "packet_only_summary.csv", packet_rows)
    write_csv(OUT / "packet_only_normalized.csv", packet_normalized)

    chart_paths = {}
    for scenario in SCENARIOS:
        chart_paths[scenario["key"]] = plot_scenario(scenario, normalized)
        print(chart_paths[scenario["key"]][0], flush=True)
        packet_chart = plot_scenario(
            scenario,
            packet_normalized,
            schemes=PER_PACKET_SCHEMES,
            suffix="packet_lb",
            title_scope="逐包方案",
            horizontal_value_labels=True,
        )
        print(packet_chart[0], flush=True)
        packet_no_cct_chart = plot_scenario(
            scenario,
            packet_normalized,
            schemes=PER_PACKET_SCHEMES,
            metrics=NO_CCT_METRICS,
            suffix="packet_lb_no_cct",
            title_scope="逐包方案（不含CCT）",
            horizontal_value_labels=True,
            sparse_value_labels=True,
        )
        print(packet_no_cct_chart[0], flush=True)
    write_report(rows, chart_paths)

    print(OUT / "summary.csv", flush=True)
    print(OUT / "normalized.csv", flush=True)
    print(OUT / "packet_only_summary.csv", flush=True)
    print(OUT / "packet_only_normalized.csv", flush=True)
    print(OUT / "README_all_lb_report.md", flush=True)


if __name__ == "__main__":
    main()
