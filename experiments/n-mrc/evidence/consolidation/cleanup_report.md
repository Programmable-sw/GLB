# 2026-08-17 实验区整合结果

## 主线定义

`main-htsim` 继续采用当前主方案：固定 64 spine/64 平面内路径；MRC 使用 64 active EV 和 `skip_token` 一次跳过语义；默认 SGLB 使用真实 GCN、状态改变触发且生产端最短 15 us 间隔、最佳档精确 min24 并向下补足 24、候选内 shuffled RR；旧实现通过 `sglb-old` 进入。

主线只直接维护两个生命周期证据 runner：

- `run_mrc_sglb_cold_qp_256.py`
- `run_mrc_sglb_steady_mixed_256.py`

其他历史 runner 已固化在 [archive_refs.md](archive_refs.md) 指向的归档引用中，不再作为当前默认入口。

## `5ms` 和采样规模

`mrc_pressure_transition_examples_5ms` 与 `mrc_sglb_pressure_transition_examples_5ms` 中的 `5ms` 表示流到达窗口持续 **5 毫秒**。它用于形成持续路径压力，不是 SGLB 的 5 us 或 15 us 通告周期。对应的旧校准目录没有 `_5ms`，使用约 1 ms 到达窗口。

`sample_scale` 直接乘到 Poisson 到达率：

- `1.0` 是正式负载和正式采样规模；完整生命周期实验使用 seeds `13,29,47`、短流 10%/长流 56% offered load、每个 size/seed 16 个 transition probe，稳态约 17,725 条流。
- `0.05` 把到达率降为正式值的 5%；对应负载约为 0.5%/2.8%，通常只用 seed 13、每档至少 1 个 probe、约 272 条流。

因此 quick 结果不是从正式结果中抽取 5% 行，而是运行了一个更稀疏、排队压力不同的仿真。它只能检查 runner 是否能跑通，数据不能与 `1.0` 直接合并或用于性能结论。旧 quick 还曾使用 60% 长流目标、较少 condition 和不同 simulator SHA，这进一步解释了它与正式目录的差异。

## 保留内容

- 当前 MRC 生命周期证据：manifest、theory checks、汇总 CSV、压缩配对指标、报告和图表。
- `mrc-new-result/heatmap` 的矩阵、验证记录和图表。
- `n-mrc-results` 的 manifest、results/summary、诊断报告、压缩崩溃日志、主要图表、paper-style CSV/PDF/PNG 和 trace manifest。
- 各逐包方案 P2P/A2A 的现有汇总、报告、图表和命令索引。
- 没有任何替代汇总的四组小型根因日志：`mrc_cooldown_recovery_multiscenario_512`、两个 `mrc_shortflow_rootcause` 目录和 `current_async_real_gcn_root_cause_seed29`。
- 112 个当前保留证据文件的校验值见 `retained_sha256.tsv`。

## 删除内容

- 23 个仍存在的 quick/pilot 顶层目录；另外两个早先的 lifecycle quick 目录在清理前已不存在，账本记录为 `already-absent`。
- 已有 manifest/summary/results/report/figure 替代的 `raw/`、`traffic/`、partial CSV 和逐流中间 CSV。
- 所有辅助 worktree，以及其中可重建的 build 输出和二进制。
- 主目录未跟踪的历史脚本副本；其内容先保存到 `archive/nmrc-exploration-scripts-20260817`。

实验 `output` 从 11,409,171,259 字节降至 698,259,983 字节，共释放 10,710,911,276 字节（约 9.98 GiB）。全部 worktree 路径从 15 个降为 1 个；整合前所有 worktree 合计 31,470,491,682 字节，整合后主仓库约 1,559,898,733 字节。后一个差值还包括重复 checkout、build 产物和各工作区本地输出，不能视为单纯实验数据压缩率。
