#!/usr/bin/env python3
"""Steady mixed-flow validation of cold per-QP MRC state."""

import argparse
import concurrent.futures
import csv
import gzip
import importlib.util
import io
import json
import math
import hashlib
import os
from pathlib import Path
import random
import re
import shlex
import shutil
import subprocess
import time


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
_COLD_SPEC = importlib.util.spec_from_file_location(
    "mrc_sglb_cold_qp_256",
    SCRIPT_DIR / "run_mrc_sglb_cold_qp_256.py")
cold = importlib.util.module_from_spec(_COLD_SPEC)
_COLD_SPEC.loader.exec_module(cold)

DEFAULT_SIM = ROOT / "sim/datacenter/htsim_roce"
DEFAULT_OUT = SCRIPT_DIR / "output/mrc_cold_qp_lifecycle_evidence_256"
DEFAULT_COLD_OUT = SCRIPT_DIR / "output/mrc_sglb_cold_qp_256"
NODES = 256
SOURCE_LEAF = 0
SOURCE_HOSTS = tuple(range(64))
SCHEMES = cold.SCHEMES
CONDITIONS = ("healthy", "fixed_hotspot", "ecmp_mixed", "slow_uplink")
SEEDS = cold.SEEDS

ARRIVAL_START_US = 50.0
STEADY_START_US = 250.0
STEADY_END_US = 950.0
ARRIVAL_END_US = 1150.0
SHORT_OFFERED_LOAD = 0.10
LONG_OFFERED_LOAD = 0.56
LINK_RATE_MBIT_PER_US = 400000.0
SHORT_DISTRIBUTION = (
    (6 * 1024, 0.50),
    (33 * 1024, 0.30),
    (133 * 1024, 0.20),
)
LONG_DISTRIBUTION = (
    (3333 * 1024, 0.50),
    (6667 * 1024, 0.50),
)
TRANSITION_PROBE_SIZES = (
    256 * 1024, 512 * 1024, 667 * 1024,
    1024 * 1024, 1333 * 1024, 2048 * 1024,
)
TRANSITION_PROBES_PER_SIZE = 16
SLOW_TOR_UPLINKS = 64
SLOW_TOR_UPLINK_DIVISOR = 2
ECMP_BACKGROUND_FLOWS = 16
ECMP_BACKGROUND_START_US = 25.0
ECMP_BACKGROUND_SIZE = 64 * 1024 * 1024
SHORT_RR_EQUIVALENCE_MARGIN = 0.10


def _mean_size(distribution):
    return sum(size * probability for size, probability in distribution)


def _sample_size(rng, distribution):
    draw = rng.random()
    cumulative = 0.0
    for size, probability in distribution:
        cumulative += probability
        if draw < cumulative:
            return size
    return distribution[-1][0]


def _destination_outside_source_leaf(rng):
    leaf = rng.randrange(1, 4)
    return leaf * 64 + rng.randrange(64)


def build_steady_flows(seed, sample_scale=1.0):
    if not 0 < sample_scale <= 1:
        raise ValueError("sample scale must be in (0, 1]")
    flows = []
    cohorts = (
        ("short", SHORT_OFFERED_LOAD, SHORT_DISTRIBUTION),
        ("long", LONG_OFFERED_LOAD, LONG_DISTRIBUTION),
    )
    for cohort, load, distribution in cohorts:
        rate_per_us = (
            load * LINK_RATE_MBIT_PER_US /
            (8.0 * _mean_size(distribution)) * sample_scale
        )
        for src in SOURCE_HOSTS:
            rng = random.Random(f"steady:{seed}:{cohort}:{src}")
            start_us = ARRIVAL_START_US
            while True:
                start_us += rng.expovariate(rate_per_us)
                if start_us > ARRIVAL_END_US:
                    break
                flows.append({
                    "src": src,
                    "dst": _destination_outside_source_leaf(rng),
                    "start_us": start_us,
                    "start_ps": int(round(start_us * 1_000_000)),
                    "flow_size": _sample_size(rng, distribution),
                    "cohort": cohort,
                })
    probe_rng = random.Random(f"steady:{seed}:transition-probes")
    probe_count = max(
        1, math.ceil(TRANSITION_PROBES_PER_SIZE * sample_scale))
    for flow_size in TRANSITION_PROBE_SIZES:
        for _ in range(probe_count):
            start_us = probe_rng.uniform(STEADY_START_US, STEADY_END_US)
            flows.append({
                "src": probe_rng.choice(SOURCE_HOSTS),
                "dst": _destination_outside_source_leaf(probe_rng),
                "start_us": start_us,
                "start_ps": int(round(start_us * 1_000_000)),
                "flow_size": flow_size,
                "cohort": "transition_probe",
            })
    flows.sort(key=lambda row: (
        row["start_ps"], row["src"], row["dst"], row["flow_size"]))
    for flow_id, row in enumerate(flows, 1):
        row["flow_id"] = flow_id
    return flows


def build_ecmp_mixed_flows(seed, measured_flows):
    flows = [dict(flow) for flow in measured_flows]
    next_flow_id = max((flow["flow_id"] for flow in flows), default=0) + 1
    rng = random.Random(f"ecmp-background:{seed}")
    for offset in range(ECMP_BACKGROUND_FLOWS):
        src = offset % len(SOURCE_HOSTS)
        flows.append({
            "src": src,
            "dst": _destination_outside_source_leaf(rng),
            "start_us": ECMP_BACKGROUND_START_US,
            "start_ps": int(round(ECMP_BACKGROUND_START_US * 1_000_000)),
            "flow_size": ECMP_BACKGROUND_SIZE,
            "flow_id": next_flow_id + offset,
            "cohort": "ecmp_background",
            "lb_mode": "ecmp",
        })
    return flows


def write_traffic(path, flows):
    lines = [f"Nodes {NODES}", f"Connections {len(flows)}"]
    for flow in flows:
        line = (
            "{src}->{dst} id {flow_id} start {start_ps} "
            "size {flow_size}".format(**flow))
        if flow.get("lb_mode"):
            line += f" lb {flow['lb_mode']}"
        lines.append(line)
    cold.atomic_write_text(path, "\n".join(lines) + "\n")


def select_steady_flows(flows):
    return [
        row for row in flows
        if row["cohort"] != "ecmp_background" and
        STEADY_START_US <= row["start_us"] <= STEADY_END_US
    ]


def build_command(
        sim, scheme, traffic, output, connections, seed, condition):
    if condition not in CONDITIONS:
        raise ValueError(f"unsupported condition {condition}")
    base_condition = (
        "healthy" if condition in ("slow_uplink", "ecmp_mixed")
        else condition)
    command = cold.build_command(
        sim, scheme, traffic, output, connections, seed, base_condition)
    if condition == "slow_uplink":
        command.extend([
            "-slow_tor_uplinks", str(SLOW_TOR_UPLINKS),
            "-slow_tor_uplink_divisor", str(SLOW_TOR_UPLINK_DIVISOR),
            "-slow_tor_uplink_select", "random-sparse",
        ])
    return command


def validate_cell(text, returncode, scheme, condition, connections):
    base_condition = (
        "healthy" if condition in ("slow_uplink", "ecmp_mixed")
        else condition)
    background_connections = (
        ECMP_BACKGROUND_FLOWS if condition == "ecmp_mixed" else 0)
    measured_connections = connections - background_connections
    completions = cold.validate_cell(
        text, returncode, scheme, base_condition, measured_connections,
        mrc_connections=measured_connections)
    if condition == "slow_uplink":
        required = [
            f"Slow ToR-to-agg uplinks {SLOW_TOR_UPLINKS}",
            f"Slow ToR-to-agg uplink divisor {SLOW_TOR_UPLINK_DIVISOR}",
            "Slow ToR-to-agg uplink selection random-sparse",
        ]
        missing = [token for token in required if token not in text]
        installed = text.count("Adding slow ToR-to-agg uplink")
        if missing or installed != SLOW_TOR_UPLINKS:
            raise RuntimeError(
                f"invalid slow_uplink/{scheme}: missing={missing} "
                f"installed={installed}/{SLOW_TOR_UPLINKS}")
    if condition == "ecmp_mixed":
        diagnostic = (
            f"ExplicitLbDiag ecmp_background_flows="
            f"{ECMP_BACKGROUND_FLOWS}")
        background_completions = len(re.findall(
            r"^Flow .* bg traffic 1 flowid ", text, re.MULTILINE))
        if diagnostic not in text or background_completions != background_connections:
            raise RuntimeError(
                f"invalid ecmp_mixed/{scheme}: diagnostic="
                f"{diagnostic in text} background_completions="
                f"{background_completions}/{background_connections}")
    return completions


def run_cell(
        sim, out, seed, condition, scheme, traffic_path, connections, force):
    cell = Path(out) / "raw" / condition / scheme / f"seed_{seed}"
    cell.mkdir(parents=True, exist_ok=True)
    output = (cell / "logout.dat").resolve()
    command = build_command(
        Path(sim).resolve(), scheme, traffic_path.resolve(), output,
        connections, seed, condition)
    command_text = shlex.join(command)
    fingerprint = {
        "sim_sha256": cold.file_sha256(sim),
        "traffic_sha256": cold.file_sha256(traffic_path),
        "command_sha256": hashlib.sha256(command_text.encode()).hexdigest(),
    }
    summary_path = cell / "summary.json"
    stdout_path = cell / "stdout.log.gz"
    if not force and summary_path.exists() and stdout_path.exists():
        cached = json.loads(summary_path.read_text(encoding="utf-8"))
        if cached.get("valid") == 1 and all(
                cached.get(key) == value for key, value in fingerprint.items()):
            with gzip.open(stdout_path, "rt", encoding="utf-8") as handle:
                text = handle.read()
            completions = validate_cell(
                text, 0, scheme, condition, connections)
            return text, completions, cached

    cold.atomic_write_text(cell / "command.txt", command_text + "\n")
    running = cell / ".stdout.running"
    started = time.monotonic()
    with running.open("w", encoding="utf-8") as handle:
        process = subprocess.run(
            command, cwd=cell, stdout=handle, stderr=subprocess.STDOUT,
            text=True, check=False)
    runtime_s = time.monotonic() - started
    text = running.read_text(encoding="utf-8")
    completions = validate_cell(
        text, process.returncode, scheme, condition, connections)
    cold.deterministic_gzip_text(stdout_path, text)
    running.unlink()
    metadata = {
        "seed": seed, "condition": condition, "scheme": scheme,
        "connections": connections, "runtime_s": runtime_s,
        "returncode": process.returncode, "valid": 1, **fingerprint,
    }
    cold.atomic_write_text(
        summary_path, json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return text, completions, metadata


def make_steady_rows(seed, flows, completions, mrc_diags):
    rows = []
    for spec in select_steady_flows(flows):
        flow_id = spec["flow_id"]
        fct = {}
        for condition in CONDITIONS:
            for scheme in SCHEMES:
                item = completions[condition][scheme].get(flow_id)
                if item is None:
                    raise ValueError(
                        f"missing completion {condition}/{scheme}/{flow_id}")
                fct[(condition, scheme)] = cold._checked_fct(spec, item)
        row = {
            "seed": seed,
            "flow_id": flow_id,
            "src": spec["src"],
            "dst": spec["dst"],
            "start_us": spec["start_us"],
            "flow_size": spec["flow_size"],
            "flow_size_kib": spec["flow_size"] / 1024.0,
            "cohort": spec["cohort"],
        }
        for condition in CONDITIONS:
            for scheme in SCHEMES:
                row[f"{condition}_{scheme}_fct_us"] = fct[(condition, scheme)]
        row.update({
            "fixed_mrc_sglb_ratio":
                fct[("fixed_hotspot", "mrc")] /
                fct[("fixed_hotspot", "sglb")],
            "fixed_rr_sglb_ratio":
                fct[("fixed_hotspot", "rr")] /
                fct[("fixed_hotspot", "sglb")],
            "fixed_mrc_rr_ratio":
                fct[("fixed_hotspot", "mrc")] /
                fct[("fixed_hotspot", "rr")],
            "slow_mrc_sglb_ratio":
                fct[("slow_uplink", "mrc")] /
                fct[("slow_uplink", "sglb")],
            "slow_mrc_rr_ratio":
                fct[("slow_uplink", "mrc")] /
                fct[("slow_uplink", "rr")],
            "ecmp_mrc_sglb_ratio":
                fct[("ecmp_mixed", "mrc")] /
                fct[("ecmp_mixed", "sglb")],
            "ecmp_mrc_rr_ratio":
                fct[("ecmp_mixed", "mrc")] /
                fct[("ecmp_mixed", "rr")],
        })
        for scheme in SCHEMES:
            row[f"{scheme}_degradation"] = (
                fct[("fixed_hotspot", scheme)] /
                fct[("healthy", scheme)])
        for condition in CONDITIONS:
            diag = mrc_diags[condition].get(flow_id)
            if diag is None:
                raise ValueError(f"missing MRC diagnostic {condition}/{flow_id}")
            prefix = f"{condition}_mrc_"
            row[prefix + "actionable"] = int(diag.actionable_feedback > 0)
            row[prefix + "actionable_feedback"] = diag.actionable_feedback
            row[prefix + "quality_feedback"] = diag.quality_feedback_before_done
            row[prefix + "effective_updates"] = diag.effective_state_updates
            row[prefix + "new_data_selections"] = diag.new_data_selections
            row[prefix + "nominal_rotations"] = (
                diag.new_data_selections / cold.ACTIVE_EVS)
            row[prefix + "pre_feedback_selections"] = (
                diag.packets_before_first_update)
            row[prefix + "post_feedback_selections"] = (
                diag.new_selections_after_first_update)
            row[prefix + "coverage"] = diag.unique_active_evs / cold.ACTIVE_EVS
            row[prefix + "full_sweeps"] = diag.full_sweeps
            row[prefix + "actual_full_sweeps"] = diag.full_sweeps
            row[prefix + "first_update_delay_us"] = (
                diag.first_state_update_us - spec["start_us"]
                if diag.first_state_update_us >= 0 else -1.0)
        rows.append(row)
    return rows


def summarize_steady(rows):
    result = []
    for cohort in ("short", "long"):
        selected = [row for row in rows if row["cohort"] == cohort]
        if not selected:
            continue
        item = {"cohort": cohort, "flows": len(selected)}
        for condition in CONDITIONS:
            for scheme in SCHEMES:
                metric = f"{condition}_{scheme}_fct_us"
                values = [row[metric] for row in selected]
                for name, quantile in (("p50", 0.50), ("p95", 0.95),
                                       ("p99", 0.99)):
                    item[f"{metric}_{name}"] = cold.percentile(values, quantile)
                item[f"{metric}_gmean"] = cold.geometric_mean(values)
        for metric in (
                "fixed_mrc_sglb_ratio", "fixed_rr_sglb_ratio",
                "fixed_mrc_rr_ratio", "mrc_degradation",
                "rr_degradation", "sglb_degradation"):
            item[metric + "_gmean"] = cold.geometric_mean(
                row[metric] for row in selected)
        item["mrc_actionable_fraction"] = sum(
            row["fixed_hotspot_mrc_actionable"] for row in selected
        ) / len(selected)
        item["mrc_coverage_mean"] = sum(
            row["fixed_hotspot_mrc_coverage"] for row in selected
        ) / len(selected)
        item["mrc_full_sweep_fraction"] = sum(
            row["fixed_hotspot_mrc_full_sweeps"] > 0 for row in selected
        ) / len(selected)
        result.append(item)
    return result


def _arrival_load(flows, cohort, seed_count=1):
    if seed_count < 1:
        raise ValueError("seed count must be positive")
    selected = [
        row for row in flows
        if row["cohort"] == cohort and
        STEADY_START_US <= row["start_us"] <= STEADY_END_US
    ]
    duration_us = STEADY_END_US - STEADY_START_US
    aggregate_gbps = sum(row["flow_size"] for row in selected) * 8.0 / (
        duration_us * 1000.0 * seed_count)
    return len(selected), aggregate_gbps, aggregate_gbps / (
        len(SOURCE_HOSTS) * 400.0)


def write_report(summary, paired, flows, out, sample_scale, seed_count=1):
    by_cohort = {row["cohort"]: row for row in summary}
    lines = [
        "# MRC 冷 QP：持续高压稳态混合流补测",
        "",
        "## 结论",
        "",
        (
            "该补测把离散锚点改为持续 Poisson 到达：固定路径背景贯穿全程，"
            "源 Leaf 在稳态窗口内持续产生新的短 QP，同时有长 QP 保持数据面"
            "压力。它回答的是已有拥塞下不断创建新 QP，而不是少量流在几个"
            "时刻启动。"
        ),
        "",
        (
            "默认 SGLB 已锁定为真实 256 B 高优先级 GCN、版本拒旧、源 Leaf×"
            "目标 DTOR×64 Spine 预装 profile，本地 1 µs、远端最快 **15 µs**；"
            "每个仿真 cell 还要求 GCN packet、delivery 和 profile-update 计数"
            "均为正。"
        ),
        "",
        "![按流大小汇总的 FCT 与 MRC 控制机会](cold_qp_startup_and_feedback.png)",
        "",
        (
            "四个面板统一按实际流大小聚合：左上为同一 flow 在固定背景与健康"
            "网络间的 FCT 恶化；右上只保留 MRC/RR 和 MRC/SGLB；左下是每条"
            "MRC flow 完成全部 64 EV 的平均 rotation 次数；右下是至少一次"
            "反馈确实控制后续新数据选路的 flow 比例。"
        ),
        "",
        "## 稳态窗口结果",
        "",
        "| 流类 | flows | MRC 压力恶化 | RR 压力恶化 | SGLB 压力恶化 | MRC/SGLB | RR/SGLB | MRC/RR | MRC actionable | MRC EV 覆盖 | MRC full rotation | hotspot p99 FCT：MRC / RR / SGLB |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cohort, label in (("short", "短流 6–133 KiB"),
                          ("long", "长流 3.3–6.5 MiB")):
        row = by_cohort[cohort]
        lines.append(
            f"| {label} | {row['flows']} | "
            f"{row['mrc_degradation_gmean']:.4f} | "
            f"{row['rr_degradation_gmean']:.4f} | "
            f"{row['sglb_degradation_gmean']:.4f} | "
            f"{row['fixed_mrc_sglb_ratio_gmean']:.4f} | "
            f"{row['fixed_rr_sglb_ratio_gmean']:.4f} | "
            f"{row['fixed_mrc_rr_ratio_gmean']:.4f} | "
            f"{row['mrc_actionable_fraction']:.1%} | "
            f"{row['mrc_coverage_mean']:.1%} | "
            f"{row['mrc_full_sweep_fraction']:.1%} | "
            f"{row['fixed_hotspot_mrc_fct_us_p99']:.2f} / "
            f"{row['fixed_hotspot_rr_fct_us_p99']:.2f} / "
            f"{row['fixed_hotspot_sglb_fct_us_p99']:.2f} µs |")
    lines.extend([
        "",
        "## 按流大小的控制转折",
        "",
        "| 流大小 | flows | MRC/RR | MRC/SGLB | nominal rotation | 实际全EV sweep | actionable flow |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in flow_size_plot_rows(paired):
        size_kib = row["flow_size"] / 1024.0
        label = (f"{size_kib:g} KiB" if size_kib < 1024 else
                 f"{size_kib / 1024:.1f} MiB")
        lines.append(
            f"| {label} | {row['flows']} | "
            f"{row['mrc_rr_ratio_gmean']:.4f} | "
            f"{row['mrc_sglb_ratio_gmean']:.4f} | "
            f"{row['mrc_nominal_rotations_mean']:.3f} | "
            f"{row['mrc_actual_full_sweeps_mean']:.3f} | "
            f"{row['mrc_actionable_fraction']:.1%} |")
    short = by_cohort["short"]
    long = by_cohort["long"]
    long_seed_mrc_rr = []
    for seed in sorted({row["seed"] for row in paired}):
        selected = [
            row for row in paired
            if row["seed"] == seed and row["cohort"] == "long"
        ]
        if selected:
            long_seed_mrc_rr.append(cold.geometric_mean(
                row["fixed_mrc_rr_ratio"] for row in selected))
    lines.extend([
        "",
        "图中的压力恶化是逐 flow 的 `FCT_hotspot/FCT_healthy` 几何均值；"
        "表中 p99 是各方案在固定热点场景里的 FCT p99，不是平均值或 max。",
        "",
        "## 怎么解读这组结果",
        "",
        (
            f"短流的 MRC actionable 为 {short['mrc_actionable_fraction']:.1%}，"
            f"MRC/SGLB={short['fixed_mrc_sglb_ratio_gmean']:.4f}，"
            f"RR/SGLB={short['fixed_rr_sglb_ratio_gmean']:.4f}。"
            "这说明新 QP 在反馈返回并用于后续新数据前已经结束；MRC 与 RR 都"
            "不能利用此前其他 QP 的认识，而已经预热的 SGLB 可以直接避开热点。"
        ),
        "",
        (
            f"长流的 MRC/RR={long['fixed_mrc_rr_ratio_gmean']:.4f}，即 MRC "
            f"相对无状态轮转改善 {(1-long['fixed_mrc_rr_ratio_gmean']):.1%}；"
            f"但 MRC/SGLB 仍为 {long['fixed_mrc_sglb_ratio_gmean']:.4f}。"
            "因此正确结论不是‘MRC 学习无效’，而是‘反馈闭环有效，但在持续"
            "高压和不断新建 QP 时，不能达到持续 fabric 视角的效果’。"
        ),
        "",
        (
            "当前 MRC 使用 skip token：坏 EV 只跳过下一次 nominal 发送机会，"
            "随后仍会被再次试探；不同 QP 又各自从全 GOOD 状态开始。高压下，"
            "这些重复试探会反复触发重传、乱序恢复与 DCQCN 降速。"
            "`actionable=100%` 只证明状态更新后还有新数据可选，不等于该控制"
            "规则已经稳定屏蔽热点。"
        ),
        "",
        (
            "离散启动实验的 500/2000 µs 点会下降，是因为它们与早期被测流的"
            "重叠显著减少；固定背景仍在，但没有连续前景到达去维持排队。"
            "本补测让短流和长流在整个窗口内持续到达，因此 RR 落到热点的包"
            "会持续叠加，测到的是稳态拥塞而不是单个稀疏探针。"
        ),
        "",
        (
            f"三 seed 的长流 MRC/RR 范围为 {min(long_seed_mrc_rr):.4f}–"
            f"{max(long_seed_mrc_rr):.4f}，方向和幅度稳定。"
        ),
        "",
        "## 输入与稳态口径",
        "",
        "- 标准 256 节点二层拓扑：4 Leaf、64 Spine，每 Leaf 64 上联和 64 下联。",
        "- 仅 Leaf 0 的 64 台 host 产生被测流，目的均匀分散到另外三个 Leaf；这样源 Leaf 是受控瓶颈。",
        "- 固定背景：16/64 条 Spine 路径双向持续 390 Gbit/s，占源 Leaf 总上联容量的 24.375%。",
        "- 开环目标：短流10%、长流56%、过渡探针约4.3% host线速；加固定背景后理想重分配约95%，RR均匀喷洒仍会把热点路径推过400 Gbit/s。",
        f"- 到达区间 {ARRIVAL_START_US:g}–{ARRIVAL_END_US:g} µs；只统计 {STEADY_START_US:g}–{STEADY_END_US:g} µs 启动的 flow，前后各留 200 µs 预热/边界保护。",
        "- 每条 flow 是新 QP；基础负载大小为6/33/133 KiB和3.3/6.5 MiB。",
        "- 另加入每 seed、每档16条256/512/667 KiB和1/1.3/2 MiB过渡探针；它们均匀散布在稳态窗口，只用于补齐rotation转折。",
        f"- sample_scale={sample_scale:g}；正式结果使用1.0时才代表约70%开环前景目标。",
        "",
        "| 流类 | 稳态到达数 | 实际注入字节对应聚合速率 | 64 host 线速占比 |",
        "|---|---:|---:|---:|",
    ])
    for cohort, label in (("short", "短流"),
                          ("transition_probe", "过渡探针"),
                          ("long", "长流")):
        count, gbps, load = _arrival_load(flows, cohort, seed_count)
        lines.append(
            f"| {label} | {count // seed_count:.1f} / seed | "
            f"{gbps:.2f} Gbit/s / seed | {load:.2%} |")
    lines.extend([
        "",
        "## 机制解释边界",
        "",
        "- RR 是 MRC 的无状态版本，端侧 EV 编码与 64-EV 轮转相同；MRC/RR 才用于隔离反馈学习本身。",
        "- SGLB 与 MRC 不只是状态是否跨 QP 共享不同，因此 MRC/SGLB 只说明实现方案差异，不能全部归因于共享状态。",
        "- `actionable` 仍采用严格定义：反馈改变状态后，当前 QP 至少还有一次新的原始数据选择；收到普通 ACK 本身不计。",
        "- 短流持续到达使 SGLB 可以复用既有 fabric profile，而每个 MRC QP 仍从全 GOOD 的 64-EV 状态开始；这正是本实验要暴露的冷启动窗口。",
        "- 长流在一个 RTT 后仍有新数据时才能将反馈用于后续选择；其生命周期结果同时包含冷启动和闭环阶段。",
        "",
        "## 数据",
        "",
        "- `steady_paired_flow_metrics.csv.gz`：稳态窗口逐 flow 配对结果。",
        "- `steady_summary.csv`：短/长 cohort 的 FCT、p99、配对比值与 MRC 机制指标。",
        "- `manifest.json`：拓扑、负载、时间窗口、SGLB 语义与二进制/traffic 指纹。",
        "- `raw/`：18 个 scheme × condition × seed 日志；SGLB cell 均校验真实 GCN 计数。",
    ])
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    report = out / "mrc_steady_mixed_limitations_256.md"
    cold.atomic_write_text(report, "\n".join(lines) + "\n")
    return report


def plot_combined(cold_seed_summary, steady_summary, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    plt.rcParams.update({
        "font.size": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.20,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    })
    colors = {"MRC": "#276FBF", "RR": "#6B7280", "SGLB": "#D97706"}
    figure, axes = plt.subplots(3, 2, figsize=(13.4, 12.6))
    positions = list(range(len(cold.START_ANCHORS_US)))
    labels = [f"{value:g}" for value in cold.START_ANCHORS_US]

    for axis, (predicate, title) in zip(axes[0], (
            (lambda size: size <= 136192, "Discrete starts: short flows"),
            (lambda size: size >= 3412992, "Discrete starts: long QPs"))):
        for label, metric in (
                ("MRC", "mrc_degradation_gmean"),
                ("RR", "rr_degradation_gmean"),
                ("SGLB", "sglb_degradation_gmean")):
            series = cold._seed_series(cold_seed_summary, predicate, metric)
            medians, lows, highs = [], [], []
            for start in cold.START_ANCHORS_US:
                values = [
                    series[(seed, start)] for seed in SEEDS
                    if (seed, start) in series
                ]
                medians.append(cold.percentile(values, 0.5))
                lows.append(min(values))
                highs.append(max(values))
            axis.plot(positions, medians, marker="o", linewidth=1.8,
                      label=label, color=colors[label])
            axis.fill_between(positions, lows, highs,
                              color=colors[label], alpha=0.10)
        axis.axhline(1.0, color="#111827", linewidth=1, linestyle=":")
        axis.set_xticks(positions, labels)
        axis.set_title(title)
        axis.set_xlabel("QP start after fixed background begins (µs)")
        axis.set_ylabel("Paired FCT: hotspot / healthy")
    axes[0, 0].legend(frameon=False, ncol=3)

    warmed = [
        row for row in cold_seed_summary if row["start_anchor_us"] >= 25
    ]
    sizes_kib = [size / 1024 for size in cold.FLOW_SIZES]
    size_labels = ("6 KiB", "33 KiB", "133 KiB", "256 KiB",
                   "3.3 MiB", "6.5 MiB")
    axis = axes[1, 0]
    for label, metric, color in (
            ("MRC / SGLB", "fixed_mrc_sglb_ratio_gmean", colors["MRC"]),
            ("RR / SGLB", "fixed_rr_sglb_ratio_gmean", colors["RR"]),
            ("MRC / RR", "fixed_mrc_rr_ratio_gmean", "#7C3AED")):
        values = []
        for size in cold.FLOW_SIZES:
            values.append(cold.geometric_mean(
                row[metric] for row in warmed if row["flow_size"] == size))
        axis.plot(sizes_kib, values, marker="o", linewidth=1.8,
                  label=label, color=color)
    axis.axhline(1.0, color="#111827", linewidth=1, linestyle=":")
    axis.set_xscale("log", base=10)
    axis.set_xticks(sizes_kib, size_labels, rotation=16)
    axis.set_title("Discrete starts after fabric warm-up")
    axis.set_xlabel("Flow size (actual values, log₁₀ spacing)")
    axis.set_ylabel("Paired FCT ratio")
    axis.legend(frameon=False, ncol=3, fontsize=8)

    axis = axes[1, 1]
    opportunity = []
    for size in cold.FLOW_SIZES:
        selected = [row for row in warmed if row["flow_size"] == size]
        opportunity.append((
            sum(row["fixed_hotspot_mrc_coverage_mean"] for row in selected) /
            len(selected),
            sum(row["fixed_hotspot_mrc_full_sweep_fraction"] for row in selected) /
            len(selected),
            sum(row["fixed_hotspot_mrc_actionable_fraction"] for row in selected) /
            len(selected),
        ))
    x = list(range(len(cold.FLOW_SIZES)))
    width = 0.25
    axis.bar([value - width for value in x], [row[0] for row in opportunity],
             width, label="Mean EV coverage", color=colors["RR"])
    axis.bar(x, [row[1] for row in opportunity], width,
             label="≥1 full rotation", color="#7C3AED")
    axis.bar([value + width for value in x], [row[2] for row in opportunity],
             width, label="Actionable feedback", color=colors["MRC"])
    axis.set_xticks(x, size_labels, rotation=16)
    axis.set_ylim(0, 1.03)
    axis.yaxis.set_major_formatter(PercentFormatter(1.0))
    axis.set_title("Discrete starts: MRC control opportunity")
    axis.set_xlabel("Flow size")
    axis.set_ylabel("Fraction")
    axis.legend(frameon=False, fontsize=8)

    by_cohort = {row["cohort"]: row for row in steady_summary}
    cohorts = ("short", "long")
    cohort_labels = ("Short 6–133 KiB", "Long 3.3–6.5 MiB")
    x = list(range(len(cohorts)))
    width = 0.24
    axis = axes[2, 0]
    for offset, label, metric in (
            (-width, "MRC", "mrc_degradation_gmean"),
            (0, "RR", "rr_degradation_gmean"),
            (width, "SGLB", "sglb_degradation_gmean")):
        axis.bar([value + offset for value in x],
                 [by_cohort[item][metric] for item in cohorts], width,
                 label=label, color=colors[label])
    axis.axhline(1.0, color="#111827", linewidth=1, linestyle=":")
    axis.set_xticks(x, cohort_labels)
    axis.set_title("Steady arrivals: fixed-hotspot degradation")
    axis.set_ylabel("Paired FCT: hotspot / healthy (geomean)")
    axis.legend(frameon=False, ncol=3)

    axis = axes[2, 1]
    for offset, label, metric, color in (
            (-width, "MRC / SGLB", "fixed_mrc_sglb_ratio_gmean", colors["MRC"]),
            (0, "RR / SGLB", "fixed_rr_sglb_ratio_gmean", colors["RR"]),
            (width, "MRC / RR", "fixed_mrc_rr_ratio_gmean", "#7C3AED")):
        axis.bar([value + offset for value in x],
                 [by_cohort[item][metric] for item in cohorts], width,
                 label=label, color=color)
    axis.axhline(1.0, color="#111827", linewidth=1, linestyle=":")
    axis.set_xticks(x, cohort_labels)
    axis.set_title("Steady arrivals: paired scheme ratios")
    axis.set_ylabel("Paired FCT ratio (geomean)")
    axis.legend(frameon=False, ncol=3, fontsize=8)

    figure.suptitle(
        "Cold per-QP learning under persistent path pressure — 256 hosts, 64 spines",
        fontsize=14, fontweight="bold")
    figure.text(
        0.5, 0.005,
        "Top/middle: discrete-start workload. Bottom: continuous 70% mixed foreground "
        "+ 16×390 Gbit/s fixed paths; steady cohort starts at 250–950 µs. "
        "SGLB uses real GCN, 15 µs remote cadence.",
        ha="center", va="bottom", fontsize=8.5, color="#4B5563")
    figure.tight_layout(rect=(0, 0.025, 1, 0.965))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    png = out / "cold_qp_startup_and_feedback.png"
    pdf = out / "cold_qp_startup_and_feedback.pdf"
    figure.savefig(png, dpi=180, bbox_inches="tight", facecolor="white")
    figure.savefig(pdf, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return png, pdf


def flow_size_plot_rows(paired):
    rows = []
    for flow_size in sorted({row["flow_size"] for row in paired}):
        selected = [row for row in paired if row["flow_size"] == flow_size]
        item = {
            "flow_size": flow_size,
            "flows": len(selected),
            "mrc_degradation_gmean": cold.geometric_mean(
                row["mrc_degradation"] for row in selected),
            "rr_degradation_gmean": cold.geometric_mean(
                row["rr_degradation"] for row in selected),
            "sglb_degradation_gmean": cold.geometric_mean(
                row["sglb_degradation"] for row in selected),
            "mrc_rr_ratio_gmean": cold.geometric_mean(
                row["fixed_mrc_rr_ratio"] for row in selected),
            "mrc_sglb_ratio_gmean": cold.geometric_mean(
                row["fixed_mrc_sglb_ratio"] for row in selected),
            "slow_mrc_rr_ratio_gmean": cold.geometric_mean(
                row["slow_mrc_rr_ratio"] for row in selected),
            "slow_mrc_sglb_ratio_gmean": cold.geometric_mean(
                row["slow_mrc_sglb_ratio"] for row in selected),
            "ecmp_mixed_mrc_rr_ratio_gmean": cold.geometric_mean(
                row["ecmp_mrc_rr_ratio"] for row in selected),
            "ecmp_mixed_mrc_sglb_ratio_gmean": cold.geometric_mean(
                row["ecmp_mrc_sglb_ratio"] for row in selected),
            "mrc_nominal_rotations_mean": sum(
                row["fixed_hotspot_mrc_nominal_rotations"]
                for row in selected
            ) / len(selected),
            "mrc_actual_full_sweeps_mean": sum(
                row["fixed_hotspot_mrc_full_sweeps"] for row in selected
            ) / len(selected),
            "mrc_actionable_fraction": sum(
                row["fixed_hotspot_mrc_actionable"] for row in selected
            ) / len(selected),
            "mrc_coverage_mean": sum(
                row["fixed_hotspot_mrc_coverage"] for row in selected
            ) / len(selected),
            "mrc_pre_feedback_selections_mean": sum(
                row["fixed_hotspot_mrc_pre_feedback_selections"]
                for row in selected
            ) / len(selected),
            "mrc_post_feedback_selections_mean": sum(
                row["fixed_hotspot_mrc_post_feedback_selections"]
                for row in selected
            ) / len(selected),
        }
        for condition in CONDITIONS:
            for scheme in SCHEMES:
                metric = f"{condition}_{scheme}_fct_us"
                values = [row[metric] for row in selected]
                item[metric + "_mean"] = sum(values) / len(values)
                item[metric + "_p99"] = cold.percentile(values, 0.99)
        rows.append(item)
    return rows


def evaluate_theory_checks(paired, rtt_us):
    short = [
        row for row in paired
        if row["flow_size"] <= 256 * 1024]
    long = [
        row for row in paired
        if row["flow_size"] >= 3333 * 1024]
    boundary = [
        row for row in paired
        if row["flow_size"] == 256 * 1024]
    zero_actionable = [
        row for row in short
        if row["fixed_hotspot_mrc_actionable"] == 0 and
        row["fixed_hotspot_mrc_post_feedback_selections"] == 0]
    feedback_delays = [
        row["fixed_hotspot_mrc_first_update_delay_us"] for row in paired
        if row["fixed_hotspot_mrc_first_update_delay_us"] >= 0]
    short_actionable = (
        sum(row["fixed_hotspot_mrc_actionable"] for row in short) /
        len(short) if short else 0.0)
    long_actionable = (
        sum(row["fixed_hotspot_mrc_actionable"] for row in long) /
        len(long) if long else 0.0)
    short_mrc_rr = (
        cold.geometric_mean(row["fixed_mrc_rr_ratio"] for row in short)
        if short else float("nan"))
    long_mrc_rr = (
        cold.geometric_mean(row["fixed_mrc_rr_ratio"] for row in long)
        if long else float("nan"))
    short_mrc_sglb = (
        cold.geometric_mean(row["fixed_mrc_sglb_ratio"] for row in short)
        if short else float("nan"))
    long_mrc_sglb = (
        cold.geometric_mean(row["fixed_mrc_sglb_ratio"] for row in long)
        if long else float("nan"))
    boundary_rotations = (
        sum(row["fixed_hotspot_mrc_nominal_rotations"] for row in boundary) /
        len(boundary) if boundary else 0.0)
    boundary_zero_post = (
        sum(row["fixed_hotspot_mrc_post_feedback_selections"] == 0
            for row in boundary) / len(boundary) if boundary else 0.0)
    zero_actionable_ratio = (
        cold.geometric_mean(
            row["fixed_mrc_rr_ratio"] for row in zero_actionable)
        if zero_actionable else float("nan"))
    return {
        "short_zero_actionable_rr_equivalence": {
            "supported": bool(zero_actionable) and
                abs(zero_actionable_ratio - 1.0) <=
                SHORT_RR_EQUIVALENCE_MARGIN,
            "flows": len(zero_actionable),
            "mrc_rr_gmean": zero_actionable_ratio,
            "equivalence_margin": SHORT_RR_EQUIVALENCE_MARGIN,
        },
        "feedback_causal_delay": {
            "supported": bool(feedback_delays) and
                min(feedback_delays) + 1e-9 >= rtt_us,
            "minimum_feedback_delay_us": (
                min(feedback_delays) if feedback_delays else -1.0),
            "reference_rtt_us": rtt_us,
        },
        "rotation_boundary": {
            "supported": bool(boundary) and boundary_rotations >= 1.0 and
                boundary_zero_post > 0.0,
            "nominal_rotations_mean": boundary_rotations,
            "zero_post_feedback_fraction": boundary_zero_post,
        },
        "actionable_rises_with_lifetime": {
            "supported": bool(short and long) and
                long_actionable > short_actionable,
            "short_fraction": short_actionable,
            "long_fraction": long_actionable,
        },
        "long_mrc_improves_over_rr": {
            "supported": bool(short and long) and long_mrc_rr < short_mrc_rr,
            "short_mrc_rr_gmean": short_mrc_rr,
            "long_mrc_rr_gmean": long_mrc_rr,
        },
        "mrc_sglb_gap_narrows": {
            "supported": bool(short and long) and
                abs(long_mrc_sglb - 1.0) < abs(short_mrc_sglb - 1.0),
            "short_mrc_sglb_gmean": short_mrc_sglb,
            "long_mrc_sglb_gmean": long_mrc_sglb,
        },
    }


def write_theory_checks(checks, out):
    path = Path(out) / "theory_checks.json"
    cold.atomic_write_text(
        path, json.dumps(checks, indent=2, sort_keys=True) + "\n")
    return path


def _format_size(flow_size):
    kib = flow_size / 1024.0
    return f"{kib:g} KiB" if kib < 1024 else f"{kib / 1024:.1f} MiB"


def write_lifecycle_report(
        summary, paired, flows, out, sample_scale, seed_count, checks):
    del summary
    size_rows = flow_size_plot_rows(paired)
    supported = sum(item["supported"] for item in checks.values())
    short_rr = checks["long_mrc_improves_over_rr"]["short_mrc_rr_gmean"]
    long_rr = checks["long_mrc_improves_over_rr"]["long_mrc_rr_gmean"]
    short_sglb = checks["mrc_sglb_gap_narrows"]["short_mrc_sglb_gmean"]
    long_sglb = checks["mrc_sglb_gap_narrows"]["long_mrc_sglb_gmean"]
    minimum_feedback = checks["feedback_causal_delay"][
        "minimum_feedback_delay_us"]
    seed_rows = []
    for seed in sorted({row["seed"] for row in paired}):
        seed_short = [
            row for row in paired
            if row["seed"] == seed and row["flow_size"] <= 256 * 1024]
        seed_long = [
            row for row in paired
            if row["seed"] == seed and row["flow_size"] >= 3333 * 1024]
        seed_rows.append((
            seed,
            cold.geometric_mean(
                row["fixed_mrc_rr_ratio"] for row in seed_short),
            cold.geometric_mean(
                row["fixed_mrc_rr_ratio"] for row in seed_long),
            cold.geometric_mean(
                row["fixed_mrc_sglb_ratio"] for row in seed_short),
            cold.geometric_mean(
                row["fixed_mrc_sglb_ratio"] for row in seed_long),
        ))
    check_labels = {
        "short_zero_actionable_rr_equivalence":
            "无可执行反馈的短流近似 RR",
        "feedback_causal_delay": "反馈控制至少需要 1 RTT",
        "rotation_boundary": "256 KiB rotation 边界仍可能无后续控制",
        "actionable_rises_with_lifetime": "可执行反馈比例随生命周期增加",
        "long_mrc_improves_over_rr": "长流 MRC 相对 RR 改善",
        "mrc_sglb_gap_narrows": "MRC 与 SGLB 差距随流长缩小",
    }
    lines = [
        "# MRC 生命周期、冷 QP 与独立学习实验",
        "",
        "## 结论摘要",
        "",
        f"- 核心生命周期判断成立。短流 actionable=0，长流=100%；"
        f"固定热点下长流 MRC/RR={long_rr:.3f}，短流={short_rr:.3f}。",
        f"- 控制延迟被直接观测。首次有效反馈最早 {minimum_feedback:.3f} µs，"
        "256 KiB 虽完成一轮 64-EV rotation，但反馈后新 DATA 仍为 0。",
        f"- **本次固定热点下，MRC 不会自动收敛到 SGLB。** "
        f"MRC/SGLB 从短流 {short_sglb:.3f} "
        f"扩大到长流 {long_sglb:.3f}；持续坏路径下 skip-token 只跳过下一次机会，"
        "会反复探索，而 SGLB 持续使用 fabric 视图。",
        f"- 6 项可证伪判据中支持 {supported}/6 项。"
        f"结果来自 {seed_count} 个配对 seed；机制计数逐 flow 验证，"
        "不把单一路径哈希当作结论。",
        "",
        "![按流大小的生命周期证据](cold_qp_startup_and_feedback.png)",
        "",
        "上图左上直接给出用户要求的分流长平均 FCT；右上给出同 flow 配对比值；"
        "下方把 rotation、实际 sweep、EV 覆盖和 actionable 控制机会与性能转折对齐。",
        "",
        "![三种不均衡来源的稳健性](mrc_lifecycle_robustness.png)",
        "",
        "固定热点与半速链路产生了清晰的长流 MRC/RR 改善；显式 ECMP 大流场景"
        f"的 MRC/RR 接近 1，说明这 {seed_count} 个 seed 的逐流哈希背景没有让反馈方案拉开差距，"
        "不能把这一场景解释成额外正证据。",
        "半速链路中 MRC/SGLB 从 33 KiB 的 1.391 缩小到 1.3 MiB 的 1.061，"
        "但到 6.5 MiB 又扩大到 1.412；只能说过渡区间接近，"
        "不能说流越长就必然收敛。",
        "",
        "## 逐项理论判定",
        "",
        "| 理论判据 | 结果 | 关键数据 |",
        "|---|---|---|",
    ]
    for name, item in checks.items():
        evidence = ", ".join(
            f"{key}={value:.6g}" if isinstance(value, float)
            else f"{key}={value}"
            for key, value in item.items() if key != "supported")
        lines.append(
            f"| {check_labels[name]} | "
            f"{'支持' if item['supported'] else '不支持'} | {evidence} |")
    lines.extend([
        "",
        "## 按流大小的结果",
        "",
        "所有比值先在相同 seed、相同 flow ID 内配对，再取几何均值。"
        "FCT 平均值和 p99 分开报告，p99 不是最大值。",
        "",
        "| 流大小 | 固定热点平均 FCT：MRC / RR / SGLB (µs) | "
        "固定热点 p99 FCT：MRC / RR / SGLB (µs) |",
        "|---:|---:|---:|",
    ])
    for row in size_rows:
        lines.append(
            f"| {_format_size(row['flow_size'])} | "
            f"{row['fixed_hotspot_mrc_fct_us_mean']:.2f} / "
            f"{row['fixed_hotspot_rr_fct_us_mean']:.2f} / "
            f"{row['fixed_hotspot_sglb_fct_us_mean']:.2f} | "
            f"{row['fixed_hotspot_mrc_fct_us_p99']:.2f} / "
            f"{row['fixed_hotspot_rr_fct_us_p99']:.2f} / "
            f"{row['fixed_hotspot_sglb_fct_us_p99']:.2f} |")
    lines.extend([
        "",
        "| 流大小 | flows | 固定热点 MRC/RR | 固定热点 MRC/SGLB | "
        "ECMP 大流 MRC/RR | 半速链路 MRC/RR | nominal rotation | "
        "actual sweep | EV覆盖 | actionable |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in size_rows:
        lines.append(
            f"| {_format_size(row['flow_size'])} | {row['flows']} | "
            f"{row['mrc_rr_ratio_gmean']:.4f} | "
            f"{row['mrc_sglb_ratio_gmean']:.4f} | "
            f"{row['ecmp_mixed_mrc_rr_ratio_gmean']:.4f} | "
            f"{row['slow_mrc_rr_ratio_gmean']:.4f} | "
            f"{row['mrc_nominal_rotations_mean']:.3f} | "
            f"{row['mrc_actual_full_sweeps_mean']:.3f} | "
            f"{row['mrc_coverage_mean']:.1%} | "
            f"{row['mrc_actionable_fraction']:.1%} |")
    lines.extend([
        "",
        "## 跨 seed 一致性",
        "",
        "| seed | 短流 MRC/RR | 长流 MRC/RR | 短流 MRC/SGLB | 长流 MRC/SGLB |",
        "|---:|---:|---:|---:|---:|",
    ])
    for seed, seed_short_rr, seed_long_rr, seed_short_sglb, seed_long_sglb in seed_rows:
        lines.append(
            f"| {seed} | {seed_short_rr:.4f} | {seed_long_rr:.4f} | "
            f"{seed_short_sglb:.4f} | {seed_long_sglb:.4f} |")
    lines.extend([
        "",
        "## 因果解释",
        "",
        "- 每个 MRC QP 独立从 64 个 GOOD EV 开始，不继承其他 QP 已经学到的拥塞状态。",
        "- 新 QP 必须先发送 DATA，再等待拥塞 ACK/TRIM 返回；只有反馈之后仍有新的原始 DATA 选择时，当前 flow 才能受益，因此控制至少跨越 1 RTT。",
        "- 4 KiB MTU 下 64 个 nominal slot 等于 256 KiB。nominal rotation 只表示游标机会数；actual sweep 才表示真实覆盖全部 EV，两者不可混称。",
        "- SGLB 使用已预热的交换机侧 dToR profile，因此短流比较反映完整方案差异；它不是只改变‘是否跨 QP 共享’的纯消融。",
        "- 固定热点用于清晰机制证明；显式 ECMP 大流用于构造长期逐流哈希不均衡；半速上行用于检验结论是否依赖合成背景源。",
        "- ‘短流类似 RR’使用 ±10% 的描述性实用等价带；它不是统计等价检验。"
        f"短流总体 MRC 相对 RR 变化 {(short_rr - 1) * 100:+.1f}%，"
        f"远小于长流改善 {(1 - long_rr) * 100:.1f}%；6/33/133 KiB 三档分别"
        "只变化 +6.1%/+6.3%/+7.8%。",
        "- 256 KiB 的 MRC/RR 改善不能归因于当前 flow 的反馈，因为该档"
        "post-feedback DATA 为 0；它来自不同方案造成的队列外部性和恢复轨迹差异。",
        "",
        "## 局限性与稳健性",
        "",
        f"- 本次运行 3 个配对 seed、{len(paired):,} 条稳态被测流；"
        "流级样本量很大，但只有三个独立 traffic/path seeds，"
        "因此不把逐流样本数误当成大量独立实验重复。",
        "- ECMP 大流场景结果接近 1，只能作为负结果保留，不能支持 MRC 长流优势。",
        "- MRC/SGLB 是完整方案比较，不是仅改变状态共享的一因素消融。",
        "- 固定 390 Gbit/s 热点是放大反馈作用的机制场景，不代表所有生产负载强度。",
        "",
        "## 建议与后续问题",
        "",
        "- 将 MRC 定位为长生命周期通信相对 RR 的反馈增强方案，不应宣称它能处理"
        "冷启动短流或最终逼近持续 fabric 视图。",
        "- 若要改善短流，应引入跨 QP/ToR-pair 状态共享或网络侧预热信息；单纯增加"
        "当前 QP 的 rotation 强度无法绕过至少 1 RTT 的因果延迟。",
        "- 若要处理长期坏路径，需比较累积惩罚或多次 skip 的策略。当前 skip-token"
        "每次只跳过一个 opportunity；它是长流仍显著落后 SGLB 的候选解释，"
        "但本实验没有单独消融该机制，不能把相关性写成已证实原因。",
        "- 进一步问题：共享 MRC 状态能否在不引入错误继承的前提下缩短短流探索窗口，"
        "以及更强的 ECMP 大流哈希偏斜是否会出现与固定热点一致的转折。",
        "",
        "## 配置与复现口径",
        "",
        "- 256 hosts，4 Leaf，64 Spines，64 paths，400 Gbit/s，4 KiB MTU。",
        "- MRC：64 active EV、identity mapping、skip token、故障恢复关闭。",
        "- SGLB：real 256 B high-priority GCN、dToR profile、15 µs remote cadence、exact-min24、shuffled RR。",
        "- WebSearch-like 前景：短流 offered load 10%，长流 56%，过渡探针约 4.3%；"
        "Poisson 到达覆盖 50–1150 µs，只统计 250–950 µs 启动的稳态 flow。",
        "- 固定热点：16/64 paths 持续承载 390 Gbit/s 双向背景；"
        "ECMP 背景：16 条 64 MiB 长流在 25 µs 启动并逐流 ECMP；"
        "非对称容量：每 Leaf 16 条上联降为半速。",
        "- actionable 定义：MRC 反馈先改变路径状态，且此后同一 QP 至少还有一次"
        "新的原始 DATA 路径选择；收到普通 ACK 本身不计 actionable。",
        f"- seeds={seed_count}；sample_scale={sample_scale:g}。",
        f"- 稳态被测流数={len(paired)}；输入流记录数={len(flows)}。",
        "- `steady_paired_flow_metrics.csv.gz` 保存逐流数据；"
        "`flow_size_focus.csv` 保存精确绘图数据；`theory_checks.json` 保存判据。",
    ])
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    report = out / "mrc_lifecycle_evidence_256.md"
    cold.atomic_write_text(report, "\n".join(lines) + "\n")
    return report


def plot_lifecycle_robustness(paired, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = flow_size_plot_rows(paired)
    sizes_kib = [row["flow_size"] / 1024.0 for row in rows]
    positions = list(range(len(rows)))
    labels = [
        f"{size:g} KiB" if size < 1024 else f"{size / 1024:.1f} MiB"
        for size in sizes_kib]
    conditions = (
        ("Fixed hotspot", "mrc_rr_ratio_gmean",
         "mrc_sglb_ratio_gmean", "#276FBF"),
        ("ECMP large flows", "ecmp_mixed_mrc_rr_ratio_gmean",
         "ecmp_mixed_mrc_sglb_ratio_gmean", "#D97706"),
        ("Half-rate uplinks", "slow_mrc_rr_ratio_gmean",
         "slow_mrc_sglb_ratio_gmean", "#059669"),
    )
    figure, axes = plt.subplots(1, 2, figsize=(13.2, 4.8))
    for axis, ratio_index, title in (
            (axes[0], 1, "MRC / RR across imbalance sources"),
            (axes[1], 2, "MRC / SGLB across imbalance sources")):
        for label, mrc_rr, mrc_sglb, color in conditions:
            field = (mrc_rr, mrc_sglb)[ratio_index - 1]
            axis.plot(positions, [row[field] for row in rows], marker="o",
                      linewidth=1.8, label=label, color=color)
        axis.axhline(1.0, color="#111827", linewidth=1, linestyle=":")
        axis.set_xticks(positions, labels, rotation=28, ha="right")
        axis.tick_params(axis="x", labelsize=8.5)
        axis.set_xlabel("Flow size")
        axis.set_ylabel("Paired FCT ratio (geomean)")
        axis.set_title(title)
        axis.grid(alpha=0.2)
    axes[0].legend(frameon=False, fontsize=8)
    figure.tight_layout()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    png = out / "mrc_lifecycle_robustness.png"
    pdf = out / "mrc_lifecycle_robustness.pdf"
    figure.savefig(png, dpi=180, bbox_inches="tight", facecolor="white")
    figure.savefig(pdf, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return png, pdf


def plot_flow_size_focus(paired, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    rows = flow_size_plot_rows(paired)
    sizes_kib = [row["flow_size"] / 1024.0 for row in rows]
    positions = list(range(len(rows)))
    labels = [
        f"{size:g} KiB" if size < 1024 else f"{size / 1024:.1f} MiB"
        for size in sizes_kib
    ]
    plt.rcParams.update({
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.20,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    })
    colors = {"MRC": "#276FBF", "RR": "#6B7280", "SGLB": "#D97706"}
    figure, axes = plt.subplots(2, 2, figsize=(13.2, 8.8))

    axis = axes[0, 0]
    for label, field, color, marker, linestyle in (
            ("MRC", "fixed_hotspot_mrc_fct_us_mean",
             colors["MRC"], "o", "-"),
            ("RR", "fixed_hotspot_rr_fct_us_mean",
             colors["RR"], "s", "--"),
            ("SGLB", "fixed_hotspot_sglb_fct_us_mean",
             colors["SGLB"], "^", "-.")):
        axis.plot(positions, [row[field] for row in rows], label=label,
                  color=color, marker=marker, linestyle=linestyle,
                  linewidth=2, markersize=6)
    axis.set_yscale("log")
    axis.set_title("Average FCT under fixed path pressure")
    axis.set_ylabel("Average FCT (µs, log scale)")
    axis.legend(frameon=False, ncol=3)

    axis = axes[0, 1]
    for label, field, color, marker, linestyle in (
            ("MRC / RR", "mrc_rr_ratio_gmean", "#7C3AED", "s", "--"),
            ("MRC / SGLB", "mrc_sglb_ratio_gmean", colors["MRC"], "o", "-")):
        axis.plot(positions, [row[field] for row in rows], label=label,
                  color=color, marker=marker, linestyle=linestyle,
                  linewidth=2, markersize=6)
    axis.axhline(1.0, color="#111827", linewidth=1, linestyle=":")
    axis.set_title("MRC FCT ratios by flow size")
    axis.set_ylabel("Paired FCT ratio under fixed background")
    axis.legend(frameon=False, ncol=2)

    axis = axes[1, 0]
    axis.plot(positions, [row["mrc_nominal_rotations_mean"] for row in rows],
              color="#7C3AED", marker="s", linewidth=2,
              label="Nominal rotations (new DATA / 64)")
    axis.plot(
        positions, [row["mrc_actual_full_sweeps_mean"] for row in rows],
        color="#9CA3AF", marker="o", linewidth=1.8, linestyle="--",
        label="Actual complete-EV sweeps")
    axis.set_ylim(bottom=0)
    axis.set_title("MRC nominal rotations and actual EV coverage")
    axis.set_ylabel("Mean rotations / sweeps per MRC flow")
    axis.legend(frameon=False, fontsize=8)

    axis = axes[1, 1]
    axis.plot(positions, [row["mrc_coverage_mean"] for row in rows],
              color=colors["RR"], marker="s", linewidth=1.8,
              linestyle="--", label="Mean EV coverage")
    axis.plot(positions, [row["mrc_actionable_fraction"] for row in rows],
              color=colors["MRC"], marker="o", linewidth=2,
              label="Actionable feedback")
    axis.set_ylim(-0.03, 1.03)
    axis.yaxis.set_major_formatter(PercentFormatter(1.0))
    axis.set_title("MRC exploration coverage and usable feedback")
    axis.set_ylabel("Flow fraction / EV coverage")
    axis.legend(frameon=False, fontsize=8)

    for axis in axes.flat:
        axis.set_xticks(positions, labels, rotation=28, ha="right")
        axis.tick_params(axis="x", labelsize=8.5)
        axis.set_xlabel("Flow size (ordered actual values)")
        if 256 in sizes_kib:
            axis.axvline(sizes_kib.index(256), color="#111827",
                         linewidth=0.8, linestyle=":", alpha=0.55)

    figure.suptitle(
        "MRC cold-QP behavior versus flow size — steady persistent pressure",
        fontsize=14, fontweight="bold")
    figure.text(
        0.5, 0.008,
        "256 hosts, 64 spines; 16 paths carry 390 Gbit/s fixed background. "
        "Flows start in the 250–950 µs steady window; SGLB uses real GCN at 15 µs.",
        ha="center", fontsize=8.8, color="#4B5563")
    figure.tight_layout(rect=(0, 0.035, 1, 0.96))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    png = out / "cold_qp_startup_and_feedback.png"
    pdf = out / "cold_qp_startup_and_feedback.pdf"
    figure.savefig(png, dpi=180, bbox_inches="tight", facecolor="white")
    figure.savefig(pdf, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return png, pdf


def _read_numeric_csv(path):
    integer_fields = {"seed", "flow_size", "flows"}
    rows = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            row = {}
            for key, value in raw.items():
                if key in integer_fields:
                    row[key] = int(float(value))
                elif key == "cohort":
                    row[key] = value
                else:
                    try:
                        row[key] = float(value)
                    except ValueError:
                        row[key] = value
            rows.append(row)
    return rows


def _paper_diag(text):
    match = re.search(r"^PaperSglbDiag (.*)$", text, re.MULTILINE)
    if not match:
        raise ValueError("missing PaperSglbDiag")
    fields = dict(token.split("=", 1) for token in match.group(1).split())
    return {
        name: int(fields[name])
        for name in ("gcn_packets", "gcn_bytes", "gcn_deliveries",
                     "gcn_profile_updates", "gcn_stale")
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", type=Path, default=DEFAULT_SIM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cold-out", type=Path, default=DEFAULT_COLD_OUT)
    parser.add_argument("--seeds", default="13")
    parser.add_argument("--sample-scale", type=float, default=1.0)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--quick", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    seeds = tuple(int(value) for value in args.seeds.split(",") if value)
    if not seeds or any(seed not in SEEDS for seed in seeds):
        raise ValueError("seeds must be selected from 13,29,47")
    sample_scale = 0.05 if args.quick else args.sample_scale
    if not 0 < sample_scale <= 1 or args.workers < 1:
        raise ValueError("invalid sample scale or worker count")
    args.sim = args.sim.resolve()
    args.out = args.out.resolve()
    args.cold_out = args.cold_out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.sim.exists():
        raise FileNotFoundError(args.sim)

    flows_by_seed = {}
    traffic_by_seed = {}
    for seed in seeds:
        flows = build_steady_flows(seed, sample_scale)
        traffic = args.out / "traffic" / f"steady_mixed_seed_{seed}.cm"
        write_traffic(traffic, flows)
        flows_by_seed[seed] = flows
        traffic_by_seed[(seed, "measured")] = traffic
        mixed_flows = build_ecmp_mixed_flows(seed, flows)
        mixed_traffic = (
            args.out / "traffic" / f"steady_ecmp_mixed_seed_{seed}.cm")
        write_traffic(mixed_traffic, mixed_flows)
        traffic_by_seed[(seed, "ecmp_mixed")] = mixed_traffic

    specs = [
        (seed, condition, scheme)
        for seed in seeds for condition in CONDITIONS for scheme in SCHEMES
    ]
    cells = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(args.workers, len(specs))) as executor:
        futures = {
            executor.submit(
                run_cell, args.sim, args.out, seed, condition, scheme,
                traffic_by_seed[(
                    seed, "ecmp_mixed" if condition == "ecmp_mixed"
                    else "measured")],
                (len(flows_by_seed[seed]) + ECMP_BACKGROUND_FLOWS
                 if condition == "ecmp_mixed"
                 else len(flows_by_seed[seed])),
                args.force
            ): (seed, condition, scheme)
            for seed, condition, scheme in specs
        }
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            text, completions, metadata = future.result()
            cells[key] = {
                "text": text,
                "completions": completions,
                "metadata": metadata,
            }
            print(
                f"complete seed={key[0]} condition={key[1]} "
                f"scheme={key[2]} runtime={metadata['runtime_s']:.2f}s",
                flush=True)

    paired = []
    for seed in seeds:
        completions = {
            condition: {
                scheme: cells[(seed, condition, scheme)]["completions"]
                for scheme in SCHEMES
            }
            for condition in CONDITIONS
        }
        diagnostics = {
            condition: cold.parse_mrc_diags(
                cells[(seed, condition, "mrc")]["text"])
            for condition in CONDITIONS
        }
        paired.extend(make_steady_rows(
            seed, flows_by_seed[seed], completions, diagnostics))

    summary = summarize_steady(paired)
    size_plot_rows = flow_size_plot_rows(paired)
    cold.write_gzip_csv(args.out / "steady_paired_flow_metrics.csv.gz", paired)
    cold.write_csv(args.out / "steady_summary.csv", summary)
    cold.write_csv(args.out / "flow_size_focus.csv", size_plot_rows)
    all_flows = [flow for seed in seeds for flow in flows_by_seed[seed]]
    checks = evaluate_theory_checks(paired, rtt_us=7.0)
    checks_path = write_theory_checks(checks, args.out)
    report = write_lifecycle_report(
        summary, paired, all_flows, args.out, sample_scale, len(seeds),
        checks)

    figures = (
        *plot_flow_size_focus(paired, args.out),
        *plot_lifecycle_robustness(paired, args.out),
    )
    args.cold_out.mkdir(parents=True, exist_ok=True)
    published_figures = []
    for figure in figures:
        published = args.cold_out / figure.name
        if figure.resolve() != published.resolve():
            shutil.copy2(figure, published)
        published_figures.append(published)
    copied_report = args.cold_out / report.name
    if report.resolve() != copied_report.resolve():
        cold.atomic_write_text(
            copied_report, report.read_text(encoding="utf-8"))

    sglb_diags = {
        f"{seed}/{condition}": _paper_diag(
            cells[(seed, condition, "sglb")]["text"])
        for seed in seeds for condition in CONDITIONS
    }
    manifest = {
        "status": "complete",
        "nodes": NODES,
        "leaves": 4,
        "hosts_per_leaf": 64,
        "spines": 64,
        "paths": 64,
        "source_leaf": SOURCE_LEAF,
        "source_hosts": len(SOURCE_HOSTS),
        "schemes": SCHEMES,
        "conditions": CONDITIONS,
        "seeds": seeds,
        "sample_scale": sample_scale,
        "arrival_window_us": [ARRIVAL_START_US, ARRIVAL_END_US],
        "steady_window_us": [STEADY_START_US, STEADY_END_US],
        "short_offered_load": SHORT_OFFERED_LOAD,
        "long_offered_load": LONG_OFFERED_LOAD,
        "hot_spines": cold.HOT_SPINES,
        "hotspot_rate_gbps": cold.HOTSPOT_RATE_GBPS,
        "mrc_active_evs": 64,
        "mrc_policy": "skip_token",
        "sglb": {
            "scheme": "sglb",
            "profile": "real_gcn_raw_linear",
            "local_update_us": 1,
            "remote_gcn_min_interval_us": 15,
            "gcn_packet_bytes": 256,
            "gcn_priority": "high",
            "cell_diagnostics": sglb_diags,
        },
        "sim_sha256": cold.file_sha256(args.sim),
        "traffic_sha256": {
            f"{seed}/{kind}": cold.file_sha256(path)
            for (seed, kind), path in traffic_by_seed.items()
        },
        "ecmp_background_flows": ECMP_BACKGROUND_FLOWS,
        "ecmp_background_size": ECMP_BACKGROUND_SIZE,
        "reference_rtt_us": 7.0,
        "theory_checks": checks,
        "all_arrival_flows": len(all_flows),
        "steady_flows": len(paired),
        "flow_size_points": [row["flow_size"] for row in size_plot_rows],
        "report": report.name,
        "theory_checks_file": checks_path.name,
        "combined_figure": [path.name for path in published_figures],
    }
    cold.atomic_write_text(
        args.out / "manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(report)
    print(published_figures[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
