# 清理前盘点

- 基线分支：`main-htsim`
- 基线提交：`8186330ce0f0126613462d7756c943815cb32369`
- 安全引用：`archive/pre-consolidation-20260817`
- 盘点日期：2026-08-17

本盘点显式包含 ignored 文件。旧的 `.gitignore` 使用 `/experiments/n-mrc/*.py` 隐藏了正式 runner 和历史研究脚本，因此普通 `git status` 不能代表实验源码完整性。

`worktrees_before.tsv` 记录每个 worktree 的源码状态与占用；`branches_before.tsv` 记录相对主线的提交关系；`storage_before.tsv` 记录所有顶层实验输出与辅助 worktree 的清理前字节数。后续删除必须能够在实验资产账本或 archive 引用中找到对应追溯记录。

