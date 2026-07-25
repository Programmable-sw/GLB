#!/usr/bin/env python3

import argparse
import csv
import math
from pathlib import Path


SCHEME_LABELS = {
    "avail": "avail",
    "grade": "grade",
    "netaware": "netaware",
    "n-mrc": "n-mrc",
}


def population_cv(values):
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) / mean


def is_complete_window(row, window_selections):
    return int(row["window_selected"]) == window_selections


def transform_trace(scheme, path):
    with Path(path).open(encoding="utf-8") as handle:
        source_rows = list(csv.DictReader(handle))
    if not source_rows:
        raise ValueError(f"empty path-selection trace: {path}")
    path_fields = sorted(
        (name for name in source_rows[0] if name.startswith("path_")),
        key=lambda name: int(name.split("_", 1)[1]),
    )
    if not path_fields:
        raise ValueError(f"trace has no path columns: {path}")

    transformed = []
    previous = [0] * len(path_fields)
    previous_total = 0
    for row in source_rows:
        counts = [int(row[name]) for name in path_fields]
        total = int(row["selected_total"])
        if total <= previous_total:
            raise ValueError(f"non-increasing selected_total in {path}: {total}")
        if sum(counts) != total:
            raise ValueError(
                f"path counts do not match selected_total in {path}: "
                f"{sum(counts)} != {total}"
            )
        increments = [value - old for value, old in zip(counts, previous)]
        if min(increments) < 0:
            raise ValueError(f"path counter decreased in {path}")
        transformed_row = {
            "scheme": scheme,
            "time_us": float(row["time_us"]),
            "selected_total": total,
            "window_selected": total - previous_total,
            "cumulative_cv": population_cv(counts),
            "window_cv": population_cv(increments),
        }
        for name, count in zip(path_fields, counts):
            transformed_row[name] = count
        transformed.append(transformed_row)
        previous = counts
        previous_total = total
    return transformed


def write_rows(rows, path):
    path_fields = sorted(
        {name for row in rows for name in row if name.startswith("path_")},
        key=lambda name: int(name.split("_", 1)[1]),
    )
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "scheme", "time_us", "selected_total",
                "window_selected", "cumulative_cv", "window_cv",
            ) + tuple(path_fields),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def plot_rows(rows, path, window_selections):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {
        "avail": "#2463A6",
        "grade": "#D28B17",
        "netaware": "#8B4A8F",
        "n-mrc": "#4F6B3A",
    }
    styles = {
        "avail": "-",
        "grade": "--",
        "netaware": "-.",
        "n-mrc": ":",
    }
    figure, axes = plt.subplots(2, 1, figsize=(9.2, 7.0), sharex=True)
    for scheme in SCHEME_LABELS:
        selected = [row for row in rows if row["scheme"] == scheme]
        selected.sort(key=lambda row: row["time_us"])
        complete_windows = [
            row for row in selected
            if is_complete_window(row, window_selections)
        ]
        x = [row["time_us"] for row in complete_windows]
        axes[0].plot(
            x,
            [row["cumulative_cv"] for row in complete_windows],
            label=SCHEME_LABELS[scheme],
            color=colors[scheme],
            linestyle=styles[scheme],
            linewidth=1.8,
        )
        axes[1].plot(
            x,
            [row["window_cv"] for row in complete_windows],
            label=SCHEME_LABELS[scheme],
            color=colors[scheme],
            linestyle=styles[scheme],
            linewidth=1.2,
            alpha=0.9,
        )

    axes[0].set_title("Cumulative physical-path selection CV")
    axes[0].set_ylabel("Cumulative CV")
    axes[1].set_title("Per-sampling-window physical-path selection CV")
    axes[1].set_ylabel("Window CV")
    axes[1].set_xlabel("Simulation time (μs)")
    for axis in axes:
        axis.grid(axis="y", color="#D6D9DD", linewidth=0.7)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
    axes[0].legend(ncol=4, frameon=False, loc="upper right")
    figure.suptitle(
        "P16 full-global load, background off, seed 13; "
        f"{window_selections:,} selections per complete window",
        fontsize=11,
    )
    figure.tight_layout()
    figure.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(figure)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--trace",
        action="append",
        required=True,
        metavar="SCHEME=CSV",
    )
    parser.add_argument("--csv", required=True)
    parser.add_argument("--png", required=True)
    parser.add_argument("--window-selections", type=int, default=100000)
    return parser.parse_args()


def main():
    args = parse_args()
    rows = []
    seen = set()
    for item in args.trace:
        scheme, separator, path = item.partition("=")
        if not separator or scheme not in SCHEME_LABELS:
            raise ValueError(f"expected SCHEME=CSV, got {item!r}")
        if scheme in seen:
            raise ValueError(f"duplicate scheme: {scheme}")
        seen.add(scheme)
        rows.extend(transform_trace(scheme, path))
    missing = set(SCHEME_LABELS) - seen
    if missing:
        raise ValueError(f"missing schemes: {sorted(missing)}")
    write_rows(rows, args.csv)
    plot_rows(rows, args.png, args.window_selections)


if __name__ == "__main__":
    main()
