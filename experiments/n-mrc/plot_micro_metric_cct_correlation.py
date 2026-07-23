#!/usr/bin/env python3
"""Relate path-balance micro metrics to normalized All-to-All CCT."""

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


SIZES = (64, 256, 1024)
SCHEMES = ("mrc", "sglb", "n-mrc")
LABELS = {"mrc": "MRC", "sglb": "SGLB", "n-mrc": "N-MRC"}
COLORS = {64: "#D95F02", 256: "#7570B3", 1024: "#1B9E77"}
MARKERS = {"mrc": "D", "sglb": "^", "n-mrc": "P"}


def pearson(xs, ys):
    xbar, ybar = sum(xs) / len(xs), sum(ys) / len(ys)
    numerator = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - xbar) ** 2 for x in xs) *
        sum((y - ybar) ** 2 for y in ys)
    )
    return numerator / denominator


def ranks(values):
    output = [0.0] * len(values)
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    for rank, index in enumerate(ordered):
        output[index] = float(rank)
    return output


def load_rows(batch, micro_dir):
    timeline = defaultdict(list)
    with (micro_dir / "alltoall_size_sweep_p4_timeline.csv").open(
            newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timeline[(int(row["size_mib"]), row["scheme"])].append(row)

    share_cv = {}
    with (micro_dir / "alltoall_size_sweep_p4_path_balance.csv").open(
            newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            share_cv.setdefault(
                (int(row["size_mib"]), row["scheme"]),
                float(row["share_cv"]),
            )

    rows = []
    for size in SIZES:
        scenario = f"asymmetric_alltoall_background_{size}mib_p4"
        for scheme in SCHEMES:
            samples = timeline[size, scheme]
            byte_deltas = [float(row["bytes_delta"]) for row in samples]
            utilization_cv = [
                float(row["utilization_cv_display"]) for row in samples
            ]
            byte_total = sum(byte_deltas)
            weighted_cv = sum(
                value * weight
                for value, weight in zip(utilization_cv, byte_deltas)
            ) / byte_total
            summary_path = (
                batch / "raw" / scenario / scheme / "seed_13" / "summary.json"
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            rows.append({
                "size_mib": size,
                "scheme": scheme,
                "cct_us": float(summary["primary_us"]),
                "path_share_cv": share_cv[size, scheme],
                "byte_weighted_utilization_cv": weighted_cv,
            })
    for size in SIZES:
        best = min(row["cct_us"] for row in rows if row["size_mib"] == size)
        for row in rows:
            if row["size_mib"] == size:
                row["cct_slowdown"] = row["cct_us"] / best
    return rows


def correlation(rows, field):
    xs = [row[field] for row in rows]
    ys = [row["cct_slowdown"] for row in rows]
    return pearson(xs, ys), pearson(ranks(xs), ranks(ys))


def regression(xs, ys):
    xbar, ybar = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
    slope /= sum((x - xbar) ** 2 for x in xs)
    return slope, ybar - slope * xbar


def style_axis(axis):
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)
    axis.grid(True, color="#C4C6C8", linestyle=":", linewidth=1.0)
    axis.tick_params(labelsize=12)
    axis.set_axisbelow(True)


def build_figure(rows):
    figure, axes = plt.subplots(1, 2, figsize=(16, 6.8), sharey=True)
    panels = (
        ("path_share_cv", "Cumulative path byte-share CV",
         "Useful summary: lower imbalance generally tracks lower CCT"),
        ("byte_weighted_utilization_cv", "Byte-weighted instantaneous utilization CV",
         "Weak summary: smooth instantaneous load does not predict CCT"),
    )
    for axis, (field, xlabel, title) in zip(axes, panels):
        xs = [row[field] for row in rows]
        ys = [row["cct_slowdown"] for row in rows]
        slope, intercept = regression(xs, ys)
        line_x = [min(xs), max(xs)]
        axis.plot(
            line_x, [slope * x + intercept for x in line_x],
            color="#555555", linestyle="--", linewidth=1.6, zorder=1,
        )
        for row in rows:
            axis.scatter(
                row[field], row["cct_slowdown"], s=135,
                color=COLORS[row["size_mib"]],
                marker=MARKERS[row["scheme"]],
                edgecolor="white", linewidth=0.8, zorder=3,
            )
        r, rho = correlation(rows, field)
        axis.text(
            0.035, 0.96, f"Pearson r = {r:.2f}\nSpearman ρ = {rho:.2f}",
            transform=axis.transAxes, va="top", fontsize=12,
            bbox={"facecolor": "white", "edgecolor": "#BBBBBB", "alpha": 0.9},
        )
        axis.set_title(title, fontsize=15, pad=12)
        axis.set_xlabel(xlabel + " (lower is better)", fontsize=13)
        style_axis(axis)
    axes[0].set_ylabel(
        "CCT slowdown vs best scheme at same size (lower is better)",
        fontsize=13,
    )
    size_handles = [
        Line2D([], [], marker="o", linestyle="", markersize=10,
               color=COLORS[size], label=f"{size} MiB")
        for size in SIZES
    ]
    scheme_handles = [
        Line2D([], [], marker=MARKERS[scheme], linestyle="", markersize=10,
               markerfacecolor="#777777", markeredgecolor="white",
               label=LABELS[scheme])
        for scheme in SCHEMES
    ]
    figure.legend(
        handles=size_handles + scheme_handles, loc="lower center",
        ncol=6, frameon=False, fontsize=12, bbox_to_anchor=(0.5, 0.005),
    )
    figure.suptitle(
        "Which micro metric tracks completion performance?",
        fontsize=20, y=0.985,
    )
    figure.text(
        0.5, 0.935,
        "Asymmetric All-to-All + periodic BG · P=4 · seed 13 · "
        "each scheme truncated at its own CCT · BG included",
        ha="center", fontsize=12.5, color="#404040",
    )
    figure.subplots_adjust(
        left=0.08, right=0.985, bottom=0.16, top=0.84, wspace=0.18,
    )
    return figure


def write_rows(path, rows):
    fields = (
        "size_mib", "scheme", "cct_us", "cct_slowdown",
        "path_share_cv", "byte_weighted_utilization_cv",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--micro-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_rows(args.batch, args.micro_dir)
    csv_path = args.output_dir / "micro_metric_cct_correlation.csv"
    write_rows(csv_path, rows)
    figure = build_figure(rows)
    try:
        for suffix in (".png", ".pdf"):
            path = args.output_dir / ("micro_metric_cct_correlation" + suffix)
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
