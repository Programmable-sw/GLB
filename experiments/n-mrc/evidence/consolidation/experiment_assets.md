# 实验资产账本

本账本区分当前正式证据、历史探索和仅用于冒烟的 quick/pilot 结果。`sample_scale` 直接缩放 Poisson 到达率；因此 `0.05` 不只是减少统计样本，也把 offered load 降到目标值的 5%，不可与 `1.0` 正式结果合并或直接比较。

- `current`：当前主方案的正式证据，保留可复核的精简资产。
- `historical`：保留研究结论和 Git 来源，不把旧语义写成当前默认。
- `smoke-only`：只证明 runner 曾经跑通；写入配置记录后删除原目录。
- `superseded`：已被更完整或更新语义的正式结果取代。

清理动作必须以 `experiment_assets.csv` 为准。删除后更新 `status`、`cleanup_action` 和空间统计；保留文件由 `retained_sha256.tsv` 校验。

2026-08-17 的实际清理、空间变化、归档引用和例外保留项见
[`cleanup_report.md`](cleanup_report.md) 与 [`archive_refs.md`](archive_refs.md)。
