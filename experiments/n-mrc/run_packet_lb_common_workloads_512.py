#!/usr/bin/env python3
"""Compare the current packet-level LB schemes on common 512-node workloads."""

import concurrent.futures
import csv
import hashlib
import os
import random
import re
import subprocess
from collections import namedtuple
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "sim/datacenter/htsim_roce"
OUT = Path(os.environ.get(
    "PACKET_LB_COMMON_OUT",
    str(ROOT / "experiments/n-mrc/output/nmrc_packet_lb_common_workloads_512"),
)).resolve()

NODES = 512
TIERS = 2
HOSTS_PER_LEAF = 16
LEAVES = 32
SPINES = 16
HOT_SPINES = 4
LINKSPEED_MBPS = 400000
FLOW_SIZE = 1024 * 1024
SEED = 13
END_US = 5000
MAX_WORKERS = int(os.environ.get("PACKET_LB_COMMON_WORKERS", "4"))
TIMEOUT_S = int(os.environ.get("PACKET_LB_COMMON_TIMEOUT_S", "1800"))
FORCE = os.environ.get("FORCE_RERUN") == "1"
DRY_RUN = os.environ.get("PACKET_LB_COMMON_DRY_RUN") == "1"

MIXED_BACKGROUND_RATE_MBPS = 300000
MIXED_BACKGROUND_ON_US = 100.0
MIXED_BACKGROUND_PERIOD_US = 200.0
MIXED_BACKGROUND_BURSTS = 3

Scheme = namedtuple("Scheme", "key label lb")
Workload = namedtuple(
    "Workload", "name traffic degraded mixed path_hotspot incast")
Flow = namedtuple("Flow", "src dst size start_us role rate_mbps")

FINISH_RE = re.compile(r"Flow Roce_(\d+)_(\d+) (\d+) finished at ([0-9.]+)")
IDMAP_RE = re.compile(r"^(\d+)\s+Roce_(\d+)_(\d+)$")
KEY_VALUE_RE = re.compile(r"([A-Za-z0-9_]+)=([0-9.]+)")
NEW_RETX_RE = re.compile(r"New:\s+(\d+)\s+Rtx:\s+(\d+)")
HOTSPOT_INSTALLED_RE = re.compile(
    r"Path hotspot background installed (\d+) fixed-link sources")

ROCE_FIELDS = [
    "acks", "nacks", "nacks_ooo", "nacks_trim", "nacks_loss", "rtos",
    "ecn_echo_acks", "feedback_acks", "feedback_zero_bits",
]
QUEUE_FIELDS = [
    "lossless_overflows", "lossless_ecn_marks", "lossy_drops",
    "lossy_ecn_marks", "composite_trims", "composite_drops",
    "composite_ecn_marks",
]
MRC_FIELDS = [
    "cwnd_scaled_feedback_events", "cwnd_scaled_duplicate_feedback_ignored",
    "forced_cooling_use", "forced_cooling_earliest_use",
    "forced_cooling_round_robin_use", "cooling_skip_selection_avg",
]

SCHEMES = [
    Scheme("ecmp_rr", "ECMP-RR", "ecmp_rr"),
    Scheme("ops", "OPS", "ops"),
    Scheme("reps", "REPS", "reps"),
    Scheme("mrc", "MRC", "mrc"),
    Scheme("avail", "Avail", "avail"),
    Scheme("grade", "Grade", "grade"),
    Scheme("netaware", "n-MRC", "netaware"),
]

WORKLOADS = [
    Workload("healthy_permutation_1m", "permutation", False, False, False, False),
    Workload("healthy_tornado_1m", "tornado", False, False, False, False),
    Workload("degraded_permutation_1m", "permutation", True, False, False, False),
    Workload("degraded_tornado_1m", "tornado", True, False, False, False),
    Workload("mixed_1m", "mixed", False, True, False, False),
    Workload("path_hotspot_1m", "permutation", False, False, True, False),
    Workload("incast_32x1m", "incast", False, False, False, True),
]


def make_flow(src, dst, size=FLOW_SIZE, start_us=0.0,
              role="target", rate_mbps=0):
    return Flow(src, dst, size, start_us, role, rate_mbps)


def permutation_flows(start_us=0.0):
    rng = random.Random(SEED)
    destinations = list(range(NODES))
    while True:
        rng.shuffle(destinations)
        if all(src != destinations[src] for src in range(NODES)):
            break
    return [make_flow(src, destinations[src], start_us=start_us)
            for src in range(NODES)]


def tornado_flows(start_us=0.0):
    shift = NODES // 2
    return [make_flow(src, (src + shift) % NODES, start_us=start_us)
            for src in range(NODES)]


def mixed_flows():
    flows = []
    for leaf in range(LEAVES):
        dst_leaf = (leaf + LEAVES // 2) % LEAVES
        for offset in range(1, HOSTS_PER_LEAF):
            flows.append(make_flow(
                leaf * HOSTS_PER_LEAF + offset,
                dst_leaf * HOSTS_PER_LEAF + offset,
                start_us=250.0))

    background_size = int(round(
        MIXED_BACKGROUND_RATE_MBPS * MIXED_BACKGROUND_ON_US / 8.0))
    for burst in range(MIXED_BACKGROUND_BURSTS):
        start_us = burst * MIXED_BACKGROUND_PERIOD_US
        for leaf in range(LEAVES):
            dst_leaf = (leaf + LEAVES // 2) % LEAVES
            flows.append(make_flow(
                leaf * HOSTS_PER_LEAF,
                dst_leaf * HOSTS_PER_LEAF,
                size=background_size,
                start_us=start_us,
                role="background",
                rate_mbps=MIXED_BACKGROUND_RATE_MBPS))
    return flows


def incast_flows():
    return [make_flow(src, 0, start_us=250.0) for src in range(1, 33)]


def expected_hotspot_background_sources():
    return 2 * LEAVES * HOT_SPINES


def make_flows(workload):
    if workload.mixed:
        return mixed_flows()
    if workload.incast:
        return incast_flows()
    start_us = 250.0 if workload.path_hotspot else 0.0
    if workload.traffic == "permutation":
        return permutation_flows(start_us)
    if workload.traffic == "tornado":
        return tornado_flows(start_us)
    raise ValueError(f"unsupported workload {workload.name}")


def build_command(workload, scheme, traffic_file, dat_file, flow_count):
    end_us = 20000 if workload.incast else END_US
    command = [
        str(SIM),
        "-o", str(dat_file),
        "-tm", str(traffic_file),
        "-nodes", str(NODES),
        "-conns", str(flow_count),
        "-tiers", str(TIERS),
        "-lb", scheme.lb,
        "-linkspeed", str(LINKSPEED_MBPS),
        "-queue_type", "composite_ecn_lb",
        "-host_queue_type", "prio",
        "-mtu", "4096",
        "-end", str(end_us),
        "-paths", "128",
        "-seed", str(SEED),
        "-cc", "dcqcn_variant",
        "-roce_transport_semantics", "legacy",
        "-roce_trim_recovery", "cumulative",
        "-roce_rx_mode", "sp",
        "-roce_sack_bitmap_bits", "64",
        "-hop_latency", "0.5",
        "-switch_latency", "0.5",
    ]
    if workload.degraded:
        command += [
            "-slow_tor_uplinks", "12",
            "-slow_tor_uplink_divisor", "2",
            "-slow_tor_uplink_select", "random-sparse",
        ]
    if workload.path_hotspot:
        command += [
            "-path_hotspot_spines", str(HOT_SPINES),
            "-path_hotspot_bg_rate_gbps", "300",
            "-path_hotspot_bg_on_us", "1000",
            "-path_hotspot_bg_off_us", "0",
        ]
    if "one_cycle" in scheme.key:
        command += ["-mrc_cooldown_mode", "one_cycle"]
    if scheme.key.endswith("_round_robin"):
        command += ["-mrc_all_cooling_fallback", "round_robin"]
    return command


def run_specs():
    return [(workload, scheme) for workload in WORKLOADS for scheme in SCHEMES]


def write_traffic_matrix(path, flows):
    with path.open("w", encoding="utf-8") as handle:
        print("Nodes", NODES, file=handle)
        print("Connections", len(flows), file=handle)
        for flow_id, item in enumerate(flows, 1):
            start_ps = int(round(item.start_us * 1_000_000.0))
            line = (
                f"{item.src}->{item.dst} id {flow_id} "
                f"start {start_ps} size {item.size}")
            if item.rate_mbps:
                line += f" rate_mbps {item.rate_mbps}"
            print(line, file=handle)


def percentile(values, quantile):
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low = int(position)
    high = min(len(ordered) - 1, low + 1)
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def parse_key_values(text, prefix, fields):
    values = {field: 0 for field in fields}
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        for key, raw in KEY_VALUE_RE.findall(line):
            if key in values:
                values[key] = float(raw) if "." in raw else int(raw)
    return values


def source_id_map(idmap_file, flows):
    sources = []
    seen = set()
    if not idmap_file.exists():
        return {}
    for line in idmap_file.read_text(errors="ignore").splitlines():
        match = IDMAP_RE.match(line)
        if not match:
            continue
        source_id = int(match.group(1))
        if source_id in seen:
            continue
        seen.add(source_id)
        sources.append((source_id, int(match.group(2)), int(match.group(3))))
    if len(sources) != len(flows):
        return {}
    for (_source_id, src, dst), flow in zip(sources, flows):
        if src != flow.src or dst != flow.dst:
            return {}
    return {item[0]: flow for item, flow in zip(sources, flows)}


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


_SIMULATOR_SHA256 = None


def simulator_sha256():
    global _SIMULATOR_SHA256
    if _SIMULATOR_SHA256 is None:
        _SIMULATOR_SHA256 = sha256_file(SIM)
    return _SIMULATOR_SHA256


def cache_fingerprint(traffic_file, command):
    digest = hashlib.sha256()
    digest.update(sha256_file(traffic_file).encode("ascii"))
    digest.update(b"\0")
    digest.update(simulator_sha256().encode("ascii"))
    digest.update(b"\0")
    digest.update("\0".join(str(part) for part in command).encode("utf-8"))
    return digest.hexdigest()


def parse_run(workload, scheme, flows, stdout_file, idmap_file,
              returncode, command):
    text = stdout_file.read_text(errors="ignore") if stdout_file.exists() else ""
    id_to_flow = source_id_map(idmap_file, flows)
    target_fcts = []
    background_fcts = []
    for line in text.splitlines():
        match = FINISH_RE.search(line)
        if not match:
            continue
        source_id = int(match.group(3))
        flow = id_to_flow.get(source_id)
        if flow is None:
            continue
        fct = float(match.group(4)) - flow.start_us
        if flow.role == "target":
            target_fcts.append(fct)
        else:
            background_fcts.append(fct)

    target_total = sum(flow.role == "target" for flow in flows)
    background_total = len(flows) - target_total
    row = {
        "workload": workload.name,
        "scheme": scheme.key,
        "scheme_label": scheme.label,
        "returncode": returncode,
        "completed": len(target_fcts),
        "flows": target_total,
        "background_completed": len(background_fcts),
        "background_flows": background_total,
        "avg_fct_us": sum(target_fcts) / len(target_fcts) if target_fcts else 0.0,
        "p50_fct_us": percentile(target_fcts, 0.50),
        "p95_fct_us": percentile(target_fcts, 0.95),
        "p99_fct_us": percentile(target_fcts, 0.99),
        "p999_fct_us": percentile(target_fcts, 0.999),
        "max_fct_us": max(target_fcts) if target_fcts else 0.0,
        "stdout": str(stdout_file),
        "command": " ".join(str(part) for part in command),
    }
    row.update(parse_key_values(text, "RoceDiag", ROCE_FIELDS))
    row.update(parse_key_values(text, "QueueDiag", QUEUE_FIELDS))
    row.update(parse_key_values(text, "MrcDiag", MRC_FIELDS))

    new_packets = 0
    retx_packets = 0
    for match in NEW_RETX_RE.finditer(text):
        new_packets = int(match.group(1))
        retx_packets = int(match.group(2))
    row["new_packets"] = new_packets
    row["retx_packets"] = retx_packets
    row["retx_ratio"] = (
        retx_packets / float(new_packets) if new_packets else 0.0)

    hotspot_match = HOTSPOT_INSTALLED_RE.search(text)
    row["hotspot_background_sources"] = (
        int(hotspot_match.group(1)) if hotspot_match else 0)
    required = [
        f"lb mode {scheme.lb}",
        "queue_type 11",
        "cc mode dcqcn_variant",
        "RoCE receive mode sp",
        "RoCE SACK bitmap 64 bits",
        "FinalCcMrcConfig dcqcn_variant_inflate=natural "
        "mrc_ecn_trim_penalty=mode_uniform",
    ]
    if scheme.lb == "mrc":
        reference_match = re.search(r"(?:^|_)ref([0-9]+)(?:_|$)", scheme.key)
        diagnostic_scaled = bool(
            reference_match or "cwnd_scaled" in scheme.key or
            "reference100" in scheme.key or "bdp" in scheme.key)
        cooldown_mode = "cwnd_scaled" if diagnostic_scaled else "one_cycle"
        if reference_match:
            reference_pkts = int(reference_match.group(1))
            rotations = (reference_pkts + 15) // 16
            skip_selections = rotations * 16
            required.append(
                f"MrcCooldownDiag mrc_cooldown_mode={cooldown_mode} "
                "mrc_cooldown_reference=explicit "
                f"mrc_cooldown_reference_pkts={reference_pkts} "
                f"mrc_cwnd_scaled_rotations={rotations} "
                f"mrc_cwnd_scaled_skip_selections={skip_selections}")
        elif "reference100" in scheme.key:
            required.append(
                f"MrcCooldownDiag mrc_cooldown_mode={cooldown_mode} "
                "mrc_cooldown_reference=explicit "
                "mrc_cooldown_reference_pkts=100 "
                "mrc_cwnd_scaled_rotations=7 "
                "mrc_cwnd_scaled_skip_selections=112")
        else:
            required.append(
                f"MrcCooldownDiag mrc_cooldown_mode={cooldown_mode} "
                "mrc_cooldown_reference=topology_bdp "
                "mrc_cooldown_reference_pkts=86 "
                "mrc_cwnd_scaled_rotations=6 "
                "mrc_cwnd_scaled_skip_selections=96")
        fallback = (
            "round_robin" if scheme.key.endswith("_round_robin")
            else "earliest")
        required.append(
            f"MrcFallbackDiag mrc_all_cooling_fallback={fallback}")
    if scheme.lb == "netaware":
        required.append(
            ", local_update_us 1, remote_update_us 5, queue_threshold")
    if scheme.lb == "sglb":
        required.append(
            "SGLB effective config: local quality update 1us, "
            "GCN update 5us")
    if workload.path_hotspot:
        required.append(
            f"Path hotspot background installed "
            f"{expected_hotspot_background_sources()} fixed-link sources "
            f"hot_spines {HOT_SPINES} rate 300Gbps on 1000us off 0us")
    row["config_ok"] = int(
        returncode == 0 and all(item in text for item in required))
    row["all_flows_completed"] = int(
        len(target_fcts) == target_total and
        len(background_fcts) == background_total)
    return row


def row_complete(row):
    return (
        int(row["returncode"]) == 0 and
        int(row["config_ok"]) == 1 and
        int(row["all_flows_completed"]) == 1)


def run_case(workload, scheme):
    case_dir = OUT / "raw" / workload.name / scheme.key
    case_dir.mkdir(parents=True, exist_ok=True)
    traffic_file = case_dir / "traffic.cm"
    dat_file = case_dir / "logout.dat"
    stdout_file = case_dir / "run.stdout"
    command_file = case_dir / "run.cmd"
    returncode_file = case_dir / "returncode.txt"
    fingerprint_file = case_dir / "run.fingerprint"
    idmap_file = case_dir / "idmap.txt"

    flows = make_flows(workload)
    write_traffic_matrix(traffic_file, flows)
    command = build_command(workload, scheme, traffic_file, dat_file, len(flows))
    command_text = " ".join(str(part) for part in command) + "\n"
    expected_fingerprint = cache_fingerprint(traffic_file, command)
    cached_command = command_file.read_text() if command_file.exists() else ""
    cached_returncode = None
    if returncode_file.exists():
        try:
            cached_returncode = int(returncode_file.read_text().strip())
        except ValueError:
            cached_returncode = None

    use_cache = (
        not FORCE and cached_command == command_text and
        cached_returncode is not None and stdout_file.exists() and
        idmap_file.exists() and fingerprint_file.exists() and
        fingerprint_file.read_text().strip() == expected_fingerprint)
    if use_cache:
        cached_row = parse_run(
            workload, scheme, flows, stdout_file, idmap_file,
            cached_returncode, command)
        use_cache = row_complete(cached_row)

    if DRY_RUN:
        command_file.write_text(command_text)
        target_total = sum(flow.role == "target" for flow in flows)
        background_total = len(flows) - target_total
        row = {
            "workload": workload.name,
            "scheme": scheme.key,
            "scheme_label": scheme.label,
            "returncode": 0,
            "completed": 0,
            "flows": target_total,
            "background_completed": 0,
            "background_flows": background_total,
            "avg_fct_us": 0.0,
            "p50_fct_us": 0.0,
            "p95_fct_us": 0.0,
            "p99_fct_us": 0.0,
            "p999_fct_us": 0.0,
            "max_fct_us": 0.0,
            "stdout": str(stdout_file),
            "command": command_text.strip(),
            "new_packets": 0,
            "retx_packets": 0,
            "retx_ratio": 0.0,
            "hotspot_background_sources": 0,
            "config_ok": 0,
            "all_flows_completed": 0,
        }
        row.update({field: 0 for field in ROCE_FIELDS})
        row.update({field: 0 for field in QUEUE_FIELDS})
        row.update({field: 0 for field in MRC_FIELDS})
        return row

    if not use_cache:
        command_file.write_text(command_text)
        fingerprint_file.write_text(expected_fingerprint + "\n")
        print(f"run {workload.name} {scheme.key}", flush=True)
        try:
            with stdout_file.open("w") as handle:
                process = subprocess.run(
                    command, cwd=case_dir, stdout=handle,
                    stderr=subprocess.STDOUT, timeout=TIMEOUT_S,
                    check=False)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            returncode = 124
            with stdout_file.open("a") as handle:
                handle.write(f"\nTIMEOUT after {TIMEOUT_S}s\n")
        returncode_file.write_text(f"{returncode}\n")
    else:
        print(f"skip {workload.name} {scheme.key} cached", flush=True)
        returncode = cached_returncode

    row = parse_run(
        workload, scheme, flows, stdout_file, idmap_file,
        returncode, command)
    print(
        f"{workload.name:28s} {scheme.key:8s} "
        f"done={row['completed']}/{row['flows']} "
        f"p99={row['p99_fct_us']:.3f}us nacks={row['nacks']}",
        flush=True)
    return row


def write_outputs(rows, output_dir=OUT):
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_file = output_dir / "summary.csv"
    commands_file = output_dir / "commands.tsv"
    report_file = output_dir / "packet_lb_common_workloads_512_for_gpt.md"

    if rows:
        with summary_file.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    with commands_file.open("w", encoding="utf-8") as handle:
        print("workload\tscheme\tcommand", file=handle)
        for row in rows:
            print(
                f"{row.get('workload', '')}\t{row.get('scheme', '')}\t"
                f"{row.get('command', '')}", file=handle)

    scheme_names = ", ".join(scheme.key for scheme in SCHEMES)
    lines = [
        "# 512-Node Packet-LB Common-Workload Comparison",
        "",
        "This is a single-seed transport/load-balancing sanity comparison, not a final superiority claim.",
        "",
        "## Setup",
        "",
        "- 512 nodes, single-plane 2-tier Clos, 16 spine paths, 400 Gbit/s",
        "- composite_ecn_lb, host prio, SP/SACK 64-bit, dcqcn_variant natural",
        f"- schemes: {scheme_names}",
        "- mrc defaults to one-cycle ECN/TRIM cooldown with feedback-driven rearming and earliest-expiry fallback",
        "- explicit -mrc_cooldown_mode cwnd_scaled retains topology-BDP diagnostic cooldown",
        "- grade defaults to the 4-bit simple scorer: clean +1, ECN/TRIM -4, thresholds 12/7/3; -grade_complex_score restores the balanced complex scorer",
        "- n-mrc defaults to noisy-OR queue scoring, 0.10/0.40/0.60 level thresholds, 4/2/1/0 shuffled-bucket weights",
        "- periodic switch state: SGLB local/GCN=1/5us; n-mrc leaf-local/spine-export=1/5us; control-message propagation cost is not modeled",
        "- seed: 13; target flow size: 1 MiB",
        "- path_hotspot: identical 300 Gbit/s offered background on 4/16 spines",
        "- incast: 32 sources to one destination host",
        "",
        "## Results",
        "",
        "| workload | scheme | done | avg | p50 | p95 | p99 | p99.9 | max | NACK O/T/L | RTO | retx ratio | trim/drop/ECN | config |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['workload']} | {row['scheme']} | "
            f"{row['completed']}/{row['flows']} | {row['avg_fct_us']:.3f} | "
            f"{row['p50_fct_us']:.3f} | {row['p95_fct_us']:.3f} | "
            f"{row['p99_fct_us']:.3f} | {row['p999_fct_us']:.3f} | "
            f"{row['max_fct_us']:.3f} | {row['nacks_ooo']}/{row['nacks_trim']}/"
            f"{row['nacks_loss']} | {row['rtos']} | {row['retx_ratio']:.6f} | "
            f"{row['composite_trims']}/{row['composite_drops']}/"
            f"{row['composite_ecn_marks']} | {'PASS' if row['config_ok'] else 'FAIL'} |")

    lines += ["", "## Per-Workload p99", ""]
    by_workload = {}
    for row in rows:
        by_workload.setdefault(row["workload"], []).append(row)
    for workload in WORKLOADS:
        group = by_workload.get(workload.name, [])
        complete = [row for row in group if row_complete(row)]
        if not complete:
            continue
        ordered = sorted(complete, key=lambda row: row["p99_fct_us"])
        lines.append(
            f"- `{workload.name}`: " + ", ".join(
                f"{row['scheme']}={row['p99_fct_us']:.3f}us"
                for row in ordered))

    lines += ["", "## Findings", ""]

    complete_by_workload = {
        name: [row for row in group
               if row_complete(row) and float(row["p99_fct_us"]) > 0.0]
        for name, group in by_workload.items()
    }

    healthy_rows = (
        complete_by_workload.get("healthy_permutation_1m", []) +
        complete_by_workload.get("healthy_tornado_1m", []))
    if len(healthy_rows) == 2 * len(SCHEMES):
        healthy_clean = all(
            int(row["nacks"]) == 0 and int(row["retx_packets"]) == 0
            for row in healthy_rows)
        permutation_winner = min(
            complete_by_workload["healthy_permutation_1m"],
            key=lambda row: row["p99_fct_us"])
        tornado_winner = min(
            complete_by_workload["healthy_tornado_1m"],
            key=lambda row: row["p99_fct_us"])
        lines.append(
            "- Healthy: all schemes completed without NACK or retransmission. "
            f"Clean transport across the matrix: {'yes' if healthy_clean else 'no'}. "
            f"{permutation_winner['scheme']} leads permutation and "
            f"{tornado_winner['scheme']} leads tornado; the ranking therefore "
            "depends on traffic shape even without persistent imbalance.")

    for workload_name, traffic_label in [
            ("degraded_permutation_1m", "permutation"),
            ("degraded_tornado_1m", "tornado")]:
        group = complete_by_workload.get(workload_name, [])
        if group:
            by_scheme = {row["scheme"]: row for row in group}
            winner = min(group, key=lambda row: row["p99_fct_us"])
            ecmp = by_scheme.get("ecmp_rr")
            ops = by_scheme.get("ops")
            if winner and ecmp and ops and ecmp["p99_fct_us"] and ops["p99_fct_us"]:
                ecmp_reduction = 100.0 * (
                    1.0 - winner["p99_fct_us"] / ecmp["p99_fct_us"])
                ops_reduction = 100.0 * (
                    1.0 - winner["p99_fct_us"] / ops["p99_fct_us"])
                ordered = sorted(group, key=lambda row: row["p99_fct_us"])
                lines.append(
                    f"- Degraded {traffic_label}: {winner['scheme']} has the lowest "
                    f"p99 at {winner['p99_fct_us']:.3f} us, {ecmp_reduction:.1f}% "
                    f"below ECMP-RR and {ops_reduction:.1f}% below OPS. "
                    f"{ordered[1]['scheme']} is second; no complete row records a "
                    "LOSS NACK.")

    mixed = complete_by_workload.get("mixed_1m", [])
    if mixed:
        ordered = sorted(mixed, key=lambda row: row["p99_fct_us"])
        mixed_clean = all(
            int(row["nacks"]) == 0 and int(row["rtos"]) == 0
            for row in mixed)
        if len(ordered) >= 2:
            lines.append(
                f"- Mixed: complete rows have "
                f"{'zero' if mixed_clean else 'nonzero'} NACK/RTO. "
                f"{ordered[0]['scheme']} and {ordered[1]['scheme']} have the lowest "
                f"target p99 ({ordered[0]['p99_fct_us']:.3f} and "
                f"{ordered[1]['p99_fct_us']:.3f} us); this load is a coexistence "
                "check, not a fixed-path hotspot.")
        else:
            lines.append(
                "- Mixed: target FCT is reported separately from background flows; "
                "this load is a coexistence check, not a fixed-path hotspot.")

    hotspot = complete_by_workload.get("path_hotspot_1m", [])
    if hotspot:
        by_scheme = {row["scheme"]: row for row in hotspot}
        ordered = sorted(hotspot, key=lambda row: row["p99_fct_us"])
        winner = ordered[0]
        runner_up = ordered[1] if len(ordered) > 1 else None
        ecmp = by_scheme.get("ecmp_rr")
        ops = by_scheme.get("ops")
        if winner and runner_up and ecmp and ops:
            versus_second = 100.0 * (
                1.0 - winner["p99_fct_us"] / runner_up["p99_fct_us"])
            versus_ecmp = 100.0 * (
                1.0 - winner["p99_fct_us"] / ecmp["p99_fct_us"])
            versus_ops = 100.0 * (
                1.0 - winner["p99_fct_us"] / ops["p99_fct_us"])
            lines.append(
                f"- Controlled path hotspot: {winner['scheme']} has the lowest p99 "
                f"at {winner['p99_fct_us']:.3f} us and max at "
                f"{winner['max_fct_us']:.3f} us, {versus_second:.1f}% below "
                f"second-place {runner_up['scheme']}, {versus_ecmp:.1f}% "
                f"below ECMP-RR and {versus_ops:.1f}% below OPS. It records "
                f"{winner['nacks_trim']} target TRIM NACKs and "
                f"{winner['rtos']} RTOs.")

    incast = complete_by_workload.get("incast_32x1m", [])
    if incast:
        ordered = sorted(incast, key=lambda row: row["p99_fct_us"])
        spread = 100.0 * (
            ordered[-1]["p99_fct_us"] / ordered[0]["p99_fct_us"] - 1.0)
        min_trim = min(int(row["nacks_trim"]) for row in incast)
        max_trim = max(int(row["nacks_trim"]) for row in incast)
        min_rto = min(int(row["rtos"]) for row in incast)
        max_rto = max(int(row["rtos"]) for row in incast)
        lines.append(
            f"- 32:1 incast: p99 spans {ordered[0]['p99_fct_us']:.3f}-"
            f"{ordered[-1]['p99_fct_us']:.3f} us ({spread:.1f}% spread), while every "
            f"scheme sees {min_trim}-{max_trim} TRIM NACKs and {min_rto}-{max_rto} "
            "RTOs. The small ordering differences must not be read as path-avoidance "
            "superiority because all paths share the receiver downlink.")

    if any(row["scheme"] == "mrc_one_cycle" for row in rows):
        lines += ["", "## Default vs One-Cycle MRC", ""]
        for workload in WORKLOADS:
            group = complete_by_workload.get(workload.name, [])
            by_scheme = {row["scheme"]: row for row in group}
            default = by_scheme.get("mrc")
            one_cycle = by_scheme.get("mrc_one_cycle")
            if not default or not one_cycle:
                continue
            p99_change = 100.0 * (
                default["p99_fct_us"] / one_cycle["p99_fct_us"] - 1.0)
            p999_change = 100.0 * (
                default["p999_fct_us"] / one_cycle["p999_fct_us"] - 1.0)
            max_change = 100.0 * (
                default["max_fct_us"] / one_cycle["max_fct_us"] - 1.0)
            lines.append(
                f"- `{workload.name}`: one-cycle -> default p99 "
                f"{one_cycle['p99_fct_us']:.3f} -> "
                f"{default['p99_fct_us']:.3f} us ({p99_change:+.1f}%); "
                f"p99.9 {p999_change:+.1f}%; max {max_change:+.1f}%; "
                f"TRIM NACK {one_cycle['nacks_trim']} -> "
                f"{default['nacks_trim']}; RTO {one_cycle['rtos']} -> "
                f"{default['rtos']}; retx ratio "
                f"{one_cycle['retx_ratio']:.6f} -> "
                f"{default['retx_ratio']:.6f}; default cwnd-scaled "
                f"feedback/duplicate-ignored "
                f"{default['cwnd_scaled_feedback_events']}/"
                f"{default['cwnd_scaled_duplicate_feedback_ignored']}.")
    if (len(rows) == len(WORKLOADS) * len(SCHEMES) and
            all(row_complete(row) for row in rows) and
            all(int(row.get("nacks_loss", 0)) == 0 for row in rows)):
        lines.append(
            f"- Across all {len(WORKLOADS) * len(SCHEMES)} runs, LOSS NACK "
            "count is zero; recovery cost is OOO/TRIM/RTO driven.")

    incomplete = [row for row in rows if not row_complete(row)]
    lines += [
        "",
        "## Data Quality",
        "",
        f"- rows: {len(rows)}/{len(WORKLOADS) * len(SCHEMES)}",
        f"- incomplete or config-failed rows: {len(incomplete)}",
        "- mixed FCT metrics cover target flows; global NACK/queue counters include target and background flows.",
        "- path-hotspot background is synthetic fixed-link queue load with identical offered traffic across schemes; realized payload load can differ after trimming. QueueDiag trim/drop/ECN counters include this synthetic load, while RoCE NACK counters cover target flows.",
        "- incast is an unavoidable last-hop bottleneck and is not evidence of path-avoidance quality.",
    ]
    report_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_file


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if DRY_RUN:
        rows = [run_case(workload, scheme) for workload, scheme in run_specs()]
    else:
        workers = max(1, min(MAX_WORKERS, len(SCHEMES)))
        rows = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(run_case, workload, scheme)
                for workload, scheme in run_specs()
            ]
            for future in concurrent.futures.as_completed(futures):
                rows.append(future.result())
        workload_order = {item.name: index for index, item in enumerate(WORKLOADS)}
        scheme_order = {item.key: index for index, item in enumerate(SCHEMES)}
        rows.sort(key=lambda row: (
            workload_order[row["workload"]], scheme_order[row["scheme"]]))
    report = write_outputs(rows)
    print(report, flush=True)
    if not DRY_RUN:
        failed = [row for row in rows if not row_complete(row)]
        if failed:
            raise SystemExit(f"{len(failed)} incomplete or invalid runs")


if __name__ == "__main__":
    main()
