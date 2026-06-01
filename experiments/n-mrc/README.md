# OPS / REPS / N-MRC 对比实验

本目录用于维护 RoCE over fat-tree 下 OPS / REPS / N-MRC 相关的负载均衡对比实验。当前主线先跑 `ecmp`、`ops`、`reps`、`N-MRC` 四个方案，统一使用 `DCQCN_variant`。注意：N-MRC 在模拟器 CLI 和代码路径中仍复用 `dtor` 名称。

## LB 机制实现汇总

| 方案 | CLI | 当前定位 | 实现要点 |
| --- | --- | --- | --- |
| `ecmp` | `-lb ecmp` | 基线 | 端侧使用固定 pathid；交换机按 `flow_id + pathid` 做 ECMP 哈希。 |
| `ops` | `-lb ops` | 基线 | 源端逐包随机选择 EV/pathid；交换机仍按 ECMP 组转发。 |
| `reps` | `-lb reps` | 对照 | ACK 携带上一跳未 ECN 的 pathid；源端优先复用近期 clean 的 EV，buffer 为空时随机。 |
| `N-MRC` | `-lb dtor -dtor_state_mode 2bit-ecn01 -dtor_unknown_reopen` | 主推方案 | 端侧按 ToR-pair 共享逐 EV 的 2-bit 状态。优先在 `11` 状态路径中轮询；ECN 将路径降到 `01`；observed-clean 才逐步升到 `11`；unknown feedback 只把低状态温和复开到最高 `10`。 |
| `N-MRC(1bit+hold)` | `-lb dtor -dtor_bad_hold_down_us 60` | 保留对照 | 原二值 bitmap 方案，加 ToR-pair 级 60us bad hold-down，避免 bad feedback 后过早 reset。 |
| `adaptive-routing` | `-lb adaptive-routing -ar_granularity packet -ar_method queue` | 交换机本地对照 | Broadcom/csg-htsim 原有本地拥塞自适应选路。 |
| `drill` | `-lb drill` | 交换机本地对照 | 交换机保留历史候选端口，并结合随机候选按本地拥塞 metric 选择。 |
| `glb` | `-lb glb` | SGLB/GLB 对照 | 把本地端口拥塞、链路利用率和下一跳候选端口状态纳入评分。 |

`N-MRC` 的 EV 选路逻辑已核对：源端维护 EV 状态并在可用状态集合里做 per-flow seed/stride 伪轮询，不是简单全局递增。交换机侧对 source-controlled pathid 模式启用 tier EV mapping，按当前 ECMP stage 从 pathid 中取对应段并对本级 ECMP 组大小取模。3-tier EV 空间为 `ToR uplink choices * Agg uplink choices * Core-down bundle * Agg-down bundle`；2-tier EV 空间为 `ToR uplink choices * Agg-down bundle`。默认 generated 1024-node 3-tier topology 的 bundle 为 1，因此 inter-pod EV 数为 `8 * 8 = 64`。

## N-MRC 详细机制与工作流

N-MRC 的目标是在不维护 per-flow/per-QP 路径表的前提下，让端侧拥有跨流共享的路径健康视图，并用 source-controlled EV 控制每个 packet 走哪条多路径组合。当前实验里的 N-MRC 由 `-lb dtor -dtor_state_mode 2bit-ecn01 -dtor_unknown_reopen` 启用；`dtor` 是代码中的历史名称，文档和图表统一称为 N-MRC。

### 状态粒度

- 端侧状态按 `(src ToR, dst ToR)` 共享，不按 QP/flow 独立维护。一个源端发送到同一个目标 ToR 的所有 QP 复用同一份 EV/path bitmap。
- 每个 EV/path 保存 2-bit 状态：`11` 表示 strong-good，`10` 表示 usable，`01` 表示 suspect/low，`00` 保留给不可用/二值模式下的 bad 语义。
- 初始化时所有 EV 为 `11`。当前主推配置中，ECN 不会把路径直接清零，而是把路径降到 `01`，避免一次拥塞反馈导致路径长期不可用。
- 每个发送流还有一个很小的派生游标：起始 cursor 和 stride 由 `src, dst, flow_id, priority` hash 得到。它用于把同一 ToR-pair 下的不同 QP/flow 打散到不同轮询序列；这两个值可以按 QP hash 派生，不需要作为大表长期保存。

### EV 空间与分段映射

N-MRC 使用一个整数 `pathid/EV` 表示整条多路径组合。交换机在不同 hop 上读取这个 EV 的不同段，并对当前 ECMP 组大小取模：

- 2-tier leaf-spine：`EV space = ToR uplink choices * Agg-down bundle`。默认 bundle 为 1，因此 path space 等于 ToR 上行可选数。
- 3-tier fat-tree：`EV space = ToR uplink choices * Agg uplink choices * Core-down bundle * Agg-down bundle`。
- 对 source-controlled LB，交换机启用 `pathid_only_hash`，不再使用 `flow_id + pathid` 的普通 ECMP 哈希；同一个 EV 在每一跳被稳定解释为对应段的端口选择。

### 发送端选路

发送 packet 时，源端先根据 packet priority 选择对应 cursor/stride，再加载 `(src ToR, dst ToR)` 的共享 bitmap：

1. 统计当前 bitmap 中 `11` 状态的路径数。
2. 若 `11` 路径数不少于 `min_good_paths`，只在 `11` 集合里按 seed/stride 伪轮询。
3. 若 `11` 不足，则降级到 `10` 集合；若 `10` 也不足，再降级到 `01` 集合。
4. 若所有状态集合都不可用，才回退到随机 EV。

`min_good_paths` 由拓扑 path space 自动给出，规则为 `min(path_space / 2, 16)`，下限为 1。这样在大拓扑中不会要求维护过多 strong-good 路径，在小拓扑中也不会因为少量 ECN 把可选路径收得过窄。

### ToR 侧反馈生成

N-MRC 的交换机反馈只发生在 ToR。接收侧 ToR 在看到 RoCE data packet 时：

1. 根据 packet 的 source host 计算 `src ToR`，并以 `(local dst ToR, src ToR)` 为 key 维护一份短期反馈 bitmap。
2. 对 packet 当前 `pathid % path_count` 记录观测结果。
3. 如果 packet 带 ECN CE，则该 EV 在反馈 bitmap 中记为 bad。
4. 如果 packet 未带 ECN，则该 EV 记为 observed-clean。
5. 当累计 packet 数达到阈值，或距离上次反馈超过最大间隔时，把 feedback bitmap 挂到当前 data packet 上。

反馈触发参数也是自动的：`feedback_pkts = clamp(path_space / 2, 32, 128)`，当前默认反馈时间窗口为 `5us` 到 `20us`。以 64 条路径为例，packet 阈值为 32。

### ACK 回传与端侧更新

接收端收到 data packet 后，会把 packet 上携带的 N-MRC feedback bitmap 拷贝到 ACK。源端收到 ACK 后更新共享 bitmap：

1. 对 ECN/bad 的 EV：当前主推 `2bit-ecn01` 模式直接降为 `01`。
2. 对 observed-clean 的 EV：若本地状态小于 `11`，升一级，例如 `01 -> 10`、`10 -> 11`。
3. 对未被本轮明确观测但出现在 unknown feedback 中的 EV：`unknown_reopen` 只允许低状态温和恢复到最高 `10`，不会直接升到 `11`。
4. 更新完成后写回 `(src ToR, dst ToR)` 共享状态，后续所有同 ToR-pair 的 QP 都能复用。

这个规则的核心意图是：坏反馈快速生效，干净反馈逐步恢复，未知路径可以被重新打开但不能立刻成为最优路径。这样能避免路径因为短时 ECN 被永久排除，也避免 unknown 路径过快污染 strong-good 集合。

### 整体数据流

完整工作流如下：

1. 源端为待发送 packet 选择一个 EV。
2. 每一跳交换机按 EV 的对应段选择 ECMP 组内端口。
3. 队列超过 ECN 阈值时，交换机对 packet 打 ECN CE。
4. 目的 ToR 聚合来自同一 source ToR 的路径观测，周期性把 bitmap 写入 data packet。
5. 接收端把 data packet 上的 bitmap 和 pathid 带回 ACK。
6. 源端根据 ACK 更新 ToR-pair 共享 path state。
7. 后续 packet 优先使用 strong-good 路径；当 strong-good 不足时才逐级放宽到 usable/low 路径。

### 与 REPS 的主要差异

- REPS 是 per-connection circular buffer：每个 QP 缓存最近 clean EV，buffer 为空时随机探索。
- N-MRC 是 per-ToR-pair shared bitmap：同一 ToR-pair 的多个 QP 共享路径状态，不需要每 QP 存一份路径缓存。
- REPS 只知道 ACK 对应 EV 是否 clean；N-MRC 通过 ToR 聚合 bitmap，一次反馈可以更新多个 EV 的状态。
- REPS 对 clean EV 做一次性复用；N-MRC 对 EV 做 2-bit soft-state 管理，支持降级、逐步恢复和 unknown 温和复开。

### 协议承载与部署假设

N-MRC 的目标场景是 AI 训练/推理和高性能存储网络，端侧需要支持 packet-level multipath 带来的乱序接收，并具备 SACK/选择性重传或等价机制。当前实验运行在 RoCEv2 仿真器上，但 N-MRC 的 bitmap feedback 不应理解为可以直接塞进现有商用 RoCEv2 RNIC 的 BTH/ETH 头里。

在标准 RoCEv2 中，交换机可以改写的是 IP ECN CE 这类标准拥塞标记；BTH 是 RNIC 解析的 IB transport header，普通交换机临时构造或扩展 BTH/ACK payload，商用 RNIC 通常不会按 N-MRC 语义解析。用额外自定义控制报文也存在同样问题：除非 RNIC/firmware/driver 明确定义并接收这种报文，否则它只会成为普通 UDP/IP 或未知控制流，无法进入 RDMA fast path 的 ACK/重传/选路逻辑。

因此，N-MRC 的合理承载方式是 piggyback 在端侧传输协议已有的 ACK/feedback 机制上，而不是占用固定 BTH/ETH 头部。EV/pathid 可以像 MRC 一样放在交换机本来会参与 ECMP/hash 或 source-routing 的字段中；但反向的 path-state bitmap 应该走 ACK/SACK/NACK 或 congestion feedback，而不是假设交换机能临时扩展 BTH 且 RNIC 会自动理解。

- 对 RoCEv2 兼容部署：只能做研究原型或厂商扩展，需要 RNIC firmware/driver 支持新的 ACK metadata 或控制报文；否则只能退化为 ECN/CNP 粒度反馈，无法携带完整 bitmap。
- 对 UEC/UET 或类似新传输：假设协议本身支持乱序、SACK/选择性重传、可扩展拥塞信令或 ACK metadata，N-MRC bitmap 可以作为标准化/厂商扩展的 feedback 字段随 ACK/SACK/NACK 或 congestion signaling 返回。
- 对可编程 RNIC/SmartNIC：可以定义专用 feedback header 或 sideband control queue，但这已经是自定义传输/RNIC fast path 能力，不是透明 RoCEv2。

UEC/UET 和 OpenAI MRC 的做法都更接近这个假设：端侧协议/RNIC 本身承认 packet spraying 和乱序，接收端用选择性确认描述哪些 packet/path 状态需要源端处理；MRC 还会在反向 SACK/NACK 中回显 EV，并可携带端口/链路状态 bitmap 来让远端重映射 EV 集合。N-MRC 可以把 ToR 聚合出来的 path-state bitmap 放到同类 feedback 语义里；关键假设不是“RoCEv2 现成 BTH 足够大”，而是“端侧传输协议已经有可扩展的反馈报文，并且 RNIC fast path 会解析它”。

bitmap 体量取决于 path space。2-tier 8192 卡 leaf-spine 中，默认 `K=128`、每 ToR 上行路径数为 64，N-MRC 2-bit bitmap 为 `64 * 2bit = 16B`。这个大小对 UET/自定义 ACK metadata 是合理的，但对固定 RoCEv2 BTH/ETH 头部并没有天然容纳位置。若 path space 增大到 256，2-bit bitmap 为 64B，更需要明确的扩展 header 或分片/压缩策略。

端侧除 shared path bitmap 外，只需要很小的派生状态。每 QP 使用不同起始 cursor 和 stride 是有价值的，可以避免同一 ToR-pair 下大量 QP 同步扫同一批 EV；但这两个值可以由 `src ToR, dst ToR, qpn/flow_id, priority` hash 派生，不必为每 QP 存一份大状态。若硬件实现选择显式保存，64-path 场景中 cursor 和 stride 各约 6 bit，工程上按 2B/QP 预算即可。

## 测试场景

通用参数：

- 链路速率：400Gbps。
- 队列：`lossless_input_ecn`，ECN Kmin=20% queue、Kmax=80% queue。
- PFC：low/high threshold 为 20/80 packets。
- Host queue：`prio`。
- MTU：4096 bytes。
- 拥塞控制：支持 `DCQCN` 和 `DCQCN_variant`；当前 `ecmp`、`ops`、`reps`、`N-MRC` 主线先统一使用 `DCQCN_variant`。
- 主要指标：avg FCT、p99 FCT、p99.9 FCT、max FCT、CCT。

| 编号 | 场景 | 拓扑 | Flow size | 链路条件 | 当前批跑方案 |
| --- | --- | --- | --- | --- | --- |
| 1 | 健康网络场景 | 1024 nodes / 3-tier；128 nodes / 2-tier | 4MiB、8MiB、16MiB、32MiB | 全链路 400Gbps | `ecmp`、`ops`、`reps`、`N-MRC` |
| 2 | 非对称带宽场景 | 1024 nodes / 3-tier；128 nodes / 2-tier | 4MiB、8MiB、16MiB、32MiB | 3% ToR 上行链路降为半带宽 | `ecmp`、`ops`、`reps`、`N-MRC` |
| 3 | 背景流、热点场景 | TBD | TBD | TBD | 暂不展开 |
| 4 | 网络链路故障场景 | TBD | TBD | TBD | 暂不展开 |

非对称带宽场景中，脚本按 ToR 上行链路总数做 `ceil(total * 0.03)`。generated fat-tree 当前按节点数估算 ToR 上行总数：1024-node 场景取 31 条慢链路，128-node 场景取 4 条慢链路。

## 运行

先编译 RoCE 仿真器：

```bash
cd /home/user/workspace/csg-htsim
make -C sim
```

运行默认主线脚本。默认展开健康网络和非对称带宽两类场景，拓扑为 `1024:3,128:2`，flow size 为 `4,8,16,32` MiB：

```bash
python3 experiments/sglb_reps_dtor/run_literature_metric_compare.py
```

用环境变量缩小规模，例如只跑 128-node / 2-tier / 8MiB：

```bash
SCENARIO_TOPOLOGIES=128:2 SCENARIO_FLOW_SIZE_MIBS=8 \
python3 experiments/sglb_reps_dtor/run_literature_metric_compare.py
```

切到标准 DCQCN：

```bash
SCENARIO_CC=dcqcn python3 experiments/sglb_reps_dtor/run_literature_metric_compare.py
```

保留逐方案 stdout/dat 原始文件用于排查：

```bash
KEEP_RAW_OUTPUT=1 python3 experiments/sglb_reps_dtor/run_literature_metric_compare.py
```

GLB 参数对比脚本保留，用于比较不同 `Q(L), L(L), Q(R), L(R), B(R)` 系数组合：

```bash
python3 experiments/sglb_reps_dtor/run_glb_factor_compare.py
```

## 输出位置

主线脚本输出：

```text
experiments/sglb_reps_dtor/output/scenario_compare/
```

GLB 参数对比输出：

```text
experiments/sglb_reps_dtor/output_glb_factor_compare/scenario_compare/
```

核心结果文件：

- `summary.csv`：每个场景、每个方案的原始指标。
- `normalized.csv`：场景内相对最优值的归一化结果，越接近 1 越好。
- `per_flow.csv`：逐流 FCT 明细。
- `comparison_report.md`：自动生成的 Markdown 汇总报告。
- `scenario_plan.md`：脚本当前启用/预留场景列表。

## 核心代码

- `experiments/sglb_reps_dtor/run_literature_metric_compare.py`：主线场景生成、命令拼接、指标解析和报告生成。
- `experiments/sglb_reps_dtor/run_glb_factor_compare.py`：GLB 不同参数组合对比。
- `sim/datacenter/main_roce.cpp`：RoCE CLI 参数、LB 模式选择、N-MRC canonical 参数、EV 空间按拓扑自动校准。
- `sim/roce.cpp` / `sim/roce.h`：源端 `ecmp`、`ops`、`reps`、`N-MRC` 选路状态机和 ACK feedback 更新。
- `sim/rocepacket.h`：RoCE packet/ACK 上携带 pathid 和 N-MRC bitmap feedback。
- `sim/datacenter/fat_tree_switch.cpp` / `sim/datacenter/fat_tree_switch.h`：交换机 ECMP/AR/DRILL/GLB 转发、source-controlled pathid 分段映射、N-MRC feedback 生成。
- `sim/datacenter/fat_tree_topology.cpp` / `sim/datacenter/fat_tree_topology.h`：generated fat-tree 拓扑、bundle/radix 参数、慢链路注入。

## 清理策略

历史调参输出、旧 smoke、旧 N-MRC 消融和临时 GLB 参数搜索结果不再维护。本目录只保留 README、主线场景脚本和一个 GLB 参数对比脚本；输出目录可随时由脚本重新生成。
