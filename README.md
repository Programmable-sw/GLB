# N-MRC on htsim

本仓库在 Broadcom `csg-htsim` 的 RoCE datacenter 仿真器基础上，实现并评估 N-MRC 负载均衡方案。实验使用 generated fat-tree 拓扑，主要比较 `ECMP`、`OPS`、`REPS` 和 `N-MRC` 在健康网络与非对称带宽网络下的 FCT / CCT 表现。

N-MRC 是一种 source-controlled packet-level 负载均衡方案。源端为每个 packet 选择一个 EV/pathid；交换机在每个 ECMP stage 解释该 EV；目的 ToR 聚合路径观测并把紧凑的 path-state feedback 返回给源端；源端再用按 `(src ToR, dst ToR)` 共享的路径健康 bitmap 选择后续 packet 的 EV。这样可以避免维护大规模 per-flow/per-QP 路径表，同时让同一 ToR-pair 下的多个 flow 共享路径健康信息。

详细实验说明见 [experiments/n-mrc/README.md](experiments/n-mrc/README.md)。

## 仓库结构

- `sim/`：htsim C++ 离散事件仿真器。
- `sim/datacenter/htsim_roce`：编译后的 RoCE 仿真程序。
- `experiments/n-mrc/run_literature_metric_compare.py`：标准 N-MRC 对比实验脚本。
- `experiments/n-mrc/run_glb_factor_compare.py`：辅助 GLB 参数对比脚本。
- `experiments/n-mrc/README.md`：N-MRC 实验说明。
- `experiments/n-mrc/output/`：本地实验输出目录，默认不提交。

## N-MRC 机制简介

N-MRC 把一个 EV 看作完整的端到端路径组合。2-tier 拓扑中，EV 主要对应 ToR 上行选择；3-tier 拓扑中，EV 会被分段映射到 ToR uplink、Agg uplink、Core-down bundle 和 Agg-down bundle。

源端按 `(src ToR, dst ToR)` 维护共享路径状态。每个 EV 使用 2-bit 状态：

| 状态 | 含义 |
| --- | --- |
| `11` | strong-good path |
| `10` | usable path |
| `01` | suspect/low path |
| `00` | 留给丢包语义 |

发送时源端优先选择 `11` 路径；若 `11` 路径不足，再逐步放宽到 `10` 和 `01`。目的 ToR 记录每条 EV 上 packet 是否 clean 或带 ECN CE 标记，生成 bitmap feedback，并由 ACK 带回源端更新共享状态。

## 已实现负载均衡方案

| 方案 | 机制简述 |
| --- | --- |
| `ECMP` | 每条 flow 使用固定 pathid，交换机用 ECMP hash 映射到下一跳。 |
| `OPS` | 源端每个 packet 随机选择 EV/pathid，实现无反馈 packet spraying。 |
| `REPS` | ACK 回传近期未 ECN 的 pathid，源端优先复用 clean EV；没有反馈时随机。 |
| `N-MRC` | 源端按 ToR-pair 共享 EV 健康状态，目的 ToR 聚合 ECN/clean 观测并反馈，源端逐包选择更健康的 EV。 |
| `conweave` | RTT 超过阈值后切换 pathid，减少持续走拥塞路径的概率。 |
| `adaptive-routing` | 交换机按本地队列拥塞情况在可用下一跳中选择端口。 |
| `drill` | 交换机结合随机候选和历史候选端口，优先选择拥塞较低的下一跳。 |
| `glb` | 交换机把本地队列、链路利用率和下游状态合成评分后选择下一跳。 |

## 标准测试场景

| 场景 | 拓扑 | 链路条件 | Flow size | 对比方案 |
| --- | --- | --- | --- | --- |
| 健康网络 | 2048 nodes / 2-tier | 全链路 400Gbps | 4/8/16/32MiB | ECMP / OPS / REPS / N-MRC |
| 非对称带宽 | 1024 nodes / 3-tier | 3% ToR 上行半带宽，即 200Gbps | 4/8/16/32MiB | ECMP / OPS / REPS / N-MRC |

通用参数：

- 链路速率：400Gbps。
- 队列：`lossless_input_ecn`。
- 队列长度：`-q 100` packets。
- ECN：Kmin/Kmax = 20/80 packets。
- PFC：low/high threshold = 20/80 packets。
- Host queue：`prio`。
- MTU：4096 bytes。
- 拥塞控制：`DCQCN_variant`。
- 流量：`tornado`，每个 host 一个 foreground flow，目的端为 `(src + nodes/2) % nodes`。
- 指标：avg FCT、p99 FCT、p99.9 FCT、max FCT、CCT。

标准实验使用 lossless + ECN + PFC，不把网络设成有损丢包队列。RoCE 源端/接收端代码有 NACK/重传逻辑，但当前默认对比主要考察拥塞标记和负载均衡，不依赖有损网络配合重传。

非对称带宽场景按所有 ToR-to-aggregation uplink 计算慢链路数量。1024-node / 3-tier generated fat-tree 的 ToR 上行总数为 1024 条，因此默认慢链路数量为 `ceil(1024 * 0.03) = 31`。

## 环境准备

推荐直接 clone 当前 N-MRC 分支：

```bash
git clone -b main-htsim https://github.com/Programmable-sw/GLB.git csg-htsim
cd csg-htsim
git remote add upstream https://github.com/Broadcom/csg-htsim.git || true
git fetch --all --prune
```

Ubuntu/Debian 依赖：

```bash
sudo apt-get update
sudo apt-get install -y build-essential make g++ git python3 python3-pip
python3 -m pip install --user matplotlib
```

构建仿真器：

```bash
make -C sim -j"$(nproc)"
```

检查 RoCE 仿真程序和脚本：

```bash
./sim/datacenter/htsim_roce -h | head -n 40
python3 -m py_compile \
  experiments/n-mrc/run_literature_metric_compare.py \
  experiments/n-mrc/run_glb_factor_compare.py
```

## 运行实验

运行标准两场景主测试：

```bash
python3 experiments/n-mrc/run_literature_metric_compare.py
```

默认会展开：

- 健康网络：2048 nodes / 2-tier，4/8/16/32MiB。
- 非对称带宽：1024 nodes / 3-tier，4/8/16/32MiB。
- 每个 scenario 跑 `ecmp`、`ops`、`reps`、`N-MRC`，共 `2 * 4 * 4 = 32` 次仿真。

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

常用环境变量：

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `SCENARIO_HEALTHY_TOPOLOGIES` | `2048:2` | 健康网络拓扑列表，格式为 `nodes:tiers`。 |
| `SCENARIO_ASYM_TOPOLOGIES` | `1024:3` | 非对称带宽拓扑列表。 |
| `SCENARIO_FLOW_SIZE_MIBS` | `4,8,16,32` | flow size 列表。 |
| `SCENARIO_TRAFFIC` | `tornado` | 支持 `tornado` / `permutation`。 |
| `SCENARIO_CC` | `dcqcn_variant` | 可改为 `dcqcn`。 |
| `SCENARIO_OUT` | 自动生成 | 输出目录。 |
| `KEEP_RAW_OUTPUT` | unset | 设为 `1` 时保留 `.cmd`、`.stdout`、`.dat` 和 `.cm` 文件。 |

## 输出文件

主测试输出目录包含：

- `summary.csv`：每个场景、每个方案的原始指标。
- `normalized.csv`：场景内相对最优值的归一化结果。
- `per_flow.csv`：逐流 FCT 明细。
- `comparison_report.md`：自动生成的 Markdown 汇总报告。
- `scenario_plan.md`：脚本本次展开的场景列表。

## 辅助 GLB 参数脚本

GLB 五因子参数对比脚本保留为辅助实验：

```bash
python3 experiments/n-mrc/run_glb_factor_compare.py
```

示例：

```bash
GLB_FACTOR_NODES=1024 GLB_FACTOR_TIERS=3 GLB_FACTOR_FLOW_SIZE=$((32 * 1024 * 1024)) \
python3 experiments/n-mrc/run_glb_factor_compare.py
```

## 核心代码

- `experiments/n-mrc/run_literature_metric_compare.py`：标准场景生成、命令拼接、指标解析和报告生成。
- `experiments/n-mrc/run_glb_factor_compare.py`：GLB 参数对比。
- `sim/datacenter/main_roce.cpp`：RoCE CLI 参数、LB 模式选择、EV 空间按拓扑自动校准。
- `sim/roce.cpp` / `sim/roce.h`：源端 `ecmp`、`ops`、`reps`、`N-MRC` 选路状态机和 ACK feedback 更新。
- `sim/rocepacket.h`：RoCE packet/ACK 上携带 pathid 和 N-MRC bitmap feedback。
- `sim/datacenter/fat_tree_switch.cpp` / `sim/datacenter/fat_tree_switch.h`：交换机 ECMP/AR/DRILL/GLB 转发、source-controlled pathid 分段映射、N-MRC feedback 生成。
- `sim/datacenter/fat_tree_topology.cpp` / `sim/datacenter/fat_tree_topology.h`：generated fat-tree 拓扑、bundle/radix 参数、慢链路注入。

## 清理

```bash
rm -rf experiments/n-mrc/output experiments/n-mrc/output_*
make -C sim clean
```

## htsim 背景

htsim 是一个高性能离散事件仿真器，设计目标是快速研究拥塞控制算法行为。本 fork 保留 htsim/RoCE 仿真器结构，并在其上加入 N-MRC 相关实验。
