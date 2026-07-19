#!/usr/bin/env python3
"""Compare DCQCN duplicate credit and TRIM recovery across four LB schemes."""

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
import experiment_metrics as metrics  # noqa: E402
import feedback_eval_common as common  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = (
    ROOT / "experiments/n-mrc/output/"
    "dcqcn_narrow_trim_lb_compare_128_seed13"
)
CANONICAL_ROOT = ROOT / "experiments/n-mrc/output"
if not (CANONICAL_ROOT / "nmrc_communication_p2p_128").exists():
    workspace_output = Path(
        "/home/user/workspace/csg-htsim/experiments/n-mrc/output"
    )
    if workspace_output.exists():
        CANONICAL_ROOT = workspace_output
P2P_SOURCE = (
    CANONICAL_ROOT / "nmrc_communication_p2p_128/"
    "raw/simple/nodes_128/seed_13"
)
ALLTOALL_SOURCE = (
    CANONICAL_ROOT /
    "nmrc_communication_alltoall_128_seed13/raw/alltoall/"
    "nodes_128/seed_13"
)

SCENARIOS = (
    "asymmetric_permutation_16mib",
    "standard_websearch_proxy_80pct",
    "full_global_p16_256mib_background_on",
    "full_global_p4_64mib_background_off",
)
P2P_SCENARIOS = set(SCENARIOS[:2])
SCHEMES = {
    "sglb": "sglb",
    "ar": "adaptive-routing",
    "reps": "reps",
    "mrc": "mrc",
}
VARIANTS = {
    "natural_cumulative": {
        "cc": "dcqcn_variant",
        "inflate": "natural",
        "trim": "cumulative",
    },
    "natural_exact": {
        "cc": "dcqcn_variant",
        "inflate": "natural",
        "trim": "exact",
    },
    "narrow_cumulative": {
        "cc": "dcqcn_variant_nodup_old",
        "inflate": "natural_nodup_old",
        "trim": "cumulative",
    },
    "narrow_exact": {
        "cc": "dcqcn_variant_nodup_old",
        "inflate": "natural_nodup_old",
        "trim": "exact",
    },
}
ROCE_FIELDS = tuple(dict.fromkeys(metrics.ROCE_FIELDS + (
    "duplicate_acks", "duplicate_inflate_suppressed",
)))
QUEUE_FIELDS = metrics.QUEUE_FIELDS
MRC_FIELDS = metrics.NMRC_FIELDS


def point_flows(scenario):
    topology = common.TOPOLOGIES[128]
    if scenario == "standard_websearch_proxy_80pct":
        return common.websearch_flows(topology, 13, 0.8, 10000)
    return common.permutation_flows(topology, 13, 16 * common.MIB, 0)


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
    index = argv.index(flag)
    argv[index + 1] = str(value)


def strip_option(argv, flag, value_count=1):
    while flag in argv:
        index = argv.index(flag)
        del argv[index:index + 1 + value_count]


def source_command_path(scenario, scheme):
    source = P2P_SOURCE if scenario in P2P_SCENARIOS else ALLTOALL_SOURCE
    return source / scenario / scheme / "command.txt"


def load_source_command(scenario, scheme):
    path = source_command_path(scenario, scheme)
    if not path.exists():
        raise FileNotFoundError(f"missing canonical command: {path}")
    return shlex.split(path.read_text(encoding="utf-8").strip())


def make_specs(args):
    specs = []
    for scenario in SCENARIOS:
        for scheme, lb_name in SCHEMES.items():
            source = load_source_command(scenario, scheme)
            for variant, config in VARIANTS.items():
                case_dir = args.out / "raw" / scenario / scheme / variant
                argv = list(source)
                argv[0] = str(args.sim)
                set_option(argv, "-o", case_dir / "logout.dat")
                set_option(argv, "-cc", config["cc"])
                strip_option(argv, "-roce_trim_recovery")
                argv += ["-roce_trim_recovery", config["trim"]]
                specs.append({
                    "scenario": scenario,
                    "kind": (
                        "point_to_point" if scenario in P2P_SCENARIOS
                        else "all_to_all"
                    ),
                    "scheme": scheme,
                    "lb_name": lb_name,
                    "variant": variant,
                    "cc_mode": config["cc"],
                    "inflate_diag": config["inflate"],
                    "trim_mode": config["trim"],
                    "case_dir": case_dir,
                    "command": argv,
                    "traffic_file": Path(argv[argv.index("-tm") + 1]),
                    "expected_flows": int(argv[argv.index("-conns") + 1]),
                    "flows_data": (
                        point_flows(scenario)
                        if scenario in P2P_SCENARIOS else None
                    ),
                })
    return specs


def masked_variant_command(command):
    masked = list(command)
    set_option(masked, "-o", "<OUTPUT>")
    set_option(masked, "-cc", "<CC>")
    set_option(masked, "-roce_trim_recovery", "<TRIM>")
    return masked


def validate_variant_commands(specs):
    grouped = {}
    for spec in specs:
        grouped.setdefault((spec["scenario"], spec["scheme"]), []).append(spec)
    expected_groups = len(SCENARIOS) * len(SCHEMES)
    if len(grouped) != expected_groups:
        raise ValueError(f"expected {expected_groups} groups, got {len(grouped)}")
    for key, group in grouped.items():
        if len(group) != len(VARIANTS):
            raise ValueError(f"{key}: expected {len(VARIANTS)} variants")
        commands = [masked_variant_command(item["command"]) for item in group]
        if any(command != commands[0] for command in commands[1:]):
            raise ValueError(f"{key}: transport commands differ beyond 2x2 axes")


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def config_ok(text, spec, returncode):
    required = (
        f"lb mode {spec['lb_name']}",
        f"cc mode {spec['cc_mode']}",
        "queue_type 11",
        "host queue_type 4",
        "RoCE receive mode sp",
        "RoCE SACK bitmap 64 bits",
        f"RoCE TRIM recovery mode {spec['trim_mode']}",
        f"dcqcn_variant_inflate={spec['inflate_diag']}",
        f"roce_trim_recovery={spec['trim_mode']}",
    )
    return int(returncode == 0 and all(item in text for item in required))


def parse_run(spec, returncode, elapsed_s):
    case_dir = spec["case_dir"]
    text = (case_dir / "stdout.log").read_text(errors="ignore")
    finishes = list(metrics.FINISH_RE.finditer(text))
    completion_times = [float(match.group(4)) for match in finishes]
    mapping_ok = True
    if spec["kind"] == "point_to_point":
        mapping = metrics.source_id_map(
            case_dir / "idmap.txt", spec["flows_data"])
        mapping_ok = len(mapping) == spec["expected_flows"]
        samples = []
        for match in finishes:
            flow = mapping.get(int(match.group(3)))
            if flow is None:
                mapping_ok = False
                continue
            samples.append(float(match.group(4)) - flow.start_us)
    else:
        samples = completion_times

    primary = (
        metrics.percentile(samples, 0.99)
        if spec["kind"] == "point_to_point"
        else (max(samples) if samples else 0.0)
    )
    row = {
        "scenario": spec["scenario"],
        "kind": spec["kind"],
        "scheme": spec["scheme"],
        "variant": spec["variant"],
        "cc_mode": spec["cc_mode"],
        "trim_mode": spec["trim_mode"],
        "primary_metric": (
            "p99_fct_us" if spec["kind"] == "point_to_point"
            else "all_to_all_cct_us"
        ),
        "primary_us": primary,
        "avg_fct_or_finish_us": sum(samples) / len(samples) if samples else 0.0,
        "p50_fct_or_finish_us": metrics.percentile(samples, 0.50),
        "p95_fct_or_finish_us": metrics.percentile(samples, 0.95),
        "p99_fct_or_finish_us": metrics.percentile(samples, 0.99),
        "p999_fct_or_finish_us": metrics.percentile(samples, 0.999),
        "max_fct_or_cct_us": max(samples) if samples else 0.0,
        "completed": len(completion_times),
        "flows": spec["expected_flows"],
        "returncode": returncode,
        "config_ok": config_ok(text, spec, returncode),
        "all_flows_completed": int(
            len(completion_times) == spec["expected_flows"] and mapping_ok
        ),
        "runtime_s": elapsed_s,
        "stdout": str(case_dir / "stdout.log"),
        "command": shlex.join(spec["command"]),
    }
    row.update(metrics.parse_key_values(text, "RoceDiag", ROCE_FIELDS))
    row.update(metrics.parse_key_values(text, "QueueDiag", QUEUE_FIELDS))
    row.update(metrics.parse_key_values(text, "MrcDiag", MRC_FIELDS))
    new_packets = 0
    retx_packets = 0
    for match in metrics.NEW_RETX_RE.finditer(text):
        new_packets = int(match.group(1))
        retx_packets = int(match.group(2))
    row["new_packets"] = new_packets
    row["retx_packets"] = retx_packets
    row["retx_ratio"] = (
        retx_packets / float(new_packets) if new_packets else 0.0
    )
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
                (case_dir / "fingerprint.txt").read_text().strip() == fingerprint):
            row = parse_run(
                spec,
                int((case_dir / "returncode.txt").read_text()),
                float((case_dir / "runtime.txt").read_text()),
            )
            if row["config_ok"] and row["all_flows_completed"]:
                print(
                    f"cached {spec['scenario']} {spec['scheme']} "
                    f"{spec['variant']}", flush=True)
                return row

    (case_dir / "command.txt").write_text(command_text + "\n")
    (case_dir / "fingerprint.txt").write_text(fingerprint + "\n")
    print(
        f"run {spec['scenario']} {spec['scheme']} {spec['variant']}",
        flush=True)
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
        f"done {spec['scenario']} {spec['scheme']} {spec['variant']} "
        f"primary={row['primary_us']:.3f}us "
        f"flows={row['completed']}/{row['flows']} "
        f"runtime={elapsed_s:.1f}s", flush=True)
    return row


def pct_change(new, old):
    if not old:
        return float("nan")
    return 100.0 * (new / old - 1.0)


def fmt_pct(value):
    return "n/a" if not math.isfinite(value) else f"{value:+.2f}%"


def geometric_mean(values):
    values = [value for value in values if value > 0]
    if not values:
        return float("nan")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def build_report(rows):
    lookup = {
        (row["scenario"], row["scheme"], row["variant"]): row
        for row in rows
    }
    lines = [
        "# DCQCN Narrow and TRIM Recovery LB Comparison",
        "",
        "128 nodes, seed 13, SP/SACK, composite queue. This is a strict ",
        "2x2 transport ablation: Natural/Narrow duplicate credit crossed ",
        "with cumulative/exact-PSN TRIM recovery. Lower latency is better.",
        "",
        "## Primary Metric",
        "",
        "| Scenario | LB | Natural+Cumulative | Natural+Exact | "
        "Narrow+Cumulative | Narrow+Exact | Exact effect (Natural) | "
        "Narrow effect (Cumulative) | Combined effect |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    effects = []
    for scenario in SCENARIOS:
        for scheme in SCHEMES:
            base = lookup[(scenario, scheme, "natural_cumulative")]
            natural_exact = lookup[(scenario, scheme, "natural_exact")]
            narrow_cumulative = lookup[(scenario, scheme, "narrow_cumulative")]
            narrow_exact = lookup[(scenario, scheme, "narrow_exact")]
            exact_effect = pct_change(
                natural_exact["primary_us"], base["primary_us"])
            narrow_effect = pct_change(
                narrow_cumulative["primary_us"], base["primary_us"])
            combined_effect = pct_change(
                narrow_exact["primary_us"], base["primary_us"])
            effects.append((combined_effect, scenario, scheme))
            lines.append(
                f"| {scenario} | {scheme} | {base['primary_us']:.3f} | "
                f"{natural_exact['primary_us']:.3f} | "
                f"{narrow_cumulative['primary_us']:.3f} | "
                f"{narrow_exact['primary_us']:.3f} | "
                f"{fmt_pct(exact_effect)} | {fmt_pct(narrow_effect)} | "
                f"{fmt_pct(combined_effect)} |"
            )

    lines += [
        "",
        "## Recovery Cost",
        "",
        "Each cell is `NACK(OOO/TRIM/LOSS); RTO; retx%`.",
        "",
        "| Scenario | LB | Natural+Cumulative | Natural+Exact | "
        "Narrow+Cumulative | Narrow+Exact |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    def recovery_cell(row):
        return (
            f"{row['nacks']:.0f}({row['nacks_ooo']:.0f}/"
            f"{row['nacks_trim']:.0f}/{row['nacks_loss']:.0f}); "
            f"{row['rtos']:.0f}; {100 * row['retx_ratio']:.3f}%"
        )

    for scenario in SCENARIOS:
        for scheme in SCHEMES:
            variants = [
                lookup[(scenario, scheme, variant)] for variant in VARIANTS
            ]
            lines.append(
                f"| {scenario} | {scheme} | " +
                " | ".join(recovery_cell(row) for row in variants) + " |"
            )

    lines += [
        "",
        "## Four-Scenario Geometric Mean",
        "",
        "Ratios are relative to Natural+Cumulative for the same scenario/LB.",
        "",
        "| LB | Natural+Exact | Narrow+Cumulative | Narrow+Exact | "
        "Exact effect inside Narrow |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for scheme in SCHEMES:
        ratios = {variant: [] for variant in VARIANTS}
        exact_inside_narrow = []
        for scenario in SCENARIOS:
            base = lookup[(scenario, scheme, "natural_cumulative")]["primary_us"]
            for variant in VARIANTS:
                value = lookup[(scenario, scheme, variant)]["primary_us"]
                ratios[variant].append(value / base)
            narrow_cumulative = lookup[
                (scenario, scheme, "narrow_cumulative")]["primary_us"]
            narrow_exact = lookup[
                (scenario, scheme, "narrow_exact")]["primary_us"]
            exact_inside_narrow.append(narrow_exact / narrow_cumulative)
        lines.append(
            f"| {scheme} | "
            f"{fmt_pct(100 * (geometric_mean(ratios['natural_exact']) - 1))} | "
            f"{fmt_pct(100 * (geometric_mean(ratios['narrow_cumulative']) - 1))} | "
            f"{fmt_pct(100 * (geometric_mean(ratios['narrow_exact']) - 1))} | "
            f"{fmt_pct(100 * (geometric_mean(exact_inside_narrow) - 1))} |"
        )

    finite_effects = [item for item in effects if math.isfinite(item[0])]
    wins = sum(effect < 0 for effect, _, _ in finite_effects)
    best = min(finite_effects)
    worst = max(finite_effects)
    lines += [
        "",
        "## Findings",
        "",
        f"- Narrow+Exact improves `{wins}/{len(finite_effects)}` cells versus "
        "Natural+Cumulative.",
        f"- Largest combined improvement: `{best[1]} / {best[2]}` "
        f"at `{best[0]:+.2f}%`.",
        f"- Largest combined regression: `{worst[1]} / {worst[2]}` "
        f"at `{worst[0]:+.2f}%`.",
        "- Exact commands are in `commands.tsv`; full tails, counters and "
        "runtime are in `summary.csv`; per-case stdout is under `raw/`.",
        "- This is a single-seed mechanism comparison, not a final universal "
        "performance claim.",
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
    validate_variant_commands(specs)
    write_csv(args.out / "commands.tsv", ({
        "scenario": spec["scenario"],
        "scheme": spec["scheme"],
        "variant": spec["variant"],
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
    variant_order = {name: index for index, name in enumerate(VARIANTS)}
    rows.sort(key=lambda row: (
        scenario_order[row["scenario"]], scheme_order[row["scheme"]],
        variant_order[row["variant"]],
    ))
    write_csv(args.out / "summary.csv", rows)
    invalid = [row for row in rows if not (
        row["returncode"] == 0 and row["config_ok"] and
        row["all_flows_completed"]
    )]
    if invalid:
        raise RuntimeError("invalid runs: " + ", ".join(
            f"{row['scenario']}:{row['scheme']}:{row['variant']}"
            for row in invalid
        ))
    report = args.out / "dcqcn_narrow_trim_lb_compare_for_gpt.md"
    report.write_text(build_report(rows), encoding="utf-8")
    print(args.out / "summary.csv")
    print(report)


if __name__ == "__main__":
    main()
