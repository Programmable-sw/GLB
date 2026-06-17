#!/usr/bin/env python3
# 测试 128 节点 2 层拓扑中 300Gbps、50% 占空比背景流压力下的逐包 LB，前景流为 4/32MiB。
import csv
import concurrent.futures
import math
import os
import re
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
        "PACKET_BG_OUT",
        str(EXP_DIR / "output" / "packet_bg_128n_300g50_lossless_input_ecn_sp"),
    )
).resolve()

NODES = 128
TIERS = 2
HOSTS_PER_TOR = 8
TORS = NODES // HOSTS_PER_TOR
LINKSPEED_MBPS = 400000
BACKGROUND_RATE_MBPS = 300000
BACKGROUND_DUTY = 0.5
BACKGROUND_ON_US = 100.0
BACKGROUND_PERIOD_US = BACKGROUND_ON_US / BACKGROUND_DUTY
BACKGROUND_START_US = 0.0
BACKGROUND_END_US = 2400.0
FOREGROUND_START_US = 250.0
FLOW_SIZE_MIBS = [4, 32]
MTU = 4096
SEED = int(os.environ.get("PACKET_BG_SEED", "13"))
END_US = int(os.environ.get("PACKET_BG_END_US", "5000"))
CC_MODE = "dcqcn_variant"
RX_MODE = "sp"
SACK_BITMAP_BITS = int(os.environ.get("PACKET_BG_SACK_BITMAP_BITS", "64"))
MAX_WORKERS = int(os.environ.get("PACKET_BG_WORKERS", "4"))
FORCE = os.environ.get("FORCE_RERUN") == "1"
CJK_FONT = None

FINISH_RE = re.compile(r"Flow Roce_(\d+)_(\d+) (\d+) finished at ([0-9.]+)")
IDMAP_RE = re.compile(r"^(\d+)\s+Roce_(\d+)_(\d+)$")

SCHEMES = [
    ("ecmp_rr", "ECMP-RR", "ecmp_rr", []),
    ("ops", "OPS", "ops", []),
    ("rr", "RR", "rr", []),
    ("reps", "REPS", "reps", []),
    ("n-mrc", "N-MRC", "n-mrc", []),
    ("adaptive-routing", "AR", "adaptive-routing", ["-ar_granularity", "packet"]),
    ("drill", "DRILL", "drill", []),
    ("glb", "GLB", "glb", []),
]

METRICS = [
    ("avg_fct_us", "平均FCT"),
    ("p99_fct_us", "p99 FCT"),
    ("p999_fct_us", "p99.9 FCT"),
]

COLORS = {
    "ecmp_rr": "#9ca3af",
    "ops": "#2563eb",
    "rr": "#38bdf8",
    "reps": "#059669",
    "n-mrc": "#dc2626",
    "adaptive-routing": "#7c3aed",
    "drill": "#db2777",
    "glb": "#111827",
}


def configure_fonts():
    global CJK_FONT
    for path in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    ]:
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


def bytes_for_rate(rate_mbps, duration_us):
    return int(round(rate_mbps * duration_us / 8.0))


def flow(src, dst, size, start_us, role, rate_mbps=0):
    return {
        "src": src,
        "dst": dst,
        "size": size,
        "start_us": start_us,
        "start_ps": int(round(start_us * 1_000_000)),
        "role": role,
        "rate_mbps": rate_mbps,
    }


def foreground_flows(size_bytes):
    flows = []
    for tor in range(TORS):
        dst_tor = (tor + TORS // 2) % TORS
        for host_offset in range(1, HOSTS_PER_TOR):
            src = tor * HOSTS_PER_TOR + host_offset
            dst = dst_tor * HOSTS_PER_TOR + host_offset
            flows.append(flow(src, dst, size_bytes, FOREGROUND_START_US, "foreground"))
    return flows


def background_flows():
    flows = []
    size = bytes_for_rate(BACKGROUND_RATE_MBPS, BACKGROUND_ON_US)
    starts = []
    t = BACKGROUND_START_US
    while t < BACKGROUND_END_US:
        starts.append(t)
        t += BACKGROUND_PERIOD_US

    for start_us in starts:
        for tor in range(TORS):
            dst_tor = (tor + TORS // 2) % TORS
            src = tor * HOSTS_PER_TOR
            dst = dst_tor * HOSTS_PER_TOR
            flows.append(
                flow(
                    src,
                    dst,
                    size,
                    start_us,
                    "background",
                    rate_mbps=BACKGROUND_RATE_MBPS,
                )
            )
    return flows


def write_tm(path, flows):
    with path.open("w") as fh:
        print("Nodes", NODES, file=fh)
        print("Connections", len(flows), file=fh)
        for flow_id, item in enumerate(flows, 1):
            line = (
                f"{item['src']}->{item['dst']} id {flow_id} "
                f"start {item['start_ps']} size {item['size']}"
            )
            if item["rate_mbps"]:
                line += f" rate_mbps {item['rate_mbps']}"
            print(line, file=fh)


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
        "cct_us": cct,
    }


def command_for(tm, dat_file, scheme, flow_count):
    _label, _display, lb_mode, extra = scheme
    return [
        str(SIM),
        "-o",
        str(dat_file),
        "-tm",
        str(tm),
        "-nodes",
        str(NODES),
        "-conns",
        str(flow_count),
        "-tiers",
        str(TIERS),
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
        str(NODES),
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
    ] + extra


def source_id_map(idmap_file, flows):
    ids = []
    seen = set()
    for line in idmap_file.read_text(errors="ignore").splitlines():
        match = IDMAP_RE.match(line)
        if not match:
            continue
        source_id = int(match.group(1))
        if source_id in seen:
            continue
        seen.add(source_id)
        ids.append(source_id)
    if len(ids) != len(flows):
        raise RuntimeError(f"expected {len(flows)} RoCE source IDs in {idmap_file}, got {len(ids)}")
    return {source_id: item for source_id, item in zip(ids, flows)}


def parse_stdout(stdout_file, idmap_file, flows):
    id_to_flow = source_id_map(idmap_file, flows)
    rows = []
    for line in stdout_file.read_text(errors="ignore").splitlines():
        match = FINISH_RE.search(line)
        if not match:
            continue
        source_id = int(match.group(3))
        meta = id_to_flow.get(source_id)
        if not meta:
            continue
        finish_us = float(match.group(4))
        rows.append(
            {
                "role": meta["role"],
                "src": meta["src"],
                "dst": meta["dst"],
                "start_us": meta["start_us"],
                "finish_us": finish_us,
                "fct_us": finish_us - meta["start_us"],
            }
        )
    return rows


def cached_complete(stdout_file, idmap_file, flows):
    if not stdout_file.exists() or not idmap_file.exists():
        return False
    try:
        return len(parse_stdout(stdout_file, idmap_file, flows)) == len(flows)
    except RuntimeError:
        return False


def run_scheme(size_mib, flows, case_dir, tm, scheme):
    label = scheme[0]
    scheme_dir = case_dir / label
    scheme_dir.mkdir(parents=True, exist_ok=True)
    stdout_file = scheme_dir / f"{label}.stdout"
    dat_file = scheme_dir / f"{label}.dat"
    cmd_file = scheme_dir / f"{label}.cmd"
    idmap_file = scheme_dir / "idmap.txt"
    cmd = command_for(tm, dat_file, scheme, len(flows))
    cmd_text = " ".join(cmd) + "\n"
    old_cmd_text = cmd_file.read_text() if cmd_file.exists() else ""

    if FORCE or old_cmd_text != cmd_text or not cached_complete(stdout_file, idmap_file, flows):
        cmd_file.write_text(cmd_text)
        print(f"run bg300g50 {size_mib}MiB {label}", flush=True)
        with stdout_file.open("w") as fh:
            subprocess.run(cmd, cwd=scheme_dir, stdout=fh, stderr=subprocess.STDOUT, check=True)
    else:
        print(f"skip bg300g50 {size_mib}MiB {label} cached", flush=True)

    parsed = parse_stdout(stdout_file, idmap_file, flows)
    fg = [row for row in parsed if row["role"] == "foreground"]
    bg = [row for row in parsed if row["role"] == "background"]
    fg_total = sum(1 for item in flows if item["role"] == "foreground")
    bg_total = len(flows) - fg_total
    row = {
        "scenario": "small_bg_128n_2tier_300g50",
        "flow_size_mib": size_mib,
        "scheme": label,
        "scheme_display": scheme[1],
        "nodes": NODES,
        "tiers": TIERS,
        "cc": CC_MODE,
        "rx_mode": RX_MODE,
        "sack_bitmap_bits": SACK_BITMAP_BITS,
        "foreground_flows": fg_total,
        "background_flows": bg_total,
        "background_rate_mbps": BACKGROUND_RATE_MBPS,
        "background_duty": BACKGROUND_DUTY,
        "background_on_us": BACKGROUND_ON_US,
        "background_period_us": BACKGROUND_PERIOD_US,
        **{f"fg_{key}": value for key, value in metrics_for(fg, fg_total).items()},
        **{f"bg_{key}": value for key, value in metrics_for(bg, bg_total).items()},
    }
    print(
        "{flow_size_mib:>2}MiB {scheme:16s} fg={fg_completed:3d}/{foreground_flows:<3d} "
        "avg={fg_avg_fct_us:8.3f} p99={fg_p99_fct_us:8.3f} "
        "p99.9={fg_p999_fct_us:8.3f}".format(**row),
        flush=True,
    )
    return row


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def normalize(rows):
    out = []
    for size_mib in FLOW_SIZE_MIBS:
        group = [row for row in rows if row["flow_size_mib"] == size_mib]
        best = {}
        for metric, _label in METRICS:
            key = f"fg_{metric}"
            values = [row[key] for row in group if row[key] > 0]
            best[key] = min(values) if values else 0
        for row in group:
            normed = dict(row)
            for metric, _label in METRICS:
                key = f"fg_{metric}"
                denom = best[key]
                normed[f"norm_{key}"] = row[key] / denom if denom else 0
            out.append(normed)
    return out


def plot(rows):
    configure_fonts()
    fig, axes = plt.subplots(1, len(FLOW_SIZE_MIBS), figsize=(23.5, 5.9), sharey=True)
    scheme_labels = [scheme[0] for scheme in SCHEMES]
    scheme_display = {scheme[0]: scheme[1] for scheme in SCHEMES}
    x_positions = [idx * 1.55 for idx in range(len(METRICS))]
    width = 1.10 / len(scheme_labels)
    max_y = 1.0

    for ax, size_mib in zip(axes, FLOW_SIZE_MIBS):
        group = [row for row in rows if row["flow_size_mib"] == size_mib]
        by_scheme = {row["scheme"]: row for row in group}
        for scheme_idx, scheme in enumerate(scheme_labels):
            row = by_scheme[scheme]
            values = [row[f"norm_fg_{metric}"] for metric, _label in METRICS]
            max_y = max(max_y, *values)
            offset = (scheme_idx - (len(scheme_labels) - 1) / 2) * width
            bars = ax.bar(
                [x + offset for x in x_positions],
                values,
                width,
                label=scheme_display[scheme],
                color=COLORS[scheme],
                edgecolor="white",
                linewidth=0.35,
            )
            for bar, value in zip(bars, values):
                ax.annotate(
                    f"{value:.3f}",
                    xy=(bar.get_x() + bar.get_width() / 2, value),
                    xytext=(0, 4),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=6.0,
                    color="#374151",
                    clip_on=False,
                )
        ax.set_title(f"128节点背景流 | {size_mib}MiB", fontsize=12, pad=12, **cjk_text_kwargs())
        ax.set_xticks(x_positions)
        ax.set_xticklabels([label for _metric, label in METRICS], fontsize=9, **cjk_text_kwargs())
        ax.grid(axis="y", color="#d1d5db", linestyle="-", linewidth=0.7, alpha=0.7)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    axes[0].set_ylabel("foreground FCT slowdown", fontsize=11)
    for ax in axes:
        ax.set_ylim(0, max(max_y * 1.20, max_y + 0.18, 1.3))

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.0),
        ncol=len(scheme_labels),
        frameon=False,
        fontsize=8,
    )
    fig.suptitle(
        (
            "小规模背景流逐包方案 FCT slowdown | "
            f"background 300Gbps, 50% duty, lossless_input_ecn + SP {SACK_BITMAP_BITS}-bit SACK"
        ),
        y=0.93,
        fontsize=13,
        **cjk_text_kwargs(),
    )
    fig.text(
        0.5,
        0.02,
        (
            "128 nodes, 2-tier, foreground starts at 250us, "
            "background 100us on / 100us off, rate_mbps=300000, "
            "400Gbps links, queue=1BDP, ECN/PFC=0.2/0.8 queue, RTO=70us"
        ),
        ha="center",
        fontsize=8.5,
        color="#4b5563",
    )
    fig.tight_layout(rect=[0.02, 0.07, 1.0, 0.88])
    png = OUT / "small_bg_packet_lb_no_cct_slowdown.png"
    pdf = OUT / "small_bg_packet_lb_no_cct_slowdown.pdf"
    fig.savefig(png, dpi=220)
    fig.savefig(pdf)
    plt.close(fig)
    return png, pdf


def main():
    if not SIM.exists():
        raise SystemExit(f"missing simulator binary: {SIM}")
    OUT.mkdir(parents=True, exist_ok=True)

    rows = []
    for size_mib in FLOW_SIZE_MIBS:
        flows = foreground_flows(size_mib * 1024 * 1024) + background_flows()
        case_dir = OUT / f"small_bg_128n_2tier_{size_mib}m"
        case_dir.mkdir(parents=True, exist_ok=True)
        tm = case_dir / f"small_bg_128n_2tier_{size_mib}m.cm"
        write_tm(tm, flows)

        workers = max(1, min(MAX_WORKERS, len(SCHEMES)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(run_scheme, size_mib, flows, case_dir, tm, scheme)
                for scheme in SCHEMES
            ]
            for future in concurrent.futures.as_completed(futures):
                rows.append(future.result())

    rows.sort(key=lambda row: (row["flow_size_mib"], [scheme[0] for scheme in SCHEMES].index(row["scheme"])))
    normalized = normalize(rows)
    write_csv(OUT / "summary.csv", rows)
    write_csv(OUT / "normalized.csv", normalized)
    png, pdf = plot(normalized)
    print(png, flush=True)
    print(pdf, flush=True)
    print(OUT / "summary.csv", flush=True)
    print(OUT / "normalized.csv", flush=True)


if __name__ == "__main__":
    main()
