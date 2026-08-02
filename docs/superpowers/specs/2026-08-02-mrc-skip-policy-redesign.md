# MRC 64-Path Skip-Policy Redesign

## 1. Goal

本设计将 `-lb mrc` 收敛为面向**拥塞反馈与负载均衡**的单平面离散事件模型。默认策略改为 `skip-token`，并保留 `skip-rotation`、现有 one-cycle deadline cooldown 和 `cwnd-scaled` cooldown 作为消融对照。

核心研究问题是：

> 在 64 条真实等价路径上，收到精确归因到某个 EV 的拥塞反馈后，一次性路径避让需要多强，才能在吞吐、队列、路径公平性和反馈稳定性之间取得最好平衡？

本阶段不研究路径故障恢复，不扩大到 multi-plane、SRv6 wire format、真实 VA/rkey、NSCC 或完整 OCP wire protocol。设计只改变 MRC 的拓扑基线、EV profile、拥塞状态和选择器语义；n-MRC、SGLB、Avail、Grade、REPS 等其他负载均衡模式不得改变。

## 2. Selected Design

采用以下组合，而不是继续扩展当前 `ACTIVE/COOLING/FAILED/PROBING + backup` 主路径：

- 固定 64 条跨 Leaf 真实等价路径；
- 一个共享的 64-EV profile，EV 与物理路径确定性一一映射；
- per-QP 独立维护 `GOOD/SKIP` 拥塞状态；
- 默认 `skip-token`，显式支持 `skip-rotation`；
- 当前 one-cycle deadline 与 `cwnd-scaled` 作为 legacy congestion-reaction ablation 保留；
- 普通数据只使用当前 eligible/GOOD EV，all-SKIP 由同一轮询与恢复规则自然处理；
- 故障状态和 ProbePacket 只保留接口，默认实验中不启用；
- Static MPR 延后到本轮拥塞/LB语义稳定之后。

这个方案比直接复刻论文生产系统更适合 htsim：64 EV 已覆盖全部 64 条真实路径，能够隔离拥塞反馈、路径避让和公平性机制，同时避免把 multi-plane、SRv6、controller 和 failure-recovery 状态混入当前实验。

## 3. Scope And Non-Goals

### In scope

- 64 Spine / 64 uplink / 64 downlink 拓扑族；
- EV↔path identity mapping；
- 精确 EV feedback；
- `skip-token` 与 `skip-rotation`；
- SKIP 期间重复 ECN/TRIM feedback 不重新启动或延长 cooldown；
- 新规范基线不引入独立 all-cooling 检测或 fallback 机制；
- 保留并回归验证现有 ACK/SACK reliability state 与 EV/LB state 解耦；
- 预留 `ASSUMED_BAD` 和独立 ProbePacket 接口，但默认关闭；
- 四策略消融及规模敏感性实验。

### Out of scope

- 当前实验中的 silent loss、link flap、blackhole 或 plane failure；
- backup EV 和故障后替换；
- 真实 PETH/ERTH/EETH wire encoding；
- multi-plane、SRv6 header、uSID 或 Clustermapper；
- 真实 RDMA VA/rkey 和 memory write；
- WriteIMM resource accounting；
- NSCC；
- Dynamic MPR。

## 4. Canonical Topology

所有正式 MRC skip-policy 实验使用：

```text
Spine switches   = 64
Leaf uplinks     = 64 per Leaf
Leaf downlinks   = 64 per Leaf
Hosts per Leaf   = 64
MRC EV profile   = 64 EVs
Cross-Leaf paths = 64
```

规模只通过 Leaf 数量变化：

| Leaf | Nodes |
| ---: | ---: |
| 4 | 256 |
| 8 | 512 |
| 16 | 1024 |
| 32 | 2048 |
| 64 | 4096 |
| 128 | 8192 |

任意跨 Leaf source/destination pair 始终有 64 条等价路径。Leaf 数量变化不得改变每对 Leaf 的 path count、EV count 或 EV/path mapping。

同 Leaf 流量不经过 Spine，不参与 64-path MRC path-diversity 结论。实验报告必须分别披露跨 Leaf 与同 Leaf 流量占比，或只生成跨 Leaf traffic matrix。

## 5. EV Profile And Mapping

建立一个 topology-owned 的静态 profile：

```cpp
EvProfile {
    id = 0;
    evs = [0, 1, ..., 63];
}
```

所有 MRC QP 引用同一 EV universe，但每个 QP 独立维护 EV 状态、rotation cursor 和反馈计数。EV 字段宽度保持现有实现，不因 profile 只使用 64 个值而缩窄。

映射固定为：

```text
EV0  -> Path0  -> Spine0
EV1  -> Path1  -> Spine1
...
EV63 -> Path63 -> Spine63
```

`logical_ev == physical_path_id == spine_id` 是本拓扑的运行时不变量。任何 alias、modulo collision、backup pool 或动态 remap 都是配置错误。

为避免不同 QP 在同一时刻全部从 EV0 开始，允许保留由 `src/dst/flow_id` 决定的 per-QP deterministic rotation order 或起始 offset；这只改变访问顺序，不改变 EV↔path identity mapping。相同 seed 和 flow identity 必须完全可复现。

## 6. Congestion State Model

拥塞研究的基础状态只有：

```text
GOOD --congestion feedback--> SKIP --policy recovery--> GOOD
```

对应建议状态：

```cpp
enum class MrcEvEligibility {
    GOOD,
    SKIP,
    ASSUMED_BAD, // interface only; unreachable in normal experiments
    DENIED       // reserved controller-facing state; not used in this phase
};
```

per-EV 拥塞字段：

```cpp
struct MrcEvState {
    MrcEvEligibility eligibility;
    bool skip_pending;
    uint64_t resume_rotation;
    uint64_t congestion_epoch;
    uint32_t probe_successes;
};
```

`skip_pending` 只属于 `skip-token`；`resume_rotation` 只属于 `skip-rotation`。legacy cooldown 可以继续使用独立 deadline 字段，但不得与新策略字段同时生效。

## 7. Policy Interface

公开 CLI 使用一个统一入口：

```text
-mrc_congestion_policy \
    skip_token | skip_rotation | one_cycle | cwnd_scaled
```

默认：

```text
mrc_congestion_policy = skip_token
```

实验和图表使用以下稳定名称：

| CLI | 实验标签 | 说明 |
| --- | --- | --- |
| `skip_token` | `MRC-skip-token` | 新默认；每次拥塞只跳过该 EV 的下一次真实选择机会 |
| `skip_rotation` | `MRC-skip-rotation` | 在当前 logical rotation 剩余部分排除该 EV，下一 rotation 恢复 |
| `one_cycle` | `MRC-timed-cooldown` | legacy selection-deadline 对照；名称保留实验连续性，但不是 wall-clock timer |
| `cwnd_scaled` | `MRC-cwnd-scaled-cooldown` | legacy BDP/cwnd-scaled selection deadline 对照 |

旧 `-mrc_cooldown_mode` 在过渡期可被解析为 deprecated alias，但新输出、runner 和文档只能写 `mrc_congestion_policy`。同一命令同时提供新旧参数必须拒绝，避免覆盖顺序造成不可复现。旧 all-cooling fallback 参数只服务于 legacy policy 复现；`skip_token` 与 `skip_rotation` 不读取该参数。

## 8. Shared Feedback Rule: No Rearm While SKIP

ECN ACK 和 TRIM NACK 的 LB 处理都必须汇聚到一个入口：

```cpp
bool mark_ev_congested(uint32_t ev, MrcCongestionSignal signal) {
    if (ev.eligibility != GOOD) {
        duplicate_feedback_ignored++;
        return false;
    }

    ev.eligibility = SKIP;
    ev.congestion_epoch++;

    switch (policy) {
    case SKIP_TOKEN:
        ev.skip_pending = true;
        break;
    case SKIP_ROTATION:
        ev.resume_rotation = current_rotation + 1;
        break;
    case ONE_CYCLE:
        start_existing_one_cycle_deadline(ev);
        break;
    case CWND_SCALED:
        start_existing_cwnd_scaled_deadline(ev);
        break;
    }
    return true;
}
```

只允许 `GOOD -> SKIP` 启动一次避让。EV 已为 SKIP 时，任何后续 ECN/TRIM echo：

- 不改变 token；
- 不改变 resume rotation；
- 不延长 legacy deadline；
- 不创建新 congestion epoch；
- 只增加 ignored-duplicate 诊断。

EV 恢复为 GOOD 后收到的新反馈可以开始新的 episode。该规则不依赖 ACK/SACK 到达顺序，避免旧在途反馈无限续期。

## 9. `skip-token` Semantics

每个 EV 至多持有一个布尔 token：

```text
GOOD + ECN/TRIM -> SKIP(skip_pending=true)
```

token 只能在 rotation cursor 真正到达该 EV 的 nominal slot 时消费。以下操作不得清 token：

- eligibility 预扫描；
- 诊断遍历；
- 查找是否存在 GOOD EV；
- ACK 清理；
- cursor 尚未到达该 EV 时的其他发送。

选择器按 per-QP rotation order 逐 slot 前进。遇到 `SKIP + skip_pending` 时：

1. 该 slot 记为一次 `skip_opportunity`；
2. 当前 packet 不得使用该 EV；
3. 清除 `skip_pending`；
4. 将 EV 恢复为 GOOD，但本次 selector 调用不能回头选它；
5. cursor 继续向后寻找本次 packet 可用的 GOOD EV。

如果连续多个 EV 都有 token，cursor 对每个被实际经过的 nominal slot 分别消费一次 opportunity。若当前 rotation 的 64 个 EV 全部 SKIP，选择器按相同规则走完剩余 rotation，进入下一 rotation 后选择刚恢复的 EV。这里没有额外的 all-cooling 分支：全 SKIP 只是普通轮询恰好连续经过 64 个 SKIP slot 的边界情况。

这一定义保证 token 表示“失去一次本应属于该 EV 的发送机会”，而不是“被任意状态扫描看见一次”。

## 10. `skip-rotation` Semantics

当 EV 在 rotation `R` 收到首次拥塞反馈：

```text
eligibility    = SKIP
resume_rotation = R + 1
```

在 `current_rotation < resume_rotation` 时，该 EV 不参与普通数据发送。rotation cursor 每访问完 profile 中的 64 个 nominal slot，`current_rotation` 加一。进入 `R+1` 时，满足恢复条件的 EV 在被选择前转回 GOOD。

rotation 10 后半段收到 EV17 的旧 ECN 时，`resume_rotation` 仍为 11，不能变成 12。进入 rotation 11 后，EV17 恢复；此后新 ECN 可创建下一次 SKIP episode。

如果全部 EV 都在 SKIP，selector 继续普通轮询，完成当前 logical rotation并进入下一 rotation，到期 EV 按既定规则恢复。该过程不需要识别“all-cooling episode”，也不需要 earliest-cooling、random selection 或其他额外 fallback。

## 11. Selector Contract And Natural All-SKIP Progress

选择器返回：

```cpp
struct MrcSelection {
    bool has_eligible_ev;
    uint32_t ev;
    uint32_t path;
    uint32_t nominal_slots_advanced;
};
```

核心不变量：

- DATA 只使用 GOOD EV；
- selector cursor 只能单调向前；
- token 只能由 cursor 经过 nominal slot 时消费；
- logical rotation 只由遍历完 64 个 nominal slot 推进；
- 状态/诊断扫描不推进 cursor；
- `skip_token` 与 `skip_rotation` 始终使用同一 rotation cursor，不进入独立 all-cooling 处理路径；
- 如果当前 rotation 暂无 eligible EV，cursor 自然推进到下一 rotation 的恢复边界，再按普通 GOOD 选择规则继续。

正常 64-path 无故障实验中，all-SKIP 应非常罕见。它不是一个需要单独策略的异常状态，而是轮询状态机必须覆盖的边界输入：结果由 token 消费或 rotation 恢复规则唯一决定。

## 12. Preserve Existing SACK, ECN And LB-State Separation

当前 MRC 已经在处理顺序上分离 reliability 与 LB：`processNack()` 先更新 cumulative ACK、SACK bitmap 和 selective retransmission queue，再调用 `update_mrc_on_nack()`；`processAck()` 先处理累计确认和 congestion control，再调用 `update_mrc_on_ack()`。本设计保留这条现有边界，不把它列为新的功能缺口。

本阶段不重写已经可工作的 PSN bitmap 和 selective retransmission，只把新 skip policy 接到现有精确 EV feedback 入口。反馈的逻辑抽象仍可表示为：

```text
SACK {
    cumulative_psn
    sack_offset
    sack_bitmap
    feedback_ev
    mrc_action = NONE | SKIP
}
```

receiver 收到：

```text
DATA(EV17, ECN=CE)
```

生成逻辑反馈：

```text
feedback_ev = 17
mrc_action  = SKIP
```

sender 继续按现有固定顺序处理：

1. cumulative ACK 和 SACK bitmap 更新 reliability state；
2. selective retransmission queue 只处理 PSN holes；
3. `feedback_ev + mrc_action` 更新 LB state；
4. congestion control 使用自身 ECN/ACK 信号更新 QP cwnd。

reliability state 不得直接修改 EV eligibility；LB state 不得释放 PSN credit、确认字节或决定重传。TRIM NACK 可以同时驱动 selective recovery 和 `feedback_ev -> SKIP`，但继续沿现有两条独立调用路径处理。实现工作是保持该边界并增加回归断言，不是重新设计可靠性状态机。

精确 EV 优先级保持：显式 `feedback_ev` 优先；仅兼容旧包时才允许 `seqno -> EV` 回退。不得使用 cumulative ACK 指向的 EV 代替产生 ECN 的 EV。

## 13. Failure Interface, Disabled By Default

为未来故障实验预留：

```text
GOOD --silent loss/timeout/known bad--> ASSUMED_BAD
ASSUMED_BAD --N probe successes--> GOOD
```

独立 `ProbePacket` 必须：

- 不占 data PSN；
- 不计 application bytes；
- 不占 data cwnd；
- 不进入普通 selective retransmission queue；
- 携带目标 EV 和独立 probe request id；
- 只更新对应 EV 的 probe/health state。

参数：

```text
mrc_failure_recovery_enabled = false
mrc_probe_success_threshold  = 3
```

本阶段正常实验不得产生 ASSUMED_BAD，不得发送 ProbePacket，也不得创建 backup EV。64 个 EV 已覆盖 64 条真实路径；未来 EV17 故障时可用路径从 64 降至 63，恢复后回到 64。

任何 LOSS/RTO 自动触发 FAILED、backup promotion 或业务数据 probe 的旧路径在新默认实验中必须禁用。接口测试可以使用人工状态注入，不进入正式拥塞结果。

## 14. Static MPR Deferred To P2

后续最小 Static MPR 使用：

```text
next_psn <= cack_psn + mpr_pkts
```

requester 和 responder 维护有限 PSN receive/outstanding bitmap。当前轮次先不让 MPR 阻塞新策略开发，但每个正式实验必须打印：

```text
cwnd_max
max_outstanding_psn_span
configured_or_assumed_mpr
```

只有验证 `cwnd` 和实际 outstanding PSN span 显著小于计划 MPR 时，才能说明暂缓 MPR 不影响本轮 LB 比较。若任一实验接近或超过 MPR 假设，必须先实现 Static MPR 再使用该结果。

## 15. OOO/DDP Abstraction

receiver 继续使用现有 PSN/byte-sequence OOO tracking 和 SACK bitmap。验收语义是：PSN 8 可在 PSN 6、7 缺失时被独立记录，随后反馈 bitmap 中保留 6、7 的 hole。

不新增真实 VA、rkey 或 memory write。只要乱序 packet 的逻辑 offset 和接收状态不依赖按序到达，就满足本研究所需的 DDP abstraction。

## 16. CLI, Defaults And Diagnostics

默认运行：

```text
-lb mrc
mrc_congestion_policy=skip_token
mrc_ev_profile_size=64
mrc_path_count=64
mrc_ev_path_mapping=identity
mrc_failure_recovery_enabled=false
mrc_probe_success_threshold=3
```

运行时至少打印：

```text
MrcTopologyDiag spine_count=64 leaf_uplinks=64 leaf_downlinks=64 hosts_per_leaf=64
MrcEvProfileDiag profile_id=0 ev_count=64 path_count=64 mapping=identity backup_evs=0
MrcPolicyDiag policy=skip_token no_rearm=1 all_skip_resolution=natural_rotation
MrcFailureDiag enabled=0 assumed_bad=0 probe_packets=0 probe_success_threshold=3
```

per-flow/per-QP 聚合诊断：

- congestion episodes；
- duplicate feedback ignored；
- skip opportunities consumed；
- rotation skips；
- GOOD/SKIP 状态驻留选择次数；
- 每个 EV 的 data selections、ECN/TRIM feedback 和 skip count；
- 跨 rotation 的连续 SKIP slots 与 selector nominal slots advanced；
- DATA-on-non-GOOD violations，必须为 0；
- probe packets 和 ASSUMED_BAD transitions，正常实验必须为 0。

## 17. Compatibility And Migration

本设计替代当前文档中的以下 canonical 假设：

- `min(path_space, 32)` active EV + backup；
- `ACTIVE/COOLING` 作为默认拥塞状态名；
- `one_cycle` 或 `cwnd_scaled` 作为默认策略；
- 新默认策略对 earliest/round-robin all-cooling DATA fallback 的依赖；
- 普通业务包近似 probe；
- LOSS/RTO 在无故障实验中自动驱动 failure state。

历史输出目录保持只读，不覆盖、不重解释。旧 runner 必须显式选择 legacy policy，不能因默认改为 `skip_token` 而悄悄生成不同语义的数据。

`MRC_IMPLEMENTATION.md` 只在代码实现和测试通过后更新为新默认；设计阶段继续描述当前已实现状态，并在顶部链接本设计作为 pending redesign。

## 18. Test Strategy

### P0 topology/profile tests

1. 所有规定 Leaf 规模都解析为 64 Spine、64 uplink、64 downlink和每 Leaf 64 host。
2. 跨 Leaf pair 的 path count 始终为 64。
3. EV0..63 分别映射 Path0..63/Spine0..63。
4. 64 EV 无重复、无 alias、无 backup。
5. 相同 seed/flow identity 的 rotation order 可复现。

### P0 `skip-token` tests

1. GOOD EV 首次 ECN/TRIM 后变为 SKIP且只有一个 token。
2. SKIP 期间重复反馈不改变 token或 episode。
3. eligibility 预扫描和诊断遍历不清 token。
4. cursor 到达 nominal slot 时恰好消费一次 token。
5. 刚消费 token 的 EV 本次 packet 不可被选，后续机会可重新使用。
6. 连续多个 token 按 cursor 顺序各消费一次。
7. 全部 64 EV 带 token 时按普通 cursor 消费 slot 并跨入下一 rotation，不需要特殊 fallback。

### P0 `skip-rotation` tests

1. rotation R 的拥塞设置 `resume_rotation=R+1`。
2. rotation R 内重复反馈不延长到 R+2。
3. R+1 选择前恢复 GOOD。
4. all-SKIP 时仍按普通 cursor 完成 logical rotation并自然恢复，不触发独立处理分支。

### P0 feedback-attribution tests

1. ECN ACK 的显式 feedback EV 只修改对应 EV。
2. TRIM NACK 同时进入 reliability recovery 和 LB SKIP，但两者状态互不代写。
3. cumulative ACK 不被误当作产生 ECN 的 packet identity。
4. stale/duplicate feedback 不重新开始未结束的 episode。

### P1 reliability and failure-interface tests

1. 现有 cumulative ACK、SACK bitmap、OOO hole、selective retransmission 以及其与 `update_mrc_on_ack/nack()` 的调用边界回归通过。
2. 正常拥塞矩阵中 ASSUMED_BAD、ProbePacket、backup promotion 均为 0。
3. 人工 ASSUMED_BAD 测试中 ProbePacket 不占 data PSN、bytes 或 cwnd。
4. 恰好 N 次连续成功后恢复 GOOD；失败会重置连续成功计数。

### Regression tests

- RR、REPS、SGLB、Avail、Grade、n-MRC 的选路与默认参数不变；
- legacy `one_cycle` 和 `cwnd_scaled` 可显式运行；
- 旧输出解析器不会把新默认结果标成历史 MRC；
- 运行时配置摘要与实际 policy/profile 完全一致。

## 19. Experiment Matrix

四个 MRC congestion-reaction policy 使用同一 traffic matrix、seed、queue、RX、CC 和 topology：

```text
MRC-skip-token
MRC-skip-rotation
MRC-timed-cooldown
MRC-cwnd-scaled-cooldown
```

第一阶段建议使用 Leaf `4/8/16`，即 256/512/1024 nodes，先验证机制和成本；通过后再扩展到 2048/4096/8192。

至少使用三个 seed，并对同一 `(leaf_count, workload, load, seed)` 做 paired comparison。所有方案复用完全相同的 traffic matrix hash。

主要指标：

- aggregate throughput / CCT；
- flow FCT p50/p95/p99/p999；
- queue occupancy p50/p95/p99/p999；
- per-path bytes/packets 与 path CV/Jain fairness；
- ECN、TRIM、NACK、retransmission 和 RTO；
- congestion episodes、ignored duplicate feedback、skip opportunities；
- 每个 EV 的 selection share 和 feedback-to-action latency；
- 跨 rotation 的连续 SKIP slot 计数；
- DATA-on-non-GOOD violation 计数。

四策略结果必须同时报告性能与机制证据。不能只根据单一 CCT 或均值选择默认策略。

## 20. Acceptance Criteria

设计实现只有在以下条件全部满足时才可更新 canonical MRC 文档和默认实验：

1. plain `-lb mrc` 打印并实际使用 `skip_token`。
2. 所有正式拓扑具有 64 条跨 Leaf path 和 64 个 identity-mapped EV。
3. 无 backup EV；正常实验无 failure/probe activity。
4. 所有 DATA packet 的发送 EV 在选择瞬间均为 GOOD。
5. SKIP 期间重复反馈不刷新 token、rotation 或 legacy deadline。
6. `skip-token` 只在真实 nominal opportunity 消费 token。
7. `skip-rotation` 恰好在下一 logical rotation 恢复。
8. 全 SKIP 输入由普通 rotation/token 规则自然收敛，不存在额外 all-cooling policy 分支。
9. 回归测试证明现有 SACK/PSN reliability state 与 EV/LB state 的解耦在接入新策略后保持不变。
10. 四策略使用相同 traffic matrix 和非策略配置完成至少三 seed 对照。
11. 256/512/1024-node 阶段无配置漂移、未完成流和非零 violation counter。
12. `MRC_IMPLEMENTATION.md`、experiment README、CLI help和运行时诊断在实现验证后同步更新。

## 21. Risks And Controls

- **QP 同步风险：** 64 QP 都从 EV0 开始会制造人为碰撞；通过 per-QP deterministic order/offset 消除，同时保持 identity mapping。
- **token 被扫描误消费：** 只有 rotation cursor 的 nominal-slot advancement 能修改 token，诊断和 eligibility scan 必须为只读。
- **全 SKIP 轮询边界：** selector 必须限制一次选择最多推进到下一次可恢复 rotation，并用 bounded-iteration assert 证明普通轮询会自然收敛；不得另加 random/earliest fallback。
- **策略状态串用：** 每个 policy 只读取自己的字段，初始化和切换时清理其他 policy 状态。
- **旧 runner 静默漂移：** 历史 runner 显式固定 legacy policy，并校验运行时配置摘要。
- **MPR 暂缓掩盖问题：** 每个实验记录最大 PSN span；接近假设 MPR 时停止扩展实验并先实现 Static MPR。
- **规模成本：** 先完成 256/512/1024-node paired matrix，再决定是否运行 2048–8192。

## 22. Documentation Handoff

实现完成前，本文件是 pending design，不代表当前代码行为。实现和验证完成后：

1. 将 `MRC_IMPLEMENTATION.md` 改写为 64-path、`skip_token` 默认和故障接口关闭的实际语义；
2. 更新 `experiments/n-mrc/README.md` 的公共命令、四策略标签和诊断字段；
3. 将被替代的 one-cycle/cwnd-scaled 默认设计标记为 historical，而不是删除；
4. 保存实施计划、测试输出和最终实验 manifest，以便复现默认切换。
