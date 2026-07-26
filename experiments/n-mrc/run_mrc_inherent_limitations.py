#!/usr/bin/env python3
"""Run and analyze the controlled MRC inherent-limitations experiments."""

import argparse
import concurrent.futures
import csv
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
from typing import NamedTuple


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import feedback_eval_common as common  # noqa: E402
import experiment_metrics as metrics  # noqa: E402


NODES = 128
PATHS = 8
CANONICAL_SEEDS = (13, 29, 47)
WEBSEARCH_DURATION_US = 10000
WEBSEARCH_END_US = 40000
ALLTOALL_END_US = 100000
DEFAULT_SIM = ROOT / "sim/datacenter/htsim_roce"
DEFAULT_OUT = (
    ROOT / "experiments/n-mrc/output/mrc_inherent_limitations_128"
)
COMPLETION_RE = re.compile(
    r"^\.*Flow Roce_(\d+)_(\d+)\s+(\d+) finished at ([0-9.]+) "
    r"total bytes (\d+) bg traffic ([01]) flowid (\d+)$",
    re.MULTILINE,
)


class CaseSpec(NamedTuple):
    experiment: str
    scenario: str
    family: str
    scheme: str
    seed: int
    traffic: str
    load_pct: int = 0
    parallel: int = 0
    active_evs: int = 0
    degraded: bool = False
    background: bool = False


class TrafficArtifact(NamedTuple):
    path: Path
    connections: int
    sha256: str
    serialized_bytes: int


def case_catalog(seeds=CANONICAL_SEEDS):
    cases = []
    for family, degraded in (("healthy", False), ("asymmetric", True)):
        for load in (40, 60, 80, 100):
            scenario = "exp1_{}_websearch_{}pct".format(family, load)
            for seed in seeds:
                for scheme in ("rr", "mrc"):
                    cases.append(CaseSpec(
                        "flow_lifetime", scenario, family, scheme, seed,
                        "websearch", load_pct=load, degraded=degraded,
                    ))
    for parallel in (4, 8, 16):
        scenario = "exp2_asymmetric_alltoall_256mib_p{}".format(parallel)
        for seed in seeds:
            for scheme in ("mrc", "mrc-shared"):
                cases.append(CaseSpec(
                    "per_qp_sharing", scenario, "asymmetric_alltoall",
                    scheme, seed, "alltoall", parallel=parallel,
                    degraded=True, background=True,
                ))
    for load in (40, 80):
        for active_evs in (2, 4, 8):
            scenario = "exp3_asymmetric_websearch_{}pct_k{}".format(
                load, active_evs)
            for seed in seeds:
                for scheme in ("rr", "mrc"):
                    cases.append(CaseSpec(
                        "active_ev_count", scenario, "asymmetric_websearch",
                        scheme, seed, "websearch", load_pct=load,
                        active_evs=active_evs, degraded=True,
                    ))
    return tuple(cases)


def smoke_catalog():
    cases = case_catalog((13,))
    selectors = (
        ("flow_lifetime", 40, 0, 0),
        ("per_qp_sharing", 0, 4, 0),
        ("active_ev_count", 40, 0, 2),
    )
    selected = []
    for experiment, load, parallel, active in selectors:
        selected.extend(
            case for case in cases
            if case.experiment == experiment and
            (not load or case.load_pct == load) and
            (not parallel or case.parallel == parallel) and
            (not active or case.active_evs == active) and
            (case.family != "asymmetric"
             if experiment == "flow_lifetime" else True)
        )
    return tuple(selected)


def traffic_key(case):
    if case.experiment == "active_ev_count":
        return "exp3_asymmetric_websearch_{}pct_seed{}".format(
            case.load_pct, case.seed)
    return "{}_seed{}".format(case.scenario, case.seed)


def geometric_mean(values):
    values = tuple(values)
    if not values or any(value <= 0 or not math.isfinite(value)
                         for value in values):
        raise ValueError("geometric mean requires positive finite values")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def seed_summary(rows, field):
    values = [float(row[field]) for row in rows]
    return {
        "geomean": geometric_mean(values),
        "min": min(values),
        "max": max(values),
    }


def classify_slowdown(row):
    if int(row["new_selections_after_first_update"]) <= 1:
        return "too_late"
    if int(row["post_cooldown_first_clean"]):
        return "stale_consistent"
    if (int(row["max_simultaneous_cooling"]) >= 2 and
            int(row["replacement_congestion"])):
        return "capacity_removal"
    if int(row["forced_cooling_uses"]):
        return "fallback_pressure"
    return "unexplained"


def pair_flow_rows(rows, baseline_scheme, treatment_scheme):
    identity_fields = (
        "scenario", "seed", "flow_id", "src", "dst", "flow_size"
    )
    indexed = {}
    for row in rows:
        if row["scheme"] not in (baseline_scheme, treatment_scheme):
            continue
        key = tuple(row[field] for field in identity_fields)
        scheme_rows = indexed.setdefault(key, {})
        if row["scheme"] in scheme_rows:
            raise ValueError("duplicate paired flow identity")
        scheme_rows[row["scheme"]] = row
    result = []
    for key, schemes in indexed.items():
        if set(schemes) != {baseline_scheme, treatment_scheme}:
            raise ValueError("missing paired flow for {}".format(key))
        baseline = schemes[baseline_scheme]
        treatment = schemes[treatment_scheme]
        ratio = treatment["fct_us"] / baseline["fct_us"]
        result.append({
            **{field: value for field, value in zip(identity_fields, key)},
            "baseline_scheme": baseline_scheme,
            "treatment_scheme": treatment_scheme,
            "baseline_fct_us": baseline["fct_us"],
            "treatment_fct_us": treatment["fct_us"],
            "fct_ratio": ratio,
            "slowdown_gt_1pct": int(ratio > 1.01),
            "slowdown_gt_5pct": int(ratio > 1.05),
            "slowdown_gt_10pct": int(ratio > 1.10),
            "exclusive_class": classify_slowdown(treatment),
            **{
                key: value for key, value in treatment.items()
                if key not in identity_fields
            },
        })
    return result


def validate_complete_cells(rows, expected_identities):
    actual = [row["identity"] for row in rows]
    if len(actual) != len(set(actual)):
        raise ValueError("duplicate result cell")
    missing = set(expected_identities) - set(actual)
    extra = set(actual) - set(expected_identities)
    if missing or extra:
        raise ValueError("missing={} extra={}".format(
            sorted(missing), sorted(extra)))
    invalid = [row["identity"] for row in rows if not row.get("valid")]
    if invalid:
        raise ValueError("invalid cells: {}".format(invalid))

MRC_FLOW_DIAG_INT_FIELDS = (
    "flow_id",
    "src",
    "dst",
    "dst_tor",
    "flow_size",
    "new_data_selections",
    "unique_active_evs",
    "full_sweeps",
    "unused_active_evs",
    "quality_feedback_before_done",
    "effective_state_updates",
    "packets_before_first_update",
    "new_selections_after_first_update",
    "actionable_feedback",
    "cooldown_starts",
    "failure_starts",
    "forced_cooling_uses",
    "max_simultaneous_cooling",
    "replacement_congestion",
    "post_cooldown_first_clean",
    "shared_updates_published",
    "shared_updates_consumed",
    "shared_updates_from_other_qps",
    "redundant_discoveries",
    "post_shared_bad_ev_sends",
)

MRC_FLOW_DIAG_FLOAT_FIELDS = (
    "start_us",
    "finish_us",
    "first_full_sweep_us",
    "first_state_update_us",
    "feedback_age_sum_us",
    "feedback_age_max_us",
)

MRC_FLOW_DIAG_FIELDS = (
    MRC_FLOW_DIAG_INT_FIELDS + MRC_FLOW_DIAG_FLOAT_FIELDS
)


def parse_mrc_flow_diags(text):
    """Parse and strictly validate all MrcFlowDiag records in simulator text."""
    records = []
    seen = set()
    expected = set(MRC_FLOW_DIAG_FIELDS)
    for line in text.splitlines():
        if not line.startswith("MrcFlowDiag "):
            continue
        raw = {}
        for token in line.split()[1:]:
            if "=" not in token:
                raise ValueError("malformed MrcFlowDiag token: " + token)
            key, value = token.split("=", 1)
            if key in raw:
                raise ValueError("duplicate MrcFlowDiag field: " + key)
            raw[key] = value
        actual = set(raw)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                "MrcFlowDiag missing={} extra={}".format(missing, extra)
            )
        record = {
            key: int(raw[key]) for key in MRC_FLOW_DIAG_INT_FIELDS
        }
        record.update({
            key: float(raw[key]) for key in MRC_FLOW_DIAG_FLOAT_FIELDS
        })
        flow_id = record["flow_id"]
        if flow_id in seen:
            raise ValueError("duplicate MrcFlowDiag flow_id {}".format(flow_id))
        seen.add(flow_id)
        records.append(record)
    return records


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def case_identity(case):
    return "{}:{}:{}:seed{}".format(
        case.experiment, case.scenario, case.scheme, case.seed)


def materialize_traffic(traffic_dir, case, smoke=False):
    traffic_dir = Path(traffic_dir)
    traffic_dir.mkdir(parents=True, exist_ok=True)
    path = traffic_dir / (traffic_key(case) + ".cm")
    if path.exists():
        first = path.read_text(encoding="utf-8").splitlines()[:2]
        connections = int(first[1].split()[1])
        return TrafficArtifact(
            path, connections, file_sha256(path), path.stat().st_size)

    topology = common.TOPOLOGIES[NODES]
    if smoke:
        if case.experiment == "per_qp_sharing":
            flows = [
                common.Flow(0, dst, 256 * 1024)
                for dst in (8, 9, 10, 11)
            ]
        else:
            flows = common.websearch_flows(
                topology, case.seed, case.load_pct / 100.0, 250)
        common.write_traffic_matrix(path, topology, flows)
        connections = len(flows)
    elif case.traffic == "websearch":
        flows = common.websearch_flows(
            topology, case.seed, case.load_pct / 100.0,
            WEBSEARCH_DURATION_US)
        common.write_traffic_matrix(path, topology, flows)
        connections = len(flows)
    else:
        plan = common.alltoall_plan(
            NODES, NODES, case.parallel,
            (256 * common.MIB) // NODES, 0, case.seed)
        manifest = common.write_alltoall_matrix(path, plan)
        connections = manifest.connections
    if connections <= 0:
        raise ValueError("traffic generation produced no flows")
    return TrafficArtifact(
        path, connections, file_sha256(path), path.stat().st_size)


def build_command(case, sim, traffic_file, case_dir, connections):
    end_us = (
        ALLTOALL_END_US if case.traffic == "alltoall"
        else WEBSEARCH_END_US
    )
    command = [
        str(sim),
        "-o", str(Path(case_dir) / "logout.dat"),
        "-tm", str(traffic_file),
        "-nodes", str(NODES),
        "-conns", str(connections),
        "-tiers", "2",
        "-lb", case.scheme,
        "-linkspeed", "400000",
        "-queue_type", "composite_ecn_lb",
        "-host_queue_type", "prio",
        "-mtu", "4096",
        "-end", str(end_us),
        "-paths", str(PATHS),
        "-seed", str(case.seed),
        "-cc", "dcqcn_variant",
        "-roce_rx_mode", "sp",
        "-roce_sack_bitmap_bits", "64",
        "-roce_transport_semantics", "mrc_exact_bounded",
        "-roce_trim_recovery", "exact",
        "-hop_latency", "0.5",
        "-switch_latency", "0.5",
    ]
    if case.active_evs:
        command.extend(("-mrc_active_evs", str(case.active_evs)))
    if case.degraded:
        command.extend((
            "-slow_tor_uplinks",
            str(common.slow_uplink_count(common.TOPOLOGIES[NODES])),
            "-slow_tor_uplink_divisor", "2",
            "-slow_tor_uplink_select", "random-sparse",
        ))
    if case.background:
        command.extend(common.alltoall_background_args(
            256 * common.MIB, True))
    return command


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent,
                prefix="." + path.name + ".", suffix=".tmp",
                delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def atomic_json(path, value):
    atomic_text(path, json.dumps(
        value, indent=2, sort_keys=True) + "\n")


def atomic_csv(path, rows):
    rows = list(rows)
    if not rows:
        atomic_text(path, "")
        return
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="", dir=path.parent,
                prefix="." + path.name + ".", suffix=".tmp",
                delete=False) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(
                handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def parse_completions(text):
    completions = {}
    for match in COMPLETION_RE.finditer(text):
        flow_id = int(match.group(7))
        if flow_id in completions:
            raise ValueError("duplicate completion flow_id {}".format(flow_id))
        completions[flow_id] = {
            "completion_src": int(match.group(1)),
            "completion_dst": int(match.group(2)),
            "finish_us": float(match.group(4)),
            "completed_bytes": int(match.group(5)),
            "background": int(match.group(6)),
        }
    return completions


def parse_shared_events(text):
    events = []
    for line in text.splitlines():
        if not line.startswith("MrcSharedEventDiag "):
            continue
        raw = dict(token.split("=", 1) for token in line.split()[1:])
        events.append({
            "time_us": float(raw["time_us"]),
            "flow_id": int(raw["flow_id"]),
            "src": int(raw["src"]),
            "dst_tor": int(raw["dst_tor"]),
            "ev": int(raw["ev"]),
            "signal": int(raw["signal"]),
            "generation": int(raw["generation"]),
        })
    return events


def config_ok(text, case):
    required = (
        "lb mode {}".format(case.scheme),
        "cc mode dcqcn_variant",
        "RoCE receive mode sp",
        "RoceTransportConfig semantics=mrc_exact_bounded",
    )
    if case.scheme == "rr":
        required += ("RR: stateless_mrc true",)
    else:
        required += ("MRC: paths 8",)
    if case.scheme == "mrc-shared":
        required += (
            "MrcSharedConfig enabled=1 key=source_nic,destination_tor,ev",
        )
    if case.active_evs:
        required += ("active_evs {}".format(case.active_evs),)
    return all(token in text for token in required)


def run_case(case, artifact, args):
    case_dir = (
        Path(args.out) / "raw" / case.experiment / case.scenario /
        case.scheme / "seed_{}".format(case.seed)
    )
    case_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(
        case, args.sim, artifact.path, case_dir, artifact.connections)
    command_text = shlex.join(command)
    fingerprint = hashlib.sha256((
        file_sha256(args.sim) + "\0" + artifact.sha256 + "\0" +
        command_text).encode()).hexdigest()
    summary_path = case_dir / "summary.json"
    flow_path = case_dir / "flow_metrics.json.gz"
    event_path = case_dir / "shared_events.json.gz"
    if not args.force and summary_path.exists() and flow_path.exists():
        cached = json.loads(summary_path.read_text(encoding="utf-8"))
        if cached.get("fingerprint") == fingerprint and cached.get("valid"):
            with gzip.open(flow_path, "rt", encoding="utf-8") as handle:
                flows = json.load(handle)
            events = []
            if event_path.exists():
                with gzip.open(event_path, "rt", encoding="utf-8") as handle:
                    events = json.load(handle)
            print("cached " + case_identity(case), flush=True)
            return cached, flows, events

    print("run " + case_identity(case), flush=True)
    started = time.monotonic()
    try:
        process = subprocess.run(
            command, cwd=case_dir, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=args.timeout,
            check=False,
        )
        returncode = process.returncode
        stdout = process.stdout
    except subprocess.TimeoutExpired as error:
        returncode = 124
        partial = error.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode(errors="replace")
        stdout = partial + "\nTIMEOUT\n"
    runtime_s = time.monotonic() - started
    with gzip.open(
            case_dir / "stdout.log.gz", "wt",
            encoding="utf-8", compresslevel=6) as handle:
        handle.write(stdout)
    atomic_text(case_dir / "command.txt", command_text + "\n")

    parse_error = ""
    try:
        completions = parse_completions(stdout)
        flow_records = parse_mrc_flow_diags(stdout)
        events = parse_shared_events(stdout)
        diag_ids = {row["flow_id"] for row in flow_records}
        completion_ids = set(completions)
        if len(flow_records) != artifact.connections:
            raise ValueError("MrcFlowDiag count mismatch")
        if diag_ids != completion_ids:
            raise ValueError("completion/diagnostic flow IDs differ")
        if len(completions) != artifact.connections:
            raise ValueError("completion count mismatch")
        for row in flow_records:
            completion = completions[row["flow_id"]]
            if (row["src"] != completion["completion_src"] or
                    row["dst"] != completion["completion_dst"] or
                    ((row["flow_size"] + common.MTU_BYTES - 1) //
                     common.MTU_BYTES) * common.MTU_BYTES !=
                    completion["completed_bytes"]):
                raise ValueError("completion/diagnostic payload mismatch")
            row["fct_us"] = row["finish_us"] - row["start_us"]
            if row["fct_us"] <= 0:
                raise ValueError("non-positive FCT")
    except (KeyError, TypeError, ValueError) as error:
        parse_error = str(error)
        flow_records = []
        events = []

    valid = int(
        returncode == 0 and not parse_error and config_ok(stdout, case)
    )
    cell = {
        "identity": case_identity(case),
        **case._asdict(),
        "nodes": NODES,
        "paths": PATHS,
        "connections": artifact.connections,
        "returncode": returncode,
        "config_ok": int(config_ok(stdout, case)),
        "parse_error": parse_error,
        "valid": valid,
        "runtime_s": runtime_s,
        "traffic_sha256": artifact.sha256,
        "sim_sha256": file_sha256(args.sim),
        "command_sha256": hashlib.sha256(command_text.encode()).hexdigest(),
        "fingerprint": fingerprint,
    }
    for row in flow_records:
        row.update({
            "experiment": case.experiment,
            "scenario": case.scenario,
            "family": case.family,
            "scheme": case.scheme,
            "seed": case.seed,
            "load_pct": case.load_pct,
            "parallel": case.parallel,
            "active_evs": case.active_evs or PATHS,
        })
    for event in events:
        event.update({
            "experiment": case.experiment,
            "scenario": case.scenario,
            "scheme": case.scheme,
            "seed": case.seed,
            "parallel": case.parallel,
        })
    atomic_json(summary_path, cell)
    with gzip.open(flow_path, "wt", encoding="utf-8") as handle:
        json.dump(flow_records, handle, sort_keys=True)
    with gzip.open(event_path, "wt", encoding="utf-8") as handle:
        json.dump(events, handle, sort_keys=True)
    for disposable in (case_dir / "idmap.txt", case_dir / "logout.dat"):
        disposable.unlink(missing_ok=True)
    print("done {} valid={} flows={} runtime={:.1f}s".format(
        case_identity(case), valid, len(flow_records), runtime_s), flush=True)
    return cell, flow_records, events


def aggregate_shared_keys(events):
    groups = {}
    for event in events:
        key = (
            event["scenario"], event["scheme"], event["seed"],
            event["src"], event["dst_tor"], event["ev"],
        )
        groups.setdefault(key, []).append(event)
    rows = []
    for key, selected in sorted(groups.items()):
        selected.sort(key=lambda item: (
            item["time_us"], item["generation"], item["flow_id"]))
        rows.append({
            "scenario": key[0],
            "scheme": key[1],
            "seed": key[2],
            "src": key[3],
            "dst_tor": key[4],
            "ev": key[5],
            "updates": len(selected),
            "distinct_discovery_qps": len({
                item["flow_id"] for item in selected}),
            "redundant_updates_after_first": max(0, len(selected) - 1),
            "first_update_us": selected[0]["time_us"],
            "last_update_us": selected[-1]["time_us"],
            "discovery_span_us": (
                selected[-1]["time_us"] - selected[0]["time_us"]),
            "strongest_signal": max(item["signal"] for item in selected),
        })
    return rows


def percentile(values, quantile):
    return metrics.percentile(list(values), quantile) if values else 0.0


def feedback_bucket(row):
    if not int(row["effective_state_updates"]):
        return "no_effective_update"
    remaining = int(row["new_selections_after_first_update"])
    if remaining <= 1:
        return "too_late"
    if int(row["actionable_feedback"]):
        return "actionable"
    return "effective_not_actionable"


def build_aggregates(flow_rows, shared_key_rows):
    for row in flow_rows:
        row["feedback_bucket"] = feedback_bucket(row)
        row["coverage_fraction"] = (
            row["unique_active_evs"] / row["active_evs"]
            if row["active_evs"] else 0.0)

    exp13 = [
        row for row in flow_rows
        if row["experiment"] in ("flow_lifetime", "active_ev_count")
    ]
    paired = pair_flow_rows(exp13, "rr", "mrc")
    exp2 = [
        row for row in flow_rows
        if row["experiment"] == "per_qp_sharing"
    ]
    paired.extend(pair_flow_rows(exp2, "mrc", "mrc-shared"))

    summary = []
    exp1_groups = {}
    for row in paired:
        if not row["scenario"].startswith("exp1_"):
            continue
        key = (row["family"], row["load_pct"])
        exp1_groups.setdefault(key, []).append(row)
    for (family, load), selected in sorted(exp1_groups.items()):
        summary.append({
            "experiment": "flow_lifetime",
            "family": family,
            "load_pct": load,
            "parallel": 0,
            "active_evs": PATHS,
            "scheme": "mrc_vs_rr",
            "flow_count": len(selected),
            "fct_ratio_geomean": geometric_mean(
                row["fct_ratio"] for row in selected),
            "slowdown_gt_1pct_fraction": sum(
                row["slowdown_gt_1pct"] for row in selected) / len(selected),
            "slowdown_gt_5pct_fraction": sum(
                row["slowdown_gt_5pct"] for row in selected) / len(selected),
            "slowdown_gt_10pct_fraction": sum(
                row["slowdown_gt_10pct"] for row in selected) / len(selected),
            "actionable_fraction": sum(
                int(row["actionable_feedback"] > 0)
                for row in selected) / len(selected),
            "complete_sweep_fraction": sum(
                int(row["full_sweeps"] > 0)
                for row in selected) / len(selected),
            "coverage_mean": sum(
                row["coverage_fraction"] for row in selected) / len(selected),
            "p99_treatment_fct_us": percentile(
                [row["treatment_fct_us"] for row in selected], 0.99),
        })

    exp2_groups = {}
    for row in flow_rows:
        if row["experiment"] == "per_qp_sharing":
            exp2_groups.setdefault(
                (row["parallel"], row["scheme"]), []).append(row)
    for (parallel, scheme), selected in sorted(exp2_groups.items()):
        per_seed_cct = []
        for seed in sorted({row["seed"] for row in selected}):
            per_seed_cct.append(max(
                row["fct_us"] for row in selected if row["seed"] == seed))
        summary.append({
            "experiment": "per_qp_sharing",
            "family": "asymmetric_alltoall",
            "load_pct": 0,
            "parallel": parallel,
            "active_evs": PATHS,
            "scheme": scheme,
            "flow_count": len(selected),
            "cct_geomean_us": geometric_mean(per_seed_cct),
            "p99_fct_us": percentile(
                [row["fct_us"] for row in selected], 0.99),
            "shared_updates_published": sum(
                row["shared_updates_published"] for row in selected),
            "shared_updates_consumed": sum(
                row["shared_updates_consumed"] for row in selected),
            "shared_updates_from_other_qps": sum(
                row["shared_updates_from_other_qps"] for row in selected),
            "redundant_discoveries": sum(
                row["redundant_discoveries"] for row in selected),
            "post_shared_bad_ev_sends": sum(
                row["post_shared_bad_ev_sends"] for row in selected),
        })

    exp3_groups = {}
    for row in flow_rows:
        if row["experiment"] == "active_ev_count":
            exp3_groups.setdefault(
                (row["load_pct"], row["active_evs"], row["scheme"]),
                []).append(row)
    for (load, active, scheme), selected in sorted(exp3_groups.items()):
        summary.append({
            "experiment": "active_ev_count",
            "family": "asymmetric_websearch",
            "load_pct": load,
            "parallel": 0,
            "active_evs": active,
            "scheme": scheme,
            "flow_count": len(selected),
            "p99_fct_us": percentile(
                [row["fct_us"] for row in selected], 0.99),
            "coverage_mean": sum(
                row["coverage_fraction"] for row in selected) / len(selected),
            "complete_sweep_fraction": sum(
                int(row["full_sweeps"] > 0)
                for row in selected) / len(selected),
            "unused_active_evs_mean": sum(
                row["unused_active_evs"] for row in selected) / len(selected),
            "actionable_fraction": sum(
                int(row["actionable_feedback"] > 0)
                for row in selected) / len(selected),
        })
    return summary, paired


def make_figures(out, flow_rows, summary, paired):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure_dir = Path(out) / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)

    exp1 = [row for row in paired if row["scenario"].startswith("exp1_")]
    sizes = sorted({row["flow_size"] for row in exp1})
    ratios = []
    actionable = []
    labels = []
    for size in sizes:
        selected = [row for row in exp1 if row["flow_size"] == size]
        ratios.append(geometric_mean(row["fct_ratio"] for row in selected))
        actionable.append(sum(
            int(row["actionable_feedback"] > 0)
            for row in selected) / len(selected))
        labels.append(size / 1024)
    fig, axis = plt.subplots(figsize=(8, 4.8))
    axis.plot(labels, ratios, marker="o", label="MRC / RR FCT")
    axis.axhline(1.0, color="black", linewidth=0.8)
    axis.set_xscale("log")
    axis.set_xlabel("Flow size (KiB, log scale)")
    axis.set_ylabel("Paired FCT ratio")
    other = axis.twinx()
    other.plot(labels, actionable, marker="s", color="tab:orange",
               label="actionable feedback fraction")
    other.set_ylabel("Actionable feedback fraction")
    axis.set_title("Experiment 1: feedback opportunity versus flow size")
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(figure_dir / (
            "exp1_feedback_value_by_flow_size." + suffix), dpi=180)
    plt.close(fig)

    exp2 = [
        row for row in summary if row["experiment"] == "per_qp_sharing"
    ]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4.2))
    for scheme in ("mrc", "mrc-shared"):
        selected = sorted(
            (row for row in exp2 if row["scheme"] == scheme),
            key=lambda row: row["parallel"])
        axes[0].plot(
            [row["parallel"] for row in selected],
            [row["redundant_discoveries"] for row in selected],
            marker="o", label=scheme)
        axes[1].plot(
            [row["parallel"] for row in selected],
            [row["cct_geomean_us"] for row in selected],
            marker="o", label=scheme)
    axes[0].set_title("Repeated discoveries")
    axes[0].set_xlabel("Parallel QPs per source")
    axes[0].set_ylabel("Redundant local discoveries")
    axes[1].set_title("Completion time (secondary)")
    axes[1].set_xlabel("Parallel QPs per source")
    axes[1].set_ylabel("CCT geometric mean (us)")
    axes[0].legend()
    axes[1].legend()
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(
            figure_dir / ("exp2_per_qp_repeated_exploration." + suffix),
            dpi=180)
    plt.close(fig)

    exp3 = [
        row for row in summary if row["experiment"] == "active_ev_count"
    ]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4.2))
    for load in (40, 80):
        selected = sorted(
            (row for row in exp3
             if row["scheme"] == "mrc" and row["load_pct"] == load),
            key=lambda row: row["active_evs"])
        axes[0].plot(
            [row["active_evs"] for row in selected],
            [row["coverage_mean"] for row in selected],
            marker="o", label="{}% load".format(load))
        axes[1].plot(
            [row["active_evs"] for row in selected],
            [row["p99_fct_us"] for row in selected],
            marker="o", label="{}% load".format(load))
    axes[0].set_xlabel("Active EV count")
    axes[0].set_ylabel("Mean EV coverage")
    axes[0].set_ylim(0, 1.05)
    axes[1].set_xlabel("Active EV count")
    axes[1].set_ylabel("p99 FCT (us)")
    axes[0].legend()
    axes[1].legend()
    fig.suptitle("Experiment 3: EV-set size versus workload granularity")
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        fig.savefig(
            figure_dir / ("exp3_active_ev_coverage_and_fct." + suffix),
            dpi=180)
    plt.close(fig)


def build_report_text(cells, flow_rows, summary, paired, smoke):
    valid = sum(int(row["valid"]) for row in cells)
    exp1 = [row for row in paired if row["scenario"].startswith("exp1_")]
    slow5 = (
        sum(row["slowdown_gt_5pct"] for row in exp1) / len(exp1)
        if exp1 else 0.0)
    actionable = (
        sum(int(row["actionable_feedback"] > 0)
            for row in exp1) / len(exp1) if exp1 else 0.0)
    return "\n".join([
        "# MRC inherent limitations: controlled experiment report",
        "",
        "This is a {} run at 128 nodes / 8 physical paths.".format(
            "smoke" if smoke else "full"),
        "",
        "## Validation",
        "",
        "- Validated cells: {}/{}.".format(valid, len(cells)),
        "- Completed target-flow diagnostics: {}.".format(len(flow_rows)),
        "- Traffic is byte-identical inside every paired comparison.",
        "",
        "## Experiment 1: flow lifetime and feedback value",
        "",
        "- Overall actionable-feedback fraction: {:.4f}.".format(actionable),
        "- Fraction of paired flows with MRC slowdown >5%: {:.4f}.".format(
            slow5),
        "- Interpret size-resolved results in `flow_metrics.csv`; a no-update "
        "flow is a no-learning-opportunity control.",
        "",
        "![Experiment 1](figures/exp1_feedback_value_by_flow_size.png)",
        "",
        "## Experiment 2: per-QP repeated exploration",
        "",
        "Direct evidence is in `shared_key_metrics.csv` and the per-flow "
        "publication/consumption/redundancy counters. CCT is secondary.",
        "",
        "![Experiment 2](figures/exp2_per_qp_repeated_exploration.png)",
        "",
        "## Experiment 3: active EV count",
        "",
        "The physical path namespace remains eight; only the endpoint active "
        "EV prefix changes.",
        "",
        "![Experiment 3](figures/exp3_active_ev_coverage_and_fct.png)",
        "",
        "## Caveats",
        "",
        "- Failure injection is intentionally excluded.",
        "- WebSearch uses the repository's digitized proxy CDF.",
        "- Mechanism labels describe observed evidence, not unobserved "
        "counterfactual path state.",
        "",
    ])


def read_csv(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run_matrix(args, smoke=False):
    cases = smoke_catalog() if smoke else case_catalog(args.seeds)
    args.out.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for case in cases:
        key = traffic_key(case)
        if key not in artifacts:
            artifacts[key] = materialize_traffic(
                args.out / "traffic", case, smoke=smoke)
    manifest = {
        "status": "running",
        "mode": "smoke" if smoke else "full",
        "nodes": NODES,
        "paths": PATHS,
        "seeds": sorted({case.seed for case in cases}),
        "case_count": len(cases),
        "expected_identities": [case_identity(case) for case in cases],
        "sim_sha256": file_sha256(args.sim),
        "traffic": {
            key: {
                "path": str(value.path),
                "sha256": value.sha256,
                "connections": value.connections,
            } for key, value in artifacts.items()
        },
    }
    atomic_json(args.out / "manifest.json", manifest)

    results = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(args.workers, len(cases))) as executor:
        futures = [
            executor.submit(run_case, case, artifacts[traffic_key(case)], args)
            for case in cases
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
            atomic_csv(
                args.out / "cells.partial.csv",
                [item[0] for item in results])
    cells = [item[0] for item in results]
    validate_complete_cells(
        cells, {case_identity(case) for case in cases})
    flow_rows = [
        row for _, selected, _ in results for row in selected
    ]
    events = [row for _, _, selected in results for row in selected]
    shared = aggregate_shared_keys(events)
    summary, paired = build_aggregates(flow_rows, shared)
    cells.sort(key=lambda row: row["identity"])
    flow_rows.sort(key=lambda row: (
        row["experiment"], row["scenario"], row["scheme"],
        row["seed"], row["flow_id"]))
    atomic_csv(args.out / "cells.csv", cells)
    atomic_csv(args.out / "flow_metrics.csv", flow_rows)
    atomic_csv(args.out / "paired_flow_metrics.csv", paired)
    atomic_csv(args.out / "shared_key_metrics.csv", shared)
    atomic_csv(args.out / "summary.csv", summary)
    make_figures(args.out, flow_rows, summary, paired)
    atomic_text(
        args.out / "mrc_inherent_limitations_report.md",
        build_report_text(cells, flow_rows, summary, paired, smoke))
    manifest["status"] = "complete"
    manifest["validated_cells"] = len(cells)
    manifest["flow_diagnostics"] = len(flow_rows)
    atomic_json(args.out / "manifest.json", manifest)
    atomic_text(args.out / "COMPLETE", "{} validated cells\n".format(
        len(cells)))
    print("validated {} cells; report={}".format(
        len(cells), args.out / "mrc_inherent_limitations_report.md"))


def report_existing(args):
    manifest = json.loads(
        (args.out / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise ValueError("cannot report an incomplete result matrix")
    cells = read_csv(args.out / "cells.csv")
    flow_rows = read_csv(args.out / "flow_metrics.csv")
    paired = read_csv(args.out / "paired_flow_metrics.csv")
    summary = read_csv(args.out / "summary.csv")
    atomic_text(
        args.out / "mrc_inherent_limitations_report.md",
        build_report_text(
            cells, flow_rows, summary, paired,
            manifest.get("mode") == "smoke"))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("smoke", "run", "report"):
        child = subparsers.add_parser(command)
        child.add_argument("--out", type=Path, default=DEFAULT_OUT)
        child.add_argument("--sim", type=Path, default=DEFAULT_SIM)
        child.add_argument("--seeds", default="13,29,47")
        child.add_argument("--workers", type=int, default=3)
        child.add_argument("--timeout", type=int, default=3600)
        child.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    args.out = args.out.resolve()
    args.sim = args.sim.resolve()
    args.seeds = tuple(
        int(value) for value in args.seeds.split(",") if value)
    if not args.seeds or args.workers < 1 or args.timeout < 1:
        raise ValueError("seeds, workers, and timeout must be positive")
    if args.command != "report" and not args.sim.exists():
        raise FileNotFoundError(args.sim)
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.command == "smoke":
        run_matrix(args, smoke=True)
    elif args.command == "run":
        run_matrix(args, smoke=False)
    else:
        report_existing(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
