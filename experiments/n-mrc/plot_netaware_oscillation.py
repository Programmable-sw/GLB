#!/usr/bin/env python3
"""Plot path-load oscillation diagnostics from htsim path-state traces."""

import argparse
import csv
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def open_text(path):
    return gzip.open(path, "rt", newline="") if str(path).endswith(".gz") else open(path, newline="")


def load_trace(path, bin_us):
    previous = {}
    traffic = defaultdict(lambda: np.zeros(8, dtype=float))
    queue = defaultdict(lambda: np.zeros(8, dtype=float))
    ecn = defaultdict(lambda: np.zeros(8, dtype=float))
    with open_text(path) as handle:
        for row in csv.DictReader(handle):
            try:
                time_us = float(row["time_us"])
                src = int(row["src_leaf"])
                dst = int(row["dst_leaf"])
                path_id = int(row["path_id"])
                sent = int(row["bytes_sent_on_path"])
                marks = int(row["ecn_marks_on_path"])
                q1 = float(row["q_leaf_to_spine"])
                q2 = float(row["q_spine_to_dst_leaf"])
                qm1 = float(row["q_leaf_to_spine_max"])
                qm2 = float(row["q_spine_to_dst_leaf_max"])
            except (KeyError, TypeError, ValueError):
                continue
            key = (src, dst, path_id)
            old_sent, old_marks = previous.get(key, (sent, marks))
            previous[key] = (sent, marks)
            bucket = int(time_us // bin_us)
            traffic[(bucket, src)][path_id] += max(0, sent - old_sent)
            ecn[(bucket, src)][path_id] += max(0, marks - old_marks)
            pressure = max(q1 / qm1 if qm1 else 0.0, q2 / qm2 if qm2 else 0.0)
            queue[(bucket, src)][path_id] = max(queue[(bucket, src)][path_id], pressure)
    return traffic, queue, ecn


def source_metrics(traffic, queue):
    sources = sorted({src for _, src in traffic})
    result = {}
    for src in sources:
        buckets = sorted(bucket for bucket, candidate in traffic if candidate == src)
        loads = np.array([traffic[(bucket, src)] for bucket in buckets])
        queues = np.array([queue[(bucket, src)] for bucket in buckets])
        totals = loads.sum(axis=1)
        active = totals > 0
        if active.sum() < 3:
            continue
        shares = np.divide(loads, totals[:, None], out=np.zeros_like(loads), where=totals[:, None] > 0)
        dominant = shares.argmax(axis=1)
        switches = (dominant[1:] != dominant[:-1]) & active[1:] & active[:-1]
        cv = np.divide(loads.std(axis=1), loads.mean(axis=1), out=np.zeros(len(loads)), where=loads.mean(axis=1) > 0)
        result[src] = {
            "switch_rate": float(switches.sum() / max(1, (active[1:] & active[:-1]).sum())),
            "cv_p95": float(np.percentile(cv[active], 95)),
            "dominant_share_p95": float(np.percentile(shares[active].max(axis=1), 95)),
            "queue_p99": float(np.percentile(queues[active].max(axis=1), 99)),
        }
        result[src]["score"] = result[src]["switch_rate"] * result[src]["cv_p95"] * (1 + result[src]["queue_p99"])
    return result


def arrays_for_source(traffic, queue, ecn, src):
    buckets = sorted(bucket for bucket, candidate in traffic if candidate == src)
    loads = np.array([traffic[(bucket, src)] for bucket in buckets])
    queues = np.array([queue[(bucket, src)] for bucket in buckets])
    marks = np.array([ecn[(bucket, src)] for bucket in buckets])
    totals = loads.sum(axis=1)
    shares = np.divide(loads, totals[:, None], out=np.zeros_like(loads), where=totals[:, None] > 0)
    cv = np.divide(loads.std(axis=1), loads.mean(axis=1), out=np.zeros(len(loads)), where=loads.mean(axis=1) > 0)
    return np.asarray(buckets), loads, shares, queues, marks, cv


def best_window(shares, queues, cv, bins):
    width = min(60, len(bins))
    if width == len(bins):
        return 0, len(bins)
    dom = shares.argmax(axis=1)
    scores = np.zeros(len(bins) - width + 1)
    for start in range(len(scores)):
        stop = start + width
        switches = np.count_nonzero(dom[start + 1:stop] != dom[start:stop - 1])
        scores[start] = switches + 3 * np.mean(cv[start:stop]) + 2 * np.max(queues[start:stop])
    start = int(np.argmax(scores))
    return start, start + width


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--bin-us", type=float, default=20.0)
    parser.add_argument("--scenario", default="healthy_p2p_websearch_100pct")
    parser.add_argument("--title", default="WebSearch 100%")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    traffic, queue, ecn = load_trace(args.trace, args.bin_us)
    metrics = source_metrics(traffic, queue)
    src = max(metrics, key=lambda value: metrics[value]["score"])
    bins, loads, shares, queues, marks, cv = arrays_for_source(traffic, queue, ecn, src)
    start, stop = best_window(shares, queues, cv, bins)
    times = bins[start:stop] * args.bin_us
    share_window = shares[start:stop]
    queue_window = queues[start:stop]
    mark_window = marks[start:stop]
    cv_window = cv[start:stop]
    dominant = share_window.argmax(axis=1)
    dominant_share = share_window.max(axis=1)

    plt.rcParams.update({"font.size": 10, "axes.titlesize": 11, "axes.labelsize": 10})
    fig, axes = plt.subplots(3, 1, figsize=(11.5, 8.2), sharex=True,
                             gridspec_kw={"height_ratios": [1.35, 1, 1]})
    image = axes[0].imshow(share_window.T, aspect="auto", origin="lower", vmin=0, vmax=max(.35, share_window.max()),
                           extent=[times[0], times[-1] + args.bin_us, -.5, 7.5], cmap="magma")
    axes[0].set_ylabel("Path / EV")
    axes[0].set_yticks(range(8))
    axes[0].set_title("Path traffic share (warm colors indicate synchronized concentration)")
    fig.colorbar(image, ax=axes[0], pad=.01, label="Traffic share")

    axes[1].step(times, dominant, where="post", color="#2b6cb0", lw=1.4, label="Dominant path")
    axes[1].set_ylabel("Dominant path")
    axes[1].set_yticks(range(8))
    twin = axes[1].twinx()
    twin.plot(times, dominant_share, color="#c53030", lw=1.2, label="Dominant share")
    twin.plot(times, cv_window / max(1.0, cv_window.max()), color="#805ad5", lw=1.0, alpha=.8, label="Normalized load CV")
    twin.set_ylim(0, 1.05)
    twin.set_ylabel("Share / normalized CV")
    lines = axes[1].lines + twin.lines
    axes[1].legend(lines, [line.get_label() for line in lines], loc="upper right", ncol=3, fontsize=8)

    max_queue = queue_window.max(axis=1)
    total_marks = mark_window.sum(axis=1)
    axes[2].plot(times, max_queue, color="#dd6b20", lw=1.4, label="Max path queue occupancy")
    axes[2].fill_between(times, 0, max_queue, color="#f6ad55", alpha=.2)
    axes[2].set_ylabel("Queue / capacity")
    mark_axis = axes[2].twinx()
    mark_axis.plot(times, total_marks, color="#718096", lw=.9, alpha=.8, label="ECN marks")
    mark_axis.set_ylabel("ECN marks / bin")
    lines = axes[2].lines + mark_axis.lines
    axes[2].legend(lines, [line.get_label() for line in lines], loc="upper right", fontsize=8)
    axes[2].set_xlabel("Simulation time (μs)")

    selected = metrics[src]
    fig.suptitle(f"NetAware synchronized path-migration diagnostic — {args.title}, seed 13", fontsize=14, y=.985)
    fig.text(.5, .952, f"Most oscillatory Source ToR: {src} | {args.bin_us:g} μs bins | switch rate {selected['switch_rate']:.1%} | p95 dominant share {selected['dominant_share_p95']:.1%}", ha="center", fontsize=9)
    fig.tight_layout(rect=[0, 0, 1, .94])
    slug = args.scenario.replace("/", "_")
    png = args.output_dir / f"netaware_{slug}_oscillation_diagnostic.png"
    pdf = args.output_dir / f"netaware_{slug}_oscillation_diagnostic.pdf"
    fig.savefig(png, dpi=220, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)

    report = {
        "scenario": args.scenario, "seed": 13,
        "trace_end_us": float(bins[-1] * args.bin_us), "bin_us": args.bin_us,
        "selected_source_leaf": src, "selected_metrics": selected,
        "window_start_us": float(times[0]), "window_end_us": float(times[-1] + args.bin_us),
        "window_dominant_switches": int(np.count_nonzero(dominant[1:] != dominant[:-1])),
        "window_max_queue_fraction": float(max_queue.max()),
        "window_total_ecn_marks": float(total_marks.sum()),
        "all_source_metrics": metrics,
        "figure_png": str(png), "figure_pdf": str(pdf),
    }
    (args.output_dir / f"netaware_{slug}_oscillation_metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
