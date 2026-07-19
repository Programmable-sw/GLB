#!/usr/bin/env python3
"""Compare hybrid n-MRC EV/reroute choices and common baselines.

The runner deliberately keeps the transport, congestion control, queueing,
traffic matrix, and random seed identical within every scenario/seed block.
It writes the complete command manifest before running any simulations.
"""

import argparse
import concurrent.futures
import csv
import hashlib
import math
from collections import namedtuple
from pathlib import Path
import random
import re
import shlex
import subprocess
import time


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SIM = ROOT / "sim/datacenter/htsim_roce"
DEFAULT_OUT = ROOT / "experiments/n-mrc/output/hybrid_nmrc_compare"

Variant = namedtuple("Variant", "key label lb ev_mode policy")
Scenario = namedtuple(
    "Scenario", "key label traffic degraded path_hotspot mixed incast")
Flow = namedtuple("Flow", "src dst size start_us role rate_mbps")

SCENARIOS = (
    Scenario("healthy_permutation", "healthy permutation", "permutation",
             False, False, False, False),
    Scenario("healthy_tornado", "healthy tornado", "tornado",
             False, False, False, False),
    Scenario("asymmetric_uplink", "sparse half-rate ToR uplinks",
             "permutation", True, False, False, False),
    Scenario("path_hotspot", "fixed hot-spine background", "permutation",
             False, True, False, False),
    Scenario("mixed_background", "foreground plus pulsed background", "mixed",
             False, False, True, False),
    Scenario("incast_recovery", "32-to-1 recovery pressure", "incast",
             False, False, False, True),
)

BASELINES = (
    Variant("sglb", "SGLB", "sglb", "", ""),
    Variant("adaptive_routing", "AR", "adaptive-routing", "", ""),
    Variant("netaware", "NetAware", "netaware", "", ""),
    Variant("mrc", "MRC", "mrc", "", ""),
    Variant("reps", "REPS", "reps", "", ""),
)

FINISH_RE = re.compile(r"Flow Roce_(\d+)_(\d+) (\d+) finished at ([0-9.]+)")
IDMAP_RE = re.compile(r"^(\d+)\s+Roce_(\d+)_(\d+)$")
KEY_VALUE_RE = re.compile(r"([A-Za-z0-9_]+)=([0-9.eE+\-]+)")
NEW_RETX_RE = re.compile(r"New:\s+(\d+)\s+Rtx:\s+(\d+)")

ROCE_FIELDS = (
    "acks", "nacks", "nacks_ooo", "nacks_trim", "nacks_loss", "rtos",
    "ecn_echo_acks", "duplicate_acks", "feedback_acks", "feedback_nacks",
)
QUEUE_FIELDS = (
    "lossless_overflows", "lossless_ecn_marks", "lossy_drops",
    "lossy_ecn_marks", "composite_trims", "composite_drops",
    "composite_ecn_marks",
)
HYBRID_FIELDS = (
    "route_checks", "reroutes", "threshold_blocked", "fastcnp_generated",
    "fastcnp_arrived", "fastcnp_after_done", "fastcnp_bytes",
    "fastcnp_arrived_bytes",
    "fastcnp_latency_avg_us", "fastcnp_route_missing",
    "fastcnp_unknown_qp", "fastcnp_unknown_ev", "cooldown_starts",
    "cooling_skips", "cooling_recoveries", "duplicate_notifications",
    "all_cooling_fallbacks", "observed_evs",
    "observed_first_hop_choices", "observed_first_hop_alias_ratio",
)
NETAWARE_FIELDS = (
    "samples", "feedbacks", "packet_feedbacks", "time_feedbacks",
    "feedback_packets_sum", "feedback_zero_bits", "all_good_feedbacks",
)
SGLB_FIELDS = (
    "route_calls", "avg_available_choices", "avg_candidate_choices",
    "avg_best_quality_choices", "avg_distinct_qualities",
    "all_same_quality_calls", "all_zero_quality_calls",
    "selected_nonbest_quality", "avg_score_spread",
)
BOUNDED_FIELDS = (
    "inflight_final", "recovery_inflight_final_bytes",
    "recovery_inflight_max_bytes", "exact_trim_recoveries",
    "sack_loss_recoveries",
)
QUEUE_CV_FIELDS = (
    "spine_queue_cv", "spine_queue_avg", "spine_queue_count",
    "spine_queue_peak_bytes", "spine_queue_p95_fraction",
    "spine_queue_p99_fraction",
)

SUMMARY_FIELDS = (
    "scenario", "variant", "variant_label", "seed", "paths", "traffic_sha256",
    "returncode", "config_ok", "all_flows_completed", "completed", "flows",
    "background_completed", "background_flows", "avg_fct_us", "p50_fct_us",
    "p95_fct_us", "p99_fct_us", "p999_fct_us", "max_fct_us", "cct_us",
    "goodput_gbps", "flow_goodput_jain", "new_packets", "retx_packets",
    "retx_ratio", "level_transitions", "ev_coverage_ratio",
    "fastcnp_packet_amplification", "fastcnp_effective_cooling_ratio",
) + ROCE_FIELDS + QUEUE_FIELDS + QUEUE_CV_FIELDS + HYBRID_FIELDS + NETAWARE_FIELDS + SGLB_FIELDS + BOUNDED_FIELDS + (
    "runtime_s", "stdout", "command",
)


def variant_matrix(paths):
    """Return the non-redundant experiment matrix for physical path count P."""
    modes = ("encoded", "random_matched")
    if paths < 32:
        modes += ("random32",)
    variants = []
    for mode in modes:
        for policy in ("any_better", "better_ge3"):
            variants.append(Variant(
                f"nmrc_{mode}_{policy}",
                f"n-MRC {mode}/{policy}",
                "n-mrc", mode, policy))
    return tuple(variants) + BASELINES


def generated_fattree_shape(nodes):
    k = int(round(math.sqrt(2 * nodes)))
    if k * k // 2 != nodes or k % 2:
        raise ValueError(
            f"{nodes} nodes is not a generated two-tier k^2/2 fat tree")
    return {"k": k, "leaves": k, "hosts_per_leaf": k // 2,
            "paths": k // 2}


def parse_csv(raw, cast=str):
    return tuple(cast(item.strip()) for item in raw.split(",") if item.strip())


def make_flow(src, dst, size, start_us=0.0, role="target", rate_mbps=0):
    return Flow(src, dst, size, start_us, role, rate_mbps)


def permutation_flows(nodes, size, seed, start_us=0.0):
    rng = random.Random(seed)
    destinations = list(range(nodes))
    while True:
        rng.shuffle(destinations)
        if all(src != destinations[src] for src in range(nodes)):
            break
    return [make_flow(src, destinations[src], size, start_us)
            for src in range(nodes)]


def tornado_flows(nodes, size, start_us=0.0):
    shift = nodes // 2
    return [make_flow(src, (src + shift) % nodes, size, start_us)
            for src in range(nodes)]


def mixed_flows(nodes, size, seed, quick):
    shape = generated_fattree_shape(nodes)
    flows = permutation_flows(nodes, size, seed, 250.0)
    rate_mbps = 100000 if quick else 300000
    on_us = 20.0 if quick else 100.0
    period_us = 100.0 if quick else 200.0
    bursts = 2 if quick else 3
    background_size = int(round(rate_mbps * on_us / 8.0))
    for burst in range(bursts):
        start_us = burst * period_us
        for leaf in range(shape["leaves"]):
            peer = (leaf + shape["leaves"] // 2) % shape["leaves"]
            src = leaf * shape["hosts_per_leaf"]
            dst = peer * shape["hosts_per_leaf"]
            flows.append(make_flow(
                src, dst, background_size, start_us, "background", rate_mbps))
    return flows


def flows_for(scenario, nodes, size, seed, quick):
    if scenario.incast:
        count = min(32, nodes - 1)
        return [make_flow(src, 0, size, 250.0) for src in range(1, count + 1)]
    if scenario.mixed:
        return mixed_flows(nodes, size, seed, quick)
    start_us = 250.0 if scenario.path_hotspot else 0.0
    if scenario.traffic == "tornado":
        return tornado_flows(nodes, size, start_us)
    return permutation_flows(nodes, size, seed, start_us)


def write_traffic(path, nodes, flows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        print("Nodes", nodes, file=handle)
        print("Connections", len(flows), file=handle)
        for flow_id, flow in enumerate(flows, 1):
            start_ps = int(round(flow.start_us * 1_000_000.0))
            line = (f"{flow.src}->{flow.dst} id {flow_id} "
                    f"start {start_ps} size {flow.size}")
            if flow.rate_mbps:
                line += f" rate_mbps {flow.rate_mbps}"
            print(line, file=handle)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_command(args, scenario, variant, seed, paths, traffic_file,
                  dat_file, flow_count):
    command = [
        str(args.sim),
        "-o", str(dat_file),
        "-tm", str(traffic_file),
        "-nodes", str(args.nodes),
        "-conns", str(flow_count),
        "-tiers", "2",
        "-linkspeed", "400000",
        "-queue_type", "composite_ecn_lb",
        "-host_queue_type", "prio",
        "-mtu", "4096",
        "-end", str(args.end_us),
        "-paths", str(paths),
        "-seed", str(seed),
        "-cc", "dcqcn_variant",
        "-roce_rx_mode", "sp",
        "-roce_sack_bitmap_bits", "64",
        "-roce_transport_semantics", "mrc_exact_bounded",
        "-roce_trim_recovery", "exact",
        "-hop_latency", "0.5",
        "-switch_latency", "0.5",
        "-queue_cv_sample_us", "1",
        "-lb", variant.lb,
    ]
    if variant.lb == "n-mrc":
        command += [
            "-nmrc_ev_mode", variant.ev_mode,
            "-nmrc_reroute_policy", variant.policy,
            "-nmrc_fastcnp", "on",
        ]
    elif variant.lb == "mrc":
        command += [
            "-mrc_cooldown_mode", "one_cycle",
            "-mrc_all_cooling_fallback", "earliest",
        ]
    elif variant.lb == "reps":
        command += ["-reps_buffer", "8"]
    if scenario.degraded:
        command += [
            "-slow_tor_uplinks", str(max(1, args.nodes // 10)),
            "-slow_tor_uplink_divisor", "2",
            "-slow_tor_uplink_select", "random-sparse",
        ]
    if scenario.path_hotspot:
        command += [
            # Leave exactly two clean paths when possible.  A packet on a hot
            # path then has two strictly better candidates, which separates
            # any_better from the better_ge3 guard in a controlled workload.
            "-path_hotspot_spines", str(max(1, paths - 2)),
            "-path_hotspot_bg_rate_gbps", "300",
            "-path_hotspot_bg_on_us", "1000",
            "-path_hotspot_bg_off_us", "0",
        ]
    return command


def selected_items(items, raw, attr="key"):
    if not raw:
        return tuple(items)
    wanted = set(parse_csv(raw))
    selected = tuple(item for item in items if getattr(item, attr) in wanted)
    found = {getattr(item, attr) for item in selected}
    missing = wanted - found
    if missing:
        raise ValueError("unknown selections: " + ", ".join(sorted(missing)))
    return selected


def make_specs(args):
    shape = generated_fattree_shape(args.nodes)
    paths = args.paths or shape["paths"]
    if paths != shape["paths"]:
        raise ValueError(
            f"--paths={paths} does not match the generated topology's "
            f"physical path count {shape['paths']}")
    scenarios = selected_items(SCENARIOS, args.scenarios)
    variants = selected_items(variant_matrix(paths), args.variants)
    specs = []
    for seed in args.seeds:
        for scenario in scenarios:
            flows = flows_for(
                scenario, args.nodes, args.flow_size, seed, args.quick)
            traffic_file = args.out / "traffic" / f"{scenario.key}_seed{seed}.cm"
            write_traffic(traffic_file, args.nodes, flows)
            traffic_sha = sha256_file(traffic_file)
            for variant in variants:
                case_dir = (args.out / "raw" / scenario.key / variant.key /
                            f"seed_{seed}")
                command = build_command(
                    args, scenario, variant, seed, paths, traffic_file,
                    case_dir / "logout.dat", len(flows))
                specs.append({
                    "scenario": scenario,
                    "variant": variant,
                    "seed": seed,
                    "paths": paths,
                    "ev_set_size": (
                        32 if variant.ev_mode == "random32" else
                        min(paths, 32) if variant.lb == "n-mrc" else 0
                    ),
                    "flows_data": flows,
                    "traffic_file": traffic_file,
                    "traffic_sha256": traffic_sha,
                    "case_dir": case_dir,
                    "command": command,
                })
    return specs


def validate_specs(specs):
    required = {
        "-queue_type": "composite_ecn_lb",
        "-host_queue_type": "prio",
        "-cc": "dcqcn_variant",
        "-roce_rx_mode": "sp",
        "-roce_transport_semantics": "mrc_exact_bounded",
        "-roce_trim_recovery": "exact",
    }
    traffic_by_block = {}
    for spec in specs:
        command = spec["command"]
        for flag, value in required.items():
            if flag not in command or command[command.index(flag) + 1] != value:
                raise ValueError(f"{spec['variant'].key} lacks {flag}={value}")
        if spec["variant"].lb == "n-mrc":
            for flag in ("-nmrc_ev_mode", "-nmrc_reroute_policy",
                         "-nmrc_fastcnp"):
                if flag not in command:
                    raise ValueError(f"hybrid command lacks {flag}")
        elif spec["variant"].lb == "mrc":
            for flag, value in (
                    ("-mrc_cooldown_mode", "one_cycle"),
                    ("-mrc_all_cooling_fallback", "earliest")):
                if (flag not in command or
                        command[command.index(flag) + 1] != value):
                    raise ValueError(f"mrc command lacks {flag}={value}")
        elif spec["variant"].lb == "reps":
            flag, value = "-reps_buffer", "8"
            if (flag not in command or
                    command[command.index(flag) + 1] != value):
                raise ValueError(f"reps command lacks {flag}={value}")
        block = (spec["scenario"].key, spec["seed"])
        previous = traffic_by_block.setdefault(block, spec["traffic_sha256"])
        if previous != spec["traffic_sha256"]:
            raise ValueError(f"traffic differs within block {block}")


def write_commands(path, specs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("scenario\tvariant\tseed\tpaths\ttraffic_sha256\tcommand\n")
        for spec in specs:
            handle.write(
                f"{spec['scenario'].key}\t{spec['variant'].key}\t"
                f"{spec['seed']}\t{spec['paths']}\t"
                f"{spec['traffic_sha256']}\t{shlex.join(spec['command'])}\n")


def percentile(values, quantile):
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low = int(position)
    high = min(len(ordered) - 1, low + 1)
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def jain_fairness(values):
    if not values:
        return 0.0
    total = sum(values)
    squares = sum(value * value for value in values)
    return total * total / (len(values) * squares) if squares else 0.0


def parse_number(raw):
    try:
        value = float(raw)
        return int(value) if value.is_integer() else value
    except ValueError:
        return 0


def parse_diag(text, prefix, fields):
    result = {field: 0 for field in fields}
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        for key, raw in KEY_VALUE_RE.findall(line):
            if key in result:
                result[key] = parse_number(raw)
    return result


def source_id_map(idmap_file, flows):
    sources = []
    seen = set()
    if not idmap_file.exists():
        return {}
    for line in idmap_file.read_text(errors="ignore").splitlines():
        match = IDMAP_RE.match(line)
        if not match or int(match.group(1)) in seen:
            continue
        seen.add(int(match.group(1)))
        sources.append((int(match.group(1)), int(match.group(2)),
                        int(match.group(3))))
    if len(sources) != len(flows):
        return {}
    for (_source_id, src, dst), flow in zip(sources, flows):
        if src != flow.src or dst != flow.dst:
            return {}
    return {item[0]: flow for item, flow in zip(sources, flows)}


def parse_run(spec, returncode, runtime_s):
    case_dir = spec["case_dir"]
    stdout_file = case_dir / "stdout.log"
    idmap_file = case_dir / "idmap.txt"
    text = stdout_file.read_text(errors="ignore") if stdout_file.exists() else ""
    id_to_flow = source_id_map(idmap_file, spec["flows_data"])
    target_fcts = []
    target_flow_goodputs = []
    target_finishes = []
    background_fcts = []
    for match in FINISH_RE.finditer(text):
        source_id = int(match.group(3))
        flow = id_to_flow.get(source_id)
        if flow is None:
            continue
        finish_us = float(match.group(4))
        fct = finish_us - flow.start_us
        if flow.role == "background":
            background_fcts.append(fct)
        else:
            target_fcts.append(fct)
            target_finishes.append(finish_us)
            if fct > 0:
                target_flow_goodputs.append(flow.size * 8.0 / fct / 1000.0)

    target_flows = [flow for flow in spec["flows_data"] if flow.role == "target"]
    background_flows = [flow for flow in spec["flows_data"]
                        if flow.role == "background"]
    cct_us = 0.0
    if target_finishes:
        cct_us = max(target_finishes) - min(flow.start_us for flow in target_flows)
    total_target_bytes = sum(flow.size for flow in target_flows)
    row = {
        "scenario": spec["scenario"].key,
        "variant": spec["variant"].key,
        "variant_label": spec["variant"].label,
        "seed": spec["seed"],
        "paths": spec["paths"],
        "traffic_sha256": spec["traffic_sha256"],
        "returncode": returncode,
        "completed": len(target_fcts),
        "flows": len(target_flows),
        "background_completed": len(background_fcts),
        "background_flows": len(background_flows),
        "avg_fct_us": sum(target_fcts) / len(target_fcts) if target_fcts else 0.0,
        "p50_fct_us": percentile(target_fcts, 0.50),
        "p95_fct_us": percentile(target_fcts, 0.95),
        "p99_fct_us": percentile(target_fcts, 0.99),
        "p999_fct_us": percentile(target_fcts, 0.999),
        "max_fct_us": max(target_fcts) if target_fcts else 0.0,
        "cct_us": cct_us,
        "goodput_gbps": (total_target_bytes * 8.0 / cct_us / 1000.0
                           if cct_us else 0.0),
        "flow_goodput_jain": jain_fairness(target_flow_goodputs),
        "runtime_s": runtime_s,
        "stdout": str(stdout_file),
        "command": shlex.join(spec["command"]),
    }
    row.update(parse_diag(text, "RoceDiag ", ROCE_FIELDS))
    row.update(parse_diag(text, "QueueDiag ", QUEUE_FIELDS))
    row.update(parse_diag(text, "QueueCvDiag ", QUEUE_CV_FIELDS))
    row.update(parse_diag(text, "HybridNmrcDiag", HYBRID_FIELDS))
    row.update(parse_diag(text, "NetawareDiag ", NETAWARE_FIELDS))
    row.update(parse_diag(text, "SglbRouteDiag ", SGLB_FIELDS))
    row.update(parse_diag(text, "BoundedRecoveryDiag ", BOUNDED_FIELDS))
    transition_match = re.search(
        r"(?:^|\s)level_transitions=([0-9,/]+)", text)
    row["level_transitions"] = (
        transition_match.group(1) if transition_match else "")
    new_packets = 0
    retx_packets = 0
    for match in NEW_RETX_RE.finditer(text):
        new_packets, retx_packets = int(match.group(1)), int(match.group(2))
    row["new_packets"] = new_packets
    row["retx_packets"] = retx_packets
    row["retx_ratio"] = retx_packets / float(new_packets) if new_packets else 0.0
    expected_ev_slots = len(spec["flows_data"]) * spec["ev_set_size"]
    row["ev_coverage_ratio"] = (
        row["observed_evs"] / float(expected_ev_slots)
        if expected_ev_slots else 0.0)
    row["fastcnp_packet_amplification"] = (
        row["fastcnp_arrived"] / float(new_packets) if new_packets else 0.0)
    row["fastcnp_effective_cooling_ratio"] = (
        row["cooldown_starts"] / float(row["fastcnp_arrived"])
        if row["fastcnp_arrived"] else 0.0)
    config_tokens = (
        f"lb mode {spec['variant'].lb}",
        "cc mode dcqcn_variant",
        "RoCE receive mode sp",
        "RoceTransportConfig semantics=mrc_exact_bounded",
        "awnd=cwnd_minus_inflight",
        "FinalCcMrcConfig dcqcn_variant_inflate=disabled",
    )
    if spec["variant"].lb == "n-mrc":
        config_tokens += (
            f"HybridNmrcConfig ev_mode={spec['variant'].ev_mode} ",
            f"reroute_policy={spec['variant'].policy} fastcnp=on",
        )
    elif spec["variant"].lb == "mrc":
        config_tokens += (
            "MrcCooldownDiag mrc_cooldown_mode=one_cycle ",
            "MrcFallbackDiag mrc_all_cooling_fallback=earliest",
        )
    elif spec["variant"].lb == "reps":
        config_tokens += (
            "reps buffer size 8",
            "REPS warmup exploration ",
            " packets (1BDP)",
        )
    row["config_ok"] = int(
        returncode == 0 and all(token in text for token in config_tokens))
    row["all_flows_completed"] = int(
        len(target_fcts) == len(target_flows) and
        len(background_fcts) == len(background_flows))
    return row


def reusable_result(args, case_dir, command_text, fingerprint):
    if args.force:
        return False
    command_file = case_dir / "command.txt"
    fingerprint_file = case_dir / "fingerprint.txt"
    required = (
        command_file, case_dir / "stdout.log", case_dir / "returncode.txt",
        case_dir / "runtime.txt", fingerprint_file, case_dir / "idmap.txt")
    return (all(path.exists() for path in required) and
            command_file.read_text().strip() == command_text and
            fingerprint_file.read_text().strip() == fingerprint)


def run_spec(spec, args):
    case_dir = spec["case_dir"]
    case_dir.mkdir(parents=True, exist_ok=True)
    command_text = shlex.join(spec["command"])
    command_file = case_dir / "command.txt"
    stdout_file = case_dir / "stdout.log"
    returncode_file = case_dir / "returncode.txt"
    runtime_file = case_dir / "runtime.txt"
    fingerprint_file = case_dir / "fingerprint.txt"
    simulator_sha = sha256_file(args.sim)
    fingerprint = hashlib.sha256((
        simulator_sha + "\0" + spec["traffic_sha256"] + "\0" + command_text
    ).encode()).hexdigest()
    if reusable_result(args, case_dir, command_text, fingerprint):
        row = parse_run(spec, int(returncode_file.read_text()),
                        float(runtime_file.read_text()))
        if row["config_ok"] and row["all_flows_completed"]:
            print(f"cached {spec['scenario'].key} {spec['variant'].key} "
                  f"seed={spec['seed']}", flush=True)
            return row

    command_file.write_text(command_text + "\n", encoding="utf-8")
    fingerprint_file.write_text(fingerprint + "\n", encoding="utf-8")
    print(f"run {spec['scenario'].key} {spec['variant'].key} "
          f"seed={spec['seed']}", flush=True)
    started = time.monotonic()
    try:
        with stdout_file.open("w", encoding="utf-8") as handle:
            process = subprocess.run(
                spec["command"], cwd=case_dir, stdout=handle,
                stderr=subprocess.STDOUT, timeout=args.timeout, check=False)
        returncode = process.returncode
    except subprocess.TimeoutExpired:
        returncode = 124
        with stdout_file.open("a", encoding="utf-8") as handle:
            handle.write(f"\nTIMEOUT after {args.timeout}s\n")
    runtime_s = time.monotonic() - started
    returncode_file.write_text(f"{returncode}\n", encoding="utf-8")
    runtime_file.write_text(f"{runtime_s:.6f}\n", encoding="utf-8")
    row = parse_run(spec, returncode, runtime_s)
    print(f"done {spec['scenario'].key} {spec['variant'].key} "
          f"{row['completed']}/{row['flows']} p99={row['p99_fct_us']:.3f}us",
          flush=True)
    return row


def write_summary(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=SUMMARY_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, 0) for field in SUMMARY_FIELDS})


def aggregate(rows):
    valid = [row for row in rows if row["config_ok"] and
             row["all_flows_completed"]]
    minima = {}
    for row in valid:
        key = (row["scenario"], row["seed"])
        value = row["p99_fct_us"]
        if value > 0:
            minima[key] = min(minima.get(key, value), value)
    grouped = {}
    for row in valid:
        key = row["variant"]
        item = grouped.setdefault(key, {
            "label": row["variant_label"], "runs": 0, "norm_p99": [],
            "p99": [], "goodput": [], "reroutes": 0, "fastcnp": 0,
            "fairness": [], "queue_p99": [], "coverage": [],
            "amplification": [], "ecn": 0, "trim": 0, "nacks": 0,
            "rtos": 0,
        })
        block_min = minima.get((row["scenario"], row["seed"]), 0)
        item["runs"] += 1
        if block_min:
            item["norm_p99"].append(row["p99_fct_us"] / block_min)
        item["p99"].append(row["p99_fct_us"])
        item["goodput"].append(row["goodput_gbps"])
        item["fairness"].append(row["flow_goodput_jain"])
        item["queue_p99"].append(row["spine_queue_p99_fraction"])
        if row["variant"].startswith("nmrc_"):
            item["coverage"].append(row["ev_coverage_ratio"])
            item["amplification"].append(
                row["fastcnp_packet_amplification"])
        item["reroutes"] += row["reroutes"]
        item["fastcnp"] += row["fastcnp_arrived"]
        item["ecn"] += row["composite_ecn_marks"]
        item["trim"] += row["composite_trims"]
        item["nacks"] += row["nacks"]
        item["rtos"] += row["rtos"]
    result = []
    for key, item in grouped.items():
        result.append({
            "variant": key,
            "label": item["label"],
            "runs": item["runs"],
            "mean_normalized_p99": (sum(item["norm_p99"]) /
                                      len(item["norm_p99"]) if
                                      item["norm_p99"] else 0.0),
            "mean_p99_us": sum(item["p99"]) / len(item["p99"]),
            "mean_goodput_gbps": sum(item["goodput"]) / len(item["goodput"]),
            "mean_flow_goodput_jain": (
                sum(item["fairness"]) / len(item["fairness"])),
            "mean_queue_p99_fraction": (
                sum(item["queue_p99"]) / len(item["queue_p99"])),
            "mean_ev_coverage_ratio": (
                sum(item["coverage"]) / len(item["coverage"])
                if item["coverage"] else 0.0),
            "mean_fastcnp_amplification": (
                sum(item["amplification"]) / len(item["amplification"])
                if item["amplification"] else 0.0),
            "reroutes": item["reroutes"],
            "fastcnp_arrived": item["fastcnp"],
            "ecn_marks": item["ecn"],
            "trims": item["trim"],
            "nacks": item["nacks"],
            "rtos": item["rtos"],
        })
    return sorted(result, key=lambda item: (
        item["mean_normalized_p99"] or float("inf"), item["variant"]))


def guardrail_selection(rows):
    nmrc_rows = [row for row in rows
                 if row["variant"].startswith("nmrc_")]
    required_blocks = {(row["scenario"], row["seed"])
                       for row in nmrc_rows}
    valid = [row for row in nmrc_rows if row["config_ok"] and
             row["all_flows_completed"]]
    if not valid:
        return None, []
    healthy_blocks = {block for block in required_blocks if
                      block[0].startswith("healthy_")}
    best_p99 = {}
    best_goodput = {}
    for row in valid:
        block = (row["scenario"], row["seed"])
        p99 = row["p99_fct_us"]
        if p99 > 0:
            best_p99[block] = min(best_p99.get(block, p99), p99)
        goodput = row["goodput_gbps"]
        best_goodput[block] = max(best_goodput.get(block, 0.0), goodput)

    by_variant = {}
    for row in valid:
        by_variant.setdefault(row["variant"], []).append(row)
    candidates = []
    audit = []
    for variant, variant_rows in by_variant.items():
        variant_blocks = {(row["scenario"], row["seed"])
                          for row in variant_rows}
        complete = variant_blocks == required_blocks
        healthy_regressions = [
            row["p99_fct_us"] / best_p99[(row["scenario"], row["seed"])] - 1.0
            for row in variant_rows
            if (row["scenario"], row["seed"]) in healthy_blocks and
            best_p99.get((row["scenario"], row["seed"]), 0) > 0
        ]
        max_healthy_regression = max(healthy_regressions or [0.0])
        goodput_ratios = [
            row["goodput_gbps"] /
            best_goodput[(row["scenario"], row["seed"])]
            for row in variant_rows
            if best_goodput.get((row["scenario"], row["seed"]), 0) > 0
        ]
        mean_goodput_ratio = (sum(goodput_ratios) / len(goodput_ratios)
                              if goodput_ratios else 0.0)
        recovery_ok = all(row["rtos"] == 0 for row in variant_rows)
        routing_scores = [
            row["p99_fct_us"] / best_p99[(row["scenario"], row["seed"])]
            for row in variant_rows if row["scenario"] != "incast_recovery" and
            best_p99.get((row["scenario"], row["seed"]), 0) > 0
        ]
        routing_score = (sum(routing_scores) / len(routing_scores)
                         if routing_scores else float("inf"))
        amplification = sum(
            row["fastcnp_packet_amplification"] for row in variant_rows
        ) / len(variant_rows)
        passed = (complete and max_healthy_regression <= 0.05 and
                  mean_goodput_ratio >= 0.95 and recovery_ok and
                  amplification <= 0.10)
        audit.append({
            "variant": variant, "passed": passed,
            "healthy_regression": max_healthy_regression,
            "goodput_ratio": mean_goodput_ratio,
            "routing_score": routing_score,
            "amplification": amplification,
        })
        if passed:
            candidates.append((routing_score, amplification, variant))
    candidates.sort()
    audit.sort(key=lambda item: item["variant"])
    return (candidates[0][2] if candidates else None), audit


def write_report(path, args, specs, rows, dry_run):
    paths = specs[0]["paths"] if specs else (args.paths or 0)
    variants = []
    for spec in specs:
        if spec["variant"].key not in variants:
            variants.append(spec["variant"].key)
    lines = [
        "# Hybrid n-MRC comparison",
        "",
        ("This is a dry-run command audit; no performance result was inferred."
         if dry_run else
         "All rows use the same traffic hash within each scenario/seed block."),
        "",
        "## Setup",
        "",
        f"- nodes: {args.nodes}; two-tier generated fat tree; physical paths P={paths}",
        f"- seeds: {', '.join(str(seed) for seed in args.seeds)}",
        f"- variants: {', '.join(variants)}",
        "- common stack: composite_ecn_lb, host prio, DCQCN variant, SP, "
        "exact bounded transport, exact trim recovery",
        "- FastCNP is PATH_REROUTE feedback only; downstream ECN echo remains enabled",
        "- random32 is omitted when P >= 32 because it is equivalent to "
        "random_matched at that path count",
        "- per-flow goodput fairness uses Jain's index; queue p99 is the "
        "sampled spine queue occupancy fraction",
        "- default selection uses completion/recovery, healthy p99, goodput, "
        "and a mean FastCNP/new-packet guardrail at or below 10% before "
        "routing-tail ranking",
        "",
    ]
    if dry_run:
        lines += [
            "## Audit",
            "",
            f"Validated {len(specs)} commands and wrote all traffic matrices.",
            "See `commands.tsv` for seeds, traffic SHA-256 hashes, and exact commands.",
        ]
    else:
        aggregates = aggregate(rows)
        lines += [
            "## Aggregate results",
            "",
            "Mean normalized p99 divides every run by the best valid p99 in the "
            "same scenario and seed; lower is better.",
            "",
            "| rank | variant | runs | norm p99 | p99 us | goodput | Jain | queue p99 | "
            "coverage | CNP/new | reroutes | FastCNP | NACK | RTO |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for rank, item in enumerate(aggregates, 1):
            lines.append(
                f"| {rank} | {item['variant']} | {item['runs']} | "
                f"{item['mean_normalized_p99']:.4f} | {item['mean_p99_us']:.3f} | "
                f"{item['mean_goodput_gbps']:.3f} | "
                f"{item['mean_flow_goodput_jain']:.4f} | "
                f"{item['mean_queue_p99_fraction']:.3f} | "
                f"{item['mean_ev_coverage_ratio']:.3f} | "
                f"{item['mean_fastcnp_amplification']:.5f} | "
                f"{item['reroutes']} | {item['fastcnp_arrived']} | "
                f"{item['nacks']} | {item['rtos']} |")
        selected, guardrail_audit = guardrail_selection(rows)
        lines += ["", "## Guardrail selection", ""]
        lines += [
            "A candidate must complete every block, have no RTO, stay within "
            "5% of the best n-MRC healthy p99, and retain at least 95% of the "
            "best n-MRC goodput, with mean FastCNP/new-packet amplification "
            "at or below 10%. Passing candidates are ranked by non-incast "
            "normalized p99, then control amplification.",
            "",
            "| variant | pass | max healthy regression | goodput ratio | routing score | CNP/new |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
        for item in guardrail_audit:
            lines.append(
                f"| {item['variant']} | {'yes' if item['passed'] else 'no'} | "
                f"{item['healthy_regression']:.4f} | {item['goodput_ratio']:.4f} | "
                f"{item['routing_score']:.4f} | {item['amplification']:.5f} |")
        if selected:
            lines += ["", f"Guardrail-selected n-MRC default: `{selected}`."]
        else:
            lines += ["", "No n-MRC candidate passed every guardrail; no default is inferred."]
        invalid = [row for row in rows if not (
            row["returncode"] == 0 and row["config_ok"] and
            row["all_flows_completed"])]
        if invalid:
            lines += [
                "",
                f"Caution: {len(invalid)} run(s) were invalid or incomplete and "
                "were excluded from ranking.",
            ]
        lines += [
            "",
            "The ranking is evidence for this topology/workload/seed set, not a "
            "general superiority claim. Inspect `summary.csv` and raw logs before "
            "changing the compiled defaults.",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", type=Path, default=DEFAULT_SIM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--nodes", type=int)
    parser.add_argument("--paths", type=int, default=0)
    parser.add_argument("--seeds", default="13")
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--variants", default="")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    args.nodes = args.nodes or (32 if args.quick else 2048)
    args.seeds = parse_csv(args.seeds, int)
    args.flow_size = 256 * 1024 if args.quick else 1024 * 1024
    args.end_us = 10000 if args.quick else 30000
    args.sim = args.sim.resolve()
    args.out = args.out.resolve()
    return args


def main(argv=None):
    args = parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    specs = make_specs(args)
    validate_specs(specs)
    write_commands(args.out / "commands.tsv", specs)
    if args.dry_run:
        write_summary(args.out / "summary.csv", [])
        write_report(
            args.out / "hybrid_nmrc_compare_for_gpt.md",
            args, specs, [], True)
        print(f"validated {len(specs)} commands")
        return 0
    if not args.sim.exists():
        raise FileNotFoundError(f"simulator not found: {args.sim}")
    rows = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(args.workers, len(specs)))) as executor:
        futures = [executor.submit(run_spec, spec, args) for spec in specs]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())
    scenario_order = {scenario.key: index for index, scenario in
                      enumerate(SCENARIOS)}
    variant_order = {variant.key: index for index, variant in
                     enumerate(variant_matrix(specs[0]["paths"]))}
    rows.sort(key=lambda row: (
        scenario_order[row["scenario"]], variant_order[row["variant"]],
        row["seed"]))
    write_summary(args.out / "summary.csv", rows)
    write_report(
        args.out / "hybrid_nmrc_compare_for_gpt.md",
        args, specs, rows, False)
    invalid = [row for row in rows if not (
        row["returncode"] == 0 and row["config_ok"] and
        row["all_flows_completed"])]
    print(args.out / "summary.csv")
    print(args.out / "hybrid_nmrc_compare_for_gpt.md")
    if invalid:
        print(f"warning: {len(invalid)} invalid or incomplete runs")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
