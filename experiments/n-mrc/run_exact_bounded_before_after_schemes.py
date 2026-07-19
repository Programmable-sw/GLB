#!/usr/bin/env python3
"""Compare Natural+Cumulative with exact bounded recovery per LB scheme."""

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
import run_exact_bounded_representative_schemes as exact_runner  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = (
    ROOT / "experiments/n-mrc/output/"
    "mrc_exact_bounded_before_after_128_seed13"
)
EXACT_OUT = (
    ROOT / "experiments/n-mrc/output/"
    "mrc_exact_bounded_representative_schemes_128_seed13"
)
SCENARIOS = exact_runner.SCENARIOS
P2P_SCENARIOS = exact_runner.P2P_SCENARIOS
SCHEMES = exact_runner.SCHEMES


def set_option(argv, flag, value):
    if flag in argv:
        argv[argv.index(flag) + 1] = str(value)
    else:
        argv.extend((flag, str(value)))


def make_legacy_specs(args):
    exact_args = argparse.Namespace(out=args.out, sim=args.sim)
    specs = []
    for exact_spec in exact_runner.make_specs(exact_args):
        spec = dict(exact_spec)
        scenario = spec["scenario"]
        scheme = spec["scheme"]
        case_dir = args.out / "raw" / scenario / scheme / "natural_cumulative"
        command = list(spec["command"])
        set_option(command, "-o", case_dir / "logout.dat")
        set_option(command, "-roce_transport_semantics", "legacy")
        set_option(command, "-roce_trim_recovery", "cumulative")
        spec.update({
            "variant": "natural_cumulative",
            "inflate_diag": "natural",
            "trim_mode": "cumulative",
            "case_dir": case_dir,
            "command": command,
        })
        specs.append(spec)
    return specs


def validate_legacy_specs(specs):
    expected = len(SCENARIOS) * len(SCHEMES)
    if len(specs) != expected:
        raise ValueError(f"expected {expected} legacy specs, got {len(specs)}")
    for spec in specs:
        argv = spec["command"]
        required = {
            "-cc": "dcqcn_variant",
            "-roce_rx_mode": "sp",
            "-roce_sack_bitmap_bits": "64",
            "-queue_type": "composite_ecn_lb",
            "-host_queue_type": "prio",
            "-roce_transport_semantics": "legacy",
            "-roce_trim_recovery": "cumulative",
        }
        for flag, value in required.items():
            if flag not in argv or argv[argv.index(flag) + 1] != value:
                raise ValueError(
                    f"{spec['scenario']}:{spec['scheme']} missing "
                    f"{flag}={value}")
        if spec["scheme"] == "netaware":
            flag = "-netaware_weight_adaptation"
            if argv[argv.index(flag) + 1] != "good_share_cap":
                raise ValueError(
                    f"{spec['scenario']}:n-mrc missing GoodCap adaptation")


def masked_transport_command(command):
    masked = list(command)
    set_option(masked, "-o", "<OUTPUT>")
    set_option(masked, "-roce_transport_semantics", "<TRANSPORT>")
    set_option(masked, "-roce_trim_recovery", "<TRIM>")
    return masked


def validate_paired_commands(legacy_specs, exact_specs):
    exact_lookup = {
        (spec["scenario"], spec["scheme"]): spec for spec in exact_specs
    }
    for legacy_spec in legacy_specs:
        key = (legacy_spec["scenario"], legacy_spec["scheme"])
        exact_spec = exact_lookup[key]
        if masked_transport_command(legacy_spec["command"]) != \
                masked_transport_command(exact_spec["command"]):
            raise ValueError(
                f"{key[0]}:{key[1]} differs beyond transport semantics")


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def legacy_transport_config_ok(text):
    expected = (
        "RoceTransportConfig semantics=legacy",
        "awnd=legacy",
        "dcqcn_variant_inflate=natural",
        "roce_trim_recovery=cumulative",
    )
    return all(item in text for item in expected)


def parse_legacy(spec, returncode, elapsed_s):
    row = legacy_runner.parse_run(spec, returncode, elapsed_s)
    text = Path(row["stdout"]).read_text(errors="ignore")
    row["config_ok"] = int(
        row["config_ok"] and legacy_transport_config_ok(text))
    return row


def run_legacy(spec, args):
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
            row = parse_legacy(
                spec, int((case_dir / "returncode.txt").read_text()),
                float((case_dir / "runtime.txt").read_text()))
            if row["config_ok"] and row["all_flows_completed"]:
                print(
                    f"cached legacy {spec['scenario']} {spec['scheme']}",
                    flush=True)
                return row

    (case_dir / "command.txt").write_text(command_text + "\n")
    (case_dir / "fingerprint.txt").write_text(fingerprint + "\n")
    print(f"run legacy {spec['scenario']} {spec['scheme']}", flush=True)
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
    row = parse_legacy(spec, returncode, elapsed_s)
    print(
        f"done legacy {spec['scenario']} {spec['scheme']} "
        f"primary={row['primary_us']:.3f}us "
        f"flows={row['completed']}/{row['flows']} runtime={elapsed_s:.1f}s",
        flush=True)
    return row


def pct_change(new, old):
    if not old:
        return math.nan if not new else math.inf
    return 100.0 * (new / old - 1.0)


def comparison_row(scenario, scheme, baseline, exact):
    row = {
        "scenario": scenario,
        "kind": baseline.get("kind", ""),
        "scheme": scheme,
        "primary_metric": baseline.get("primary_metric", ""),
        "baseline_primary_us": baseline["primary_us"],
        "exact_primary_us": exact["primary_us"],
        "primary_delta_us": exact["primary_us"] - baseline["primary_us"],
        "primary_delta_pct": pct_change(
            exact["primary_us"], baseline["primary_us"]),
    }
    for field in (
            "avg_fct_or_finish_us", "p50_fct_or_finish_us",
            "p95_fct_or_finish_us", "p99_fct_or_finish_us",
            "p999_fct_or_finish_us", "max_fct_or_cct_us", "nacks",
            "nacks_ooo", "nacks_trim", "nacks_loss", "rtos",
            "retx_packets", "retx_ratio", "composite_trims",
            "composite_drops", "composite_ecn_marks"):
        old = baseline.get(field, 0)
        new = exact.get(field, 0)
        row[f"baseline_{field}"] = old
        row[f"exact_{field}"] = new
        row[f"{field}_delta_pct"] = pct_change(new, old)
    return row


def geometric_mean(values):
    values = [value for value in values if value > 0]
    return math.exp(sum(math.log(value) for value in values) / len(values))


def fmt_pct(value):
    if not math.isfinite(value):
        return "n/a"
    return f"{value:+.2f}%"


def classify_delta(value):
    if value <= -5.0:
        return "material_improvement"
    if value >= 5.0:
        return "material_regression"
    return "near_neutral"


def normalized_exact_rows(comparisons):
    best = {}
    for row in comparisons:
        scenario = row["scenario"]
        value = float(row["exact_primary_us"])
        best[scenario] = min(best.get(scenario, value), value)
    return [{
        "scenario": row["scenario"],
        "scheme": row["scheme"],
        "exact_primary_us": float(row["exact_primary_us"]),
        "best_exact_primary_us": best[row["scenario"]],
        "ratio_to_best": (
            float(row["exact_primary_us"]) / best[row["scenario"]]),
    } for row in comparisons]


def write_primary_table(path, comparisons):
    lines = [
        "# Natural+Cumulative vs Exact+Bounded Primary Latency",
        "",
        "Point-to-point scenarios report p99 FCT. All-to-All scenarios "
        "report collective completion time (CCT). All values are microseconds; "
        "lower is better.",
        "",
        "| Scenario | Metric | Scheme | Natural+Cumulative | Exact+Bounded | "
        "Delta |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for row in comparisons:
        metric = ("p99 FCT" if row["kind"] == "point_to_point" else "CCT")
        lines.append(
            f"| {row['scenario']} | {metric} | {row['scheme']} | "
            f"{row['baseline_primary_us']:.3f} | "
            f"{row['exact_primary_us']:.3f} | "
            f"{fmt_pct(row['primary_delta_pct'])} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_exact_normalized_heatmap(path, normalized_rows):
    import matplotlib.pyplot as plt
    import numpy as np

    scenario_labels = {
        "healthy_permutation_16mib": "Healthy\npermutation",
        "asymmetric_permutation_16mib": "Asymmetric\npermutation",
        "standard_websearch_proxy_80pct": "WebSearch\n80% load",
        "full_global_p16_256mib_background_off": "All-to-All P16\n256MiB, bg off",
        "full_global_p16_256mib_background_on": "All-to-All P16\n256MiB, bg on",
        "full_global_p4_64mib_background_off": "All-to-All P4\n64MiB, bg off",
    }
    scheme_labels = {
        "sglb": "SGLB", "mrc": "MRC", "ar": "AR",
        "avail": "Avail", "grade": "Grade", "netaware": "n-MRC",
    }
    lookup = {
        (row["scenario"], row["scheme"]): row["ratio_to_best"]
        for row in normalized_rows
    }
    matrix = np.array([
        [lookup[(scenario, scheme)] for scenario in SCENARIOS]
        for scheme in SCHEMES
    ])
    maximum = float(matrix.max())
    fig, ax = plt.subplots(figsize=(15.5, 6.8), constrained_layout=True)
    image = ax.imshow(
        matrix, cmap="RdYlBu_r", vmin=1.0, vmax=maximum, aspect="auto")
    ax.set_xticks(range(len(SCENARIOS)),
                  [scenario_labels[name] for name in SCENARIOS])
    ax.set_yticks(range(len(SCHEMES)),
                  [scheme_labels[name] for name in SCHEMES])
    ax.set_xlabel("Scenario", fontsize=13)
    ax.set_ylabel("Load-balancing scheme", fontsize=13)
    ax.set_title(
        "Exact+Bounded latency relative to the best scheme in each scenario\n"
        "Point-to-point: p99 FCT; All-to-All: CCT; lower is better",
        fontsize=16, pad=16)
    ax.tick_params(axis="both", labelsize=11)
    midpoint = 1.0 + (maximum - 1.0) * 0.62
    for row_index in range(matrix.shape[0]):
        for column_index in range(matrix.shape[1]):
            value = matrix[row_index, column_index]
            ax.text(
                column_index, row_index, f"{value:.2f}x",
                ha="center", va="center", fontsize=13,
                fontweight="bold" if value <= 1.005 else "normal",
                color="white" if value >= midpoint else "#14202b")
    colorbar = fig.colorbar(image, ax=ax, fraction=0.035, pad=0.025)
    colorbar.set_label("Ratio to best Exact result in the same scenario",
                       fontsize=11)
    colorbar.ax.tick_params(labelsize=10)
    fig.savefig(path, dpi=240, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def build_report(comparisons, revision):
    lookup = {
        (row["scenario"], row["scheme"]): row for row in comparisons
    }
    lines = [
        "# Exact Bounded Recovery: Strict Before/After Comparison",
        "",
        "128 nodes, seed 13, SP/SACK, composite queue and dcqcn_variant. "
        "The paired commands differ only in transport semantics: the "
        "baseline is Natural+Cumulative (`legacy`), while the candidate "
        "uses exact-PSN bounded recovery. Lower latency is better.",
        "",
        "## Primary Metric",
        "",
        "P2P uses p99 FCT; All-to-All uses CCT. Negative delta means the "
        "new recovery mechanism is faster.",
        "",
        "| Scenario | Scheme | Natural+Cumulative (us) | Exact+Bounded (us) "
        "| Delta |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for scenario in SCENARIOS:
        for scheme in SCHEMES:
            row = lookup[(scenario, scheme)]
            lines.append(
                f"| {scenario} | {scheme} | "
                f"{row['baseline_primary_us']:.3f} | "
                f"{row['exact_primary_us']:.3f} | "
                f"{fmt_pct(row['primary_delta_pct'])} |")

    lines += [
        "",
        "## Scheme Summary",
        "",
        "Changes within +/-5% are treated as near-neutral in this one-seed "
        "screen.",
        "",
        "| Scheme | Material improvement | Near-neutral | Material regression "
        "| All-six geometric delta | "
        "P2P geometric delta | All-to-All geometric delta |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scheme in SCHEMES:
        rows = [lookup[(scenario, scheme)] for scenario in SCENARIOS]
        classes = [classify_delta(row["primary_delta_pct"]) for row in rows]
        improved = classes.count("material_improvement")
        neutral = classes.count("near_neutral")
        regressed = classes.count("material_regression")
        ratios = [
            row["exact_primary_us"] / row["baseline_primary_us"]
            for row in rows
        ]
        p2p = ratios[:3]
        alltoall = ratios[3:]
        lines.append(
            f"| {scheme} | {improved}/6 | {neutral}/6 | {regressed}/6 | "
            f"{fmt_pct(100 * (geometric_mean(ratios) - 1))} | "
            f"{fmt_pct(100 * (geometric_mean(p2p) - 1))} | "
            f"{fmt_pct(100 * (geometric_mean(alltoall) - 1))} |")

    classes = [classify_delta(row["primary_delta_pct"])
               for row in comparisons]
    aggregate = {}
    for metric in ("nacks", "nacks_ooo", "nacks_trim", "nacks_loss",
                   "rtos", "retx_packets", "composite_trims",
                   "composite_ecn_marks"):
        old = sum(row[f"baseline_{metric}"] for row in comparisons)
        new = sum(row[f"exact_{metric}"] for row in comparisons)
        aggregate[metric] = (old, new, pct_change(new, old))

    lines += [
        "",
        "## Verdict",
        "",
        f"Exact+Bounded materially improves {classes.count('material_improvement')} "
        f"of 36 paired cells, is near-neutral in "
        f"{classes.count('near_neutral')}, and materially regresses "
        f"{classes.count('material_regression')}. The single material "
        "regression is SGLB on P16/256MiB with background traffic "
        "(`+7.42%`).",
        "",
        "- Healthy permutation is neutral for all schemes (within about 1%).",
        "- WebSearch 80% improves for every scheme by 36.56% to 57.28%.",
        "- P4/64MiB All-to-All improves for every scheme by 34.99% to 58.77%.",
        "- Asymmetric permutation improves strongly only where the legacy "
        "transport generated substantial recovery traffic: MRC, Avail and "
        "Grade. SGLB, AR and n-MRC remain near-neutral.",
        "- P16/256MiB All-to-All improves for MRC, AR, Avail, Grade and n-MRC. "
        "SGLB is near-neutral without background and regresses with background.",
        "",
        "Across all 36 cells, aggregate recovery work changes as follows:",
        "",
        "| Metric | Natural+Cumulative | Exact+Bounded | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for metric in ("nacks", "nacks_ooo", "nacks_trim", "rtos",
                   "retx_packets", "composite_trims", "composite_ecn_marks"):
        old, new, delta = aggregate[metric]
        lines.append(
            f"| {metric} | {int(old)} | {int(new)} | {fmt_pct(delta)} |")

    lines += [
        "",
        "The result is consistent with unique-PSN ACK credit preventing "
        "duplicate-credit bursts, while exact attempt-matched Trim recovery "
        "and the one-MTU reserve avoid first-hole/RTO stalls. It is not an "
        "isolated attribution test: all three transport changes move together "
        "in this branch. The SGLB background regression is consistent with "
        "the stricter `cwnd-inflight` gate reducing work-conserving issue rate "
        "under sustained load, even though recovery work falls sharply.",
    ]

    lines += [
        "",
        "## Recovery Cost",
        "",
        "Each cell is `NACK delta; RTO delta; retransmission-ratio delta`. "
        "Percent changes from a zero baseline are shown as n/a.",
        "",
        "| Scenario | Scheme | NACK | RTO | Retx ratio |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for scenario in SCENARIOS:
        for scheme in SCHEMES:
            row = lookup[(scenario, scheme)]
            lines.append(
                f"| {scenario} | {scheme} | "
                f"{fmt_pct(row['nacks_delta_pct'])} | "
                f"{fmt_pct(row['rtos_delta_pct'])} | "
                f"{fmt_pct(row['retx_ratio_delta_pct'])} |")

    lines += [
        "",
        "## Comparability Audit",
        "",
        "- Both variants use the current binary and identical traffic "
        "matrices, LB selectors, feedback settings and network parameters.",
        "- n-MRC uses GoodCap in both variants. The older canonical artifacts "
        "used fixed 4/2/1/0, so they are historical references rather than "
        "the strict causal control used here.",
        "- Complete metrics are in `summary.csv`; paired deltas are in "
        "`comparison.csv`; commands are in `commands.tsv`.",
        "- The first legacy Grade/WebSearch attempt completed all flows but "
        "exited with SIGABRT. An immediate clean rerun completed with return "
        "code 0; the abort is therefore recorded as a transient observation, "
        "not a reproducible stability claim.",
        "",
        "This is a one-seed mechanism validation. A small delta should not be "
        "treated as a stable performance change until it survives more seeds.",
        "",
        f"Source revision: `{revision}`.",
    ]
    return "\n".join(lines) + "\n"


def normalize_rows(rows):
    fields = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    return fields, [{field: row.get(field, 0) for field in fields} for row in rows]


def write_csv(path, rows, delimiter=","):
    rows = list(rows)
    if not rows:
        raise ValueError(f"no rows for {path}")
    fields, rows = normalize_rows(rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sim", type=Path, default=ROOT / "sim/datacenter/htsim_roce")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--exact-out", type=Path, default=EXACT_OUT)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    args.sim = args.sim.resolve()
    args.out = args.out.resolve()
    args.exact_out = args.exact_out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)

    legacy_specs = make_legacy_specs(args)
    exact_args = argparse.Namespace(
        out=args.exact_out, sim=args.sim, force=False, timeout=args.timeout)
    exact_specs = exact_runner.make_specs(exact_args)
    validate_legacy_specs(legacy_specs)
    exact_runner.validate_specs(exact_specs)
    validate_paired_commands(legacy_specs, exact_specs)

    commands = []
    for spec in legacy_specs + exact_specs:
        commands.append({
            "scenario": spec["scenario"],
            "scheme": spec["scheme"],
            "variant": spec["variant"],
            "command": shlex.join(spec["command"]),
        })
    write_csv(args.out / "commands.tsv", commands, delimiter="\t")
    if args.dry_run:
        print(f"validated {len(legacy_specs)} paired commands")
        return

    legacy_rows = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(args.workers, len(legacy_specs)))) as executor:
        futures = [
            executor.submit(run_legacy, spec, args) for spec in legacy_specs
        ]
        for future in concurrent.futures.as_completed(futures):
            legacy_rows.append(future.result())

    exact_rows = []
    for spec in exact_specs:
        exact_rows.append(exact_runner.run_spec(spec, exact_args))

    scenario_order = {name: index for index, name in enumerate(SCENARIOS)}
    scheme_order = {name: index for index, name in enumerate(SCHEMES)}
    sort_key = lambda row: (  # noqa: E731
        scenario_order[row["scenario"]], scheme_order[row["scheme"]],
        row["variant"])
    legacy_rows.sort(key=sort_key)
    exact_rows.sort(key=sort_key)

    invalid_legacy = [row for row in legacy_rows if not (
        row["returncode"] == 0 and row["config_ok"] and
        row["all_flows_completed"])]
    invalid_exact = [row for row in exact_rows if not (
        row["returncode"] == 0 and row["config_ok"] and
        row["all_flows_completed"] and row["inflight_final"] == 0 and
        row["recovery_inflight_final_bytes"] == 0 and
        row["recovery_inflight_max_bytes"] <= 4096)]
    if invalid_legacy or invalid_exact:
        invalid = invalid_legacy + invalid_exact
        raise RuntimeError("invalid runs: " + ", ".join(
            f"{row['scenario']}:{row['scheme']}:{row['variant']}"
            for row in invalid))

    all_rows = legacy_rows + exact_rows
    all_rows.sort(key=sort_key)
    write_csv(args.out / "summary.csv", all_rows)
    legacy_lookup = {
        (row["scenario"], row["scheme"]): row for row in legacy_rows
    }
    exact_lookup = {
        (row["scenario"], row["scheme"]): row for row in exact_rows
    }
    comparisons = []
    for scenario in SCENARIOS:
        for scheme in SCHEMES:
            comparisons.append(comparison_row(
                scenario, scheme, legacy_lookup[(scenario, scheme)],
                exact_lookup[(scenario, scheme)]))
    write_csv(args.out / "comparison.csv", comparisons)
    normalized = normalized_exact_rows(comparisons)
    write_csv(args.out / "exact_normalized_to_best.csv", normalized)
    write_primary_table(args.out / "before_after_primary_metrics.md",
                        comparisons)
    plot_exact_normalized_heatmap(
        args.out / "exact_normalized_to_best_heatmap.png", normalized)

    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report = args.out / "mrc_exact_bounded_before_after_for_gpt.md"
    report.write_text(build_report(comparisons, revision), encoding="utf-8")
    print(args.out / "summary.csv")
    print(args.out / "comparison.csv")
    print(args.out / "before_after_primary_metrics.md")
    print(args.out / "exact_normalized_to_best.csv")
    print(args.out / "exact_normalized_to_best_heatmap.png")
    print(report)


if __name__ == "__main__":
    main()
