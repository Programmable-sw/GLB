#!/usr/bin/env python3
import csv
import math
import os
import random
import re
import shutil
import statistics
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "sim" / "datacenter" / "htsim_roce"
OUT = Path(__file__).resolve().parent / "output" / "scenario_compare"

TOPOLOGIES = os.environ.get("SCENARIO_TOPOLOGIES", "1024:3,128:2")
FLOW_SIZE_MIBS = os.environ.get("SCENARIO_FLOW_SIZE_MIBS", "4,8,16,32")
TRAFFIC = os.environ.get("SCENARIO_TRAFFIC", "tornado")
LINKSPEED_MBPS = int(os.environ.get("SCENARIO_LINKSPEED_MBPS", "400000"))
QUEUE_PKTS = int(os.environ.get("SCENARIO_QUEUE_PKTS", "100"))
MTU = int(os.environ.get("SCENARIO_MTU", "4096"))
SEED = int(os.environ.get("SCENARIO_SEED", "13"))
END_US = int(os.environ.get("SCENARIO_END_US", "10000"))
CC_MODE = os.environ.get("SCENARIO_CC", "dcqcn_variant")

FINISH_RE = re.compile(r"Flow Roce_(\d+)_(\d+) \d+ finished at ([0-9.]+)")
KEEP_RAW = os.environ.get("KEEP_RAW_OUTPUT") == "1"

VARIANTS = [
    ("ecmp", "ecmp", []),
    ("ops", "ops", []),
    ("reps", "reps", []),
    ("dtor", "dtor", ["-dtor_state_mode", "2bit-ecn01", "-dtor_unknown_reopen"]),
]

VARIANT_DISPLAY = {
    "ecmp": "ECMP",
    "ops": "OPS",
    "reps": "REPS",
    "dtor": "N-MRC",
}


def pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, int(round((len(values) - 1) * p)))
    return values[idx]


def validate_generated_fattree(nodes, tiers):
    generated = 0
    k = 0
    if tiers == 3:
        while generated < nodes:
            k += 1
            generated = k * k * k // 4
    elif tiers == 2:
        while generated < nodes:
            k += 1
            generated = k * k // 2
    else:
        raise ValueError(f"unsupported tiers={tiers}")

    if generated != nodes:
        raise ValueError(f"{nodes} nodes is not supported by the generated {tiers}-tier fat-tree")
    return k


def default_total_tor_uplinks(nodes, tiers):
    validate_generated_fattree(nodes, tiers)
    return nodes


def flow(src, dst, size, start_us=0.0, role="foreground"):
    return {
        "src": src,
        "dst": dst,
        "size": size,
        "start_us": start_us,
        "start_ps": int(start_us * 1_000_000),
        "role": role,
    }


def tornado_flows(nodes, size):
    shift = nodes // 2
    return [flow(src, (src + shift) % nodes, size) for src in range(nodes)]


def permutation_flows(nodes, conns, size, seed):
    rng = random.Random(seed)
    srcs = list(range(nodes))
    dsts = list(range(nodes))
    rng.shuffle(srcs)
    rng.shuffle(dsts)
    for i in range(nodes):
        if srcs[i] == dsts[i]:
            j = (i + 1) % nodes
            dsts[i], dsts[j] = dsts[j], dsts[i]
    return [flow(srcs[i], dsts[i], size) for i in range(conns)]


def parse_topologies(raw):
    topologies = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        nodes, tiers = item.split(":", 1)
        topologies.append((int(nodes), int(tiers)))
    return topologies


def parse_flow_sizes(raw):
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def build_flow_builder():
    if TRAFFIC == "tornado":
        return lambda scenario: tornado_flows(scenario["nodes"], scenario["flow_size"])
    if TRAFFIC == "permutation":
        return lambda scenario: permutation_flows(
            scenario["nodes"], scenario["nodes"], scenario["flow_size"], SEED
        )
    raise ValueError(f"unsupported SCENARIO_TRAFFIC={TRAFFIC}")


def build_scenarios():
    scenarios = []
    flow_builder = build_flow_builder()
    for nodes, tiers in parse_topologies(TOPOLOGIES):
        validate_generated_fattree(nodes, tiers)
        paths = int(os.environ.get("SCENARIO_PATHS", str(nodes)))
        for size_mib in parse_flow_sizes(FLOW_SIZE_MIBS):
            flow_size = size_mib * 1024 * 1024
            base = {
                "nodes": nodes,
                "tiers": tiers,
                "paths": paths,
                "flow_size": flow_size,
                "flow_size_mib": size_mib,
                "traffic": TRAFFIC,
                "flow_builder": flow_builder,
                "end_us": END_US,
            }
            scenarios.append({
                **base,
                "name": f"healthy_{nodes}n_{tiers}tier_{size_mib}m",
                "category": "健康网络场景",
                "description": (
                    f"{nodes} nodes, {tiers}-tier fat-tree, {TRAFFIC}, "
                    f"{size_mib}MiB flows, all links 400Gbps."
                ),
            })
            scenarios.append({
                **base,
                "name": f"asym_tor3pct_{nodes}n_{tiers}tier_{size_mib}m",
                "category": "非对称带宽场景",
                "description": (
                    f"{nodes} nodes, {tiers}-tier fat-tree, {TRAFFIC}, "
                    f"{size_mib}MiB flows, 3% ToR uplinks at half bandwidth."
                ),
                "slow_tor_uplink_fraction": 0.03,
                "slow_tor_uplink_divisor": 2,
            })
    return scenarios


SCENARIOS = build_scenarios()


def write_tm(path, nodes, flows):
    with path.open("w") as fh:
        print("Nodes", nodes, file=fh)
        print("Connections", len(flows), file=fh)
        for flow_id, item in enumerate(flows, 1):
            print(
                f"{item['src']}->{item['dst']} id {flow_id} "
                f"start {item['start_ps']} size {item['size']}",
                file=fh,
            )


def metrics_for_rows(rows, total):
    fcts = [row["fct_us"] for row in rows]
    completion_pct = 100.0 * len(rows) / total if total else 0.0
    cct = 0.0
    if rows:
        cct = max(row["finish_us"] for row in rows) - min(row["start_us"] for row in rows)
    return {
        "completed": len(rows),
        "completion_pct": completion_pct,
        "avg_fct_us": statistics.mean(fcts) if fcts else 0.0,
        "p50_fct_us": pct(fcts, 0.50),
        "p95_fct_us": pct(fcts, 0.95),
        "p99_fct_us": pct(fcts, 0.99),
        "p999_fct_us": pct(fcts, 0.999),
        "max_fct_us": max(fcts) if fcts else 0.0,
        "cct_us": cct,
    }


def prefixed(prefix, metrics):
    return {f"{prefix}_{key}": value for key, value in metrics.items()}


def parse_stdout(stdout_file, flow_map):
    rows = []
    for line in stdout_file.read_text(errors="ignore").splitlines():
        match = FINISH_RE.search(line)
        if not match:
            continue
        src = int(match.group(1))
        dst = int(match.group(2))
        finish_us = float(match.group(3))
        meta = flow_map.get((src, dst))
        if not meta:
            continue
        rows.append(
            {
                "role": meta["role"],
                "src": src,
                "dst": dst,
                "start_us": meta["start_us"],
                "finish_us": finish_us,
                "fct_us": finish_us - meta["start_us"],
            }
        )
    return rows


def slow_tor_uplinks_for(scenario):
    total = default_total_tor_uplinks(scenario["nodes"], scenario["tiers"])
    fraction = scenario.get("slow_tor_uplink_fraction", 0.0)
    return total, max(1, math.ceil(total * fraction)) if fraction else 0


def command_for(scenario, variant, tm, dat_file, flow_count, slow_tor_uplinks):
    _label, lb_mode, extra = variant
    cmd = [
        str(SIM),
        "-o",
        str(dat_file),
        "-tm",
        str(tm),
        "-nodes",
        str(scenario["nodes"]),
        "-conns",
        str(flow_count),
        "-tiers",
        str(scenario["tiers"]),
        "-linkspeed",
        str(LINKSPEED_MBPS),
        "-queue_type",
        "lossless_input_ecn",
        "-host_queue_type",
        "prio",
        "-q",
        str(QUEUE_PKTS),
        "-mtu",
        str(MTU),
        "-end",
        str(scenario["end_us"]),
        "-paths",
        str(scenario["paths"]),
        "-seed",
        str(SEED),
        "-lb",
        lb_mode,
        "-cc",
        CC_MODE,
        "-hop_latency",
        "0.5",
        "-switch_latency",
        "0.5",
        "-pfc_thresholds",
        "20",
        "80",
    ]
    if slow_tor_uplinks:
        cmd += [
            "-slow_tor_uplinks",
            str(slow_tor_uplinks),
            "-slow_tor_uplink_divisor",
            str(scenario["slow_tor_uplink_divisor"]),
        ]
    return cmd + extra


def run_scenario(scenario):
    flows = scenario["flow_builder"](scenario)
    role_totals = {
        "all": len(flows),
        "foreground": sum(1 for item in flows if item["role"] == "foreground"),
        "background": sum(1 for item in flows if item["role"] == "background"),
    }
    total_tor_uplinks, slow_tor_uplinks = slow_tor_uplinks_for(scenario)

    scenario_dir = OUT / scenario["name"]
    if scenario_dir.exists() and not KEEP_RAW:
        shutil.rmtree(scenario_dir)
    scenario_dir.mkdir(parents=True, exist_ok=True)
    tm = scenario_dir / f"{scenario['name']}.cm"
    write_tm(tm, scenario["nodes"], flows)
    flow_map = {(item["src"], item["dst"]): item for item in flows}

    summary_rows = []
    per_flow_rows = []
    for variant in VARIANTS:
        label = variant[0]
        stdout_file = scenario_dir / f"{label}.stdout"
        dat_file = scenario_dir / f"{label}.dat"
        cmd = command_for(scenario, variant, tm, dat_file, len(flows), slow_tor_uplinks)
        (scenario_dir / f"{label}.cmd").write_text(" ".join(cmd) + "\n")

        with stdout_file.open("w") as fh:
            subprocess.run(cmd, cwd=scenario_dir, stdout=fh, stderr=subprocess.STDOUT, check=True)

        parsed = parse_stdout(stdout_file, flow_map)
        for row in parsed:
            per_flow_rows.append({"scenario": scenario["name"], "variant": label, **row})

        foreground = [row for row in parsed if row["role"] == "foreground"]
        background = [row for row in parsed if row["role"] == "background"]
        summary = {
            "scenario": scenario["name"],
            "category": scenario["category"],
            "description": scenario["description"],
            "variant": label,
            "nodes": scenario["nodes"],
            "tiers": scenario["tiers"],
            "traffic": scenario["traffic"],
            "flow_size_mib": scenario["flow_size_mib"],
            "cc": CC_MODE,
            "total_flows": len(flows),
            "total_tor_uplinks": total_tor_uplinks,
            "slow_tor_uplinks": slow_tor_uplinks,
            "slow_tor_uplink_fraction": slow_tor_uplinks / total_tor_uplinks if total_tor_uplinks else 0.0,
            "slow_tor_uplink_divisor": scenario.get("slow_tor_uplink_divisor", 1),
            **prefixed("all", metrics_for_rows(parsed, role_totals["all"])),
            **prefixed("fg", metrics_for_rows(foreground, role_totals["foreground"])),
            **prefixed("bg", metrics_for_rows(background, role_totals["background"])),
        }
        summary_rows.append(summary)
        print(
            "{scenario:32s} {variant:5s} done={all_completed:4d}/{total_flows:<4d} "
            "avg={all_avg_fct_us:8.3f} p99={all_p99_fct_us:8.3f} "
            "p99.9={all_p999_fct_us:8.3f} cct={all_cct_us:8.3f}".format(**summary),
            flush=True,
        )

    if not KEEP_RAW:
        shutil.rmtree(scenario_dir)

    return summary_rows, per_flow_rows


def build_normalized(summary_rows):
    rows = []
    by_scenario = {}
    metrics = [
        "all_avg_fct_us",
        "all_p99_fct_us",
        "all_p999_fct_us",
        "all_max_fct_us",
        "all_cct_us",
        "fg_p99_fct_us",
        "fg_cct_us",
    ]
    for row in summary_rows:
        by_scenario.setdefault(row["scenario"], []).append(row)

    for scenario, scenario_rows in by_scenario.items():
        best = {}
        for metric in metrics:
            values = [row[metric] for row in scenario_rows if row[metric] > 0.0]
            best[metric] = min(values) if values else 0.0
        for row in scenario_rows:
            out = {
                "scenario": scenario,
                "category": row["category"],
                "variant": row["variant"],
                "completed": row["all_completed"],
                "completion_pct": row["all_completion_pct"],
            }
            for metric in metrics:
                denom = best[metric]
                out[f"norm_{metric}"] = row[metric] / denom if denom else 0.0
            rows.append(out)
    return rows


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def best_variant(rows, scenario, metric):
    candidates = [row for row in rows if row["scenario"] == scenario and row[metric] > 0.0]
    if not candidates:
        return ""
    best = min(candidates, key=lambda row: row[metric])
    return f"{VARIANT_DISPLAY.get(best['variant'], best['variant'])} ({best[metric]:.3f} us)"


def write_plan(path):
    lines = [
        "# Scenario Plan",
        "",
        "| scenario | category | topology | flow size | traffic | description |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for scenario in SCENARIOS:
        lines.append(
            "| {name} | {category} | {nodes}n/{tiers}-tier | {size}MiB | {traffic} | {description} |".format(
                name=scenario["name"],
                category=scenario["category"],
                nodes=scenario["nodes"],
                tiers=scenario["tiers"],
                size=scenario["flow_size_mib"],
                traffic=scenario["traffic"],
                description=scenario["description"],
            )
        )
    path.write_text("\n".join(lines) + "\n")


def write_report(summary_rows, path):
    scenarios = []
    seen = set()
    for row in summary_rows:
        if row["scenario"] not in seen:
            seen.add(row["scenario"])
            scenarios.append((row["scenario"], row["category"], row["description"]))

    lines = [
        "# OPS / REPS / N-MRC Scenario Comparison",
        "",
        "当前脚本覆盖健康网络和非对称带宽两类主线场景；背景流/热点、链路故障场景暂不展开。",
        "",
        "## Best Variant By Scenario",
        "",
        "| scenario | category | best avg FCT | best p99 FCT | best p99.9 FCT | best CCT |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for scenario, category, _description in scenarios:
        lines.append(
            "| {scenario} | {category} | {avg} | {p99} | {p999} | {cct} |".format(
                scenario=scenario,
                category=category,
                avg=best_variant(summary_rows, scenario, "all_avg_fct_us"),
                p99=best_variant(summary_rows, scenario, "all_p99_fct_us"),
                p999=best_variant(summary_rows, scenario, "all_p999_fct_us"),
                cct=best_variant(summary_rows, scenario, "all_cct_us"),
            )
        )

    lines += [
        "",
        "## Per-Scenario Summary",
        "",
    ]
    for scenario, _category, description in scenarios:
        lines += [
            f"### {scenario}",
            "",
            description,
            "",
            "| variant | completed | avg FCT us | p99 FCT us | p99.9 FCT us | max FCT us | CCT us |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for row in [row for row in summary_rows if row["scenario"] == scenario]:
            lines.append(
                "| {variant} | {completed}/{total} | {avg:.3f} | {p99:.3f} | {p999:.3f} | {maxf:.3f} | {cct:.3f} |".format(
                    variant=VARIANT_DISPLAY.get(row["variant"], row["variant"]),
                    completed=row["all_completed"],
                    total=row["total_flows"],
                    avg=row["all_avg_fct_us"],
                    p99=row["all_p99_fct_us"],
                    p999=row["all_p999_fct_us"],
                    maxf=row["all_max_fct_us"],
                    cct=row["all_cct_us"],
                )
            )
        lines.append("")

    path.write_text("\n".join(lines) + "\n")


def main():
    if not SIM.exists():
        raise SystemExit(f"missing simulator binary: {SIM}")

    if OUT.exists() and not KEEP_RAW:
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    write_plan(OUT / "scenario_plan.md")

    summary_rows = []
    per_flow_rows = []
    for scenario in SCENARIOS:
        rows, flows = run_scenario(scenario)
        summary_rows.extend(rows)
        per_flow_rows.extend(flows)

    normalized_rows = build_normalized(summary_rows)
    summary_csv = OUT / "summary.csv"
    normalized_csv = OUT / "normalized.csv"
    per_flow_csv = OUT / "per_flow.csv"
    report_md = OUT / "comparison_report.md"

    write_csv(summary_csv, summary_rows)
    write_csv(normalized_csv, normalized_rows)
    write_csv(per_flow_csv, per_flow_rows)
    write_report(summary_rows, report_md)

    print(summary_csv)
    print(normalized_csv)
    print(per_flow_csv)
    print(report_md)


if __name__ == "__main__":
    main()
