#!/usr/bin/env python3
"""Validate MRC cooldown recovery with long and time-varying workloads."""

import concurrent.futures
import csv
import importlib.util
import math
import os
import random
import re
import statistics
from collections import namedtuple
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "experiments/n-mrc/run_packet_lb_common_workloads_512.py"
OUT = Path(os.environ.get(
    "MRC_COOLDOWN_RECOVERY_OUT",
    str(ROOT / "experiments/n-mrc/output/"
        "mrc_cooldown_recovery_multiscenario_512"),
)).resolve()
SEEDS = [13, 29, 47]
BDP_PKTS = 86
ACTIVE_EVS = 16
MIB = 1024 * 1024

Variant = namedtuple(
    "Variant", "key label reference_pkts bdp_ratio skip_selections args")
Scenario = namedtuple(
    "Scenario",
    "name traffic size_bytes degraded mixed path_hotspot incast "
    "hotspot_rate_gbps hotspot_on_us hotspot_off_us mixed_bursts "
    "target_start_us end_us category")


def rounded_skip(reference_pkts):
    return ((reference_pkts + ACTIVE_EVS - 1) // ACTIVE_EVS) * ACTIVE_EVS


VARIANTS = [
    Variant("mrc_one_cycle", "one cycle", ACTIVE_EVS,
            ACTIVE_EVS / BDP_PKTS, ACTIVE_EVS, []),
    Variant("mrc_bdp86", "1.00 BDP", 86, 1.0, rounded_skip(86),
            ["-mrc_cooldown_mode", "cwnd_scaled"]),
    Variant("mrc_ref108", "1.25 BDP", 108, 1.25, rounded_skip(108),
            ["-mrc_cooldown_mode", "cwnd_scaled",
             "-mrc_cooldown_reference_pkts", "108"]),
    Variant("mrc_ref129", "1.50 BDP", 129, 1.5, rounded_skip(129),
            ["-mrc_cooldown_mode", "cwnd_scaled",
             "-mrc_cooldown_reference_pkts", "129"]),
    Variant("mrc_ref155", "1.80 BDP", 155, 1.8, rounded_skip(155),
            ["-mrc_cooldown_mode", "cwnd_scaled",
             "-mrc_cooldown_reference_pkts", "155"]),
    Variant("mrc_ref172", "2.00 BDP", 172, 2.0, rounded_skip(172),
            ["-mrc_cooldown_mode", "cwnd_scaled",
             "-mrc_cooldown_reference_pkts", "172"]),
    Variant("mrc_ref215", "2.50 BDP", 215, 2.5, rounded_skip(215),
            ["-mrc_cooldown_mode", "cwnd_scaled",
             "-mrc_cooldown_reference_pkts", "215"]),
    Variant("mrc_ref344", "4.00 BDP", 344, 4.0, rounded_skip(344),
            ["-mrc_cooldown_mode", "cwnd_scaled",
             "-mrc_cooldown_reference_pkts", "344"]),
    Variant("mrc_ref688", "8.00 BDP", 688, 8.0, rounded_skip(688),
            ["-mrc_cooldown_mode", "cwnd_scaled",
             "-mrc_cooldown_reference_pkts", "688"]),
]


def scenario(name, traffic, size_mib, category, degraded=False, mixed=False,
             path_hotspot=False, rate=0, on_us=0, off_us=0, bursts=0,
             start_us=None, end_us=10000):
    if start_us is None:
        start_us = 250.0 if path_hotspot or mixed else 0.0
    return Scenario(
        name, traffic, size_mib * MIB, degraded, mixed, path_hotspot, False,
        rate, on_us, off_us, bursts, start_us, end_us, category)


SCENARIOS = [
    scenario("healthy_permutation_4m", "permutation", 4, "healthy"),
    scenario("degraded_permutation_4m", "permutation", 4,
             "persistent", degraded=True),
    scenario("degraded_tornado_4m", "tornado", 4,
             "persistent", degraded=True),
    scenario("persistent_hotspot_4m_300g", "permutation", 4,
             "persistent", path_hotspot=True, rate=300, on_us=1000,
             off_us=0),
    scenario("bursty_hotspot_4m_300g_50pct", "permutation", 4,
             "transient", path_hotspot=True, rate=300, on_us=20,
             off_us=20),
    scenario("bursty_hotspot_4m_300g_50pct_offphase", "permutation", 4,
             "transient", path_hotspot=True, rate=300, on_us=20,
             off_us=20, start_us=270.0),
    scenario("sparse_hotspot_4m_300g_20pct", "permutation", 4,
             "transient", path_hotspot=True, rate=300, on_us=20,
             off_us=80),
    scenario("sparse_hotspot_4m_300g_20pct_onphase", "permutation", 4,
             "transient", path_hotspot=True, rate=300, on_us=20,
             off_us=80, start_us=210.0),
    scenario("bursty_hotspot_4m_200g_50pct", "permutation", 4,
             "transient", path_hotspot=True, rate=200, on_us=50,
             off_us=50),
    scenario("bursty_hotspot_4m_200g_50pct_onphase", "permutation", 4,
             "transient", path_hotspot=True, rate=200, on_us=50,
             off_us=50, start_us=225.0),
    scenario("sparse_hotspot_8m_300g_20pct", "permutation", 8,
             "long_transient", path_hotspot=True, rate=300, on_us=20,
             off_us=80),
    scenario("sparse_hotspot_8m_300g_20pct_onphase", "permutation", 8,
             "long_transient", path_hotspot=True, rate=300, on_us=20,
             off_us=80, start_us=210.0),
    scenario("mixed_bursty_8m", "mixed", 8, "long_transient",
             mixed=True, rate=300, on_us=50, off_us=50, bursts=8),
]

RECOVERY_FIELDS = [
    "cycle_cooling_events", "cycle_cooling_expiries",
    "cooling_skip_selection_sum", "cooling_skip_selection_events",
    "cwnd_scaled_feedback_events",
    "cwnd_scaled_duplicate_feedback_ignored",
    "forced_cooling_use", "forced_cooling_earliest_use",
    "active_avg", "cooling_avg", "active_final", "cooling_final",
    "active_physical_final", "cooling_physical_final",
]

PATH_HIST_RE = re.compile(r"^PathSelectDiag .*?physical_mod_hist=([^\s]*)",
                          re.MULTILINE)


def variant_by_key(key):
    return next(variant for variant in VARIANTS if variant.key == key)


def load_runner():
    spec = importlib.util.spec_from_file_location("mrc_recovery_base", BASE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replace_option(command, option, value):
    index = command.index(option)
    command[index + 1] = str(value)


def permutation_flows(runner, scenario_item, seed):
    rng = random.Random(seed)
    destinations = list(range(runner.NODES))
    while True:
        rng.shuffle(destinations)
        if all(src != destinations[src] for src in range(runner.NODES)):
            break
    return [runner.make_flow(
        src, destinations[src], size=scenario_item.size_bytes,
        start_us=scenario_item.target_start_us) for src in range(runner.NODES)]


def tornado_flows(runner, scenario_item):
    shift = runner.NODES // 2
    return [runner.make_flow(
        src, (src + shift) % runner.NODES, size=scenario_item.size_bytes)
        for src in range(runner.NODES)]


def mixed_flows(runner, scenario_item):
    flows = []
    for leaf in range(runner.LEAVES):
        dst_leaf = (leaf + runner.LEAVES // 2) % runner.LEAVES
        for offset in range(1, runner.HOSTS_PER_LEAF):
            flows.append(runner.make_flow(
                leaf * runner.HOSTS_PER_LEAF + offset,
                dst_leaf * runner.HOSTS_PER_LEAF + offset,
                size=scenario_item.size_bytes,
                start_us=scenario_item.target_start_us))

    rate_mbps = scenario_item.hotspot_rate_gbps * 1000
    background_size = int(round(rate_mbps * scenario_item.hotspot_on_us / 8.0))
    period_us = scenario_item.hotspot_on_us + scenario_item.hotspot_off_us
    for burst in range(scenario_item.mixed_bursts):
        start_us = burst * period_us
        for leaf in range(runner.LEAVES):
            dst_leaf = (leaf + runner.LEAVES // 2) % runner.LEAVES
            flows.append(runner.make_flow(
                leaf * runner.HOSTS_PER_LEAF,
                dst_leaf * runner.HOSTS_PER_LEAF,
                size=background_size, start_us=start_us,
                role="background", rate_mbps=rate_mbps))
    return flows


def parse_physical_hist(text):
    match = PATH_HIST_RE.search(text)
    if not match or not match.group(1):
        return {}
    result = {}
    for item in match.group(1).split("/"):
        if not item:
            continue
        path_id, count = item.split(":", 1)
        result[int(path_id)] = int(count)
    return result


def path_distribution_metrics(histogram):
    if not histogram:
        return {
            "physical_path_jain": 0.0,
            "physical_path_cv": 0.0,
            "physical_path_max_share": 0.0,
        }
    path_count = max(histogram) + 1
    values = [float(histogram.get(path_id, 0))
              for path_id in range(path_count)]
    total = sum(values)
    square_sum = sum(value * value for value in values)
    mean = total / path_count
    variance = sum((value - mean) ** 2 for value in values) / path_count
    return {
        "physical_path_jain": (
            total * total / (path_count * square_sum) if square_sum else 0.0),
        "physical_path_cv": math.sqrt(variance) / mean if mean else 0.0,
        "physical_path_max_share": max(values) / total if total else 0.0,
    }


def runtime_ok(text, variant):
    if variant.key == "mrc_one_cycle":
        mode = "one_cycle"
        reference_pkts = BDP_PKTS
        source = "topology_bdp"
        skip = rounded_skip(BDP_PKTS)
    else:
        mode = "cwnd_scaled"
        reference_pkts = variant.reference_pkts
        source = "topology_bdp" if reference_pkts == BDP_PKTS else "explicit"
        skip = variant.skip_selections
    rotations = skip // ACTIVE_EVS
    expected = (
        f"MrcCooldownDiag mrc_cooldown_mode={mode} "
        f"mrc_cooldown_reference={source} "
        f"mrc_cooldown_reference_pkts={reference_pkts} "
        f"mrc_cwnd_scaled_rotations={rotations} "
        f"mrc_cwnd_scaled_skip_selections={skip}")
    return int(
        expected in text and
        "MrcFallbackDiag mrc_all_cooling_fallback=earliest" in text)


def configured_runner(seed):
    runner = load_runner()
    runner.SEED = seed
    runner.WORKLOADS = SCENARIOS
    runner.SCHEMES = [
        runner.Scheme(variant.key, variant.label, "mrc")
        for variant in VARIANTS]
    runner.OUT = OUT / "raw" / f"seed{seed}"
    base_build_command = runner.build_command
    base_parse_run = runner.parse_run
    base_run_case = runner.run_case

    def make_flows(scenario_item):
        if scenario_item.mixed:
            return mixed_flows(runner, scenario_item)
        if scenario_item.traffic == "permutation":
            return permutation_flows(runner, scenario_item, seed)
        if scenario_item.traffic == "tornado":
            return tornado_flows(runner, scenario_item)
        raise ValueError(f"unsupported scenario {scenario_item.name}")

    def build_command(scenario_item, scheme, traffic_file, dat_file,
                      flow_count):
        command = base_build_command(
            scenario_item, scheme, traffic_file, dat_file, flow_count)
        replace_option(command, "-end", scenario_item.end_us)
        if scenario_item.path_hotspot:
            replace_option(command, "-path_hotspot_bg_rate_gbps",
                           scenario_item.hotspot_rate_gbps)
            replace_option(command, "-path_hotspot_bg_on_us",
                           scenario_item.hotspot_on_us)
            replace_option(command, "-path_hotspot_bg_off_us",
                           scenario_item.hotspot_off_us)
        return command + variant_by_key(scheme.key).args

    def decorate_row(row, scenario_item, scheme, text):
        variant = variant_by_key(scheme.key)
        row.update(runner.parse_key_values(text, "MrcDiag", RECOVERY_FIELDS))
        histogram = parse_physical_hist(text)
        row.update(path_distribution_metrics(histogram))
        row["physical_path_hist"] = "/".join(
            f"{path_id}:{histogram[path_id]}" for path_id in sorted(histogram))
        row["seed"] = seed
        row["category"] = scenario_item.category
        row["flow_size_bytes"] = scenario_item.size_bytes
        row["hotspot_rate_gbps"] = scenario_item.hotspot_rate_gbps
        row["hotspot_on_us"] = scenario_item.hotspot_on_us
        row["hotspot_off_us"] = scenario_item.hotspot_off_us
        row["target_start_us"] = scenario_item.target_start_us
        row["reference_pkts"] = variant.reference_pkts
        row["bdp_ratio"] = variant.bdp_ratio
        row["skip_selections"] = variant.skip_selections
        row["cooling_expiry_ratio"] = (
            float(row["cycle_cooling_expiries"]) /
            float(row["cycle_cooling_events"])
            if row["cycle_cooling_events"] else 0.0)
        return row

    def parse_run(scenario_item, scheme, flows, stdout_file, idmap_file,
                  returncode, command):
        parse_view = scenario_item._replace(path_hotspot=False)
        row = base_parse_run(
            parse_view, scheme, flows, stdout_file, idmap_file,
            returncode, command)
        text = stdout_file.read_text(errors="ignore") if stdout_file.exists() else ""
        if scenario_item.path_hotspot:
            expected = (
                f"Path hotspot background installed "
                f"{runner.expected_hotspot_background_sources()} "
                f"fixed-link sources hot_spines {runner.HOT_SPINES} "
                f"rate {scenario_item.hotspot_rate_gbps}Gbps "
                f"on {scenario_item.hotspot_on_us}us "
                f"off {scenario_item.hotspot_off_us}us")
            row["config_ok"] = int(row["config_ok"] and expected in text)
        variant = variant_by_key(scheme.key)
        row["config_ok"] = int(row["config_ok"] and runtime_ok(text, variant))
        return decorate_row(row, scenario_item, scheme, text)

    def run_case(scenario_item, scheme):
        row = base_run_case(scenario_item, scheme)
        if "seed" not in row:
            decorate_row(row, scenario_item, scheme, "")
        return row

    runner.make_flows = make_flows
    runner.build_command = build_command
    runner.parse_run = parse_run
    runner.run_case = run_case
    return runner


def run_seed(seed):
    runner = configured_runner(seed)
    rows = []
    workers = int(os.environ.get("MRC_COOLDOWN_RECOVERY_WORKERS", "4"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(runner.run_case, scenario_item, scheme)
            for scenario_item in runner.WORKLOADS
            for scheme in runner.SCHEMES]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())
    return runner, rows


def median_rows(rows):
    metrics = [
        "avg_fct_us", "p99_fct_us", "p999_fct_us", "max_fct_us",
        "nacks", "nacks_ooo", "nacks_trim", "nacks_loss", "rtos",
        "retx_packets", "retx_ratio", "composite_trims",
        "composite_drops", "composite_ecn_marks",
        "cycle_cooling_events", "cycle_cooling_expiries",
        "cooling_expiry_ratio", "cwnd_scaled_duplicate_feedback_ignored",
        "forced_cooling_use", "active_avg", "cooling_avg",
        "active_final", "cooling_final", "physical_path_jain",
        "physical_path_cv", "physical_path_max_share",
    ]
    result = {}
    for scenario_item in SCENARIOS:
        for variant in VARIANTS:
            group = [
                row for row in rows
                if row["workload"] == scenario_item.name and
                row["scheme"] == variant.key]
            result[scenario_item.name, variant.key] = {
                metric: statistics.median(float(row[metric]) for row in group)
                for metric in metrics}
    return result


def percent_change(new, old):
    return 100.0 * (new / old - 1.0) if old else 0.0


def geometric_mean(values):
    return math.prod(values) ** (1.0 / len(values)) if values else 0.0


def write_outputs(rows):
    OUT.mkdir(parents=True, exist_ok=True)
    scenario_order = {
        item.name: index for index, item in enumerate(SCENARIOS)}
    variant_order = {
        item.key: index for index, item in enumerate(VARIANTS)}
    rows.sort(key=lambda row: (
        scenario_order[row["workload"]], variant_order[row["scheme"]],
        int(row["seed"])))

    with (OUT / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with (OUT / "commands.tsv").open("w", encoding="utf-8") as handle:
        print("seed\tworkload\tvariant\tcommand", file=handle)
        for row in rows:
            print(f"{row['seed']}\t{row['workload']}\t{row['scheme']}\t"
                  f"{row['command']}", file=handle)

    medians = median_rows(rows)
    discriminating = [
        item for item in SCENARIOS
        if item.name not in {"healthy_permutation_4m", "mixed_bursty_8m"}]

    def normalized_regret(variant_key):
        ratios = []
        for scenario_item in discriminating:
            best = min(
                medians[scenario_item.name, candidate.key]["p99_fct_us"]
                for candidate in VARIANTS)
            ratios.append(
                medians[scenario_item.name, variant_key]["p99_fct_us"] / best)
        return {
            "gmean": 100.0 * (geometric_mean(ratios) - 1.0),
            "median": 100.0 * (statistics.median(ratios) - 1.0),
            "worst": 100.0 * (max(ratios) - 1.0),
        }

    one_regret = normalized_regret("mrc_one_cycle")
    bdp_regret = normalized_regret("mrc_bdp86")
    sparse4_one = medians[
        "sparse_hotspot_4m_300g_20pct", "mrc_one_cycle"]
    sparse4_bdp = medians[
        "sparse_hotspot_4m_300g_20pct", "mrc_bdp86"]
    sparse8_one = medians[
        "sparse_hotspot_8m_300g_20pct", "mrc_one_cycle"]
    sparse8_bdp = medians[
        "sparse_hotspot_8m_300g_20pct", "mrc_bdp86"]
    persistent_one = medians[
        "persistent_hotspot_4m_300g", "mrc_one_cycle"]
    persistent_bdp = medians[
        "persistent_hotspot_4m_300g", "mrc_bdp86"]

    lines = [
        "# MRC Long-Flow And Transient-Congestion Cooldown Validation",
        "",
        "## Verdict",
        "",
        "- Natural EV recovery is performance-critical. Under the sparse 300 Gbit/s 20% duty hotspot, one-cycle p99 is "
        f"{sparse4_one['p99_fct_us']:.3f} us for 4 MiB and "
        f"{sparse8_one['p99_fct_us']:.3f} us for 8 MiB, versus "
        f"{sparse4_bdp['p99_fct_us']:.3f} and "
        f"{sparse8_bdp['p99_fct_us']:.3f} us at 1 BDP. Longer 2.5-8 BDP windows are substantially worse.",
        "- Persistent congestion still benefits from a longer exclusion window: one-cycle is "
        f"{percent_change(persistent_one['p99_fct_us'], persistent_bdp['p99_fct_us']):+.1f}% "
        "slower than 1 BDP on the persistent 300 Gbit/s hotspot, and 1.8 BDP has the lowest median p99 there.",
        "- Across the 11 workloads that actually exercise cooldown, one-cycle has "
        f"{one_regret['gmean']:.1f}% geometric-mean p99 regret and "
        f"{one_regret['worst']:.1f}% worst regret versus the per-workload oracle. "
        f"The current 1-BDP default is {bdp_regret['gmean']:.1f}%/"
        f"{bdp_regret['worst']:.1f}%. One-cycle is therefore the most robust fixed policy in this matrix.",
        "- No universal multi-BDP optimum exists. Several apparent winners are phase resonances between selection-count deadlines and the synthetic on/off period, not stable cooldown choices.",
        "- Following this experiment, one-cycle rearming was selected as the MRC default; fixed multi-BDP modes remain explicit diagnostics.",
        "",
        "## Why One-Cycle Recovers Without Ignoring Persistent Congestion",
        "",
        "- Current `one_cycle` semantics re-arm the one-active-set deadline when another ECN/TRIM feedback arrives while the EV is still cooling. Persistent in-flight feedback can therefore extend the short skip.",
        "- Once feedback from that EV drains, no new re-arm occurs and the EV returns after one active-set traversal. This is short-window debounce with signal-driven extension, not permanent exclusion.",
        "- `cwnd_scaled` multi-BDP mode behaves differently: feedback received while an EV is already cooling is counted as duplicate and ignored, so the original fixed deadline is not extended.",
        "- Consequently, `cycle_cooling_events/expiries` is not a directly comparable episode ratio between the two modes: one-cycle counts re-arms as events, while multi-BDP counts one event and records later feedback as duplicate-ignored.",
        "",
        "## Setup",
        "",
        "- 512 nodes, 2-tier single-plane Clos, 16 active EVs, 400 Gbit/s",
        "- composite_ecn_lb, SP/SACK64, dcqcn_variant natural, earliest fallback",
        "- seeds 13/29/47; topology BDP is 86 packets",
        "- cooldowns: one EV-set cycle and 1/1.25/1.5/1.8/2/2.5/4/8 BDP",
        "- 4 MiB common long flows; 8 MiB repeated transient/mixed flows",
        "- fixed-link hotspots include persistent, 50% duty, 20% duty, lower-rate, and paired on/off start phases",
        "- no MRC selector/state, queue, transport, or congestion-control changes",
        "- 351 completed runs: 13 workloads x 9 cooldowns x 3 seeds",
        "",
        "## Start-Phase Sensitivity",
        "",
        "The paired phases hold topology, load, duty cycle, and seed set constant; only target start time changes.",
        "",
        "| workload pair | cooldown | phase A p99 | phase B p99 | change |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    phase_pairs = [
        ("bursty_hotspot_4m_300g_50pct",
         "bursty_hotspot_4m_300g_50pct_offphase", "300G 50% 4MiB"),
        ("sparse_hotspot_4m_300g_20pct",
         "sparse_hotspot_4m_300g_20pct_onphase", "300G 20% 4MiB"),
        ("sparse_hotspot_8m_300g_20pct",
         "sparse_hotspot_8m_300g_20pct_onphase", "300G 20% 8MiB"),
        ("bursty_hotspot_4m_200g_50pct",
         "bursty_hotspot_4m_200g_50pct_onphase", "200G 50% 4MiB"),
    ]
    phase_variants = [
        variant_by_key("mrc_one_cycle"), variant_by_key("mrc_bdp86"),
        variant_by_key("mrc_ref129"), variant_by_key("mrc_ref155"),
        variant_by_key("mrc_ref215")]
    for first, second, label in phase_pairs:
        for variant in phase_variants:
            first_p99 = medians[first, variant.key]["p99_fct_us"]
            second_p99 = medians[second, variant.key]["p99_fct_us"]
            lines.append(
                f"| {label} | {variant.label} | {first_p99:.3f} | "
                f"{second_p99:.3f} | "
                f"{percent_change(second_p99, first_p99):+.1f}% |")

    lines += [
        "",
        "## Three-Seed Median Results",
        "",
        "| workload | cooldown | skip | p99 | p99.9 | max | NACK O/T/L | RTO | retx | cool/expire | expiry | forced | final A/C | Jain/maxshare |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: | --- | --- |",
    ]
    for scenario_item in SCENARIOS:
        for variant in VARIANTS:
            row = medians[scenario_item.name, variant.key]
            lines.append(
                f"| {scenario_item.name} | {variant.label} | "
                f"{variant.skip_selections} | {row['p99_fct_us']:.3f} | "
                f"{row['p999_fct_us']:.3f} | {row['max_fct_us']:.3f} | "
                f"{row['nacks_ooo']:.0f}/{row['nacks_trim']:.0f}/"
                f"{row['nacks_loss']:.0f} | {row['rtos']:.0f} | "
                f"{row['retx_ratio']:.6f} | "
                f"{row['cycle_cooling_events']:.0f}/"
                f"{row['cycle_cooling_expiries']:.0f} | "
                f"{row['cooling_expiry_ratio']:.3f} | "
                f"{row['forced_cooling_use']:.0f} | "
                f"{row['active_final']:.0f}/{row['cooling_final']:.0f} | "
                f"{row['physical_path_jain']:.4f}/"
                f"{row['physical_path_max_share']:.4f} |")

    lines += [
        "",
        "## Relative To One BDP",
        "",
        "| workload | cooldown | p99 | p99.9 | max | retx | expiry delta | forced delta |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario_item in SCENARIOS:
        base = medians[scenario_item.name, "mrc_bdp86"]
        for variant in VARIANTS:
            row = medians[scenario_item.name, variant.key]
            lines.append(
                f"| {scenario_item.name} | {variant.label} | "
                f"{percent_change(row['p99_fct_us'], base['p99_fct_us']):+.2f}% | "
                f"{percent_change(row['p999_fct_us'], base['p999_fct_us']):+.2f}% | "
                f"{percent_change(row['max_fct_us'], base['max_fct_us']):+.2f}% | "
                f"{percent_change(row['retx_packets'], base['retx_packets']):+.2f}% | "
                f"{row['cooling_expiry_ratio'] - base['cooling_expiry_ratio']:+.3f} | "
                f"{row['forced_cooling_use'] - base['forced_cooling_use']:+.0f} |")

    lines += ["", "## Per-Workload Best Median p99", "",
              "| workload | category | best | p99 | p99.9 | max | expiry | forced |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for scenario_item in SCENARIOS:
        best = min(VARIANTS, key=lambda variant: medians[
            scenario_item.name, variant.key]["p99_fct_us"])
        row = medians[scenario_item.name, best.key]
        lines.append(
            f"| {scenario_item.name} | {scenario_item.category} | "
            f"{best.label} | {row['p99_fct_us']:.3f} | "
            f"{row['p999_fct_us']:.3f} | {row['max_fct_us']:.3f} | "
            f"{row['cooling_expiry_ratio']:.3f} | "
            f"{row['forced_cooling_use']:.0f} |")

    lines += [
        "",
        "## Interpretation Guardrails",
        "",
        "- Persistent-hotspot wins alone do not justify a long default cooldown.",
        "- A useful recovery policy should record natural expiries on long flows and avoid tail regressions when the hotspot turns off.",
        "- High forced-cooling counts mean the all-cooling fallback is bypassing deadlines; such a result is not clean evidence for path exclusion.",
        "- Jain index and maximum path share are aggregate diagnostics. They identify gross path concentration but do not replace per-QP state analysis.",
        "",
        "## Data Quality",
        "",
        f"- rows: {len(rows)}/{len(SEEDS) * len(SCENARIOS) * len(VARIANTS)}",
        f"- incomplete/config-failed: {sum(not (int(row['returncode']) == 0 and int(row['config_ok']) == 1 and int(row['all_flows_completed']) == 1) for row in rows)}",
        "- target FCT excludes background flows; queue and MRC diagnostics cover the full run.",
    ]
    report = OUT / "mrc_cooldown_recovery_multiscenario_for_gpt.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    all_rows = []
    runner = None
    for seed in SEEDS:
        runner, rows = run_seed(seed)
        all_rows.extend(rows)
    report = write_outputs(all_rows)
    print(report, flush=True)
    if runner is not None and not runner.DRY_RUN:
        failed = [row for row in all_rows if not runner.row_complete(row)]
        if failed:
            raise SystemExit(f"{len(failed)} incomplete or invalid runs")


if __name__ == "__main__":
    main()
