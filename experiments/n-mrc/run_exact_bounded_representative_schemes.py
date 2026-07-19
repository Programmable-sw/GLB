#!/usr/bin/env python3
"""Run the six canonical representative scenarios with exact bounded recovery."""

import argparse
import concurrent.futures
import csv
import hashlib
import math
from pathlib import Path
import shlex
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_dcqcn_narrow_trim_lb_compare as legacy_runner  # noqa: E402
import run_mrc_exact_bounded_recovery as bounded_runner  # noqa: E402
import experiment_metrics as metrics  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = (
    ROOT / "experiments/n-mrc/output/"
    "mrc_exact_bounded_representative_schemes_128_seed13"
)
CANONICAL_ROOT = (
    Path("/home/user/workspace/csg-htsim/experiments/n-mrc/output")
)
P2P_SOURCE = (
    CANONICAL_ROOT / "nmrc_communication_p2p_128/raw/simple/"
    "nodes_128/seed_13"
)
ALLTOALL_SOURCE = (
    CANONICAL_ROOT / "nmrc_communication_alltoall_128_seed13/raw/"
    "alltoall/nodes_128/seed_13"
)

SCENARIOS = (
    "healthy_permutation_16mib",
    "asymmetric_permutation_16mib",
    "standard_websearch_proxy_80pct",
    "full_global_p16_256mib_background_off",
    "full_global_p16_256mib_background_on",
    "full_global_p4_64mib_background_off",
)
P2P_SCENARIOS = set(SCENARIOS[:3])
SCHEMES = {
    "sglb": ("SGLB", "sglb", "sglb"),
    "mrc": ("MRC", "mrc", "mrc"),
    "ar": ("AR", "ar", "adaptive-routing"),
    "avail": ("Avail", "avail_fixed5", "avail"),
    "grade": ("Grade", "grade_fixed5", "grade"),
    "netaware": ("n-MRC", "n-mrc_fixed5", "netaware"),
}


def write_csv(path, rows, delimiter=","):
    rows = list(rows)
    if not rows:
        raise ValueError(f"no rows for {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]),
                                delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def set_option(argv, flag, value):
    if flag in argv:
        argv[argv.index(flag) + 1] = str(value)
    else:
        argv.extend((flag, str(value)))


def strip_option(argv, flag, value_count=1):
    while flag in argv:
        index = argv.index(flag)
        del argv[index:index + 1 + value_count]


def source_command(scenario, scheme):
    source = P2P_SOURCE if scenario in P2P_SCENARIOS else ALLTOALL_SOURCE
    source_dir = SCHEMES[scheme][1]
    path = source / scenario / source_dir / "command.txt"
    if not path.exists():
        raise FileNotFoundError(f"missing canonical command: {path}")
    argv = shlex.split(path.read_text(encoding="utf-8").strip())
    for flag in tuple(
            item for item in argv if item.startswith("-netaware_feedback_")):
        strip_option(argv, flag)
    return argv


def make_specs(args):
    specs = []
    for scenario in SCENARIOS:
        for scheme, (_, _, lb_name) in SCHEMES.items():
            case_dir = args.out / "raw" / scenario / scheme
            argv = source_command(scenario, scheme)
            argv[0] = str(args.sim)
            set_option(argv, "-o", case_dir / "logout.dat")
            set_option(argv, "-cc", "dcqcn_variant")
            set_option(argv, "-roce_transport_semantics",
                       "mrc_exact_bounded")
            set_option(argv, "-roce_trim_recovery", "exact")
            if scenario not in P2P_SCENARIOS and scheme == "ar":
                # The canonical cutoff left one AR QP unresolved in P16/off.
                # A larger safety cutoff does not alter a completed CCT.
                set_option(argv, "-end", "100000")
            if scheme == "netaware":
                strip_option(argv, "-netaware_weight_adaptation")
                argv.extend((
                    "-netaware_weight_adaptation", "good_share_cap",
                ))
            traffic_file = Path(argv[argv.index("-tm") + 1])
            if not traffic_file.exists():
                raise FileNotFoundError(
                    f"missing canonical traffic matrix: {traffic_file}")
            specs.append({
                "scenario": scenario,
                "kind": (
                    "point_to_point" if scenario in P2P_SCENARIOS
                    else "all_to_all"
                ),
                "scheme": scheme,
                "lb_name": lb_name,
                "variant": "mrc_exact_bounded",
                "cc_mode": "dcqcn_variant",
                "inflate_diag": "disabled",
                "trim_mode": "exact",
                "case_dir": case_dir,
                "command": argv,
                "traffic_file": traffic_file,
                "expected_flows": int(argv[argv.index("-conns") + 1]),
                "flows_data": (
                    legacy_runner.point_flows(scenario)
                    if scenario in P2P_SCENARIOS else None
                ),
            })
    return specs


def validate_specs(specs):
    expected = len(SCENARIOS) * len(SCHEMES)
    if len(specs) != expected:
        raise ValueError(f"expected {expected} specs, got {len(specs)}")
    for spec in specs:
        argv = spec["command"]
        required = {
            "-cc": "dcqcn_variant",
            "-roce_rx_mode": "sp",
            "-roce_sack_bitmap_bits": "64",
            "-queue_type": "composite_ecn_lb",
            "-host_queue_type": "prio",
            "-roce_transport_semantics": "mrc_exact_bounded",
            "-roce_trim_recovery": "exact",
        }
        for flag, value in required.items():
            if flag not in argv or argv[argv.index(flag) + 1] != value:
                raise ValueError(
                    f"{spec['scenario']}:{spec['scheme']} missing "
                    f"{flag}={value}")


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_run(spec, returncode, elapsed_s):
    row = legacy_runner.parse_run(spec, returncode, elapsed_s)
    text = Path(row["stdout"]).read_text(errors="ignore")
    row.update(metrics.parse_key_values(
        text, "BoundedRecoveryDiag", bounded_runner.BOUNDED_FIELDS))
    expected = (
        "RoceTransportConfig semantics=mrc_exact_bounded",
        "awnd=cwnd_minus_inflight",
        "dcqcn_variant_inflate=disabled",
    )
    row["config_ok"] = int(
        row["config_ok"] and all(item in text for item in expected))
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
                print(f"cached {spec['scenario']} {spec['scheme']}",
                      flush=True)
                return row

    (case_dir / "command.txt").write_text(command_text + "\n")
    (case_dir / "fingerprint.txt").write_text(fingerprint + "\n")
    print(f"run {spec['scenario']} {spec['scheme']}", flush=True)
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
        f"done {spec['scenario']} {spec['scheme']} "
        f"primary={row['primary_us']:.3f}us "
        f"flows={row['completed']}/{row['flows']} runtime={elapsed_s:.1f}s",
        flush=True)
    return row


def recovery_cell(row):
    return (
        f"{int(row['nacks'])} "
        f"({int(row['nacks_ooo'])}/{int(row['nacks_trim'])}/"
        f"{int(row['nacks_loss'])}); {int(row['rtos'])}; "
        f"{100 * row['retx_ratio']:.3f}%"
    )


def build_report(rows, revision):
    lookup = {(row["scenario"], row["scheme"]): row for row in rows}
    lines = [
        "# Exact Bounded Representative Scheme Comparison",
        "",
        "128 nodes, seed 13, SP/SACK, composite queue, dcqcn_variant and "
        "mrc_exact_bounded transport. Each scheme uses its canonical final "
        "selector/feedback configuration. Lower is better.",
        "",
        "## Primary Metric",
        "",
        "P2P cells are p99 FCT; All-to-All cells are CCT, all in microseconds.",
        "",
        "| Scenario | " + " | ".join(SCHEMES) + " |",
        "| --- | " + " | ".join("---:" for _ in SCHEMES) + " |",
    ]
    for scenario in SCENARIOS:
        values = [lookup[(scenario, scheme)]["primary_us"] for scheme in SCHEMES]
        lines.append(
            f"| {scenario} | " + " | ".join(
                f"{value:.3f}" for value in values) + " |")

    lines += [
        "",
        "## Recovery Cost",
        "",
        "Each cell is `NACK (OOO/TRIM/LOSS); RTO; retx%`.",
        "",
        "| Scenario | " + " | ".join(SCHEMES) + " |",
        "| --- | " + " | ".join("---" for _ in SCHEMES) + " |",
    ]
    for scenario in SCENARIOS:
        lines.append(
            f"| {scenario} | " + " | ".join(
                recovery_cell(lookup[(scenario, scheme)])
                for scheme in SCHEMES) + " |")

    lines += ["", "## Scenario Winners", ""]
    for scenario in SCENARIOS:
        winner = min(
            (lookup[(scenario, scheme)]["primary_us"], scheme)
            for scheme in SCHEMES)
        lines.append(
            f"- `{scenario}`: `{winner[1]}` at `{winner[0]:.3f} us`.")
    lines += [
        "",
        "All bounded runs must finish with zero inflight and zero recovery "
        "reserve bytes; the observed reserve is checked against the one-MTU "
        "limit in `summary.csv`.",
        "",
        "This is a single-seed transport-unified preview, not a universal "
        "routing-performance claim. Commands are in `commands.tsv`, complete "
        "metrics in `summary.csv`, and stdout under `raw/`.",
        "",
        f"Source revision: `{revision}`.",
    ]
    return "\n".join(lines) + "\n"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sim", type=Path, default=ROOT / "sim/datacenter/htsim_roce")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=3)
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
        "scheme": spec["scheme"],
        "command": shlex.join(spec["command"]),
    } for spec in specs), delimiter="\t")
    if args.dry_run:
        print(f"validated {len(specs)} commands")
        return

    rows = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(args.workers, len(specs)))) as executor:
        futures = [executor.submit(run_spec, spec, args) for spec in specs]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())
    scenario_order = {name: index for index, name in enumerate(SCENARIOS)}
    scheme_order = {name: index for index, name in enumerate(SCHEMES)}
    rows.sort(key=lambda row: (
        scenario_order[row["scenario"]], scheme_order[row["scheme"]]))
    write_csv(args.out / "summary.csv", rows)

    invalid = [row for row in rows if not (
        row["returncode"] == 0 and row["config_ok"] and
        row["all_flows_completed"] and row["inflight_final"] == 0 and
        row["recovery_inflight_final_bytes"] == 0 and
        row["recovery_inflight_max_bytes"] <= 4096
    )]
    if invalid:
        raise RuntimeError("invalid runs: " + ", ".join(
            f"{row['scenario']}:{row['scheme']}" for row in invalid))
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report = args.out / (
        "mrc_exact_bounded_representative_schemes_for_gpt.md")
    report.write_text(build_report(rows, revision), encoding="utf-8")
    print(args.out / "summary.csv")
    print(report)


if __name__ == "__main__":
    main()
