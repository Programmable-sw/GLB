#!/usr/bin/env python3
"""Compare hard Top-4 and weighted NetAware oscillation on A2A 256 MiB P=16."""

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


FLOW_RE = re.compile(
    r"^Flow (Roce_\d+_\d+) (\d+) finished at ([0-9.]+) total bytes (\d+)$",
    re.MULTILINE,
)


def parse_chained_flow_performance(path, expected_flows):
    text = path.read_text(errors="replace")
    starts = {
        name: float(start)
        for name, start in re.findall(r"^startflow (Roce_\d+_\d+) at ([0-9.]+)$", text, re.MULTILINE)
    }
    completions = list(FLOW_RE.finditer(text))
    if len(starts) != expected_flows or len(completions) != expected_flows:
        raise ValueError(
            f"Expected {expected_flows} flows, found {len(starts)} starts and "
            f"{len(completions)} completions in {path}"
        )
    names = [match.group(1) for match in completions]
    if len(set(names)) != expected_flows or any(name not in starts for name in names):
        raise ValueError(f"Flow names are not a one-to-one start/completion mapping in {path}")
    values = np.asarray([
        float(match.group(3)) - starts[match.group(1)] for match in completions
    ])
    return {
        "completed_flows": len(values),
        "mean_fct_us": float(values.mean()),
        "p50_fct_us": float(np.percentile(values, 50)),
        "p99_fct_us": float(np.percentile(values, 99)),
        "max_fct_us": float(values.max()),
    }


def load_pair(path, src_leaf, dst_leaf, start_us, end_us):
    rows = defaultdict(dict)
    with gzip.open(path, "rt", newline="") as stream:
        for row in csv.DictReader(stream):
            t = float(row["time_us"])
            if t < start_us or t > end_us:
                continue
            if int(row["src_leaf"]) == src_leaf and int(row["dst_leaf"]) == dst_leaf:
                rows[t][int(row["path_id"])] = row
    return {t: paths for t, paths in rows.items() if len(paths) == 8}


def queue_fraction(row):
    local = float(row["q_leaf_to_spine"]) / max(float(row["q_leaf_to_spine_max"]), 1)
    remote = float(row["q_spine_to_dst_leaf"]) / max(float(row["q_spine_to_dst_leaf_max"]), 1)
    return max(local, remote)


def build_cycles(rows, grain_us=10):
    # Both traces are evaluated on the same 10-us grid. This intentionally drops
    # the extra 5-us observations present only in the hard Top-4 trace.
    times = sorted(t for t in rows if abs(t / grain_us - round(t / grain_us)) < 1e-9)
    cycles = []
    for left, right in zip(times, times[1:]):
        if abs(right - left - grain_us) > 1e-9:
            continue
        start, end = rows[left], rows[right]
        delta = np.array([
            max(0, int(end[ev]["bytes_sent_on_path"]) - int(start[ev]["bytes_sent_on_path"]))
            for ev in range(8)
        ], dtype=float)
        if delta.sum() == 0:
            continue
        score = np.array([float(start[ev]["path_score"]) for ev in range(8)])
        recommended = set(np.argsort(score, kind="stable")[:4].tolist())
        q0 = np.array([queue_fraction(start[ev]) for ev in range(8)])
        q1 = np.array([queue_fraction(end[ev]) for ev in range(8)])
        share = delta / delta.sum()
        cycles.append({"left": left, "right": right, "share": share, "q0": q0,
                       "q1": q1, "score": score, "recommended": recommended})
    return cycles


def series(cycles):
    shares = np.stack([c["share"] for c in cycles])
    q = np.stack([c["q1"] for c in cycles])
    qcv = q.std(axis=1) / (q.mean(axis=1) + 1e-12)
    migration = np.r_[np.nan, 0.5 * np.abs(np.diff(shares, axis=0)).sum(axis=1)]
    concentration = np.sort(shares, axis=1)[:, -4:].sum(axis=1)
    rec_share = np.array([sum(c["share"][ev] for ev in c["recommended"]) for c in cycles])
    rec_q_gap = np.array([
        np.mean([c["q1"][ev] - c["q0"][ev] for ev in c["recommended"]])
        - np.mean([c["q1"][ev] - c["q0"][ev] for ev in range(8)
                   if ev not in c["recommended"]])
        for c in cycles
    ])
    eviction = np.r_[np.nan, [
        len(cycles[i - 1]["recommended"] - cycles[i]["recommended"]) / 4
        for i in range(1, len(cycles))
    ]]
    return {"shares": shares, "q": q, "qcv": qcv, "migration": migration,
            "concentration": concentration, "rec_share": rec_share,
            "rec_q_gap": rec_q_gap, "best4_eviction_fraction": eviction}


def select_window(top, weighted, count=16):
    n = min(len(top["migration"]), len(weighted["migration"]))
    count = min(count, n)
    best_start, best_value = 1, -np.inf
    for start in range(1, n - count + 1):
        stop = start + count
        # Favor windows where hard selection visibly moves more traffic and has
        # greater queue imbalance than the weighted selector.
        value = (np.nanmean(top["migration"][start:stop])
                 - np.nanmean(weighted["migration"][start:stop])
                 + 0.25 * (np.mean(top["qcv"][start:stop])
                           - np.mean(weighted["qcv"][start:stop])))
        if value > best_value:
            best_start, best_value = start, value
    return slice(best_start, best_start + count)


def configure_fonts():
    path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    font_manager.fontManager.addfont(path)
    family = font_manager.FontProperties(fname=path).get_name()
    plt.rcParams.update({"font.family": family, "axes.unicode_minus": False, "font.size": 10})


def summarize(data, window):
    def mean(key):
        return float(np.nanmean(data[key][window]))
    return {"mean_adjacent_traffic_migration": mean("migration"),
            "mean_queue_cv": mean("qcv"),
            "mean_hottest4_concentration": mean("concentration"),
            "mean_current_best4_traffic_share": mean("rec_share"),
            "mean_recommended_minus_other_queue_growth": mean("rec_q_gap"),
            "mean_best4_eviction_fraction": mean("best4_eviction_fraction")}


def plot(
    top_cycles,
    weighted_cycles,
    output_dir,
    src_leaf,
    dst_leaf,
    performance=None,
    grain_us=10,
    hard_name="硬 Top-4",
    comparator_name="4/2/1/0 加权",
    title="非对称 All-to-All：Hard Top-4 振荡及性能影响",
    scenario_note="256 MiB，P=16｜128 节点｜3% ToR 上行降半｜周期背景流｜seed 13",
    stem="a2a256_p16_topk4_vs_weighted_micro_cn",
):
    configure_fonts()
    top, weighted = series(top_cycles), series(weighted_cycles)
    window = select_window(top, weighted)
    tcycles = top_cycles[window]
    wcycles = weighted_cycles[window]
    topw = {k: v[window] for k, v in top.items()}
    weightedw = {k: v[window] for k, v in weighted.items()}
    times = np.array([c["left"] for c in tcycles])
    labels = [f"{t:.0f}" for t in times]
    x = np.arange(len(times))

    fig = plt.figure(figsize=(15.2, 11.6), layout="constrained")
    fig.get_layout_engine().set(rect=(0.025, 0.02, 0.975, 0.925), w_pad=0.08, h_pad=0.10)
    grid = fig.add_gridspec(4, 2, height_ratios=[1.18, 1.18, 0.92, 1.0])
    axes_share = [fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1])]
    axes_q = [fig.add_subplot(grid[1, 0]), fig.add_subplot(grid[1, 1])]
    names = [hard_name, comparator_name]
    colors = ["#d97706", "#2563a6"]
    for col, (name, data, ax_share, ax_q) in enumerate(zip(
            names, [topw, weightedw], axes_share, axes_q)):
        im = ax_share.imshow(data["shares"].T * 100, aspect="auto", interpolation="nearest",
                             cmap="YlOrRd", vmin=0, vmax=max(25, np.percentile(topw["shares"] * 100, 98)))
        ax_share.set_title(f"{name} · 每周期各 EV 的发送字节占比", loc="left", pad=7)
        ax_share.set_yticks(range(8), [f"EV{i}" for i in range(8)])
        ax_share.tick_params(axis="x", labelbottom=False)
        if col == 1:
            cb = fig.colorbar(im, ax=ax_share, pad=0.012, fraction=0.027)
            cb.set_label("周期流量占比（%）")
        iq = ax_q.imshow(data["q"].T, aspect="auto", interpolation="nearest",
                         cmap="OrRd", vmin=0, vmax=1)
        ax_q.set_title(f"{name} · 周期末各 EV 队列占用", loc="left", pad=7)
        ax_q.set_yticks(range(8), [f"EV{i}" for i in range(8)])
        ax_q.set_xticks(x, labels, rotation=45, ha="right")
        ax_q.set_xlabel(f"周期起点（μs；统一为 {grain_us:g} μs）")
        if col == 1:
            cb = fig.colorbar(iq, ax=ax_q, pad=0.012, fraction=0.027)
            cb.set_label("队列 / 容量")

    ax = fig.add_subplot(grid[2, :])
    ax.plot(x, topw["migration"] * 100, color=colors[0], marker="o", linewidth=2,
            label=f"{hard_name}：相邻周期迁移量")
    ax.plot(x, weightedw["migration"] * 100, color=colors[1], marker="s", linewidth=2,
            label=f"{comparator_name}：相邻周期迁移量")
    ax.set_ylabel("迁移流量份额（%）")
    ax.set_xticks(x, labels)
    ax.set_xlabel("周期起点（μs）")
    ax.set_title("相邻周期的流量分布改变量（总变差距离，越高表示路径间搬移越剧烈）", loc="left")
    ax.grid(linestyle=":", alpha=0.4)
    ax.legend(frameon=False, ncol=2)

    ax = fig.add_subplot(grid[3, 0])
    specs = [("migration", "流量迁移"), ("qcv", "队列 CV"),
             ("concentration", "最热四路集中度")]
    ratios = [np.nanmean(topw[key]) / np.nanmean(weightedw[key]) for key, _ in specs]
    bars = ax.bar(np.arange(3), ratios, color=colors[0], width=0.55)
    ax.axhline(
        1,
        color=colors[1],
        linestyle="--",
        linewidth=1.5,
        label=f"{comparator_name} = 1.0",
    )
    for bar, value in zip(bars, ratios):
        ax.text(bar.get_x() + bar.get_width()/2, value + 0.025, f"{value:.2f}×", ha="center")
    ax.set_xticks(np.arange(3), [label for _, label in specs])
    ax.set_ylabel(f"{hard_name} / {comparator_name}")
    ax.set_ylim(0, max(ratios) * 1.22)
    ax.set_title(f"微观窗口：{hard_name} 对振荡指标的放大", loc="left", pad=8)
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    ax.legend(frameon=False)

    perf_ratios = None
    if performance:
        ax = fig.add_subplot(grid[3, 1])
        specs = [("mean_fct_us", "Mean"), ("p50_fct_us", "p50"),
                 ("p99_fct_us", "p99"), ("max_fct_us", "Max")]
        positions = np.arange(len(specs)); width = 0.34
        hard_values = np.array([performance["hard_top4"][key] / 1000 for key, _ in specs])
        weighted_values = np.array([performance["weighted_4210"][key] / 1000 for key, _ in specs])
        for offset, values, color, label in [
                (-width / 2, hard_values, colors[0], hard_name),
                (width / 2, weighted_values, colors[1], comparator_name)]:
            bars = ax.bar(positions + offset, values, width, color=color, label=label)
            for bar, value in zip(bars, values):
                ax.text(bar.get_x() + bar.get_width()/2, value - 0.055,
                        f"{value:.2f}", ha="center", va="top", fontsize=8,
                        color="white", fontweight="semibold")
        perf_ratios = hard_values / weighted_values
        ax.set_xticks(positions, [label for _, label in specs])
        ax.set_ylabel("完成时间（ms）")
        ax.set_ylim(0, max(hard_values.max(), weighted_values.max()) * 1.18)
        ax.set_title("同流量配对实验：完成时间", loc="left", pad=8)
        ax.grid(axis="y", linestyle=":", alpha=0.4)
        ax.legend(frameon=False, ncol=2, fontsize=9)
        for pos, ratio in zip(positions, perf_ratios):
            ax.text(pos, max(hard_values[pos], weighted_values[pos]) + 0.14,
                    f"{ratio:.3f}×", ha="center", fontsize=8, color="#7c2d12")

    start, end = tcycles[0]["left"], tcycles[-1]["right"]
    fig.suptitle(title, fontsize=18, fontweight="semibold", y=0.985)
    fig.text(0.5, 0.953,
             f"{scenario_note}｜"
             f"微观窗口 ToR {src_leaf}→{dst_leaf}，{start:.0f}–{end:.0f} μs"
             f"（统一 {grain_us:g} μs 采样）",
             ha="center", color="#475569", fontsize=10.5)
    output_dir.mkdir(parents=True, exist_ok=True)
    png = output_dir / f"{stem}.png"
    pdf = output_dir / f"{stem}.pdf"
    fig.savefig(png, dpi=220, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    metrics = {
        "scenario": "asymmetric_alltoall_background_256mib_p16", "seed": 13,
        "src_leaf": src_leaf, "dst_leaf": dst_leaf, "common_grain_us": 10,
        "window_start_us": start, "window_end_us": end,
        "hard_top4": summarize(top, window), "weighted_4210": summarize(weighted, window),
        "full_scan_900_2500_us": {
            "hard_top4": summarize(top, slice(None)),
            "weighted_4210": summarize(weighted, slice(None)),
        },
        "hard_over_weighted": {
            "adjacent_traffic_migration": ratios[0], "queue_cv": ratios[1],
            "hottest4_concentration": ratios[2]},
        "figure_png": str(png), "figure_pdf": str(pdf),
    }
    if performance:
        metrics["performance"] = performance
        metrics["performance_hard_over_weighted"] = {
            label: float(value) for (_, label), value in zip(specs, perf_ratios)
        }
    metrics["hard_selector_label"] = hard_name
    metrics["comparator_label"] = comparator_name
    metrics["common_grain_us"] = grain_us
    with (output_dir / f"{stem}_metrics.json").open("w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    return metrics


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--topk-trace", type=Path, required=True)
    p.add_argument("--weighted-trace", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--src-leaf", type=int, default=7)
    p.add_argument("--dst-leaf", type=int, default=12)
    p.add_argument("--scan-start-us", type=float, default=900)
    p.add_argument("--scan-end-us", type=float, default=2500)
    p.add_argument("--traffic", type=Path)
    p.add_argument("--topk-log", type=Path)
    p.add_argument("--weighted-log", type=Path)
    p.add_argument("--grain-us", type=float, default=10)
    p.add_argument("--hard-name", default="硬 Top-4")
    p.add_argument("--comparator-name", default="4/2/1/0 加权")
    p.add_argument("--title", default="非对称 All-to-All：Hard Top-4 振荡及性能影响")
    p.add_argument(
        "--scenario-note",
        default="256 MiB，P=16｜128 节点｜3% ToR 上行降半｜周期背景流｜seed 13",
    )
    p.add_argument("--stem", default="a2a256_p16_topk4_vs_weighted_micro_cn")
    args = p.parse_args()
    top = build_cycles(
        load_pair(
            args.topk_trace,
            args.src_leaf,
            args.dst_leaf,
            args.scan_start_us,
            args.scan_end_us,
        ),
        args.grain_us,
    )
    weighted = build_cycles(
        load_pair(
            args.weighted_trace,
            args.src_leaf,
            args.dst_leaf,
            args.scan_start_us,
            args.scan_end_us,
        ),
        args.grain_us,
    )
    if not top or not weighted:
        raise SystemExit("No complete common-grid cycles found")
    performance = None
    supplied = [args.traffic, args.topk_log, args.weighted_log]
    if any(supplied) and not all(supplied):
        raise SystemExit("--traffic, --topk-log and --weighted-log must be supplied together")
    if all(supplied):
        match = re.search(r"^Connections (\d+)$", args.traffic.read_text(), re.MULTILINE)
        if not match:
            raise SystemExit(f"Missing Connections header in {args.traffic}")
        expected_flows = int(match.group(1))
        performance = {
            "hard_top4": parse_chained_flow_performance(args.topk_log, expected_flows),
            "weighted_4210": parse_chained_flow_performance(args.weighted_log, expected_flows),
        }
    print(json.dumps(plot(
        top,
        weighted,
        args.output_dir,
        args.src_leaf,
        args.dst_leaf,
        performance,
        args.grain_us,
        args.hard_name,
        args.comparator_name,
        args.title,
        args.scenario_note,
        args.stem,
    ),
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
