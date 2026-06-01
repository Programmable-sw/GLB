# N-MRC on htsim

本仓库基于 Broadcom `csg-htsim`，用于在 RoCE over generated fat-tree 拓扑中评估 N-MRC 及若干负载均衡方案。当前主线实验对比 `ECMP`、`OPS`、`REPS`、`N-MRC` 四个方案；四个方案统一使用 `DCQCN_variant` 拥塞控制。

文档、图表、结果和推荐 CLI 统一使用 `N-MRC` 作为方案名称。直接调用 htsim 时，使用：

```bash
-lb n-mrc -nmrc_state_mode 2bit-ecn01 -nmrc_unknown_reopen
```

## 仓库内容

- `sim/`：htsim C++ 离散事件仿真器。
- `sim/datacenter/htsim_roce`：RoCE 仿真入口，编译后生成。
- `experiments/n-mrc/run_literature_metric_compare.py`：当前标准场景主测试脚本。
- `experiments/n-mrc/run_glb_factor_compare.py`：GLB 五因子参数辅助对比脚本。
- `experiments/n-mrc/README.md`：N-MRC 实验目录内的详细说明。
- `experiments/n-mrc/output/`：本地实验输出目录，已被 `.gitignore` 忽略。

## 当前版本改动

相对原始 `csg-htsim`，本分支主要增加和整理了以下能力：

- 增加 N-MRC 端侧选路逻辑；直接调用仿真器时使用 `-lb n-mrc -nmrc_state_mode 2bit-ecn01 -nmrc_unknown_reopen` 启用。
- N-MRC 使用 ToR-pair 共享 2-bit EV/path bitmap，不维护 per-flow/per-QP 大路径表。
- 支持 ECN soft-degrade：ECN bad feedback 将路径降为 `01`，observed-clean 逐步恢复到 `11`。
- 支持 unknown-low-state reopen：unknown feedback 可把低状态路径温和复开到最高 `10`，不会直接升到 strong-good。
- source-controlled pathid 使用 tier EV mapping：交换机在每个 ECMP stage 从 EV 中截取对应段并对本级 ECMP 组取模。
- N-MRC path space、feedback packet 阈值和 `min_good_paths` 按拓扑自动校准。
- 支持 `-slow_tor_uplinks` / `-slow_tor_uplink_divisor` 注入 ToR 上行半带宽链路。
- 增加标准化实验脚本，自动生成 traffic matrix、运行仿真、解析 FCT/CCT 指标并输出报告。

## 负载均衡方案

| 方案 | CLI | 说明 |
| --- | --- | --- |
| ECMP | `-lb ecmp` | 端侧固定 pathid，交换机按 `flow_id + pathid` 做 ECMP 哈希。 |
| OPS | `-lb ops` | 源端逐包随机选择 EV/pathid。 |
| REPS | `-lb reps` | ACK 携带 clean pathid，源端优先复用近期 clean EV。 |
| N-MRC | `-lb n-mrc -nmrc_state_mode 2bit-ecn01 -nmrc_unknown_reopen` | ToR-pair 共享 2-bit EV 状态，按状态优先级逐包选路。 |
| ConWeave-like | `-lb conweave` | 当前仓库中的简化 RTT-threshold reroute 版本，不包含论文版 VOQ 保序缓冲。 |
| Adaptive Routing | `-lb adaptive-routing` | 交换机本地拥塞自适应选路。 |
| DRILL | `-lb drill` | 交换机本地随机候选 + 历史候选的拥塞感知选路。 |
| GLB | `-lb glb` | 基于本地端口拥塞、链路利用率和远端候选状态评分。 |

## 标准测试场景

当前主测试只保留两个标准场景：

| 场景 | 拓扑 | 链路条件 | Flow size | 方案 |
| --- | --- | --- | --- | --- |
| 健康网络 | 2048 nodes / 2-tier leaf-spine | 全链路 400Gbps | 4/8/16/32MiB | ECMP / OPS / REPS / N-MRC |
| 非对称带宽 | 1024 nodes / 3-tier fat-tree | 3% ToR 上行链路带宽减半 | 4/8/16/32MiB | ECMP / OPS / REPS / N-MRC |

通用参数：

- 链路速率：400Gbps。
- 队列：`lossless_input_ecn`。
- 队列长度：`-q 100` packets。
- ECN：Kmin=20% queue、Kmax=80% queue，即 20/80 packets。
- PFC：low/high threshold 为 20/80 packets。
- Host queue：`prio`。
- MTU：4096 bytes。
- 拥塞控制：`DCQCN_variant`。
- 流量：`tornado`，每个 host 发一个 foreground flow，目的端为 `(src + nodes/2) % nodes`。
- 主要指标：avg FCT、p99 FCT、p99.9 FCT、max FCT、CCT。

非对称带宽场景按 ToR-to-aggregation uplink 总数注入慢链路。1024-node / 3-tier generated fat-tree 共有 1024 条 ToR 上行链路，因此 `ceil(1024 * 0.03) = 31` 条链路降为 200Gbps。

## 从 Clone 到运行

推荐直接 clone 当前 N-MRC 分支：

```bash
git clone -b main-htsim https://github.com/Programmable-sw/GLB.git csg-htsim
cd csg-htsim
git remote add upstream https://github.com/Broadcom/csg-htsim.git || true
git fetch --all --prune
```

如果已经从 Broadcom 上游仓库 clone，可以这样切到本分支：

```bash
git clone https://github.com/Broadcom/csg-htsim.git csg-htsim
cd csg-htsim
git remote add n-mrc https://github.com/Programmable-sw/GLB.git
git fetch n-mrc main-htsim
git checkout -B main-htsim n-mrc/main-htsim
```

安装依赖。Ubuntu/Debian 上可用：

```bash
sudo apt-get update
sudo apt-get install -y build-essential make g++ git python3 python3-pip
python3 -m pip install --user matplotlib
```

编译 htsim：

```bash
make -C sim -j"$(nproc)"
```

确认 RoCE 仿真入口和 Python 脚本可用：

```bash
./sim/datacenter/htsim_roce -h | head -n 40
python3 -m py_compile \
  experiments/n-mrc/run_literature_metric_compare.py \
  experiments/n-mrc/run_glb_factor_compare.py
```

## 运行标准主测试

运行完整标准测试：

```bash
python3 experiments/n-mrc/run_literature_metric_compare.py
```

默认会展开 8 个 scenario，并对每个 scenario 跑 4 个方案，共 32 次仿真：

- `healthy_2048n_2tier_{4,8,16,32}m`
- `asym_tor3pct_1024n_3tier_{4,8,16,32}m`

默认输出目录：

```text
experiments/n-mrc/output/topo-healthy2048n2t-asym1024n3t_traffic-tornado_flow-4-32m_scene-healthy-asym3pct_schemes-ecmp-ops-reps-n-mrc/
```

快速检查只跑 8MiB：

```bash
SCENARIO_FLOW_SIZE_MIBS=8 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

只跑健康网络：

```bash
SCENARIO_ASYM_TOPOLOGIES= SCENARIO_FLOW_SIZE_MIBS=8 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

只跑非对称带宽：

```bash
SCENARIO_HEALTHY_TOPOLOGIES= SCENARIO_FLOW_SIZE_MIBS=8 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

保留每个方案的原始 stdout/dat/cmd/traffic matrix：

```bash
KEEP_RAW_OUTPUT=1 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

常用环境变量：

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `SCENARIO_HEALTHY_TOPOLOGIES` | `2048:2` | 健康网络拓扑列表，格式为 `nodes:tiers`。 |
| `SCENARIO_ASYM_TOPOLOGIES` | `1024:3` | 非对称带宽拓扑列表。 |
| `SCENARIO_TOPOLOGIES` | unset | 同时覆盖健康和非对称拓扑，主要用于临时扫参。 |
| `SCENARIO_FLOW_SIZE_MIBS` | `4,8,16,32` | flow size 列表。 |
| `SCENARIO_TRAFFIC` | `tornado` | 支持 `tornado` / `permutation`。 |
| `SCENARIO_CC` | `dcqcn_variant` | 可改为 `dcqcn`。 |
| `SCENARIO_OUT` | 自动生成 | 输出目录。 |
| `KEEP_RAW_OUTPUT` | unset | 设为 `1` 时保留每个 scenario 的原始运行文件。 |

## 输出文件

主测试输出目录包含：

- `summary.csv`：每个场景、每个方案的原始指标。
- `normalized.csv`：场景内相对最优值的归一化结果。
- `per_flow.csv`：逐流 FCT 明细。
- `comparison_report.md`：自动生成的 Markdown 汇总报告。
- `scenario_plan.md`：脚本本次展开的场景列表。

使用 `KEEP_RAW_OUTPUT=1` 时，每个 scenario 子目录还会包含：

- `*.cmd`：实际 htsim 命令。
- `*.stdout`：仿真器 stdout。
- `*.dat`：仿真器 dat 输出。
- `*.cm`：traffic matrix。

## GLB 辅助实验

GLB 五因子参数对比脚本保留为辅助实验，不属于当前标准主测试：

```bash
python3 experiments/n-mrc/run_glb_factor_compare.py
```

示例：1024-node / 3-tier / 32MiB：

```bash
GLB_FACTOR_NODES=1024 GLB_FACTOR_TIERS=3 GLB_FACTOR_FLOW_SIZE=$((32 * 1024 * 1024)) \
python3 experiments/n-mrc/run_glb_factor_compare.py
```

## 核心代码位置

- `experiments/n-mrc/run_literature_metric_compare.py`：标准场景生成、命令拼接、指标解析和报告生成。
- `experiments/n-mrc/run_glb_factor_compare.py`：GLB 不同参数组合对比。
- `sim/datacenter/main_roce.cpp`：RoCE CLI 参数、LB 模式选择、N-MRC canonical 参数、EV 空间按拓扑自动校准。
- `sim/roce.cpp` / `sim/roce.h`：源端 `ecmp`、`ops`、`reps`、`N-MRC` 选路状态机和 ACK feedback 更新。
- `sim/rocepacket.h`：RoCE packet/ACK 上携带 pathid 和 N-MRC bitmap feedback。
- `sim/datacenter/fat_tree_switch.cpp` / `sim/datacenter/fat_tree_switch.h`：交换机 ECMP/AR/DRILL/GLB 转发、source-controlled pathid 分段映射、N-MRC feedback 生成。
- `sim/datacenter/fat_tree_topology.cpp` / `sim/datacenter/fat_tree_topology.h`：generated fat-tree 拓扑、bundle/radix 参数、慢链路注入。

## 清理

清理实验输出：

```bash
rm -rf experiments/n-mrc/output experiments/n-mrc/output_*
```

清理编译产物：

```bash
make -C sim clean
```

## htsim 背景

htsim is a high performance discrete event simulator, inspired by ns2, but much faster, primarily intended to examine congestion control algorithm behaviour. It was originally written by Mark Handley to examine TCP stability issues, extended to study Multipath TCP and datacenter topologies, and later adopted by Correct Networks/Broadcom to develop EQDS. This fork keeps the htsim/RoCE simulator structure and adds N-MRC-oriented experiments on top.
