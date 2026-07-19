#!/usr/bin/env python3
"""Run the canonical six-workload, three-seed n-MRC comparison."""

import argparse
import concurrent.futures
import csv
import hashlib
import math
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import feedback_eval_common as common  # noqa: E402
import experiment_metrics as metrics  # noqa: E402
import run_dcqcn_narrow_trim_lb_compare as legacy_runner  # noqa: E402
import run_hybrid_nmrc_compare as hybrid_runner  # noqa: E402
import run_mrc_exact_bounded_recovery as bounded_runner  # noqa: E402


DEFAULT_SIM = ROOT / "sim/datacenter/htsim_roce"
DEFAULT_OUT = (
    ROOT / "experiments/n-mrc/output/"
    "nmrc_canonical_six_default_p8_3seed"
)
NODES = 128
TOPOLOGY = common.TOPOLOGIES[NODES]
CANONICAL_SEEDS = (13, 29, 47)

SCENARIOS = (
    "healthy_permutation_16mib",
    "asymmetric_permutation_16mib",
    "standard_websearch_proxy_80pct",
    "full_global_p16_256mib_background_off",
    "full_global_p16_256mib_background_on",
    "full_global_p4_64mib_background_off",
)
P2P_SCENARIOS = frozenset(SCENARIOS[:3])
SCHEMES = (
    "nmrc_encoded_better_ge3",
    "netaware",
    "sglb",
    "ar",
    "mrc",
    "reps",
)
LB_NAMES = {
    "nmrc_encoded_better_ge3": "n-mrc",
    "netaware": "netaware",
    "sglb": "sglb",
    "ar": "adaptive-routing",
    "mrc": "mrc",
    "reps": "reps",
}
SCHEME_ARGS = {
    "nmrc_encoded_better_ge3": (
        "-nmrc_ev_mode", "encoded",
        "-nmrc_reroute_policy", "better_ge3",
        "-nmrc_fastcnp", "on",
    ),
    "netaware": (
        "-netaware_weight_adaptation", "good_share_cap",
    ),
    "sglb": (),
    "ar": ("-ar_granularity", "packet"),
    "mrc": (
        "-mrc_cooldown_mode", "one_cycle",
        "-mrc_all_cooling_fallback", "earliest",
    ),
    "reps": ("-reps_buffer", "8"),
}
REPS_FIELDS = (
    "random_sends", "cached_sends", "cache_hit_ratio", "clean_ack_cached",
    "ecn_ack_discarded", "buffer_occupancy_samples",
)
LEVEL_TRANSITION_RE = re.compile(r"(?:^|\s)level_transitions=([^\s]+)")


def parse_csv(raw, cast=str):
    return tuple(cast(item.strip()) for item in raw.split(",") if item.strip())


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def materialize_traffic(out, scenario, seed):
    """Write one canonical traffic matrix and return its stable metadata."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    traffic_file = out / f"{scenario}_seed{seed}.cm"

    if scenario in (
            "healthy_permutation_16mib",
            "asymmetric_permutation_16mib"):
        flows = common.permutation_flows(
            TOPOLOGY, seed, 16 * common.MIB, 0)
        common.write_traffic_matrix(traffic_file, TOPOLOGY, flows)
        connections = len(flows)
        flows_data = flows
    elif scenario == "standard_websearch_proxy_80pct":
        flows = common.websearch_flows(TOPOLOGY, seed, 0.8, 10000)
        common.write_traffic_matrix(traffic_file, TOPOLOGY, flows)
        connections = len(flows)
        flows_data = flows
    else:
        parallel = 4 if scenario.startswith("full_global_p4_") else 16
        message_size = (
            64 * common.MIB if "_64mib_" in scenario
            else 256 * common.MIB
        )
        plan = common.alltoall_plan(
            NODES, NODES, parallel, message_size // NODES, 0, seed)
        common.write_alltoall_matrix(traffic_file, plan)
        connections = plan.connection_count
        flows_data = None

    return {
        "scenario": scenario,
        "kind": (
            "point_to_point" if scenario in P2P_SCENARIOS
            else "all_to_all"
        ),
        "seed": seed,
        "traffic_file": traffic_file,
        "traffic_sha256": file_sha256(traffic_file),
        "connections": connections,
        "flows_data": flows_data,
    }


def scenario_end_us(scenario):
    if scenario == "standard_websearch_proxy_80pct":
        return 40000
    if scenario in P2P_SCENARIOS:
        return 10000
    return 100000


def scenario_args(scenario):
    if scenario == "asymmetric_permutation_16mib":
        return (
            "-slow_tor_uplinks", "4",
            "-slow_tor_uplink_divisor", "2",
            "-slow_tor_uplink_select", "random-sparse",
        )
    if scenario == "full_global_p16_256mib_background_on":
        return tuple(common.alltoall_background_args(256 * common.MIB, True))
    return ()


def build_command(args, traffic, scheme, case_dir):
    command = [
        str(args.sim),
        "-o", str(case_dir / "logout.dat"),
        "-tm", str(traffic["traffic_file"]),
        "-nodes", str(NODES),
        "-conns", str(traffic["connections"]),
        "-tiers", "2",
        "-lb", LB_NAMES[scheme],
        "-linkspeed", "400000",
        "-queue_type", "composite_ecn_lb",
        "-host_queue_type", "prio",
        "-mtu", "4096",
        "-end", str(scenario_end_us(traffic["scenario"])),
        "-paths", "8",
        "-seed", str(traffic["seed"]),
        "-cc", "dcqcn_variant",
        "-roce_rx_mode", "sp",
        "-roce_sack_bitmap_bits", "64",
        "-roce_transport_semantics", "mrc_exact_bounded",
        "-roce_trim_recovery", "exact",
        "-hop_latency", "0.5",
        "-switch_latency", "0.5",
        "-queue_cv_sample_us", "100",
    ]
    command.extend(scenario_args(traffic["scenario"]))
    command.extend(SCHEME_ARGS[scheme])
    return command


def make_specs(args):
    specs = []
    traffic_dir = Path(args.out) / "traffic"
    for scenario in SCENARIOS:
        for seed in args.seeds:
            traffic = materialize_traffic(traffic_dir, scenario, seed)
            for scheme in SCHEMES:
                case_dir = (
                    Path(args.out) / "raw" / scenario / scheme /
                    f"seed_{seed}"
                )
                specs.append({
                    **traffic,
                    "scheme": scheme,
                    "lb_name": LB_NAMES[scheme],
                    "variant": "mrc_exact_bounded",
                    "cc_mode": "dcqcn_variant",
                    "inflate_diag": "disabled",
                    "trim_mode": "exact",
                    "case_dir": case_dir,
                    "command": build_command(
                        args, traffic, scheme, case_dir),
                    "expected_flows": traffic["connections"],
                })
    return specs


def option_value(command, option):
    if command.count(option) != 1:
        raise ValueError(f"expected exactly one {option}")
    index = command.index(option)
    if index + 1 >= len(command):
        raise ValueError(f"missing value for {option}")
    return command[index + 1]


def validate_specs(specs):
    expected = len(SCENARIOS) * len(SCHEMES) * 3
    if len(specs) != expected:
        raise ValueError(f"expected {expected} specs, got {len(specs)}")
    required = {
        "-nodes": "128",
        "-tiers": "2",
        "-linkspeed": "400000",
        "-queue_type": "composite_ecn_lb",
        "-host_queue_type": "prio",
        "-mtu": "4096",
        "-paths": "8",
        "-cc": "dcqcn_variant",
        "-roce_rx_mode": "sp",
        "-roce_sack_bitmap_bits": "64",
        "-roce_transport_semantics": "mrc_exact_bounded",
        "-roce_trim_recovery": "exact",
        "-hop_latency": "0.5",
        "-switch_latency": "0.5",
    }
    blocks = {}
    observed_seeds = set()
    for spec in specs:
        observed_seeds.add(spec["seed"])
        command = spec["command"]
        for option, value in required.items():
            if option_value(command, option) != value:
                raise ValueError(
                    f"{spec['scenario']}:{spec['scheme']} missing "
                    f"{option}={value}")
        if option_value(command, "-lb") != LB_NAMES[spec["scheme"]]:
            raise ValueError(f"wrong LB for {spec['scheme']}")
        if option_value(command, "-seed") != str(spec["seed"]):
            raise ValueError(f"wrong seed for {spec['scenario']}")
        if (spec["kind"] == "all_to_all" and
                option_value(command, "-end") != "100000"):
            raise ValueError("All-to-All cutoff must be 100000 us")
        if any(
                token.startswith("-nmrc_feedback_") or
                token.startswith("-netaware_feedback_")
                for token in command):
            raise ValueError("obsolete feedback cadence option")
        scheme_options = SCHEME_ARGS[spec["scheme"]]
        for index in range(0, len(scheme_options), 2):
            option = scheme_options[index]
            value = scheme_options[index + 1]
            if option_value(command, option) != value:
                raise ValueError(
                    f"{spec['scheme']} missing {option}={value}")
        block = (spec["scenario"], spec["seed"])
        item = blocks.setdefault(block, {
            "hashes": set(), "schemes": set(),
        })
        item["hashes"].add(spec["traffic_sha256"])
        item["schemes"].add(spec["scheme"])

    if observed_seeds != set(CANONICAL_SEEDS):
        raise ValueError(
            f"canonical seeds must be {CANONICAL_SEEDS}, got "
            f"{tuple(sorted(observed_seeds))}")
    if len(blocks) != len(SCENARIOS) * len(CANONICAL_SEEDS):
        raise ValueError(f"expected 18 scenario/seed blocks, got {len(blocks)}")
    for block, item in blocks.items():
        if len(item["hashes"]) != 1:
            raise ValueError(f"traffic differs within block {block}")
        if item["schemes"] != set(SCHEMES):
            raise ValueError(f"scheme matrix incomplete in block {block}")


def diagnostic_fields_present(text, prefix, fields):
    required = set(fields)
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        present = {key for key, _value in metrics.KEY_VALUE_RE.findall(line)}
        if required <= present:
            return True
    return False


def level_transition_raw(text):
    for line in text.splitlines():
        if not line.startswith("HybridNmrcDiag"):
            continue
        transition = LEVEL_TRANSITION_RE.search(line)
        if transition is not None:
            return transition.group(1)
    return ""


def valid_level_transitions(text):
    groups = level_transition_raw(text).split("/")
    return (
        len(groups) == 4 and
        all(
            len(group.split(",")) == 4 and
            all(cell.isdigit() for cell in group.split(","))
            for group in groups
        )
    )


def config_ok(text, spec, returncode):
    common_tokens = (
        f"lb mode {spec['lb_name']}",
        "cc mode dcqcn_variant",
        "queue_type 11",
        "host queue_type 4",
        "RoCE receive mode sp",
        "RoCE SACK bitmap 64 bits",
        "RoCE TRIM recovery mode exact",
        "RoceTransportConfig semantics=mrc_exact_bounded",
        "awnd=cwnd_minus_inflight",
        "exact_trim_attempt_id=on",
        "recovery_reserve_bytes=4096",
        "FinalCcMrcConfig dcqcn_variant_inflate=disabled",
        "roce_trim_recovery=exact",
        "BoundedRecoveryDiag semantics=mrc_exact_bounded",
    )
    scheme_tokens = {
        "nmrc_encoded_better_ge3": (
            "HybridNmrcConfig ev_mode=encoded ",
            "reroute_policy=better_ge3 fastcnp=on",
            "HybridNmrcDiag",
        ),
        "netaware": (
            "weight_adaptation good_share_cap",
            "NetawareDiag ",
        ),
        "sglb": ("SglbRouteDiag ",),
        "ar": ("Adaptive routing granularity packet",),
        "mrc": (
            "MrcCooldownDiag mrc_cooldown_mode=one_cycle",
            "MrcFallbackDiag mrc_all_cooling_fallback=earliest",
            "MrcDiag ",
        ),
        "reps": (
            "reps buffer size 8",
            "RepsLikeDiag ",
        ),
    }[spec["scheme"]]
    if spec["scenario"] == "full_global_p16_256mib_background_on":
        scheme_tokens += (
            "SGLB fixed-link background enabled",
            "SGLB background links per direction 2",
            "SGLB background rate 350Gbps",
            "SGLB background ON 400us",
            "SGLB background OFF 400us",
            "SGLB background installed 4 fixed-link sources",
        )
    complete_diagnostics = diagnostic_fields_present(
        text, "BoundedRecoveryDiag", bounded_runner.BOUNDED_FIELDS)
    if spec["scheme"] == "nmrc_encoded_better_ge3":
        complete_diagnostics = (
            complete_diagnostics and
            diagnostic_fields_present(
                text, "HybridNmrcDiag", hybrid_runner.HYBRID_FIELDS) and
            valid_level_transitions(text)
        )
    return int(
        returncode == 0 and complete_diagnostics and
        all(token in text for token in common_tokens + scheme_tokens)
    )


def source_ids_from_idmap(path):
    ids = set()
    if not Path(path).exists():
        return ids
    for line in Path(path).read_text(errors="ignore").splitlines():
        match = metrics.IDMAP_RE.fullmatch(line.strip())
        if match is not None:
            ids.add(int(match.group(1)))
    return ids


def parse_run(spec, returncode, elapsed_s):
    """Parse one run using the retained exact-bounded FCT/CCT semantics."""
    row = legacy_runner.parse_run(spec, returncode, elapsed_s)
    text = (spec["case_dir"] / "stdout.log").read_text(errors="ignore")
    finish_ids = [
        int(match.group(3)) for match in metrics.FINISH_RE.finditer(text)
    ]
    unique_finish_ids = set(finish_ids)
    expected_source_ids = source_ids_from_idmap(
        spec["case_dir"] / "idmap.txt")
    completion_ids_ok = (
        len(finish_ids) == len(unique_finish_ids) and
        len(expected_source_ids) == spec["expected_flows"] and
        unique_finish_ids == expected_source_ids
    )
    row.update(metrics.parse_key_values(
        text, "BoundedRecoveryDiag", bounded_runner.BOUNDED_FIELDS))
    row.update(metrics.parse_key_values(
        text, "HybridNmrcDiag", hybrid_runner.HYBRID_FIELDS))
    row.update(metrics.parse_key_values(
        text, "NetawareDiag", hybrid_runner.NETAWARE_FIELDS))
    row.update(metrics.parse_key_values(
        text, "SglbRouteDiag", hybrid_runner.SGLB_FIELDS))
    row.update(metrics.parse_key_values(
        text, "QueueCvDiag", hybrid_runner.QUEUE_CV_FIELDS))
    row.update(metrics.parse_key_values(text, "RepsLikeDiag", REPS_FIELDS))
    transition = level_transition_raw(text)
    row.update({
        "seed": spec["seed"],
        "traffic_sha256": spec["traffic_sha256"],
        "unique_completed": len(unique_finish_ids),
        "expected_source_ids": len(expected_source_ids),
        "completion_ids_ok": int(completion_ids_ok),
        "all_flows_completed": int(
            row["all_flows_completed"] and completion_ids_ok),
        "level_transitions": transition,
        "config_ok": config_ok(text, spec, returncode),
    })
    return row


def reusable_result(spec, args, command_text, fingerprint):
    if args.force:
        return False
    required = tuple(spec["case_dir"] / name for name in (
        "stdout.log", "command.txt", "returncode.txt", "runtime.txt",
        "fingerprint.txt", "idmap.txt",
    ))
    return (
        all(path.exists() for path in required) and
        (spec["case_dir"] / "command.txt").read_text().strip() == command_text and
        (spec["case_dir"] / "fingerprint.txt").read_text().strip() == fingerprint
    )


def run_spec(spec, args):
    case_dir = spec["case_dir"]
    case_dir.mkdir(parents=True, exist_ok=True)
    command_text = shlex.join(spec["command"])
    fingerprint = hashlib.sha256((
        file_sha256(args.sim) + "\0" + spec["traffic_sha256"] + "\0" +
        command_text
    ).encode()).hexdigest()
    if reusable_result(spec, args, command_text, fingerprint):
        row = parse_run(
            spec, int((case_dir / "returncode.txt").read_text()),
            float((case_dir / "runtime.txt").read_text()))
        if row["config_ok"] and row["all_flows_completed"]:
            print(
                f"cached {spec['scenario']} {spec['scheme']} "
                f"seed={spec['seed']}", flush=True)
            return row

    (case_dir / "command.txt").write_text(command_text + "\n", encoding="utf-8")
    (case_dir / "fingerprint.txt").write_text(fingerprint + "\n", encoding="ascii")
    print(
        f"run {spec['scenario']} {spec['scheme']} seed={spec['seed']}",
        flush=True)
    started = time.monotonic()
    try:
        with (case_dir / "stdout.log").open("w", encoding="utf-8") as handle:
            process = subprocess.run(
                spec["command"], cwd=case_dir, stdout=handle,
                stderr=subprocess.STDOUT, timeout=args.timeout, check=False)
        returncode = process.returncode
    except subprocess.TimeoutExpired:
        returncode = 124
        with (case_dir / "stdout.log").open("a", encoding="utf-8") as handle:
            handle.write(f"\nTIMEOUT after {args.timeout}s\n")
    elapsed_s = time.monotonic() - started
    (case_dir / "returncode.txt").write_text(f"{returncode}\n", encoding="ascii")
    (case_dir / "runtime.txt").write_text(f"{elapsed_s:.6f}\n", encoding="ascii")
    row = parse_run(spec, returncode, elapsed_s)
    print(
        f"done {spec['scenario']} {spec['scheme']} seed={spec['seed']} "
        f"primary={row['primary_us']:.3f}us "
        f"flows={row['completed']}/{row['flows']} runtime={elapsed_s:.1f}s",
        flush=True)
    return row


def geometric_mean(values):
    values = list(values)
    if not values or any(value <= 0 for value in values):
        return 0.0
    return math.exp(sum(math.log(value) for value in values) / len(values))


def aggregate(rows):
    validate_result_matrix(rows)
    valid = list(rows)
    minima = {}
    for row in valid:
        block = (row["scenario"], row["seed"])
        minima[block] = min(minima.get(block, row["primary_us"]),
                            row["primary_us"])
    result = []
    for scheme in SCHEMES:
        scheme_rows = [row for row in valid if row["scheme"] == scheme]
        ratios = [
            row["primary_us"] / minima[(row["scenario"], row["seed"])]
            for row in scheme_rows
        ]
        p2p = [
            ratio for row, ratio in zip(scheme_rows, ratios)
            if row["kind"] == "point_to_point"
        ]
        alltoall = [
            ratio for row, ratio in zip(scheme_rows, ratios)
            if row["kind"] == "all_to_all"
        ]
        result.append({
            "scheme": scheme,
            "raw_cells": len(scheme_rows),
            "normalized_geometric_mean": geometric_mean(ratios),
            "p2p_normalized_geometric_mean": geometric_mean(p2p),
            "alltoall_normalized_geometric_mean": geometric_mean(alltoall),
        })
    return result


def format_number(value):
    return f"{value:g}"


def summarize_three_seed_cells(rows):
    validate_result_matrix(rows)
    valid = list(rows)
    result = []
    for scenario in SCENARIOS:
        for scheme in SCHEMES:
            selected = sorted(
                (row for row in valid if row["scenario"] == scenario and
                 row["scheme"] == scheme), key=lambda row: row["seed"])
            values = sorted(row["primary_us"] for row in selected)
            if not values:
                continue
            middle = len(values) // 2
            median = (values[middle] if len(values) % 2 else
                      (values[middle - 1] + values[middle]) / 2.0)
            result.append({
                "scenario": scenario,
                "kind": selected[0]["kind"],
                "scheme": scheme,
                "primary_metric": selected[0]["primary_metric"],
                "seed_values": ";".join(
                    f"{row['seed']}:{format_number(row['primary_us'])}"
                    for row in selected),
                "primary_median_us": median,
                "primary_min_us": min(values),
                "primary_max_us": max(values),
                "raw_cells": len(values),
            })
    return result


def build_report(rows, revision):
    aggregates = sorted(
        aggregate(rows), key=lambda item: item["normalized_geometric_mean"])
    cells = summarize_three_seed_cells(rows)
    lines = [
        "# n-MRC Canonical Six Three-Seed Comparison",
        "",
        "128 nodes, P=8 physical paths, seeds 13/29/47. P2P and WebSearch "
        "use p99 FCT; All-to-All uses CCT. Lower is better.",
        "",
        "## Normalized ranking",
        "",
        "Each raw cell is divided by the best primary value in the same "
        "scenario/seed block; the table reports normalized geometric mean.",
        "",
        "| rank | scheme | raw cells | overall | P2P/WebSearch | All-to-All |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for rank, item in enumerate(aggregates, 1):
        lines.append(
            f"| {rank} | {item['scheme']} | {item['raw_cells']} | "
            f"{item['normalized_geometric_mean']:.4f} | "
            f"{item['p2p_normalized_geometric_mean']:.4f} | "
            f"{item['alltoall_normalized_geometric_mean']:.4f} |")
    lines += [
        "",
        "## Three-seed cells (median / min / max)",
        "",
        "| scenario | metric | scheme | median us | min us | max us |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for item in cells:
        lines.append(
            f"| {item['scenario']} | {item['primary_metric']} | "
            f"{item['scheme']} | {item['primary_median_us']:.3f} | "
            f"{item['primary_min_us']:.3f} | "
            f"{item['primary_max_us']:.3f} |")
    lines += [
        "",
        "## Per-seed primary results (108 raw cells)",
        "",
        "| scenario | metric | scheme | seed values (us) |",
        "| --- | --- | --- | --- |",
    ]
    for item in cells:
        lines.append(
            f"| {item['scenario']} | {item['primary_metric']} | "
            f"{item['scheme']} | {item['seed_values']} |")
    lines += [
        "",
        "All All-to-All schemes use the same 100000 us safety cutoff; "
        "completed CCT is unaffected by the extended cutoff.",
        "",
        "WebSearch is the repository's digitized proxy CDF, not the original "
        "REPS machine-readable trace.",
        "",
        f"Source revision: `{revision}`.",
    ]
    return "\n".join(lines) + "\n"


def write_csv(path, rows, delimiter=","):
    rows = list(rows)
    if not rows:
        raise ValueError(f"no rows for {path}")
    fieldnames = list(rows[0])
    for row in rows[1:]:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames,
                                delimiter=delimiter, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def transition_total(raw):
    return sum(int(value) for value in raw.replace("/", ",").split(",")
               if value)


def validate_result_matrix(rows):
    rows = list(rows)
    expected_identities = {
        (scenario, scheme, seed)
        for scenario in SCENARIOS
        for scheme in SCHEMES
        for seed in CANONICAL_SEEDS
    }
    identities = [
        (row.get("scenario"), row.get("scheme"), row.get("seed"))
        for row in rows
    ]
    if len(rows) != len(expected_identities):
        raise ValueError(
            f"expected {len(expected_identities)} result rows, got "
            f"{len(rows)}")
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate scenario/scheme/seed result identity")
    if set(identities) != expected_identities:
        missing = expected_identities - set(identities)
        extra = set(identities) - expected_identities
        raise ValueError(f"result matrix mismatch: missing={missing} extra={extra}")
    for row in rows:
        scenario = row["scenario"]
        expected_kind = (
            "point_to_point" if scenario in P2P_SCENARIOS else "all_to_all")
        expected_metric = (
            "p99_fct_us" if scenario in P2P_SCENARIOS
            else "all_to_all_cct_us")
        primary = row.get("primary_us", 0)
        if (
                row.get("kind") != expected_kind or
                row.get("primary_metric") != expected_metric or
                not isinstance(primary, (int, float)) or
                not math.isfinite(primary) or primary <= 0 or
                row.get("returncode") != 0 or not row.get("config_ok") or
                not row.get("all_flows_completed")):
            raise ValueError(
                "invalid result cell "
                f"{scenario}:{row['scheme']}:seed{row['seed']}")


def invalid_rows(rows):
    invalid = []
    for row in rows:
        ok = (
            row["returncode"] == 0 and row["config_ok"] and
            row["all_flows_completed"] and row["inflight_final"] == 0 and
            row["completion_ids_ok"] == 1 and
            math.isfinite(row["primary_us"]) and row["primary_us"] > 0 and
            row["recovery_inflight_final_bytes"] == 0 and
            row["recovery_inflight_max_bytes"] <= 4096
        )
        if row["scheme"] == "nmrc_encoded_better_ge3":
            ok = ok and (
                row["fastcnp_generated"] == row["fastcnp_arrived"] and
                row["fastcnp_after_done"] <= row["fastcnp_arrived"] and
                row["fastcnp_route_missing"] == 0 and
                row["fastcnp_unknown_qp"] == 0 and
                row["fastcnp_unknown_ev"] == 0 and
                transition_total(row["level_transitions"]) == row["reroutes"]
            )
        else:
            ok = ok and all(row[field] == 0 for field in (
                "reroutes", "fastcnp_generated", "fastcnp_arrived",
                "cooldown_starts",
            ))
        if not ok:
            invalid.append(row)
    return invalid


def main(argv=None):
    args = parse_args(argv)
    if not args.seeds:
        raise ValueError("--seeds must contain at least one seed")
    if args.workers < 1 or args.timeout <= 0:
        raise ValueError("workers and timeout must be positive")
    args.out.mkdir(parents=True, exist_ok=True)
    specs = make_specs(args)
    validate_specs(specs)
    write_csv(args.out / "commands.tsv", ({
        "scenario": spec["scenario"], "scheme": spec["scheme"],
        "seed": spec["seed"], "traffic_sha256": spec["traffic_sha256"],
        "command": shlex.join(spec["command"]),
    } for spec in specs), delimiter="\t")
    if args.dry_run:
        print(f"validated {len(specs)} commands")
        return 0
    if not args.sim.exists():
        raise FileNotFoundError(f"simulator not found: {args.sim}")

    rows = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(args.workers, len(specs))) as executor:
        futures = [executor.submit(run_spec, spec, args) for spec in specs]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())
    scenario_order = {scenario: index for index, scenario in enumerate(SCENARIOS)}
    scheme_order = {scheme: index for index, scheme in enumerate(SCHEMES)}
    rows.sort(key=lambda row: (
        scenario_order[row["scenario"]], scheme_order[row["scheme"]],
        row["seed"]))
    write_csv(args.out / "summary.csv", rows)
    validate_result_matrix(rows)
    invalid = invalid_rows(rows)
    if invalid:
        raise RuntimeError("invalid runs: " + ", ".join(
            f"{row['scenario']}:{row['scheme']}:seed{row['seed']}"
            for row in invalid))
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report = args.out / "nmrc_canonical_six_compare_for_gpt.md"
    report.write_text(build_report(rows, revision), encoding="utf-8")
    print(args.out / "summary.csv")
    print(report)
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", type=Path, default=DEFAULT_SIM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seeds", default="13,29,47")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.seeds = parse_csv(args.seeds, int)
    args.sim = args.sim.resolve()
    args.out = args.out.resolve()
    return args


if __name__ == "__main__":
    raise SystemExit(main())
