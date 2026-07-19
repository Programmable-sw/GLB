# 仿真历史改动

Last updated: 2026-06-26

这个文件记录已经落到仿真器里的历史改动。它不是逐轮对话日志，也不记录每次临时实验输出。重点是：队列怎么改、端侧反馈怎么改、CC 怎么改、默认参数怎么改，以及这些底座如何支撑 `mrc`、`dtor`、`stor` 和后续真正的 `n-MRC` 设计。

当前命名口径：

- `mrc` 是 MRC 文献复现 baseline。参考 `docs/papers/MRC.md` 和 `MRC_IMPLEMENTATION.md`。
- `dtor` 和 `stor` 是已经实现的两个探索分支。
- 最终主方案叫 `n-MRC`。当前代码里还没有独立的 `-lb n-mrc` / `LB_N_MRC` 接口，后面需要先设计架构。

## 1. 队列模型改动

### 1.1 composite queue

`composite_ecn_lb` 是当前 MRC / DTOR / STOR 的主要队列底座。它和 `lossless_input_ecn` 的语义不同：

- `lossless_input_ecn` 表示无损 RoCE/PFC 方向。链路侧有 lossless input queue 和 PAUSE 语义。
- `composite_ecn_lb` 表示有损或 trim 风格 RoCE 方向。data 低优先级，header/control 高优先级；队满时 data payload 可被 trim，控制报文继续优先通过。

对 composite 做过的关键修改：

- `CompositeQueue` 增加 RED 风格 ECN 区间，而不是只用一个硬阈值。
- `COMPOSITE_ECN_LB` 在非 ToR downlink 上设置：

```text
Kmin = 0.2 * queue
Kmax = ecn_thresh * queue
默认 ecn_thresh = 0.8
```

- 当队列低于 Kmin 时不标 ECN。
- 在 Kmin 到 Kmax 之间线性概率标 ECN。
- 高于 Kmax 时必标 ECN。
- ToR downlink 仍关闭 ECN，避免最后一跳 incast 被误当成可绕开的路径拥塞。
- trimming、header priority、低优先级 data queue 的语义没有被改掉。

这让 `composite_ecn_lb` 更接近“有损 RoCE + ECN + trimming”的底座。MRC、DTOR、STOR 都可以在同一队列模型下比较，避免一个方案吃 lossless/PFC，另一个方案吃 lossy/trim，最后结果不可比。

### 1.2 lossless / composite 有损队列

`lossless_input_ecn` 保留为无损 RoCE baseline。它适合看 PFC/ECN 下的传统 RoCE 行为，但不是当前 MRC 主线。

`lossy_input_ecn` 不是 Broadcom 原仓库里的队列类型，当前版本也不再保留为可选队列。后续有损 RoCE / trim 风格实验统一使用 `composite_ecn_lb`，这样 DTOR、STOR 和 MRC baseline 在同一个队列底座上比较。

历史上曾用纯 lossy + RED ECN 口径做过 bitmap/SACK 与 GBN 的对比。这类结论只能作为接收端重传机制的压力测试参考，不能直接等同于 `composite_ecn_lb` 下的主参数，因为 composite trim 队列的 loss profile 不一样。

`LosslessOutputQueue` 也补了 ECN 计数和 RED 阈值口径，方便统一输出 `QueueDiag`。这主要用于诊断，不改变 MRC 的路径机制。

### 1.3 队列诊断

`main_roce.cpp` 结束时会收集并打印 `QueueDiag`，用于看底层机制有没有被触发：

```text
lossless_overflows
lossless_ecn_marks
lossy_drops
lossy_ecn_marks
composite_trims
composite_drops
composite_ecn_marks
```

这些指标很重要。一个 LB 方案的 FCT 好坏不能单独看，要同时看它是否把 ECN、trim、NACK、RTO 打爆了。

## 2. RoCE 接收与 SACK/bitmap 重传

### 2.1 GBN 和 SP 两种接收模式

当前有两个接收模式：

```text
-roce_rx_mode gbn
-roce_rx_mode sp
```

`gbn` 接近 Broadcom 原始 htsim 抽象：收到乱序 data 后发无 bitmap NACK，并丢弃乱序包。这个模式会把 packet spraying 的正常乱序当成丢包，因此不适合作为 MRC、DTOR、STOR 的默认模式。

`sp` 是选择性重传模式。receiver 会缓存 out-of-order packet，并用 Lazy SACK 风格的 bitmap 告诉 sender 哪些 PSN 已经到达，哪些还缺。MRC、DTOR、STOR 默认都使用 `sp`。

### 2.2 RoceNack bitmap 字段

`RoceNack` 现在携带 SACK 信息：

```text
aPSN / ackno
bitmap_start_psn
bitmap_valid_length
sack_bitmap_low
sack_bitmap_high
sack_offset
has_sack
```

默认 SACK bitmap 是 64 bit：

```text
-roce_sack_bitmap_bits 64
```

也支持 128 bit：

```text
-roce_sack_bitmap_bits 128
```

64 bit 默认是为了对齐 OCP MRC 里的单个 SACK bitmap 字段。128 bit 是 htsim 里的覆盖度对照，会额外计入 high bitmap word 的控制开销。

### 2.3 offsetted SACK block

如果乱序 packet 已经超出当前 bitmap 覆盖范围，receiver 不会简单丢掉这个信息。它会移动 `bitmap_start_psn`，让最高优先级的乱序区间仍能落进 bitmap。这就是当前的 offsetted SACK block。

如果 `roce_ooo_window_pkts` 大于单个 bitmap bit 数，仿真器会分批报告，不会假装一次 bitmap 能覆盖无限窗口。

### 2.4 RxtPSN

sender 侧维护本地 `RxtPSN`，它不是包字段。作用是避免多个重复 SACK 对同一段缺口反复触发重传。

当前规则：

- 只对 `seq > RxtPSN` 的缺口触发新的 fast selective retransmission。
- 当 SACK 覆盖范围向前推进时更新 `RxtPSN`。
- 如果超过 OOO tolerance 或相关窗口没有推进，`RxtPSN` 会失效，允许下一轮选择性重传。

### 2.5 SP 默认参数

基础默认值：

```text
roce_rx_mode = gbn
roce_sack_bitmap_bits = 64
roce_ooo_us = 15
roce_ooo_window_pkts = 32
roce_nack_interval_us = 4
```

但当选择 MRC、DTOR、STOR 时，如果用户没有显式覆盖，仿真器会把接收模式切到：

```text
roce_rx_mode = sp
```

SP 模式下，如果用户没有手工指定 OOO tolerance，会根据 RTT 和 Kmax drain time 估一个更合适的值；如果没有指定 OOO window，会默认按 1 BDP 设置。

之前 lossy bitmap/SACK 调参里用过一组 balanced 参数：

```text
roce_ooo_us = 5
roce_nack_interval_us = 4
roce_ooo_window_pkts = 64
roce_sack_bitmap_bits = 64
```

这组参数只说明 lossy ECMP 压力下，SACK 比 GBN 更适合 packet spraying。它不能直接替代 MRC/STOR/DTOR 在 composite trim 队列下的默认参数。

## 3. 拥塞控制改动

### 3.1 标准 DCQCN

`-cc dcqcn` 是 rate-based 版本。发送端维护：

```text
dcqcn_current_rate
dcqcn_target_rate
dcqcn_alpha
dcqcn_bytes_since_increase
dcqcn_recovery_count
```

收到 ECN echo 时，当前实现直接走 CNP 处理：

```text
target_rate = current_rate
current_rate *= (1 - alpha / 2)
alpha = (1 - g) * alpha + g
```

rate increase 由 timer 或 byte counter 触发：

- fast recovery 阶段：`current_rate = (target_rate + current_rate) / 2`
- active increase 阶段：`target_rate += ai_rate`，再把 current rate 拉向 target rate

新增或暴露过的主要参数：

```text
dcqcn_g
dcqcn_initial_alpha
dcqcn_ai_mbps
dcqcn_min_rate_mbps
dcqcn_alpha_us
dcqcn_rate_us
dcqcn_cnp_us
dcqcn_byte_counter
dcqcn_fast_recovery_steps
dcqcn_nack_reaction
```

`dcqcn_nack_reaction` 有三种：

```text
cnp
ignore
rate_cut
```

`rate_cut` 是为了避免把所有 NACK 都当成强 CNP。它会做较温和的 rate cut，并重置 rate increase 计时。

### 3.2 dcqcn_variant

`-cc dcqcn_variant` 是 ACK-clock / cwnd 风格版本，更接近 MP-RDMA 里的 per-ACK window control。

核心规则：

```text
clean ACK: cwnd += 1 / cwnd
ECN ACK:   cwnd -= 0.5
NACK:      cwnd -= 1
```

它不维护 `current_rate / target_rate / alpha`。它靠 ACK 打开窗口，比 rate-based DCQCN 更适合 packet-level multipath spraying 的仿真主线。

### 3.3 flight cap

为了避免短流在反馈回来之前无限发送，DCQCN 和 dcqcn_variant 都有初始 flight cap。如果用户没有传 `-cc_iw_pkts`，默认设置为：

```text
cc_iw_pkts = ceil(0.4 * estimated_bdp_pkts)
```

这不是 DCQCN 论文的原始公式，但对离散事件仿真很重要。没有这个 cap，短 RTT 场景里会把队列瞬间打满，CC 反馈还没回来，结果已经被初始爆发污染。

### 3.4 各方案默认 CC

当前默认关系：

| LB | 默认 CC | 原因 |
| --- | --- | --- |
| `mrc` | `none` | MRC 文献复现里 ECN 主要作为 path avoidance 信号，不默认叠加 DCQCN。 |
| `dtor` | `dcqcn_variant` | DTOR 是探索分支，需要和 RoCE 端侧窗口控制一起跑。 |
| `stor` | `dcqcn_variant` | STOR 同 DTOR，默认和 composite + SP + variant 绑定。 |
| 普通 baseline | `dcqcn_variant` | 当前全局默认更适合作为 packet-level LB 对照。 |

标准 DCQCN 仍保留为补充 baseline，尤其用于说明 rate-based CC 与 ACK-clock multipath 的差异。

## 4. source-controlled EV / pathid 底座

### 4.1 pathid 作为 EV 抽象

当前 htsim 不模拟真实 RoCEv2 UDP source port、IPv6 flow label 或 SRv6/uSID。所有端侧 entropy 都抽象为：

```text
RocePacket::pathid
```

对 `dtor`、`stor`、`mrc`、`rr`、`ops` 来说，source 端逐包写 `pathid`，fat-tree 交换机再按拓扑层级把 pathid 映射到当前 stage 的 ECMP next-hop。

### 4.2 path count 自动校准

当启用 source-controlled LB 时，仿真器会根据 fat-tree 拓扑计算实际 path combination。如果用户传的 `-paths` 和拓扑不一致，会把 path entropy size 调到可用 path count。

例如 2-tier / 2048 nodes 场景里，实际 source-controlled path count 会校准到 64。这个值也就是 STOR 里 EV set 的默认大小。

### 4.3 RR / DTOR / STOR 共用的确定性 EV 序列

`rr`、`dtor`、`stor` 都使用 per-flow / per-priority 的确定性 EV 序列：

- 起点由 `src`、`dst`、`flow_id`、priority 混合得到。
- stride 会调整到与 path space 互质。
- 单个 flow 可以遍历整个 EV 空间。

区别在于：

- `rr` 直接按序列发，不看反馈。
- `dtor` 扫描序列时跳过 recent-bad bitmap 里的 EV。
- `stor` 按 EV level 权重选择，不是简单跳过。

## 5. LB 方案和底层机制的耦合

### 5.1 MRC 文献复现 baseline

入口：

```text
-lb mrc
```

默认底座：

```text
queue_type = composite_ecn_lb
roce_rx_mode = sp
roce_sack_bitmap_bits = 64
cc = none
ecn_thresh = 0.8
mrc_active_paths = 256
mrc_backup_paths = 256
mrc_min_active_paths = 16
mrc_ecn_cooldown_us = 20
mrc_failed_retry_us = 100
mrc_probe_interval_pkts = 256
```

MRC 的路径状态是 per RoCE source/QP 的 logical EV set：

- active EV 参与逐包 spraying。
- backup EV 用来补 active。
- ECN ACK 把 EV 标为 `COOLING`。
- TRIM NACK 也作为拥塞信号，标 `COOLING`。
- 普通 NACK、SACK gap、RTO 标 `FAILED`。
- probe 用普通 data packet 近似，ACK 后恢复。

它是 MRC 文献复现 baseline，不是最终 n-MRC，也不是当前第三探索分支。

### 5.2 DTOR 探索分支

入口：

```text
-lb dtor
```

默认底座：

```text
queue_type = composite_ecn_lb
roce_rx_mode = sp
cc = dcqcn_variant
ecn_thresh = 0.8
```

反馈节奏：

```text
dtor_feedback_pkts = clamp(path_space / 2, 32, 128)
dtor_feedback_min_us = 5
dtor_feedback_max_us = 20
```

机制：

- destination ToR 聚合到达 packet 的 ECN CE。
- 看到某 EV 上有 ECN CE，就把该 EV 标为 bad。
- 到达 packet window 或时间窗口后，把 bad bitmap piggyback 到 ACK。
- source 端按 `(src ToR, dst ToR)` 共享该 bitmap。
- 同一 ToR-pair 下多个 flow 可复用彼此看到的 bad path 信息。

当前已有诊断：

```text
dtor_feedback_acks
dtor_feedback_zero_bits
```

还需要补：feedback count、bitmap 密度、源端跳过 bad EV 的次数。

### 5.3 STOR 探索分支

入口：

```text
-lb stor
```

默认底座：

```text
queue_type = composite_ecn_lb
roce_rx_mode = sp
cc = dcqcn_variant
ecn_thresh = 0.8
```

反馈节奏：

```text
stor_feedback_pkts = clamp(path_space / 2, 32, 128)
stor_feedback_min_us = 5
stor_feedback_max_us = 20
stor_feedback_on_trim = true
```

STOR 不反馈 bad bitmap，而是在 source ToR 上为每个 `(src ToR, dst ToR, ev_index)` 维护：

```text
score
ecn_acc
trim_acc
level = GOOD / DEGRADED / BAD / AVOID
```

当前默认使用优化后的 `balanced/current` 公式：

```text
Clean ACK:
  ecn_acc  = ecn_acc  - (ecn_acc  >> 3)
  trim_acc = trim_acc - (trim_acc >> 2)
  score    = min(score + 4, 255)

ECN ACK:
  ecn_acc  = ecn_acc  - (ecn_acc  >> 3)
  trim_acc = trim_acc - (trim_acc >> 2)
  ecn_acc  = min(ecn_acc + 24, 255)
  penalty  = 16 + (ecn_acc >> 3)
  score    = max(score - penalty, 0)

TRIM NACK:
  ecn_acc  = ecn_acc  - (ecn_acc  >> 3)
  trim_acc = trim_acc - (trim_acc >> 2)
  trim_acc = min(trim_acc + 48, 255)
  penalty  = 48 + (trim_acc >> 2)
  score    = max(score - penalty, 0)
```

等级映射：

```text
GOOD      score >= 240
DEGRADED  score >= 160
BAD       score >= 80
AVOID     otherwise
```

端侧权重：

```text
GOOD / DEGRADED / BAD / AVOID = 8 / 3 / 1 / 0
```

这套参数已经比最初公式更敏感：一个全新 EV 收到一次 ECN 会从 GOOD 掉到 DEGRADED，但不会立刻完全禁用。TRIM 惩罚更重。

当前需要继续补的点：

- STOR 专用诊断计数。
- 8bit、5bit、4bit 的量化对比。
- 不同规模、path count、flow size、trim 压力下的鲁棒性。

### 5.4 REPS baseline

`reps` 维护 clean-EV ring buffer。ACK 没有 ECN echo 时，source 把 ACK 上的 pathid 写入 buffer，后续优先复用。

已修正 warmup 语义：

- 新连接或 idle 连接先发约 1 BDP packet 做随机探索。
- warmup 没结束前，即使 buffer 里已经有 clean EV，也不提前消费。
- 这更接近 REPS 论文的探索逻辑。

### 5.5 SGLB / AR / DRILL baseline

这三类是交换机侧 baseline，不是 source-controlled EV 方案：

- `adaptive-routing` 默认逐包，按本地队列选择 next-hop。
- `drill` 随机抽样候选 + 历史候选，再按队列选最优。
- `sglb` 使用本地队列/利用率 + next-hop 导出的下游 GCN snapshot，量化成 quality bucket 后选路。

SGLB 相关底层改动：

- 增加 GCN snapshot export / aging。
- 增加 neighbor availability hook，用于 failed-link 场景。
- 增加 `quality_levels`、`quality_bucket`、`min_choices`、`downstream_weight`、local damping 等参数。
- 修复 queue utilization 采样，让 composite queue 的 busy time 能被 SGLB 看到。
- 增加固定链路 ON/OFF background source，用于模拟 SGLB 论文式背景压力，而不是普通 RoCE background flow。

这些 baseline 会影响论文对照，但不应和最终 n-MRC 的端侧机制混成一个方案。

## 6. 参数和实验口径

### 6.1 标准中等规模对比

当前 README 标准脚本在 `experiments/n-mrc/run_literature_metric_compare.py`。默认比较：

```text
ecmp_rr
ops
rr
reps
dtor
stor
mrc
adaptive-routing
drill
sglb
```

默认场景包括健康网络、3-tier 慢 ToR uplink、2-tier 稀疏慢 ToR uplink。默认 traffic 是 tornado / permutation，flow size 默认跑 4MiB 和 32MiB。

### 6.2 MRC 小规模校准参数

MRC 文献复现的内置默认是 256 active + 256 backup。但在小拓扑里，实际物理 path 少，过多 logical EV 会 alias 到同一 physical path。

小规模展示中曾用：

```text
mrc_active_paths = 16
mrc_backup_paths = 16
mrc_ecn_cooldown_us = 5
ecn_thresh = 0.8
```

这只是小规模 htsim 校准点，不是 MRC 论文或最终 n-MRC 的通用参数。

### 6.3 STOR profile 对比

STOR 旧公式 `original` 保留为对照入口，但默认是 `balanced/current`。之前 1024 nodes / 3-tier / tornado / seed=13 / 3% ToR uplinks 半带宽样例里，`balanced/current` 相对旧公式主要改善 tail：

```text
4MiB: P99 -8.1%, P99.9 -9.5%, CCT -13.2%, ECN marks -85.1%
8MiB: P99 -8.6%, P99.9 -11.7%, CCT -15.0%, ECN marks -81.1%
```

这说明更敏感的 ECN 降权有价值，但仍需要多 seed、多 traffic 和更低 bit 量化实验支撑。

## 7. 当前还没有做完的底层问题

- 还没有真正的 `-lb n-mrc` 接口。下一步需要设计 n-MRC 的状态机和数据流。
- STOR 的 8bit score / acc 是否必要还没证明，需要 8bit、5bit、4bit 对比。
- STOR 缺少专用诊断：反馈次数、每次变化 EV 数、level 驻留比例、EV 选择分布。
- DTOR 缺少源端跳过 bad EV 次数和 bad bitmap 密度统计。
- NSCC-lite 还没实现。它应先作为独立 `-cc nscc_lite` baseline，不要默认绑定到 MRC 或 STOR。
- AR baseline 的边界还需要写清楚，尤其是最后一跳拥塞、反向 ACK/NACK 拥塞和非对称慢链路。

## 8. 读代码时的入口索引

主要文件：

```text
sim/datacenter/main_roce.cpp
  CLI、默认 queue/RX/CC/LB 参数、canonical 输出、QueueDiag/RoceDiag

sim/datacenter/fat_tree_topology.cpp
  queue 创建、COMPOSITE_ECN_LB 的 Kmin/Kmax、slow link 注入

sim/compositequeue.cpp / sim/compositequeue.h
  composite queue、RED ECN、trim、priority header queue

sim/queue_lossless_output.cpp / sim/queue_lossless_output.h
  lossless output queue 的 RED ECN 和计数

sim/roce.cpp / sim/roce.h
  source 端 LB、RoCE SP/GBN、SACK/RxtPSN、DCQCN、DTOR/STOR/MRC 状态机

sim/rocepacket.h
  RoCE ACK/NACK/SACK、DTOR bitmap、STOR feedback 字段

sim/datacenter/fat_tree_switch.cpp / sim/datacenter/fat_tree_switch.h
  ECMP/pathid 分段映射、DTOR/STOR feedback、SGLB/AR/DRILL switch-side 逻辑

experiments/n-mrc/run_literature_metric_compare.py
  当前标准中等规模实验入口
```

相关说明文档：

```text
docs/papers/MRC.md
  MRC 文献整理

MRC_IMPLEMENTATION.md
  当前 -lb mrc 文献复现 baseline 的稳定实现说明

experiments/n-mrc/README.md
  已实现探索分支和 baseline 的机制说明

stor_optimize.md
  STOR 计分、调参和 FCT 样例记录

todo
  下一步还没探索完的任务
```
