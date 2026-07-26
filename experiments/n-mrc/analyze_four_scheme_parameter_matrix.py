#!/usr/bin/env python3
"""Aggregate the paired parameter matrix and extract causal diagnostics."""

import argparse
import csv
import math
from pathlib import Path
import re
import statistics


COMPLETION = re.compile(
    r"Flow Roce_\d+_\d+\s+\d+ finished at ([0-9.]+) "
    r"total bytes (\d+) bg traffic [01] flowid (\d+)")
PROFILE = re.compile(r"^StorProfileDiag (.*)$", re.MULTILINE)
PAIR = re.compile(r"(\w+)=([^ ]+)")
METRICS = (
    "QueueDiag.composite_ecn_marks", "QueueDiag.composite_trims",
    "RoceDiag.nacks", "RoceDiag.ecn_echo_acks",
    "StorProfileDiag.stor_level_changes",
    "StorProfileDiag.netaware_level_changes",
    "StorProfileDiag.stor_all_zero_profiles",
    "StorProfileDiag.stor_all_zero_selections",
)
SEEDS = {"13", "29", "47"}


def geomean(values):
    return math.exp(statistics.fmean(math.log(value) for value in values))


def write_csv(path, rows):
    rows = list(rows)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def aggregate(rows):
    groups = {}
    paired_hashes = {}
    for row in rows:
        if row["all_flows_completed"] != "1" or row["config_ok"] != "1":
            raise ValueError(
                f"invalid row {row['scenario']}:{row['variant']}:"
                f"seed{row['seed']}")
        groups.setdefault((row["scenario"], row["variant"]), []).append(row)
        paired_hashes.setdefault(
            (row["scenario"], row["seed"]), set()).add(
                row["traffic_sha256"])
    if any(len(values) != 1 for values in paired_hashes.values()):
        raise ValueError("traffic hash differs within a scenario/seed pair")
    output = []
    for (scenario, variant), selected in sorted(groups.items()):
        seeds = [row["seed"] for row in selected]
        if len(seeds) != 3 or set(seeds) != SEEDS:
            raise ValueError(
                f"{scenario}:{variant} lacks exactly seeds 13/29/47")
        if len({row["traffic_sha256"] for row in selected}) != 3:
            raise ValueError(f"{scenario}:{variant} has duplicate traffic")
        item = {
            "scenario": scenario, "variant": variant,
            "primary_metric": selected[0]["primary_metric"],
            "primary_geomean_us": geomean(
                float(row["primary_us"]) for row in selected),
            "seed_values_us": ";".join(
                f"{row['seed']}:{float(row['primary_us']):.6f}"
                for row in selected),
        }
        for metric in METRICS:
            values = [float(row.get(metric) or 0) for row in selected]
            item[metric] = statistics.fmean(values)
        output.append(item)
    return output


def transition_rows(root, rows):
    output = []
    for row in rows:
        log = (root / "raw" / row["scenario"] / row["variant"] /
               f"seed_{row['seed']}" / "stdout.log")
        match = PROFILE.search(log.read_text(errors="ignore"))
        if not match:
            continue
        values = dict(PAIR.findall(match.group(1)))
        for kind in ("stor", "netaware"):
            if f"{kind}_level_transitions" not in values:
                continue
            matrix = [int(value) for value in
                      values[f"{kind}_level_transitions"].split("/")]
            changed = sum(
                matrix[i * 4 + j] for i in range(4) for j in range(4)
                if i != j)
            reversals = sum(
                min(matrix[i * 4 + j], matrix[j * 4 + i])
                for i in range(4) for j in range(i + 1, 4))
            output.append({
                "scenario": row["scenario"], "variant": row["variant"],
                "seed": row["seed"], "profile": kind,
                "level_changes": changed, "paired_reversals": reversals,
                "all_zero_profiles": values[f"{kind}_all_zero_profiles"],
                "all_zero_selections": values[f"{kind}_all_zero_selections"],
            })
    return output


def flow_bins(root, rows):
    output = []
    for row in rows:
        if row["scenario"] != "standard_websearch_proxy_80pct":
            continue
        log = (root / "raw" / row["scenario"] / row["variant"] /
               f"seed_{row['seed']}" / "stdout.log")
        starts = {}
        traffic = root / "traffic" / (
            f"n128_{row['scenario']}_seed{row['seed']}.cm")
        traffic_line = re.compile(
            r"\d+->\d+ id (\d+) start (\d+) size \d+")
        for line in traffic.read_text().splitlines():
            fields = line.split()
            match = traffic_line.fullmatch(line)
            if match:
                starts[int(match.group(1))] = int(match.group(2)) / 1_000_000
        bins = {"short_le_100k": [], "medium_100k_1m": [], "long_gt_1m": []}
        for match in COMPLETION.finditer(log.read_text(errors="ignore")):
            finish, size, flow_id = (
                float(match.group(1)), int(match.group(2)),
                int(match.group(3)))
            bucket = (
                "short_le_100k" if size <= 100_000 else
                "medium_100k_1m" if size <= 1_000_000 else "long_gt_1m")
            bins[bucket].append(finish - starts.get(flow_id, 0.0))
        for bucket, values in bins.items():
            if values:
                values.sort()
                output.append({
                    "variant": row["variant"], "seed": row["seed"],
                    "size_bin": bucket, "flows": len(values),
                    "mean_fct_us": statistics.fmean(values),
                    "p99_fct_us": values[math.ceil(.99 * len(values)) - 1],
                })
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    rows = list(csv.DictReader((args.root / "results.csv").open()))
    write_csv(args.root / "summary.csv", aggregate(rows))
    write_csv(args.root / "transitions.csv",
              transition_rows(args.root, rows))
    write_csv(args.root / "flow_size_bins.csv", flow_bins(args.root, rows))


if __name__ == "__main__":
    main()
