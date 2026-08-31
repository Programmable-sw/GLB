# MRC LB 模式实现说明

本文档只描述当前 `-lb mrc`。历史多策略设计见已标记 superseded 的 `docs/superpowers/specs/2026-08-02-mrc-skip-policy-redesign.md`；当前设计见 `docs/superpowers/specs/2026-08-18-mrc-ocp-skip-once-consolidation-design.md`。

## 当前模型

MRC 是逐 QP 的 source-controlled packet spraying 模型。每个 QP 固定建立 64 个 EV，全部从 `GOOD` 开始；EV `i` 与物理 path-id `i` 一一映射。`src/dst/flow_id` 只用于确定性打乱遍历顺序，不改变 EV 与物理路径的映射，也不产生 backup EV。

唯一公开入口是：

```text
-lb mrc
```

不需要、也不接受 MRC policy、cooldown、active-EV、all-cooling 或 failure-recovery 参数。当前阶段只验证拥塞性能；故障状态类型和人工状态转换测试保留在内部，正常 LOSS/RTO 不会启用故障恢复或 probe。

固定配置为：

```text
logical_evs          = 64
active_evs           = 64
backup_evs           = 0
ev_path_mapping      = identity
congestion_reaction  = skip_once
failure_recovery     = disabled
```

## OCP SKIP_ONCE 状态机

数据包携带实际选择的 `mrc_ev`，receiver 在 ACK/NACK 中回显精确 EV。带 `ECN_ECHO` 的 ACK 和 TRIM NACK 都执行：

```text
GOOD --ECN/TRIM--> SKIP(token=true)
SKIP --nominal slot is reached--> GOOD
```

EV 已处于 SKIP 时，重复 ECN/TRIM 只增加 ignored 诊断，不重新计时、不累计 token。OOO/SACK NACK 只影响选择重传，不改变 EV 拥塞状态。

每次发送沿该 QP 的确定性 rotation 扫描：

1. 遇到 GOOD，选择该 EV。
2. 遇到本次选择中的第一个 SKIP，消费 token 并恢复为 GOOD，但当前包跳过它的这个名义机会。
3. 同一次选择中遇到的其他 SKIP 保持不变。
4. 如果整轮没有原有 GOOD，则普通 rotation 回到刚恢复的 EV并使用它。

因此一次发送选择最多恢复一个 SKIP，DATA 永远不使用非 GOOD EV；全 SKIP 不需要 deadline、earliest fallback、随机 fallback 或强制使用冷却路径。

## 传输与拥塞控制

默认 MRC 运行使用：

```text
queue_type               = composite_ecn_lb
roce_rx_mode              = sp
roce_sack_bitmap_bits     = 64
roce_transport_semantics  = mrc_exact_bounded
roce_trim_recovery        = exact
cc                        = dcqcn_variant
ecn_thresh                = 0.8
```

SP/SACK 负责可靠性，MRC 只负责 EV 选择和拥塞状态。Exact+Bounded 传输按唯一 PSN 的 ACK/SACK 释放额度；TRIM 精确重传缺失 PSN。`dcqcn_variant` 的 QP 级窗口更新与 MRC 的逐 EV SKIP 状态相互独立。

TRIM 按发生位置区分。ToR 到接收端 host 的下行队列产生的裁剪标记为 last-hop（LH），该属性由裁剪头携带并在 TRIM NACK 中回传。LH TRIM 与非 LH TRIM 使用相同的精确 PSN 恢复、拥塞控制和正常重传 EV rotation；只有非 LH TRIM 才让回显 EV 进入 SKIP。重传不绑定原 EV，也不显式排除原 EV。

## 运行时诊断

64-path canonical topology 上应包含：

```text
MrcEvModelDiag mrc_ev_model=encoded mrc_active_evs=64 mrc_backup_evs=0 ...
MrcPolicyDiag policy=skip_once all_skip_resolution=ordinary_rotation
MrcFailureRecoveryDiag enabled=0 assumed_bad=0 probe_packets=0
```

非 64-path topology 使用 `-lb mrc` 时直接返回错误。已删除的旧 MRC 参数也按未知参数返回非零状态，不做静默兼容。

## 代码入口

- `sim/datacenter/main_roce.cpp`：CLI、64-path 校验和固定诊断。
- `sim/roce.cpp`：EV 初始化、SKIP_ONCE 选择器、反馈归因和重传选择。
- `sim/roce.h`：per-QP EV 状态和内部故障预留类型。
- `sim/rocepacket.h`：ACK/NACK 的 EV 与 SACK 抽象字段。
- `sim/tests/main_roce_mrc_skip_semantics.cpp`：64 EV、精确反馈、duplicate no-rearm、one-packet-one-reset 和内部故障状态测试。
- `sim/tests/test_mrc_skip_policy_cli.py`：唯一公开入口与旧参数拒绝测试。
