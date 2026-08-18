# MRC OCP SKIP_ONCE 收敛设计

## 目标

将当前 `-lb mrc` 收敛为唯一、无需额外命令行参数的 OCP MRC 1.0 拥塞性能模型：每个 QP 使用 64 个 EV，EV 与 64 条物理路径一一映射，正常情况下全部处于 GOOD/可选状态；ECN 或 TRIM 精确归因到某个 EV 后，只让该 EV 失去下一次名义发送机会。

当前阶段只研究拥塞性能，不研究路径故障。故障恢复类型和人工测试作为未来接口保留，但不开放 CLI，也不参与正常 MRC 运行。

## 一手规范依据

OCP Multipath Reliable Connection Specification 1.0 规定：

- SACK M 字段的 `0b01` 表示 `Skip once (ECN marked)`；
- 收到 `SKIP_ONCE` 或相应 TRIM 反馈后，精确 EV 进入 SKIP；
- 实现指导建议轮询到 SKIP EV 时将它恢复为 GOOD，但当前包继续寻找下一个 GOOD EV；
- 一个发送包最多将一个 EV 从 SKIP 恢复为 GOOD；
- EV 轮询顺序由实现定义。

规范地址：<https://www.opencompute.org/documents/ocp-mrc-1-0-pdf>，相关章节为 7.5.5.5、9.3.1 和 11.2.2。

## 唯一公开配置

用户只需指定：

```text
-lb mrc
```

运行时固定为：

```text
ev_count                 = 64
active_evs               = 64
backup_evs               = 0
ev_path_mapping          = identity
congestion_reaction      = skip_once
failure_recovery         = disabled
all_skip_resolution      = ordinary_rotation
```

删除以下公开入口：

```text
-mrc_congestion_policy
-mrc_cooldown_mode
-mrc_cooldown_reference_pkts
-mrc_all_cooling_fallback
-mrc_active_evs
-mrc_failed_retry_us
-mrc_probe_interval_pkts
-mrc_failure_recovery
-mrc_probe_success_threshold
```

旧命令不得被静默兼容；传入已删除参数时，主程序按未知参数返回非零状态。历史输出目录保持只读，不删除也不重新解释。

## EV 状态与反馈

正常拥塞研究只使用：

```text
GOOD --ECN/TRIM--> SKIP(token=true)
SKIP --cursor reaches nominal slot--> GOOD
```

规则如下：

1. 每个 QP 建立同一个 64-EV universe 的独立状态副本。
2. `EV i == physical path i`；per-QP 确定性打乱只改变访问顺序。
3. 第一次精确 ECN/TRIM 反馈执行 GOOD→SKIP 并设置一个布尔 token。
4. EV 已处于 SKIP 时，后续反馈只计入 duplicate/ignored 诊断，不重置或累积 token。
5. EV 恢复 GOOD 后，新反馈可以创建新的 skip episode。
6. OOO 反馈不改变 EV 拥塞状态。

## 选择器

每次发送选择沿 per-QP rotation cursor 单调向前扫描：

1. 遇到 GOOD EV，选择它发送。
2. 遇到本次选择中的第一个 SKIP EV，消费其 token并恢复为 GOOD，但本包不能在该名义槽位立即使用它。
3. 本次选择继续扫描时，其他 SKIP EV保持 SKIP，不能再被恢复。
4. 找到后续已有 GOOD EV时使用该 EV。
5. 如果扫描完整个 64-EV rotation 后没有其他 GOOD EV，则允许扫描回到本次刚恢复的 EV并使用它。此时发送数据的 EV 已为 GOOD，且 cursor 继续位于该 EV 的后一槽位。

该算法保证：

- 一个发送包最多恢复一个 SKIP EV；
- 一个拥塞 episode 精确损失一次名义发送机会；
- DATA 永不使用 SKIP EV；
- 全 SKIP 不需要独立检测、earliest deadline、随机选择或强制使用非 GOOD EV；
- 全 SKIP 时状态通过普通发送和 rotation 逐步回到 GOOD；不要求每个后续发送都必须恢复一个 EV。

## 删除的机制

完全删除以下拥塞对照及其状态：

- `skip_rotation`；
- `one_cycle`；
- `cwnd_scaled`；
- selection deadline；
- cooldown reference/BDP rotation；
- all-cooling earliest/round-robin fallback；
- legacy active/backup selector 分支。

不再保留单值 policy enum；固定行为直接由 MRC 状态机表达。

## 故障恢复预留

保留 `ASSUMED_BAD`、独立 `RoceEvProbe` 报文类型和纯状态转换人工单测，供未来故障设计复用。当前不提供 CLI，不允许默认 LOSS/RTO 将 canonical MRC EV 转为 ASSUMED_BAD，也不调度 probe。

未来加入故障实验时必须另行设计：故障检测条件、probe 调度、恢复阈值、全路径故障活性保证和运行时诊断；不得通过重新打开现有未接通的 CLI 完成。

## 文档与历史记录

现行文档只描述本设计：

- `README.md`；
- `MRC_IMPLEMENTATION.md`；
- `experiments/n-mrc/README.md`；
- 当前 MRC 审计和实验入口说明。

旧设计/计划保留为历史记录，但在顶部标明已被本设计取代。旧文档中的 32 active EV、backup、one-cycle/cwnd-scaled 默认、特殊 all-cooling fallback 和公开故障 CLI 不得再被表述为当前行为。

## 测试要求

### CLI

- plain `-lb mrc` 输出固定 64 EV、0 backup、`skip_once`、failure disabled；
- 所有已删除 MRC 参数均返回非零状态；
- 64-path canonical topology 约束继续生效。

### 状态机

- ECN 和 TRIM 都精确触发一个 token；
- SKIP 期间重复反馈不 rearm；
- token 只在对应 nominal slot 被 cursor 遇到时消费；
- 一次发送最多恢复一个 SKIP EV；
- 连续 SKIP 和全 SKIP 均只恢复一个 EV并返回 GOOD EV；
- `data_on_non_good_violations == 0`。

### 隔离与回归

- ACK/SACK reliability、TRIM exact retransmission 和 DCQCN 行为不变；
- RR、REPS、SGLB、n-MRC、Avail 和 Grade 不变；
- 故障内部类型和人工 probe-result 状态测试继续编译通过；
- 当前实验 runner 不再生成已删除参数。

## 验收标准

1. 生产代码和当前 runner 中不存在 `skip_rotation`、`one_cycle`、`cwnd_scaled` 或 legacy all-cooling MRC 分支。
2. plain `-lb mrc` 是唯一公开 MRC 拥塞策略入口。
3. 一次选择调用最多将一个 EV 从 SKIP 恢复为 GOOD。
4. 正常 MRC 运行固定报告 64 active EV、0 backup、故障关闭。
5. 当前文档与运行时诊断一致，历史设计清楚标记为 superseded。
