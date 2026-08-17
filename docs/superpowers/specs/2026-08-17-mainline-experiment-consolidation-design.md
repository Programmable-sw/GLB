# 主线与实验资产整合设计

## 目标

把当前仓库整理为一个明确的主实现、一组可复现的正式实验，以及一套可追溯但不占用大量空间的历史探索记录。整合后不再依赖散落的 worktree、ignored runner 或未提交源码来解释当前结论。

## 主方案基线

`main-htsim` 的当前实现是唯一主方案。以下语义必须保持，不从旧分支回退：

1. 标准二层拓扑固定为 64 个 Spine；每个 Leaf 有 64 个 host 下联和 64 个 Spine 上联。所有逐包方案在同一平面内使用 64 条物理路径。
2. MRC 使用 64 个 active EV 和 encoded identity 映射。ECN/TRIM 把对应 EV 标为一次性 skip；普通轮询访问到该 EV 时消费一次 skip，随后恢复。全 EV 同时 skip 时仍通过同一个轮询过程逐项消费，不增加清空状态、最早截止时间选择或随机 fallback。
3. 默认 `sglb` 使用真实 GCN 控制包和版本语义。状态改变触发通告，同一 producer 的连续通告至少间隔 15 us；最优质量档至少选 24 条路径，不足时按质量档依次补齐到 24；候选集合使用确定性 shuffled round-robin。
4. 原有 SGLB 行为仅通过 `sglb-old` 暴露。旧实验如果使用旧语义，必须明确标为 historical，不能写成当前 `sglb` 结果。

主线已有的语义测试是整合门禁。旧分支中的核心 C++ 代码不得整体 merge；只有在主线确实缺少独立 runner、测试、报告或数据索引时才选择性移植。

## `sample_scale` 口径

`sample_scale` 直接乘在 Poisson 流到达率上，同时缩放过渡探针数量。它不是对完整 workload 的事后抽样。

- `sample_scale=1.0` 使用完整目标 offered load。
- `sample_scale=0.05` 将 Poisson offered load 降为目标值的 5%，且每档过渡探针由 16 条降为最少 1 条。

因此 quick 数据只用于冒烟验证。它不能与正式数据合并、平均，也不能用于替代正式负载下的性能结论。

## Git 整合策略

### 主线

所有正式修改落在 `main-htsim`。整合期间先建立安全快照引用，随后用小提交完成以下工作：

- 修正实验脚本的跟踪规则；
- 纳入正式结果所需 runner 和测试；
- 纳入历史实验索引和精简证据；
- 修正文档中与当前 64-path MRC、当前 SGLB 语义冲突的旧描述。

### 历史分支

有独立研究价值的分支统一保留为 `archive/` 或现有 `checkpoint/` 引用。已被主线吸收的普通别名分支在确认 archive 引用后可以删除。上游 `master` 和远端跟踪分支不改动。

下列内容按历史研究保留，不重新合并旧核心实现：

- n-MRC share-cap、top-k、GoodCap、cooldown 和公开 preset 探索；
- Avail、Grade、Netaware 与 n-MRC 的四方案对比和因果消融；
- SGLB/n-MRC All-to-All 参数筛选；
- exact-bounded 恢复和旧 SGLB 参数探索。

### Worktree

删除 worktree 前必须逐个完成：

1. 记录 HEAD、分支、dirty 文件和磁盘占用；
2. 判断 dirty 内容是否已进入主线；
3. 将仍独有且有价值的 runner、测试、报告或补丁放入 archive 引用或主线；
4. 排除二进制、对象文件、ASAN 输出和可重建日志；
5. 确认对应分支或 archive 引用仍可定位源码。

禁止直接丢弃未审计的 dirty worktree。

## 实验资产分级

### A：当前正式证据

必须保留报告、manifest、汇总 CSV、绘图源数据、图、完整命令或命令模板、模拟器 SHA、traffic SHA，以及复现所需 runner：

- `mrc_cold_qp_flow_size_evidence_256`；
- `mrc-new-result`；
- 当前逐包方案 P2P/A2A 正式比较；
- 当前 n-MRC 综合结果和仍被引用的机制诊断。

若逐流数据是现有结论不可替代的输入，则保留确定性压缩文件；若报告和汇总可以从更小的配对表完整复核，则删除重复 raw stdout。

### B：历史探索证据

保留以下最小集合：

- manifest 或等价配置记录；
- 汇总 CSV 和结论报告；
- 有解释价值的图；
- runner/分析脚本所在 Git 引用；
- 原始目录名称、生成时间、大小、状态和清理原因；
- 能取得时记录模拟器、traffic 和命令 SHA。

原始 cell 目录、重复 traffic、完整 stdout 和中间缓存无需保留。

### C：试跑与可重建数据

以下内容在写入清理账本后删除：

- 名称含 `quick` 或 `pilot` 的输出；
- 已有正式版本覆盖的单 seed、缩小负载或早期配置结果；
- 重复图、缓存、临时锁和 partial 文件；
- 编译产物、ASAN/反汇编日志及本地诊断二进制；
- 可以由保留 runner 和 traffic 定义重新生成的中间文件。

## 追溯账本

仓库新增受 Git 跟踪的实验资产账本，至少包含：

- 资产 ID 和原目录；
- `current`、`historical`、`smoke-only` 或 `superseded` 分类；
- 拓扑、方案、seed、负载和 `sample_scale`；
- runner、源码提交和相关 archive 分支；
- manifest/summary/report/figure 的保留位置；
- 可用的 SHA；
- 删除内容、删除原因和回收空间。

账本同时说明 quick 结果不可与正式结果比较，避免以后误引用。

## `.gitignore` 与复现性

删除 `/experiments/n-mrc/*.py` 的全局忽略策略，改为明确跟踪正式 runner、共享解析器和必要分析脚本。一次性本地探针如果不进入主线，必须在历史 archive 分支中可定位，不能只存在于某个 worktree。

实验输出目录仍默认忽略；只有经过精简的报告、manifest、CSV、图和账本使用显式例外纳入 Git。

## 清理安全性

所有删除遵循以下顺序：

1. 生成删除候选清单和删除前磁盘统计；
2. 对保留文件生成 SHA-256；
3. 提交追溯账本与精简证据；
4. 创建清理前 Git archive 引用；
5. 删除 raw 目录和 worktree；
6. 重新计算磁盘统计并验证保留链接。

不会删除远端分支、上游引用或用户未授权范围内的其他仓库。删除后的 raw 仿真输出不保证可恢复，但其配置、命令、摘要和代码来源必须可追溯。

## 验证门禁

整合完成必须满足：

- 主工作区无未解释的 tracked 或重要 ignored 源码；
- 64-spine、MRC skip、SGLB GCN/min24/RR 和 `sglb-old` 测试通过；
- 正式 runner 的单元测试通过；
- A 类正式证据的 manifest、CSV、报告和图链接有效；
- 历史探索均可从账本定位到 Git 引用；
- `quick` 和 `pilot` 不再出现在正式结果入口；
- worktree 列表只保留仍在使用的工作区；
- 输出删除前后空间统计写入账本。

## 非目标

- 不重新运行耗时的完整 P2P/A2A 或 256-node 正式矩阵；
- 不改变当前 MRC、SGLB 或 n-MRC 算法；
- 不把旧拓扑数据重标为 64-spine 当前结果；
- 不把历史探索结果升级为当前主方案结论；
- 不推送远端或创建 PR。
