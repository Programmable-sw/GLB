# N-MRC 全冷恢复、n-mrc1 随机端侧与 n-mrc2 二态 FastCNP 设计

## 目标

在现有 `mrc`、`sglb` 和 `n-mrc` 基线上增加三个可独立复现实验的变体：

- `n-mrc-allcool-rr-reset`：只修改原 n-mrc 的 all-cooling fallback；全部 EV cooling 后完整轮询所有路径一轮，再清空全部 cooldown 状态；
- `n-mrc1`：端侧不维护 EV cooldown，每个数据包随机写入一个有效 EV，由源 ToR 的现有 GLB 逻辑兜底；
- `n-mrc2`：保留现有端侧 EV 轮询和一轮 cooldown，但把源 ToR 的多级质量判断改为一个固定阈值的二态判断；达到阈值时，换路和 FastCNP 必须作为同一次动作发生。

三个变体用于回答三个机制问题：

1. 只修改全冷 fallback 并在一轮后恢复全部路径，能否消除原 n-mrc 的 WebSearch 回退；
2. 去掉端侧记忆后，仅靠逐包随机 EV 和网侧兜底能达到什么性能；
3. 当前 n-mrc 的问题是否来自多级、相对路径判定引起的过度换路和过度 cooldown，改成有明确 ECN 概率含义的二态判定后能否改善。

现有 `mrc`、`sglb` 和 `n-mrc` 的默认行为必须保持不变。

## n-mrc-allcool-rr-reset：全冷后一轮全路径恢复

该方案是原 n-mrc 的单变量消融，不属于 n-mrc1 或 n-mrc2，也不改变网侧 GLB、FastCNP 触发、TRIM、EV 构造或普通 cooldown 语义。

正常状态继续使用原 n-mrc：从 per-QP 打乱后的 encoded EV 向量开始扫描，跳过 cooling EV，并在剩余 active EV 中轮询。

当一次选择完整扫描全部 EV 仍未找到 active EV 时：

1. 进入一个 per-QP `all-cooling RR-reset episode`；
2. 从进入时的 `_nmrc_cursor` 开始，忽略 cooling 标记，严格按该 QP 已打乱的 EV 向量选择；
3. episode 内连续执行恰好 `P` 次选择，使每个 encoded EV/物理路径恰好使用一次；
4. 每次选择仍正常增加 `_nmrc_select_ordinal`，但 episode 内到达的重复 FastCNP/TRIM 不延长 deadline；
5. 第 `P` 次选择提交后，将该 QP 所有 EV 的 `cooling=false`、`cool_until_select_count=0`，结束 episode；
6. 下一次选择从刚完成一轮后的 cursor 进入原 n-mrc 正常逻辑。

因此它不是“第一条路径到期即退出”，也不是当前 earliest-expiry fallback。它保证一次完整全路径 RR 后所有路径恢复正常，避免同 expiry 时连续选择最小 logical EV。flow 结束、EV set 重建或 path-space 变化时必须清除 episode 状态。

该 preset 的其他 resolved 配置与旧 n-mrc 完全相同：

```text
endpoint_policy    = rr_cooldown
reroute_policy     = better_ge3
fastcnp            = on
ev_mode            = encoded
all_cooling_policy = rr_reset
```

## 共同路径分数

源 ToR 继续复用现有 SGLB/N-MRC 遥测，不引入新的探测报文。对原 EV 对应的完整两跳路径计算：

```text
p(q) = clamp((q/B - 0.20) / 0.60, 0, 1)

score = 1 - (1 - p_local) * (1 - p_remote)
```

其中 `p_local` 表示 source leaf 到 spine 上行队列的 RED ECN 标记概率，`p_remote` 表示该 spine 到 destination leaf 下行队列的 RED ECN 标记概率。快照新鲜且两跳随机标记近似独立时，`score` 表示一个反事实数据包沿原路径通过时，被至少一跳 ECN 标记的预测概率。

该解释不是逐包保证：本地状态约有 1 us 缓存，远端 GCN 状态约有 5 us 更新周期。文档和图表必须称其为“预测 ECN 概率”或“路径拥塞分数”，不能称为该数据包已经收到的真实 CE。

## n-mrc1：随机 EV、无端侧 cooldown

### 端侧选择

每次发送新包或重传包时，从 encoded EV 空间 `0..P-1` 中均匀选择一个 EV。标准实验固定使用 `encoded` 模式，使一个 EV 对应一条物理路径，避免随机 16-bit EV 别名干扰解释。

随机序列由每个 QP 独立的确定性散列生成，输入至少包含：

```text
global seed, src, dst, flow/QP id, selection ordinal
```

不得消耗仿真器的全局 `random()`/`drand()` 序列。相同配置和 seed 必须完全可复现，不同 QP 不应保持同相位。

### 网侧与反馈

- 源 ToR 继续使用当前 n-mrc 的 `better_ge3` 多级 GLB reroute 逻辑；
- 网侧实际换路仍可被诊断，但不生成 FastCNP；
- 端侧忽略 FastCNP cooldown 和 accepted-TRIM cooldown 两个入口；
- 所有 endpoint cooldown start、skip、recovery 和 all-cooling fallback 计数应为零。

因此 n-mrc1 不是 standalone SGLB：数据包仍由端侧先随机指定 EV，只有源 ToR 能对该原始选择做混合兜底；它用于隔离端侧状态记忆的贡献。

## n-mrc2：单阈值二态 reroute + FastCNP

### 固定阈值

主实验固定：

```text
T = 0.50
```

`-lb n-mrc2` preset 将该值锁定为 `0.50`，冲突的命令行 override 必须报错。通用 `-lb n-mrc -nmrc_reroute_policy binary_score` 可以显式配置其他阈值用于独立消融，但这类结果不得标为 `n-mrc2`。

`T=0.50` 的含义是：按当前两跳状态估计，沿原路径被至少一跳 ECN 标记的概率已经不低于未标记概率。它对应：

- 只有一跳拥塞时，该跳队列约达到缓冲区的 50%；
- 两跳拥塞程度相同时，每跳队列约达到缓冲区的 37.6%。

该阈值比 `0.80` 更早介入，给 FastCNP 传输和端侧 cooldown 留出时间；同时又显著高于 RED ECN 开始标记的位置，避免轻微排队即触发路径惩罚。

### 二态规则

每个候选物理路径只按同一个阈值分为两类：

```text
SAFE:      score < T
CONGESTED: score >= T
```

不得再使用 `GOOD/DEGRADED/BAD/AVOID` 四级质量、等级差或 `better_ge3` 作为 n-mrc2 的触发条件。

对每个进入源 ToR 的 n-mrc2 数据包执行：

1. 解析原 EV 对应的原物理出口，并验证本地和远端 score snapshot 在现有 TTL 内均有效；
2. 任一段 snapshot 缺失或过期时，将该路径视为 UNKNOWN，本包保持原出口且不生成 FastCNP；UNKNOWN 只是数据有效性状态，不是第三个拥塞级别；
3. 若有效的 `original_score < T`，保持原出口，不生成 FastCNP；
4. 若 `original_score >= T`，收集所有 link available、两段 snapshot 有效且 `candidate_score < T` 的替代出口；
5. 若候选集合为空，保持原出口，不生成 FastCNP；
6. 若候选集合非空，使用现有 per-packet deterministic hash 在全部 SAFE 候选中选择一条；
7. 只有选中不同出口且能成功构造并注入真实反向 FastCNP 时，才同时提交数据包 reroute、FastCNP generated 和 paired-action 三个状态；
8. FastCNP 携带原 EV，端侧收到后将这个原 EV 冷却现有的一轮，而不是冷却替代出口。

步骤 7 是原子语义：不存在 silent reroute，也不存在未实际换路却发送 FastCNP。若 FastCNP master 被关闭、反向通知路径不可解析或控制包无法注入，为保持该语义，本次数据包不换路并单独计数。

候选选择只使用 SAFE/CONGESTED 两种状态。SAFE 集合内部不按旧质量等级扩池；使用确定性散列而不是永远选择最低 score，避免多个流同时拥向同一条瞬时最低路径。

真实数据包在替代路径上仍可被后续队列正常标记 CE，并继续走现有 ECN echo/DCQCN 环路。n-mrc2 的预测 FastCNP 不清除或替代真实 ECN 拥塞控制。

### 端侧行为

n-mrc2 保留当前 n-mrc 的端侧行为：

- encoded EV set；
- 每 QP 的轮询选择；
- FastCNP 到达后对原 EV 冷却一个完整轮次；
- 重复在途通知不延长正在进行的 cooldown；
- all-cooling 时使用现有 earliest-expiry fallback，避免停发。

现有 actual-path TRIM cooldown 作为独立的丢包保护保持不变，并与 FastCNP cooldown 分开统计。随机 ECN 标记本身不直接启动 EV cooldown。

## 配置与内部组织

内部继续使用一个 `LB_NMRC`，不新增 `LB_NMRC1`/`LB_NMRC2` 枚举。原因是当前发送、TRIM、FastCNP、诊断和 source-ToR gate 有多处精确检查 `LB_NMRC`；新建 LB 枚举容易产生行为遗漏。

增加三个正交策略：

```text
endpoint policy:
    rr_cooldown       # 当前 n-mrc 和 n-mrc2
    random_stateless  # n-mrc1

reroute policy:
    any_better        # 现有
    better_ge3        # 当前 n-mrc 和 n-mrc1
    binary_score      # n-mrc2

all-cooling policy:
    earliest          # 当前 n-mrc、n-mrc2
    rr_reset          # n-mrc-allcool-rr-reset
```

建议提供 `-lb n-mrc-allcool-rr-reset`、`-lb n-mrc1` 和 `-lb n-mrc2` 作为 parser preset，解析后仍映射到 `LB_NMRC`：

```text
n-mrc-allcool-rr-reset:
    endpoint_policy = rr_cooldown
    reroute_policy  = better_ge3
    fastcnp         = on
    ev_mode         = encoded
    all_cooling_policy = rr_reset

n-mrc1:
    endpoint_policy = random_stateless
    reroute_policy  = better_ge3
    fastcnp         = off
    ev_mode         = encoded

n-mrc2:
    endpoint_policy = rr_cooldown
    reroute_policy  = binary_score
    binary_threshold = 0.50
    fastcnp         = on
    ev_mode         = encoded
```

两个命名 preset 是不可拆分的实验定义：

- `-lb n-mrc1` 必须拒绝 `rr_cooldown`、FastCNP on 和非 encoded EV mode 等冲突 override；
- `-lb n-mrc2` 必须拒绝非 `0.50` 阈值、FastCNP off、非 `rr_cooldown`、非 `binary_score` 和非 encoded EV mode；
- `-lb n-mrc-allcool-rr-reset` 必须拒绝非 `rr_reset` all-cooling policy，以及任何会改变旧 n-mrc 其余 resolved config 的 override；
- 通用 `binary_score` policy 也强制 FastCNP on，禁止退化成 silent reroute；
- n-mrc2 固定使用当前 `q_min=0.20`、`q_max=0.80`、noisy-OR 和与其一致的 RED ECN 标尺，冲突配置必须报错。

resolved configuration 必须打印 preset、内部策略和最终阈值。显式阈值只允许在通用 `-lb n-mrc -nmrc_reroute_policy binary_score` 下使用，并校验为 `[0,1]` 内有限值。

n-mrc2 复用当前 FastCNP packet 的两个 level metadata 字段时，固定编码为：

```text
original_level = 1  # CONGESTED
selected_level = 0  # SAFE
```

这两个值只表示二态诊断，不允许调用旧 `sglb_nmrc_level(score)` 重新量化，也不参与端侧决定。n-mrc2 的 FastCNP 只能出现 `CONGESTED -> SAFE` 这一种转换。

## 诊断

在不改变旧字段含义的前提下增加：

- `nmrc_endpoint_policy`、`nmrc_reroute_policy`、`nmrc_binary_threshold`；
- `binary_checks`；
- `binary_below_threshold`；
- `binary_snapshot_unknown`；
- `binary_no_safe_candidate`；
- `binary_reverse_path_blocked`；
- `binary_fastcnp_injection_blocked`；
- `binary_safe_candidate_count_sum`；
- `binary_paired_actions`；
- `binary_original_congested` 和 `binary_selected_safe`；
- 原路径和目标路径 score 的计数、总和与最大值；
- n-mrc1 随机选择的每 EV/物理路径计数、路径份额最大偏差和均匀性统计；
- FastCNP generated/arrived、cooldown starts/skips/recoveries、TRIM cooldown 和 all-cooling fallback。
- `all_cooling_rr_episodes`、`all_cooling_rr_selections`、`all_cooling_rr_resets`，并验证每个完成 episode 的 selections 恰为 `P`；

对 n-mrc2，`binary_paired_actions` 必须等于实际 binary reroute 数，也必须等于 FastCNP generated 数；三个计数只在控制包成功注入后一起提交。FastCNP 可能在真实队列中延迟，因此 generated 不要求等于 arrived。旧四级 transition 若继续输出，只能作为旁路诊断，不能参与决策。

## 测试设计

先写失败测试，再实现生产代码。

### 端侧测试

1. 旧 n-mrc 的 earliest-expiry fallback 序列和默认行为保持不变；
2. `P=8` 全部 cooling 时，rr-reset 按 per-QP 向量从 cursor 开始返回每个 EV 恰好一次；
3. rr-reset 前七次不提前清除状态，第八次后全部 EV active、deadline 为零；
4. episode 中到达的 FastCNP/TRIM 不延长 episode，完成后下一次选择恢复旧 n-mrc RR；
5. 两个 QP 使用各自打乱顺序，不因最小 logical EV tie-break 同步到 path 0；
6. n-mrc1 相同 seed/QP 的随机 EV 序列可复现，不同 QP 去相位；
7. 随机选择只产生 `0..P-1` encoded EV，且不是当前 RR 顺序；
8. 新包和重传包都满足 `pathid == mrc_ev`；
9. n-mrc1 收到 FastCNP 或 accepted TRIM 都不启动 cooldown；意外到达的 FastCNP 可以计入 arrived/ignored，但 cooldown start/skip/recovery 必须保持零；
10. 当前 n-mrc 的 RR、FastCNP cooldown、TRIM cooldown 和重传重选测试保持通过。

### 二态选择纯函数测试

1. `score = nextafter(0.5, 0)`：不换路、不通知；
2. `score = 0.5` 且存在 SAFE 候选：换路并通知；
3. 原路径达到阈值但无 SAFE 候选：不换路、不通知；
4. 不可用路径不能成为候选；
5. 多个 SAFE 候选的选择可复现并能跨包打散，且候选集合不含原出口、UNKNOWN、不可用或 `score >= T` 的路径；
6. 反向路径不可解析：不换路、不通知并增加 blocked 计数；
7. 旧 `any_better`/`better_ge3` 边界测试保持不变。

### 分数与集成测试

1. 单跳队列 50%、另一跳处于 Kmin 时得到 score 0.5；
2. 两跳各约 37.5736% 时得到 score 0.5；
3. source leaf 构造原路径高于阈值、替代路径低于阈值，检查一次 reroute 对应一次 FastCNP；
4. 低于阈值和全路径均高于阈值两种情况下均不生成动作；
5. FastCNP 沿真实反向队列到达后只冷却原 EV；
6. n-mrc2 FastCNP metadata 固定为 `CONGESTED -> SAFE`，不出现旧四级转换；
7. remote snapshot 缺失或过期时原路径不触发、候选路径不进入 SAFE 集合；
8. SGLB、MRC 和旧 n-mrc 的 golden smoke 输出与旧基线一致。

## 代表实验

### 第一阶段：WebSearch 100% 四方案同二进制对照

先只运行 `healthy_p2p_websearch_100pct`，固定 128 nodes、8 paths、现有 traffic matrices 和 seeds `13/29/47`，四个方案均使用同一次新构建的 simulator：

```text
n-mrc
n-mrc-allcool-rr-reset
n-mrc1
n-mrc2@T=0.5
```

不得复用旧 n-mrc 正式结果；四方案共 12 个单元全部重跑。按已有日志估算约 87 分钟串行 simulator 时间，按 scheme 分成四个独立 worker 时预计约 22--30 分钟墙钟时间。

主指标为三 seed `p99_fct_us` 几何均值，同时报告每 seed、p99.9/max、reroute/check、FastCNP、cooldown starts/selection、cooling skips/selection、all-cooling episode/rate、ECN/TRIM、queue CV/p99 fraction。至少输出绝对 p99 对照图、相对旧 n-mrc 加速比图和机制计数表。

第一阶段只回答 WebSearch 100% 下四个模块变量的效果，不据此声称适用于全部健康、非对称或 all-to-all 场景。

### 第二阶段：五个代表场景

复用已有 seeds `13/29/47` 和完全相同的 traffic matrix。正式新增矩阵为 n-mrc1/n-mrc2 共 30 个单元，但旧基线只有通过下列资格重放后才能复用：

1. 记录新旧 simulator binary SHA、traffic SHA、resolved config 和完整 experiment fingerprint；
2. 重放 `asymmetric_p2p_tornado_16mib/seed13` 的 SGLB、MRC、旧 n-mrc，覆盖能够触发 reroute 的低成本场景；
3. 重放 `healthy_p2p_websearch_100pct/seed13` 的旧 n-mrc，覆盖高负载 FastCNP/cooldown；
4. 比较时只忽略 runtime 和 output path；primary metric、FCT 分位数及关键机制诊断必须精确一致；
5. 任一方案不一致时，不得复用该方案旧数据，必须重跑其正式基线单元。

运行预算按已有日志估算：30 个新正式单元约 70.4 分钟串行 simulator 时间；资格重放约 7.9 分钟；若 45 个旧基线均不能复用，总串行量约 174.6 分钟。n-mrc1 和 n-mrc2 可各用一个独立 worker 并行，但同一输出单元不得被两个 worker 同时写入。

尽管三个 N-MRC 名称内部都映射到 `LB_NMRC`，`n-mrc`、`n-mrc1` 和 `n-mrc2` 的 preset 名、endpoint policy、reroute policy、阈值、FastCNP 开关必须进入 CSV `scheme`、输出目录键、manifest 和 fingerprint，禁止日志覆盖或错误缓存复用。

| 场景 | 主指标 | 目的 |
|---|---|---|
| `healthy_p2p_tornado_16mib` | p99 FCT | 健康、规则流量零差异控制 |
| `asymmetric_p2p_tornado_16mib` | p99 FCT | 检查慢上行链路下的收益是否保留 |
| `healthy_p2p_websearch_100pct` | p99 FCT | 检查当前 n-mrc 的高负载回退是否消失 |
| `healthy_alltoall_256mib_p4` | CCT | 检查健康 all-to-all 回退 |
| `asymmetric_alltoall_background_64mib_p4` | CCT | 检查非对称背景流量下的收益 |

这里 all-to-all 名称中的 `_p4` 表示 `parallel=4`，不是物理路径数；本轮五个场景固定 `paths=8`。本轮结论只覆盖表内代表性 P2P/all-to-all，不外推为完整 mixed-deployment 结论。

每个场景和 seed 比较：

```text
sglb, mrc, n-mrc, n-mrc1, n-mrc2
```

主图数值采用三 seed 几何均值，并用配套 dot/range 图或表保留每个 seed 和 min--max。P2P 主指标为 `p99_fct_us`，同时报告 p99.9/max FCT；all-to-all 主指标固定为 `all_to_all_cct_us`，不得用 p99 FCT 替代。相对 MRC 加速比统一定义为：

```text
speedup_vs_mrc = MRC_metric / scheme_metric
```

因此加速比大于 1 表示更快。

机制指标使用可比较的归一化比率，不能直接跨场景比较原始总数：

- reroute / route checks；
- no-safe / binary checks；
- FastCNP generated / reroute、arrived / generated；
- cooldown / flow 和 cooldown / arrived FastCNP；
- ECN、TRIM / data packet；
- reverse-path-blocked / binary checks；
- snapshot missing/stale / binary checks；
- spine queue CV 和 p99 queue fraction；
- `paired_actions == binary reroutes == FastCNP generated` 一致性。

正式图至少输出：

1. 五种方案的代表 P2P p99 FCT 热点图；固定大小与 WebSearch 分面并使用独立色阶或 log 色阶，避免 356 us 与 6,000--8,000 us 量级差掩盖差异；
2. 五种方案的代表 all-to-all CCT 热点图；
3. 相对 MRC 的加速比热点图，使用以 1 为中心的发散色阶；
4. n-mrc/n-mrc1/n-mrc2 的 reroute、FastCNP 和 cooldown 机制对照图或表。

所有图保留绝对数值标注，并注明较低 FCT/CCT 更好、加速比大于 1 更好。缺失或失败显示为 `NA`，禁止按 0 填充；热点图另配每-seed dot/range 图，不能用聚合热图隐藏 seed 方差。

结果说明必须包含两个已有数据限制：WebSearch 使用 digitized proxy CDF，不是论文原始 trace；`asymmetric_alltoall_background_*` 的背景流使用固定大小 dummy `TcpPacket`，trim 后可能仍保留 4096B。

## 完成标准

- 新旧单元和接口测试全部通过；
- `n-mrc-allcool-rr-reset` 每个完成 episode 恰好选择 P 次、覆盖 P 个不同 EV，随后全部 cooldown 清零；
- n-mrc1 `fastcnp_generated=0`，端侧 cooldown start/skip/recovery 为零，随机 EV 分布和可复现性测试通过；
- n-mrc2 只在 `original_score >= 0.5` 且存在 SAFE 候选时执行动作；
- n-mrc2 每次动作严格满足一个 reroute 对应一个 FastCNP generated；
- 旧 n-mrc 默认配置和 golden smoke 不变；
- WebSearch 第一阶段 12 个单元无失败、无缺失且四方案 traffic SHA 一致；
- 第二阶段启动后，30 个新实验单元无失败、无缺失且 traffic SHA 与基线一致；
- CSV、原始日志、汇总、诊断说明和四类图均可追溯到配置与 seed。

## 非目标与风险

- 不修改 DCQCN、真实 ECN echo、TRIM、RTO、SACK 或重传算法；
- 不宣称当前 1 us/5 us 快照可以保证单个包的真实 ECN 结果；
- 不针对五个代表场景分别调阈值；`T=0.5` 是统一的机制定义；
- remote snapshot 缺失或陈旧时不把路径判成 SAFE 或 CONGESTED，只增加 UNKNOWN 诊断；
- `T=0.6` 灵敏度实验不属于本方案完成范围；若主结果证明有必要，应另立消融任务，且不得混入 `n-mrc2@0.5` 主结果。
