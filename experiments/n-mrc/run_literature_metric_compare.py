#!/usr/bin/env python3
"""运行 README 中的逐包负载均衡标准验证场景。

这是当前唯一的 README 场景入口脚本，负责展开健康网络、非对称带宽1
和非对称带宽2等场景，运行逐包方案仿真，并生成 CSV、报告和对比图。
"""

import csv
import concurrent.futures
import math
import os
import random
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


def env_value(name, default=None, aliases=()):
    for key in (name, *aliases):
        value = os.environ.get(key)
        if value is not None:
            return value
    return default


def parse_csv_list(raw):
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_int_list(raw):
    return [int(item) for item in parse_csv_list(raw)]


LINKSPEED_MBPS = int(env_value("SCENARIO_LINKSPEED_MBPS", "400000"))
MTU = int(env_value("SCENARIO_MTU", "4096"))
SEED = int(env_value("SCENARIO_SEED", "13", aliases=("README_ALL_LB_SEED",)))
END_US = int(env_value("SCENARIO_END_US", "10000", aliases=("README_ALL_LB_END_US",)))
CC_MODE = env_value("SCENARIO_CC", "dcqcn_variant")
RX_MODE = env_value("SCENARIO_RX_MODE", "sp")
SACK_BITMAP_BITS = int(
    env_value("SCENARIO_SACK_BITMAP_BITS", "64", aliases=("README_ALL_LB_SACK_BITMAP_BITS",))
)

# The full README matrix is 4/8/16/32MiB, but the default enabled subset is 4/32MiB.
ALL_FLOW_SIZE_MIBS = [4, 8, 16, 32]
FLOW_SIZE_MIBS = parse_int_list(
    env_value("SCENARIO_FLOW_SIZE_MIBS", "4,32", aliases=("README_ALL_LB_FLOW_SIZES",))
)
TRAFFICS = parse_csv_list(
    env_value(
        "SCENARIO_TRAFFICS",
        env_value("SCENARIO_TRAFFIC", "tornado,permutation"),
    )
)
SCENARIO_SET = parse_csv_list(
    env_value(
        "SCENARIO_SET",
        "healthy,asym_tor3pct,asym2_tor10pct_sparse",
        aliases=("SCENARIO_SCENARIOS",),
    )
)
QUEUE_TYPES = parse_csv_list(env_value("SCENARIO_QUEUE_TYPES", "lossless_input_ecn"))
SCHEME_SCOPE = env_value("SCENARIO_SCHEMES", "packet", aliases=("README_ALL_LB_SCHEME_SCOPE",)).strip()
MAX_WORKERS = int(env_value("SCENARIO_WORKERS", "4", aliases=("README_ALL_LB_WORKERS",)))
KEEP_RAW = env_value("KEEP_RAW_OUTPUT", "1") != "0"
FORCE = env_value("FORCE_RERUN", "0") == "1"
CJK_FONT = None

FINISH_RE = re.compile(r"Flow Roce_(\d+)_(\d+) \d+ finished at ([0-9.]+)")
KEY_VALUE_RE = re.compile(r"([A-Za-z0-9_]+)=([0-9.]+)")
QUEUE_DIAG_FIELDS = [
    "lossless_overflows",
    "lossless_ecn_marks",
    "lossy_drops",
    "lossy_ecn_marks",
    "composite_trims",
    "composite_drops",
    "composite_ecn_marks",
]
ROCE_DIAG_FIELDS = [
    "acks",
    "nacks",
    "rtos",
    "ecn_echo_acks",
    "feedback_acks",
    "feedback_zero_bits",
    "nmrc_ev_skips",
    "nmrc_bitmap_fallbacks",
    "stor_selected_good",
    "stor_selected_degraded",
    "stor_selected_bad",
    "stor_selected_avoid",
]
STOR_DIAG_FIELDS = [
    "stor_min_score",
    "stor_avoid_entries",
    "stor_avoid_exits",
    "stor_clean_signals",
    "stor_ecn_signals",
    "stor_trim_signals",
]


def default_output_dir():
    scenario_slug = "-".join(SCENARIO_SET)
    traffic_slug = "-".join(TRAFFICS)
    size_slug = "-".join(str(size) for size in FLOW_SIZE_MIBS)
    queue_suffix = ""
    if QUEUE_TYPES != ["lossless_input_ecn"]:
        queue_suffix = f"_queue-{'-'.join(QUEUE_TYPES)}"
    return EXP_DIR / "output" / (
        f"readme_packet_lb_scenarios-{scenario_slug}_traffic-{traffic_slug}_flow-{size_slug}m"
        f"{queue_suffix}"
    )


OUT = Path(env_value("SCENARIO_OUT", str(default_output_dir()), aliases=("README_ALL_LB_OUT",))).resolve()

SCENARIO_DEFINITIONS = [
    {
        "key": "healthy",
        "title": "健康网络",
        "nodes": 2048,
        "tiers": 2,
        "description": "2048 nodes / 2-tier, all links 400Gbps",
    },
    {
        "key": "asym_tor3pct",
        "title": "非对称带宽",
        "nodes": 1024,
        "tiers": 3,
        "slow_tor_uplink_fraction": 0.03,
        "slow_tor_uplink_rounding": "ceil",
        "slow_tor_uplink_divisor": 2,
        "description": "1024 nodes / 3-tier, 3% ToR uplinks at half bandwidth",
    },
    {
        "key": "asym2_tor10pct_sparse",
        "title": "非对称带宽2",
        "nodes": 2048,
        "tiers": 2,
        "slow_tor_uplink_fraction": 0.10,
        "slow_tor_uplink_rounding": "floor",
        "slow_tor_uplink_divisor": 2,
        "slow_tor_uplink_select": "random-sparse",
        "description": (
            "2048 nodes / 2-tier, floor(10%) sparse random ToR uplinks at half bandwidth"
        ),
    },
]

ALL_SCHEMES = [
    ("ecmp", "ECMP", "ecmp", []),
    ("ecmp_rr", "ECMP-RR", "ecmp_rr", []),
    ("ops", "OPS", "ops", []),
    ("rr", "RR", "rr", []),
    ("reps", "REPS", "reps", []),
    ("avail", "Avail", "avail", []),
    ("grade", "Grade", "grade", []),
    ("netaware", "n-MRC", "netaware", []),
    ("mrc", "MRC", "mrc", []),
    ("conweave", "CONWEAVE", "conweave", []),
    ("adaptive-routing", "AR", "adaptive-routing", ["-ar_granularity", "packet"]),
    ("drill", "DRILL", "drill", []),
    ("sglb", "SGLB", "sglb", []),
]

COMPOSITE_DEFAULT_SCHEMES = {"mrc", "avail", "grade", "netaware"}

PACKET_SCHEME_LABELS = [
    "ecmp_rr",
    "ops",
    "rr",
    "reps",
    "avail",
    "grade",
    "netaware",
    "mrc",
    "adaptive-routing",
    "drill",
    "sglb",
]

METRICS = [
    ("avg_fct_us", "平均FCT"),
    ("p99_fct_us", "p99 FCT"),
    ("p999_fct_us", "p99.9 FCT"),
    ("cct_us", "CCT"),
]
PLOT_METRICS = METRICS[:3]

COLORS = {
    "ecmp": "#6b7280",
    "ecmp_rr": "#9ca3af",
    "ops": "#2563eb",
    "rr": "#38bdf8",
    "reps": "#059669",
    "avail": "#dc2626",
    "grade": "#be123c",
    "netaware": "#7f1d1d",
    "mrc": "#ea580c",
    "conweave": "#f59e0b",
    "adaptive-routing": "#7c3aed",
    "drill": "#db2777",
    "sglb": "#111827",
}


def selected_schemes():
    if SCHEME_SCOPE == "packet":
        wanted = PACKET_SCHEME_LABELS
    elif SCHEME_SCOPE == "all":
        wanted = [scheme[0] for scheme in ALL_SCHEMES]
    else:
        wanted = parse_csv_list(SCHEME_SCOPE)

    by_label = {scheme[0]: scheme for scheme in ALL_SCHEMES}
    missing = [label for label in wanted if label not in by_label]
    if missing:
        raise ValueError(f"unknown SCENARIO_SCHEMES labels: {', '.join(missing)}")
    return [by_label[label] for label in wanted]


SCHEMES = selected_schemes()


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


def default_total_tor_uplinks(nodes, tiers):
    validate_generated_fattree(nodes, tiers)
    return nodes


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


def flows_for(scenario):
    size = scenario["flow_size_mib"] * 1024 * 1024
    if scenario["traffic"] == "tornado":
        return tornado_flows(scenario["nodes"], size)
    if scenario["traffic"] == "permutation":
        return permutation_flows(scenario["nodes"], size, SEED)
    raise ValueError(f"unsupported traffic={scenario['traffic']}")


def scenario_name(definition, traffic, size_mib, queue_type):
    base = f"{definition['key']}_{traffic}_{definition['nodes']}n_{definition['tiers']}tier_{size_mib}m"
    if QUEUE_TYPES == ["lossless_input_ecn"]:
        return base
    return f"{base}_{queue_type}"


def build_scenarios():
    by_key = {scenario["key"]: scenario for scenario in SCENARIO_DEFINITIONS}
    missing = [key for key in SCENARIO_SET if key not in by_key]
    if missing:
        raise ValueError(f"unknown SCENARIO_SET labels: {', '.join(missing)}")

    scenarios = []
    for key in SCENARIO_SET:
        definition = by_key[key]
        validate_generated_fattree(definition["nodes"], definition["tiers"])
        for queue_type in QUEUE_TYPES:
            for traffic in TRAFFICS:
                if traffic not in {"tornado", "permutation"}:
                    raise ValueError(f"unsupported traffic={traffic}")
                for size_mib in FLOW_SIZE_MIBS:
                    if size_mib not in ALL_FLOW_SIZE_MIBS:
                        raise ValueError(
                            f"flow size {size_mib}MiB is not in supported set {ALL_FLOW_SIZE_MIBS}"
                        )
                    scenarios.append(
                        {
                            **definition,
                            "traffic": traffic,
                            "flow_size_mib": size_mib,
                            "queue_type": queue_type,
                            "paths": definition.get("paths", definition["nodes"]),
                            "end_us": END_US,
                            "name": scenario_name(definition, traffic, size_mib, queue_type),
                        }
                    )
    return scenarios


SCENARIOS = build_scenarios()


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


def parse_key_value_diag(stdout_file, prefix, fields):
    values = {field: 0 for field in fields}
    if not stdout_file.exists():
        return values
    for line in stdout_file.read_text(errors="ignore").splitlines():
        if not line.startswith(prefix):
            continue
        for key, raw_value in KEY_VALUE_RE.findall(line):
            if key in values:
                values[key] = float(raw_value) if "." in raw_value else int(raw_value)
    return values


def parse_queue_diag(stdout_file):
    return parse_key_value_diag(stdout_file, "QueueDiag ", QUEUE_DIAG_FIELDS)


def parse_roce_diag(stdout_file):
    return parse_key_value_diag(stdout_file, "RoceDiag ", ROCE_DIAG_FIELDS)


def parse_stor_diag(stdout_file):
    return parse_key_value_diag(stdout_file, "StorDiag ", STOR_DIAG_FIELDS)


def metrics_for(rows, total):
    fcts = [row["fct_us"] for row in rows]
    cct = 0.0
    if rows:
        cct = max(row["finish_us"] for row in rows) - min(row["start_us"] for row in rows)
    return {
        "completed": len(rows),
        "completion_pct": 100.0 * len(rows) / total if total else 0.0,
        "avg_fct_us": sum(fcts) / len(fcts) if fcts else 0.0,
        "p50_fct_us": pct(fcts, 0.50),
        "p95_fct_us": pct(fcts, 0.95),
        "p99_fct_us": pct(fcts, 0.99),
        "p999_fct_us": pct(fcts, 0.999),
        "max_fct_us": max(fcts) if fcts else 0.0,
        "cct_us": cct,
    }


def slow_tor_uplinks_for(scenario):
    total = default_total_tor_uplinks(scenario["nodes"], scenario["tiers"])
    fraction = scenario.get("slow_tor_uplink_fraction", 0.0)
    if not fraction:
        return total, 0

    raw = total * fraction
    rounding = scenario.get("slow_tor_uplink_rounding", "ceil")
    if rounding == "floor":
        slow = math.floor(raw)
    elif rounding == "round":
        slow = int(round(raw))
    else:
        slow = math.ceil(raw)
    return total, max(1, min(total, slow))


def scenario_run_name(scenario):
    return scenario["name"]


def queue_type_for_scheme(scenario, scheme):
    label = scheme[0]
    if label in COMPOSITE_DEFAULT_SCHEMES:
        return "composite_ecn_lb"
    return scenario["queue_type"]


def command_for(scenario, scheme, tm, dat_file, flow_count, slow_tor_uplinks):
    _label, _display, lb_mode, extra = scheme
    queue_type = queue_type_for_scheme(scenario, scheme)
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
        queue_type,
        "-host_queue_type",
        "prio",
        "-mtu",
        str(MTU),
        "-end",
        str(scenario["end_us"]),
        "-paths",
        str(scenario["paths"]),
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
    if slow_tor_uplinks:
        cmd += [
            "-slow_tor_uplinks",
            str(slow_tor_uplinks),
            "-slow_tor_uplink_divisor",
            str(scenario.get("slow_tor_uplink_divisor", 2)),
        ]
        if scenario.get("slow_tor_uplink_select"):
            cmd += ["-slow_tor_uplink_select", scenario["slow_tor_uplink_select"]]
    return cmd + extra


def run_scheme(scenario, flows, flow_map, case_dir, tm, scheme, total_tor_uplinks, slow_tor_uplinks):
    label = scheme[0]
    stdout_file = case_dir / f"{label}.stdout"
    dat_file = case_dir / f"{label}.dat"
    cmd_file = case_dir / f"{label}.cmd"
    cmd = command_for(scenario, scheme, tm, dat_file, len(flows), slow_tor_uplinks)
    cmd_text = " ".join(cmd) + "\n"
    cached_cmd_text = cmd_file.read_text() if cmd_file.exists() else ""

    parsed = parse_stdout(stdout_file, flow_map)
    if FORCE or len(parsed) != len(flows) or cached_cmd_text != cmd_text:
        cmd_file.write_text(cmd_text)
        print(
            f"run {scenario['title']} {scenario['traffic']} {scenario['flow_size_mib']}MiB "
            f"{label} ({len(parsed)}/{len(flows)} cached)",
            flush=True,
        )
        with stdout_file.open("w") as fh:
            subprocess.run(cmd, cwd=case_dir, stdout=fh, stderr=subprocess.STDOUT, check=True)
        parsed = parse_stdout(stdout_file, flow_map)
    else:
        print(
            f"skip {scenario['title']} {scenario['traffic']} {scenario['flow_size_mib']}MiB "
            f"{label} cached",
            flush=True,
        )
    queue_diag = parse_queue_diag(stdout_file)
    roce_diag = parse_roce_diag(stdout_file)
    stor_diag = parse_stor_diag(stdout_file)
    queue_type = queue_type_for_scheme(scenario, scheme)

    summary = {
        "case": scenario["name"],
        "scenario": scenario["key"],
        "scenario_title": scenario["title"],
        "description": scenario["description"],
        "nodes": scenario["nodes"],
        "tiers": scenario["tiers"],
        "traffic": scenario["traffic"],
        "flow_size_mib": scenario["flow_size_mib"],
        "scheme": label,
        "scheme_display": scheme[1],
        "cc": CC_MODE,
        "rx_mode": RX_MODE,
        "sack_bitmap_bits": SACK_BITMAP_BITS,
        "queue_type": queue_type,
        "linkspeed_mbps": LINKSPEED_MBPS,
        "mtu": MTU,
        "end_us": scenario["end_us"],
        "total_tor_uplinks": total_tor_uplinks,
        "slow_tor_uplinks": slow_tor_uplinks,
        "slow_tor_uplink_fraction": slow_tor_uplinks / total_tor_uplinks if total_tor_uplinks else 0.0,
        "slow_tor_uplink_divisor": scenario.get("slow_tor_uplink_divisor", 1),
        "slow_tor_uplink_select": scenario.get("slow_tor_uplink_select", "spaced"),
        **roce_diag,
        **queue_diag,
        **stor_diag,
        **metrics_for(parsed, len(flows)),
    }
    print(
        "{scenario_title} {traffic:11s} {flow_size_mib:>2}MiB {scheme:16s} "
        "done={completed:4d}/{total:<4d} avg={avg_fct_us:8.3f} "
        "p99={p99_fct_us:8.3f} p99.9={p999_fct_us:8.3f} cct={cct_us:8.3f}".format(
            total=len(flows), **summary
        ),
        flush=True,
    )
    per_flow = [
        {
            "case": scenario["name"],
            "scenario": scenario["key"],
            "traffic": scenario["traffic"],
            "flow_size_mib": scenario["flow_size_mib"],
            "scheme": label,
            **row,
        }
        for row in parsed
    ]
    return summary, per_flow


def run_case(scenario):
    flows = flows_for(scenario)
    flow_map = {(item["src"], item["dst"]): item for item in flows}
    total_tor_uplinks, slow_tor_uplinks = slow_tor_uplinks_for(scenario)

    case_dir = OUT / "raw" / scenario_run_name(scenario)
    if case_dir.exists() and not KEEP_RAW:
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    tm = case_dir / f"{scenario_run_name(scenario)}.cm"
    write_tm(tm, scenario["nodes"], flows)

    rows = []
    per_flow = []
    workers = max(1, min(MAX_WORKERS, len(SCHEMES)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                run_scheme,
                scenario,
                flows,
                flow_map,
                case_dir,
                tm,
                scheme,
                total_tor_uplinks,
                slow_tor_uplinks,
            )
            for scheme in SCHEMES
        ]
        for future in concurrent.futures.as_completed(futures):
            summary, flows_rows = future.result()
            rows.append(summary)
            per_flow.extend(flows_rows)

    order = [scheme[0] for scheme in SCHEMES]
    rows.sort(key=lambda row: order.index(row["scheme"]))
    per_flow.sort(key=lambda row: (row["scheme"], row["src"], row["dst"]))

    if not KEEP_RAW:
        shutil.rmtree(case_dir)
    return rows, per_flow


def normalize_rows(rows):
    out = []
    grouped = {}
    for row in rows:
        grouped.setdefault(row["case"], []).append(row)

    for _case, group in grouped.items():
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


def chart_label_for(traffic):
    return f"{traffic} 图"


def chart_filename(scenario_key, traffic):
    return f"{scenario_key}_{traffic}_packet_lb_slowdown.png"


def bar_label_offset(_scheme_idx):
    return (0, 4)


def plot_scenario_traffic(
    scenario_key,
    traffic,
    rows,
    schemes=SCHEMES,
    metrics=PLOT_METRICS,
):
    scenario_rows = [
        row for row in rows if row["scenario"] == scenario_key and row["traffic"] == traffic
    ]
    if not scenario_rows:
        return None

    sizes = sorted({row["flow_size_mib"] for row in scenario_rows})
    title = scenario_rows[0]["scenario_title"]
    figsize = (20.0, 6.4)
    fig, axes = plt.subplots(1, len(sizes), figsize=figsize, sharey=True)
    if len(sizes) == 1:
        axes = [axes]

    scheme_labels = [scheme[0] for scheme in schemes]
    scheme_display = {scheme[0]: scheme[1] for scheme in schemes}
    x_step = 1.0
    x_positions = [idx * x_step for idx in range(len(metrics))]
    group_width = 0.78
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
                ax.annotate(
                    f"{value:.3f}",
                    xy=(bar.get_x() + bar.get_width() / 2, value),
                    xytext=bar_label_offset(scheme_idx),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=5.8,
                    color="#374151",
                    clip_on=False,
                )
        ax.set_title(
            f"{title} | {traffic} | {size_mib}MiB",
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
    y_limit = max(max_y * 1.35, max_y + 0.20, 1.25)
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
        f"{title} {traffic} FCT slowdown",
        y=0.93,
        fontsize=13,
        **cjk_text_kwargs(),
    )
    fig.tight_layout(rect=[0.02, 0.05, 1.0, 0.88])

    png = OUT / chart_filename(scenario_key, traffic)
    pdf = png.with_suffix(".pdf")
    fig.savefig(png, dpi=220)
    fig.savefig(pdf)
    plt.close(fig)
    return png, pdf


def write_plan(path):
    lines = [
        "# Scenario Plan",
        "",
        f"RoCE: `rx_mode={RX_MODE}`, `sack_bitmap_bits={SACK_BITMAP_BITS}`, `cc={CC_MODE}`.",
        "Queue defaults: `mrc`/`avail`/`grade`/`n-mrc` use `composite_ecn_lb`; "
        "other schemes use the scenario queue.",
        "",
        "| case | scenario | topology | traffic | flow size | slow ToR uplinks | description |",
        "| --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for scenario in SCENARIOS:
        total, slow = slow_tor_uplinks_for(scenario)
        lines.append(
            "| {case} | {title} | {nodes}n/{tiers}-tier | {traffic} | {size} | "
            "{slow}/{total} | {description} |".format(
                case=scenario["name"],
                title=scenario["title"],
                nodes=scenario["nodes"],
                tiers=scenario["tiers"],
                traffic=scenario["traffic"],
                size=scenario["flow_size_mib"],
                slow=slow,
                total=total,
                description=scenario["description"],
            )
        )
    path.write_text("\n".join(lines) + "\n")


def best_scheme(rows, case, metric):
    candidates = [row for row in rows if row["case"] == case and row[metric] > 0.0]
    if not candidates:
        return ""
    best = min(candidates, key=lambda row: row[metric])
    return f"{best['scheme_display']} ({best[metric]:.3f} us)"


def write_report(rows, chart_paths):
    lines = [
        "# README Packet-Level Scenario Comparison",
        "",
        "Parameters: non-MRC-path baselines use `lossless_input_ecn`; "
        "`mrc`/`avail`/`grade`/`n-mrc` use `composite_ecn_lb`; "
        f"`roce_rx_mode={RX_MODE}`, `sack_bitmap_bits={SACK_BITMAP_BITS}`, "
        "`queue=1BDP`, ECN/PFC `0.2/0.8 * queue`, RTO `70us`, "
        f"CC `{CC_MODE}`.",
        "",
        "## Charts",
        "",
    ]
    for traffic, path in chart_paths:
        lines.append(f"- {chart_label_for(traffic)}: ![]({path.name})")

    lines += [
        "",
        "## Best Scheme By Case",
        "",
        "| case | best avg FCT | best p99 FCT | best p99.9 FCT | best CCT |",
        "| --- | --- | --- | --- | --- |",
    ]
    seen_cases = []
    for row in rows:
        if row["case"] not in seen_cases:
            seen_cases.append(row["case"])
    for case in seen_cases:
        lines.append(
            "| {case} | {avg} | {p99} | {p999} | {cct} |".format(
                case=case,
                avg=best_scheme(rows, case, "avg_fct_us"),
                p99=best_scheme(rows, case, "p99_fct_us"),
                p999=best_scheme(rows, case, "p999_fct_us"),
                cct=best_scheme(rows, case, "cct_us"),
            )
        )

    lines += [
        "",
        "## Completion",
        "",
        "| case | scheme | completed | avg FCT us | p99 FCT us | p99.9 FCT us | CCT us | ECN echo | trims | nacks | STOR min score | STOR avoid in/out |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {case} | {scheme_display} | {completed}/{nodes} | {avg_fct_us:.3f} | "
            "{p99_fct_us:.3f} | {p999_fct_us:.3f} | {cct_us:.3f} | "
            "{ecn_echo_acks} | {composite_trims} | {nacks} | {stor_min_score} | "
            "{stor_avoid_entries}/{stor_avoid_exits} |".format(**row)
        )

    (OUT / "comparison_report.md").write_text("\n".join(lines) + "\n")


def main():
    configure_fonts()
    if not SIM.exists():
        raise SystemExit(f"missing simulator binary: {SIM}")

    if OUT.exists() and not KEEP_RAW:
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    write_plan(OUT / "scenario_plan.md")

    summary_rows = []
    per_flow_rows = []
    for scenario in SCENARIOS:
        rows, flows = run_case(scenario)
        summary_rows.extend(rows)
        per_flow_rows.extend(flows)

    normalized_rows = normalize_rows(summary_rows)
    write_csv(OUT / "summary.csv", summary_rows)
    write_csv(OUT / "normalized.csv", normalized_rows)
    write_csv(OUT / "per_flow.csv", per_flow_rows)

    chart_paths = []
    for scenario_key in SCENARIO_SET:
        for traffic in TRAFFICS:
            chart = plot_scenario_traffic(
                scenario_key,
                traffic,
                normalized_rows,
                schemes=SCHEMES,
            )
            if chart:
                chart_paths.append((traffic, chart[0]))
                print(chart[0], flush=True)

    write_report(summary_rows, chart_paths)

    print(OUT / "summary.csv", flush=True)
    print(OUT / "normalized.csv", flush=True)
    print(OUT / "per_flow.csv", flush=True)
    print(OUT / "comparison_report.md", flush=True)


if __name__ == "__main__":
    main()
