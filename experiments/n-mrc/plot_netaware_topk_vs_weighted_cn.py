#!/usr/bin/env python3
"""直接对比 hard top-k 与加权 NetAware 的路径流量/队列时间线。"""

import argparse
import csv
import gzip
import json
import re
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager, patches
import numpy as np


ROW = re.compile(r"^(\d+)->(\d+) id (\d+) ")


def qp_pairs(path):
    result = {}
    with path.open() as handle:
        for line in handle:
            match = ROW.match(line)
            if match:
                result[int(match.group(3))] = (int(match.group(1)) // 8,
                                                   int(match.group(2)) // 8)
    return result


def decision_shares(path, pairs, src, dst, start_us, end_us, bin_us):
    bins = int((end_us - start_us) / bin_us)
    counts = np.zeros((bins, 8), dtype=np.int64)
    with gzip.open(path, "rt", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                time_us = float(row["time_us"])
                qp, ev = int(row["qp_id"]), int(row["selected_ev"])
            except (TypeError, ValueError, KeyError):
                continue
            if time_us < start_us or time_us >= end_us:
                continue
            if pairs.get(qp) != (src, dst) or not 0 <= ev < 8:
                continue
            counts[int((time_us - start_us) // bin_us), ev] += 1
    totals = counts.sum(axis=1)
    return np.divide(counts, totals[:, None], out=np.zeros_like(counts, dtype=float),
                     where=totals[:, None] > 0), totals


def queue_matrix(path, src, dst, start_us, end_us, bin_us):
    bins = int((end_us - start_us) / bin_us)
    result = np.full((bins, 8), np.nan)
    with gzip.open(path, "rt", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                time_us = float(row["time_us"])
                row_src, row_dst, ev = int(row["src_leaf"]), int(row["dst_leaf"]), int(row["path_id"])
                q1, q2 = float(row["q_leaf_to_spine"]), float(row["q_spine_to_dst_leaf"])
                qm1, qm2 = float(row["q_leaf_to_spine_max"]), float(row["q_spine_to_dst_leaf_max"])
            except (TypeError, ValueError, KeyError):
                continue
            if row_src != src or row_dst != dst or time_us < start_us or time_us >= end_us:
                continue
            index = int((time_us - start_us) // bin_us)
            pressure = max(q1 / qm1 if qm1 else 0, q2 / qm2 if qm2 else 0)
            result[index, ev] = pressure if np.isnan(result[index, ev]) else max(result[index, ev], pressure)
    for ev in range(8):
        valid = np.flatnonzero(~np.isnan(result[:, ev]))
        if not len(valid):
            result[:, ev] = 0
            continue
        result[:valid[0] + 1, ev] = result[valid[0], ev]
        for left, right in zip(valid, valid[1:]):
            result[left:right, ev] = result[left, ev]
        result[valid[-1]:, ev] = result[valid[-1], ev]
    return result


def metrics(shares, totals):
    active = totals > 0
    values = shares[active]
    top4 = np.sort(values, axis=1)[:, -4:].sum(axis=1)
    active_evs = (values > 0).sum(axis=1)
    variation = .5 * np.abs(np.diff(values, axis=0)).sum(axis=1)
    dominant = values.argmax(axis=1)
    return {
        "mean_top4_concentration": float(top4.mean()),
        "median_active_evs": float(np.median(active_evs)),
        "mean_adjacent_cycle_variation": float(variation.mean()),
        "dominant_ev_switches": int(np.count_nonzero(dominant[1:] != dominant[:-1])),
        "active_cycles": int(active.sum()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("topk_decisions", type=Path)
    parser.add_argument("topk_paths", type=Path)
    parser.add_argument("weighted_decisions", type=Path)
    parser.add_argument("weighted_paths", type=Path)
    parser.add_argument("traffic_matrix", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--src", type=int, default=7)
    parser.add_argument("--dst", type=int, default=12)
    parser.add_argument("--start-us", type=float, default=1305)
    parser.add_argument("--end-us", type=float, default=1385)
    parser.add_argument("--bin-us", type=float, default=5)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    pairs = qp_pairs(args.traffic_matrix)

    topk_share, topk_total = decision_shares(args.topk_decisions, pairs, args.src, args.dst,
                                              args.start_us, args.end_us, args.bin_us)
    weighted_share, weighted_total = decision_shares(args.weighted_decisions, pairs, args.src, args.dst,
                                                      args.start_us, args.end_us, args.bin_us)
    topk_queue = queue_matrix(args.topk_paths, args.src, args.dst, args.start_us,
                              args.end_us, args.bin_us)
    weighted_queue = queue_matrix(args.weighted_paths, args.src, args.dst, args.start_us,
                                  args.end_us, args.bin_us)
    topk_metrics, weighted_metrics = metrics(topk_share, topk_total), metrics(weighted_share, weighted_total)

    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    font_manager.fontManager.addfont(font_path)
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=font_path).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(2, 2, figsize=(16, 8.3), sharex=True, sharey=True)
    extent = [args.start_us, args.end_us, -.5, 7.5]
    panels = [
        (axes[0, 0], topk_share, "硬 Top-4：每周期 EV 流量占比", "YlOrBr", 1.0),
        (axes[0, 1], weighted_share, "原加权方式：每周期 EV 流量占比", "YlOrBr", 1.0),
        (axes[1, 0], topk_queue, "硬 Top-4：路径队列占用", "OrRd", 1.0),
        (axes[1, 1], weighted_queue, "原加权方式：路径队列占用", "OrRd", 1.0),
    ]
    for axis, values, title, cmap, vmax in panels:
        image = axis.imshow(values.T, origin="lower", aspect="auto", extent=extent,
                            vmin=0, vmax=vmax, cmap=cmap, interpolation="nearest")
        axis.set_title(title, fontsize=12)
        axis.set_yticks(range(8))
        axis.set_ylabel("路径 EV")
        for boundary in np.arange(args.start_us, args.end_us + args.bin_us, args.bin_us):
            axis.axvline(boundary, color="#718096", lw=.45, ls=":", alpha=.7)
        axis.add_patch(patches.Rectangle((1315, -.5), 20, 8, fill=False,
                                         edgecolor="#1f4e79", linewidth=2, linestyle="--"))
        fig.colorbar(image, ax=axis, pad=.01, label="占比（0–1）")
    axes[1, 0].set_xlabel("仿真时间（微秒）")
    axes[1, 1].set_xlabel("仿真时间（微秒）")
    fig.suptitle("WebSearch 100%：硬 Top-4 与加权选路的微观路径震荡对比",
                 fontsize=16, y=.985)
    fig.text(.5, .95,
             f"seed 13，Source ToR {args.src} → Destination ToR {args.dst}；每列代表一个 {args.bin_us:g} μs反馈周期；蓝色虚框为重点解释区间 1315–1335 μs",
             ha="center", fontsize=10)
    fig.text(.25, .915,
             f"Top-4：前4路集中度 {topk_metrics['mean_top4_concentration']:.0%}｜相邻周期变化量 {topk_metrics['mean_adjacent_cycle_variation']:.0%}｜主导EV切换 {topk_metrics['dominant_ev_switches']}次",
             ha="center", fontsize=9, color="#7b341e")
    fig.text(.75, .915,
             f"加权：前4路集中度 {weighted_metrics['mean_top4_concentration']:.0%}｜相邻周期变化量 {weighted_metrics['mean_adjacent_cycle_variation']:.0%}｜主导EV切换 {weighted_metrics['dominant_ev_switches']}次",
             ha="center", fontsize=9, color="#1f4e79")
    fig.tight_layout(rect=[0, 0, 1, .89])
    png = args.output_dir / "websearch100_topk_vs_weighted_oscillation_cn.png"
    pdf = args.output_dir / "websearch100_topk_vs_weighted_oscillation_cn.pdf"
    fig.savefig(png, dpi=220, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    report = {
        "scenario": "healthy_websearch_100pct", "seed": 13,
        "src_leaf": args.src, "dst_leaf": args.dst,
        "start_us": args.start_us, "end_us": args.end_us, "bin_us": args.bin_us,
        "focus_start_us": 1315, "focus_end_us": 1335,
        "topk": topk_metrics, "weighted": weighted_metrics,
        "topk_share_by_cycle": topk_share.tolist(),
        "weighted_share_by_cycle": weighted_share.tolist(),
        "topk_queue_by_cycle": topk_queue.tolist(),
        "weighted_queue_by_cycle": weighted_queue.tolist(),
        "figure_png": str(png), "figure_pdf": str(pdf),
    }
    metrics_path = args.output_dir / "websearch100_topk_vs_weighted_oscillation_metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"topk": topk_metrics, "weighted": weighted_metrics,
                      "png": str(png)}, indent=2))


if __name__ == "__main__":
    main()
