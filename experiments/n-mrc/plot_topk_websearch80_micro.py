#!/usr/bin/env python3
"""Plot a cycle-level hard Top-K diagnostic for WebSearch 80%."""

import argparse
import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap
from matplotlib import font_manager


def read_window(path, src_leaf, dst_leaf, start_us, end_us):
    rows = defaultdict(dict)
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", newline="") as stream:
        for row in csv.DictReader(stream):
            time_us = float(row["time_us"])
            if time_us < start_us or time_us > end_us:
                continue
            if int(row["src_leaf"]) != src_leaf or int(row["dst_leaf"]) != dst_leaf:
                continue
            rows[time_us][int(row["path_id"])] = row
    return rows


def queue_fraction(row):
    local = float(row["q_leaf_to_spine"]) / max(1.0, float(row["q_leaf_to_spine_max"]))
    remote = float(row["q_spine_to_dst_leaf"]) / max(1.0, float(row["q_spine_to_dst_leaf_max"]))
    return max(local, remote)


def build_cycles(rows, start_us, end_us, path_count=8):
    times = sorted(t for t, paths in rows.items() if len(paths) == path_count)
    cycles = []
    for left, right in zip(times, times[1:]):
        if left < start_us or right > end_us or right - left != 5:
            continue
        recommended = set(sorted(
            range(path_count),
            key=lambda ev: (float(rows[left][ev]["path_score"]), ev),
        )[: path_count // 2])
        byte_delta = np.array([
            max(0, int(rows[right][ev]["bytes_sent_on_path"])
                - int(rows[left][ev]["bytes_sent_on_path"]))
            for ev in range(path_count)
        ], dtype=float)
        total = byte_delta.sum()
        share = byte_delta / total if total else np.zeros(path_count)
        q_start = np.array([queue_fraction(rows[left][ev]) for ev in range(path_count)])
        q_end = np.array([queue_fraction(rows[right][ev]) for ev in range(path_count)])
        cycles.append({
            "left": left,
            "right": right,
            "recommended": recommended,
            "share": share,
            "q_start": q_start,
            "q_end": q_end,
        })
    return cycles


def configure_fonts():
    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    font_manager.fontManager.addfont(font_path)
    family = font_manager.FontProperties(fname=font_path).get_name()
    plt.rcParams.update({
        "font.family": family,
        "font.sans-serif": [family, "DejaVu Sans"],
        "axes.unicode_minus": False,
        "font.size": 10,
    })


def plot(cycles, output_png, output_pdf, src_leaf, dst_leaf):
    configure_fonts()
    labels = [f"{c['left']:.0f}–{c['right']:.0f}" for c in cycles]
    x = np.arange(len(cycles))
    recommended = np.array([
        [1 if ev in c["recommended"] else 0 for c in cycles]
        for ev in range(8)
    ])
    q_end = np.array([[c["q_end"][ev] for c in cycles] for ev in range(8)])

    rec_traffic = np.array([sum(c["share"][ev] for ev in c["recommended"]) for c in cycles])
    rec_q_delta = np.array([
        np.mean([c["q_end"][ev] - c["q_start"][ev] for ev in c["recommended"]])
        for c in cycles
    ])
    non_q_delta = np.array([
        np.mean([c["q_end"][ev] - c["q_start"][ev]
                 for ev in range(8) if ev not in c["recommended"]])
        for c in cycles
    ])

    fig = plt.figure(figsize=(13.4, 9.2), constrained_layout=True)
    grid = fig.add_gridspec(4, 1, height_ratios=[0.72, 1.45, 1.0, 0.95])
    ax_rec = fig.add_subplot(grid[0])
    ax_queue = fig.add_subplot(grid[1], sharex=ax_rec)
    ax_delta = fig.add_subplot(grid[2], sharex=ax_rec)
    ax_traffic = fig.add_subplot(grid[3], sharex=ax_rec)

    ax_rec.imshow(recommended, aspect="auto", interpolation="nearest",
                  cmap=ListedColormap(["#f1f5f9", "#2a9d8f"]), vmin=0, vmax=1)
    ax_rec.set_yticks(range(8), [f"EV{i}" for i in range(8)])
    ax_rec.set_title("周期初硬 Top-4 推荐集合（绿色=本周期推荐）", loc="left", pad=8)
    ax_rec.tick_params(axis="x", labelbottom=False)

    image = ax_queue.imshow(q_end, aspect="auto", interpolation="nearest",
                            cmap="OrRd", vmin=0, vmax=1)
    ax_queue.set_yticks(range(8), [f"EV{i}" for i in range(8)])
    ax_queue.set_title("周期末路径队列占用（越红越拥塞）", loc="left", pad=8)
    ax_queue.tick_params(axis="x", labelbottom=False)
    colorbar = fig.colorbar(image, ax=ax_queue, pad=0.012, fraction=0.025)
    colorbar.set_label("队列 / 容量")
    for row in range(8):
        for col in range(len(cycles)):
            ax_queue.text(col, row, f"{q_end[row, col]:.2f}", ha="center", va="center",
                          fontsize=8, color="white" if q_end[row, col] > 0.58 else "#1f2937")

    width = 0.34
    ax_delta.bar(x - width / 2, rec_q_delta * 100, width, color="#2a9d8f",
                 label="本周期推荐 Top-4")
    ax_delta.bar(x + width / 2, non_q_delta * 100, width, color="#94a3b8",
                 label="本周期未推荐四路")
    ax_delta.axhline(0, color="#334155", linewidth=0.9)
    ax_delta.set_ylabel("周期内队列变化\n（百分点）")
    ax_delta.set_title("推荐路径是否在一个反馈周期后变得更拥塞？", loc="left", pad=8)
    ax_delta.legend(frameon=False, ncol=2, loc="upper left")
    ax_delta.grid(axis="y", linestyle=":", alpha=0.45)
    ax_delta.tick_params(axis="x", labelbottom=False)

    ax_traffic.plot(x, rec_traffic * 100, color="#d97706", marker="o", linewidth=2,
                    label="当期 Top-4 的物理路径字节占比")
    ax_traffic.axhline(50, color="#475569", linestyle="--", linewidth=1.2,
                      label="均匀喷洒基准：4/8 = 50%")
    ax_traffic.fill_between(x, 48, 52, color="#cbd5e1", alpha=0.35, linewidth=0)
    ax_traffic.set_ylim(40, 60)
    ax_traffic.set_ylabel("流量占比（%）")
    ax_traffic.set_xlabel("仿真时间 / 5 μs 反馈周期")
    ax_traffic.set_xticks(x, labels)
    ax_traffic.set_title("流量是否同步挤向当期 Top-4？", loc="left", pad=8)
    ax_traffic.legend(frameon=False, ncol=2, loc="upper left")
    ax_traffic.grid(axis="y", linestyle=":", alpha=0.45)

    fig.suptitle(
        "WebSearch 80%：硬 Top-4 的逐周期路径身份翻转",
        fontsize=18, y=1.075,
    )
    fig.text(
        0.5, 1.035,
        f"健康 128 节点，seed 13，Source ToR {src_leaf} → Destination ToR {dst_leaf}；"
        "每列为连续 5 μs 周期",
        ha="center", va="top", color="#475569", fontsize=11,
    )
    fig.savefig(output_png, dpi=220, bbox_inches="tight")
    fig.savefig(output_pdf, bbox_inches="tight")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--src-leaf", type=int, default=7)
    parser.add_argument("--dst-leaf", type=int, default=12)
    parser.add_argument("--start-us", type=float, default=1885)
    parser.add_argument("--end-us", type=float, default=1915)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = read_window(args.trace, args.src_leaf, args.dst_leaf,
                       args.start_us, args.end_us)
    cycles = build_cycles(rows, args.start_us, args.end_us)
    if not cycles:
        raise SystemExit("No complete 5 us cycles found")

    stem = "websearch80_topk4_cycle_oscillation_cn"
    output_png = args.output_dir / f"{stem}.png"
    output_pdf = args.output_dir / f"{stem}.pdf"
    plot(cycles, output_png, output_pdf, args.src_leaf, args.dst_leaf)

    rec_traffic = [sum(c["share"][ev] for ev in c["recommended"]) for c in cycles]
    rec_delta = [np.mean([c["q_end"][ev] - c["q_start"][ev]
                          for ev in c["recommended"]]) for c in cycles]
    non_delta = [np.mean([c["q_end"][ev] - c["q_start"][ev]
                          for ev in range(8) if ev not in c["recommended"]]) for c in cycles]
    evictions = []
    for current, following in zip(cycles, cycles[1:]):
        evictions.append(len(current["recommended"] - following["recommended"]) / 4)
    metrics = {
        "scenario": "healthy_websearch_80pct",
        "seed": 13,
        "src_leaf": args.src_leaf,
        "dst_leaf": args.dst_leaf,
        "start_us": args.start_us,
        "end_us": args.end_us,
        "cycle_us": 5,
        "cycles": len(cycles),
        "mean_recommended_traffic_share": float(np.mean(rec_traffic)),
        "mean_recommended_queue_delta": float(np.mean(rec_delta)),
        "mean_nonrecommended_queue_delta": float(np.mean(non_delta)),
        "recommended_minus_nonrecommended_queue_delta": float(np.mean(rec_delta) - np.mean(non_delta)),
        "mean_top4_eviction_fraction": float(np.mean(evictions)) if evictions else 0.0,
        "figure_png": str(output_png),
        "figure_pdf": str(output_pdf),
    }
    with (args.output_dir / f"{stem}_metrics.json").open("w") as stream:
        json.dump(metrics, stream, indent=2, ensure_ascii=False)

    with (args.output_dir / f"{stem}_cycles.csv").open("w", newline="") as stream:
        fields = ["cycle_start_us", "cycle_end_us", "ev", "recommended",
                  "traffic_share", "queue_start", "queue_end", "queue_delta"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for cycle in cycles:
            for ev in range(8):
                writer.writerow({
                    "cycle_start_us": cycle["left"],
                    "cycle_end_us": cycle["right"],
                    "ev": ev,
                    "recommended": int(ev in cycle["recommended"]),
                    "traffic_share": cycle["share"][ev],
                    "queue_start": cycle["q_start"][ev],
                    "queue_end": cycle["q_end"][ev],
                    "queue_delta": cycle["q_end"][ev] - cycle["q_start"][ev],
                })
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
