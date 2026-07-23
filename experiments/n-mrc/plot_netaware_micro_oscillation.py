#!/usr/bin/env python3
"""Diagnose sub-RTT NetAware path synchronization at Source-ToR/QP grain."""

import argparse
import csv
import gzip
import json
import re
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np


MATRIX_ROW = re.compile(r"^(\d+)->(\d+) id (\d+) ")


def qp_source_leaf(matrix, hosts_per_leaf=8):
    result = {}
    with matrix.open() as handle:
        for line in handle:
            match = MATRIX_ROW.match(line)
            if match:
                result[int(match.group(3))] = int(match.group(1)) // hosts_per_leaf
    return result


def load_decisions(path, qp_to_leaf, bin_us, selected_leaf):
    rows = {}
    last_choice = {}
    last_version = {}
    packet_total = 0

    current_bucket = None
    packet_counts = defaultdict(lambda: np.zeros(8, dtype=np.int64))
    qp_choices = defaultdict(dict)
    changed_destinations = defaultdict(dict)
    changed_qps = defaultdict(set)
    updated_qps = defaultdict(set)

    def flush(bucket):
        if bucket is None:
            return
        for leaf, packets in packet_counts.items():
            qp_count = np.zeros(8, dtype=np.int64)
            for ev in qp_choices[leaf].values():
                qp_count[ev] += 1
            changed = len(changed_qps[leaf])
            destination_counts = np.zeros(8, dtype=np.int64)
            for ev in changed_destinations[leaf].values():
                destination_counts[ev] += 1
            rows[(bucket, leaf)] = {
                "packets": packets.copy(), "qps": qp_count,
                "active_qps": len(qp_choices[leaf]), "changed_qps": changed,
                "updated_qps": len(updated_qps[leaf]),
                "change_alignment": (float(destination_counts.max()) / changed) if changed else 0.0,
            }

    with gzip.open(path, "rt", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                time_us = float(row["time_us"])
                qp = int(row["qp_id"])
                ev = int(row["selected_ev"])
                version = int(row["profile_version"])
            except (TypeError, ValueError, KeyError):
                continue
            leaf = qp_to_leaf.get(qp)
            if leaf != selected_leaf or not 0 <= ev < 8:
                continue
            bucket = int(time_us // bin_us)
            if current_bucket is None:
                current_bucket = bucket
            elif bucket != current_bucket:
                flush(current_bucket)
                packet_counts.clear()
                qp_choices.clear()
                changed_destinations.clear()
                changed_qps.clear()
                updated_qps.clear()
                current_bucket = bucket
            packet_counts[leaf][ev] += 1
            qp_choices[leaf][qp] = ev
            if qp in last_choice and last_choice[qp] != ev:
                changed_qps[leaf].add(qp)
                changed_destinations[leaf][qp] = ev
            if qp in last_version and last_version[qp] != version:
                updated_qps[leaf].add(qp)
            last_choice[qp] = ev
            last_version[qp] = version
            packet_total += 1
    flush(current_bucket)
    return rows, packet_total


def load_queue(path, bin_us):
    queue = defaultdict(float)
    marks = defaultdict(int)
    previous_marks = {}
    with gzip.open(path, "rt", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                time_us = float(row["time_us"])
                leaf = int(row["src_leaf"])
                dst = int(row["dst_leaf"])
                ev = int(row["path_id"])
                q1, q2 = float(row["q_leaf_to_spine"]), float(row["q_spine_to_dst_leaf"])
                qm1, qm2 = float(row["q_leaf_to_spine_max"]), float(row["q_spine_to_dst_leaf_max"])
                mark = int(row["ecn_marks_on_path"])
            except (ValueError, KeyError):
                continue
            bucket = int(time_us // bin_us)
            key = (bucket, leaf, ev)
            queue[key] = max(queue[key], q1 / qm1 if qm1 else 0.0, q2 / qm2 if qm2 else 0.0)
            counter_key = (leaf, dst, ev)
            old = previous_marks.get(counter_key, mark)
            marks[key] += max(0, mark - old)
            previous_marks[counter_key] = mark
    return queue, marks


def shares(values):
    total = values.sum()
    return values / total if total else np.zeros(8)


def select_leaf(rows):
    scored = {}
    for leaf in sorted({leaf for _, leaf in rows}):
        relevant = [value for (bucket, candidate), value in rows.items()
                    if candidate == leaf and value["active_qps"] >= 16]
        if not relevant:
            continue
        qp_dom = np.array([shares(value["qps"]).max() for value in relevant])
        packet_dom = np.array([shares(value["packets"]).max() for value in relevant])
        aligned = np.array([value["change_alignment"] for value in relevant if value["changed_qps"] >= 8])
        score = np.percentile(qp_dom, 99) + np.percentile(packet_dom, 99)
        if len(aligned):
            score += np.percentile(aligned, 99)
        scored[leaf] = {
            "score": float(score),
            "qp_dominant_p99": float(np.percentile(qp_dom, 99)),
            "packet_dominant_p99": float(np.percentile(packet_dom, 99)),
            "change_alignment_p99": float(np.percentile(aligned, 99)) if len(aligned) else 0.0,
            "bins": len(relevant),
        }
    return max(scored, key=lambda leaf: scored[leaf]["score"]), scored


def series(rows, queue, marks, leaf, bin_us):
    buckets = sorted(bucket for bucket, candidate in rows if candidate == leaf)
    packet_share = np.array([shares(rows[(bucket, leaf)]["packets"]) for bucket in buckets])
    qp_share = np.array([shares(rows[(bucket, leaf)]["qps"]) for bucket in buckets])
    active = np.array([rows[(bucket, leaf)]["active_qps"] for bucket in buckets])
    changed = np.array([rows[(bucket, leaf)]["changed_qps"] for bucket in buckets])
    updated = np.array([rows[(bucket, leaf)]["updated_qps"] for bucket in buckets])
    alignment = np.array([rows[(bucket, leaf)]["change_alignment"] for bucket in buckets])
    q = np.array([max((queue.get((bucket, leaf, ev), np.nan) for ev in range(8)), default=np.nan)
                  for bucket in buckets])
    ecn = np.array([sum(marks.get((bucket, leaf, ev), 0) for ev in range(8)) for bucket in buckets])
    return np.asarray(buckets), packet_share, qp_share, active, changed, updated, alignment, q, ecn


def sample_hold(values):
    held = np.zeros_like(values, dtype=float)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid):
        held[:valid[0] + 1] = values[valid[0]]
        for left, right in zip(valid, valid[1:]):
            held[left:right] = values[left]
        held[valid[-1]:] = values[valid[-1]]
    return held


def moving_average(values, width=5):
    if width <= 1:
        return values
    kernel = np.ones(width) / width
    padded = np.pad(values, ((width // 2, width - 1 - width // 2), (0, 0)), mode="edge")
    return np.apply_along_axis(lambda column: np.convolve(column, kernel, mode="valid"), 0, padded)


def choose_window(packet_share, qp_share, active, changed, alignment, width=400):
    width = min(width, len(active))
    if width == len(active):
        return 0, width
    signal = packet_share.max(axis=1) + qp_share.max(axis=1)
    signal += np.where(changed >= 8, alignment, 0)
    signal *= np.minimum(1.0, active / 32.0)
    rolling = np.convolve(signal, np.ones(width), mode="valid")
    start = int(np.argmax(rolling))
    return start, start + width


def random_max_share_p99(counts):
    """Finite-sample p99 for the largest of eight uniform-choice buckets."""
    rng = np.random.default_rng(13)
    result = np.ones(len(counts))
    cache = {}
    for index, count in enumerate(counts.astype(int)):
        if count <= 0:
            continue
        if count not in cache:
            samples = rng.multinomial(count, np.full(8, 1 / 8), size=4000)
            cache[count] = float(np.percentile(samples.max(axis=1) / count, 99))
        result[index] = cache[count]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("decision_trace", type=Path)
    parser.add_argument("path_trace", type=Path)
    parser.add_argument("traffic_matrix", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--bin-us", type=float, default=1.0)
    parser.add_argument("--leaf", type=int, default=7)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    mapping = qp_source_leaf(args.traffic_matrix)
    print(f"mapped {len(mapping)} QPs", flush=True)
    rows, packet_total = load_decisions(args.decision_trace, mapping, args.bin_us, args.leaf)
    print(f"aggregated {packet_total} packet decisions into {len(rows)} ToR-time rows", flush=True)
    queue, marks = load_queue(args.path_trace, args.bin_us)
    print(f"aggregated {len(queue)} queue rows", flush=True)
    leaf_metrics = select_leaf(rows)[1]
    leaf = args.leaf
    values = series(rows, queue, marks, leaf, args.bin_us)
    buckets, packet_share, qp_share, active, changed, updated, alignment, q, ecn = values
    start, stop = choose_window(packet_share, qp_share, active, changed, alignment)
    time = buckets[start:stop] * args.bin_us
    pshare, qshare = packet_share[start:stop], qp_share[start:stop]
    active, changed, updated = active[start:stop], changed[start:stop], updated[start:stop]
    alignment, q, ecn = alignment[start:stop], q[start:stop], ecn[start:stop]
    alignment_null = random_max_share_p99(changed)
    qp_share_null = random_max_share_p99(active)

    colors = ["#1f4e79", "#3d6f8e", "#6a8caf", "#9db4c8", "#d69e2e", "#dd6b20", "#c05621", "#7b341e"]
    fig, axes = plt.subplots(4, 1, figsize=(12, 9.5), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1, 1.05, 1.05]})
    extent = [time[0], time[-1] + args.bin_us, -.5, 7.5]
    im0 = axes[0].imshow(pshare.T, aspect="auto", origin="lower", extent=extent,
                         vmin=0, vmax=max(.35, pshare.max()), cmap="YlOrBr")
    axes[0].set_title("Per-packet EV selection share")
    axes[0].set_ylabel("EV")
    axes[0].set_yticks(range(8))
    fig.colorbar(im0, ax=axes[0], pad=.01, label="Share")

    im1 = axes[1].imshow(qshare.T, aspect="auto", origin="lower", extent=extent,
                         vmin=0, vmax=max(.35, qshare.max()), cmap="Blues")
    axes[1].set_title("Distinct-QP EV selection share")
    axes[1].set_ylabel("EV")
    axes[1].set_yticks(range(8))
    fig.colorbar(im1, ax=axes[1], pad=.01, label="Share")

    change_fraction = np.divide(changed, active, out=np.zeros_like(changed, dtype=float), where=active > 0)
    update_fraction = np.divide(updated, active, out=np.zeros_like(updated, dtype=float), where=active > 0)
    axes[2].plot(time, change_fraction, color="#1f4e79", lw=1.0, label="QPs changing EV / active QPs")
    axes[2].plot(time, update_fraction, color="#d69e2e", lw=1.0, label="QPs receiving new profile / active QPs")
    axes[2].plot(time, alignment, color="#c53030", lw=1.2, label="Changed QPs choosing same new EV")
    axes[2].plot(time, alignment_null, color="#4a5568", lw=.9, ls="--", label="Random 8-way p99 bound")
    axes[2].set_ylim(0, 1.05)
    axes[2].set_ylabel("Fraction")
    axes[2].set_title("Cross-QP action synchronization")
    axes[2].legend(loc="upper right", ncol=2, fontsize=8)

    q_held = sample_hold(q)
    axes[3].step(time, q_held, where="post", color="#dd6b20", lw=1.2, label="Max path queue occupancy (10 μs sample-hold)")
    axes[3].fill_between(time, 0, q_held, step="post", color="#f6ad55", alpha=.2)
    axes[3].set_ylim(0, max(1.05, np.nanmax(q) * 1.05))
    axes[3].set_ylabel("Queue / capacity")
    ecn_axis = axes[3].twinx()
    ecn_axis.plot(time, ecn, color="#718096", lw=.9, alpha=.85, label="ECN marks")
    ecn_axis.set_ylabel("ECN marks / μs")
    lines = axes[3].lines + ecn_axis.lines
    axes[3].legend(lines, [line.get_label() for line in lines], loc="upper right", fontsize=8)
    axes[3].set_title("Subsequent path congestion signals")
    axes[3].set_xlabel("Simulation time (μs)")

    metrics = leaf_metrics[leaf]
    fig.suptitle("NetAware micro-timescale path synchronization — asymmetric A2A 256 MiB, P=16", fontsize=14, y=.99)
    fig.text(.5, .963, f"seed 13 | Source ToR {leaf} | {args.bin_us:g} μs bins | p99 distinct-QP dominant EV share {metrics['qp_dominant_p99']:.1%} | p99 changed-QP alignment {metrics['change_alignment_p99']:.1%}", ha="center", fontsize=9)
    fig.tight_layout(rect=[0, 0, 1, .95])
    png = args.output_dir / "netaware_a2a256_p16_micro_sync.png"
    pdf = args.output_dir / "netaware_a2a256_p16_micro_sync.pdf"
    fig.savefig(png, dpi=220, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)

    # Direct eight-path view: selection share and congestion on the same time axis.
    cjk_font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    font_manager.fontManager.addfont(cjk_font_path)
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=cjk_font_path).get_name()
    plt.rcParams["axes.unicode_minus"] = False
    smooth_share = moving_average(pshare, width=5)
    ev_queue = np.zeros((len(time), 8), dtype=float)
    for ev in range(8):
        raw = np.array([queue.get((int(bucket), leaf, ev), np.nan) for bucket in buckets[start:stop]])
        ev_queue[:, ev] = sample_hold(raw)
    dominant = smooth_share.argmax(axis=1)
    max_share = smooth_share.max(axis=1)

    fig2, axes2 = plt.subplots(9, 1, figsize=(13, 15), sharex=True,
                               gridspec_kw={"height_ratios": [1.15] + [1] * 8})
    axes2[0].step(time, dominant, where="post", color="#1f4e79", lw=1.2, label="当前使用率最高的 EV")
    axes2[0].set_yticks(range(8))
    axes2[0].set_ylabel("主导 EV")
    top_share_axis = axes2[0].twinx()
    top_share_axis.plot(time, max_share * 100, color="#c53030", lw=1.0, label="最高 EV 使用率")
    top_share_axis.axhline(12.5, color="#4a5568", lw=.8, ls="--", label="理想均分 12.5%")
    top_share_axis.set_ylabel("最高使用率 (%)")
    top_share_axis.set_ylim(0, max(45, max_share.max() * 110))
    lines = axes2[0].lines + top_share_axis.lines
    axes2[0].legend(lines, [line.get_label() for line in lines], loc="upper right", ncol=3, fontsize=8)
    axes2[0].set_title("主导路径是否反复切换，以及切换时流量是否明显集中")

    for ev in range(8):
        axis = axes2[ev + 1]
        axis.plot(time, smooth_share[:, ev] * 100, color="#1f4e79", lw=1.0, label=f"EV{ev} 使用率")
        axis.axhline(12.5, color="#718096", lw=.7, ls="--")
        axis.set_ylim(0, max(45, smooth_share[:, ev].max() * 110))
        axis.set_ylabel(f"EV{ev}\n使用率 (%)", color="#1f4e79")
        queue_axis = axis.twinx()
        queue_axis.step(time, ev_queue[:, ev] * 100, where="post", color="#dd6b20", lw=1.0,
                        label=f"EV{ev} 最大队列占用")
        queue_axis.set_ylim(0, 105)
        queue_axis.set_ylabel("最大队列 (%)", color="#dd6b20")
        is_dominant = dominant == ev
        axis.fill_between(time, 0, axis.get_ylim()[1], where=is_dominant, step="post",
                          color="#f6ad55", alpha=.10)
        if ev == 0:
            axis.legend(loc="upper left", fontsize=8)
            queue_axis.legend(loc="upper right", fontsize=8)
    axes2[-1].set_xlabel("仿真时间（微秒）")
    fig2.suptitle("NetAware 八条路径的使用率与拥堵时间线", fontsize=16, y=.995)
    fig2.text(.5, .977,
              "非对称 All-to-All 256 MiB，P=16，seed 13，Source ToR 7；蓝线为 5 μs 平滑后的逐包 EV 使用率，橙线为跨目的 ToR 的最大队列占用",
              ha="center", fontsize=10)
    fig2.tight_layout(rect=[0, 0, 1, .965])
    cn_png = args.output_dir / "netaware_a2a256_p16_8ev_timeline_cn.png"
    cn_pdf = args.output_dir / "netaware_a2a256_p16_8ev_timeline_cn.pdf"
    fig2.savefig(cn_png, dpi=220, bbox_inches="tight")
    fig2.savefig(cn_pdf, bbox_inches="tight")
    plt.close(fig2)

    report = {
        "scenario": "asymmetric_alltoall_background_256mib_p16", "seed": 13,
        "bin_us": args.bin_us, "decision_packets": packet_total,
        "selected_source_leaf": leaf, "selected_metrics": metrics,
        "window_start_us": float(time[0]), "window_end_us": float(time[-1] + args.bin_us),
        "window_max_packet_dominant_share": float(pshare.max(axis=1).max()),
        "window_max_qp_dominant_share": float(qshare.max(axis=1).max()),
        "window_max_changed_qp_alignment": float(alignment[changed >= 8].max()) if np.any(changed >= 8) else 0.0,
        "window_alignment_above_random_p99_fraction": float(np.mean(alignment > alignment_null)),
        "window_qp_dominance_above_random_p99_fraction": float(np.mean(qshare.max(axis=1) > qp_share_null)),
        "window_max_profile_update_fraction": float(update_fraction.max()),
        "window_max_ev_change_fraction": float(change_fraction.max()),
        "window_max_queue_fraction": float(np.nanmax(q)),
        "all_leaf_metrics": leaf_metrics, "figure_png": str(png), "figure_pdf": str(pdf),
        "eight_ev_timeline_png": str(cn_png), "eight_ev_timeline_pdf": str(cn_pdf),
    }
    metrics_path = args.output_dir / "netaware_a2a256_p16_micro_sync_metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
