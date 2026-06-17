# n-mrc 实验目录

本目录存放 n-mrc 相关的 htsim/RoCE 仿真实验脚本和本地输出。完整项目背景、构建方式和根标准场景表见仓库根目录 [README.md](../../README.md)。

## 主要文件

- `run_literature_metric_compare.py`：README 标准场景统一验证脚本，默认跑逐包方案集合并输出 CSV/report/图表。
- `run_glb_factor_compare.py`：GLB 参数对比辅助脚本。
- `run_packet_background_300g50.py`：packet-level LB 背景流压力场景脚本。
- `run_hash_entropy_demo.py`：hash/path entropy 小实验脚本。
- `output/`：本地实验结果目录，默认不提交。

## 根 README 标准场景

仓库根目录 README 的标准表当前定义三类场景：

| 场景 | 拓扑 | 链路条件 | Traffic | Flow size | 对比方案 |
| --- | --- | --- | --- | --- | --- |
| 健康网络 | 2048 nodes / 2-tier | 全链路 400Gbps | `tornado` / `permutation` | 支持 4/8/16/32MiB，默认启用 4/32MiB | `ecmp_rr` / `ops` / `rr` / `reps` / `n-mrc` / `mrc` / `adaptive-routing` / `drill` / `glb` |
| 非对称带宽 | 1024 nodes / 3-tier | `ceil(1024 * 3%) = 31` 条 ToR 上行半带宽 | `tornado` / `permutation` | 支持 4/8/16/32MiB，默认启用 4/32MiB | `ecmp_rr` / `ops` / `rr` / `reps` / `n-mrc` / `mrc` / `adaptive-routing` / `drill` / `glb` |
| 非对称带宽2 | 2048 nodes / 2-tier | `floor(2048 * 10%) = 204` 条 ToR 上行随机稀疏半带宽 | `tornado` / `permutation` | 支持 4/8/16/32MiB，默认启用 4/32MiB | `ecmp_rr` / `ops` / `rr` / `reps` / `n-mrc` / `mrc` / `adaptive-routing` / `drill` / `glb` |

通用口径为 `lossless_input_ecn` 队列、host `prio` 队列、MTU 4096B、400Gbps、hop/switch latency 0.5us、ECN/PFC 阈值 0.2/0.8 queue、RoCE RTO 70us、`DCQCN_variant`。

`run_literature_metric_compare.py` 是当前唯一主入口。默认展开 README 逐包方案集合、三类场景、两种 traffic 和 4/32MiB；也可以通过环境变量只开启某个场景、traffic 或 flow size。

非对称带宽2的 random-sparse 慢链路选择会先按 ToR 均匀分配慢链路数量，再在每个 ToR 内随机选具体上行；2048-node / 2-tier 的 204 条慢上行会分散到 64 个 ToR 上，每个 ToR 3 或 4 条。

## 已实现方案机制

这些方案按选路位置可以分为三类：source 端逐包选择 EV/pathid、source 端带反馈的逐包选择、以及交换机侧逐跳选择 next-hop。

| 方案 | 选路位置 | 机制简述 |
| --- | --- | --- |
| `ecmp` | source 固定 pathid + switch hash | 每条 flow 固定 pathid，交换机用 flow/pathid hash 映射到 ECMP next-hop。 |
| `ops` | source packet-level | 每个 data packet 随机选 EV/pathid；无反馈、无历史状态，是随机 packet spraying baseline。 |
| `reps` | source packet-level + clean ACK 反馈 | ACK 若未携带 ECN echo，source 把 ACK 上的 pathid 放入一个小型 clean-EV 缓存；后续优先消费缓存里的 clean EV，没有缓存时随机探索。 |
| `n-mrc` | source packet-level + destination ToR bitmap 反馈 | 主推方案。目的 ToR 按 `(src ToR, dst ToR)` 聚合 recent path 观测，把 ECN CE packet 对应 EV 标为 bad，并把紧凑 bitmap 搭在 ACK 上回传；source 端多个同 ToR-pair flow 共享该 bad bitmap，逐包扫描 EV 序列并跳过 recent-bad EV。 |
| `mrc` | source packet-level per-QP EV set | 独立 MRC 风格近似模型。每个 QP/source 维护 active/backup logical EV 集，active 上轮转；ECN/trim 把 EV 临时放入 cooling，non-trim NACK、SACK gap、RTO 把 EV 标为 failed，并从 backup/可重试 EV 中补 active。 |
| `glb` | switch packet-level | 每跳交换机把本地队列/利用率与 next-hop 周期导出的 GCN 下游快照合成 score，再量化成 quality bucket，在较好 bucket 内喷洒。 |
| `adaptive-routing` | switch packet-level 或 flowlet | 默认按本地队列拥塞比较在可用 next-hop 中选更空的端口；可用 `-ar_granularity flowlet` 切到 flowlet sticky。 |
| `drill` | switch packet-level | 每跳抽两个随机 next-hop 加一个 per-destination 历史候选，比较队列拥塞后选择最优，并更新历史候选。 |

### n-mrc：主推方案

n-mrc 把 EV/pathid 当作完整端到端路径组合的索引。2-tier 中主要对应 ToR 上行；3-tier 中会被 fat-tree 代码分段解释为 ToR uplink、Agg uplink、Core-down bundle 和 Agg-down bundle。

默认模式是二态 bad-cache：

- destination ToR 只对带 ECN CE 的 data packet 把对应 EV 标 bad。
- 非 ECN packet 不写强 clean 正反馈；未被报告 bad 的 EV 保持 unknown，可自然回到候选池。
- feedback 按 packet 数和最小/最大时间间隔触发，作为 bitmap 搭载到后续 ACK。
- source 按 `(src ToR, dst ToR)` 共享 bitmap；同一 ToR-pair 下多个 flow 可复用彼此看到的 bad path 信息。
- source 发包时使用 per-flow/per-priority 的确定性 EV 序列，步长会调到和 EV 空间互质；发送时扫描序列并跳过 recent-bad EV。

这和 `reps` 的关键区别是：`reps` 只复用自己 ACK 返回的 clean pathid，反馈粒度偏 per-flow；n-mrc 反馈的是 ToR-pair 共享的 bad bitmap，目标是用更少状态把拥塞/非对称路径信息扩散给同一对 ToR 间的多个 flow。

旧版 4-state n-mrc 仍可通过 `-nmrc_state_mode 4-state` 打开。它把 EV 状态编码为 strong-good、usable、suspect/low 和保留状态，发送时优先选择更高状态路径；当前默认实验使用更保守的二态 bad-cache。

### REPS

`reps` 是轻量反馈 baseline：

- source 维护一个固定大小的 clean-EV ring buffer。
- ACK 未带 ECN echo 时，ACK 上的 pathid 被写入 buffer。
- 发送新 packet 时优先取 buffer 里最早的 clean EV；buffer 空时随机选 path。
- 初始阶段可配置 warmup/explore packet 数，先随机探索后再依赖反馈。

它的优点是实现简单且能快速复用近期 clean path；局限是 clean 反馈主要来自本 flow 的 ACK，不能像 n-mrc 一样跨同 ToR-pair flow 共享，也不会显式维护 bad-path 避让窗口。

### MRC

`mrc` 是独立于 n-mrc 的 Multipath RC 风格近似模型，用来和论文式 MRC 思路对齐，而不是 n-mrc 的内部状态模式。

当前实现要点：

- 每个 `RoceSrc` 初始化自己的 logical EV 集，默认 active + backup，logical EV 再映射到 physical pathid。
- data packet 在 active EV 上 round-robin；达到 probe interval 时可用普通 data packet 近似 probe failed/unused EV。
- ACK clean 可恢复 probing EV；ACK ECN echo 把对应 EV 标为 `COOLING`，cooldown 到期后可回 active。
- trim NACK 被视为拥塞信号，标 `COOLING`；普通 NACK、SACK bitmap 缺口、RTO 被视为疑似故障，标 `FAILED`。
- active EV 不足时从 backup 或已可重试 EV 中提升，避免继续压在疑似坏路径上。

需要注意：这是 htsim 抽象模型，不实现真实 NIC wire format、SRv6/uSID、外部 Clustermapper 或生产控制面。更完整的实现边界见 [MRC_IMPLEMENTATION.md](../../MRC_IMPLEMENTATION.md)。

### GLB、Adaptive Routing 和 DRILL

这三类是交换机侧 baseline：

- `adaptive-routing` 只看本地候选端口拥塞，默认逐包决策，反应快但没有显式下游视野。
- `drill` 在随机候选和历史候选中选队列更短的 next-hop，是低状态逐跳随机化。
- `glb` 在本地 score 上叠加 next-hop 导出的 GCN 快照，并按 quality bucket 保留多个较优候选，从而在局部拥塞和下游拥塞之间折中。

它们与 source-controlled 的 `ops`/`reps`/`n-mrc`/`mrc` 不同：packet 本身不决定完整端到端 EV，而是每台交换机基于本地或邻居状态重新选择下一跳。

## 运行方式

构建仿真器：

```bash
make -C sim -j"$(nproc)"
```

运行 README 标准逐包方案测试：

```bash
python3 experiments/n-mrc/run_literature_metric_compare.py
```

只跑非对称带宽2的 8MiB 快速检查：

```bash
SCENARIO_SET=asym2_tor10pct_sparse \
SCENARIO_TRAFFICS=tornado \
SCENARIO_FLOW_SIZE_MIBS=8 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

保留每次仿真的原始输出：

```bash
KEEP_RAW_OUTPUT=1 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

比较 64-bit 与 128-bit SACK bitmap 覆盖度/开销时，保持其它参数和 seed 不变，只切换：

```bash
SCENARIO_RX_MODE=sp SCENARIO_SACK_BITMAP_BITS=64 \
python3 experiments/n-mrc/run_literature_metric_compare.py

SCENARIO_RX_MODE=sp SCENARIO_SACK_BITMAP_BITS=128 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

选择性开启场景、traffic、flow size 或方案：

```bash
SCENARIO_SET=healthy,asym2_tor10pct_sparse \
SCENARIO_TRAFFICS=permutation \
SCENARIO_FLOW_SIZE_MIBS=4,8,32 \
SCENARIO_SCHEMES=ops,reps,mrc,adaptive-routing,drill,glb \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

## 输出说明

标准输出目录中通常包含：

- `summary.csv`：每个场景、每个方案的原始指标。
- `normalized.csv`：场景内相对最优值的归一化结果。
- `per_flow.csv`：逐流 FCT 明细。
- `comparison_report.md`：自动生成的 Markdown 汇总报告。
- `scenario_plan.md`：脚本本次展开的场景列表。
- `*_packet_lb_slowdown.png/pdf`：README 风格归一化对比图，指标为 avg FCT、p99 FCT、p99.9 FCT。

使用 `KEEP_RAW_OUTPUT=1` 时，还会保留每个 scenario 的 `.cmd`、`.stdout`、`.dat` 和 `.cm` 文件。
