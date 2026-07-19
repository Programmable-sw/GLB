# SGLB / n-MRC All-to-All 调参设计说明

## 背景与目标

当前逐包 AR 在每一级交换机上重新读取本地出口队列并选择本地最优路径；SGLB
同样逐包调用选路，但额外读取下一跳交换机发布的队列状态。现有 canonical six
三-seed结果中，n-MRC 相对 AR 的 All-to-All 配对几何平均为 `0.9709x`，已经
体现全局感知收益；SGLB 为 `1.0200x`，尚未将两跳信息转化成稳定收益。

目标是在不改变 AR、不引入场景专用参数的前提下，校准 SGLB 与 n-MRC 共用的
两跳队列评分，使两种全局感知方案在 All-to-All 场景中明确优于 AR。达不到
保留门槛的方案不进入主推配置。

## 现状与约束

- AR 使用 `-ar_granularity packet`，每包在每跳比较本地量化队列。
- SGLB 每包重新抽取候选，但本地质量默认缓存 `1us`，远端 GCN 默认缓存
  `5us`；同一窗口内大量包复用同一质量排序。
- SGLB 默认四级 noisy-OR 评分、`min_choices=3`。实现按完整质量级加入候选，
  canonical All-to-All 中平均实际候选约为 `5.6--6.8` 条，大量调用的候选质量
  完全相同。
- n-MRC 保持 `encoded + better_ge3 + FastCNP + one-cycle cooldown`，不恢复
  已淘汰的 EV 模式或 reroute policy。
- 正式比较固定六个 canonical 场景、seeds `13/29/47`、相同 traffic matrix、
  Exact+Bounded transport 和现有 DCQCN variant。
- 所有跨-seed与跨场景汇总使用几何平均。

## 方案对比

### 方案一：共享全局评分、分别调选择器（采用）

- 共调 queue-pressure range、四级阈值、本地缓存周期、GCN 发布周期和老化周期。
- SGLB 额外筛选 4/8 级量化与 `min_choices=1/2/3`。
- n-MRC 固定 encoded≥3，只筛选与四级语义兼容的评分参数和候选数。

优点是能把收益归因到同一套两跳状态，同时允许两种 endpoint/switch selector
使用适合自身的候选宽度。缺点是搜索空间需要分阶段收缩。

### 方案二：SGLB 与 n-MRC 完全独立调参

更容易为每个方案找到局部最优，但难以证明收益来自统一的两跳感知，并增加
配置数量和过拟合风险，因此不采用。

### 方案三：修改 SGLB 算法

可加入精确分数 tie-break、远端增益门槛或显式 packet batching。潜在收益较大，
但超出本轮“调参验证全局感知”的范围；如果参数搜索失败，本轮直接淘汰 SGLB，
不以算法改写挽救结果。

## 推荐方案

采用方案一。先在三个 All-to-All 场景进行低成本筛选，再把候选配置放到完整
六场景三-seed中验证。AR 命令与流量文件固定，候选必须显式记录完整参数与运行
指纹，禁止按场景选择不同 profile。

## 详细设计

### 架构

新增一个独立 tuning runner，复用 canonical six 的 traffic、命令构建、解析与
Exact+Bounded 校验。runner 分为 `screen` 和 `validate` 两阶段：

1. `screen`：三个 All-to-All 场景，先用 seed 13 比较有限候选。
2. `validate`：保留每类前 2--3 个候选，运行全部六场景和三个 seed。
3. `select`：按预先固定的保留门槛选默认配置，不允许人工挑场景。

### 参数空间

共享评分候选围绕以下轴构造，使用小型正交/分阶段搜索而非全笛卡尔积：

- local snapshot：`0`, `0.25`, `0.5`, `1us`；
- GCN update：`0.5`, `1`, `2`, `5us`；
- aging：GCN 周期的 `3x` 或 `6x`；
- queue-pressure range：以当前 `0.20/0.80` 为基准，下移到能区分实际
  All-to-All 队列分布的候选；
- 四级阈值：保持严格递增，围绕当前 `0.10/0.40/0.60` 收紧 GOOD 区间；
- SGLB：4/8 levels，`min_choices=1/2/3`；
- n-MRC：4 levels、`better_ge3` 固定，`min_choices=1/2/3`。

初筛先利用 `all_zero_quality_calls`、`avg_distinct_qualities`、
`avg_candidate_choices`、`remote_snapshot_used/missing` 排除没有真正利用远端状态
的配置，再按 CCT 排序。

### 数据流与比较

每个 `(scenario, seed)` 只生成一次 traffic matrix，AR、SGLB 和 n-MRC 共用。
每个候选输出完整 command、traffic SHA-256、runtime configuration、主指标、队列
与 SGLB/n-MRC诊断。候选首先在同一 cell 内除以 AR，再计算：

- 三个 All-to-All场景的配对几何平均；
- 六场景总体配对几何平均；
- 每个场景三-seed配对几何平均；
- P2P/WebSearch guardrail。

### 保留门槛

每个方案独立判定：

1. All-to-All 对 AR 的组合几何平均不高于 `0.98x`；
2. 三个 All-to-All 场景至少两个低于 `1.0x`；
3. 剩余 All-to-All 场景不得高于 `1.03x`；
4. 六场景总体配对几何平均低于 `1.0x`；
5. n-MRC 新配置不得劣于当前 n-MRC 的 All-to-All 与总体几何平均；
6. 所有运行完成、配置校验通过、无缺失/重复 flow completion，Exact+Bounded
   恢复不变量成立。

失败的 SGLB 或 n-MRC 不写入 canonical 默认参数；对应 tuning runner 与候选
配置也不进入最终主线，只保留简洁的失败结论和可复核汇总。

### 异常与边界处理

- 非正主指标、不完整矩阵、traffic hash 不一致、配置回显不匹配立即失败。
- screening 不允许替代三-seed validation。
- 若候选依赖场景专用参数才能达标，按失败处理。
- GCN 更新加快带来的状态刷新次数和潜在控制开销单列，不把仿真内免费全局读取
  误写成线上零成本。

### 测试策略

- 单元测试覆盖候选命令、共享 traffic、矩阵完整性、几何平均与保留门槛。
- dry-run 验证所有候选参数合法且 AR 命令不含 SGLB/n-MRC 调参项。
- 小型 smoke 先验证诊断字段和缓存周期确实生效。
- 正式 screen 后只验证预注册的 top candidates；最终用六场景三-seed重跑并与
  固定 AR reference 配对。

## 风险与待确认项

- 当前 SGLB 的远端状态在仿真中通过邻居对象读取，GCN 周期表达状态陈旧度而非
  真实控制包带宽；最终报告必须明确这一抽象。
- 过短更新周期可能获得理想化收益，因此最终默认优先选择满足门槛的最慢周期，
  而不是绝对最快配置。
- 如果纯参数搜索无法使 SGLB 达标，按用户要求从主推方案中移除，不进入算法修改。
