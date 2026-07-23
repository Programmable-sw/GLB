#!/usr/bin/env python3
"""横版展示 NetAware hard-top-k 每周期的推荐、积流与队列变化。"""

import argparse
import csv
import gzip
import re
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
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


def load_decisions(path, pairs, source_leaf, bin_us):
    counts = defaultdict(lambda: np.zeros(8, dtype=np.int64))
    allocations = defaultdict(Counter)
    with gzip.open(path, "rt", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                time_us = float(row["time_us"])
                qp = int(row["qp_id"])
                ev = int(row["selected_ev"])
                allocation = row["bucket_allocation"]
            except (TypeError, ValueError, KeyError):
                continue
            pair = pairs.get(qp)
            if pair is None or pair[0] != source_leaf or not 0 <= ev < 8:
                continue
            bucket = int(time_us // bin_us)
            key = (pair[1], bucket)
            counts[key][ev] += 1
            if allocation:
                allocations[key][allocation] += 1
    return counts, allocations


def load_queue(path, source_leaf, bin_us):
    queue = defaultdict(float)
    with gzip.open(path, "rt", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                time_us = float(row["time_us"])
                src, dst, ev = int(row["src_leaf"]), int(row["dst_leaf"]), int(row["path_id"])
                q1, q2 = float(row["q_leaf_to_spine"]), float(row["q_spine_to_dst_leaf"])
                qm1, qm2 = float(row["q_leaf_to_spine_max"]), float(row["q_spine_to_dst_leaf_max"])
            except (TypeError, ValueError, KeyError):
                continue
            if src != source_leaf:
                continue
            bucket = int(time_us // bin_us)
            queue[(dst, bucket, ev)] = max(queue[(dst, bucket, ev)],
                                           q1 / qm1 if qm1 else 0,
                                           q2 / qm2 if qm2 else 0)
    return queue


def modal_topk(counter):
    if not counter:
        return np.zeros(8, dtype=bool)
    allocation = counter.most_common(1)[0][0]
    values = allocation.split("/")
    return np.array([index < len(values) and values[index] == "1" for index in range(8)])


def choose_pair(counts, allocations):
    scores = {}
    for dst in sorted({dst for dst, _ in counts}):
        buckets = sorted(bucket for candidate, bucket in counts if candidate == dst)
        if len(buckets) < 50:
            continue
        switches = 0
        previous = None
        concentrations = []
        for bucket in buckets:
            total = counts[(dst, bucket)].sum()
            if total:
                concentrations.append(counts[(dst, bucket)].max() / total)
            current = tuple(modal_topk(allocations[(dst, bucket)]))
            if previous is not None and current != previous:
                switches += 1
            previous = current
        scores[dst] = switches + 10 * np.percentile(concentrations, 95)
    return max(scores, key=scores.get)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("decision_trace", type=Path)
    parser.add_argument("path_trace", type=Path)
    parser.add_argument("traffic_matrix", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--source-leaf", type=int, default=7)
    parser.add_argument("--bin-us", type=float, default=1.0)
    parser.add_argument("--cycle-us", type=int, default=5)
    parser.add_argument("--cycles", type=int, default=12)
    parser.add_argument("--scenario-title", default="非对称 All-to-All 256 MiB，P=16")
    parser.add_argument("--output-stem", default="netaware_topk4_cycle_accumulation_cn")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    pairs = qp_pairs(args.traffic_matrix)
    counts, allocations = load_decisions(args.decision_trace, pairs,
                                         args.source_leaf, args.bin_us)
    queue = load_queue(args.path_trace, args.source_leaf, args.bin_us)
    dst = choose_pair(counts, allocations)
    buckets = sorted(bucket for candidate, bucket in counts if candidate == dst)
    first, last = min(buckets), max(buckets)
    width = args.cycles * args.cycle_us
    best_start, best_score = first, -1
    for start in range(first, last - width + 1, args.cycle_us):
        changes = 0
        concentration = 0.0
        previous = None
        for bucket in range(start, start + width):
            member = tuple(modal_topk(allocations[(dst, bucket)]))
            changes += previous is not None and member != previous
            previous = member
            total = counts[(dst, bucket)].sum()
            concentration += counts[(dst, bucket)].max() / total if total else 0
        covered = np.count_nonzero(
            np.sum([counts[(dst, bucket)] for bucket in range(start, start + width)], axis=0))
        score = covered * 20 + changes * 5 + concentration
        if score > best_score:
            best_start, best_score = start, score

    selected = np.arange(best_start, best_start + width)
    time = selected * args.bin_us
    packet_counts = np.array([counts[(dst, int(bucket))] for bucket in selected])
    topk = np.array([modal_topk(allocations[(dst, int(bucket))]) for bucket in selected])
    q = np.full((len(selected), 8), np.nan)
    for row, bucket in enumerate(selected):
        for ev in range(8):
            q[row, ev] = queue.get((dst, int(bucket), ev), np.nan)
    for ev in range(8):
        valid = np.flatnonzero(~np.isnan(q[:, ev]))
        if len(valid):
            q[:valid[0] + 1, ev] = q[valid[0], ev]
            for left, right in zip(valid, valid[1:]):
                q[left:right, ev] = q[left, ev]
            q[valid[-1]:, ev] = q[valid[-1], ev]
        else:
            q[:, ev] = 0

    cumulative_share = np.zeros_like(packet_counts, dtype=float)
    for cycle_start in range(0, len(selected), args.cycle_us):
        cumulative = np.zeros(8, dtype=float)
        for offset in range(args.cycle_us):
            index = cycle_start + offset
            cumulative += packet_counts[index]
            cumulative_share[index] = cumulative / cumulative.sum() if cumulative.sum() else 0

    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    font_manager.fontManager.addfont(font_path)
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=font_path).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(4, 2, figsize=(16, 9.5), sharex=True, sharey=True)
    axes = axes.ravel()
    for ev, axis in enumerate(axes):
        for index in range(len(time)):
            if topk[index, ev]:
                axis.axvspan(time[index], time[index] + args.bin_us,
                            color="#68a357", alpha=.16, lw=0)
        axis.plot(time, cumulative_share[:, ev] * 100, color="#1f4e79", lw=1.5,
                  marker="o", ms=2.3, label="本周期累计流量占比")
        axis.axhline(25, color="#4a5568", lw=.8, ls="--", label="top-4 均分 25%")
        axis.set_ylim(0, 55)
        axis.set_ylabel("累计流量占比 (%)", color="#1f4e79")
        qaxis = axis.twinx()
        qaxis.step(time, q[:, ev] * 100, where="post", color="#dd6b20", lw=1.15,
                   label="路径队列占用")
        qaxis.set_ylim(0, 105)
        qaxis.set_ylabel("队列占用 (%)", color="#dd6b20")
        axis.set_title(f"EV{ev}")
        for boundary in np.arange(time[0], time[-1] + args.cycle_us,
                                  args.cycle_us):
            axis.axvline(boundary, color="#a0aec0", lw=.65, ls=":")
        if ev == 0:
            lines = axis.lines[:2] + qaxis.lines[:1]
            axis.legend(lines, [line.get_label() for line in lines],
                        loc="upper right", fontsize=8)
    for axis in axes[-2:]:
        axis.set_xlabel("仿真时间（微秒）")
    fig.suptitle("MRC+GLB 硬 Top-4：推荐路径在反馈周期内的积流与队列变化",
                 fontsize=16, y=.985)
    fig.text(.5, .953,
             f"{args.scenario_title}，seed 13；Source ToR {args.source_leaf} → Destination ToR {dst}；每条竖虚线间隔 {args.cycle_us} μs",
             ha="center", fontsize=10)
    fig.text(.5, .928,
             "浅绿色表示该 EV 当时属于推荐 top-4；蓝线每个周期从零重新累计，周期末越高表示该推荐路径在本周期吸收的流量越多",
             ha="center", fontsize=9)
    fig.tight_layout(rect=[0, 0, 1, .90])
    png = args.output_dir / f"{args.output_stem}.png"
    pdf = args.output_dir / f"{args.output_stem}.pdf"
    fig.savefig(png, dpi=220, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    cycle_csv = args.output_dir / f"{args.output_stem}_cycle_details.csv"
    with cycle_csv.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cycle_start_us", "cycle_end_us", "ev", "recommended_majority",
                         "cycle_packet_share", "queue_start_fraction", "queue_end_fraction"])
        for cycle_start in range(0, len(selected), args.cycle_us):
            cycle_stop = min(cycle_start + args.cycle_us, len(selected))
            cycle_packets = packet_counts[cycle_start:cycle_stop].sum(axis=0)
            total = cycle_packets.sum()
            recommended = topk[cycle_start:cycle_stop].mean(axis=0) >= .5
            for ev in range(8):
                writer.writerow([time[cycle_start], time[cycle_stop - 1] + args.bin_us,
                                 ev, int(recommended[ev]),
                                 cycle_packets[ev] / total if total else 0,
                                 q[cycle_start, ev], q[cycle_stop - 1, ev]])
    print(f"selected pair {args.source_leaf}->{dst}, window {time[0]}-{time[-1] + args.bin_us} us")
    print(png)
    print(cycle_csv)


if __name__ == "__main__":
    main()
