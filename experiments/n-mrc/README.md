# N-MRC 实验目录

本目录存放 N-MRC 的标准仿真实验脚本、辅助对比脚本、PPT 材料和本地输出。完整项目说明、方案机制和运行方法见仓库根目录 [README.md](../../README.md)。

## 主要文件

- `run_literature_metric_compare.py`：标准两场景对比脚本，默认比较 `ecmp`、`ops`、`reps`、`N-MRC`。
- `run_glb_factor_compare.py`：GLB 参数对比辅助脚本，不属于当前标准主测试。
- `DESIGN_POINTS.md`：N-MRC 机制设计记录。
- `n-mrc_htsim_overview.pptx`：N-MRC 仿真实验汇报 PPT。
- `n-mrc_ppt_prompt.md`：生成上述 PPT 使用的 prompt。
- `output/`：本地实验结果目录，默认不提交。

## 标准测试

默认标准测试包含两个场景：

| 场景 | 拓扑 | 链路条件 | 对比方案 |
| --- | --- | --- | --- |
| 健康网络 | 2048 nodes / 2-tier | 全链路 400Gbps | `ecmp`、`ops`、`reps`、`N-MRC` |
| 非对称带宽 | 1024 nodes / 3-tier | 3% ToR 上行半带宽 | `ecmp`、`ops`、`reps`、`N-MRC` |

运行完整标准测试：

```bash
python3 experiments/n-mrc/run_literature_metric_compare.py
```

只跑 8MiB 快速检查：

```bash
SCENARIO_FLOW_SIZE_MIBS=8 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

保留每次仿真的原始输出：

```bash
KEEP_RAW_OUTPUT=1 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

## 输出说明

标准测试输出目录中通常包含：

- `summary.csv`：每个场景、每个方案的原始指标。
- `normalized.csv`：场景内相对最优值的归一化结果。
- `per_flow.csv`：逐流 FCT 明细。
- `comparison_report.md`：自动生成的 Markdown 汇总报告。
- `scenario_plan.md`：脚本本次展开的场景列表。

使用 `KEEP_RAW_OUTPUT=1` 时，还会保留每个 scenario 的 `.cmd`、`.stdout`、`.dat` 和 `.cm` 文件。
