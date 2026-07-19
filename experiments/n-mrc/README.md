# n-MRC 实验分支说明

这里记录当前实验脚本里会跑到的负载均衡方案。原来的 full-snapshot `n-mrc` 已改名为 `netaware`；新的 `n-mrc` 是端侧 EV 轮询与源 Leaf SGLB 换路融合方案。`mrc` 是 MRC 文献复现 baseline。

## Canonical six 三 seed 对比

`run_nmrc_canonical_six_compare.py` 固定比较默认
`n-mrc encoded + better_ge3`、NetAware、SGLB、逐包 AR、MRC 和 REPS。
场景为健康 permutation、非对称 permutation、WebSearch 80% proxy、
P16/256 MiB 背景关/开，以及 P4/64 MiB 背景关；固定 seeds 为
`13,29,47`。同一 `(scenario, seed)` 的六个方案严格复用一份 traffic
matrix。前三个场景使用 p99 FCT，后三个 All-to-All 场景使用 CCT；排名先在
每个 `(scenario, seed)` 内除以该块最优值，再计算几何平均。

运行命令：

```bash
python3 experiments/n-mrc/run_nmrc_canonical_six_compare.py \
  --seeds 13,29,47 --workers 3 --timeout 3600 \
  --out experiments/n-mrc/output/nmrc_canonical_six_default_p8_3seed
```

runner 会按 simulator、traffic 和完整命令的 SHA-256 指纹复用已完成单元，
并拒绝非 canonical seed、重复或缺失的完成 flow ID、不完整的 Exact+Bounded
诊断、非正 primary metric 以及不完整的 108-cell 结果矩阵。三个 All-to-All
场景统一使用 `100000us` 安全截止；只读取已完成 CCT，不改变完成时刻。
FastCNP 即使在对应 QP 完成后才物理到达，仍计入 `fastcnp_arrived` 并释放报文，
同时单列 `fastcnp_after_done`；这类报文不再启动 EV cooldown。

## `ecmp`

Source 端在 flow 初始化时给该 flow 固定一个 `pathid`。交换机转发时在当前 ECMP next-hop 集合里用 `freeBSDHash(flow_id, pathid, hash_salt)` 取模选端口。同一 flow 的 packet 默认保持同一个 source `pathid`，路径变化主要来自每跳交换机的 hash 映射，而不是 source 逐包换 EV。

## `ecmp_rr`

交换机侧逐跳 round-robin。每台交换机维护本地 `_crt_route` 游标，在当前目的地址对应的 ECMP next-hop 集合中轮转选择端口；游标走过若干轮后会重新打乱候选端口顺序。`RR_ECMP` 模式下 ToR 做 round-robin，非 ToR 层仍退回 hash ECMP。

## `ops`

Source 端逐包随机喷洒。每次发送 data packet 时直接从 `path_space` 中随机选一个 EV/pathid 写入 packet，没有 ACK 反馈状态、没有 bad/clean 缓存，也不保证单个 flow 均匀遍历全部 EV。

## `rr`

Source 端逐包确定性轮转 EV/pathid。每个 flow、每个 priority 用 `src/dst/flow_id/priority` 混合出起点和步长，步长会调整到与 EV 空间互质；后续 packet 按这个序列前进，因此单个 flow 可以遍历整个 EV 空间。该方案不使用拥塞反馈，只提供比随机 `ops` 更稳定的覆盖。

## `reps`

Source 端维护一个固定大小的 clean-EV ring buffer。ACK 如果没有 `ECN_ECHO`，source 就把 ACK 携带的 `pathid` 写入 buffer；后续发送 packet 时优先取 buffer 里尚未消费的 clean EV。buffer 为空时退回随机选 path；可配置 warmup packet 数，在 warmup 阶段先随机探索。

## `avail`

`avail` 使用 source-ToR 生成的 ToR-pair bad bitmap。Source 端逐包选择 EV/pathid；source ToR 按 destination ToR 聚合返回反馈，ACK 带 ECN 或 TRIM NACK 时都会把对应 EV 标为 bad。达到 packet 数或时间触发条件后，把整个窗口的 bitmap 放进 ACK 反馈给 source NIC。

ECN+TRIM 是默认语义；显式 `-avail_ecn_only` 仅用于恢复旧 ECN-only 行为的消融实验。两种模式都不改变反馈周期、窗口后 all-GOOD 重置或全部 bad 时的保活 fallback。512-node、seeds 13/29/47 消融中，ECN+TRIM 对 healthy/degraded/mixed 无影响，path-hotspot p99 中位数改善 `0.04%`，incast p99 中位数改善 `2.0%`但不同 seed 方向不一致。结果见 `output/avail_trim_bad_ablation_512/avail_trim_bad_ablation_for_gpt.md`。

Source 端按 `(src ToR, dst ToR)` 只保存一份 availability bitmap，同一 ToR-pair 下不同 flow 直接读取这份共享反馈，不再复制 per-QP bitmap。每个 QP 只维护一个 selection counter，用 `src/dst/flow_id/priority/profile_version/epoch` 对 `0..P-1` 做可随机访问的虚拟 permutation；bitmap 中 bad 的 EV 被跳过，全部 bad 时才做保活 fallback。因此逻辑路径状态是 `P bits / ToR-pair`，每 QP selector 状态为 `O(1)`。

Avail 默认采用 fixed5：packet trigger 为 `1`，普通反馈和 TRIM 反馈都按固定 `5us` 节奏发布。旧的 bounded-window 配置保留为显式兼容入口：`-stor_feedback_pkts 32 -stor_feedback_min_us 5 -stor_feedback_max_us 20 -stor_trim_feedback_min_us 5`。因为 Avail 和 Grade 共用 STOR feedback 管线，这组参数对两者都生效；NetAware 使用独立 snapshot 接口，新 n-MRC 不使用周期 feedback interval。

## `grade`

`grade` 中 source ToR 根据 ACK 和 TRIM NACK 给每个 EV 维护一个多级状态。端侧仍然逐包填 EV，交换机仍然按 EV/hash 转发；Grade 只负责观测和反馈，不替端侧选路。代码内部继续复用 STOR 状态和反馈类型。

STOR 状态按 `(src ToR, dst ToR, ev_index)` 共享。同一对 ToR 之间的多个 flow 会使用同一份 EV 状态。当前实现里，端侧预先拥有一组 EV set；每个 EV 可以映射到一条稳定路径。真实 RoCE/NIC 里可以把 EV 理解成 UDP source port、IPv6 flow label 或其它会进入 ECMP hash 的 entropy 字段。仿真里仍用 `pathid` 表示。

Avail 和 Grade 共用 STOR feedback 管线，当前默认统一为 fixed5：

```text
Avail feedback_pkts = 1
Grade feedback_pkts = 1
stor_feedback_min_us = 5
stor_feedback_max_us = 5
stor_trim_feedback_min_us = 5
```

普通 ACK/ECN ACK 仍按 packet trigger 或 interval piggyback 整个 EV level vector。默认下 TRIM 不再绕过 5us 下限，因此三个反馈边界一致。反馈节奏与计分惩罚仍是两件事：`original`/`balanced` 让 TRIM 比 ECN 扣分更重，`simple` 则让二者使用相同扣分。

fixed5 只统一 Avail 和 Grade 的反馈触发节奏，不改变 packet-aging、全表反馈、评分公式或 endpoint selector 语义。共享 STOR 接口仍可显式切换到 `32 pkt + 5--20us`；NetAware 的 full snapshot 固定为 packet trigger 1、5us，新 n-MRC 则由实际换路事件触发通知。

Grade 默认只更新一个逻辑 4-bit score；显式 `-grade_complex_score` 时才恢复三个 8-bit 状态：

- `score`：路径信用分，越大越好。
- `ecn_acc`：complex mode 的近期 ECN 累计量。
- `trim_acc`：complex mode 的近期 TRIM 累计量。

当前默认是筛选出的 simplified p4 scorer。用户构想图对应的复杂公式仍可通过内部调试 profile `-stor_score_profile original` 复现：

```text
Clean ACK: score += 4
ECN ACK:   ecn_acc += 16; penalty = 8  + (ecn_acc  >> 3)
TRIM NACK: trim_acc += 32; penalty = 32 + (trim_acc >> 2)
levels:    GOOD >= 200, DEGRADED >= 120, BAD >= 50, AVOID < 50
```

显式 `-grade_complex_score` 使用原来的 `balanced` complex scorer。它采用 `score + ecn_acc + trim_acc` 三字段结构和 shift-decay：

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

`score` 再映射成四个等级：

```text
GOOD      score >= 240
DEGRADED  score >= 160
BAD       score >= 80
AVOID     otherwise
```

端侧不重新计算 score，只消费 ToR piggyback 回来的等级。当前统一使用 scale-invariant shuffled ticket bucket，桶大小为 `K = 4 * path_count`，默认权重是：

```text
GOOD / DEGRADED / BAD / AVOID = 4 / 2 / 1 / 0
```

bucket 按路径相对权重做 largest-remainder 分配；绝对同比缩放的权重会得到相同 ticket allocation。未打乱的 base bucket 只在 `(src ToR,dst ToR)` profile 中保存一次。每个 QP 不再保存自己的 K 项 bucket，而是用一个 selection counter 和 `src/dst/flow_id/priority/profile_version/epoch` 虚拟打乱 bucket 下标并按需查询。AVOID 权重为 0，但 graded STOR 每 `K` 次选择轮转 probe 一个 AVOID EV，让 packet-aging profile 仍有机会收到 clean 反馈并恢复。

公开实验名 `avail` 对应内部 binary profile：source ToR 根据 ECN ACK 或 TRIM NACK 把对应路径置 bad，反馈窗口后清空 bad bitmap；端侧对共享 bitmap 做 unweighted virtual permutation。`-avail_ecn_only` 仅保留旧 ECN-only 行为用于消融。公开实验名 `grade` 默认对应 simplified p4 graded profile 和 weighted virtual permutation；`-grade_complex_score` 才切回 balanced complex scorer。

Grade 默认 simplified profile 为：

```text
-stor_score_profile simple
-stor_simple_score_params clean_gain congestion_penalty good degraded bad

default simple parameters:
  score range = 0..15
  clean ACK   = +1
  ECN/TRIM    = -4
  levels      = 12 / 7 / 3
```

`simple` 不维护 `ecn_acc`/`trim_acc`，恢复仍由 clean business-packet feedback 驱动，并没有删除 packet aging。512-node、7 workload、3-seed 对比中，`penalty=4, thresholds=12/7/3` 相比图片中的 `original` 明显降低 degraded/path-hotspot 尾部；相比 balanced，它改善 path hotspot 和 degraded-permutation 极端尾部，但 degraded-tornado 略退、32:1 incast 更差。基于当前机制简化目标，它已升为 Grade 默认，balanced 只作为显式 complex reference。历史筛选结果见 `output/stor_simplified_score_comparison/stor_simplified_score_comparison_for_gpt.md`。

### STOR aging profiles

STOR 当前可以用 `-stor_aging packet|time_ewma|hybrid` 比较三种恢复模型。

`packet` 是 baseline：EV score 只随 clean ACK、ECN ACK、TRIM NACK 更新。路径不收到包时不会自动恢复，适合长期慢链路，但瞬时 burst 后可能恢复偏慢。

`time_ewma` 在处理反馈前按时间衰减坏证据并恢复 score，参数为 `-stor_time_ewma_us ecn trim score`。它用来验证自动时间恢复是否会让长期坏路径过早回流。

`hybrid` 保留 packet 信号作为强证据，时间只负责 BAD/AVOID hold-down 过期后的低频 probe 资格，参数为 `-stor_hybrid_hold_us bad avoid` 和 `-stor_hybrid_probe interval_pkts clean_promote`。AVOID 到期后最多先回到 BAD，依靠低权重探测和 clean 包继续恢复。

诊断输出包括：

```text
RoceDiag ... stor_selected_good/degraded/bad/avoid=...
StorDiag stor_min_score=... stor_avoid_entries=... stor_avoid_exits=...
         stor_clean_signals=... stor_ecn_signals=... stor_trim_signals=...
```

小规模验证矩阵：

```bash
python3 experiments/n-mrc/run_stor_aging_compare.py
```

该脚本同时跑长期慢 uplink 和短 burst 恢复场景，并在 `summary.csv`/`comparison_report.md` 中列出 FCT、CCT、ECN/TRIM/NACK 和 STOR 最低分数/AVOID 进出次数。

## `netaware`

`netaware` 是原来的 full path-profile snapshot 机制：source leaf 为 `(src leaf, dst leaf)` 计算所有候选 `path_id` 的四级路径 profile，并通过 ACK 的 `netaware_feedback` 字段完整同步给 NIC。相比 `mrc` 让端侧按 ACK/NACK/RTO 管理 active/backup EV；`netaware` 让 leaf 交换机维护 ToR-pair 的路径状态，并把结果反馈给 NIC。

1.利用交换机的周期端口 snapshot 减少端到端信号的滞后性；
2.多流/多 NIC 共享交换机视图的路径表，让每个流有即时回避拥塞能力而非牺牲业务包的探索；
3.实现端侧可见的 EV-PATH 映射，协助 cc 使网络不再黑盒。

仿真默认配置是：

```text
queue_type = composite_ecn_lb
cc         = dcqcn_variant
rx_mode    = sp
ecn Kmax   = 0.8 * queue
EV/pathid  = source 端逐包填写，交换机按单 EV 哈希
```

EV 空间仍然用 `pathid` 表示。没有显式 `-paths` 时，source-controlled LB 会按拓扑自动校准 EV 数量：二层网络下是 `leaf uplinks * spine downlinks`。例如 2k 节点、64 条二层路径时，EV set 大小就是 64。

交换机分工：

```text
spine:
  每 5us 更新面向 destination 的下游端口 export snapshot。

leaf:
  每 1us 更新本地 leaf-to-spine 端口 snapshot。
  对每个 dst ToR 和每个 EV 计算 path level。
  一个 EV 对应的路径由两段队列组成：
    1. 本 leaf 到 selected spine 的 leaf uplink
    2. selected spine 到 dst leaf 的 spine downlink
  默认先把两段队列分别映射为 queue pressure，再用 noisy-OR 耦合为整条路径的 score，最后量化为 GOOD/DEGRADED/BAD/AVOID。
```

当前阈值和反馈节奏是：

```text
netaware_score_mode              = sglb_quantized
netaware_path_coupling           = noisy_or
netaware_score_q_range           = 0.20 / 0.80
netaware_score_util_range        = 0.90 / 1.00（接口保留，默认权重为 0）
netaware_score_weights           = 0.50 / 0.50 / 0.00 / 0.00
netaware_score_level_thresholds  = 0.10 / 0.40 / 0.60
netaware_level_weights           = 4 / 2 / 1 / 0
netaware_weight_adaptation       = good_share_cap
leaf local update            = 1us
spine export update          = 5us
feedback_pkts               = 1
feedback min/max            = 5/5us
```

score 公式为：

```text
local_hop  = local_q_pressure
remote_hop = remote_q_pressure
path_score = 1 - (1 - local_hop) * (1 - remote_hop)
```

queue pressure 按 composite RED 的 `Kmin=0.20`、`Kmax=0.80` 线性归一化。默认不用 30us average-utilization 参与评分，因为它对 30--40us 短流反应过慢；CLI 仍可显式设置 util 权重做消融。`additive` 和 `bottleneck` coupling 也保留为诊断模式。

当前状态传播采用与 SGLB 相同的周期缓存抽象：spine 首次按需建立 destination export，随后每 5us 更新；leaf 只读取该缓存，不再直接读取 spine 的实时 queue。仿真仍通过交换机对象共享 cache，不生成实际控制报文，因此没有计入 spine-to-leaf 通告的传播、排队、丢失和带宽成本。

量化规则固定为四级：`score < degraded -> GOOD`，`score < bad -> DEGRADED`，`score < avoid -> BAD`，`score >= avoid -> AVOID`。`worst_hop` 仍保留为兼容模式，但新的连续评分目标和 512-node 参数实验都应使用 `sglb_quantized`。

`netaware` 在 ACK 回到源端 leaf、准备下发给源 NIC 时重新计算完整 EV/path profile，并把四级 level vector piggyback 到 ACK 的 `netaware_feedback` 字段上。STOR 和 NetAware 不共享反馈语义：STOR 是 endpoint/business-packet observation based，可能只观察到实际经过的部分 EV；NetAware 是 switch/peer-state based，每次反馈都是覆盖全部 candidate path_id 的 full snapshot。

NIC 端只消费 NetAware snapshot，不直接看交换机队列。端侧按 `(src ToR, dst ToR)` 共享唯一 EV level profile 和 base weighted bucket，同一 ToR-pair 下所有 flow/QP 复用同一份反馈和 ticket allocation。收到新的 `netaware_feedback` 后，NIC 直接用最新完整 profile 替换旧 profile，不做 STOR 那类 hold/aging/probing/partial update。每个 QP 只维护 selection counter，通过虚拟 permutation 打乱共享 bucket 下标。

默认 selector 仍是 `shuffled_bucket`，默认初始等级基数仍是 GOOD/DEGRADED/BAD/AVOID = `4/2/1/0`，默认 weight adaptation 为 GoodCap（`good_share_cap`）。GoodCap 在 GOOD 路径稀少、单条 GOOD 的归一化概率超过 `(1 + nGOOD/P) / P` 时，把 4210 分布向全路径均匀分布软化；全 GOOD 时不改变均匀 spraying。固定 4210 仅作为显式消融保留：

```text
固定4210:
  -netaware_weight_adaptation off
```

除 `off` 和 `good_share_cap` 外不再提供其它软加权模式。

如果所有路径总权重为 0，则虚拟遍历完整路径空间做保活 fallback，避免死锁。GoodCap 的 seed-13 六场景基数扫描仍以 4210 的最坏场景保护最好，因此没有把基数改成 6210；完整记录见 `output/nmrc_goodcap_base_sweep_128_seed13/nmrc_goodcap_base_sweep_for_gpt.md`。

共享虚拟 selector 的历史 512-node、三 seed 对比见 `output/nmrc_shared_virtual_selector_comparison/nmrc_shared_virtual_selector_comparison_for_gpt.md`。这些历史目录名中的 `nmrc` 指现在的 NetAware；MRC 没有参与这次状态重构，仍保留 per-QP permutation、active/backup 和 EV 状态。

当前 1us leaf-local/5us spine-export 周期缓存模型与 ECMP-RR、OPS、REPS、MRC、SGLB、AR 的 512-node 七负载对比见 `output/nmrc_periodic_cache_packet_lb_512/nmrc_periodic_cache_packet_lb_512_for_gpt.md`。

代码入口上，NetAware 使用独立的 `LB_NETAWARE`、NetAware selector 和 ACK snapshot；Avail 和 Grade 都复用 `LB_STOR`、`choose_stor_path` 和 source-ToR 观测管线，分别选择 binary bitmap 与 graded weighted profile。

NetAware 的 feedback cadence 固定在运行时实现中，不再保留反馈周期扫描入口。修改端侧选路或反馈实现后必须先运行 `make -C sim`，再链接 `sim/datacenter/htsim_roce`，避免使用陈旧的 `roce.o`。

早期二值 availability 只是 NetAware 的第一版基线。当前 NetAware 使用 GOOD/DEGRADED/BAD/AVOID 四级 profile。

## `n-mrc`

新的 `n-mrc` 不再接收 5us full snapshot。每个 QP 建立自己的 EV set，按确定性打乱后的顺序逐包轮询；收到 PATH_REROUTE 通知后只把该 EV 跳过一个完整轮次。重复的在途通知不延长当前冷却，冷却结束后若下一次使用仍被源 Leaf 换路，会产生新的通知和新一轮冷却。

支持三种 EV 构造：

- `encoded`：使用 `min(P,32)` 个路径唯一编码。`P <= 32` 时各 QP 成员相同但顺序独立打乱；`P > 32` 时各 QP 独立抽取 32 条路径。
- `random_matched`：生成 `min(P,32)` 个互异的 16-bit EV，再由 flow+EV hash 映射到物理路径，允许多 EV 映到同一路径。
- `random32`：固定生成 32 个互异的 16-bit EV。它只在 `P < 32` 时与 `random_matched` 有区别。

源主机发出的 data packet 到达第一跳 Leaf 后，Leaf 复用 SGLB 的周期队列状态与四级量化。若原 EV 路径存在严格更高等级候选，则按配置执行：

- `any_better`：只要存在一条严格更优路径就换路；
- `better_ge3`：严格更优路径至少有 3 条才换路。

当前默认是 `encoded + better_ge3 + FastCNP on`。P=8、seeds 13/29/47 的六负载缩减矩阵中，`better_ge3` 相比 `any_better` 降低热点 p99 并显著减少路径通知；其它模式继续作为显式对比入口。

实际换路后，第一跳 Leaf 生成 64-byte、高优先级的 FastCNP 格式控制包，经交换机 pipe、host-facing 控制队列和反向链路到达源端。它的 cause 固定为 `PATH_REROUTE`，携带原 EV、PSN、原/新 egress、原/新等级、触发交换机和时间。端侧走独立的路径通知入口，只冷却 EV，不调用 DCQCN，也不更改 ACK、RTT、RTO、重传或窗口状态。后续交换机仍可正常 CE 标记，接收端仍按原端到端环路回送 `ECN_ECHO`。

运行接口：

```bash
-lb n-mrc
-nmrc_ev_mode encoded|random_matched|random32
-nmrc_reroute_policy any_better|better_ge3
-nmrc_fastcnp on|off
```

`-nmrc_fastcnp off` 只关闭真实控制包与端侧冷却，网侧严格升档换路仍保留，便于做消融。完整对比 runner 覆盖六类 workload、六个内部组合和 SGLB/AR/NetAware/MRC/REPS baseline；MRC 显式固定 one-cycle/earliest，REPS 显式固定 8-entry buffer。其中受控 path-hotspot 留两条干净路径，使坏路径恰有两个严格更优候选，能够直接区分两种换路门槛：

```bash
python3 experiments/n-mrc/run_hybrid_nmrc_compare.py --dry-run --quick
python3 experiments/n-mrc/run_hybrid_nmrc_compare.py --quick --nodes 128 \
  --seeds 13,29,47 --out experiments/n-mrc/output/hybrid_nmrc_compare_p8_3seed
python3 experiments/n-mrc/run_hybrid_nmrc_compare.py --quick --nodes 2048 \
  --seeds 13 --scenarios healthy_permutation,path_hotspot \
  --out experiments/n-mrc/output/hybrid_nmrc_compare_p32_reduced_seed13
```

输出目录包含 `commands.tsv`、`summary.csv`、逐 case raw log 和 `hybrid_nmrc_compare_for_gpt.md`。同一 scenario/seed 内固定 traffic SHA-256 和全部 transport/CC/queue 参数；当 `P >= 32` 时 runner 自动去掉等价的 `random32` case。

已纳入仓库的历史审计证据（扩入 MRC/REPS baseline 前）：

- P=8、3 seeds、162 个 case：[`report`](output/hybrid_nmrc_compare_p8_3seed/hybrid_nmrc_compare_for_gpt.md)、[`summary.csv`](output/hybrid_nmrc_compare_p8_3seed/summary.csv)、[`commands.tsv`](output/hybrid_nmrc_compare_p8_3seed/commands.tsv)；
- P=32、1 seed、14 个缩放 case：[`report`](output/hybrid_nmrc_compare_p32_reduced_seed13/hybrid_nmrc_compare_for_gpt.md)、[`summary.csv`](output/hybrid_nmrc_compare_p32_reduced_seed13/summary.csv)、[`commands.tsv`](output/hybrid_nmrc_compare_p32_reduced_seed13/commands.tsv)。

## `mrc`

`mrc` 是 MRC 文献简要复现，使用 `-lb mrc`。MRC 论文整理稿见 `docs/papers/MRC.md`，当前仿真实现说明见 `MRC_IMPLEMENTATION.md`。

Source 端为每个 RoCE source/QP 初始化独立 EV profile。单平面二层 Clos 中，每条远端 leaf 路径由 spine/path-id 唯一确定，因此完整 EV namespace 大小等于有效物理路径数，EV `i` 一对一直接编码 physical path-id `i`。

每个 QP 先用 `src/dst/flow_id` 对全部 `path_space` 个 EV 生成一份确定性随机排列，再取排列中的前 `min(path_space, 32)` 个作为 active set。这里的“前”是 shuffled order 的前缀，不是固定选择 EV ID `0..31`。因此当 `path_space > 32` 时，每个 QP 实际使用的是 32 条随机且互不重复的物理路径，不同 QP 的 active path 子集通常不同；排列中剩余的唯一路径进入 backup set。正常发送只沿 active order 循环遍历，不遍历整个 EV namespace。

当 `path_space <= 32` 时，所有 EV 都在 active set 中，backup 为空。当 `path_space > 32` 时，backup 不参与正常 packet spraying；它主要在 LOSS/RTO 把 active EV 标成 `FAILED` 后补足 active set，并可被低频 probe 使用。如果没有 failure、active 缺口或 probe，backup 不影响正常选择序列。

Data packet 携带实际选择的 `mrc_ev`，receiver 在 ACK/NACK 中回显该 EV。带 `ECN_ECHO` 的 ACK 与 TRIM NACK 对精确 EV 使用相同 cooldown：默认进入 one-cycle soft skip；若该 EV 仍在冷却，后续 ECN/TRIM 会把 deadline 再后移一个 active-EV cycle，因此持续反馈可以续期，而反馈排空后 EV 会自然恢复。显式 `cwnd_scaled` 诊断模式使用拓扑计算的 1-BDP packet window，`rotations = ceil(bdp_pkts / active_evs)`，随后跳过 `rotations * active_evs` 次 MRC 选择，并忽略冷却期间的重复反馈。公共 400 Gbit/s、7 us RTT、4096-byte MSS 配置解析为 `bdp_pkts=86`，16 个 active EV 对应 6 轮和 96 次选择。TRIM 仍只是 soft skip，不表示该路径已被判坏：它不会把 EV 标成 `FAILED`，也不会触发 backup replacement。普通 clean ACK 不重写健康 EV，只负责确认数据、完成 probe recovery，或在 cooldown 已到期时恢复该 EV。

OOO/SACK NACK 只进入 SP/SACK selective retransmission queue，不冷却、不 fail EV，也不触发 Go-Back-N replay。LOSS NACK 和能够归因到首个未确认 packet 的 RTO 才把对应 EV 标为 `FAILED`，从 active set 移除，并从剩余 unique backup path 中补入一个 EV。FAILED EV 等待 retry 时间后可以通过低频 probe 回到 active；backup replacement 不会引入 duplicate physical path。

`dcqcn_variant` 独立维护 QP 级 congestion window。clean ACK 执行 `cwnd += 1/cwnd`，ECN ACK 执行 `cwnd -= 0.5`；OOO、TRIM、LOSS NACK 和 RTO 都执行 `cwnd -= 1`。默认 Exact+Bounded 传输不维护 `inflate`，发送额度为 `awnd=cwnd-inflight`，唯一 PSN 首次被 ACK/SACK 后才释放额度。MRC path state 与这个 QP 级窗口更新彼此独立。

`-lb mrc` 未显式覆盖时使用：

```text
queue_type              = composite_ecn_lb
roce_rx_mode             = sp
roce_sack_bitmap_bits    = 64
cc                       = dcqcn_variant
roce_transport_semantics = mrc_exact_bounded
roce_trim_recovery       = exact
ecn_thresh               = 0.8
mrc_cooldown_mode        = one_cycle
mrc_cooldown_reference   = topology_bdp
mrc_cooldown_reference_pkts = 1 BDP
mrc_all_cooling_fallback = earliest
mrc_failed_retry_us      = 100
mrc_probe_interval_pkts  = 256
```

上表描述的是只写 `-lb mrc` 时的 CLI 默认值。`dcqcn_variant` 会在 MRC 根据 ECN 冷却精确 EV 的同时调整 source congestion window；显式使用 `-cc none` 时仍保留 MRC path-state 更新，但不做额外的发送窗口控制。当前 canonical MRC 性能矩阵使用 `dcqcn_variant`，`cc=none` 只作为拥塞控制 ablation。

显式使用 `-cc dcqcn` 会切换到 rate-based DCQCN。该模式当前按
`400 Gbit/s / 7 us RTT / 350000-byte BDP` 校准，默认使用
`initial_alpha=0.6`、`min_rate=80 Gbit/s`、`alpha/rate interval=28 us`、
`CNP interval=16.8 us` 和 `byte_counter=1.4 MB`；它不改变 MRC 或 n-MRC
的路径选择和反馈语义。完整扫描与 ECMP 验证结果保存在
`output/nmrc_ecmp_dcqcn_400g_calibration/`。

`-mrc_cooldown_mode cwnd_scaled` 是唯一保留的固定长 cooldown 对照；默认 `one_cycle` 与显式 `cwnd_scaled` 之外不再保留其他 cooldown 入口。`-mrc_cooldown_reference_pkts N` 只覆盖 `cwnd_scaled` 使用的拓扑 BDP reference，用于诊断或复现历史 100-packet 基线。若所有 active EV 同时处于 COOLING，默认 fallback 强制选择 deadline 最早的 cooling EV，但不清除其状态或 deadline；`-mrc_all_cooling_fallback round_robin` 仅保留为诊断对照。`-mrc_failed_retry_us` 和 `-mrc_probe_interval_pkts` 仍调整故障恢复状态机。运行时 `MrcEvModelDiag` 应报告 `mrc_ev_model=encoded`、`mrc_ev_path_mapping=encoded_identity` 和 `mrc_alias_ratio=1`；公共拓扑下默认 `MrcCooldownDiag` 应报告 `mrc_cooldown_mode=one_cycle mrc_cooldown_reference=topology_bdp mrc_cooldown_reference_pkts=86 mrc_cwnd_scaled_rotations=6 mrc_cwnd_scaled_skip_selections=96`；`MrcFallbackDiag` 应报告 `mrc_all_cooling_fallback=earliest`；默认 `FinalCcMrcConfig` 应报告 `dcqcn_variant_inflate=disabled mrc_ecn_trim_penalty=mode_uniform roce_trim_recovery=exact`。

此前大量 Natural+Cumulative 数据使用旧传输底座。该底座暂留为显式历史复现入口：

```text
-roce_transport_semantics legacy
-roce_trim_recovery cumulative
```

此时 `FinalCcMrcConfig` 报告 `dcqcn_variant_inflate=natural`。该入口不影响新的默认 Exact+Bounded。

## `conweave`

Source 端保留当前 `pathid`，通过 ACK RTT 触发重路由。收到 ACK 后如果 RTT 超过阈值，并且距离上次 reroute 已超过最小间隔，就随机选择一个不同于当前 path 的新 `pathid`。它不做逐包 spraying，而是在 RTT 异常时切换 flow 使用的路径。

## `adaptive-routing`

交换机侧逐跳选路。每次需要从 ECMP next-hop 集合中选端口时，交换机比较候选端口的本地状态，默认用队列拥塞比较函数选最空的一组，并在并列候选中随机挑一个。默认实验使用 packet 粒度；切到 flowlet 粒度时，同一 flowlet 会保持上次端口，只有间隔超过 sticky delta 且新端口更优时才更新。

## `drill`

交换机侧逐跳低状态采样。每次转发时随机抽两个 next-hop，再加上该 destination 上一次记住的候选端口；交换机比较这些候选的队列拥塞，选择最优端口，并把该端口写回 per-destination memory，作为下一次采样的历史候选。

## `sglb`

交换机侧逐跳评分选路。默认将本端出口和下游出口的归一化队列压力按下式合成，分数越低越好：

```text
pressure(q) = clamp((q - 0.20) / 0.60, 0, 1)
score = 1 - (1 - local_pressure) * (1 - remote_pressure)
```

默认按 `0.10/0.40/0.60` 量化为四档。八档是嵌套细化，边界为 `0.05/0.10/0.25/0.40/0.50/0.60/0.80`，由 `-sglb_nmrc_levels 8` 显式启用。原本地/下游 queue、util、busy 五因子评分由 `-sglb_score_mode legacy` 显式启用。

默认 local quality cache 每 1us 更新，GCN export cache 每 5us 更新，超过 30us 未更新的 GCN snapshot 会 aging 失效。当前实现直接读取邻居交换机对象里的周期缓存，不模拟 GCN 控制报文的传播和资源开销。

转发候选仍沿用 SGLB top-K：先加入最低 quality 的完整等级；候选少于默认 `K=3` 时继续整档加入下一等级。同等级不按瞬时 raw queue 排名，也不截断；候选满足 K 后随机喷洒。SGLB 还维护 neighbor availability，链路不可用时对应 next-hop 会被硬排除。此前的 best-level-only 和 within-grade ranked 实验模式均已删除。
