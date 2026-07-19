#!/usr/bin/env python3
"""Diagnose SGLB interaction with exact bounded transport semantics."""

import argparse
import concurrent.futures
import csv
import hashlib
from pathlib import Path
import shlex
import subprocess
import sys
import time


sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_dcqcn_narrow_trim_lb_compare as legacy_runner  # noqa: E402
import run_exact_bounded_representative_schemes as exact_runner  # noqa: E402
import run_mrc_exact_bounded_recovery as bounded_runner  # noqa: E402
import experiment_metrics as metrics  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = (
    ROOT / "experiments/n-mrc/output/"
    "mrc_exact_bounded_sglb_interaction_diag_128_seed13"
)
SCENARIOS = (
    "full_global_p16_256mib_background_off",
    "full_global_p16_256mib_background_on",
    "full_global_p4_64mib_background_off",
)
TRANSPORTS = {
    "natural_cumulative": ("dcqcn_variant", "legacy", "cumulative"),
    "natural_exact": ("dcqcn_variant", "legacy", "exact"),
    "narrow_cumulative": (
        "dcqcn_variant_nodup_old", "legacy", "cumulative"),
    "narrow_exact": ("dcqcn_variant_nodup_old", "legacy", "exact"),
    "exact_bounded": ("dcqcn_variant", "mrc_exact_bounded", "exact"),
}
SCORE_PROFILES = {
    "default_b20_k3": (20.0, 3),
    "fine_b10_k3": (10.0, 3),
    "fine_b5_k3": (5.0, 3),
    "strict_b20_k1": (20.0, 1),
    "strict_b5_k1": (5.0, 1),
}
LEGACY_INTERACTION_PROFILES = {
    "legacy_fine_b10_k3": SCORE_PROFILES["fine_b10_k3"],
    "legacy_strict_b20_k1": SCORE_PROFILES["strict_b20_k1"],
}
SGLB_FIELDS = (
    "route_calls", "avg_available_choices", "avg_candidate_choices",
    "avg_best_quality_choices", "avg_distinct_qualities",
    "all_same_quality_calls", "all_zero_quality_calls",
    "selected_nonbest_quality", "avg_score_spread",
    "remote_snapshot_used", "remote_snapshot_missing",
)
QUEUE_CV_FIELDS = ("spine_queue_cv", "spine_queue_avg", "spine_queue_count")


def set_option(argv, flag, value):
    if flag in argv:
        argv[argv.index(flag) + 1] = str(value)
    else:
        argv.extend((flag, str(value)))


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_spec(args, scenario, label, transport, score_profile):
    cc_mode, transport_mode, trim_mode = transport
    bucket, min_choices = score_profile
    case_dir = args.out / "raw" / scenario / label
    command = exact_runner.source_command(scenario, "sglb")
    command[0] = str(args.sim)
    set_option(command, "-o", case_dir / "logout.dat")
    set_option(command, "-cc", cc_mode)
    set_option(command, "-roce_transport_semantics", transport_mode)
    set_option(command, "-roce_trim_recovery", trim_mode)
    set_option(command, "-sglb_quality_bucket", bucket)
    set_option(command, "-sglb_min_choices", min_choices)
    return {
        "scenario": scenario,
        "kind": "all_to_all",
        "scheme": "sglb",
        "lb_name": "sglb",
        "variant": label,
        "cc_mode": cc_mode,
        "transport_mode": transport_mode,
        "inflate_diag": (
            "disabled" if transport_mode == "mrc_exact_bounded" else
            "natural_nodup_old" if cc_mode == "dcqcn_variant_nodup_old"
            else "natural"
        ),
        "trim_mode": trim_mode,
        "sglb_bucket": bucket,
        "sglb_min_choices": min_choices,
        "case_dir": case_dir,
        "command": command,
        "traffic_file": Path(command[command.index("-tm") + 1]),
        "expected_flows": int(command[command.index("-conns") + 1]),
        "flows_data": None,
    }


def make_specs(args):
    specs = []
    default_score = SCORE_PROFILES["default_b20_k3"]
    for scenario in SCENARIOS:
        for label, transport in TRANSPORTS.items():
            specs.append(make_spec(
                args, scenario, label, transport, default_score))
        for label, score_profile in SCORE_PROFILES.items():
            if label == "default_b20_k3":
                continue
            specs.append(make_spec(
                args, scenario, "exact_" + label,
                TRANSPORTS["exact_bounded"], score_profile))
        for label, score_profile in LEGACY_INTERACTION_PROFILES.items():
            specs.append(make_spec(
                args, scenario, label,
                TRANSPORTS["natural_cumulative"], score_profile))
    return specs


def validate_specs(specs):
    expected = len(SCENARIOS) * (
        len(TRANSPORTS) + len(SCORE_PROFILES) - 1 +
        len(LEGACY_INTERACTION_PROFILES))
    if len(specs) != expected:
        raise ValueError(f"expected {expected} specs, got {len(specs)}")
    for spec in specs:
        argv = spec["command"]
        required = {
            "-lb": "sglb",
            "-cc": spec["cc_mode"],
            "-roce_rx_mode": "sp",
            "-roce_sack_bitmap_bits": "64",
            "-queue_type": "composite_ecn_lb",
            "-host_queue_type": "prio",
            "-roce_transport_semantics": spec["transport_mode"],
            "-roce_trim_recovery": spec["trim_mode"],
            "-sglb_quality_bucket": str(spec["sglb_bucket"]),
            "-sglb_min_choices": str(spec["sglb_min_choices"]),
        }
        for flag, value in required.items():
            if flag not in argv or argv[argv.index(flag) + 1] != value:
                raise ValueError(
                    f"{spec['scenario']}:{spec['variant']} missing "
                    f"{flag}={value}")


def config_ok(text, spec, returncode):
    expected = (
        "lb mode sglb",
        f"cc mode {spec['cc_mode']}",
        "RoCE receive mode sp",
        "RoCE SACK bitmap 64 bits",
        f"RoceTransportConfig semantics={spec['transport_mode']}",
        f"roce_trim_recovery={spec['trim_mode']}",
        f"bucket {spec['sglb_bucket']:g}",
        f"min choices {spec['sglb_min_choices']}",
    )
    return int(returncode == 0 and all(item in text for item in expected))


def parse_run(spec, returncode, elapsed_s):
    row = legacy_runner.parse_run(spec, returncode, elapsed_s)
    text = Path(row["stdout"]).read_text(errors="ignore")
    row.update(metrics.parse_key_values(
        text, "SglbRouteDiag", SGLB_FIELDS))
    row.update(metrics.parse_key_values(
        text, "QueueCvDiag", QUEUE_CV_FIELDS))
    row.update(metrics.parse_key_values(
        text, "BoundedRecoveryDiag", bounded_runner.BOUNDED_FIELDS))
    row.update({
        "transport_mode": spec["transport_mode"],
        "sglb_bucket": spec["sglb_bucket"],
        "sglb_min_choices": spec["sglb_min_choices"],
        "config_ok": config_ok(text, spec, returncode),
    })
    return row


def run_spec(spec, args):
    case_dir = spec["case_dir"]
    case_dir.mkdir(parents=True, exist_ok=True)
    command_text = shlex.join(spec["command"])
    fingerprint = hashlib.sha256((
        file_sha256(args.sim) + "\0" + file_sha256(spec["traffic_file"]) +
        "\0" + command_text
    ).encode()).hexdigest()
    required = tuple(case_dir / name for name in (
        "stdout.log", "command.txt", "returncode.txt", "runtime.txt",
        "fingerprint.txt",
    ))
    if not args.force and all(path.exists() for path in required):
        if ((case_dir / "command.txt").read_text().strip() == command_text and
                (case_dir / "fingerprint.txt").read_text().strip() ==
                fingerprint):
            row = parse_run(
                spec, int((case_dir / "returncode.txt").read_text()),
                float((case_dir / "runtime.txt").read_text()))
            if row["config_ok"] and row["all_flows_completed"]:
                print(f"cached {spec['scenario']} {spec['variant']}",
                      flush=True)
                return row

    (case_dir / "command.txt").write_text(command_text + "\n")
    (case_dir / "fingerprint.txt").write_text(fingerprint + "\n")
    print(f"run {spec['scenario']} {spec['variant']}", flush=True)
    started = time.monotonic()
    try:
        with (case_dir / "stdout.log").open("w") as handle:
            process = subprocess.run(
                spec["command"], cwd=case_dir, stdout=handle,
                stderr=subprocess.STDOUT, timeout=args.timeout, check=False)
        returncode = process.returncode
    except subprocess.TimeoutExpired:
        returncode = 124
    elapsed_s = time.monotonic() - started
    (case_dir / "returncode.txt").write_text(f"{returncode}\n")
    (case_dir / "runtime.txt").write_text(f"{elapsed_s:.6f}\n")
    row = parse_run(spec, returncode, elapsed_s)
    print(
        f"done {spec['scenario']} {spec['variant']} "
        f"cct={row['primary_us']:.3f}us "
        f"flows={row['completed']}/{row['flows']} runtime={elapsed_s:.1f}s",
        flush=True)
    return row


def write_csv(path, rows, delimiter=","):
    rows = list(rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]),
                                delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def build_report(rows):
    lookup = {(row["scenario"], row["variant"]): row for row in rows}
    lines = [
        "# SGLB Exact-Bounded Interaction Diagnosis",
        "",
        "128 nodes, seed 13, SP/SACK and composite queues. All cells are "
        "All-to-All collective completion time in microseconds; lower is "
        "better.",
        "",
        "## Transport Ablation",
        "",
        "| Scenario | Natural+Cumulative | Natural+Exact | "
        "Narrow+Cumulative | Narrow+Exact | Exact+Bounded |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario in SCENARIOS:
        lines.append(
            f"| {scenario} | " + " | ".join(
                f"{lookup[(scenario, variant)]['primary_us']:.3f}"
                for variant in TRANSPORTS) + " |")
    lines += [
        "",
        "## Score Resolution Ablation Under Exact+Bounded",
        "",
        "| Scenario | b20/k3 | b10/k3 | b5/k3 | b20/k1 | b5/k1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    labels = (
        "exact_bounded", "exact_fine_b10_k3", "exact_fine_b5_k3",
        "exact_strict_b20_k1", "exact_strict_b5_k1",
    )
    for scenario in SCENARIOS:
        lines.append(
            f"| {scenario} | " + " | ".join(
                f"{lookup[(scenario, label)]['primary_us']:.3f}"
                for label in labels) + " |")
    lines += [
        "",
        "## Transport x Selector Interaction",
        "",
        "| Scenario | b20/k3 Legacy | b20/k3 Exact | b10/k3 Legacy | "
        "b10/k3 Exact | b20/k1 Legacy | b20/k1 Exact |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scenario in SCENARIOS:
        interaction_labels = (
            "natural_cumulative", "exact_bounded",
            "legacy_fine_b10_k3", "exact_fine_b10_k3",
            "legacy_strict_b20_k1", "exact_strict_b20_k1",
        )
        lines.append(
            f"| {scenario} | " + " | ".join(
                f"{lookup[(scenario, label)]['primary_us']:.3f}"
                for label in interaction_labels) + " |")
    lines += [
        "",
        "## Selector Visibility",
        "",
        "Each cell is `same-quality%; average candidate count; average "
        "distinct quality count; average raw score spread`.",
        "",
        "| Scenario | Variant | Visibility |",
        "| --- | --- | --- |",
    ]
    for scenario in SCENARIOS:
        for label in labels:
            row = lookup[(scenario, label)]
            calls = float(row["route_calls"])
            same = 100.0 * float(row["all_same_quality_calls"]) / calls
            lines.append(
                f"| {scenario} | {label} | {same:.2f}%; "
                f"{float(row['avg_candidate_choices']):.3f}; "
                f"{float(row['avg_distinct_qualities']):.3f}; "
                f"{float(row['avg_score_spread']):.3f} |")
    return "\n".join(lines) + "\n"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sim", type=Path, default=ROOT / "sim/datacenter/htsim_roce")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    args.sim = args.sim.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    specs = make_specs(args)
    validate_specs(specs)
    write_csv(args.out / "commands.tsv", ({
        "scenario": spec["scenario"],
        "variant": spec["variant"],
        "command": shlex.join(spec["command"]),
    } for spec in specs), delimiter="\t")
    if args.dry_run:
        print(f"validated {len(specs)} SGLB diagnostic runs")
        return 0
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.workers) as executor:
        futures = [executor.submit(run_spec, spec, args) for spec in specs]
        rows = [future.result() for future in futures]
    rows.sort(key=lambda row: (
        SCENARIOS.index(row["scenario"]), row["variant"]))
    write_csv(args.out / "summary.csv", rows)
    (args.out / "sglb_exact_bounded_interaction_diag_for_gpt.md").write_text(
        build_report(rows), encoding="utf-8")
    if any(not row["config_ok"] or not row["all_flows_completed"]
           for row in rows):
        raise RuntimeError("one or more diagnostic runs failed validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
