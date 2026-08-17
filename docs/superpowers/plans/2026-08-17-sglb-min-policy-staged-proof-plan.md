# SGLB min 与候选策略分阶段验证实施计划

**目标：** 用可复现的两阶段单 seed 实验确定 `min_choices`，并比较三种候选集合策略。

**设计：** 在 FatTreeSwitch 中增加实验策略枚举，默认保持 `exact_min`。Python runner
统一生成相同前景流量，分别运行无背景和周期背景场景，先选 min，再固定 min 比策略。

## 任务 1：定义三种策略的失败单测

**文件：**
- 修改 `sim/tests/main_paper_sglb.cpp`
- 修改 `sim/datacenter/fat_tree_switch.h`

1. 添加能区分 `strict_k`、`whole_grade_min`、`exact_min` 的档位样例。
2. 添加默认策略为 `exact_min` 的断言。
3. 运行现有 paper SGLB 单测并确认因接口缺失而失败。

## 任务 2：实现策略接口

**文件：**
- 修改 `sim/datacenter/fat_tree_switch.h`
- 修改 `sim/datacenter/fat_tree_switch.cpp`
- 修改 `sim/datacenter/main_roce.cpp`

1. 增加策略枚举、解析/打印接口和 strict-K helper。
2. 在 real-GCN quantized top-k 路径中按策略选择候选；共用 tie key 与 shuffled RR。
3. 增加 `-sglb_candidate_policy strict_k|whole_grade_min|exact_min`，默认 exact_min。
4. 运行 paper SGLB 单测直至通过，并验证非法 CLI 值失败。

## 任务 3：定义分阶段 runner 的失败单测

**文件：**
- 新建 `sim/tests/test_sglb_min_policy_staged_256_runner.py`

1. 断言阶段一恰有 7 min × 2 场景，前景 traffic SHA 相同。
2. 断言阶段一只使用 exact_min；阶段二固定选择出的 min 并生成三策略 × 两场景。
3. 断言 1% 对称 CCT 约束与非对称 CCT/tie-break 选择规则。
4. 断言完整流数、gcn_stale、缓存指纹与输出字段验证。
5. 运行测试并确认因 runner 缺失而失败。

## 任务 4：实现分阶段 runner

**文件：**
- 新建 `experiments/n-mrc/run_sglb_min_policy_staged_256.py`

1. 复用既有 A2A 流量生成和指标解析函数。
2. 实现 symmetric/asymmetric 命令构建、manifest 和逐单元指纹。
3. 实现阶段一汇总、min 选择、完整指标排名。
4. 实现阶段二汇总、三策略选择与 Pareto 判断。
5. 将 `remote_snapshot_missing` 保留为指标，只把不完整流与 gcn_stale 设为硬失败。
6. 支持 `--stage min|policy|all`、断点续跑、`--dry-run` 和受控 workers。
7. 运行 runner 单测直至通过。

## 任务 5：构建与静态验证

1. 构建 `sim/datacenter/htsim_roce` 和 paper SGLB 单测。
2. 运行 C++ 与 Python 相关单测。
3. dry-run 全部 18 个唯一单元，检查命令、连接数、SHA 和 manifest。
4. 提交实现与测试。

## 任务 6：运行阶段一

1. 用 seed 13、受控并行运行 14 个单元。
2. 持续记录完成数与失败单元，超时可断点续跑。
3. 校验所有单元 65,280/65,280 flows、`gcn_stale == 0`。
4. 按预注册规则选出最终 min，生成完整指标排名。

## 任务 7：运行阶段二

1. 固定阶段一选出的 min，运行缺少的 4 个策略单元并复用匹配的 exact_min 单元。
2. 生成三策略 × 两场景汇总、完整排名与 Pareto 判断。
3. 明确回答 exact_min 是否被实验支持。

## 任务 8：最终数据质量与交付

1. 检查配置/二进制/traffic 指纹、重复单元和缺失值。
2. 复算关键排名与 1% guardrail。
3. 保存可追溯日志、CSV、manifest 和 Markdown 结论。
4. 运行最终测试与 `git diff --check`，只提交本任务文件，不触碰用户已有删除项。
