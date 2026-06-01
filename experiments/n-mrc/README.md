# N-MRC htsim 仿真实验

本目录维护 RoCE over generated fat-tree 下的 OPS / REPS / N-MRC 负载均衡对比实验。当前主测试只选两类标准场景：

- 健康网络：2048 nodes / 2-tier leaf-spine。
- 非对称带宽：1024 nodes / 3-tier fat-tree，3% ToR 上行链路带宽减半。

当前主线方案为 `ecmp`、`ops`、`reps`、`N-MRC`，统一使用 `DCQCN_variant`。注意：N-MRC 在模拟器 CLI 和代码路径中仍复用历史名称 `dtor`。

## 仿真器信息

- 仿真器：htsim C++ 离散事件仿真器，RoCE 入口为 `sim/datacenter/htsim_roce`。
- 代码基线：基于 Broadcom `csg-htsim`；当前实验分支为 `main-htsim`，fork remote 为 `https://github.com/Programmable-sw/GLB.git`。
- 上游 remote：`https://github.com/Broadcom/csg-htsim.git`。
- 构建方式：`make -C sim`，主要依赖为 C++ 编译器、make、Python 3。
- 绘图脚本额外需要 `matplotlib`；主线对比脚本只依赖 Python 标准库。

该版本的核心改动：

- 增加/整理 N-MRC，即 CLI `-lb dtor -dtor_state_mode 2bit-ecn01 -dtor_unknown_reopen`。
- N-MRC 使用 ToR-pair 共享 2-bit EV/path bitmap，支持 ECN soft-degrade、observed-clean 恢复和 unknown-low-state reopen。
- source-controlled pathid 模式使用 tier EV mapping：交换机在每个 ECMP stage 从 EV 中截取对应段并对本级 ECMP 组取模。
- N-MRC 的 path space、feedback packet 阈值和 `min_good_paths` 按拓扑自动校准。
- 支持 `-slow_tor_uplinks` / `-slow_tor_uplink_divisor` 注入 ToR 上行半带宽链路。
- `experiments/n-mrc/run_literature_metric_compare.py` 负责标准场景生成、运行、指标解析和报告输出。

## LB 机制实现汇总

| 方案 | CLI | 当前定位 | 实现要点 |
| --- | --- | --- | --- |
| `ecmp` | `-lb ecmp` | 基线 | 端侧使用固定 pathid；交换机按 `flow_id + pathid` 做 ECMP 哈希。 |
| `ops` | `-lb ops` | 逐包基线 | 源端逐包随机选择 EV/pathid；交换机仍按 ECMP 组转发。 |
| `reps` | `-lb reps` | 端侧反馈对照 | ACK 携带上一跳未 ECN 的 pathid；源端优先复用近期 clean EV，buffer 为空时随机。 |
| `N-MRC` | `-lb dtor -dtor_state_mode 2bit-ecn01 -dtor_unknown_reopen` | 主推方案 | 端侧按 ToR-pair 共享逐 EV 的 2-bit 状态。优先在 `11` 状态路径中轮询；ECN 将路径降到 `01`；observed-clean 才逐步升到 `11`；unknown feedback 只把低状态温和复开到最高 `10`。 |
| `N-MRC(1bit+hold)` | `-lb dtor -dtor_bad_hold_down_us 60` | 保留对照 | 原二值 bitmap 方案，加 ToR-pair 级 60us bad hold-down，避免 bad feedback 后过早 reset。 |
| `conweave` | `-lb conweave` | 简化对照 | 当前仓库里是 RTT-threshold reroute 的 ConWeave-like 逻辑，不包含论文版 VOQ 保序缓冲。 |
| `adaptive-routing` | `-lb adaptive-routing -ar_granularity packet -ar_method queue` | 交换机本地对照 | Broadcom/csg-htsim 原有本地拥塞自适应选路。 |
| `drill` | `-lb drill` | 交换机本地对照 | 交换机保留历史候选端口，并结合随机候选按本地拥塞 metric 选择。 |
| `glb` | `-lb glb` | SGLB/GLB 对照 | 把本地端口拥塞、链路利用率和下一跳候选端口状态纳入评分。 |

## N-MRC 工作流

N-MRC 的目标是在不维护 per-flow/per-QP 路径表的前提下，让端侧拥有跨流共享的路径健康视图，并用 source-controlled EV 控制每个 packet 走哪条多路径组合。

### 状态粒度

- 端侧状态按 `(src ToR, dst ToR)` 共享，不按 QP/flow 独立维护。
- 每个 EV/path 保存 2-bit 状态：`11` 为 strong-good，`10` 为 usable，`01` 为 suspect/low，`00` 保留给不可用或二值模式下的 bad 语义。
- 初始化时所有 EV 为 `11`。
- 每个发送流的 cursor 和 stride 由 `src, dst, flow_id, priority` hash 派生，用于把同一 ToR-pair 下的不同 QP/flow 打散到不同轮询序列；这两个值不需要长期存成大表。

### EV 空间与映射

- 2-tier leaf-spine：`EV space = ToR uplink choices * Agg-down bundle`。
- 3-tier fat-tree：`EV space = ToR uplink choices * Agg uplink choices * Core-down bundle * Agg-down bundle`。
- 对 source-controlled LB，交换机启用 `pathid_only_hash`，不再使用普通 `flow_id + pathid` 哈希。
- 每一跳交换机只读取 EV 的对应段，并对当前 ECMP 组大小取模。

当前标准场景的 path space：

| 场景 | 拓扑 | generated K | ToR 上行选择 | Agg 上行选择 | EV path space |
| --- | --- | ---: | ---: | ---: | ---: |
| 健康网络 | 2048 nodes / 2-tier | 64 | 32 | 不适用 | 32 |
| 非对称带宽 | 1024 nodes / 3-tier | 16 | 8 | 8 | 64 |

### 发送与反馈

发送 packet 时，源端加载 `(src ToR, dst ToR)` 的共享 bitmap：

1. 若 `11` 路径数不少于 `min_good_paths`，只在 `11` 集合里按 seed/stride 伪轮询。
2. 若 `11` 不足，则降级到 `10` 集合；若 `10` 也不足，再降级到 `01` 集合。
3. 若所有状态集合都不可用，才回退到随机 EV。

ToR 侧反馈只在接收侧 ToR 生成。目的 ToR 按 source ToR 聚合 path 观测：带 ECN CE 的 EV 记为 bad，未带 ECN 的 EV 记为 observed-clean。达到 packet 阈值或反馈时间窗口后，ToR 把 bitmap 挂到 data packet；接收端再把 bitmap 拷贝到 ACK。

源端收到 ACK 后更新共享 bitmap：

1. ECN/bad EV 在 `2bit-ecn01` 模式下降为 `01`。
2. observed-clean EV 若未到 `11`，升一级。
3. unknown feedback 在 `unknown_reopen` 模式下最多复开到 `10`，不会直接升到 strong-good。

## 标准测试场景

通用参数：

- 链路速率：400Gbps。
- 队列：`lossless_input_ecn`。
- 队列长度：`-q 100` packets。
- ECN：Kmin=20% queue、Kmax=80% queue，即 20/80 packets。
- PFC：low/high threshold 为 20/80 packets。
- Host queue：`prio`。
- MTU：4096 bytes。
- 拥塞控制：`DCQCN_variant`。
- 流量：`tornado`，每个 host 发一个 foreground flow，目的端为 `(src + nodes/2) % nodes`，所有 foreground flow 默认 `start_us=0`。
- Flow size：4MiB、8MiB、16MiB、32MiB。
- 主要指标：avg FCT、p99 FCT、p99.9 FCT、max FCT、CCT。

| 编号 | 场景 | 拓扑 | 链路条件 | 慢链路数量 | 当前批跑方案 |
| --- | --- | --- | --- | ---: | --- |
| 1 | 健康网络 | 2048 nodes / 2-tier | 全链路 400Gbps | 0 | `ecmp`、`ops`、`reps`、`N-MRC` |
| 2 | 非对称带宽 | 1024 nodes / 3-tier | 3% ToR 上行链路降为半带宽，即 200Gbps | `ceil(1024 * 0.03) = 31` | `ecmp`、`ops`、`reps`、`N-MRC` |

这里的非对称带宽按 ToR-to-aggregation uplink 总数计算。1024-node / 3-tier generated fat-tree 的 ToR 上行总数为 1024 条，因此默认减半 31 条。

## 从 Clone 到运行

推荐直接 clone 当前 N-MRC 分支：

```bash
git clone -b main-htsim https://github.com/Programmable-sw/GLB.git csg-htsim
cd csg-htsim
git remote add upstream https://github.com/Broadcom/csg-htsim.git || true
git fetch --all --prune
```

如果已经从 Broadcom 上游 clone：

```bash
git clone https://github.com/Broadcom/csg-htsim.git csg-htsim
cd csg-htsim
git remote add n-mrc https://github.com/Programmable-sw/GLB.git
git fetch n-mrc main-htsim
git checkout -B main-htsim n-mrc/main-htsim
```

安装环境。Ubuntu/Debian 上可用：

```bash
sudo apt-get update
sudo apt-get install -y build-essential make g++ git python3 python3-pip
python3 -m pip install --user matplotlib
```

构建仿真器：

```bash
make -C sim -j"$(nproc)"
```

确认 RoCE 入口和脚本可用：

```bash
./sim/datacenter/htsim_roce -h | head -n 40
python3 -m py_compile \
  experiments/n-mrc/run_literature_metric_compare.py \
  experiments/n-mrc/run_glb_factor_compare.py
```

## 运行主测试

运行标准两场景主测试：

```bash
python3 experiments/n-mrc/run_literature_metric_compare.py
```

默认会展开：

- 健康网络：2048 nodes / 2-tier，4/8/16/32MiB。
- 非对称带宽：1024 nodes / 3-tier，4/8/16/32MiB。
- 每个 scenario 跑 `ecmp`、`ops`、`reps`、`N-MRC`，共 `2 * 4 * 4 = 32` 次仿真。

默认输出目录：

```text
experiments/n-mrc/output/topo-healthy2048n2t-asym1024n3t_traffic-tornado_flow-4-32m_scene-healthy-asym3pct_schemes-ecmp-ops-reps-nmrc/
```

只跑 8MiB 快速检查：

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

保留逐方案 stdout/dat 原始文件：

```bash
KEEP_RAW_OUTPUT=1 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

常用覆盖项：

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `SCENARIO_HEALTHY_TOPOLOGIES` | `2048:2` | 健康网络拓扑列表，格式为 `nodes:tiers`，可用逗号分隔多个拓扑。 |
| `SCENARIO_ASYM_TOPOLOGIES` | `1024:3` | 非对称带宽拓扑列表。 |
| `SCENARIO_TOPOLOGIES` | unset | 兼容已有用法；设置后会同时覆盖健康和非对称拓扑。 |
| `SCENARIO_FLOW_SIZE_MIBS` | `4,8,16,32` | flow size 列表。 |
| `SCENARIO_TRAFFIC` | `tornado` | 支持 `tornado` / `permutation`。 |
| `SCENARIO_CC` | `dcqcn_variant` | 可改为 `dcqcn`。 |
| `SCENARIO_OUT` | 自动生成 | 输出目录。 |
| `KEEP_RAW_OUTPUT` | unset | 设为 `1` 时保留每个 scenario 的 `.cmd`、`.stdout`、`.dat` 和 traffic matrix。 |

## 辅助 GLB 参数脚本

GLB 五因子参数对比脚本保留为辅助实验，不属于当前标准两场景主测试：

```bash
python3 experiments/n-mrc/run_glb_factor_compare.py
```

常用覆盖项：

```bash
GLB_FACTOR_NODES=1024 GLB_FACTOR_TIERS=3 GLB_FACTOR_FLOW_SIZE=$((32 * 1024 * 1024)) \
python3 experiments/n-mrc/run_glb_factor_compare.py
```

## 输出文件

主测试输出目录中包含：

- `summary.csv`：每个场景、每个方案的原始指标。
- `normalized.csv`：场景内相对最优值的归一化结果，越接近 1 越好。
- `per_flow.csv`：逐流 FCT 明细。
- `comparison_report.md`：自动生成的 Markdown 汇总报告。
- `scenario_plan.md`：脚本本次展开的场景列表。

使用 `KEEP_RAW_OUTPUT=1` 时，每个 scenario 子目录还会包含：

- `*.cmd`：实际 htsim 命令。
- `*.stdout`：仿真器 stdout。
- `*.dat`：仿真器 dat 输出。
- `*.cm`：traffic matrix。

## 核心代码

- `experiments/n-mrc/run_literature_metric_compare.py`：标准场景生成、命令拼接、指标解析和报告生成。
- `experiments/n-mrc/run_glb_factor_compare.py`：GLB 不同参数组合对比。
- `sim/datacenter/main_roce.cpp`：RoCE CLI 参数、LB 模式选择、N-MRC canonical 参数、EV 空间按拓扑自动校准。
- `sim/roce.cpp` / `sim/roce.h`：源端 `ecmp`、`ops`、`reps`、`N-MRC` 选路状态机和 ACK feedback 更新。
- `sim/rocepacket.h`：RoCE packet/ACK 上携带 pathid 和 N-MRC bitmap feedback。
- `sim/datacenter/fat_tree_switch.cpp` / `sim/datacenter/fat_tree_switch.h`：交换机 ECMP/AR/DRILL/GLB 转发、source-controlled pathid 分段映射、N-MRC feedback 生成。
- `sim/datacenter/fat_tree_topology.cpp` / `sim/datacenter/fat_tree_topology.h`：generated fat-tree 拓扑、bundle/radix 参数、慢链路注入。

## 清理

实验输出都在 `experiments/n-mrc/output/` 或 `experiments/n-mrc/output_*` 下。清理当前 N-MRC 输出：

```bash
rm -rf experiments/n-mrc/output experiments/n-mrc/output_*
```

清理编译产物：

```bash
make -C sim clean
```
