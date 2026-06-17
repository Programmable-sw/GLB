#!/usr/bin/env python3
# 比较默认 2048 节点 2 层单 ToR 对中 1000 个单包流的哈希输入熵。
"""Offline demo for comparing hash input entropy on one 2-tier ToR pair.

The script intentionally stays outside the htsim simulator path.  It models the
default generated 2048-node/2-tier topology only far enough to know how many
spine paths exist between two ToRs, then hashes 1000 one-packet flows onto those
paths and plots the per-path packet-count CDF.
"""

import argparse
import csv
import random
import statistics
import zlib
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


DEFAULT_OUT = Path(__file__).resolve().parent / "output_hash_entropy_demo"
HASH_SEED = 0x9E3779B9
UDP_PROTOCOL = 17
ROCE_UDP_PORT = 4791


@dataclass(frozen=True)
class TwoTierTopology:
    nodes: int
    k: int
    tor_count: int
    spine_count: int
    hosts_per_tor: int


def generated_2tier_topology(nodes):
    generated = 0
    k = 0
    while generated < nodes:
        k += 1
        generated = k * k // 2
    if generated != nodes:
        raise ValueError(
            f"{nodes} nodes is not supported by htsim's generated 2-tier fat-tree "
            f"(next generated size is {generated}, K={k})"
        )
    return TwoTierTopology(
        nodes=nodes,
        k=k,
        tor_count=k,
        spine_count=k // 2,
        hosts_per_tor=k // 2,
    )


def ipv4_for_host(host_id):
    # Stable private IPv4 encoding for the synthetic five-tuple.
    return (
        10 << 24
        | ((host_id >> 8) & 0xFF) << 16
        | (host_id & 0xFF) << 8
        | 1
    )


def u16(value):
    return value.to_bytes(2, "big")


def u32(value):
    return value.to_bytes(4, "big")


def five_tuple_104b(src_ip, dst_ip, src_port, dst_port, protocol):
    return (
        u32(src_ip)
        + u32(dst_ip)
        + u16(src_port)
        + u16(dst_port)
        + protocol.to_bytes(1, "big")
    )


def seeded_crc32(payload, seed):
    return zlib.crc32(payload, seed) & 0xFFFFFFFF


def path_for_payload(payload, path_count, hash_seed):
    return seeded_crc32(payload, hash_seed) % path_count


def count_paths(payloads, path_count, hash_seed):
    counts = [0] * path_count
    for payload in payloads:
        counts[path_for_payload(payload, path_count, hash_seed)] += 1
    return counts


def cdf_xy(counts):
    xs = sorted(counts)
    n = len(xs)
    ys = [(i + 1) / n for i in range(n)]
    return xs, ys


def summarize(label, counts):
    mean = statistics.fmean(counts)
    stdev = statistics.pstdev(counts)
    return {
        "mode": label,
        "min": min(counts),
        "max": max(counts),
        "mean": mean,
        "pstdev": stdev,
        "cv": stdev / mean if mean else 0.0,
    }


def mean_rank_counts(trial_results):
    averaged = {}
    for label in trial_results[0]:
        sorted_trials = [sorted(result[label]) for result in trial_results]
        averaged[label] = [
            statistics.fmean(rank_values) for rank_values in zip(*sorted_trials)
        ]
    return averaged


def average_summary_rows(trial_summaries):
    rows = []
    labels = []
    for row in trial_summaries:
        if row["mode"] not in labels:
            labels.append(row["mode"])

    for label in labels:
        label_rows = [row for row in trial_summaries if row["mode"] == label]
        rows.append(
            {
                "trial": "mean",
                "seed": "",
                "mode": label,
                "min": statistics.fmean(row["min"] for row in label_rows),
                "max": statistics.fmean(row["max"] for row in label_rows),
                "mean": statistics.fmean(row["mean"] for row in label_rows),
                "pstdev": statistics.fmean(row["pstdev"] for row in label_rows),
                "cv": statistics.fmean(row["cv"] for row in label_rows),
            }
        )
    return rows


def write_counts_csv(path, trial_results, seeds):
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["trial", "seed", "mode", "path_id", "packet_count"])
        for trial, (seed, results) in enumerate(zip(seeds, trial_results)):
            for label, counts in results.items():
                for path_id, count in enumerate(counts):
                    writer.writerow([trial, seed, label, path_id, count])


def write_mean_rank_cdf_csv(path, averaged):
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["mode", "rank", "avg_packet_count", "cdf"])
        for label, counts in averaged.items():
            xs, ys = cdf_xy(counts)
            for rank, (x, y) in enumerate(zip(xs, ys)):
                writer.writerow([label, rank, f"{x:.6f}", f"{y:.6f}"])


def write_summary_csv(path, summaries):
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["trial", "seed", "mode", "min", "max", "mean", "pstdev", "cv"]
        )
        writer.writeheader()
        for row in summaries:
            writer.writerow(row)


def plot_cdf(path, results, topology, packets, src_tor, dst_tor, trials, first_seed):
    fig, ax = plt.subplots(figsize=(7.0, 4.6))
    styles = {
        "16-bit srcport": {"color": "#0072B2", "marker": "o"},
        "32-bit EV": {"color": "#009E73", "marker": "s"},
        "104-bit five-tuple": {"color": "#D55E00", "marker": "^"},
    }

    for label, counts in results.items():
        xs, ys = cdf_xy(counts)
        ax.step(
            xs,
            ys,
            where="post",
            linewidth=2.0,
            label=label,
            color=styles[label]["color"],
        )
        ax.scatter(
            xs,
            ys,
            s=18,
            marker=styles[label]["marker"],
            color=styles[label]["color"],
            edgecolors="white",
            linewidths=0.4,
            zorder=3,
        )

    prefix = "Mean hash input dispersion" if trials > 1 else "Hash input dispersion"
    ax.set_title(
        f"{prefix} on one ToR pair "
        f"({topology.nodes} nodes, 2-tier, {topology.spine_count} paths)"
    )
    ax.set_xlabel("Packets on a path")
    ax.set_ylabel("CDF across spine paths")
    ax.set_ylim(0, 1.02)
    ax.grid(True, axis="both", linestyle="--", linewidth=0.6, alpha=0.35)
    ax.legend(frameon=False, loc="lower right")
    ax.text(
        0.02,
        0.05,
        (
            f"{packets} one-packet flows, ToR{src_tor}->ToR{dst_tor}, "
            f"{trials} trial(s), seeds {first_seed}-{first_seed + trials - 1}"
        ),
        transform=ax.transAxes,
        fontsize=9,
        color="#555555",
    )
    fig.tight_layout()
    fig.savefig(path, dpi=220)
    plt.close(fig)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Compare path-count CDFs for 16-bit srcport, 32-bit EV, and "
            "104-bit five-tuple hash inputs in a generated 2-tier topology."
        )
    )
    parser.add_argument("--nodes", type=int, default=2048, help="host/NIC count")
    parser.add_argument("--packets", type=int, default=1000, help="one-packet flows")
    parser.add_argument("--seed", type=int, default=13, help="random input seed")
    parser.add_argument(
        "--trials",
        type=int,
        default=1,
        help="number of consecutive random seeds to run and average",
    )
    parser.add_argument(
        "--hash-seed",
        type=lambda value: int(value, 0),
        default=HASH_SEED,
        help="seed mixed into crc32; accepts decimal or 0x-prefixed hex",
    )
    parser.add_argument("--src-tor", type=int, default=0)
    parser.add_argument("--dst-tor", type=int, default=1)
    parser.add_argument("--src-nic-offset", type=int, default=0)
    parser.add_argument("--dst-nic-offset", type=int, default=0)
    parser.add_argument("--dst-port", type=int, default=ROCE_UDP_PORT)
    parser.add_argument("--protocol", type=int, default=UDP_PROTOCOL)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    return parser


def simulate_once(args, topology, src_ip, dst_ip, seed):
    rng = random.Random(seed)
    src_ports = [rng.randrange(1 << 16) for _ in range(args.packets)]
    evs = [rng.randrange(1 << 32) for _ in range(args.packets)]

    payloads = {
        "16-bit srcport": [u16(port) for port in src_ports],
        "32-bit EV": [u32(ev) for ev in evs],
        "104-bit five-tuple": [
            five_tuple_104b(src_ip, dst_ip, port, args.dst_port, args.protocol)
            for port in src_ports
        ],
    }
    return {
        label: count_paths(items, topology.spine_count, args.hash_seed)
        for label, items in payloads.items()
    }


def main():
    args = build_arg_parser().parse_args()
    topology = generated_2tier_topology(args.nodes)

    if not (0 <= args.src_tor < topology.tor_count):
        raise SystemExit(f"--src-tor must be in [0, {topology.tor_count - 1}]")
    if not (0 <= args.dst_tor < topology.tor_count):
        raise SystemExit(f"--dst-tor must be in [0, {topology.tor_count - 1}]")
    if args.src_tor == args.dst_tor:
        raise SystemExit("--src-tor and --dst-tor must be different")
    if not (0 <= args.src_nic_offset < topology.hosts_per_tor):
        raise SystemExit(f"--src-nic-offset must be in [0, {topology.hosts_per_tor - 1}]")
    if not (0 <= args.dst_nic_offset < topology.hosts_per_tor):
        raise SystemExit(f"--dst-nic-offset must be in [0, {topology.hosts_per_tor - 1}]")
    if args.packets <= 0:
        raise SystemExit("--packets must be positive")
    if args.trials <= 0:
        raise SystemExit("--trials must be positive")
    if not (0 <= args.dst_port <= 0xFFFF):
        raise SystemExit("--dst-port must fit in 16 bits")
    if not (0 <= args.protocol <= 0xFF):
        raise SystemExit("--protocol must fit in 8 bits")

    src_host = args.src_tor * topology.hosts_per_tor + args.src_nic_offset
    dst_host = args.dst_tor * topology.hosts_per_tor + args.dst_nic_offset
    src_ip = ipv4_for_host(src_host)
    dst_ip = ipv4_for_host(dst_host)

    seeds = [args.seed + trial for trial in range(args.trials)]
    trial_results = [
        simulate_once(args, topology, src_ip, dst_ip, seed) for seed in seeds
    ]
    averaged_results = mean_rank_counts(trial_results)
    trial_summaries = []
    for trial, (seed, results) in enumerate(zip(seeds, trial_results)):
        for label, counts in results.items():
            row = summarize(label, counts)
            row.update({"trial": trial, "seed": seed})
            trial_summaries.append(row)
    average_summaries = average_summary_rows(trial_summaries)
    summaries = trial_summaries + average_summaries

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    figure = out_dir / "hash_entropy_path_count_cdf.png"
    counts_csv = out_dir / "hash_entropy_path_counts.csv"
    mean_rank_cdf_csv = out_dir / "hash_entropy_mean_rank_cdf.csv"
    summary_csv = out_dir / "hash_entropy_summary.csv"

    plot_cdf(
        figure,
        averaged_results,
        topology,
        args.packets,
        args.src_tor,
        args.dst_tor,
        args.trials,
        args.seed,
    )
    write_counts_csv(counts_csv, trial_results, seeds)
    write_mean_rank_cdf_csv(mean_rank_cdf_csv, averaged_results)
    write_summary_csv(summary_csv, summaries)

    print(
        "Topology: "
        f"{topology.nodes} NICs, 2-tier, K={topology.k}, "
        f"{topology.tor_count} ToRs, {topology.spine_count} spine paths, "
        f"{topology.hosts_per_tor} NICs/ToR"
    )
    print(
        f"Traffic: host {src_host} (ToR{args.src_tor}) -> "
        f"host {dst_host} (ToR{args.dst_tor}), {args.packets} one-packet flows"
    )
    print(f"Trials: {args.trials}, seeds {seeds[0]}-{seeds[-1]}")
    print(f"Hash: seeded crc32, seed=0x{args.hash_seed:08x}")
    print("mean over trials: mode,min,max,mean,pstdev,cv")
    for row in average_summaries:
        print(
            "{mode},{min},{max},{mean:.3f},{pstdev:.3f},{cv:.4f}".format(**row)
        )
    print(f"Wrote {figure}")
    print(f"Wrote {counts_csv}")
    print(f"Wrote {mean_rank_cdf_csv}")
    print(f"Wrote {summary_csv}")


if __name__ == "__main__":
    main()
