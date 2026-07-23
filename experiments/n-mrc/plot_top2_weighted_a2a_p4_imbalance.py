#!/usr/bin/env python3
"""Plot path-imbalance accumulation for hard Top-2 vs weighted NetAware."""

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


START_RE = re.compile(r"^startflow (Roce_\d+_\d+) at ([0-9.]+)$", re.MULTILINE)
FINISH_RE = re.compile(
    r"^Flow (Roce_\d+_\d+) \d+ finished at ([0-9.]+) total bytes \d+$",
    re.MULTILINE,
)


def completion_metrics(path, expected=16256):
    text = path.read_text(errors="replace")
    starts = {name: float(t) for name, t in START_RE.findall(text)}
    finished = FINISH_RE.findall(text)
    if len(starts) != expected or len(finished) != expected:
        raise ValueError(
            f"{path}: expected {expected}, got {len(starts)} starts/{len(finished)} finishes"
        )
    finish_times = np.asarray([float(t) for _, t in finished])
    fct = np.asarray(
        [finish - starts[name] for (name, _), finish in zip(finished, finish_times)]
    )
    return {
        "completed_flows": len(fct),
        "cct_us": float(finish_times.max()),
        "mean_fct_us": float(fct.mean()),
        "p50_fct_us": float(np.percentile(fct, 50)),
        "p99_fct_us": float(np.percentile(fct, 99)),
        "max_fct_us": float(fct.max()),
    }


def trace_series(path, cycle_us):
    totals = defaultdict(lambda: np.zeros(8, dtype=np.float64))
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="") as stream:
        for row in csv.DictReader(stream):
            totals[float(row["time_us"])][int(row["path_id"])] += int(
                row["bytes_sent_on_path"]
            )
    times = np.asarray(sorted(totals))
    cumulative = np.stack([totals[t] for t in times])
    cumulative_cv = cumulative.std(axis=1) / np.maximum(cumulative.mean(axis=1), 1)
    by_time = {t: values for t, values in zip(times, cumulative)}
    cycle_cv = np.full(len(times), np.nan)
    for i, t in enumerate(times):
        previous = t - cycle_us
        if previous in by_time:
            delta = np.maximum(0, cumulative[i] - by_time[previous])
            if delta.sum():
                cycle_cv[i] = delta.std() / delta.mean()
    return times, cumulative_cv, cycle_cv, cumulative.sum(axis=1)


def configure_fonts():
    path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    font_manager.fontManager.addfont(path)
    family = font_manager.FontProperties(fname=path).get_name()
    plt.rcParams.update(
        {"font.family": family, "axes.unicode_minus": False, "font.size": 10}
    )


def shade_background(ax, end_us, on_us, off_us):
    period = on_us + off_us
    start = 0
    first = True
    while start <= end_us:
        ax.axvspan(
            start / 1000,
            min(start + on_us, end_us) / 1000,
            color="#cbd5e1",
            alpha=0.62,
            linewidth=0,
            label="周期背景流 ON" if first else None,
        )
        first = False
        start += period


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    configure_fonts()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    sizes = [64, 256, 1024]
    bg = {64: 200, 256: 400, 1024: 1000}
    schemes = ["hard_top2", "weighted_4210"]
    labels = {"hard_top2": "硬 Top-2", "weighted_4210": "4/2/1/0 加权"}
    colors = {"hard_top2": "#d97706", "weighted_4210": "#2563a6"}
    styles = {"hard_top2": "-", "weighted_4210": "--"}
    all_data = {}
    csv_rows = []

    for size in sizes:
        all_data[size] = {}
        for scheme in schemes:
            directory = args.base_dir / f"a2a_{size}mib_p4" / scheme
            performance = completion_metrics(directory / "stdout.log")
            trace = directory / "trace_path_state.csv"
            if not trace.exists():
                trace = trace.with_suffix(".csv.gz")
            series = trace_series(trace, 2 * bg[size])
            all_data[size][scheme] = {"performance": performance, "series": series}
            times, cumulative_cv, cycle_cv, cumulative_bytes = series
            for t, ccv, wcv, total in zip(times, cumulative_cv, cycle_cv, cumulative_bytes):
                csv_rows.append(
                    {
                        "size_mib": size,
                        "scheme": scheme,
                        "time_us": t,
                        "cumulative_path_share_cv": ccv,
                        "one_bg_cycle_path_share_cv": wcv,
                        "cumulative_bytes": int(total),
                    }
                )

    fig, axes = plt.subplots(2, 3, figsize=(15.4, 7.8), layout="constrained")
    fig.get_layout_engine().set(rect=(0.025, 0.05, 0.985, 0.89), w_pad=0.08, h_pad=0.08)
    for col, size in enumerate(sizes):
        max_end = max(
            all_data[size][scheme]["performance"]["cct_us"] for scheme in schemes
        )
        for row in range(2):
            shade_background(axes[row, col], max_end, bg[size], bg[size])
        for scheme in schemes:
            times, cumulative_cv, cycle_cv, _ = all_data[size][scheme]["series"]
            end = all_data[size][scheme]["performance"]["cct_us"]
            mask = times <= end
            axes[0, col].plot(
                times[mask] / 1000,
                cumulative_cv[mask],
                color=colors[scheme],
                linestyle=styles[scheme],
                linewidth=2.1,
                label=labels[scheme],
            )
            axes[1, col].plot(
                times[mask] / 1000,
                cycle_cv[mask],
                color=colors[scheme],
                linestyle=styles[scheme],
                linewidth=2.1,
                label=labels[scheme],
            )
        top_cct = all_data[size]["hard_top2"]["performance"]["cct_us"]
        weighted_cct = all_data[size]["weighted_4210"]["performance"]["cct_us"]
        ratio = top_cct / weighted_cct
        axes[0, col].set_title(
            f"{size} MiB · BG {bg[size]}/{bg[size]} μs\n"
            f"CCT：Top-2 {top_cct/1000:.2f} ms / 加权 {weighted_cct/1000:.2f} ms（{ratio:.3f}×）",
            fontsize=12,
            pad=8,
        )
        axes[1, col].set_xlabel("时间（ms）")
        for row in range(2):
            axes[row, col].grid(linestyle=":", alpha=0.4)
            axes[row, col].set_xlim(left=0)

    axes[0, 0].set_ylabel("累计路径字节份额 CV")
    axes[1, 0].set_ylabel("一个背景周期内的路径字节份额 CV")
    handles, legend_labels = axes[1, 2].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="lower center", ncol=3, frameon=False)
    fig.suptitle(
        "路径不均衡随时间积累：Hard Top-2 与加权选择",
        fontsize=18,
        fontweight="semibold",
        y=0.98,
    )
    fig.text(
        0.5,
        0.935,
        "非对称 All-to-All · P=4 · 50 μs 采样 · 周期背景流 · seed 13 · 越低越好",
        ha="center",
        color="#475569",
        fontsize=10.5,
    )

    png = args.output_dir / "top2_vs_weighted_path_imbalance_timeline.png"
    pdf = args.output_dir / "top2_vs_weighted_path_imbalance_timeline.pdf"
    fig.savefig(png, dpi=220, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")

    with (args.output_dir / "top2_vs_weighted_path_imbalance_timeline.csv").open(
        "w", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=csv_rows[0].keys())
        writer.writeheader()
        writer.writerows(csv_rows)
    metrics = {
        str(size): {
            scheme: all_data[size][scheme]["performance"] for scheme in schemes
        }
        for size in sizes
    }
    for size in sizes:
        h = metrics[str(size)]["hard_top2"]
        w = metrics[str(size)]["weighted_4210"]
        metrics[str(size)]["hard_over_weighted"] = {
            key: h[key] / w[key]
            for key in (
                "cct_us",
                "mean_fct_us",
                "p50_fct_us",
                "p99_fct_us",
                "max_fct_us",
            )
        }
    with (args.output_dir / "top2_vs_weighted_path_imbalance_metrics.json").open(
        "w"
    ) as stream:
        json.dump(metrics, stream, indent=2, ensure_ascii=False)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
