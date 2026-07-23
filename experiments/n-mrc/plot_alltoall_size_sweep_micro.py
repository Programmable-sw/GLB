#!/usr/bin/env python3
"""Plot P=4 asymmetric All-to-All path balance for 64/256/1024 MiB."""

import argparse
import csv
import importlib.util
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


SIZES = (64, 256, 1024)
SCHEMES = ("mrc", "sglb", "n-mrc")
LABELS = {"mrc": "MRC", "sglb": "SGLB", "n-mrc": "N-MRC"}
COLORS = {"mrc": "#6A51A3", "sglb": "#31A354", "n-mrc": "#3074FE"}
STYLES = {"mrc": "-.", "sglb": "--", "n-mrc": "-"}
SAMPLE_US = 50.0
BG_PHASE_MS = {64: 0.2, 256: 0.4, 1024: 1.0}


def load_base_module(path):
    spec = importlib.util.spec_from_file_location("path_diagnostics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scenario(size):
    return f"asymmetric_alltoall_background_{size}mib_p4"


def trace_path(batch, size, scheme):
    figure_root = batch / "figures"
    if size == 64 and scheme in ("mrc", "n-mrc"):
        root = figure_root / "paper_style" / "traces_alltoall_archive_20260722"
    elif size == 256:
        root = figure_root / "paper_style" / "traces_256mib_bg"
    else:
        root = figure_root / "no_websearch100" / "traces_size_sweep_p4"
    return root / "raw" / scenario(size) / scheme / "seed_13" / "trace_path_state.csv.gz"


def scheme_window_end(batch, size, scheme):
    path = batch / "raw" / scenario(size) / scheme / "seed_13" / "summary.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    if row.get("all_flows_completed") != 1:
        raise ValueError(f"incomplete reference run: {path}")
    return float(row["primary_us"])


def select_50us(series):
    """Normalize both historical 10-us and new 50-us traces to 50 us."""
    selected = []
    last_bin = None
    for point in series.timeline:
        sample_bin = int(round(point.time_us / SAMPLE_US))
        if abs(point.time_us - sample_bin * SAMPLE_US) > 1e-6:
            continue
        if sample_bin != last_bin:
            selected.append(point)
            last_bin = sample_bin
    if not selected:
        raise ValueError("trace has no samples on the common 50-us grid")
    return tuple(selected)


def rolling_median(values, width=5):
    radius = width // 2
    output = []
    for index in range(len(values)):
        sample = sorted(values[max(0, index-radius):index+radius+1])
        middle = len(sample) // 2
        output.append(sample[middle] if len(sample) % 2 else
                      (sample[middle-1] + sample[middle]) / 2.0)
    return tuple(output)


def prepare(batch, base):
    displayed = {}
    for size in SIZES:
        displayed[size] = {}
        for scheme in SCHEMES:
            end = scheme_window_end(batch, size, scheme)
            path = trace_path(batch, size, scheme)
            if not path.is_file():
                raise FileNotFoundError(path)
            series = base.aggregate_path_trace(path, max_time_us=end)
            points = select_50us(series)
            positive_times = [point.time_us for point in points if point.bytes_delta > 0]
            if not positive_times:
                raise ValueError(f"no positive business interval: {path}")
            displayed[size][scheme] = {
                "series": series,
                "points": points,
                "valid": (min(positive_times), max(positive_times)),
                "cv": rolling_median([point.utilization_cv for point in points]),
                "queue": rolling_median([point.mean_queue_fraction for point in points]),
            }
    return displayed


def style_axis(axis):
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.grid(True, color="#BABCBE", linestyle=":", linewidth=1.0, alpha=0.8)
    axis.tick_params(labelsize=12)
    axis.set_axisbelow(True)


def shade_background(axis, lower, upper, on_ms, off_ms):
    cycle_ms = on_ms + off_ms
    start = math.floor(lower / cycle_ms) * cycle_ms
    while start < upper:
        axis.axvspan(max(lower, start), min(upper, start + on_ms),
                     color="#E8E8E8", alpha=0.36, linewidth=0, zorder=0)
        start += cycle_ms


def valid_rows(trace):
    lower, upper = trace["valid"]
    return [(index, point) for index, point in enumerate(trace["points"])
            if lower <= point.time_us <= upper]


def timeline_figure(displayed):
    figure, axes = plt.subplots(2, 3, figsize=(18, 9), squeeze=False)
    for column, size in enumerate(SIZES):
        times = []
        for scheme in SCHEMES:
            trace = displayed[size][scheme]
            rows = valid_rows(trace)
            x = [point.time_us / 1000.0 for _, point in rows]
            times.extend(x)
            axes[0, column].plot(x, [trace["cv"][i] for i, _ in rows],
                                 color=COLORS[scheme], linestyle=STYLES[scheme],
                                 linewidth=2.4, label=LABELS[scheme])
            axes[1, column].plot(x, [trace["queue"][i] for i, _ in rows],
                                 color=COLORS[scheme], linestyle=STYLES[scheme],
                                 linewidth=2.4, label=LABELS[scheme])
        lower, upper = min(times), max(times)
        for row in range(2):
            shade_background(
                axes[row, column], lower, upper,
                BG_PHASE_MS[size], BG_PHASE_MS[size],
            )
            axes[row, column].set_xlim(lower, upper)
            style_axis(axes[row, column])
        phase_us = int(BG_PHASE_MS[size] * 1000)
        axes[0, column].set_title(
            f"{size} MiB · BG {phase_us}/{phase_us} µs",
            fontsize=17, pad=10,
        )
        axes[1, column].set_xlabel("Time (ms)", fontsize=14)
    axes[0, 0].set_ylabel("Utilization CV", fontsize=15)
    axes[1, 0].set_ylabel("Mean queue fraction", fontsize=15)
    handles = [Line2D([], [], color=COLORS[s], linestyle=STYLES[s],
                      linewidth=2.7, label=LABELS[s]) for s in SCHEMES]
    handles.append(plt.Rectangle((0, 0), 1, 1, facecolor="#E8E8E8", alpha=0.55,
                                 label="Periodic BG ON"))
    figure.legend(handles=handles, loc="lower center", ncol=4, frameon=False,
                  fontsize=13, bbox_to_anchor=(0.5, 0.005))
    figure.suptitle("Asymmetric All-to-All with periodic background traffic · P=4",
                    fontsize=20, y=0.985)
    figure.text(0.5, 0.945,
                "50 µs common sampling · 5-sample rolling median · valid business interval only",
                ha="center", fontsize=13, color="#404040")
    figure.subplots_adjust(left=0.075, right=0.985, bottom=0.115, top=0.88,
                           wspace=0.24, hspace=0.28)
    return figure


def share_figure(displayed):
    figure, axes = plt.subplots(1, 3, figsize=(18, 5.5), squeeze=False, sharey=True)
    for column, size in enumerate(SIZES):
        axis = axes[0, column]
        width = 0.24
        for offset, scheme in enumerate(SCHEMES):
            shares = displayed[size][scheme]["series"].final_shares
            x = [spine + (offset - 1) * width for spine in range(8)]
            axis.bar(x, [shares.get(spine, 0.0) for spine in range(8)], width=width,
                     color=COLORS[scheme], edgecolor="white", linewidth=0.5,
                     label=LABELS[scheme])
        axis.axhline(1 / 8, color="#444444", linewidth=1.5, linestyle=":",
                     label="Ideal 1/8")
        axis.set_title(f"{size} MiB", fontsize=17)
        axis.set_xticks(range(8))
        axis.set_xlabel("Spine ID", fontsize=14)
        style_axis(axis)
    axes[0, 0].set_ylabel("Share of positive byte deltas", fontsize=15)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=4, frameon=False,
                  fontsize=13, bbox_to_anchor=(0.5, 0.005))
    figure.suptitle("Final traffic distribution across Spine paths · P=4",
                    fontsize=20, y=0.98)
    figure.subplots_adjust(left=0.075, right=0.985, bottom=0.19, top=0.84, wspace=0.18)
    return figure


def write_evidence(displayed, output):
    timeline_path = output / "alltoall_size_sweep_p4_timeline.csv"
    with timeline_path.open("w", newline="", encoding="utf-8") as handle:
        fields = ("size_mib", "scheme", "time_us", "utilization_cv_raw",
                  "utilization_cv_display", "mean_queue_fraction_raw",
                  "mean_queue_fraction_display", "bytes_delta", "valid")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for size in SIZES:
            for scheme in SCHEMES:
                trace = displayed[size][scheme]
                lower, upper = trace["valid"]
                for index, point in enumerate(trace["points"]):
                    writer.writerow({
                        "size_mib": size, "scheme": scheme,
                        "time_us": f"{point.time_us:.12g}",
                        "utilization_cv_raw": f"{point.utilization_cv:.12g}",
                        "utilization_cv_display": f"{trace['cv'][index]:.12g}",
                        "mean_queue_fraction_raw": f"{point.mean_queue_fraction:.12g}",
                        "mean_queue_fraction_display": f"{trace['queue'][index]:.12g}",
                        "bytes_delta": f"{point.bytes_delta:.12g}",
                        "valid": int(lower <= point.time_us <= upper),
                    })
    summary_path = output / "alltoall_size_sweep_p4_path_balance.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        fields = ("size_mib", "scheme", "spine_id", "byte_share", "jain_fairness",
                  "share_cv", "share_spread", "valid_start_us", "valid_end_us")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for size in SIZES:
            for scheme in SCHEMES:
                trace = displayed[size][scheme]
                shares = [trace["series"].final_shares.get(i, 0.0) for i in range(8)]
                fairness = sum(shares) ** 2 / (8 * sum(value ** 2 for value in shares))
                share_cv = math.sqrt(sum((value - 0.125) ** 2 for value in shares) / 8) / 0.125
                spread = max(shares) - min(shares)
                for spine, share in enumerate(shares):
                    writer.writerow({"size_mib": size, "scheme": scheme, "spine_id": spine,
                                     "byte_share": f"{share:.12g}",
                                     "jain_fairness": f"{fairness:.12g}",
                                     "share_cv": f"{share_cv:.12g}",
                                     "share_spread": f"{spread:.12g}",
                                     "valid_start_us": f"{trace['valid'][0]:.12g}",
                                     "valid_end_us": f"{trace['valid'][1]:.12g}"})
    return timeline_path, summary_path


def render(batch, output, base_module_path):
    output.mkdir(parents=True, exist_ok=True)
    displayed = prepare(batch, load_base_module(base_module_path))
    artifacts = list(write_evidence(displayed, output))
    for stem, builder in (("alltoall_size_sweep_p4_timeline", timeline_figure),
                          ("alltoall_size_sweep_p4_path_shares", share_figure)):
        figure = builder(displayed)
        try:
            for suffix in (".png", ".pdf"):
                path = output / (stem + suffix)
                figure.savefig(path, dpi=300 if suffix == ".png" else None,
                               bbox_inches="tight")
                artifacts.append(path)
        finally:
            plt.close(figure)
    return artifacts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-module", type=Path, required=True)
    args = parser.parse_args(argv)
    for path in render(args.batch, args.output, args.base_module):
        print(path)


if __name__ == "__main__":
    main()
