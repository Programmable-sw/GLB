#!/usr/bin/env python3
"""Validate, aggregate, and select feedback-cadence evaluation results."""

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    ROOT / "experiments/n-mrc/output/nmrc_feedback_and_workload_evaluation"
)
SUBJECTS = ("avail", "grade", "netaware")
CADENCES = ("fixed5", "fixed_rtt", "bdp_triggered")
EVALUATION_STAGES = ("pilot-time", "pilot-bdp", "simple", "alltoall")
CADENCE_SELECTION_STAGES = ("pilot-time", "pilot-bdp", "alltoall")
INDEX_REQUEST_STAGES = EVALUATION_STAGES + ("all",)
RUNNER_SCHEMES = (
    "ecmp_rr", "ops", "reps", "mrc", "sglb", "ar", "drill",
    "avail", "grade", "netaware",
)
REQUIRED_EVIDENCE_CLASSES = (
    "healthy_guardrail", "incast_guardrail", "asymmetric", "mixed",
    "periodic_background", "alltoall",
)
MIN_GUARDRAIL_PAIRED_SEEDS = 3
STABLE_P99_RATIO_THRESHOLD = 1.03
RECOVERY_BDP_PACKETS = 86
RECOVERY_RETX_RATIO_THRESHOLD = 0.001
OPPORTUNITY_IMPROVEMENT_THRESHOLD = 0.97
OPPORTUNITY_REGRESSION_THRESHOLD = 1.03
EXPECTED_PARSER_VERSION = 10
EXPECTED_TOPOLOGIES = {
    128: {
        "nodes": 128, "hosts_per_leaf": 8, "leaves": 16,
        "spines": 8, "paths": 8, "tiers": 2,
    },
    512: {
        "nodes": 512, "hosts_per_leaf": 16, "leaves": 32,
        "spines": 16, "paths": 16, "tiers": 2,
    },
    2048: {
        "nodes": 2048, "hosts_per_leaf": 32, "leaves": 64,
        "spines": 32, "paths": 32, "tiers": 2,
    },
}
FCT_METRICS = (
    "avg_fct_us", "p50_fct_us", "p95_fct_us", "p99_fct_us",
    "p999_fct_us", "max_fct_us", "all_to_all_cct_us",
)
SUMMARY_METRICS = FCT_METRICS + (
    "feedback_count", "feedback_bytes", "nacks", "nacks_ooo",
    "nacks_trim", "nacks_loss", "retx_packets", "queue_cv",
)
INTEGER_COUNTER_FIELDS = (
    "feedback_acks", "feedback_nacks", "feedback_messages_total",
    "nacks", "nacks_ooo", "nacks_trim", "nacks_loss", "rtos",
    "retx_packets", "new_packets", "nmrc_feedbacks",
    "nmrc_packet_feedbacks", "nmrc_time_feedbacks",
)
REJECTION_FIELDS = (
    "source", "row", "case", "reason", "nodes", "seed", "scenario",
    "scheme", "cadence",
)
DECISION_FIELDS = (
    "subject", "cadence", "scenario", "rule", "status", "value", "threshold",
    "paired_cases", "baseline", "evidence",
)
OUTPUT_ARTIFACTS = (
    "summary.csv", "data_quality_rejections.csv",
    "cadence_decisions.csv", "selected-cadences.json", "report.md",
    "normalized_metrics.csv", "seed_metrics.csv",
    "empirical_seed_ecdf.csv", "feedback_pareto.csv",
    "runtime_config_audit.csv",
    "normalized_tail_heatmap.png", "normalized_tail_heatmap.pdf",
    "key_fct_cdf.png", "key_fct_cdf.pdf",
    "alltoall_cct.png", "alltoall_cct.pdf",
    "topology_scaling.png", "topology_scaling.pdf",
    "queue_cv_cdf.png", "queue_cv_cdf.pdf",
    "recovery_cost.png", "recovery_cost.pdf",
    "feedback_pareto.png", "feedback_pareto.pdf",
    "seed_ranges.png", "seed_ranges.pdf",
)
SUCCESS_ARTIFACTS = tuple(
    name for name in OUTPUT_ARTIFACTS if name != "data_quality_rejections.csv"
)
FIGURE_STEMS = (
    "normalized_tail_heatmap", "key_fct_cdf", "alltoall_cct",
    "topology_scaling", "queue_cv_cdf", "recovery_cost",
    "feedback_pareto", "seed_ranges",
)
DERIVED_ARTIFACTS = (
    "normalized_metrics.csv", "seed_metrics.csv",
    "empirical_seed_ecdf.csv", "feedback_pareto.csv",
    "runtime_config_audit.csv",
)
SCHEME_COLORS = {
    "avail": "#0072B2",
    "grade": "#D55E00",
    "netaware": "#009E73",
}
CADENCE_LINESTYLES = {
    "fixed5": "-", "fixed_rtt": "--", "bdp_triggered": ":",
}
CADENCE_HATCHES = {
    "fixed5": "", "fixed_rtt": "//", "bdp_triggered": "xx",
}
CADENCE_MARKERS = {
    "fixed5": "o", "fixed_rtt": "s", "bdp_triggered": "^",
}


class LoadedRows(list):
    """A list of accepted rows carrying explicit rejected-row evidence."""

    def __init__(self, rows=(), rejections=(), expected_cases=(), run_intents=()):
        super().__init__(rows)
        self.rejections = list(rejections)
        self.expected_cases = list(expected_cases)
        self.run_intents = list(run_intents)


def _canonical_json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )


def _manifest_fingerprint(manifest):
    return hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()


def _is_sha256(value):
    return (
        isinstance(value, str) and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _case_label(row):
    return "/".join(str(row.get(key, "?")) for key in (
        "nodes", "seed", "scenario", "scheme", "cadence"
    ))


def _rejection(source, row_number, row, reason):
    return {
        "source": str(source),
        "row": row_number,
        "case": _case_label(row),
        "reason": reason,
        "nodes": row.get("nodes", ""),
        "seed": row.get("seed", ""),
        "scenario": row.get("scenario", ""),
        "scheme": row.get("scheme", ""),
        "cadence": row.get("cadence", ""),
        "stage": row.get("stage", ""),
    }


def _evidence_class(stage, scenario):
    if stage == "alltoall":
        return "alltoall"
    if scenario.startswith("healthy_"):
        return "healthy_guardrail"
    if scenario.startswith("incast_"):
        return "incast_guardrail"
    if scenario.startswith("asymmetric_"):
        return "asymmetric"
    if scenario.startswith("mixed_fixed_background_"):
        return "mixed"
    if scenario.startswith("periodic_hotspot_"):
        return "periodic_background"
    return "other"


def _scenario_matches(name, patterns):
    return not patterns or any(pattern in name for pattern in patterns)


def _ledger_case_key(raw, source):
    if not isinstance(raw, dict) or set(raw) != {
            "stage", "nodes", "scenario", "seed", "scheme", "cadence"}:
        raise ValueError(f"ledger_case_key:{source}:invalid fields")
    stage = raw["stage"]
    scenario = raw["scenario"]
    scheme = raw["scheme"]
    cadence = raw["cadence"]
    if not all(isinstance(value, str) and value for value in (
            stage, scenario, scheme, cadence)):
        raise ValueError(f"ledger_case_key:{source}:invalid string field")
    nodes = _integer(raw["nodes"], "ledger nodes", 1)
    seed = _integer(raw["seed"], "ledger seed", 0)
    return {
        "stage": stage, "nodes": nodes, "scenario": scenario, "seed": seed,
        "scheme": scheme, "cadence": cadence,
        "evidence_class": _evidence_class(stage, scenario),
    }


def _case_tuple(row):
    return tuple(row[key] for key in (
        "stage", "nodes", "scenario", "seed", "scheme", "cadence"
    ))


def _candidate_identity(candidate):
    if not isinstance(candidate, str) or not candidate:
        raise ValueError("run intent candidate must be a non-empty string")
    for cadence in CADENCES:
        suffix = f"_{cadence}"
        if candidate.endswith(suffix):
            scheme = candidate[:-len(suffix)]
            if not scheme:
                break
            return scheme, cadence
    return candidate, "none"


def _integer(value, field, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _number(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric, not bool")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(f"non_finite_numeric:{field}") from None
    if not math.isfinite(number):
        raise ValueError(f"non_finite_numeric:{field}")
    if number < 0:
        raise ValueError(f"negative_numeric:{field}")
    return number


def _counter(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"integer_counter:{field} must be a non-negative integer"
        )
    return value


def _is_float_metric(field):
    return (
        field in FCT_METRICS
        or field in {"retx_ratio", "queue_cv_spine_queue_cv"}
        or field.endswith(("_avg", "_ratio", "_cv"))
    )


def _option(argv, name):
    positions = [index for index, token in enumerate(argv) if token == name]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise ValueError(f"configuration option {name} must occur exactly once")
    return argv[positions[0] + 1]


def _decimal_equal(raw, expected):
    try:
        return Decimal(str(raw)) == Decimal(str(expected))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _cli_lb(scheme):
    return "adaptive-routing" if scheme == "ar" else scheme


def _validate_command(manifest, topology):
    argv = manifest.get("argv")
    if not isinstance(argv, list) or not argv or not all(
            isinstance(token, str) for token in argv):
        raise ValueError("command_config:argv must be a non-empty string list")

    checks = (
        ("-nodes", topology["nodes"], "topology_mismatch"),
        ("-tiers", topology["tiers"], "topology_mismatch"),
        ("-paths", topology["paths"], "topology_mismatch"),
        ("-queue_type", "composite_ecn_lb", "queue_config"),
        ("-host_queue_type", "prio", "queue_config"),
        ("-roce_rx_mode", "sp", "rx_config"),
        ("-roce_sack_bitmap_bits", 64, "rx_config"),
        ("-cc", "dcqcn_variant", "cc_config"),
        ("-mtu", 4096, "mtu_config"),
        ("-linkspeed", 400000, "link_rate_config"),
        ("-queue_cv_sample_us", 100, "queue_config"),
        ("-lb", _cli_lb(manifest.get("scheme")), "scheme_config"),
        ("-seed", manifest.get("seed"), "seed_config"),
    )
    for option, expected, reason in checks:
        try:
            actual = _option(argv, option)
        except ValueError as error:
            raise ValueError(f"{reason}:{error}") from None
        if str(actual) != str(expected):
            raise ValueError(
                f"{reason}:{option}={actual!r} expected {expected!r}"
            )

    scheme = manifest.get("scheme")
    cadence = manifest.get("cadence", "none")
    feedback_options = [
        token for token in argv
        if token.startswith("-stor_feedback_")
        or token.startswith("-netaware_feedback_")
        or token == "-stor_trim_feedback_min_us"
    ]
    if scheme not in SUBJECTS:
        if cadence != "none" or feedback_options:
            raise ValueError("cadence_config:baseline has feedback flags")
        return
    if cadence not in CADENCES:
        raise ValueError("cadence_config:unknown cadence")
    prefix = "netaware" if scheme == "netaware" else "stor"
    packets, minimum, maximum = {
        "fixed5": (1, 5, 5),
        "fixed_rtt": (1, 7, 7),
        "bdp_triggered": (86, 5, 14),
    }[cadence]
    expected = {
        f"-{prefix}_feedback_pkts": packets,
        f"-{prefix}_feedback_min_us": minimum,
        f"-{prefix}_feedback_max_us": maximum,
    }
    if prefix == "stor":
        expected["-stor_trim_feedback_min_us"] = (
            maximum if cadence == "bdp_triggered" else minimum
        )
    opposite = "stor" if prefix == "nmrc" else "nmrc"
    if any(token.startswith(f"-{opposite}_feedback_") for token in argv):
        raise ValueError("cadence_config:opposite feedback flags present")
    for option, value in expected.items():
        try:
            actual = _option(argv, option)
        except ValueError as error:
            raise ValueError(f"cadence_config:{error}") from None
        if not _decimal_equal(actual, value):
            raise ValueError(
                f"cadence_config:{option}={actual!r} expected {value!r}"
            )
    if set(feedback_options) != set(expected):
        raise ValueError("cadence_config:unexpected or duplicate feedback flags")


def _validated_simulation_duration(manifest):
    timing = manifest.get("timing")
    manifest_raw = None
    if timing is not None:
        if not isinstance(timing, dict):
            raise ValueError("timing_config:manifest timing must be an object")
        manifest_raw = timing.get("simulation_end_us")

    command_raw = None
    positions = [
        index for index, token in enumerate(manifest["argv"])
        if token == "-end"
    ]
    if len(positions) > 1:
        raise ValueError("timing_config:command -end must occur at most once")
    if positions:
        if positions[0] + 1 >= len(manifest["argv"]):
            raise ValueError("timing_config:command -end has no value")
        command_raw = manifest["argv"][positions[0] + 1]

    def positive_duration(value, source):
        try:
            duration = _number(value, source)
        except ValueError as error:
            raise ValueError(f"timing_config:{error}") from None
        if duration <= 0:
            raise ValueError(f"timing_config:{source} must be finite positive")
        return duration

    manifest_duration = (
        None if manifest_raw is None else
        positive_duration(manifest_raw, "manifest timing.simulation_end_us")
    )
    command_duration = (
        None if command_raw is None else
        positive_duration(command_raw, "command -end")
    )
    if manifest_duration is None and command_duration is None:
        raise ValueError(
            "timing_config:simulation duration is not available from manifest "
            "timing.simulation_end_us or command -end"
        )
    manifest_cli_value = (
        None if manifest_duration is None else
        format(manifest_duration, "g")
    )
    if (manifest_duration is not None and command_duration is not None
            and not _decimal_equal(manifest_cli_value, command_raw)):
        raise ValueError(
            "timing_config:manifest timing.simulation_end_us does not match "
            "command -end"
        )
    if manifest_duration is not None and command_duration is not None:
        source = "manifest timing.simulation_end_us audited against command -end"
    elif manifest_duration is not None:
        source = "manifest timing.simulation_end_us"
    else:
        source = "command -end"
    return (
        command_duration if command_duration is not None else manifest_duration,
        source,
    )


def _manifest_path(input_dir, row):
    candidate = row.get("candidate")
    if not candidate:
        cadence = row.get("cadence", "none")
        candidate = row.get("scheme", "")
        if cadence != "none":
            candidate += f"_{cadence}"
    return (
        input_dir / "raw" / str(row.get("stage", ""))
        / f"nodes_{row.get('nodes', '')}" / f"seed_{row.get('seed', '')}"
        / str(row.get("scenario", "")) / str(candidate) / "manifest.json"
    )


def _canonical_argv(argv):
    omitted = {"-seed", "-o", "-tm", "-conns"}
    result = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in omitted and index + 1 < len(argv):
            index += 2
            continue
        result.append(token)
        index += 1
    return result


def _case_config_signature(manifest):
    scenario = dict(manifest.get("scenario") or {})
    scenario.pop("seed", None)
    value = {
        "topology": manifest["topology"],
        "stage": manifest.get("stage"),
        "scenario": scenario,
        "scheme": manifest.get("scheme"),
        "cadence": manifest.get("cadence", "none"),
        "alltoall": manifest.get("alltoall"),
        "timing": manifest.get("timing"),
        "flow_identity_mode": (
            manifest.get("flow_identity") or {}
        ).get("mode"),
        "argv": _canonical_argv(manifest["argv"]),
    }
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _semantic_case_config_signature(manifest):
    scenario = dict(manifest.get("scenario") or {})
    scenario.pop("seed", None)
    value = {
        "topology": manifest["topology"],
        "scenario": scenario,
        "scheme": manifest.get("scheme"),
        "cadence": manifest.get("cadence", "none"),
        "alltoall": manifest.get("alltoall"),
        "timing": manifest.get("timing"),
        "flow_identity_mode": (
            manifest.get("flow_identity") or {}
        ).get("mode"),
        "argv": _canonical_argv(manifest["argv"]),
    }
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _case_comparability_signature(manifest):
    scenario = dict(manifest.get("scenario") or {})
    scenario.pop("seed", None)
    argv = manifest["argv"]
    comparable_argv = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if (token in {"-seed", "-o", "-tm", "-conns"}
                or token.startswith("-stor_feedback_")
                or token.startswith("-netaware_feedback_")
                or token == "-stor_trim_feedback_min_us"):
            index += 2
            continue
        comparable_argv.append(token)
        index += 1
    expected = manifest["expected_flows"]
    expected_counts = {
        "target": expected["target"],
        "background": expected["background"],
        "total": expected["target"] + expected["background"],
    }
    value = {
        "topology": manifest["topology"],
        "scenario": scenario,
        "scheme": manifest.get("scheme"),
        "expected_flow_counts": expected_counts,
        "alltoall": manifest.get("alltoall"),
        "timing": manifest.get("timing"),
        "flow_identity_mode": (
            manifest.get("flow_identity") or {}
        ).get("mode"),
        "argv": comparable_argv,
    }
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_row(row, manifest):
    if not isinstance(row, dict):
        raise ValueError("row_type:result must be a JSON object")
    if not isinstance(manifest, dict):
        raise ValueError("manifest_type:manifest must be a JSON object")

    manifest_labels = {
        "stage": manifest.get("stage"),
        "nodes": (manifest.get("topology") or {}).get("nodes"),
        "seed": manifest.get("seed"),
        "scenario": (manifest.get("scenario") or {}).get("name"),
        "scheme": manifest.get("scheme"),
        "cadence": manifest.get("cadence"),
    }
    for field, expected_label in manifest_labels.items():
        if row.get(field) != expected_label:
            raise ValueError(f"manifest_label_mismatch:{field}")
    nodes = _integer(row.get("nodes"), "nodes", 1)
    _integer(row.get("seed"), "seed", 0)
    topology = manifest.get("topology")
    if topology != EXPECTED_TOPOLOGIES.get(nodes):
        raise ValueError("topology_mismatch:manifest topology is not canonical")
    if manifest.get("parser_version") != EXPECTED_PARSER_VERSION:
        raise ValueError(
            f"parser_config:expected version {EXPECTED_PARSER_VERSION}"
        )
    if not _is_sha256(manifest.get("simulator_sha256")):
        raise ValueError("simulator_hash:invalid SHA-256")
    if not _is_sha256(manifest.get("traffic_sha256")):
        raise ValueError("traffic_hash:invalid SHA-256")
    if row.get("fingerprint") != _manifest_fingerprint(manifest):
        raise ValueError("fingerprint_mismatch:result does not match manifest")
    _validate_command(manifest, topology)
    simulation_duration_us, simulation_duration_source = (
        _validated_simulation_duration(manifest)
    )

    if _counter(row.get("returncode"), "returncode") != 0:
        raise ValueError("returncode_not_zero")
    if row.get("config_ok") is not True:
        raise ValueError("config_not_ok")
    expected = manifest.get("expected_flows")
    if not isinstance(expected, dict):
        raise ValueError("flow_count_mismatch:missing expected flows")
    target = _integer(expected.get("target"), "expected target flows", 0)
    background = _integer(
        expected.get("background"), "expected background flows", 0
    )
    if row.get("all_flows_completed") is not True:
        raise ValueError("incomplete_flows:all_flows_completed is not true")
    flow_values = {
        "target_flows": target,
        "target_completed": target,
        "background_flows": background,
        "background_completed": background,
    }
    for field, value in flow_values.items():
        actual = _counter(row.get(field), field)
        if actual != value:
            raise ValueError(
                f"flow_count_mismatch:{field}={actual!r} expected {value}"
            )
    for field, value in row.items():
        if isinstance(value, bool):
            if field in {"config_ok", "all_flows_completed"}:
                continue
            raise ValueError(
                f"integer_counter:{field} must be a non-negative integer"
            )
        if isinstance(value, (int, float)):
            if _is_float_metric(field):
                _number(value, field)
            else:
                _counter(value, field)
    for field in FCT_METRICS + (
            "retx_ratio", "queue_cv_spine_queue_cv"):
        if field not in row:
            raise ValueError(f"missing_metric:{field}")
        _number(row[field], field)
    required_counters = (
        "feedback_acks", "feedback_nacks", "feedback_messages_total",
        "nacks", "nacks_ooo", "nacks_trim", "nacks_loss", "rtos",
        "retx_packets",
    )
    for field in required_counters:
        if field not in row:
            raise ValueError(f"missing_metric:{field}")
        _counter(row[field], field)
    for field in INTEGER_COUNTER_FIELDS:
        if field in row and field not in required_counters:
            _counter(row[field], field)
    if sum(row[field] for field in (
            "nacks_ooo", "nacks_trim", "nacks_loss")) != row["nacks"]:
        raise ValueError("nack_split_mismatch")
    if row["feedback_messages_total"] != (
            row["feedback_acks"] + row["feedback_nacks"]):
        raise ValueError("feedback_message_split_mismatch")
    if row["feedback_nacks"] > row["nacks"]:
        raise ValueError("feedback_nacks_exceed_nacks")

    normalized = dict(row)
    normalized["feedback_count"] = row["feedback_messages_total"]
    bits_per_path = 1 if row["scheme"] == "avail" else 2
    payload_bytes = math.ceil(bits_per_path * topology["paths"] / 8)
    normalized["feedback_bytes"] = (
        row["feedback_messages_total"] * payload_bytes
    )
    normalized["simulation_duration_us"] = simulation_duration_us
    normalized["simulation_duration_source"] = simulation_duration_source
    normalized["feedback_bandwidth_mbps"] = (
        normalized["feedback_bytes"] * 8 / simulation_duration_us
    )
    if not math.isfinite(normalized["feedback_bandwidth_mbps"]):
        raise ValueError("timing_config:feedback bandwidth is not finite")
    normalized["queue_cv"] = row["queue_cv_spine_queue_cv"]
    normalized["simulator_sha256"] = manifest["simulator_sha256"]
    normalized["traffic_sha256"] = manifest["traffic_sha256"]
    normalized["parser_version"] = manifest["parser_version"]
    normalized["case_config_signature"] = _case_config_signature(manifest)
    normalized["semantic_case_config_signature"] = (
        _semantic_case_config_signature(manifest)
    )
    normalized["case_comparability_signature"] = (
        _case_comparability_signature(manifest)
    )
    normalized["is_alltoall"] = manifest.get("alltoall") is not None
    normalized["is_healthy"] = str(row["scenario"]).startswith("healthy_")
    scenario = str(row["scenario"])
    normalized["is_incast"] = scenario.startswith("incast_")
    normalized["is_opportunity"] = (
        normalized["is_alltoall"]
        or scenario.startswith("asymmetric_")
        or scenario.startswith("mixed_fixed_background_")
        or scenario.startswith("periodic_hotspot_")
    )
    if normalized["is_alltoall"]:
        normalized["evidence_class"] = "alltoall"
    elif scenario.startswith("healthy_"):
        normalized["evidence_class"] = "healthy_guardrail"
    elif normalized["is_incast"]:
        normalized["evidence_class"] = "incast_guardrail"
    elif scenario.startswith("asymmetric_"):
        normalized["evidence_class"] = "asymmetric"
    elif scenario.startswith("mixed_fixed_background_"):
        normalized["evidence_class"] = "mixed"
    elif scenario.startswith("periodic_hotspot_"):
        normalized["evidence_class"] = "periodic_background"
    else:
        normalized["evidence_class"] = "other"
    return normalized


def _reject_outliers(rows, rejections, source, field, reason, group_key=None):
    grouped = defaultdict(list)
    for row in rows:
        grouped[group_key(row) if group_key else None].append(row)
    accepted = []
    for group in grouped.values():
        counts = Counter(row[field] for row in group)
        chosen = min(
            counts, key=lambda value: (-counts[value], str(value))
        )
        for row in group:
            if row[field] == chosen:
                accepted.append(row)
            else:
                rejections.append(_rejection(
                    source, row["_source_row"], row,
                    f"{reason}:{field}={row[field]} expected {chosen}",
                ))
    return accepted


def _load_expected_case_ledgers(input_dir, rejections, stage_paths):
    expected = []
    intents = []
    ledger_paths = sorted(
        paths["ledger"] for paths in stage_paths.values()
        if paths.get("ledger") is not None and paths["ledger"].is_file()
    )
    intent_paths = sorted(
        paths["intent"] for paths in stage_paths.values()
        if paths.get("intent") is not None and paths["intent"].is_file()
    )
    if not ledger_paths or not intent_paths:
        rejections.append(_rejection(
            input_dir / "raw", 0, {},
            "missing_expected_case_ledger_or_run_intent",
        ))
        return expected, intents

    for path in ledger_paths:
        with path.open(encoding="utf-8") as handle:
            for row_number, line in enumerate(handle, 1):
                try:
                    raw = json.loads(line)
                    if not isinstance(raw, dict) or set(raw) != {
                            "ledger_version", "case_key", "fingerprint",
                            "traffic_sha256", "config_identity"}:
                        raise ValueError("invalid ledger record fields")
                    if raw["ledger_version"] != 1:
                        raise ValueError("unsupported ledger version")
                    case = _ledger_case_key(raw["case_key"], path)
                    if case["stage"] != path.parent.name:
                        raise ValueError("ledger stage does not match directory")
                    for field in (
                            "fingerprint", "traffic_sha256", "config_identity"):
                        if not _is_sha256(raw[field]):
                            raise ValueError(f"invalid ledger {field}")
                    expected.append(dict(
                        case,
                        fingerprint=raw["fingerprint"],
                        traffic_sha256=raw["traffic_sha256"],
                        config_identity=raw["config_identity"],
                        _source=str(path),
                        _source_row=row_number,
                    ))
                except (json.JSONDecodeError, TypeError, ValueError) as error:
                    rejections.append(_rejection(
                        path, row_number, {}, f"invalid_expected_ledger:{error}"
                    ))

    for path in intent_paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("ledger_version") != 1:
                raise ValueError("invalid run intent version")
            if (not isinstance(raw.get("stage"), str)
                    or raw["stage"] != path.parent.name):
                raise ValueError("run intent stage does not match directory")
            required = {
                "stage", "requested_nodes", "requested_scenarios",
                "requested_seeds", "requested_candidates",
                "scenario_patterns", "expected_case_keys",
            }
            if not required.issubset(raw):
                raise ValueError("run intent fields are incomplete")
            for field in (
                    "requested_nodes", "requested_scenarios",
                    "requested_seeds", "requested_candidates"):
                values = raw[field]
                if (not isinstance(values, list) or not values
                        or len(values) != len(set(values))):
                    raise ValueError(
                        f"run intent {field} must be a non-empty unique list"
                    )
            patterns = raw["scenario_patterns"]
            if (not isinstance(patterns, list)
                    or any(not isinstance(pattern, str) or not pattern
                           for pattern in patterns)
                    or len(patterns) != len(set(patterns))):
                raise ValueError(
                    "run intent scenario_patterns must be a unique string list"
                )
            expected_patterns = stage_paths[raw["stage"]][
                "scenario_patterns"
            ]
            if patterns != expected_patterns:
                raise ValueError(
                    "run intent scenario_patterns do not match stage snapshot"
                )
            keys = [
                _ledger_case_key(item, path)
                for item in raw["expected_case_keys"]
            ]
            if any(key["stage"] != raw["stage"] for key in keys):
                raise ValueError("run intent contains a cross-stage case")
            declared = Counter(_case_tuple(key) for key in keys)
            for key, count in declared.items():
                if count != 1:
                    row = dict(zip(
                        ("stage", "nodes", "scenario", "seed", "scheme", "cadence"),
                        key,
                    ))
                    rejections.append(_rejection(
                        path, 0, row,
                        f"duplicate_run_intent_case:count={count}",
                    ))
            expanded = set()
            for nodes in raw["requested_nodes"]:
                _integer(nodes, "run intent nodes", 1)
                for seed in raw["requested_seeds"]:
                    _integer(seed, "run intent seed", 0)
                    for scenario in raw["requested_scenarios"]:
                        if not isinstance(scenario, str) or not scenario:
                            raise ValueError("invalid run intent scenario")
                        for candidate in raw["requested_candidates"]:
                            scheme, cadence = _candidate_identity(candidate)
                            expanded.add((
                                raw["stage"], nodes, scenario, seed,
                                scheme, cadence,
                            ))
            for key in sorted(expanded - set(declared)):
                row = dict(zip(
                    ("stage", "nodes", "scenario", "seed", "scheme", "cadence"),
                    key,
                ))
                rejections.append(_rejection(
                    path, 0, row,
                    "missing_run_intent_case:requested grid is incomplete",
                ))
            for key in sorted(set(declared) - expanded):
                row = dict(zip(
                    ("stage", "nodes", "scenario", "seed", "scheme", "cadence"),
                    key,
                ))
                rejections.append(_rejection(
                    path, 0, row,
                    "unexpected_run_intent_case:outside requested grid",
                ))
            intents.append({
                "stage": raw["stage"],
                "requested_nodes": raw["requested_nodes"],
                "requested_scenarios": raw["requested_scenarios"],
                "requested_seeds": raw["requested_seeds"],
                "requested_candidates": raw["requested_candidates"],
                "scenario_patterns": patterns,
                "expected_case_keys": keys,
                "_source": str(path),
            })
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            rejections.append(_rejection(
                path, 0, {}, f"invalid_run_intent:{error}"
            ))

    ledger_counts = Counter(_case_tuple(item) for item in expected)
    intent_keys = {
        _case_tuple(key)
        for intent in intents for key in intent["expected_case_keys"]
    }
    for key, count in sorted(ledger_counts.items()):
        row = dict(zip(
            ("stage", "nodes", "scenario", "seed", "scheme", "cadence"),
            key,
        ))
        if count != 1:
            rejections.append(_rejection(
                "expected_cases.jsonl", 0, row,
                f"duplicate_expected_ledger_case:count={count}",
            ))
        if key not in intent_keys:
            rejections.append(_rejection(
                "expected_cases.jsonl", 0, row,
                "unexpected_expected_ledger_case:not declared by run intent",
            ))
    for key in sorted(intent_keys - set(ledger_counts)):
        row = dict(zip(
            ("stage", "nodes", "scenario", "seed", "scheme", "cadence"),
            key,
        ))
        rejections.append(_rejection(
            "run_intent.json", 0, row,
            "missing_expected_ledger_case:declared by run intent",
        ))
    return expected, intents


def _result_sources(stage_paths):
    return sorted(
        paths["results"] for paths in stage_paths.values()
        if paths.get("results") is not None and paths["results"].is_file()
    )


def _candidate_label(case_key):
    if case_key["cadence"] == "none":
        return case_key["scheme"]
    return f"{case_key['scheme']}_{case_key['cadence']}"


def _run_index_rejection(rejections, source, stage, reason):
    rejections.append(_rejection(
        source, 0,
        {
            "nodes": "", "seed": "", "scenario": stage,
            "scheme": "", "cadence": "",
        },
        reason,
    ))


def _validated_no_symlink_path(input_dir, target):
    root = Path(os.path.abspath(os.fspath(input_dir)))
    candidate = Path(os.path.abspath(os.fspath(target)))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise ValueError(
            f"path_boundary:{candidate} is outside input root {root}"
        ) from None

    current = root
    components = [root]
    for part in relative.parts:
        current /= part
        components.append(current)
    for component in components:
        if component.is_symlink():
            label = "." if component == root else component.relative_to(root)
            raise ValueError(f"symlink_path_component:{label}")

    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(
            f"path_boundary:{candidate} escapes input root: {error}"
        ) from None
    return candidate


def _validated_run_index_path(
        input_dir, source, stage, declared, expected, rejections):
    if not isinstance(declared, str) or declared != expected:
        _run_index_rejection(
            rejections, source, stage,
            f"run_index_path_boundary:declared={declared!r} "
            f"expected={expected}",
        )
        return None
    try:
        return _validated_no_symlink_path(input_dir, input_dir / declared)
    except ValueError as error:
        _run_index_rejection(
            rejections, source, stage,
            f"run_index_path_boundary:{declared}:{error}",
        )
        return None


def _load_run_index(input_dir, rejections):
    source = input_dir / "run_index.json"
    try:
        source = _validated_no_symlink_path(input_dir, source)
    except ValueError as error:
        _run_index_rejection(
            rejections, source, "run-index",
            f"run_index_path_boundary:{error}",
        )
        return None
    if not source.is_file():
        _run_index_rejection(
            rejections, source, "run-index",
            "run_index_missing:root run_index.json is required",
        )
        return None
    try:
        index = json.loads(source.read_text(encoding="utf-8"))
        if (not isinstance(index, dict) or index.get("schema_version") != 1
                or set(index) != {
                    "schema_version", "required_stages", "stages",
                    "invocations",
                }):
            raise ValueError("invalid root run index fields/version")
        required = index["required_stages"]
        stages = index["stages"]
        invocations = index["invocations"]
        if (not isinstance(required, list) or not required
                or len(required) != len(set(required))
                or any(stage not in EVALUATION_STAGES for stage in required)):
            raise ValueError("invalid required_stages")
        ordered_required = [
            stage for stage in EVALUATION_STAGES if stage in set(required)
        ]
        if required != ordered_required:
            raise ValueError("required_stages order is not canonical")
        if not isinstance(stages, dict) or set(stages) != set(required):
            raise ValueError("stage index does not match required_stages")
        if not isinstance(invocations, list) or not invocations:
            raise ValueError("invocation history is empty")

        stage_fields = {
            "invocation_id", "ledger_path", "ledger_sha256",
            "run_intent_path", "run_intent_sha256", "scenario_patterns",
            "results_path",
            "case_count",
            "case_identity_sha256", "nodes", "seeds", "scenarios",
            "candidates",
        }

        def validate_integer_intent(values, field, allowed=None):
            if (not isinstance(values, list) or not values
                    or any(type(value) is not int or value < 0 for value in values)
                    or values != sorted(set(values))
                    or (allowed is not None
                        and any(value not in allowed for value in values))):
                raise ValueError(f"invalid {field}")

        def validate_ordered_intent(values, field, order, allow_empty=False):
            if (not isinstance(values, list)
                    or (not allow_empty and not values)
                    or any(not isinstance(value, str) or not value for value in values)
                    or values != [value for value in order if value in set(values)]):
                raise ValueError(f"invalid {field}")

        def validate_stage_metadata(stage, entry, invocation_id):
            if (not isinstance(entry, dict) or set(entry) != stage_fields
                    or entry["invocation_id"] != invocation_id):
                raise ValueError(f"invalid stage index fields for {stage}")
            if (not all(isinstance(entry[field], str) for field in (
                        "ledger_path", "run_intent_path", "results_path"))
                    or not _is_sha256(entry["ledger_sha256"])
                    or not _is_sha256(entry["run_intent_sha256"])
                    or not _is_sha256(entry["case_identity_sha256"])
                    or type(entry["case_count"]) is not int
                    or entry["case_count"] <= 0):
                raise ValueError(f"invalid stage index metadata for {stage}")
            validate_integer_intent(
                entry["nodes"], f"stage {stage} nodes intent",
                set(EXPECTED_TOPOLOGIES),
            )
            validate_integer_intent(
                entry["seeds"], f"stage {stage} seeds intent"
            )
            for field in ("scenarios", "candidates"):
                values = entry[field]
                if (not isinstance(values, list) or not values
                        or any(not isinstance(value, str) or not value
                               for value in values)
                        or values != sorted(set(values))):
                    raise ValueError(f"invalid stage {stage} {field} intent")
            for candidate in entry["candidates"]:
                scheme, cadence = _candidate_identity(candidate)
                if (scheme not in RUNNER_SCHEMES
                        or cadence not in CADENCES + ("none",)):
                    raise ValueError(
                        f"invalid stage {stage} candidate {candidate}"
                    )
            patterns = entry["scenario_patterns"]
            if (not isinstance(patterns, list)
                    or any(not isinstance(pattern, str) or not pattern
                           for pattern in patterns)
                    or len(patterns) != len(set(patterns))):
                raise ValueError(
                    f"invalid stage {stage} scenario_patterns intent"
                )

        invocation_by_id = {}
        path_errors = set()
        invocation_fields = {
            "invocation_id", "requested_stage", "expanded_required_stages",
            "requested_nodes", "requested_seeds", "scenario_patterns",
            "requested_schemes", "requested_cadences", "stages",
            "invocation_sha256",
        }
        for invocation in invocations:
            if not isinstance(invocation, dict) or set(invocation) != invocation_fields:
                raise ValueError("invalid invocation fields")
            invocation_id = invocation["invocation_id"]
            if (not isinstance(invocation_id, str) or not invocation_id
                    or invocation_id in invocation_by_id):
                raise ValueError("invalid or duplicate invocation id")
            digest = invocation["invocation_sha256"]
            digest_payload = {
                key: value for key, value in invocation.items()
                if key != "invocation_sha256"
            }
            if (not _is_sha256(digest)
                    or digest != _manifest_fingerprint(digest_payload)):
                raise ValueError("invocation digest mismatch")
            requested_stage = invocation["requested_stage"]
            if requested_stage not in INDEX_REQUEST_STAGES:
                raise ValueError("invalid requested_stage")
            expanded = invocation["expanded_required_stages"]
            expected_expanded = (
                list(EVALUATION_STAGES)
                if requested_stage == "all" else [requested_stage]
            )
            if (expanded != expected_expanded
                    or not set(expanded).issubset(required)):
                raise ValueError("invalid expanded_required_stages")
            if (not isinstance(invocation["stages"], dict)
                    or set(invocation["stages"]) != set(expanded)):
                raise ValueError("invocation stage snapshots are incomplete")
            for stage, entry in invocation["stages"].items():
                validate_stage_metadata(stage, entry, invocation_id)
                for field, filename in (
                        ("ledger_path", "expected_cases.jsonl"),
                        ("run_intent_path", "run_intent.json"),
                        ("results_path", "results.jsonl")):
                    if entry[field] != f"raw/{stage}/{filename}":
                        marker = (stage, field, entry[field])
                        if marker not in path_errors:
                            _validated_run_index_path(
                                input_dir, source, stage, entry[field],
                                f"raw/{stage}/{filename}", rejections,
                            )
                            path_errors.add(marker)
            validate_integer_intent(
                invocation["requested_nodes"], "requested_nodes",
                set(EXPECTED_TOPOLOGIES),
            )
            validate_integer_intent(
                invocation["requested_seeds"], "requested_seeds"
            )
            validate_ordered_intent(
                invocation["requested_schemes"], "requested_schemes",
                RUNNER_SCHEMES,
            )
            validate_ordered_intent(
                invocation["requested_cadences"], "requested_cadences",
                CADENCES, allow_empty=True,
            )
            patterns = invocation["scenario_patterns"]
            if (not isinstance(patterns, list)
                    or any(not isinstance(pattern, str) or not pattern
                           for pattern in patterns)
                    or len(patterns) != len(set(patterns))):
                raise ValueError("invalid invocation scenario_patterns")
            if any(
                    entry["scenario_patterns"] != patterns
                    for entry in invocation["stages"].values()):
                raise ValueError(
                    "scenario_patterns do not match stage snapshots"
                )
            staged_scenarios = sorted({
                scenario for entry in invocation["stages"].values()
                for scenario in entry["scenarios"]
            })
            if patterns:
                unmatched_scenarios = [
                    scenario for scenario in staged_scenarios
                    if not _scenario_matches(scenario, patterns)
                ]
                dead_patterns = [
                    pattern for pattern in patterns
                    if not any(pattern in scenario for scenario in staged_scenarios)
                ]
                if unmatched_scenarios or dead_patterns:
                    raise ValueError(
                        "scenario_patterns do not match staged scenarios:"
                        f"unmatched={','.join(unmatched_scenarios)} "
                        f"dead={','.join(dead_patterns)}"
                    )

            snapshot_entries = invocation["stages"].values()
            actual_nodes = sorted({
                nodes for entry in snapshot_entries for nodes in entry["nodes"]
            })
            actual_seeds = sorted({
                seed for entry in invocation["stages"].values()
                for seed in entry["seeds"]
            })
            identities = [
                _candidate_identity(candidate)
                for entry in invocation["stages"].values()
                for candidate in entry["candidates"]
            ]
            actual_schemes = [
                scheme for scheme in RUNNER_SCHEMES
                if any(identity[0] == scheme for identity in identities)
            ]
            actual_cadences = [
                cadence for cadence in CADENCES
                if any(identity[1] == cadence for identity in identities)
            ]
            for field, actual in (
                    ("requested_nodes", actual_nodes),
                    ("requested_seeds", actual_seeds),
                    ("requested_schemes", actual_schemes),
                    ("requested_cadences", actual_cadences)):
                if invocation[field] != actual:
                    raise ValueError(
                        f"{field} do not match invocation stage union"
                    )
            invocation_by_id[invocation_id] = invocation

        validated_stage_paths = {}
        for stage in required:
            entry = stages[stage]
            validate_stage_metadata(
                stage, entry,
                entry.get("invocation_id") if isinstance(entry, dict) else None,
            )
            invocation = invocation_by_id.get(entry["invocation_id"])
            if invocation is None or invocation["stages"].get(stage) != entry:
                raise ValueError(f"stage {stage} is not bound to its invocation")
            declared_paths = {}
            for key, field, filename in (
                    ("ledger", "ledger_path", "expected_cases.jsonl"),
                    ("intent", "run_intent_path", "run_intent.json"),
                    ("results", "results_path", "results.jsonl")):
                declared_paths[key] = _validated_run_index_path(
                    input_dir, source, stage, entry[field],
                    f"raw/{stage}/{filename}", rejections,
                )
            declared_paths["scenario_patterns"] = list(
                entry["scenario_patterns"]
            )
            validated_stage_paths[stage] = declared_paths
            ledger_path = declared_paths["ledger"]
            intent_path = declared_paths["intent"]
            results_path = declared_paths["results"]
            if any(declared_paths[key] is None for key in (
                    "ledger", "intent", "results")):
                continue
            if (not ledger_path.is_file() or not intent_path.is_file()
                    or not results_path.is_file()):
                _run_index_rejection(
                    rejections, source, stage,
                    "run_index_missing_required_stage:ledger, intent, or results "
                    "missing",
                )
                continue
            if hashlib.sha256(ledger_path.read_bytes()).hexdigest() != entry[
                    "ledger_sha256"]:
                _run_index_rejection(
                    rejections, source, stage,
                    "run_index_ledger_hash_mismatch",
                )
            if hashlib.sha256(intent_path.read_bytes()).hexdigest() != entry[
                    "run_intent_sha256"]:
                _run_index_rejection(
                    rejections, source, stage,
                    "run_index_intent_hash_mismatch",
                )
            try:
                raw_ledger = [
                    json.loads(line) for line in ledger_path.read_text(
                        encoding="utf-8"
                    ).splitlines() if line.strip()
                ]
                case_keys = [
                    _ledger_case_key(row["case_key"], ledger_path)
                    for row in raw_ledger
                ]
            except (OSError, json.JSONDecodeError, KeyError, TypeError,
                    ValueError) as error:
                _run_index_rejection(
                    rejections, source, stage,
                    f"run_index_invalid_ledger:{error}",
                )
                continue
            if len(raw_ledger) != entry["case_count"]:
                _run_index_rejection(
                    rejections, source, stage,
                    "run_index_case_count_mismatch",
                )
            identity_keys = [{
                key: case[key] for key in (
                    "stage", "nodes", "scenario", "seed", "scheme", "cadence"
                )
            } for case in case_keys]
            if _manifest_fingerprint(identity_keys) != entry[
                    "case_identity_sha256"]:
                _run_index_rejection(
                    rejections, source, stage,
                    "run_index_case_identity_mismatch",
                )
            projections = {
                "nodes": sorted({case["nodes"] for case in case_keys}),
                "seeds": sorted({case["seed"] for case in case_keys}),
                "scenarios": sorted({case["scenario"] for case in case_keys}),
                "candidates": sorted({_candidate_label(case) for case in case_keys}),
            }
            for field, actual in projections.items():
                if entry[field] != actual:
                    _run_index_rejection(
                        rejections, source, stage,
                        f"run_index_{field}_intent_mismatch",
                    )
        allowed_paths = {
            f"raw/{stage}/{name}"
            for stage in required
            for name in (
                "expected_cases.jsonl", "run_intent.json", "results.jsonl"
            )
        }
        for name in (
                "expected_cases.jsonl", "run_intent.json", "results.jsonl"):
            for path in sorted((input_dir / "raw").rglob(name)):
                try:
                    relative = path.relative_to(input_dir).as_posix()
                except ValueError:
                    relative = str(path)
                if relative not in allowed_paths:
                    _run_index_rejection(
                        rejections, source, relative,
                        "run_index_noncanonical_duplicate:"
                        f"{relative} is not declared by root index",
                    )
        index["_validated_stage_paths"] = validated_stage_paths
        return index
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        _run_index_rejection(
            rejections, source, "run-index", f"run_index_invalid:{error}"
        )
        return None


def _semantic_duplicate_payload(row):
    excluded = {
        "stage", "command", "stdout", "case_dir", "fingerprint",
        "case_config_signature", "selection_provenance_mode",
        "_source", "_source_row",
    }
    return {
        key: value for key, value in row.items()
        if key not in excluded
        and not key.endswith("_path") and not key.endswith("_mtime")
    }


def _dedupe_semantic_rows(rows, rejections):
    groups = defaultdict(list)
    for row in rows:
        groups[(
            row["nodes"], row["scenario"], row["scheme"], row["cadence"],
            row["seed"], row["semantic_case_config_signature"],
            row["traffic_sha256"], row["simulator_sha256"],
            row["parser_version"],
        )].append(row)
    accepted = []
    for key in sorted(groups):
        ordered = sorted(groups[key], key=lambda row: (
            row["stage"], row["_source"], row["_source_row"],
        ))
        by_stage = defaultdict(list)
        for row in ordered:
            by_stage[row["stage"]].append(row)
        unique = []
        for stage in sorted(by_stage):
            stage_rows = by_stage[stage]
            if len(stage_rows) != 1:
                for row in stage_rows:
                    rejections.append(_rejection(
                        row["_source"], row["_source_row"], row,
                        "result_set_duplicate:duplicate semantic case "
                        f"within stage {stage}",
                    ))
                continue
            unique.append(stage_rows[0])
        if not unique:
            continue
        if len(unique) == 1:
            accepted.extend(unique)
            continue
        payloads = {
            _canonical_json(_semantic_duplicate_payload(row)) for row in unique
        }
        stages = ",".join(row["stage"] for row in unique)
        if len(payloads) != 1:
            for row in unique:
                rejections.append(_rejection(
                    row["_source"], row["_source_row"], row,
                    f"duplicate_conflict:stages={stages} semantic results differ",
                ))
            continue
        kept = unique[0]
        accepted.append(kept)
        for row in unique[1:]:
            rejections.append(_rejection(
                row["_source"], row["_source_row"], row,
                f"duplicate_dedup:kept={kept['stage']} dropped={row['stage']} "
                "semantic results identical",
            ))
    return accepted


def load_rows(input_dir):
    """Load and strictly validate stage-local evaluation result sets."""
    input_dir = Path(input_dir)
    rejections = []
    run_index = _load_run_index(input_dir, rejections)
    stage_paths = (
        {} if run_index is None else run_index["_validated_stage_paths"]
    )
    sources = _result_sources(stage_paths)
    if not sources:
        index_path = input_dir / "run_index.json"
        if index_path.exists() or index_path.is_symlink():
            return LoadedRows([], rejections)
        raise ValueError(f"missing input results.jsonl under: {input_dir}")
    expected_cases, run_intents = _load_expected_case_ledgers(
        input_dir, rejections, stage_paths
    )
    expected_index = defaultdict(list)
    for expected in expected_cases:
        expected_index[_case_tuple(expected)].append(expected)
    record_counts = Counter()
    valid_rows = defaultdict(list)
    result_key_fields = (
        "stage", "nodes", "scenario", "seed", "scheme", "cadence"
    )
    for source in sources:
        source_stage = source.parent.name
        with source.open(encoding="utf-8") as handle:
            for row_number, line in enumerate(handle, 1):
                if not line.strip():
                    rejections.append(_rejection(
                        source, row_number, {},
                        "result_set_invalid:empty_row",
                    ))
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as error:
                    rejections.append(_rejection(
                        source, row_number, {},
                        f"result_set_invalid:invalid_json:{error.msg}",
                    ))
                    continue
                if not isinstance(raw, dict):
                    rejections.append(_rejection(
                        source, row_number, {},
                        "result_set_invalid:row_type:result must be an object",
                    ))
                    continue
                try:
                    case = _ledger_case_key(
                        {field: raw[field] for field in result_key_fields},
                        source,
                    )
                except (KeyError, TypeError, ValueError) as error:
                    rejections.append(_rejection(
                        source, row_number, raw,
                        f"result_set_invalid:case_key:{error}",
                    ))
                    continue
                key = _case_tuple(case)
                if case["stage"] != source_stage:
                    rejections.append(_rejection(
                        source, row_number, raw,
                        "result_set_undeclared_case:stage does not match "
                        f"canonical results source {source_stage}",
                    ))
                    continue
                ledger_matches = expected_index.get(key, [])
                if len(ledger_matches) != 1:
                    reason = (
                        "result_set_undeclared_case:not present in stage ledger"
                        if not ledger_matches else
                        "result_set_invalid:expected ledger case is not unique"
                    )
                    rejections.append(_rejection(
                        source, row_number, raw, reason,
                    ))
                    continue
                record_counts[key] += 1
                try:
                    manifest_path = _validated_no_symlink_path(
                        input_dir, _manifest_path(input_dir, raw)
                    )
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    normalized = _normalize_row(raw, manifest)
                    if (source.parent.parent.name == "raw"
                            and normalized["stage"] != source.parent.name):
                        raise ValueError(
                            "stage_source_mismatch:result stage does not match directory"
                        )
                except (OSError, json.JSONDecodeError, OverflowError,
                        TypeError, ValueError) as error:
                    rejections.append(_rejection(
                        source, row_number, raw, str(error)
                    ))
                    continue
                normalized["_source"] = str(source)
                normalized["_source_row"] = row_number
                expected = ledger_matches[0]
                mismatches = []
                for result_field, ledger_field in (
                        ("fingerprint", "fingerprint"),
                        ("traffic_sha256", "traffic_sha256"),
                        ("case_config_signature", "config_identity")):
                    if normalized[result_field] != expected[ledger_field]:
                        mismatches.append(result_field)
                if mismatches:
                    rejections.append(_rejection(
                        source, row_number, normalized,
                        "expected_result_mismatch:" + ",".join(mismatches),
                    ))
                    continue
                valid_rows[key].append(normalized)

    rows = []
    for key, ledger_matches in sorted(expected_index.items()):
        if len(ledger_matches) != 1:
            continue
        expected = ledger_matches[0]
        record_count = record_counts[key]
        valid_count = len(valid_rows[key])
        if record_count > 1:
            rejections.append(_rejection(
                expected["_source"], expected["_source_row"], expected,
                f"result_set_duplicate:records={record_count} expected=1",
            ))
        if valid_count != 1:
            rejections.append(_rejection(
                expected["_source"], expected["_source_row"], expected,
                "result_set_expected_count:"
                f"valid_records={valid_count} expected=1 "
                f"records={record_count}",
            ))
        if record_count == 1 and valid_count == 1:
            rows.append(valid_rows[key][0])

    if not rows:
        return LoadedRows(
            [], rejections, expected_cases=expected_cases,
            run_intents=run_intents,
        )
    validation_source = "cross_stage_results"
    rows = _reject_outliers(
        rows, rejections, validation_source,
        "simulator_sha256", "mixed_simulator_hash"
    )
    rows = _reject_outliers(
        rows, rejections, validation_source, "traffic_sha256",
        "mixed_workload_identity:traffic_sha256",
        group_key=lambda row: (
            row["nodes"], row["scenario"], row["seed"]
        ),
    )
    rows = _dedupe_semantic_rows(rows, rejections)
    rows = _reject_outliers(
        rows, rejections, validation_source,
        "semantic_case_config_signature", "mixed_case_config",
        group_key=lambda row: (
            row["nodes"], row["scenario"], row["scheme"], row["cadence"]
        ),
    )
    rows = _reject_outliers(
        rows, rejections, validation_source, "case_comparability_signature",
        "mixed_case_comparability:expected_flow_counts/config",
        group_key=lambda row: (
            row["nodes"], row["scenario"], row["seed"], row["scheme"]
        ),
    )
    rows.sort(key=lambda row: (
        row["nodes"], row["scenario"], row["scheme"], row["cadence"],
        row["seed"], row["_source_row"],
    ))
    rejections.sort(key=lambda item: (
        str(item["source"]), int(item["row"]), item["case"], item["reason"]
    ))
    return LoadedRows(
        rows, rejections, expected_cases=expected_cases,
        run_intents=run_intents,
    )


def aggregate(rows):
    """Aggregate accepted run rows by nodes, scenario, scheme, and cadence."""
    groups = defaultdict(list)
    for row in rows:
        groups[(
            row["nodes"], row["scenario"], row["scheme"], row["cadence"]
        )].append(row)
    result = {}
    for key in sorted(groups):
        group = groups[key]
        record = {
            "nodes": key[0], "scenario": key[1], "scheme": key[2],
            "cadence": key[3], "seed_count": len(group),
        }
        for metric in SUMMARY_METRICS:
            values = [row[metric] for row in group]
            record[f"{metric}_median"] = statistics.median(values)
            record[f"{metric}_min"] = min(values)
            record[f"{metric}_max"] = max(values)
        result[key] = record
    return result


def _paired_log_ratio(candidate, reference, metric):
    candidate = _number(candidate, f"candidate {metric}")
    reference = _number(reference, f"reference {metric}")
    if candidate <= 0 or reference <= 0:
        raise ValueError(
            f"same-seed {metric} values must be finite positive"
        )
    log_ratio = math.log(candidate) - math.log(reference)
    if not math.isfinite(log_ratio):
        raise ValueError(f"derived same-seed ratio for {metric} is not finite")
    try:
        represented_ratio = math.exp(log_ratio)
    except OverflowError:
        represented_ratio = math.inf
    if not math.isfinite(represented_ratio) or represented_ratio <= 0:
        raise ValueError(
            f"derived same-seed ratio for {metric} is not finite positive"
        )
    return log_ratio


def _geomean_log_ratios(log_ratios):
    if not log_ratios:
        raise ValueError("geometric mean requires paired log-ratios")
    mean_log_ratio = math.fsum(log_ratios) / len(log_ratios)
    try:
        result = math.exp(mean_log_ratio)
    except OverflowError:
        result = math.inf
    if not math.isfinite(result) or result <= 0:
        raise ValueError("derived geometric-mean ratio is not finite positive")
    return result


def _decision(subject, cadence, rule, status, value="", threshold="",
              paired_cases="", baseline="", evidence="", scenario=""):
    return {
        "subject": subject,
        "cadence": cadence,
        "scenario": scenario,
        "rule": rule,
        "status": status,
        "value": value,
        "threshold": threshold,
        "paired_cases": paired_cases,
        "baseline": baseline,
        "evidence": evidence,
    }


def _rejection_matches(item, subject, cadence, nodes):
    try:
        item_nodes = int(item.get("nodes", -1))
    except (TypeError, ValueError):
        return False
    return (
        item.get("scheme") == subject and item.get("cadence") == cadence
        and item_nodes in nodes
    )


def _recommend(rows, rejections, expected_cases):
    decisions = []
    selected = {}
    blockers = []
    expected_subject_cases = [
        item for item in expected_cases
        if item["scheme"] in SUBJECTS
        and item["stage"] in CADENCE_SELECTION_STAGES
    ]
    node_set = {item["nodes"] for item in expected_subject_cases}
    evaluation_seeds = sorted({item["seed"] for item in expected_subject_cases})
    ledger_blockers = [
        item for item in rejections
        if (not item.get("stage")
            or item["stage"] in CADENCE_SELECTION_STAGES)
        and any(marker in item["reason"] for marker in (
            "ledger", "run_intent", "run_index", "expected_result",
            "duplicate_conflict", "result_set_",
        ))
    ]
    by_subject = defaultdict(list)
    for row in rows:
        if (row["scheme"] in SUBJECTS
                and row["stage"] in CADENCE_SELECTION_STAGES):
            by_subject[row["scheme"]].append(row)

    subject_evidence_invalid = defaultdict(list)
    for subject in SUBJECTS:
        actual = {
            (row["nodes"], row["scenario"], row["seed"], row["cadence"]): row
            for row in by_subject[subject]
        }
        required = sorted({
            (
                item["nodes"], item["scenario"], item["seed"],
                item["evidence_class"],
            )
            for item in expected_subject_cases
            if item["scheme"] == subject
            and item["evidence_class"] in REQUIRED_EVIDENCE_CLASSES
        })
        seen_invalid = set()
        for nodes, scenario, seed, evidence_class in required:
            metric = (
                "all_to_all_cct_us"
                if evidence_class == "alltoall" else "p99_fct_us"
            )
            reference = actual.get((nodes, scenario, seed, "fixed5"))
            for cadence in CADENCES:
                candidate = actual.get((nodes, scenario, seed, cadence))
                if reference is None or candidate is None:
                    detail = (
                        f"nodes={nodes} scenario={scenario} seed={seed} "
                        f"cadence={cadence} metric={metric} missing paired "
                        "candidate/reference"
                    )
                elif (not math.isfinite(reference[metric])
                        or reference[metric] <= 0
                        or not math.isfinite(candidate[metric])
                        or candidate[metric] <= 0):
                    detail = (
                        f"nodes={nodes} scenario={scenario} seed={seed} "
                        f"cadence={cadence} metric={metric} requires finite "
                        f"positive candidate/reference; candidate="
                        f"{candidate[metric]!r} reference={reference[metric]!r}"
                    )
                else:
                    continue
                if detail in seen_invalid:
                    continue
                seen_invalid.add(detail)
                subject_evidence_invalid[subject].append(detail)
                rejections.append({
                    "source": "recommendation_evidence",
                    "row": 0,
                    "case": f"{nodes}/{seed}/{scenario}/{subject}/{cadence}",
                    "reason": f"evidence_invalid:{detail}",
                    "nodes": nodes,
                    "seed": seed,
                    "scenario": scenario,
                    "scheme": subject,
                    "cadence": cadence,
                })

    registry_missing = defaultdict(list)
    expected_node_seeds = sorted({
        (item["nodes"], item["seed"]) for item in expected_subject_cases
    })
    for subject in SUBJECTS:
        for cadence in CADENCES:
            for nodes, seed in expected_node_seeds:
                observed = {
                    row["evidence_class"] for row in by_subject[subject]
                    if row["cadence"] == cadence
                    and row["nodes"] == nodes and row["seed"] == seed
                }
                missing = [
                    evidence_class
                    for evidence_class in REQUIRED_EVIDENCE_CLASSES
                    if evidence_class not in observed
                ]
                if not missing:
                    continue
                detail = (
                    f"nodes={nodes} seed={seed} classes={','.join(missing)} "
                    f"registry={','.join(REQUIRED_EVIDENCE_CLASSES)}"
                )
                registry_missing[subject].append((cadence, detail))
                rejections.append({
                    "source": "recommendation_registry",
                    "row": 0,
                    "case": f"{nodes}/{seed}/required-evidence/{subject}/{cadence}",
                    "reason": f"missing_required_evidence:{detail}",
                    "nodes": nodes,
                    "seed": seed,
                    "scenario": "required-evidence",
                    "scheme": subject,
                    "cadence": cadence,
                })

    for subject in SUBJECTS:
        subject_rows = by_subject[subject]
        subject_expected = [
            item for item in expected_subject_cases
            if item["scheme"] == subject
        ]
        union_grid = {
            (item["nodes"], item["scenario"], item["seed"])
            for item in subject_expected
        }
        candidates = {}
        for cadence in CADENCES:
            invalid_evidence = subject_evidence_invalid[subject]
            decisions.append(_decision(
                subject, cadence, "evidence_invalid",
                "fail" if invalid_evidence else "pass",
                value=len(invalid_evidence), threshold=0,
                evidence=(
                    "; ".join(invalid_evidence)
                    if invalid_evidence else
                    "all required paired metrics are finite positive"
                ),
            ))
            candidate_rows = [
                row for row in subject_rows if row["cadence"] == cadence
            ]
            candidate_index = {
                (row["nodes"], row["scenario"], row["seed"]): row
                for row in candidate_rows
            }
            candidate_rejections = [
                item for item in rejections
                if _rejection_matches(item, subject, cadence, node_set)
                and (not item.get("stage")
                     or item["stage"] in CADENCE_SELECTION_STAGES)
                and not item["reason"].startswith("duplicate_dedup:")
            ] + ledger_blockers
            candidate_registry_missing = [
                detail for missing_cadence, detail in registry_missing[subject]
                if missing_cadence == cadence
            ]
            decisions.append(_decision(
                subject, cadence, "required_evidence_registry",
                "fail" if candidate_registry_missing else "pass",
                value=len(candidate_registry_missing), threshold=0,
                evidence=(
                    "; ".join(candidate_registry_missing)
                    if candidate_registry_missing else
                    "each expected node/seed includes healthy_guardrail, incast_guardrail, asymmetric, mixed, periodic_background, and alltoall evidence"
                ),
            ))
            complete_rejections = [
                item for item in candidate_rejections
                if "incomplete_flows" in item["reason"]
                or "flow_count_mismatch" in item["reason"]
            ]
            decisions.append(_decision(
                subject, cadence, "complete_flows",
                "fail" if complete_rejections else "pass",
                value=len(complete_rejections), threshold=0,
                evidence=(
                    "; ".join(item["case"] for item in complete_rejections)
                    or "all accepted rows report every target/background flow complete"
                ),
            ))
            if candidate_rejections:
                decisions.append(_decision(
                    subject, cadence, "input_quality", "fail",
                    value=len(candidate_rejections), threshold=0,
                    evidence="; ".join(sorted({
                        item["reason"] for item in candidate_rejections
                    })),
                ))
            else:
                decisions.append(_decision(
                    subject, cadence, "input_quality", "pass", value=0,
                    threshold=0, evidence="no rejected rows for candidate",
                ))

            missing_grid = sorted(union_grid - set(candidate_index))
            baseline_index = {
                (row["nodes"], row["scenario"], row["seed"]): row
                for row in subject_rows if row["cadence"] == "fixed5"
            }
            opportunity_rows = [
                row for row in candidate_rows if row["is_opportunity"]
            ]
            log_ratios = []
            lower_tail_log_ratios = []
            opportunity_direction_groups = defaultdict(list)
            opportunity_recovery_groups = defaultdict(list)
            pairing_errors = []
            if registry_missing[subject]:
                pairing_errors.append(
                    "missing required evidence for subject comparison: "
                    + "; ".join(
                        f"{missing_cadence} {detail}"
                        for missing_cadence, detail in registry_missing[subject]
                    )
                )
            if len(evaluation_seeds) < 3:
                pairing_errors.append(
                    "recommendation requires at least 3 seeds; "
                    f"found {evaluation_seeds}"
                )
            for row in opportunity_rows:
                key = (row["nodes"], row["scenario"], row["seed"])
                baseline = baseline_index.get(key)
                if baseline is None:
                    pairing_errors.append(
                        f"missing same-seed baseline for {key}"
                    )
                    continue
                metric = (
                    "all_to_all_cct_us" if row["is_alltoall"]
                    else "p99_fct_us"
                )
                denominator = baseline[metric]
                if denominator == 0:
                    pairing_errors.append(
                        f"zero denominator for {key} metric={metric}"
                    )
                    continue
                try:
                    log_ratio = _paired_log_ratio(
                        row[metric], denominator, metric
                    )
                    log_ratios.append(log_ratio)
                    opportunity_direction_groups[
                        (row["nodes"], row["scenario"])
                    ].append(math.exp(log_ratio))
                    nack_delta = max(0, row["nacks"] - baseline["nacks"])
                    retx_delta = max(
                        0, row["retx_packets"] - baseline["retx_packets"]
                    )
                    opportunity_recovery_groups[
                        (row["nodes"], row["scenario"])
                    ].append({
                        "rto_delta": max(0, row["rtos"] - baseline["rtos"]),
                        "new_recovery": nack_delta > 0 or retx_delta > 0,
                        "recovery_packets": nack_delta + retx_delta,
                        "retx_ratio_delta": max(
                            0.0, row["retx_ratio"] - baseline["retx_ratio"]
                        ),
                    })
                except ValueError as error:
                    pairing_errors.append(f"{key} {error}")
                    continue
                if not row["is_alltoall"]:
                    lower_denominator = baseline["p50_fct_us"]
                    if lower_denominator == 0:
                        pairing_errors.append(
                            f"zero denominator for {key} metric=p50_fct_us"
                        )
                    else:
                        try:
                            lower_tail_log_ratios.append(_paired_log_ratio(
                                row["p50_fct_us"], lower_denominator,
                                "p50_fct_us",
                            ))
                        except ValueError as error:
                            pairing_errors.append(f"{key} {error}")
            if missing_grid:
                pairing_errors.append(
                    f"missing seed/case rows: {missing_grid}"
                )
            expected_opportunities = sum(
                1 for key in union_grid
                if any(
                    row["is_opportunity"]
                    for row in subject_rows
                    if (row["nodes"], row["scenario"], row["seed"]) == key
                )
            )
            if expected_opportunities == 0:
                pairing_errors.append(
                    f"no opportunity cases for subject {subject}"
                )
            if len(log_ratios) != expected_opportunities:
                pairing_errors.append(
                    f"paired {len(log_ratios)} of {expected_opportunities} opportunity cases"
                )
            pairing_status = "fail" if pairing_errors else "pass"
            decisions.append(_decision(
                subject, cadence, "same_seed_pairing", pairing_status,
                value=len(log_ratios), threshold=expected_opportunities,
                paired_cases=len(log_ratios),
                baseline="fixed5 same subject/nodes/scenario/seed",
                evidence="; ".join(pairing_errors) or "all opportunity rows paired",
            ))

            direction_failures = []
            for group_key in sorted(opportunity_direction_groups):
                scenario_ratios = opportunity_direction_groups[group_key]
                paired = len(scenario_ratios)
                required_majority = math.ceil(2 * paired / 3)
                improved = sum(
                    ratio < OPPORTUNITY_IMPROVEMENT_THRESHOLD
                    for ratio in scenario_ratios
                )
                regressed = sum(
                    ratio > OPPORTUNITY_REGRESSION_THRESHOLD
                    for ratio in scenario_ratios
                )
                neutral = paired - improved - regressed
                if paired < MIN_GUARDRAIL_PAIRED_SEEDS:
                    classification = "insufficient"
                    status = "fail"
                elif regressed >= required_majority:
                    classification = "regressed"
                    status = "fail"
                elif improved >= MIN_GUARDRAIL_PAIRED_SEEDS:
                    classification = "improved"
                    status = "pass"
                else:
                    classification = "neutral"
                    status = "pass"
                detail = (
                    f"classification={classification} paired_seeds={paired} "
                    f"improved={improved} neutral={neutral} regressed={regressed} "
                    f"neutral_band=[{OPPORTUNITY_IMPROVEMENT_THRESHOLD:.9g},"
                    f"{OPPORTUNITY_REGRESSION_THRESHOLD:.9g}] "
                    f"required_regressed_majority={required_majority}/{paired}"
                )
                if status == "fail":
                    direction_failures.append(f"{group_key} {detail}")
                decisions.append(_decision(
                    subject, cadence, "opportunity_direction_guardrail", status,
                    value=classification,
                    threshold="regressed_seeds<2/3; improved requires >=3 seeds below 0.97",
                    paired_cases=paired,
                    baseline="fixed5 same subject/nodes/scenario/seed",
                    evidence=detail,
                    scenario=group_key[1],
                ))

            opportunity_recovery_failures = []
            for group_key in sorted(opportunity_recovery_groups):
                group = opportunity_recovery_groups[group_key]
                paired = len(group)
                required_majority = math.ceil(2 * paired / 3)
                rto_events = sum(item["rto_delta"] > 0 for item in group)
                median_rto_delta = statistics.median(
                    item["rto_delta"] for item in group
                )
                recovery_events = sum(
                    item["new_recovery"] for item in group
                )
                median_recovery = statistics.median(
                    item["recovery_packets"] for item in group
                )
                median_retx_ratio_delta = statistics.median(
                    item["retx_ratio_delta"] for item in group
                )
                rto_failure = (
                    paired >= MIN_GUARDRAIL_PAIRED_SEEDS
                    and rto_events >= required_majority
                    and median_rto_delta > 0
                )
                recovery_failure = (
                    paired >= MIN_GUARDRAIL_PAIRED_SEEDS
                    and recovery_events >= required_majority
                    and (
                        median_recovery >= RECOVERY_BDP_PACKETS
                        or median_retx_ratio_delta
                        >= RECOVERY_RETX_RATIO_THRESHOLD
                    )
                )
                failed = rto_failure or recovery_failure
                warning = not failed and (rto_events or recovery_events)
                detail = (
                    f"{'warning ' if warning else ''}paired_seeds={paired} "
                    f"rto_increase_seeds={rto_events}/{paired} "
                    f"median_rto_delta={median_rto_delta:.9g} RTOs "
                    f"new_recovery_seeds={recovery_events}/{paired} "
                    f"median_recovery_packets={median_recovery:.9g} packets "
                    f"BDP={RECOVERY_BDP_PACKETS} packets "
                    f"median_retx_ratio_delta={median_retx_ratio_delta:.9g} "
                    f"retx_ratio_delta_threshold={RECOVERY_RETX_RATIO_THRESHOLD:.9g} "
                    f"required_majority={required_majority}/{paired}"
                )
                if failed:
                    opportunity_recovery_failures.append(
                        f"{group_key} {detail}"
                    )
                decisions.append(_decision(
                    subject, cadence, "opportunity_recovery_guardrail",
                    "fail" if failed else "pass",
                    value=int(failed),
                    threshold=(
                        "rto majority with median delta>0; or recovery majority "
                        "with median delta>=86 packets or retx_ratio delta>=0.001"
                    ),
                    paired_cases=paired,
                    baseline="fixed5 same subject/nodes/scenario/seed",
                    evidence=detail,
                    scenario=group_key[1],
                ))

            healthy_groups = defaultdict(list)
            incast_groups = defaultdict(list)
            healthy_pairs = 0
            for row in candidate_rows:
                if not (row["is_healthy"] or row["is_incast"]):
                    continue
                key = (row["nodes"], row["scenario"], row["seed"])
                baseline = baseline_index.get(key)
                if baseline is None or baseline["p99_fct_us"] == 0:
                    continue
                if row["is_healthy"]:
                    healthy_pairs += 1
                ratio = row["p99_fct_us"] / baseline["p99_fct_us"]
                nack_delta = max(0, row["nacks"] - baseline["nacks"])
                retx_delta = max(
                    0, row["retx_packets"] - baseline["retx_packets"]
                )
                group = healthy_groups if row["is_healthy"] else incast_groups
                group[(row["nodes"], row["scenario"])].append({
                    "seed": row["seed"],
                    "p99_ratio": ratio,
                    "new_recovery": nack_delta > 0 or retx_delta > 0,
                    "recovery_packets": nack_delta + retx_delta,
                    "retx_ratio": row["retx_ratio"],
                    "rto_delta": row["rtos"] - baseline["rtos"],
                })

            stable_failures = []
            stable_warnings = []
            cascade_failures = []
            cascade_warnings = []
            healthy_rto_failures = []
            healthy_rto_warnings = []
            stable_medians = []
            for group_key in sorted(healthy_groups):
                group = healthy_groups[group_key]
                paired = len(group)
                required_majority = math.ceil(2 * paired / 3)
                p99_ratios = [item["p99_ratio"] for item in group]
                median_ratio = statistics.median(p99_ratios)
                stable_medians.append(median_ratio)
                regressed = sum(
                    ratio > STABLE_P99_RATIO_THRESHOLD
                    for ratio in p99_ratios
                )
                stable = (
                    paired >= MIN_GUARDRAIL_PAIRED_SEEDS
                    and median_ratio > STABLE_P99_RATIO_THRESHOLD
                    and regressed >= required_majority
                )
                stable_detail = (
                    f"{group_key} paired_seeds={paired} "
                    f"median_ratio={median_ratio:.9g} "
                    f"regressed_seeds={regressed}/{paired} "
                    f"required_majority={required_majority}/{paired} "
                    f"ratio_threshold={STABLE_P99_RATIO_THRESHOLD:.9g}"
                )
                if stable:
                    stable_failures.append(stable_detail)
                elif regressed:
                    stable_warnings.append("warning " + stable_detail)

                recovery_events = sum(
                    item["new_recovery"] for item in group
                )
                median_recovery = statistics.median(
                    item["recovery_packets"] for item in group
                )
                median_retx_ratio = statistics.median(
                    item["retx_ratio"] for item in group
                )
                cascade = (
                    paired >= MIN_GUARDRAIL_PAIRED_SEEDS
                    and recovery_events >= required_majority
                    and (
                        median_recovery >= RECOVERY_BDP_PACKETS
                        or median_retx_ratio >= RECOVERY_RETX_RATIO_THRESHOLD
                    )
                )
                cascade_detail = (
                    f"{group_key} paired_seeds={paired} "
                    f"new_recovery_seeds={recovery_events}/{paired} "
                    f"required_majority={required_majority}/{paired} "
                    f"median_recovery_packets={median_recovery:.9g} "
                    f"BDP={RECOVERY_BDP_PACKETS} packets "
                    f"median_retx_ratio={median_retx_ratio:.9g} "
                    f"retx_ratio_threshold={RECOVERY_RETX_RATIO_THRESHOLD:.9g}"
                )
                if cascade:
                    cascade_failures.append(cascade_detail)
                elif recovery_events:
                    cascade_warnings.append("warning " + cascade_detail)

                rto_events = sum(item["rto_delta"] > 0 for item in group)
                median_rto_delta = statistics.median(
                    item["rto_delta"] for item in group
                )
                stable_rto = (
                    paired >= MIN_GUARDRAIL_PAIRED_SEEDS
                    and rto_events >= required_majority
                    and median_rto_delta > 0
                )
                rto_detail = (
                    f"{group_key} paired_seeds={paired} "
                    f"rto_increase_seeds={rto_events}/{paired} "
                    f"required_majority={required_majority}/{paired} "
                    f"median_rto_delta={median_rto_delta:.9g} RTOs"
                )
                if stable_rto:
                    healthy_rto_failures.append(rto_detail)
                elif rto_events:
                    healthy_rto_warnings.append("warning " + rto_detail)

            stable_evidence = stable_failures or stable_warnings or [
                "no cross-seed stable healthy p99 regression; "
                f"minimum_paired_seeds={MIN_GUARDRAIL_PAIRED_SEEDS} "
                f"ratio_threshold={STABLE_P99_RATIO_THRESHOLD:.9g}"
            ]
            decisions.append(_decision(
                subject, cadence, "healthy_p99_guardrail",
                "fail" if stable_failures else "pass",
                value=max(stable_medians, default=1.0),
                threshold=(
                    f"paired_seeds>={MIN_GUARDRAIL_PAIRED_SEEDS}; "
                    f"median_ratio>{STABLE_P99_RATIO_THRESHOLD:.9g}; "
                    "regressed_seeds>=2/3"
                ),
                paired_cases=healthy_pairs,
                baseline="fixed5 same subject/nodes/scenario/seed",
                evidence="; ".join(stable_evidence),
            ))
            cascade_evidence = cascade_failures or cascade_warnings or [
                "no cross-seed healthy recovery cascade; "
                f"minimum_paired_seeds={MIN_GUARDRAIL_PAIRED_SEEDS} "
                f"BDP={RECOVERY_BDP_PACKETS} packets "
                f"retx_ratio_threshold={RECOVERY_RETX_RATIO_THRESHOLD:.9g}"
            ]
            decisions.append(_decision(
                subject, cadence, "healthy_nack_retx_guardrail",
                "fail" if cascade_failures else "pass",
                value=len(cascade_failures),
                threshold=(
                    f"paired_seeds>={MIN_GUARDRAIL_PAIRED_SEEDS}; "
                    "new_recovery_seeds>=2/3; "
                    f"median(nacks+retx)>={RECOVERY_BDP_PACKETS} packets "
                    f"or median_retx_ratio>={RECOVERY_RETX_RATIO_THRESHOLD:.9g}"
                ),
                paired_cases=healthy_pairs,
                baseline="fixed5 same subject/nodes/scenario/seed",
                evidence="; ".join(cascade_evidence),
            ))
            healthy_rto_evidence = (
                healthy_rto_failures or healthy_rto_warnings or [
                    "no cross-seed healthy RTO increase; "
                    f"minimum_paired_seeds={MIN_GUARDRAIL_PAIRED_SEEDS}"
                ]
            )
            decisions.append(_decision(
                subject, cadence, "healthy_rto_guardrail",
                "fail" if healthy_rto_failures else "pass",
                value=len(healthy_rto_failures),
                threshold=(
                    f"paired_seeds>={MIN_GUARDRAIL_PAIRED_SEEDS}; "
                    "rto_increase_seeds>=2/3; median_rto_delta>0"
                ),
                paired_cases=healthy_pairs,
                baseline="fixed5 same subject/nodes/scenario/seed",
                evidence="; ".join(healthy_rto_evidence),
            ))

            incast_failures = []
            for group_key in sorted(incast_groups):
                group = incast_groups[group_key]
                paired = len(group)
                required_majority = math.ceil(2 * paired / 3)
                ratios = [item["p99_ratio"] for item in group]
                median_ratio = statistics.median(ratios)
                regressed = sum(
                    ratio > STABLE_P99_RATIO_THRESHOLD for ratio in ratios
                )
                stable_p99 = (
                    paired >= MIN_GUARDRAIL_PAIRED_SEEDS
                    and median_ratio > STABLE_P99_RATIO_THRESHOLD
                    and regressed >= required_majority
                )
                recovery_events = sum(
                    item["new_recovery"] for item in group
                )
                median_recovery = statistics.median(
                    item["recovery_packets"] for item in group
                )
                median_retx_ratio = statistics.median(
                    item["retx_ratio"] for item in group
                )
                recovery_cascade = (
                    paired >= MIN_GUARDRAIL_PAIRED_SEEDS
                    and recovery_events >= required_majority
                    and (
                        median_recovery >= RECOVERY_BDP_PACKETS
                        or median_retx_ratio
                        >= RECOVERY_RETX_RATIO_THRESHOLD
                    )
                )
                rto_events = sum(item["rto_delta"] > 0 for item in group)
                median_rto_delta = statistics.median(
                    item["rto_delta"] for item in group
                )
                stable_rto = (
                    paired >= MIN_GUARDRAIL_PAIRED_SEEDS
                    and rto_events >= required_majority
                    and median_rto_delta > 0
                )
                failed = stable_p99 or recovery_cascade or stable_rto
                warning = not failed and (
                    regressed or recovery_events or rto_events
                )
                detail = (
                    f"{'warning ' if warning else ''}"
                    f"stable_p99={str(stable_p99).lower()} "
                    f"recovery_cascade={str(recovery_cascade).lower()} "
                    f"stable_rto={str(stable_rto).lower()} "
                    f"paired_seeds={paired} "
                    f"median_p99_ratio={median_ratio:.9g} "
                    f"regressed_seeds={regressed}/{paired} "
                    f"new_recovery_seeds={recovery_events}/{paired} "
                    f"median_recovery_packets={median_recovery:.9g} packets "
                    f"median_retx_ratio={median_retx_ratio:.9g} "
                    f"rto_increase_seeds={rto_events}/{paired} "
                    f"median_rto_delta={median_rto_delta:.9g} RTOs "
                    f"required_majority={required_majority}/{paired}"
                )
                if failed:
                    incast_failures.append(f"{group_key} {detail}")
                decisions.append(_decision(
                    subject, cadence, "incast_guardrail",
                    "fail" if failed else "pass",
                    value=int(failed),
                    threshold=(
                        "same cross-seed stable p99, recovery cascade, and "
                        "RTO increase rules as healthy"
                    ),
                    paired_cases=paired,
                    baseline="fixed5 same subject/nodes/scenario/seed",
                    evidence=detail,
                    scenario=group_key[1],
                ))

            score = (
                None if pairing_errors
                else _geomean_log_ratios(log_ratios)
            )
            lower_score = (
                _geomean_log_ratios(lower_tail_log_ratios)
                if lower_tail_log_ratios else math.inf
            )
            decisions.append(_decision(
                subject, cadence, "opportunity_geomean",
                "fail" if score is None else "pass",
                value="" if score is None else score,
                paired_cases=len(log_ratios),
                baseline="fixed5 same subject/nodes/scenario/seed",
                evidence=(
                    "exp(mean(log(candidate)-log(reference))); "
                    "ordinary=p99_fct_us; alltoall=all_to_all_cct_us; "
                    "lower is better"
                ),
            ))
            disqualified = bool(
                candidate_rejections or pairing_errors
                or stable_failures or cascade_failures or direction_failures
                or healthy_rto_failures or opportunity_recovery_failures
                or incast_failures or invalid_evidence
            )
            guardrail_failed = bool(
                candidate_rejections or stable_failures or cascade_failures
                or healthy_rto_failures or direction_failures
                or opportunity_recovery_failures
                or incast_failures or invalid_evidence
            )
            decisions.append(_decision(
                subject, cadence, "guardrail",
                "fail" if guardrail_failed else "pass",
                evidence=(
                    "candidate has rejected/incomplete rows or violates a healthy, incast, or opportunity guardrail"
                    if guardrail_failed else
                    "input quality, completion, healthy, incast, and opportunity guardrails pass"
                ),
            ))
            decisions.append(_decision(
                subject, cadence, "geomean_eligibility",
                "fail" if disqualified or score is None else "pass",
                value="" if score is None else score,
                paired_cases=len(log_ratios),
                baseline="fixed5 same subject/nodes/scenario/seed",
                evidence=(
                    "candidate is ineligible after guardrail/pairing checks"
                    if disqualified or score is None else
                    "candidate is eligible with the recorded opportunity geometric mean"
                ),
            ))
            candidates[cadence] = {
                "rows": candidate_rows,
                "score": score,
                "lower_score": lower_score,
                "feedback_bytes": sum(
                    row["feedback_bytes"] for row in opportunity_rows
                ),
                "disqualified": disqualified,
            }

        eligible = [
            (cadence, data) for cadence, data in candidates.items()
            if not data["disqualified"] and data["score"] is not None
        ]
        if not eligible:
            for cadence in CADENCES:
                for rule, evidence in (
                    ("three_percent_cutoff", "no eligible geomean candidate"),
                    ("feedback_bytes_tiebreak", "candidate did not pass the 3% cutoff"),
                    ("final_tiebreak", "no candidate reached the final tie stage"),
                    ("final_recommendation", "recommendation is blocked"),
                ):
                    decisions.append(_decision(
                        subject, cadence, rule, "fail", evidence=evidence,
                    ))
            decisions.append(_decision(
                subject, "", "final_selection", "blocked",
                evidence="no cadence satisfies data quality, pairing, and guardrails",
            ))
            blockers.extend(
                item["reason"] for item in rejections
                if item.get("scheme") == subject
            )
            blockers.extend(
                row["evidence"] for row in decisions
                if row["subject"] == subject and row["status"] == "fail"
            )
            continue
        best_score = min(data["score"] for _, data in eligible)
        if best_score == 0:
            shortlist = [item for item in eligible if item[1]["score"] == 0]
        else:
            shortlist = [
                item for item in eligible
                if item[1]["score"] / best_score < 1.03
            ]
        shortlist_names = {cadence for cadence, unused_data in shortlist}
        for cadence in CADENCES:
            data = candidates[cadence]
            relative = ""
            if data["score"] is not None:
                relative = (
                    1.0 if best_score == 0 and data["score"] == 0
                    else math.inf if best_score == 0
                    else data["score"] / best_score
                )
            decisions.append(_decision(
                subject, cadence, "three_percent_cutoff",
                "pass" if cadence in shortlist_names else "fail",
                value=relative, threshold="<1.03 x best eligible geomean",
                evidence=(
                    "candidate is within the strict <3% performance band"
                    if cadence in shortlist_names else
                    "candidate is ineligible or outside the strict <3% performance band"
                ),
            ))
        least_feedback = min(data["feedback_bytes"] for _, data in shortlist)
        feedback_shortlist = [
            item for item in shortlist
            if item[1]["feedback_bytes"] == least_feedback
        ]
        feedback_names = {
            cadence for cadence, unused_data in feedback_shortlist
        }
        for cadence in CADENCES:
            data = candidates[cadence]
            decisions.append(_decision(
                subject, cadence, "feedback_bytes_tiebreak",
                "pass" if cadence in feedback_names else "fail",
                value=data["feedback_bytes"], threshold=least_feedback,
                evidence=(
                    "candidate remains tied at the minimum feedback payload bytes"
                    if cadence in feedback_names else
                    "candidate missed the 3% cutoff or uses more feedback payload bytes"
                ),
            ))
        for cadence, data in shortlist:
            decisions.append(_decision(
                subject, cadence, "feedback_tiebreak",
                "pass" if data["feedback_bytes"] == least_feedback else "fail",
                value=data["feedback_bytes"], threshold=least_feedback,
                evidence="candidate is within <3% of best opportunity geomean; lower feedback payload bytes wins",
            ))
        if len(feedback_shortlist) > 1 and subject == "avail":
            lower_tail_candidates = list(feedback_shortlist)
            best_lower = min(data["lower_score"] for _, data in feedback_shortlist)
            feedback_shortlist = [
                item for item in feedback_shortlist
                if item[1]["lower_score"] == best_lower
            ]
            for cadence, data in lower_tail_candidates:
                decisions.append(_decision(
                    subject, cadence, "avail_lower_tail_tiebreak",
                    "pass" if data["lower_score"] == best_lower else "fail",
                    value=data["lower_score"], threshold=best_lower,
                    baseline="fixed5 same subject/nodes/scenario/seed",
                    evidence="ordinary opportunity same-seed p50 geometric mean",
                ))
        cadence_names = [cadence for cadence, _ in feedback_shortlist]
        if len(cadence_names) > 1 and subject in ("grade", "netaware"):
            chosen = "fixed5" if "fixed5" in cadence_names else min(cadence_names)
            decisions.append(_decision(
                subject, chosen, "fixed5_final_tiebreak", "pass",
                evidence="feedback and opportunity performance remain tied",
            ))
        else:
            chosen = min(cadence_names, key=CADENCES.index)
        selected[subject] = chosen
        final_candidates = {
            cadence for cadence, unused_data in feedback_shortlist
        }
        for cadence in CADENCES:
            decisions.append(_decision(
                subject, cadence, "final_tiebreak",
                "pass" if cadence == chosen else "fail",
                value=candidates[cadence]["score"],
                evidence=(
                    "candidate wins the final subject-specific tie policy"
                    if cadence == chosen else
                    ("candidate reached the final tie but did not win"
                     if cadence in final_candidates else
                     "candidate did not reach the final tie")
                ),
            ))
            decisions.append(_decision(
                subject, cadence, "final_recommendation",
                "pass" if cadence == chosen else "fail",
                value=candidates[cadence]["score"],
                evidence=(
                    "selected cadence recommendation"
                    if cadence == chosen else "cadence not selected"
                ),
            ))
        decisions.append(_decision(
            subject, chosen, "final_selection", "selected",
            value=candidates[chosen]["score"],
            baseline="fixed5 same subject/nodes/scenario/seed",
            evidence="selected after guardrails, opportunity score, feedback bytes, and final subject tiebreak",
        ))
    return selected, decisions, blockers


def _atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_csv(path, rows, fieldnames):
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    _atomic_text(path, buffer.getvalue())


def _summary_fields():
    fields = ["nodes", "scenario", "scheme", "cadence", "seed_count"]
    for metric in SUMMARY_METRICS:
        fields.extend(
            f"{metric}_{statistic}" for statistic in ("median", "min", "max")
        )
    return fields


def _cadence_flags(scheme, cadence):
    prefix = "netaware" if scheme == "netaware" else "stor"
    packets, minimum, maximum = {
        "fixed5": (1, 5, 5),
        "fixed_rtt": (1, 7, 7),
        "bdp_triggered": (86, 5, 14),
    }[cadence]
    flags = [
        f"-{prefix}_feedback_pkts", str(packets),
        f"-{prefix}_feedback_min_us", str(minimum),
        f"-{prefix}_feedback_max_us", str(maximum),
    ]
    if prefix == "stor":
        flags.extend([
            "-stor_trim_feedback_min_us",
            str(maximum if cadence == "bdp_triggered" else minimum),
        ])
    return flags


def _provenance_digest(values):
    payload = _canonical_json(sorted(set(values))).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _semantic_result_row(row):
    excluded = {
        "stdout", "fingerprint", "_source", "_source_row", "case_dir",
    }
    result = {}
    for key, value in row.items():
        if (key in excluded or key.endswith("_mtime")
                or key.endswith("_path")):
            continue
        if key == "command" and isinstance(value, list):
            result[key] = _canonical_argv(value)
        else:
            result[key] = value
    return result


def _result_set_digest(rows):
    normalized = [_semantic_result_row(row) for row in rows]
    normalized.sort(key=_canonical_json)
    return hashlib.sha256(
        _canonical_json(normalized).encode("utf-8")
    ).hexdigest()


def _selection_document(selected, rows):
    simulator_hashes = {row["simulator_sha256"] for row in rows}
    parser_versions = {row["parser_version"] for row in rows}
    if len(simulator_hashes) != 1 or len(parser_versions) != 1:
        raise RuntimeError("selection provenance is not unique")
    document = {
        "schema_version": 1,
        "selected_cadences": dict(selected),
        "cadence_flags": {
            scheme: _cadence_flags(scheme, selected[scheme])
            for scheme in SUBJECTS
        },
        "provenance": {
            "simulator_sha256": next(iter(simulator_hashes)),
            "parser_version": next(iter(parser_versions)),
            "case_config_sha256": _provenance_digest(
                f"{row['case_config_signature']}:{row['case_comparability_signature']}"
                for row in rows
            ),
            "result_set_sha256": _result_set_digest(rows),
        },
    }
    document["selection_sha256"] = hashlib.sha256(
        _canonical_json(document).encode("utf-8")
    ).hexdigest()
    return document


def _read_csv_rows(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _derived_seed_rows(rows):
    fields = (
        "stage", "nodes", "scenario", "scheme", "cadence", "seed",
        "grain", "p50_fct_us", "p95_fct_us", "p99_fct_us",
        "p999_fct_us", "max_fct_us", "all_to_all_cct_us",
        "feedback_count", "feedback_bytes", "nacks", "retx_packets",
        "recovery_packets", "queue_cv", "simulation_duration_us",
        "simulation_duration_source", "feedback_bandwidth_mbps",
    )
    derived = []
    for row in rows:
        item = {
            field: row[field] for field in fields
            if field not in {"grain", "recovery_packets"}
        }
        item["grain"] = "validated seed/cell"
        item["recovery_packets"] = row["nacks"] + row["retx_packets"]
        derived.append(item)
    derived.sort(key=lambda item: (
        item["nodes"], item["scenario"], item["scheme"],
        item["cadence"], item["seed"],
    ))
    return derived, fields


def _derived_normalized_rows(summary):
    records = [summary[key] for key in sorted(summary)]
    lookup = {
        (row["nodes"], row["scenario"], row["scheme"], row["cadence"]): row
        for row in records
    }
    derived = []
    for row in records:
        if row["scheme"] not in SUBJECTS or row["cadence"] not in CADENCES:
            continue
        reference = lookup.get((
            row["nodes"], row["scenario"], row["scheme"], "fixed5",
        ))
        if reference is None:
            continue
        is_alltoall = float(row["all_to_all_cct_us_median"]) > 0
        metric = "all_to_all_cct_us" if is_alltoall else "p99_fct_us"
        metric_label = "All-to-All CCT" if is_alltoall else "p99 FCT"
        value = float(row[f"{metric}_median"])
        denominator = float(reference[f"{metric}_median"])
        if value <= 0 or denominator <= 0:
            continue
        derived.append({
            "nodes": row["nodes"],
            "scenario": row["scenario"],
            "scheme": row["scheme"],
            "cadence": row["cadence"],
            "metric": metric,
            "metric_key": f"{row['scenario']}|{metric}",
            "metric_label": metric_label,
            "workload_family": "alltoall" if is_alltoall else "ordinary",
            "value": value,
            "value_unit": "us",
            "denominator_value": denominator,
            "denominator": (
                f"fixed5 same scheme/nodes/scenario median {metric}"
            ),
            "normalized_value": value / denominator,
            "seed_count": row["seed_count"],
        })
    fields = (
        "nodes", "scenario", "scheme", "cadence", "metric", "metric_key",
        "metric_label", "workload_family", "value", "value_unit",
        "denominator_value", "denominator", "normalized_value", "seed_count",
    )
    return derived, fields


def _complete_cadence_metric_keys(rows):
    expected = {
        (scheme, cadence)
        for scheme in SUBJECTS for cadence in CADENCES
    }
    observed = defaultdict(set)
    for row in rows:
        observed[row["metric_key"]].add((row["scheme"], row["cadence"]))
    return {
        metric_key for metric_key, identities in observed.items()
        if expected.issubset(identities)
    }


def _derived_ecdf_rows(seed_rows):
    groups = defaultdict(list)
    for row in seed_rows:
        if row["scheme"] not in SUBJECTS or row["cadence"] not in CADENCES:
            continue
        if (row.get("stage")
                and row["stage"] not in CADENCE_SELECTION_STAGES):
            continue
        if float(row["all_to_all_cct_us"]) > 0:
            continue
        for metric in ("p99_fct_us", "queue_cv"):
            groups[(row["scheme"], row["cadence"], metric)].append(
                float(row[metric])
            )
    derived = []
    for (scheme, cadence, metric), values in sorted(groups.items()):
        ordered = sorted(values)
        for rank, value in enumerate(ordered, 1):
            derived.append({
                "scheme": scheme,
                "cadence": cadence,
                "metric": metric,
                "grain": "validated seed/cell",
                "value": value,
                "unit": "us" if metric == "p99_fct_us" else "ratio",
                "rank": rank,
                "sample_count": len(ordered),
                "ecdf": rank / len(ordered),
            })
    fields = (
        "scheme", "cadence", "metric", "grain", "value", "unit",
        "rank", "sample_count", "ecdf",
    )
    return derived, fields


def _derived_pareto_rows(normalized_rows, seed_rows):
    if normalized_rows and all("metric_key" in row for row in normalized_rows):
        complete_metric_keys = _complete_cadence_metric_keys(normalized_rows)
        normalized_rows = [
            row for row in normalized_rows
            if row["metric_key"] in complete_metric_keys
        ]
    seed_lookup = defaultdict(list)
    for row in seed_rows:
        seed_lookup[(
            int(row["nodes"]), row["scenario"], row["scheme"], row["cadence"],
        )].append(row)
    groups = defaultdict(list)
    for row in normalized_rows:
        metric_family = (
            "All-to-All CCT"
            if row["metric"] == "all_to_all_cct_us" else "p99 FCT"
        )
        groups[(row["scheme"], row["cadence"], metric_family)].append(row)
    derived = []
    for (scheme, cadence, metric_family), rows in sorted(groups.items()):
        feedback_bytes = []
        bandwidths = []
        durations = []
        duration_sources = set()
        ratios = []
        for row in rows:
            cell_seeds = seed_lookup[(
                int(row["nodes"]), row["scenario"], scheme, cadence,
            )]
            if not cell_seeds:
                raise ValueError(
                    "feedback Pareto has no validated seed rows for normalized cell"
                )
            for seed in cell_seeds:
                feedback_bytes.append(float(seed["feedback_bytes"]))
                bandwidths.append(float(seed["feedback_bandwidth_mbps"]))
                durations.append(float(seed["simulation_duration_us"]))
                duration_sources.add(seed["simulation_duration_source"])
            ratios.append(float(row["normalized_value"]))
        derived.append({
            "scheme": scheme,
            "cadence": cadence,
            "metric_family": metric_family,
            "feedback_bytes_per_run": statistics.median(feedback_bytes),
            "feedback_bandwidth_mbps": statistics.median(bandwidths),
            "simulation_duration_us_median": statistics.median(durations),
            "duration_definition": (
                "per validated seed/cell: feedback_bytes*8/"
                "simulation_duration_us; duration source(s): "
                + ", ".join(sorted(duration_sources))
            ),
            "x_unit": "Mbps",
            "normalized_tail_median": statistics.median(ratios),
            "denominator": (
                "fixed5 same scheme/nodes/scenario median p99 or CCT"
            ),
            "cell_count": len(rows),
            "seed_cell_count": len(bandwidths),
        })
    fields = (
        "scheme", "cadence", "metric_family",
        "feedback_bytes_per_run", "feedback_bandwidth_mbps",
        "simulation_duration_us_median", "duration_definition", "x_unit",
        "normalized_tail_median", "denominator", "cell_count",
        "seed_cell_count",
    )
    return derived, fields


def _derived_runtime_rows(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["nodes"]].append(row)
    derived = []
    for nodes, group in sorted(groups.items()):
        topology = EXPECTED_TOPOLOGIES[nodes]
        simulator_hashes = sorted({row["simulator_sha256"] for row in group})
        parser_versions = sorted({row["parser_version"] for row in group})
        derived.append({
            **topology,
            "link_rate_gbps": 400,
            "mtu_bytes": 4096,
            "queue_type": "composite_ecn_lb",
            "host_queue_type": "prio",
            "cc": "dcqcn_variant",
            "rx_mode": "sp",
            "sack_bitmap_bits": 64,
            "queue_cv_sample_us": 100,
            "parser_version": ",".join(str(value) for value in parser_versions),
            "simulator_sha256": ",".join(simulator_hashes),
            "accepted_seed_cells": len(group),
        })
    fields = (
        "nodes", "hosts_per_leaf", "leaves", "spines", "paths", "tiers",
        "link_rate_gbps", "mtu_bytes", "queue_type", "host_queue_type",
        "cc", "rx_mode", "sack_bitmap_bits", "queue_cv_sample_us",
        "parser_version", "simulator_sha256", "accepted_seed_cells",
    )
    return derived, fields


def _write_derived_artifacts(output_dir, rows, summary):
    seed_rows, seed_fields = _derived_seed_rows(rows)
    normalized_rows, normalized_fields = _derived_normalized_rows(summary)
    ecdf_rows, ecdf_fields = _derived_ecdf_rows(seed_rows)
    pareto_rows, pareto_fields = _derived_pareto_rows(
        normalized_rows, seed_rows
    )
    runtime_rows, runtime_fields = _derived_runtime_rows(rows)
    artifacts = (
        ("seed_metrics.csv", seed_rows, seed_fields),
        ("normalized_metrics.csv", normalized_rows, normalized_fields),
        ("empirical_seed_ecdf.csv", ecdf_rows, ecdf_fields),
        ("feedback_pareto.csv", pareto_rows, pareto_fields),
        ("runtime_config_audit.csv", runtime_rows, runtime_fields),
    )
    for name, derived_rows, fields in artifacts:
        _atomic_csv(output_dir / name, derived_rows, fields)


def _plot_style():
    plt.rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": "#FAFAFA",
        "axes.edgecolor": "#333333",
        "axes.labelcolor": "#202020",
        "axes.titlecolor": "#161616",
        "text.color": "#202020",
        "xtick.color": "#333333",
        "ytick.color": "#333333",
        "grid.color": "#D9D9D9",
        "grid.linewidth": 0.7,
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "axes.titlesize": 12,
        "axes.labelsize": 9,
        "legend.fontsize": 7.5,
        "savefig.facecolor": "white",
    })


def _new_figure(figsize=(8.4, 4.8)):
    return plt.subplots(figsize=figsize)


def _set_log_if_needed(axis, values, which="y"):
    positive = [float(value) for value in values if float(value) > 0]
    exponents = [math.log10(value) for value in positive]
    if (exponents and max(exponents) - min(exponents) > 1
            and min(exponents) > -250 and max(exponents) < 250):
        getattr(axis, f"set_{which}scale")("log")


def _save_figure(fig, output_dir, stem):
    fig.tight_layout()
    for suffix in ("png", "pdf"):
        descriptor, temporary = tempfile.mkstemp(
            dir=str(output_dir), prefix=f".{stem}.", suffix=f".{suffix}.tmp"
        )
        os.close(descriptor)
        try:
            metadata = (
                {"Software": "feedback cadence report"}
                if suffix == "png" else
                {"Creator": "feedback cadence report",
                 "CreationDate": None, "ModDate": None}
            )
            fig.savefig(
                temporary, format=suffix, dpi=220, bbox_inches="tight",
                facecolor="white", metadata=metadata,
            )
            os.replace(temporary, output_dir / f"{stem}.{suffix}")
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    plt.close(fig)


def _scenario_label(value):
    labels = {
        "healthy_permutation_4mib": "Healthy permutation",
        "incast_8to1_4mib": "8:1 incast",
        "asymmetric_permutation_4mib": "Asymmetric",
        "mixed_fixed_background_target_4mib": "Mixed background",
        "periodic_hotspot_target_4mib": "Periodic hotspot",
        "full_global_p4_64mib_background_off": "All-to-All",
    }
    return labels.get(value, value.replace("_", " "))


def _scheme_cadence_legend():
    scheme = [
        Line2D([0], [0], color=SCHEME_COLORS[name], lw=2.5, label=name)
        for name in SUBJECTS
    ]
    cadence = [
        Line2D(
            [0], [0], color="#333333", lw=1.8,
            linestyle=CADENCE_LINESTYLES[name], marker=CADENCE_MARKERS[name],
            label=name,
        )
        for name in CADENCES
    ]
    return scheme + cadence


def _plot_normalized_tail_heatmap(output_dir):
    rows = _read_csv_rows(output_dir / "normalized_metrics.csv")
    metric_keys = sorted(_complete_cadence_metric_keys(rows))
    labels = {
        row["metric_key"]:
        f"{_scenario_label(row['scenario'])}\n({row['metric_label']})"
        for row in rows
    }
    identities = [(scheme, cadence) for scheme in SUBJECTS for cadence in CADENCES]
    groups = defaultdict(list)
    for row in rows:
        groups[(row["scheme"], row["cadence"], row["metric_key"])].append(
            float(row["normalized_value"])
        )
    lookup = {key: statistics.median(values) for key, values in groups.items()}
    matrix = [[lookup[(scheme, cadence, metric_key)] for metric_key in metric_keys]
              for scheme, cadence in identities]
    values = [value for line in matrix for value in line]
    spread = max(max(values) - 1, 1 - min(values), 0.01)
    fig, axis = _new_figure((9.4, 5.4))
    image = axis.imshow(
        matrix, aspect="auto", cmap="RdBu_r", vmin=1 - spread,
        vmax=1 + spread,
    )
    axis.set_xticks(range(len(metric_keys)))
    axis.set_xticklabels([labels[item] for item in metric_keys],
                         rotation=28, ha="right")
    axis.set_yticks(range(len(identities)))
    axis.set_yticklabels([f"{scheme} / {cadence}" for scheme, cadence in identities])
    for label, (scheme, unused_cadence) in zip(axis.get_yticklabels(), identities):
        label.set_color(SCHEME_COLORS[scheme])
    for row_index, line in enumerate(matrix):
        for column_index, value in enumerate(line):
            text_color = (
                "white" if abs(value - 1) > spread * 0.42 else "#111111"
            )
            axis.text(column_index, row_index, f"{value:.2f}", ha="center",
                      va="center", fontsize=7, color=text_color)
    axis.set_xlabel("Workload scenario and tail metric (category)")
    axis.set_ylabel("Scheme / cadence (category)")
    axis.set_title("Median normalized tail latency across validated topologies")
    colorbar = fig.colorbar(image, ax=axis, pad=0.02)
    colorbar.set_label(
        "Tail ratio (denominator: fixed5, same scheme/topology/scenario)"
    )
    _save_figure(fig, output_dir, "normalized_tail_heatmap")


def _plot_empirical_cdf(output_dir, metric, stem, title, x_label):
    rows = [
        row for row in _read_csv_rows(output_dir / "empirical_seed_ecdf.csv")
        if row["metric"] == metric
    ]
    groups = defaultdict(list)
    for row in rows:
        groups[(row["scheme"], row["cadence"])].append(row)
    fig, axis = _new_figure()
    values = []
    for (scheme, cadence), group in sorted(groups.items()):
        group.sort(key=lambda row: int(row["rank"]))
        x_values = [float(row["value"]) for row in group]
        y_values = [float(row["ecdf"]) for row in group]
        values.extend(x_values)
        axis.step(
            x_values, y_values, where="post", color=SCHEME_COLORS[scheme],
            linestyle=CADENCE_LINESTYLES[cadence], linewidth=1.6,
            label=f"{scheme} / {cadence}",
        )
    _set_log_if_needed(axis, values, "x")
    axis.set_xlabel(x_label)
    axis.set_ylabel("Empirical cumulative fraction (seed/cell fraction)")
    axis.set_ylim(0, 1.02)
    axis.set_title(title)
    axis.grid(True, alpha=0.8)
    axis.legend(ncol=3, frameon=False, title="Validated seed/cell series")
    _save_figure(fig, output_dir, stem)


def _plot_alltoall_cct(output_dir):
    rows = [
        row for row in _read_csv_rows(output_dir / "summary.csv")
        if row["scheme"] in SUBJECTS and row["cadence"] in CADENCES
        and float(row["all_to_all_cct_us_median"]) > 0
    ]
    by_identity = {
        (row["scheme"], row["cadence"]):
        statistics.median([
            float(item["all_to_all_cct_us_median"]) for item in rows
            if item["scheme"] == row["scheme"] and item["cadence"] == row["cadence"]
        ]) for row in rows
    }
    fig, axis = _new_figure()
    width = 0.24
    values = []
    for cadence_index, cadence in enumerate(CADENCES):
        positions = [index + (cadence_index - 1) * width for index in range(len(SUBJECTS))]
        heights = [by_identity[(scheme, cadence)] for scheme in SUBJECTS]
        values.extend(heights)
        axis.bar(
            positions, heights, width=width, color=[SCHEME_COLORS[item] for item in SUBJECTS],
            edgecolor="#222222", linewidth=0.7, hatch=CADENCE_HATCHES[cadence],
            label=cadence,
        )
    _set_log_if_needed(axis, values)
    axis.set_xticks(range(len(SUBJECTS)))
    axis.set_xticklabels(SUBJECTS)
    axis.set_xlabel("Load-balancing scheme (category)")
    axis.set_ylabel("All-to-All completion time (us)")
    axis.set_title("Median All-to-All CCT across validated topology cells")
    axis.grid(True, axis="y", alpha=0.8)
    axis.legend(
        frameon=False, title="Cadence hatch", loc="upper left",
        bbox_to_anchor=(1.01, 1.0), borderaxespad=0,
    )
    _save_figure(fig, output_dir, "alltoall_cct")


def _plot_topology_scaling(output_dir):
    normalized = _read_csv_rows(output_dir / "normalized_metrics.csv")
    complete_metric_keys = _complete_cadence_metric_keys(normalized)
    rows = [
        row for row in normalized
        if row["metric"] == "p99_fct_us"
        and (not complete_metric_keys
             or row["metric_key"] in complete_metric_keys)
        and row.get("workload_family") == "ordinary"
        and row["scenario"] != "full_global_p4_64mib_background_off"
    ]
    topology_nodes = sorted({int(row["nodes"]) for row in rows})
    fig, axis = _new_figure()
    if len(topology_nodes) < 2:
        axis.set_xticks(topology_nodes)
        axis.set_xlabel("Topology size (hosts)")
        axis.set_ylabel(
            "Median normalized p99 FCT (ratio; denominator: fixed5 same cell)"
        )
        axis.set_title("Topology scaling of normalized ordinary-workload tail")
        axis.text(
            0.5, 0.5,
            "Insufficient topology coverage\n"
            "Need at least 2 ordinary-workload topology sizes",
            transform=axis.transAxes, ha="center", va="center",
            fontsize=10, color="#555555",
        )
        axis.grid(True, alpha=0.8)
        _save_figure(fig, output_dir, "topology_scaling")
        return

    groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        groups[(row["scheme"], row["cadence"])][int(row["nodes"])].append(
            float(row["normalized_value"])
        )
    all_nodes = []
    all_values = []
    for (scheme, cadence), node_values in sorted(groups.items()):
        nodes = sorted(node_values)
        values = [statistics.median(node_values[node]) for node in nodes]
        all_nodes.extend(nodes)
        all_values.extend(values)
        axis.plot(
            nodes, values, color=SCHEME_COLORS[scheme],
            linestyle=CADENCE_LINESTYLES[cadence],
            marker=CADENCE_MARKERS[cadence], linewidth=1.5,
            label=f"{scheme} / {cadence}",
        )
    _set_log_if_needed(axis, all_nodes, "x")
    _set_log_if_needed(axis, all_values, "y")
    axis.set_xticks(sorted(set(all_nodes)))
    axis.set_xlabel("Topology size (hosts)")
    axis.set_ylabel(
        "Median normalized p99 FCT (ratio; denominator: fixed5 same cell)"
    )
    axis.set_title("Topology scaling of normalized ordinary-workload tail")
    axis.grid(True, alpha=0.8)
    axis.legend(
        ncol=1, frameon=False, loc="upper left",
        bbox_to_anchor=(1.01, 1.0), borderaxespad=0,
    )
    _save_figure(fig, output_dir, "topology_scaling")


def _recovery_cost_statistics(rows):
    groups = defaultdict(list)
    for row in rows:
        if (row["scheme"] not in SUBJECTS or row["cadence"] not in CADENCES
                or float(row["all_to_all_cct_us"]) > 0):
            continue
        if (row.get("stage")
                and row["stage"] not in CADENCE_SELECTION_STAGES):
            continue
        recovery = (
            float(row["recovery_packets"])
            if "recovery_packets" in row else
            float(row["nacks"]) + float(row["retx_packets"])
        )
        groups[(row["scheme"], row["cadence"])].append(
            recovery
        )
    return {
        key: {
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
            "run_count": len(values),
        }
        for key, values in groups.items()
    }


def _plot_recovery_cost(output_dir):
    rows = _read_csv_rows(output_dir / "seed_metrics.csv")
    groups = _recovery_cost_statistics(rows)
    fig, axis = _new_figure()
    width = 0.24
    values = []
    for cadence_index, cadence in enumerate(CADENCES):
        positions = [index + (cadence_index - 1) * width for index in range(len(SUBJECTS))]
        stats = [groups[(scheme, cadence)] for scheme in SUBJECTS]
        heights = [item["median"] for item in stats]
        errors = [
            [item["median"] - item["min"] for item in stats],
            [item["max"] - item["median"] for item in stats],
        ]
        values.extend(heights)
        axis.bar(
            positions, heights, width=width, color=[SCHEME_COLORS[item] for item in SUBJECTS],
            edgecolor="#222222", linewidth=0.7, hatch=CADENCE_HATCHES[cadence],
            label=cadence, yerr=errors, capsize=3,
            error_kw={"elinewidth": 0.8, "ecolor": "#333333"},
        )
        if not any(heights):
            axis.scatter(
                positions, heights, color=[SCHEME_COLORS[item] for item in SUBJECTS],
                marker=CADENCE_MARKERS[cadence], s=25, zorder=3,
            )
    _set_log_if_needed(axis, values)
    axis.set_xticks(range(len(SUBJECTS)))
    axis.set_xticklabels(SUBJECTS)
    axis.set_xlabel("Load-balancing scheme (category)")
    axis.set_ylabel("Per-run NACK + retransmission cost (median, min/max packets)")
    axis.set_title("Recovery cost across ordinary seed/cells")
    if not any(values):
        axis.text(
            0.5, 0.08, "All validated ordinary cells report zero packets/run",
            transform=axis.transAxes, ha="center", va="bottom", fontsize=8,
        )
    axis.grid(True, axis="y", alpha=0.8)
    axis.legend(frameon=False, title="Cadence hatch")
    _save_figure(fig, output_dir, "recovery_cost")


def _plot_feedback_pareto(output_dir):
    rows = _read_csv_rows(output_dir / "feedback_pareto.csv")
    families = ("p99 FCT", "All-to-All CCT")
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.8), sharey=True)
    for axis, family in zip(axes, families):
        family_rows = [row for row in rows if row["metric_family"] == family]
        x_values = [float(row["feedback_bandwidth_mbps"]) for row in family_rows]
        y_values = [float(row["normalized_tail_median"]) for row in family_rows]
        x_span = max(x_values) - min(x_values) if x_values else 0.0
        y_span = max(y_values) - min(y_values) if y_values else 0.0
        x_delta = max(x_span, max(x_values, default=1.0) * 0.1, 1e-9) * 0.08
        y_delta = max(y_span, max(y_values, default=1.0) * 0.1, 1e-9) * 0.08
        coincident = defaultdict(list)
        for row in family_rows:
            exact = (
                float(row["feedback_bandwidth_mbps"]),
                float(row["normalized_tail_median"]),
            )
            coincident[exact].append(row)

        for (x_value, y_value), group in sorted(coincident.items()):
            ordered = sorted(
                group,
                key=lambda row: (
                    SUBJECTS.index(row["scheme"]),
                    CADENCES.index(row["cadence"]),
                ),
            )
            offsets = [(0, 0)]
            if len(ordered) > 1:
                pattern = (
                    (-1, 0), (1, 0), (0, -1), (0, 1),
                    (-1, -1), (1, 1), (-1, 1), (1, -1), (0, 2),
                )
                offsets = pattern[:len(ordered)]
            for row, (x_step, y_step) in zip(ordered, offsets):
                scheme = row["scheme"]
                cadence = row["cadence"]
                display_x = x_value + x_step * x_delta
                display_y = y_value + y_step * y_delta
                if (display_x, display_y) != (x_value, y_value):
                    axis.plot(
                        [x_value, display_x], [y_value, display_y],
                        color=SCHEME_COLORS[scheme], linewidth=0.6,
                        alpha=0.7, zorder=2,
                    )
                axis.scatter(
                    [display_x], [display_y], s=48,
                    marker=CADENCE_MARKERS[cadence],
                    facecolor=SCHEME_COLORS[scheme],
                    edgecolor="#202020", linewidth=0.7, zorder=3,
                )
                category_rank = (
                    SUBJECTS.index(scheme) * len(CADENCES)
                    + CADENCES.index(cadence)
                )
                high_cluster = display_y >= 0.97
                label_y = -8 - category_rank * 8 if high_cluster else 5
                near_right = (
                    x_span > 0
                    and display_x >= min(x_values) + 0.6 * x_span
                )
                axis.annotate(
                    f"{scheme} / {cadence}", (display_x, display_y),
                    xytext=(-5 if near_right else 5, label_y),
                    textcoords="offset points", fontsize=6.2,
                    ha="right" if near_right else "left",
                    va="top" if high_cluster else "bottom",
                    color=SCHEME_COLORS[scheme], zorder=4,
                    arrowprops={
                        "arrowstyle": "-", "color": SCHEME_COLORS[scheme],
                        "linewidth": 0.4, "alpha": 0.65,
                    },
                )

        _set_log_if_needed(axis, x_values, "x")
        _set_log_if_needed(axis, y_values, "y")
        axis.axhline(1.0, color="#555555", linewidth=0.8, linestyle="--")
        axis.set_xlabel("Estimated feedback bandwidth (Mbps)")
        axis.set_title(family)
        axis.grid(True, alpha=0.8)
    axes[0].set_ylabel(
        "Normalized tail outcome (ratio; denominator: fixed5 same cell)"
    )
    fig.suptitle("Estimated feedback bandwidth versus normalized tail outcome")
    fig.text(
        0.5, 0.01,
        "Small display offsets and connectors separate coincident categories; "
        "feedback_pareto.csv retains exact coordinates.",
        ha="center", va="bottom", fontsize=7, color="#555555",
    )
    _save_figure(fig, output_dir, "feedback_pareto")


def _plot_seed_ranges(output_dir):
    rows = [
        row for row in _read_csv_rows(output_dir / "summary.csv")
        if row["scheme"] in SUBJECTS and row["cadence"] in CADENCES
        and row["scenario"] == "healthy_permutation_4mib"
    ]
    if not rows:
        ordinary = [
            row for row in _read_csv_rows(output_dir / "summary.csv")
            if row["scheme"] in SUBJECTS and row["cadence"] in CADENCES
            and float(row["all_to_all_cct_us_median"]) == 0
        ]
        scenario = min(row["scenario"] for row in ordinary)
        rows = [row for row in ordinary if row["scenario"] == scenario]
    largest_topology = max(int(row["nodes"]) for row in rows)
    rows = [row for row in rows if int(row["nodes"]) == largest_topology]
    rows.sort(key=lambda row: (SUBJECTS.index(row["scheme"]), CADENCES.index(row["cadence"])))
    fig, axis = _new_figure((9.2, 4.8))
    values = []
    for index, row in enumerate(rows):
        median = float(row["p99_fct_us_median"])
        minimum = float(row["p99_fct_us_min"])
        maximum = float(row["p99_fct_us_max"])
        values.extend((minimum, median, maximum))
        cadence = row["cadence"]
        axis.errorbar(
            [index], [median], yerr=[[median - minimum], [maximum - median]],
            fmt=CADENCE_MARKERS[cadence], color=SCHEME_COLORS[row["scheme"]],
            linestyle=CADENCE_LINESTYLES[cadence], capsize=4, linewidth=1.2,
        )
    _set_log_if_needed(axis, values)
    axis.set_xticks(range(len(rows)))
    axis.set_xticklabels(
        [f"{row['scheme']}\n{row['cadence']}" for row in rows], rotation=22,
        ha="right",
    )
    axis.set_xlabel("Scheme / cadence (category)")
    axis.set_ylabel("Reported p99 FCT (us)")
    axis.set_title(
        f"Seed ranges: {_scenario_label(rows[0]['scenario'])}, "
        f"{largest_topology} hosts (median with min/max)"
    )
    axis.grid(True, axis="y", alpha=0.8)
    axis.legend(handles=_scheme_cadence_legend(), ncol=3, frameon=False)
    _save_figure(fig, output_dir, "seed_ranges")


def _render_figures(output_dir):
    """Render only from summary.csv and named derived CSV artifacts."""
    output_dir = Path(output_dir)
    _plot_style()
    _plot_normalized_tail_heatmap(output_dir)
    _plot_empirical_cdf(
        output_dir, "p99_fct_us", "key_fct_cdf",
        "Empirical distribution of reported cell-level p99 FCT",
        "Reported p99 FCT per validated seed/cell (us)",
    )
    _plot_alltoall_cct(output_dir)
    _plot_topology_scaling(output_dir)
    _plot_empirical_cdf(
        output_dir, "queue_cv", "queue_cv_cdf",
        "Empirical distribution of cell-level spine queue CV",
        "Spine queue coefficient of variation per seed/cell (ratio)",
    )
    _plot_recovery_cost(output_dir)
    _plot_feedback_pareto(output_dir)
    _plot_seed_ranges(output_dir)


def _format_number(value, digits=3):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "Not available"
    if not math.isfinite(number):
        return "Not available"
    return f"{number:.{digits}f}"


def _markdown_table(headers, rows):
    if not rows:
        return "Not available."
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for unused in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |"
                 for row in rows)
    return "\n".join(lines)


def _host_list(nodes):
    values = [str(int(node)) for node in sorted(set(nodes))]
    if len(values) == 1:
        return f"{values[0]} hosts"
    if len(values) == 2:
        return f"{values[0]} and {values[1]} hosts"
    return f"{', '.join(values[:-1])}, and {values[-1]} hosts"


def _topology_followups(nodes):
    actual = sorted(set(int(node) for node in nodes))
    missing = sorted(set(EXPECTED_TOPOLOGIES) - set(actual))
    next_steps = [
        "1. Preserve the selected cadence flags explicitly in subsequent runs.",
    ]
    if missing:
        missing_label = " and ".join(f"{node}-host" for node in missing)
        next_steps.append(
            f"2. Add {missing_label} cells only after resource and provenance checks pass."
        )
    else:
        next_steps.append(
            f"2. Preserve complete topology coverage at {_host_list(actual)}."
        )
    next_steps.append(
        "3. Re-render this report after each complete topology matrix; do not mix partial grids."
    )

    if len(actual) >= 2:
        stability = f"- Do cadence rankings remain stable across {_host_list(actual)}?"
    else:
        stability = f"- Do cadence rankings remain stable beyond {_host_list(actual)}?"
    questions = [
        stability,
        "- Which workload cells dominate any feedback-overhead tradeoff?",
        "- Can future exports include raw-flow samples for a true flow-level CDF?",
    ]
    return next_steps, questions


def _build_report(output_dir):
    output_dir = Path(output_dir)
    summary = _read_csv_rows(output_dir / "summary.csv")
    seeds = _read_csv_rows(output_dir / "seed_metrics.csv")
    normalized = _read_csv_rows(output_dir / "normalized_metrics.csv")
    runtime = _read_csv_rows(output_dir / "runtime_config_audit.csv")
    decisions = _read_csv_rows(output_dir / "cadence_decisions.csv")
    rejections = _read_csv_rows(output_dir / "data_quality_rejections.csv")
    selection = json.loads(
        (output_dir / "selected-cadences.json").read_text(encoding="utf-8")
    )
    selected = selection["selected_cadences"]
    nodes = sorted({int(row["nodes"]) for row in summary})
    ordinary = [row for row in summary if float(row["all_to_all_cct_us_median"]) == 0]
    alltoall = [row for row in summary if float(row["all_to_all_cct_us_median"]) > 0]

    final_decisions = [row for row in decisions if row["rule"] == "final_selection"]
    decision_table = _markdown_table(
        ("Scheme", "Selected cadence", "Opportunity score", "Reference"),
        [
            (row["subject"], row["cadence"], _format_number(row["value"]),
             row["baseline"] or "Not available")
            for row in final_decisions
        ],
    )
    selected_ordinary = [
        row for row in ordinary
        if (row["scheme"] in selected
            and row["cadence"] == selected[row["scheme"]])
        or row["scheme"] not in SUBJECTS
    ]
    ordinary_table = _markdown_table(
        ("Hosts", "Workload", "Scheme", "Cadence", "p99 FCT (us)",
         "Seed range min-max (us)"),
        [
            (row["nodes"], _scenario_label(row["scenario"]), row["scheme"],
             row["cadence"], _format_number(row["p99_fct_us_median"]),
             f"{_format_number(row['p99_fct_us_min'])}-{_format_number(row['p99_fct_us_max'])}")
            for row in selected_ordinary
        ],
    )
    selected_alltoall = [
        row for row in alltoall
        if (row["scheme"] in selected
            and row["cadence"] == selected[row["scheme"]])
        or row["scheme"] not in SUBJECTS
    ]
    alltoall_table = _markdown_table(
        ("Hosts", "Scheme", "Cadence", "CCT median (us)",
         "Seed range min-max (us)"),
        [
            (row["nodes"], row["scheme"], row["cadence"],
             _format_number(row["all_to_all_cct_us_median"]),
             f"{_format_number(row['all_to_all_cct_us_min'])}-{_format_number(row['all_to_all_cct_us_max'])}")
            for row in selected_alltoall
        ],
    )
    overhead_groups = defaultdict(list)
    for row in seeds:
        if (row["scheme"] in SUBJECTS and row["cadence"] in CADENCES
                and float(row["all_to_all_cct_us"]) == 0
                and row.get("stage") in CADENCE_SELECTION_STAGES):
            overhead_groups[(row["scheme"], row["cadence"])].append(row)
    overhead_table = _markdown_table(
        ("Scheme", "Cadence", "Median feedback bytes (bytes/run)",
         "Estimated bandwidth (Mbps)", "Duration median (us)",
         "Seed/cell count"),
        [
            (scheme, cadence,
             _format_number(statistics.median(
                 float(row["feedback_bytes"]) for row in rows
             ), 1),
             _format_number(statistics.median(
                 float(row["feedback_bandwidth_mbps"]) for row in rows
             ), 4),
             _format_number(statistics.median(
                 float(row["simulation_duration_us"]) for row in rows
             ), 1),
             len(rows))
            for (scheme, cadence), rows in sorted(overhead_groups.items())
        ],
    )
    runtime_table = _markdown_table(
        ("Hosts", "Leaf/spine/path", "Link (Gbps)", "MTU (bytes)",
         "Queue / CC / RX", "Parser", "Accepted seed/cells"),
        [
            (row["nodes"],
             f"{row['leaves']}/{row['spines']}/{row['paths']}",
             row["link_rate_gbps"], row["mtu_bytes"],
             f"{row['queue_type']} / {row['cc']} / {row['rx_mode']}",
             row["parser_version"], row["accepted_seed_cells"])
            for row in runtime
        ],
    )
    provenance_text = "; ".join(
        f"{row['nodes']} hosts simulator SHA-256 `{row['simulator_sha256']}`"
        for row in runtime
    ) or "Simulator provenance not available."
    rtt_bdp_table = _markdown_table(
        ("Item", "Configured value", "Unit", "Evidence status"),
        [
            ("fixed_rtt cadence", "7", "microseconds (us)",
             "validated command configuration; measured RTT not available"),
            ("BDP trigger", str(RECOVERY_BDP_PACKETS), "packets",
             "validated command configuration"),
            ("BDP payload approximation", str(RECOVERY_BDP_PACKETS * 4096),
             "bytes", "86 packets x 4096-byte MTU"),
            ("BDP serialization at 400 Gbps",
             _format_number(RECOVERY_BDP_PACKETS * 4096 * 8 / 400000),
             "microseconds (us)", "configuration-derived, not measured RTT"),
        ],
    )
    quality_table = _markdown_table(
        ("Status", "Count", "Detail"),
        [("Rejected", len(rejections),
          "No rejected rows" if not rejections else
          "; ".join(sorted(Counter(row["reason"] for row in rejections))[:5]))],
    )
    ratio_rows = [float(row["normalized_value"]) for row in normalized]
    ratio_summary = (
        f"Observed normalized tail ratios span {_format_number(min(ratio_rows))} "
        f"to {_format_number(max(ratio_rows))}."
        if ratio_rows else "Normalized tail evidence is not available."
    )
    figure_links = "\n".join(
        f"- **{stem.replace('_', ' ').title()}**: "
        f"[PNG]({stem}.png) | [PDF]({stem}.pdf)"
        for stem in FIGURE_STEMS
    )
    selected_text = ", ".join(
        f"{scheme}={selected[scheme]}" for scheme in SUBJECTS
    )
    next_steps, questions = _topology_followups(nodes)
    topology_caveat = (
        "The 2K resource caveat remains material: 2048-node evidence is not "
        "available in this report, and memory/runtime feasibility must be reviewed "
        "before treating smaller-topology trends as scaling evidence."
        if 2048 not in nodes else
        "The 2K resource caveat was exercised by this report: 2048-node evidence "
        "appears in the runtime audit, but memory/runtime feasibility remains part "
        "of its interpretation."
    )
    lines = [
        "# Feedback Cadence Evaluation",
        "",
        "## Technical Summary",
        "",
        f"Validated evidence contains {len(seeds)} seed/cells across "
        f"{len(nodes)} topology size(s): {', '.join(str(node) for node in nodes)} hosts. "
        f"The explicit recommendations are {selected_text}. {ratio_summary}",
        "",
        "Tail latency and completion time are reported in microseconds (us); "
        "feedback overhead is reported as estimated feedback bandwidth (Mbps), "
        "using simulated run duration as the denominator; feedback bytes (bytes) "
        "per run are retained as an audit value. Each run uses total endpoint-"
        "received feedback-bearing ACK plus NACK messages times the scheme payload "
        "bytes; ACK, NACK, and total counters remain separately auditable.",
        "",
        "## Key Findings and Visual Evidence",
        "",
        "The normalized figures use a fixed5 same-scheme, same-topology, "
        "same-scenario denominator. Lower ratios are better. CDF-named figures "
        "show an empirical seed/cell distribution of reported aggregate metrics, "
        "not packet-level or raw-flow samples.",
        "The Pareto x-axis is the median of per-seed/cell estimates: feedback "
        "bytes x 8 divided by simulated run duration in microseconds, yielding Mbps.",
        "Pareto facets separate p99 FCT from All-to-All CCT. Small plotted offsets "
        "and connectors only separate coincident categories; feedback_pareto.csv "
        "retains the exact coordinates.",
        "",
        "### Cadence Decisions",
        "",
        decision_table,
        "",
        "### Ordinary Workloads",
        "",
        ordinary_table,
        "",
        "### All-to-All CCT",
        "",
        alltoall_table,
        "",
        "### Feedback Overhead",
        "",
        overhead_table,
        "",
        figure_links,
        "",
        "## Scope, Data, and Metric Definitions",
        "",
        "A cell is one validated topology, scenario, scheme, cadence, and seed. "
        "The p99 value is the simulator-reported aggregate for that cell. "
        "All-to-All CCT is the reported workload completion time. Queue CV is "
        "the sampled spine-queue coefficient of variation. Normalized values "
        "state their denominator in the derived CSV and figure axis.",
        "",
        "### Runtime Configuration Audit",
        "",
        runtime_table,
        "",
        provenance_text,
        "",
        "### RTT and BDP",
        "",
        rtt_bdp_table,
        "",
        "### Data Quality and Rejections",
        "",
        quality_table,
        "",
        "## Methodology",
        "",
        "Rows are validated against manifests, run intent, topology, command "
        "configuration, flow completion, parser version, and provenance before "
        "aggregation. Validated rows generate summary.csv and named derived CSV "
        "artifacts. Plotting then reads only summary.csv and those derived CSV "
        "files; it does not read raw logs. Seed variation is shown as median and "
        "min/max, not as a confidence interval. Feedback bandwidth is calculated "
        "per validated seed/cell before aggregation.",
        "",
        "## Limitations and Robustness",
        "",
        "The key_fct_cdf and queue_cv_cdf figures are empirical distributions at "
        "validated seed/cell grain. They must not be interpreted as packet or "
        "flow CDFs. Aggregate quantiles cannot reconstruct raw-flow samples. "
        "A missing, non-positive, non-finite, or inconsistent simulated run "
        "duration blocks the affected row; bandwidth is not imputed. "
        f"Measured RTT is not available in these summary artifacts. {topology_caveat}",
        "",
        "## Recommended Next Steps",
        "",
        *next_steps,
        "",
        "## Further Questions",
        "",
        *questions,
        "",
    ]
    _atomic_text(output_dir / "report.md", "\n".join(lines))


def _build_all(output_dir, input_dir=None, nodes=None):
    """Build all Task 6 CSV/JSON artifacts atomically."""
    output_dir = Path(output_dir)
    source_dir = output_dir if input_dir is None else Path(input_dir)
    loaded = load_rows(source_dir)
    node_filter = None if nodes is None else {int(node) for node in nodes}
    accepted = LoadedRows(
        [row for row in loaded if node_filter is None or row["nodes"] in node_filter],
        [
            item for item in loaded.rejections
            if node_filter is None
            or not str(item.get("nodes", "")).isdigit()
            or int(item["nodes"]) in node_filter
        ],
        expected_cases=[
            item for item in loaded.expected_cases
            if node_filter is None or item["nodes"] in node_filter
        ],
        run_intents=loaded.run_intents,
    )
    if not accepted:
        output_dir.mkdir(parents=True, exist_ok=True)
        _atomic_csv(
            output_dir / "data_quality_rejections.csv",
            accepted.rejections, REJECTION_FIELDS,
        )
        raise ValueError("no accepted evaluation rows after validation/filtering")
    summary = aggregate(accepted)
    selected, decisions, blockers = _recommend(
        accepted, accepted.rejections, accepted.expected_cases
    )
    accepted.rejections.sort(key=lambda item: (
        str(item["source"]), int(item["row"]), item["case"], item["reason"]
    ))
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_csv(
        output_dir / "summary.csv",
        [summary[key] for key in sorted(summary)], _summary_fields(),
    )
    _write_derived_artifacts(output_dir, accepted, summary)
    _atomic_csv(
        output_dir / "data_quality_rejections.csv",
        accepted.rejections, REJECTION_FIELDS,
    )
    _atomic_csv(
        output_dir / "cadence_decisions.csv", decisions, DECISION_FIELDS,
    )
    selected_path = output_dir / "selected-cadences.json"
    if set(selected) != set(SUBJECTS):
        selected_path.unlink(missing_ok=True)
        evidence = "; ".join(str(item) for item in blockers if item)
        raise RuntimeError(
            "recommendation blocked: " + (evidence or "incomplete subject selection")
        )
    selection_document = _selection_document(selected, accepted)
    _atomic_text(
        selected_path,
        json.dumps(selection_document, sort_keys=True, indent=2) + "\n",
    )
    _render_figures(output_dir)
    _build_report(output_dir)
    return {
        "rows": accepted,
        "summary": summary,
        "decisions": decisions,
        "selected": selected,
        "selection_document": selection_document,
    }


def build_all(output_dir, input_dir=None, nodes=None):
    """Replace the complete report set, clearing every artifact on failure."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_ARTIFACTS:
        (output_dir / name).unlink(missing_ok=True)
    try:
        return _build_all(output_dir, input_dir=input_dir, nodes=nodes)
    except BaseException:
        for name in SUCCESS_ARTIFACTS:
            (output_dir / name).unlink(missing_ok=True)
        raise


def _parse_nodes(value):
    try:
        nodes = [int(part) for part in value.split(",") if part]
    except ValueError:
        raise argparse.ArgumentTypeError("--nodes must be comma-separated integers")
    if not nodes or any(node not in EXPECTED_TOPOLOGIES for node in nodes):
        raise argparse.ArgumentTypeError("--nodes contains an unsupported topology")
    return nodes


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--nodes", type=_parse_nodes)
    parser.add_argument("--fixture-test", action="store_true")
    return parser.parse_args(argv)


def _run_fixture_test():
    import runpy

    fixture_helpers = runpy.run_path(
        str(ROOT / "sim/tests/test_feedback_cadence_report.py")
    )
    with tempfile.TemporaryDirectory(prefix="feedback-report-fixture-") as directory:
        root = Path(directory)
        fixture = fixture_helpers["write_full_fixture"](root / "input")
        output = root / "report"
        if output.exists():
            raise RuntimeError("fixture-test output directory must be fresh")
        build_all(output, input_dir=fixture.root)
        figure_names = {
            f"{stem}.{suffix}" for stem in FIGURE_STEMS
            for suffix in ("png", "pdf")
        }
        actual_figure_names = {
            path.name for path in output.iterdir()
            if path.is_file() and path.suffix in {".png", ".pdf"}
        }
        if actual_figure_names != figure_names:
            raise RuntimeError(
                "fixture-test figure inventory mismatch: expected="
                f"{sorted(figure_names)} actual={sorted(actual_figure_names)}"
            )
        expected = [output / name for name in sorted(figure_names)]
        expected.extend(output / name for name in DERIVED_ARTIFACTS)
        expected.append(output / "report.md")
        missing = [path.name for path in expected
                   if not path.is_file() or path.stat().st_size == 0]
        if missing:
            raise RuntimeError(
                "fixture-test missing or empty artifacts: " + ", ".join(missing)
            )
    print("fixture-test: PASS")


def main(argv=None):
    args = parse_args(argv)
    if args.fixture_test:
        _run_fixture_test()
        return None
    result = build_all(args.output, input_dir=args.input, nodes=args.nodes)
    print(args.output / "summary.csv")
    print(args.output / "cadence_decisions.csv")
    print(args.output / "selected-cadences.json")
    return result


if __name__ == "__main__":
    main()
