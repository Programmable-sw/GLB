#!/usr/bin/env python3
"""Compare hard Top-4 with 4/2/1/0 weighted selection on WebSearch 80%."""

import argparse
import csv
import gzip
import json
import re
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager


FLOW_RE = re.compile(
    r"^Flow Roce_\d+_\d+ (\d+) finished at ([0-9.]+) total bytes (\d+)$",
    re.MULTILINE,
)


def traffic_rows(path):
    result = {}
    pattern = re.compile(r"\d+->\d+ id (\d+) start (\d+) size (\d+)")
    for line in path.read_text().splitlines():
        match = pattern.match(line)
        if match:
            result[int(match.group(1))] = {
                "start_us": int(match.group(2)) / 1e6,
                "size_bytes": int(match.group(3)),
            }
    return result


def parse_kv_line(text, prefix):
    matches = re.findall(r"^" + re.escape(prefix) + r" (.*)$", text, re.MULTILINE)
    if not matches:
        raise ValueError(f"Missing {prefix}")
    values = {}
    for key, value in re.findall(r"([A-Za-z0-9_]+)=([^ ]+)", matches[-1]):
        try:
            values[key] = float(value)
        except ValueError:
            values[key] = value
    return values


def parse_performance(path, id_base, traffic):
    text = path.read_text(errors="replace")
    fcts = []
    sizes = []
    finish_times = []
    for match in FLOW_RE.finditer(text):
        internal_id = int(match.group(1))
        connection_id = (internal_id - id_base) // 4
        if internal_id != id_base + 4 * connection_id or connection_id not in traffic:
            continue
        finish_us = float(match.group(2))
        fcts.append(finish_us - traffic[connection_id]["start_us"])
        sizes.append(traffic[connection_id]["size_bytes"])
        finish_times.append(finish_us)
    if len(fcts) != len(traffic):
        raise ValueError(f"Expected {len(traffic)} flows, parsed {len(fcts)} from {path}")
    new_rtx = re.findall(r"^New: (\d+) Rtx: (\d+)$", text, re.MULTILINE)
    if not new_rtx:
        raise ValueError(f"Missing New/Rtx summary in {path}")
    new_packets, retransmissions = map(int, new_rtx[-1])
    queue = parse_kv_line(text, "QueueDiag")
    roce = parse_kv_line(text, "RoceDiag")
    queue_cv = parse_kv_line(text, "QueueCvDiag")
    array = np.array(fcts)
    return {
        "completed_flows": len(fcts),
        "mean_fct_us": float(array.mean()),
        "p50_fct_us": float(np.percentile(array, 50)),
        "p95_fct_us": float(np.percentile(array, 95)),
        "p99_fct_us": float(np.percentile(array, 99)),
        "p999_fct_us": float(np.percentile(array, 99.9)),
        "max_fct_us": float(array.max()),
        "new_packets": new_packets,
        "retransmissions": retransmissions,
        "retransmission_ratio": retransmissions / new_packets,
        "ecn_marks": int(queue["composite_ecn_marks"]),
        "trims": int(queue["composite_trims"]),
        "rtos": int(roce["rtos"]),
        "ecn_echo_acks": int(roce["ecn_echo_acks"]),
        "spine_queue_cv": float(queue_cv["spine_queue_cv"]),
        "spine_queue_avg_bytes": float(queue_cv["spine_queue_avg"]),
    }


def open_trace(path):
    return gzip.open(path, "rt", newline="") if str(path).endswith(".gz") else path.open(newline="")


def queue_fraction(row):
    local = float(row["q_leaf_to_spine"]) / max(1, float(row["q_leaf_to_spine_max"]))
    remote = float(row["q_spine_to_dst_leaf"]) / max(1, float(row["q_spine_to_dst_leaf_max"]))
    return max(local, remote)


def parse_micro_trace(path, src_leaf=7, dst_leaf=12, start_us=1000, end_us=2500):
    rows = defaultdict(dict)
    with open_trace(path) as stream:
        for row in csv.DictReader(stream):
            time_us = float(row["time_us"])
            if not start_us <= time_us <= end_us:
                continue
            if int(row["src_leaf"]) == src_leaf and int(row["dst_leaf"]) == dst_leaf:
                rows[time_us][int(row["path_id"])] = row
    times = sorted(time_us for time_us, paths in rows.items() if len(paths) == 8)
    queue_cvs = []
    best_sets = []
    for time_us in times:
        queue = np.array([queue_fraction(rows[time_us][ev]) for ev in range(8)])
        queue_cvs.append(float(queue.std() / (queue.mean() + 1e-12)))
        best_sets.append(set(sorted(
            range(8), key=lambda ev: (float(rows[time_us][ev]["path_score"]), ev)
        )[:4]))
    traffic_shares = []
    evictions = []
    queue_delta_gaps = []
    cycle_times = []
    for index, (left, right) in enumerate(zip(times, times[1:])):
        if right - left != 5:
            continue
        byte_delta = np.array([
            max(0, int(rows[right][ev]["bytes_sent_on_path"])
                - int(rows[left][ev]["bytes_sent_on_path"]))
            for ev in range(8)
        ], dtype=float)
        if byte_delta.sum() == 0:
            continue
        traffic_shares.append(byte_delta / byte_delta.sum())
        evictions.append(len(best_sets[index] - best_sets[index + 1]) / 4)
        queue_start = np.array([queue_fraction(rows[left][ev]) for ev in range(8)])
        queue_end = np.array([queue_fraction(rows[right][ev]) for ev in range(8)])
        best = best_sets[index]
        recommended_delta = np.mean([queue_end[ev] - queue_start[ev] for ev in best])
        other_delta = np.mean([
            queue_end[ev] - queue_start[ev] for ev in range(8) if ev not in best
        ])
        queue_delta_gaps.append(recommended_delta - other_delta)
        cycle_times.append(left)
    variation = [
        0.5 * np.abs(traffic_shares[index] - traffic_shares[index - 1]).sum()
        for index in range(1, len(traffic_shares))
    ]
    return {
        "times_us": times,
        "queue_cv_series": queue_cvs,
        "cycle_times_us": cycle_times[1:],
        "traffic_variation_series": variation,
        "mean_queue_cv": float(np.mean(queue_cvs)),
        "p95_queue_cv": float(np.percentile(queue_cvs, 95)),
        "mean_best4_eviction_fraction": float(np.mean(evictions)),
        "mean_best4_queue_delta_gap": float(np.mean(queue_delta_gaps)),
        "mean_traffic_variation": float(np.mean(variation)),
        "mean_top4_traffic_concentration": float(np.mean([
            sum(sorted(share, reverse=True)[:4]) for share in traffic_shares
        ])),
    }


def smooth(values, width=5):
    values = np.asarray(values, dtype=float)
    if len(values) < width:
        return values
    return np.convolve(values, np.ones(width) / width, mode="same")


def configure_fonts():
    path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    font_manager.fontManager.addfont(path)
    family = font_manager.FontProperties(fname=path).get_name()
    plt.rcParams.update({"font.family": family, "axes.unicode_minus": False, "font.size": 10})


def plot(results, output_png, output_pdf):
    configure_fonts()
    topk = results["hard_top4"]
    weighted = results["weighted_4210"]
    colors = {"hard_top4": "#d97706", "weighted_4210": "#2563a6"}
    labels = {"hard_top4": "硬 Top-4", "weighted_4210": "4/2/1/0 加权"}
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9.2), constrained_layout=True)

    ax = axes[0, 0]
    for key in ("hard_top4", "weighted_4210"):
        micro = results[key]["micro"]
        ax.plot(micro["times_us"], smooth(micro["queue_cv_series"]),
                color=colors[key], linewidth=1.8, label=labels[key])
    ax.set_title("同一 ToR 对的路径队列不均衡时间线")
    ax.set_xlabel("仿真时间（μs）")
    ax.set_ylabel("8 路队列 CV（25 μs 平滑）")
    ax.grid(linestyle=":", alpha=0.4)
    ax.legend(frameon=False)

    ax = axes[0, 1]
    metric_specs = [
        ("mean_traffic_variation", "相邻周期\n流量迁移"),
        ("mean_best4_eviction_fraction", "Best-4\n身份替换"),
        ("p95_queue_cv", "队列 CV\np95"),
        ("mean_top4_traffic_concentration", "最热四路\n流量集中度"),
    ]
    x = np.arange(len(metric_specs))
    top_values = [topk["micro"][key] / weighted["micro"][key] for key, _ in metric_specs]
    ax.bar(x, top_values, color=colors["hard_top4"], width=0.58)
    ax.axhline(1, color=colors["weighted_4210"], linestyle="--", linewidth=1.4,
               label="加权方式 = 1.0")
    for index, value in enumerate(top_values):
        ax.text(index, value + 0.025, f"{value:.2f}×", ha="center", fontsize=10)
    ax.set_xticks(x, [label for _, label in metric_specs])
    ax.set_ylim(0, max(top_values) * 1.2)
    ax.set_title("Hard Top-4 放大的振荡指标")
    ax.set_ylabel("相对加权方式")
    ax.legend(frameon=False)
    ax.grid(axis="y", linestyle=":", alpha=0.4)

    ax = axes[1, 0]
    fct_metrics = [("mean_fct_us", "Mean"), ("p50_fct_us", "p50"),
                   ("p99_fct_us", "p99"), ("max_fct_us", "Max")]
    x = np.arange(len(fct_metrics)); width = 0.34
    for offset, key in [(-width / 2, "hard_top4"), (width / 2, "weighted_4210")]:
        values = [results[key]["performance"][metric] for metric, _ in fct_metrics]
        ax.bar(x + offset, values, width, color=colors[key], label=labels[key])
        for xpos, value in zip(x + offset, values):
            ax.text(xpos, value * 1.025, f"{value:.0f}", ha="center", fontsize=8)
    ax.set_xticks(x, [label for _, label in fct_metrics])
    ax.set_yscale("log")
    ax.set_ylabel("FCT（μs，对数轴）")
    ax.set_title("相同 29,191 条流的完成时间")
    ax.legend(frameon=False)
    ax.grid(axis="y", which="both", linestyle=":", alpha=0.35)

    ax = axes[1, 1]
    cost_specs = [
        ("ecn_marks", "ECN 标记"),
        ("rtos", "RTO"),
        ("spine_queue_cv", "全网队列 CV"),
        ("retransmission_ratio", "重传比例"),
    ]
    ratios = [topk["performance"][key] / weighted["performance"][key]
              for key, _ in cost_specs]
    ax.bar(np.arange(len(cost_specs)), ratios, color=colors["hard_top4"], width=0.58)
    ax.axhline(1, color=colors["weighted_4210"], linestyle="--", linewidth=1.4)
    for index, value in enumerate(ratios):
        ax.text(index, value + 0.012, f"{value:.3f}×", ha="center", fontsize=9)
    ax.set_xticks(np.arange(len(cost_specs)), [label for _, label in cost_specs])
    ax.set_ylim(0.8, max(ratios) * 1.13)
    ax.set_ylabel("Hard Top-4 / 加权")
    ax.set_title("拥塞与传输代价（高于 1 表示 Top-4 更差）")
    ax.grid(axis="y", linestyle=":", alpha=0.4)

    fig.suptitle("WebSearch 80%：Hard Top-4 振荡与传输效率的配对证据",
                 fontsize=17, y=1.055)
    fig.text(0.5, 1.018,
             "健康 128 节点，seed 13；相同代码、评分、反馈与流量，仅 selector 不同",
             ha="center", color="#475569")
    fig.savefig(output_png, dpi=220, bbox_inches="tight")
    fig.savefig(output_pdf, bbox_inches="tight")


def serializable(result):
    return {key: value for key, value in result.items()
            if key not in {"times_us", "queue_cv_series", "cycle_times_us",
                           "traffic_variation_series"}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--traffic", type=Path, required=True)
    parser.add_argument("--topk-log", type=Path, required=True)
    parser.add_argument("--weighted-log", type=Path, required=True)
    parser.add_argument("--topk-trace", type=Path, required=True)
    parser.add_argument("--weighted-trace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    traffic = traffic_rows(args.traffic)
    results = {
        "hard_top4": {
            "performance": parse_performance(args.topk_log, 1074, traffic),
            "micro": parse_micro_trace(args.topk_trace),
        },
        "weighted_4210": {
            "performance": parse_performance(args.weighted_log, 1075, traffic),
            "micro": parse_micro_trace(args.weighted_trace),
        },
    }
    stem = "websearch80_topk4_vs_weighted_efficiency_cn"
    output_png = args.output_dir / f"{stem}.png"
    output_pdf = args.output_dir / f"{stem}.pdf"
    plot(results, output_png, output_pdf)
    summary = {
        "scenario": "healthy_websearch_80pct",
        "seed": 13,
        "paired_difference": "selector only: exact top-4 vs shuffled-bucket 4/2/1/0",
        "micro_window_us": [1000, 2500],
        "src_leaf": 7,
        "dst_leaf": 12,
        "hard_top4": {
            "performance": results["hard_top4"]["performance"],
            "micro": serializable(results["hard_top4"]["micro"]),
        },
        "weighted_4210": {
            "performance": results["weighted_4210"]["performance"],
            "micro": serializable(results["weighted_4210"]["micro"]),
        },
        "figure_png": str(output_png),
        "figure_pdf": str(output_pdf),
    }
    summary["topk_over_weighted"] = {
        "mean_fct": results["hard_top4"]["performance"]["mean_fct_us"] / results["weighted_4210"]["performance"]["mean_fct_us"],
        "p50_fct": results["hard_top4"]["performance"]["p50_fct_us"] / results["weighted_4210"]["performance"]["p50_fct_us"],
        "p99_fct": results["hard_top4"]["performance"]["p99_fct_us"] / results["weighted_4210"]["performance"]["p99_fct_us"],
        "max_fct": results["hard_top4"]["performance"]["max_fct_us"] / results["weighted_4210"]["performance"]["max_fct_us"],
        "traffic_variation": results["hard_top4"]["micro"]["mean_traffic_variation"] / results["weighted_4210"]["micro"]["mean_traffic_variation"],
        "best4_eviction": results["hard_top4"]["micro"]["mean_best4_eviction_fraction"] / results["weighted_4210"]["micro"]["mean_best4_eviction_fraction"],
        "p95_queue_cv": results["hard_top4"]["micro"]["p95_queue_cv"] / results["weighted_4210"]["micro"]["p95_queue_cv"],
        "ecn_marks": results["hard_top4"]["performance"]["ecn_marks"] / results["weighted_4210"]["performance"]["ecn_marks"],
        "rtos": results["hard_top4"]["performance"]["rtos"] / results["weighted_4210"]["performance"]["rtos"],
    }
    (args.output_dir / f"{stem}_metrics.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )
    with (args.output_dir / f"{stem}_summary.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["group", "metric", "hard_top4", "weighted_4210", "topk_over_weighted"])
        for group in ("performance", "micro"):
            common = sorted(set(summary["hard_top4"][group]) & set(summary["weighted_4210"][group]))
            for metric in common:
                hard = summary["hard_top4"][group][metric]
                weighted = summary["weighted_4210"][group][metric]
                ratio = hard / weighted if isinstance(hard, (int, float)) and weighted else ""
                writer.writerow([group, metric, hard, weighted, ratio])
    print(json.dumps(summary["topk_over_weighted"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
