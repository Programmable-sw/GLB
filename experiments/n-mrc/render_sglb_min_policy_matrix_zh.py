#!/usr/bin/env python3
"""Render the completed SGLB matrix as a readable Chinese Markdown report."""

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output/sglb_min_policy_matrix_256"
SOURCE = OUT / "cells.csv"
REPORT = OUT / "report_zh.md"

CADENCE_ZH = {"synchronized": "同步", "independent": "异步"}
SCENARIO_ZH = {
    "healthy_medium": "健康中压",
    "asymmetric_medium": "非对称中压",
    "healthy_high": "健康高压",
    "asymmetric_high": "非对称高压",
}
POLICY_ZH = {
    "strict_k": "固定 K",
    "whole_grade_min": "整档补足",
    "exact_min": "精确补足",
}
MINS = (16, 20, 24, 28, 32, 36, 40)


def number(value, digits=2):
    return f"{float(value):.{digits}f}"


def integer(value):
    return f"{int(float(value)):,}"


def table(headers, rows):
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(map(str, row)) + " |" for row in rows)
    return "\n".join(lines)


def main():
    rows = list(csv.DictReader(SOURCE.open(encoding="utf-8")))
    if len(rows) != 168:
        raise ValueError(f"期望 168 项，实际 {len(rows)} 项")
    index = {
        (row["cadence"], row["scenario"], row["policy"],
         int(row["min_choices"])): row for row in rows
    }

    lines = [
        "# SGLB 同步/异步 GCN、min 与截断机制完整实验报告",
        "",
        "## 结论摘要",
        "",
        "- 168/168 项均完成 65,280 条流，配置校验通过，`gcn_stale=0`。",
        "- 若中压、高压使用同一配置，且两个健康场景 CCT 均不比全局最优回退超过 1%，"
        "唯一满足条件的是 **异步 GCN + 固定 K + min28**。",
        "- 分负载调优时，中压最佳为 **异步 + 固定 K + min32**；高压最佳为"
        " **同步 + 整档补足 + min16**。",
        "- 同步 GCN 在非对称压力下更常获胜，但健康场景并非稳定改善；同步效果与 min、"
        "截断机制存在明显交互。",
        "",
        "## 实验口径",
        "",
        "- 拓扑：256 主机、64 条平面内路径；seed=13。",
        "- 中压：p16 A2A、每源 64 MiB；非对称场景增加 300 Gbit/s、200 μs ON/OFF 背景。",
        "- 高压：p32 A2A、每源 256 MiB；非对称场景增加 350 Gbit/s、400 μs ON/OFF 背景。",
        "- 候选内派发：两种 GCN 均使用默认 random，不比较 RR。",
        "- 选择规则：健康 CCT 距相应最优值不超过 1%，再按非对称 CCT 选择。",
        "",
        "## 四场景完整 CCT、p99 与候选数",
        "",
        "下列每格格式为 `CCT / p99 / 平均候选数`，时间单位均为 μs。",
    ]

    for cadence in ("synchronized", "independent"):
        for scenario in SCENARIO_ZH:
            lines.extend(["", f"### {CADENCE_ZH[cadence]} · {SCENARIO_ZH[scenario]}", ""])
            body = []
            for minimum in MINS:
                values = [minimum]
                for policy in ("strict_k", "whole_grade_min", "exact_min"):
                    row = index[(cadence, scenario, policy, minimum)]
                    values.append(
                        f"{number(row['cct_us'])} / {number(row['p99_fct_us'])} / "
                        f"{number(row['avg_candidate_choices'])}")
                body.append(values)
            lines.append(table(
                ["min", "固定 K", "整档补足", "精确补足"], body))

    lines.extend([
        "",
        "## 168 项拥塞与候选集明细",
        "",
        "该表逐项列出决定结论所需的主要指标；全部其他字段见 `cells.csv`。",
        "",
    ])
    detail_rows = []
    for cadence in ("synchronized", "independent"):
        for scenario in SCENARIO_ZH:
            for policy in ("strict_k", "whole_grade_min", "exact_min"):
                for minimum in MINS:
                    row = index[(cadence, scenario, policy, minimum)]
                    detail_rows.append([
                        CADENCE_ZH[cadence], SCENARIO_ZH[scenario],
                        POLICY_ZH[policy], minimum, number(row["cct_us"]),
                        number(row["p99_fct_us"]),
                        number(row["avg_candidate_choices"]),
                        number(row["avg_best_quality_choices"]),
                        integer(row["retransmissions"]), integer(row["trims"]),
                        integer(row["ecn_marks"]),
                    ])
    lines.append(table([
        "GCN", "场景", "截断", "min", "CCT", "p99", "候选数",
        "最优档数", "重传", "TRIM", "ECN",
    ], detail_rows))

    lines.extend([
        "",
        "## 限制与解释",
        "",
        "- 结果来自单 seed；random 派发下应再用多个 seed 验证最终默认值。",
        "- 同步会改变事件顺序和随机数消耗顺序，因此单 seed 的逐项差异同时包含 cadence"
        " 与随机轨迹交互，不能当作无方差的普适提升。",
        "- `rankings.csv` 由旧场景名逻辑生成，目前为空；本报告直接从完整 `cells.csv`"
        " 按 168 个唯一配置重新索引。",
    ])
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
