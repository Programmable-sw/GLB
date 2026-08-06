# MRC skip-token 版本对齐审计

## 结论

当前 `main-htsim` 工作树原先只把 MRC/RR 修正为 64 个 active EV，实际 MRC 仍使用 legacy `one_cycle` cooldown，并没有与另一对话中的 skip-token 版本对齐。

本次已将 skip-token 核心实现对齐到 `codex/mrc-skip-policy-redesign@f56c95f`：

- 默认策略为 `skip_token`；
- 固定 64 个 GOOD EV，EV 与物理路径一一映射，无 backup；
- ECN ACK 或 TRIM NACK 精确归因到 EV 后，只执行一次 `GOOD -> SKIP`；
- EV 已处于 SKIP 时，旧的重复反馈不 rearm、不延长避让；
- token 只在 rotation cursor 真正到达该 EV 的 nominal slot 时消费；
- 消费 token 表示该 EV 恰好失去一次发送机会，随后恢复 GOOD；
- 全部 EV 都为 SKIP 时依靠普通 rotation 自然推进，不使用 legacy all-cooling fallback；
- DATA 不允许选择非 GOOD EV；
- ACK/SACK 可靠性状态与 EV 负载均衡状态保持独立；
- 故障恢复接口默认关闭，本实验不纳入故障场景。

`sim/roce.cpp`、`sim/roce.h`、`sim/network.*` 和 `sim/rocepacket.*` 已与该工作树逐文件比较，核心文件无差异。主程序保留了当前分支后来确定的标准拓扑：64 spine、每 Leaf 64 上联和 64 下联，规模只通过 Leaf 数变化。因此当前二进制与旧工作树二进制 SHA 不相同，但 skip-token 状态机相同。

## 查找范围和版本时间线

本次检查了仓库全部 refs、全部已登记 worktree，以及本机 Codex 会话目录中命中 `skip-token`、`skip_rotation` 或分支名的记录。

关键实现提交按时间为：

| 提交 | 时间 | 内容 |
|---|---|---|
| `39204b5` | 2026-08-02 22:34 | 增加统一策略接口 |
| `144a363` | 2026-08-02 22:41 | 64-EV canonical profile |
| `15ac9ca` | 2026-08-02 22:48 | 集中 GOOD/SKIP 状态转换 |
| `03ee760` | 2026-08-02 22:51 | 实现 skip-token selector |
| `6cdb006` | 2026-08-02 22:53 | 增加 skip-rotation 消融 |
| `ff9f62b` | 2026-08-02 22:56 | 回归 ACK/SACK 与 LB 状态解耦 |
| `afa9a4c` | 2026-08-02 23:01 | 增加默认关闭的 probe 接口 |
| `a1de17f` | 2026-08-03 10:15 | 修正策略比较为 64 active EV |
| `f56c95f` | 2026-08-03 16:48 | 最新实验与绘图提交，分支工作树干净 |

本机命中的决定性对话是 2026-08-02 的会话：用户明确要求 64 条真实等价路径、64 EV、`GOOD/SKIP`、skip-token 与 skip-rotation，并确认默认使用 skip-token。没有发现时间更晚、但未进入该工作分支的另一套 MRC 状态机修改。当前会话记录中的其他命中主要是对该设计和本次审计的引用。

这个审计只能覆盖当前机器上可访问的会话文件和 Git 对象；不在本机保存、也没有同步为分支或文档的远端对话无法检索。

## 与旧 64-active 修正版的控制变量对比

对比使用完全相同的 256 节点、4 Leaf、64 spine、同一 traffic SHA、同一三个 seed、每 seed 72 条流：

- 背景压力：16 条 spine 各持续注入 390 Gbit/s；
- 被测流启动时间：0.5、2、5、10、25、100、500、2000 μs；
- 流大小：6、33、133、256 KiB，3.3、6.5 MiB；
- RR、SGLB、传输、DCQCN、SACK、TRIM recovery 均不变；
- 唯一目标机制变化是 MRC 从 64-active `one_cycle` 改为 64-active `skip_token`。

下表只统计背景已运行至少 25 μs 的逐流配对几何均值：

| cohort | 指标 | legacy one-cycle | skip-token | 变化 |
|---|---|---:|---:|---:|
| 6–133 KiB | MRC/SGLB | 1.001830 | 1.001631 | -0.0199% |
| 6–133 KiB | MRC/RR | 0.999093 | 0.998895 | -0.0199% |
| 256 KiB | MRC/SGLB | 0.988593 | 0.988178 | -0.0420% |
| 256 KiB | MRC/RR | 1.001312 | 1.000892 | -0.0419% |
| 3.3–6.5 MiB | MRC/SGLB | 1.008739 | 1.008551 | -0.0186% |
| 3.3–6.5 MiB | MRC/RR | 0.996159 | 0.995974 | -0.0186% |
| 3.3–6.5 MiB | 压力/健康 FCT | 1.044509 | 1.044315 | -0.0186% |

负值表示 skip-token 更好。三个 cohort 的方向均略好，但幅度只有约 0.02%–0.04%，不能称为显著性能提升。

短流严格 actionable 比例仍为 0；256 KiB 虽能完成一轮 64-EV sweep，但反馈返回后没有新的原始 DATA 选择。skip-token 因而主要改变长流反馈后的路径序列，不会凭空消除短流的一 RTT 冷启动限制。

长流 actionable 从 legacy 的 33.3% 变为 skip-token 的 29.2%。这不是实现失效：actionable 是“状态改变后还有新 DATA”的逐流布尔量，策略改变后队列、反馈到达和完成时刻也会变化。运行诊断同时确认 `skip_opportunities_consumed > 0`、`duplicate_feedback_ignored > 0`、`data_on_non_good_violations=0`，说明新状态机确实执行。

## 为什么没有出现明显更好的数据

“版本更新”不等于在任意负载中 FCT 必然显著降低。skip-token 的设计目标是把一次拥塞反馈严格解释为“跳过下一次该 EV 的机会”，避免 one-cycle 的 deadline、重复反馈和 all-cooling fallback 引入不清晰的避让强度。它首先提高模型语义的可解释性。

当前固定热点场景中只有 16/64 路受压，而 MRC 每次反馈只跳过一个 nominal opportunity；RR 本身已经把流量均匀喷洒到 64 路，长流 MRC/RR 原本就只有约 0.4% 的空间。新策略只能产生很小的增量。最新 skip-token 分支自己的 100% WebSearch 健康矩阵也大多得到接近 1.0 的 MRC/RR，而不是普遍的大幅改善。

旧分支的 A2A 单 seed 结果中，skip-token 相对旧 32-active 基线曾得到约 1.7%–3.3% 的 CCT 改善，但该比较同时改变了 active EV 数和拓扑适配，不能作为 skip-token 的纯因果证据；同一 64-active 策略矩阵里 legacy one-cycle 在部分 cell 还可能更好。因此本次报告保留实测结论，不把轻微改善夸大为稳定优势。

## 验证状态

- skip-token 状态机语义测试通过；
- RR/legacy MRC 选路回归测试通过；
- CLI 默认策略、非法 profile、旧参数兼容和故障开关测试通过；
- 主仿真器全量构建通过；
- 三 seed、216 flow 的 skip-token 轻量矩阵完成；
- 所有 MRC cell 均为 64 active、0 backup、故障恢复关闭；
- `forced_cooling_use=0`，`data_on_non_good_violations=0`。

最新结果位于 `output/mrc_sglb_cold_qp_256_skip_token_quick/`，完整机制解释见其中的 `mrc_cold_qp_limitations_256.md`。
