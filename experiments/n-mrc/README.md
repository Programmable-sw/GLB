# N-MRC htsim 仿真实验

本目录维护 RoCE over generated fat-tree 下的 OPS / REPS / N-MRC 负载均衡对比实验。当前标准实验包含两个场景，主要比较 `ecmp` 基线与 `ops`、`reps`、`N-MRC` 这几个逐包/端侧多路径方案的 FCT 与 CCT 表现：

- 健康网络：2048 nodes / 2-tier leaf-spine。
- 非对称带宽：1024 nodes / 3-tier fat-tree，3% ToR 上行链路带宽减半。

四个方案统一使用 `DCQCN_variant`。文档、图表和结果统一使用 `N-MRC` 作为方案名称；直接调用 htsim 时使用 `-lb n-mrc`。

## 仿真器信息

- 仿真器：htsim C++ 离散事件仿真器，RoCE 仿真程序为 `sim/datacenter/htsim_roce`。
- 代码基线：基于 Broadcom `csg-htsim`；当前实验分支为 `main-htsim`，fork remote 为 `https://github.com/Programmable-sw/GLB.git`。
- 上游 remote：`https://github.com/Broadcom/csg-htsim.git`。
- 构建方式：`make -C sim`，主要依赖为 C++ 编译器、make、Python 3。
- 绘图脚本额外需要 `matplotlib`；标准对比脚本只依赖 Python 标准库。

## LB 机制实现汇总

标准两场景批跑 `ecmp`、`ops`、`reps` 和 `N-MRC`。其他 `-lb` 模式可用于辅助对照。

| 方案 | 机制简述 |
| --- | --- |
| `ecmp` | 每条 flow 使用固定 pathid，交换机用 ECMP hash 映射到下一跳。 |
| `ops` | 源端每个 packet 随机选择 EV/pathid，实现无反馈 packet spraying。 |
| `reps` | ACK 回传近期未 ECN 的 pathid，源端优先复用 clean EV；没有可用反馈时随机选路。 |
| `N-MRC` | 源端按 ToR-pair 共享 EV 健康状态，目的 ToR 聚合 ECN/clean 观测并经 ACK 带回，源端再逐包选择更健康的 EV。 |
| `conweave` | RTT 超过阈值后切换 pathid，减少持续走拥塞路径的概率。 |
| `adaptive-routing` | 交换机按本地队列拥塞情况在可用下一跳中选择端口。 |
| `drill` | 交换机结合随机候选和历史候选端口，优先选择拥塞较低的下一跳。 |
| `glb` | 交换机把本地队列、链路利用率和下游状态合成评分后选择下一跳。 |

## N-MRC 工作流

N-MRC 的方案思路是：源端不为每条 flow/QP 单独维护路径表，而是按 `(src ToR, dst ToR)` 共享一份 EV/path 健康视图；目的 ToR 聚合同一 source ToR 过来的 ECN/clean 观测，并把 bitmap 反馈给源端；源端再用 source-controlled EV 控制每个 packet 走哪条多路径组合。这样同一 ToR-pair 下不同 QP 能共享路径健康信息，同时仍保持逐包级别的路径选择。

### 状态粒度

- 端侧状态按 `(src ToR, dst ToR)` 共享，不按 QP/flow 独立维护。
- 每个 EV/path 保存 2-bit 状态：`11` 为 strong-good，`10` 为 usable，`01` 为 suspect/low，`00` 留给丢包语义。
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

ToR 侧反馈只在接收侧 ToR 生成。目的 ToR 按 source ToR 聚合 path 观测：带 ECN CE 的 EV 记为 bad，未带 ECN 的 EV 记为 clean。达到 packet 阈值或反馈时间窗口后，ToR 把 bitmap 挂到 data packet；接收端再把 bitmap 拷贝到 ACK。

源端收到 ACK 后更新共享 bitmap：

1. 明确带 ECN 的 EV 降为 `01`。
2. 明确观测到未 ECN 的 EV 若未到 `11`，升一级。
3. 没有新观测的低状态 EV 只缓慢恢复到 `10`，不会直接升到 strong-good。

## 标准测试场景

通用参数：

- 链路速率：400Gbps。
- 队列：`lossless_input_ecn`，即交换机/链路侧使用 lossless input queue，并在拥塞时打 ECN。
- 队列长度：`-q 100` packets。
- ECN：Kmin=20% queue、Kmax=80% queue，即 20/80 packets。
- PFC：low/high threshold 为 20/80 packets。
- Host queue：`prio`，即 host 发送侧使用优先级队列，高优先级 packet 优先发送，并响应 PFC PAUSE。
- MTU：4096 bytes。
- 拥塞控制：`DCQCN_variant`。
- 流量：`tornado`，每个 host 发一个 foreground flow，目的端为 `(src + nodes/2) % nodes`，所有 foreground flow 默认 `start_us=0`。
- Flow size：4MiB、8MiB、16MiB、32MiB。
- 主要指标：avg FCT、p99 FCT、p99.9 FCT、max FCT、CCT。

标准测试使用 lossless + ECN + PFC，不把网络设成有损丢包队列。RoCE 源端/接收端代码有 NACK/重传逻辑，但这里的实验主要考察拥塞标记和负载均衡，不依赖有损网络配合重传。

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

确认 RoCE 仿真程序和脚本可用：

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
experiments/n-mrc/output/topo-healthy2048n2t-asym1024n3t_traffic-tornado_flow-4-32m_scene-healthy-asym3pct_schemes-ecmp-ops-reps-n-mrc/
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
- `sim/datacenter/main_roce.cpp`：RoCE CLI 参数、LB 模式选择、EV 空间按拓扑自动校准。
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
