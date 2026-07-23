#!/usr/bin/env python3
"""Plot cumulative and one-BG-cycle path byte-share CV over time."""

import argparse
import csv
import gzip
import json
import math
from collections import defaultdict, deque
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


SIZES = (64, 256, 1024)
SCHEMES = ("mrc", "sglb", "n-mrc")
LABELS = {"mrc": "MRC", "sglb": "SGLB", "n-mrc": "N-MRC"}
COLORS = {"mrc": "#6A51A3", "sglb": "#31A354", "n-mrc": "#3074FE"}
STYLES = {"mrc": "-.", "sglb": "--", "n-mrc": "-"}
BG_PHASE_US = {64: 200.0, 256: 400.0, 1024: 1000.0}
SAMPLE_US = 50.0
SPINES = 8
SENTINEL = 2 ** 32 - 1


def scenario(size):
    return f"asymmetric_alltoall_background_{size}mib_p4"


def trace_path(batch, size, scheme):
    figures = batch / "figures"
    if size == 64 and scheme in ("mrc", "n-mrc"):
        root = figures / "paper_style" / "traces_alltoall_archive_20260722"
    elif size == 256:
        root = figures / "paper_style" / "traces_256mib_bg"
    else:
        root = figures / "no_websearch100" / "traces_size_sweep_p4"
    return (
        root / "raw" / scenario(size) / scheme / "seed_13" /
        "trace_path_state.csv.gz"
    )


def cct_us(batch, size, scheme):
    path = batch / "raw" / scenario(size) / scheme / "seed_13" / "summary.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    if row.get("all_flows_completed") != 1:
        raise ValueError(f"incomplete run: {path}")
    return float(row["primary_us"])


def cv(values):
    mean = sum(values) / len(values)
    if mean <= 0:
        return 0.0
    return math.sqrt(
        sum((value - mean) ** 2 for value in values) / len(values)
    ) / mean


def aggregate_50us_spine_deltas(path, max_time_us):
    """Reproduce the audited positive-delta logic, retaining per-spine deltas."""
    previous = {}
    resolved = {}
    bins = defaultdict(lambda: [0.0] * SPINES)
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {
            "time_us", "src_leaf", "dst_leaf", "path_id", "spine_id",
            "bytes_sent_on_path", "netaware_level_reason", "link_down",
        }
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"unexpected trace schema: {path}")
        for row in reader:
            time_us = float(row["time_us"])
            if time_us > max_time_us:
                break
            logical = (
                int(row["src_leaf"]), int(row["dst_leaf"]), int(row["path_id"])
            )
            spine = int(row["spine_id"])
            if spine == SENTINEL:
                if row["netaware_level_reason"] != "missing_queue":
                    raise ValueError("invalid missing-queue sentinel")
                resolved.setdefault(logical, SENTINEL)
                continue
            if not 0 <= spine < SPINES:
                raise ValueError("invalid spine ID")
            old_spine = resolved.get(logical)
            newly_resolved = old_spine == SENTINEL
            if old_spine not in (None, SENTINEL, spine):
                raise ValueError("logical path switched physical spine")
            resolved[logical] = spine
            current = float(row["bytes_sent_on_path"])
            prior = previous.get(logical, current if newly_resolved else 0.0)
            delta = max(0.0, current - prior)
            previous[logical] = current
            bin_id = int(math.floor((time_us + 1e-9) / SAMPLE_US))
            bins[bin_id][spine] += delta
    if not bins:
        raise ValueError(f"empty trace: {path}")
    return [(bin_id * SAMPLE_US, tuple(bins[bin_id]))
            for bin_id in sorted(bins)]


def build_series(samples, cycle_us):
    cumulative = [0.0] * SPINES
    cycle_bins = max(1, int(round(cycle_us / SAMPLE_US)))
    window = deque()
    window_sum = [0.0] * SPINES
    rows = []
    for time_us, deltas in samples:
        for spine in range(SPINES):
            cumulative[spine] += deltas[spine]
            window_sum[spine] += deltas[spine]
        window.append(deltas)
        if len(window) > cycle_bins:
            expired = window.popleft()
            for spine in range(SPINES):
                window_sum[spine] -= expired[spine]
        rows.append({
            "time_us": time_us,
            "cumulative_cv": cv(cumulative),
            "cycle_cv": cv(window_sum) if len(window) == cycle_bins else None,
            "bytes_delta": sum(deltas),
            "cumulative_bytes": sum(cumulative),
        })
    return rows


def prepare(batch):
    output = {}
    for size in SIZES:
        output[size] = {}
        cycle_us = 2.0 * BG_PHASE_US[size]
        for scheme in SCHEMES:
            end = cct_us(batch, size, scheme)
            samples = aggregate_50us_spine_deltas(
                trace_path(batch, size, scheme), end
            )
            output[size][scheme] = build_series(samples, cycle_us)
    return output


def shade_bg(axis, upper_ms, phase_us):
    phase_ms = phase_us / 1000.0
    start = 0.0
    while start < upper_ms:
        axis.axvspan(
            start, min(start + phase_ms, upper_ms),
            facecolor="#CBD5E1", alpha=0.58, linewidth=0, zorder=0,
        )
        start += 2.0 * phase_ms


def style_axis(axis):
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.grid(True, color="#BABCBE", linestyle=":", linewidth=0.9, alpha=0.8)
    axis.tick_params(labelsize=12)
    axis.set_axisbelow(True)


def build_figure(series):
    figure, axes = plt.subplots(2, 3, figsize=(18, 9), squeeze=False)
    for column, size in enumerate(SIZES):
        upper = max(
            rows[-1]["time_us"] for rows in series[size].values()
        ) / 1000.0
        shade_bg(axes[0, column], upper, BG_PHASE_US[size])
        shade_bg(axes[1, column], upper, BG_PHASE_US[size])
        for scheme in SCHEMES:
            rows = series[size][scheme]
            x = [row["time_us"] / 1000.0 for row in rows]
            axes[0, column].plot(
                x, [row["cumulative_cv"] for row in rows],
                color=COLORS[scheme], linestyle=STYLES[scheme],
                linewidth=2.5,
            )
            cycle_rows = [row for row in rows if row["cycle_cv"] is not None]
            axes[1, column].plot(
                [row["time_us"] / 1000.0 for row in cycle_rows],
                [row["cycle_cv"] for row in cycle_rows],
                color=COLORS[scheme], linestyle=STYLES[scheme],
                linewidth=2.2,
            )
            final = rows[-1]
            axes[0, column].scatter(
                final["time_us"] / 1000.0, final["cumulative_cv"],
                s=55, color=COLORS[scheme], zorder=4,
            )
            axes[0, column].annotate(
                f"{final['cumulative_cv']:.4f}",
                (final["time_us"] / 1000.0, final["cumulative_cv"]),
                xytext=(-5, 8), textcoords="offset points",
                ha="right", fontsize=10, color=COLORS[scheme],
            )
        phase = int(BG_PHASE_US[size])
        axes[0, column].set_title(
            f"{size} MiB · BG {phase}/{phase} µs",
            fontsize=17, pad=10,
        )
        axes[1, column].set_xlabel("Time (ms)", fontsize=14)
        for row in range(2):
            axes[row, column].set_xlim(0, upper)
            style_axis(axes[row, column])
    axes[0, 0].set_ylabel("Cumulative path byte-share CV", fontsize=15)
    axes[1, 0].set_ylabel("One-BG-cycle path byte-share CV", fontsize=15)
    handles = [
        Line2D([], [], color=COLORS[scheme], linestyle=STYLES[scheme],
               linewidth=2.7, label=LABELS[scheme])
        for scheme in SCHEMES
    ]
    handles.append(
        plt.Rectangle((0, 0), 1, 1, facecolor="#CBD5E1", alpha=0.72,
                      label="Periodic BG ON")
    )
    figure.legend(
        handles=handles, loc="lower center", ncol=4, frameon=False,
        fontsize=13, bbox_to_anchor=(0.5, 0.005),
    )
    figure.suptitle(
        "Path imbalance accumulation over time · Asymmetric All-to-All · P=4",
        fontsize=20, y=0.995,
    )
    figure.text(
        0.5, 0.935,
        "50 µs bins · BG included · each scheme ends at its own CCT · "
        "lower is better",
        ha="center", fontsize=13, color="#404040",
    )
    figure.subplots_adjust(
        left=0.075, right=0.985, bottom=0.115, top=0.855,
        wspace=0.24, hspace=0.28,
    )
    return figure


def write_csv(path, series):
    fields = (
        "size_mib", "scheme", "time_us", "cumulative_path_share_cv",
        "one_bg_cycle_path_share_cv", "bytes_delta", "cumulative_bytes",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for size in SIZES:
            for scheme in SCHEMES:
                for row in series[size][scheme]:
                    writer.writerow({
                        "size_mib": size,
                        "scheme": scheme,
                        "time_us": f"{row['time_us']:.12g}",
                        "cumulative_path_share_cv":
                            f"{row['cumulative_cv']:.12g}",
                        "one_bg_cycle_path_share_cv": (
                            "" if row["cycle_cv"] is None
                            else f"{row['cycle_cv']:.12g}"
                        ),
                        "bytes_delta": f"{row['bytes_delta']:.12g}",
                        "cumulative_bytes": f"{row['cumulative_bytes']:.12g}",
                    })


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    series = prepare(args.batch)
    csv_path = args.output_dir / "path_imbalance_timeline.csv"
    write_csv(csv_path, series)
    figure = build_figure(series)
    try:
        for suffix in (".png", ".pdf"):
            path = args.output_dir / ("path_imbalance_timeline" + suffix)
            figure.savefig(
                path, dpi=300 if suffix == ".png" else None,
                bbox_inches="tight",
            )
            print(path)
    finally:
        plt.close(figure)
    print(csv_path)


if __name__ == "__main__":
    main()
