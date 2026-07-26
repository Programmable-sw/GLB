# Grade 集合通信规模与并发权重扫描设计

## 目标

在 128 节点、8 路径、无背景流的 All-to-All 集合通信中，验证 grade 的最优权重是否随单流规模或每源并发度变化。

## 自变量

四组权重：

- equal：4/4/4/0
- gentle：4/3/2/0
- default：4/2/1/0
- sharp：8/2/1/0

两组单变量扫描：

1. 固定单流 0.5 MiB，扫描 P1/P2/P4/P8/P16/P32。
2. 固定 P4，扫描单流 0.125/0.5/2/8 MiB。

P4、0.5 MiB 是两组扫描的公共基准，只运行一次。每格使用 seeds 13、29、47，并保证同一场景/seed 的四组权重共享完全相同的 traffic hash。

## 指标

主指标为 All-to-All CCT。只把三 seed 几何均值最优且至少两个 seed 同向的权重切换列为有效场景。

机制指标包括：

- ECN marks
- trims
- NACK
- grade 等级变化次数
- 全零 profile/selection
- spine queue CV

## 输出

- 完整结果与汇总 CSV。
- 一个规模/并发 × 权重的 CCT 对比表。
- 只挑选确实不是 equal 最优的代表场景进行机制分析。
- 将验证结论补入 `experiments/n-mrc/four_scheme_risks_and_evidence.md`。

## 边界

- 不增加背景流或固定慢链路。
- 不扫描 grade 阈值。
- 不比较 avail、netaware、n-mrc。
- 该矩阵可区分规模效应和并发效应，但不宣称 ECN 等共变指标是已隔离因果。
