# 分支与工作区归档索引

整合后的唯一开发工作区是 `main-htsim`。旧分支不再合入主线代码；它们按探索目的保存在 `archive/*`，需要时可用
`git show <ref>:<path>` 查看单个文件，或用 `git worktree add <path> <ref>` 恢复完整环境。

| 归档引用 | 快照 | 探索目的 |
|---|---:|---|
| `archive/mrc-skip-policy-redesign` | `f56c95f` | MRC bad EV 跳过、退化转折和冷却轮转策略 |
| `archive/mrc-skip-baseline-sim` | `8539832` | MRC skip 策略的基线拓扑对照 |
| `archive/mrc-exact-bounded-recovery` | `6c5d9fd` | Exact+Bounded 可靠性恢复和参考实现 |
| `archive/remote-sync-and-paper-sglb` | `487f147` | 真实 GCN、状态改变即通告、15 us 限速和 paper-like SGLB |
| `archive/sglb-baseline-replay-20260715` | `69d0cf9` | 旧 SGLB baseline 和单 seed tail 指标复核 |
| `archive/sglb-nmrc-a2a-tuning` | `aa5e0d5` | SGLB/n-MRC 的 All-to-All 参数探索 |
| `archive/four-scheme-causal-comparison` | `6a0d928` | 四方案逐包选路的因果对照和路径均衡证据 |
| `archive/netaware-topk-replay-20260723` | `2e7e58f` | NetAware top-k 与 weighted selector 重放 |
| `archive/nmrc-routing-cooldown-pinned` | `c582f92` | n-MRC routing cooldown 实现对照 |
| `archive/nmrc-goodcap-narrow-exact-20260717` | `97776f1` | good-cap、narrow 和 exact 参数基线 |
| `archive/avail-bounded-p2p-seed13` | `fb8fd52` | Avail/Grade fixed5 与 bounded feedback |
| `archive/avail-grade-p2p-diagnosis` | `735fb66` | Avail bounded P2P seed 13 回归定位 |
| `archive/nmrc-exploration-sandbox` | `478216b` | 原脏工作区的 n-MRC scorer、share-cap、feedback 与 runner 全量源码快照 |
| `archive/final-512-experiment-sandbox` | `c863e81` | 512 节点最终比较工作区源码和测试快照 |
| `archive/asan-mrc-repro-20260720` | `f3c4e05` | MRC ASAN 复现源码、命令和压缩日志 |
| `archive/clean-build-diagnostic-20260721` | `96c6bf0` | clean-build 对照源码；不含可重建二进制 |
| `archive/nmrc-exploration-scripts-20260817` | `868486e` | 整合前主目录中 64 个 Python 实验/分析脚本的恢复点 |
| `archive/pre-consolidation-20260817` | `8186330` | 清理前主线安全点 |

原有 `checkpoint/*` 仍保留，分别记录 SGLB exact、MRC exact 和量化 top-k 变更前的边界。`master` 未改动。
