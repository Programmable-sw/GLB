# MRC 与 SGLB 高压力流大小转折对比

## 结论

原 `focused_transition.png` 比较的是 **MRC 与 RR**。其中 RR 是 MRC
相同端侧 EV 编码和轮转选路器的无反馈版本，因此该对比适合隔离 MRC
反馈学习的增量价值。

本轮保持原实验输入不变，将比较对象换成当前默认 **SGLB**。结果与
MRC/RR 明显不同：

- 短流中 MRC 没有 actionable feedback，而 SGLB 可以直接使用交换机侧
  跨流状态；80% 负载下，6–133 KiB 短流的 MRC/SGLB 配对 FCT 几何均值
  为 **1.0243**，MRC 总体慢约 2.4%；
- 中流中两者基本持平：60% 和 80% 负载下分别为 **0.9968** 和
  **0.9997**；
- 长流中 MRC 虽然有充分反馈，但没有像相对 RR 时那样形成明显优势：
  60% 和 80% 负载下分别为 **1.0032** 和 **1.0088**；
- 因此，“MRC 随流变长开始优于无状态 RR”不能外推为“MRC 随流变长
  必然优于已有状态感知方案”。在这组持续路径差异输入中，SGLB 已经
  消化了大部分可由路径状态带来的收益。

![MRC versus SGLB pressure transition](output/mrc_sglb_pressure_transition_examples_5ms/focused_transition.png)

图中上半部分是严格配对后的 `MRC FCT / SGLB FCT` 几何均值，小于 1
表示 MRC 更快；下半部分仍是 MRC 自身的 actionable-flow 比例，只用于
解释 MRC 反馈闭环何时具备作用机会，不是 SGLB 的反馈指标。

## 控制变量

本轮直接复用原 MRC/RR 聚焦实验的输入：

- 128 个节点、8 条物理路径；
- WebSearch 已有离散流大小，6 KiB–30 MiB；
- 60% 和 80% offered load，5 ms 持续到达窗口，seed 13；
- 随机稀疏选择 4 条 ToR 上行，将速率降为正常路径的 1/2；
- 相同 traffic 文件、连接数、链路速率、队列、DCQCN、Exact+Bounded
  可靠性语义、MTU、hop/switch latency 和仿真截止时间；
- MRC 与 SGLB cell 使用相同 simulator SHA-256
  `05b10989885b85e39b2734e10a7cadaeb4b6fc86a56537f69ae6abbdeae22cec`。

唯一的方案入口差异是 `-lb mrc` 与 `-lb sglb`。但这不是控制变量消融：

- MRC 在源端为每个 QP 独立维护 EV 状态，等待端到端反馈；
- SGLB 在交换机侧使用跨流共享的路径状态，默认每 1 μs 更新本地质量、
  每 5 μs 更新下游 GCN cache，以四级量化和最少三个候选进行逐跳选路。

所以本实验回答的是两个完整方案在相同输入下的性能差异，而不是把差异
全部归因于“是否跨 QP 共享”。

## 分流粒度结果

以下比值均先按相同 `flow_id/src/dst/start/flow_size` 严格配对，再对逐流
FCT 比值取几何均值：

| 负载 | 流粒度 | 配对流 | MRC/SGLB FCT | MRC 胜出流 | MRC p99 / SGLB p99 | 解释 |
|---:|---|---:|---:|---:|---:|---|
| 60% | 6–133 KiB | 6,626 | 1.0090 | 50.4% | 0.9811 | 中心分布基本持平，MRC 总体略慢 |
| 60% | 667 KiB–1.3 MiB | 2,206 | 0.9968 | 52.5% | 0.9423 | MRC 仅有很小的总体优势，但该组 p99 更低 |
| 60% | 3.3–30 MiB | 2,245 | 1.0032 | 53.2% | 0.9796 | 几何均值持平，尾部略偏向 MRC |
| 80% | 6–133 KiB | 8,789 | **1.0243** | 48.1% | 0.9995 | MRC 无本流反馈收益，SGLB 中心分布更好 |
| 80% | 667 KiB–1.3 MiB | 2,946 | 0.9997 | 51.1% | 1.0079 | 基本持平 |
| 80% | 3.3–30 MiB | 3,013 | **1.0088** | 50.0% | 1.0128 | MRC 反馈充分，但 SGLB 仍略好 |

“MRC 胜出流”是 `MRC FCT < SGLB FCT` 的配对流比例。它接近 50% 表明
两方案逐流结果高度交叠，不能只根据少数流大小点宣称全面胜出。

## 离散流大小转折

| 负载 | 流大小 | MRC actionable flow | MRC/SGLB FCT | 判断 |
|---:|---:|---:|---:|---|
| 60% | 6 KiB | 0.0% | 1.0012 | 持平 |
| 60% | 133 KiB | 0.0% | 1.0031 | 持平 |
| 60% | 667 KiB | 41.2% | 0.9948 | MRC 略好 0.5% |
| 60% | 1.3 MiB | 52.3% | 0.9989 | 持平 |
| 60% | 3.3 MiB | 71.4% | 0.9956 | 持平 |
| 60% | 6.7 MiB | 82.0% | 1.0071 | SGLB 略好 |
| 60% | 20 MiB | 96.4% | 1.0046 | 持平 |
| 60% | 30 MiB | 96.7% | **1.0590** | SGLB 更好；该点只有 90 条流 |
| 80% | 6 KiB | 0.0% | 0.9903 | 持平 |
| 80% | 133 KiB | 0.0% | 1.0220 | SGLB 略好 |
| 80% | 667 KiB | 56.3% | 1.0035 | 持平 |
| 80% | 1.3 MiB | 68.4% | 0.9956 | 持平 |
| 80% | 3.3 MiB | 83.0% | 1.0053 | 持平 |
| 80% | 6.7 MiB | 91.8% | 1.0116 | SGLB 略好 |
| 80% | 20 MiB | 99.1% | 1.0067 | 持平 |
| 80% | 30 MiB | 96.9% | **1.0311** | SGLB 更好；该点只有 131 条流 |

这组结果没有出现 MRC/RR 图中的“3.3 MiB 后 MRC 明显胜出”转折。
actionable 比例仍随流大小从 0 增至接近 100%，但 MRC/SGLB 比值仍围绕
1 波动。直接机制解释是：

1. 相对 RR，MRC 的反馈学习是在“无路径状态”之上增加价值；
2. 相对 SGLB，比较对象本身已经通过交换机侧周期状态避开持续慢路；
3. MRC 长流获得的学习收益主要用于追平 SGLB，而不是叠加成新的
   6%–10% 优势；
4. SGLB 不需要等待当前 QP 的端到端反馈，因此对短流更有条件产生收益。

## p99 与几何均值为什么可能方向不同

逐流比值几何均值衡量典型配对流的相对变化；`MRC p99 / SGLB p99` 是两组
FCT 分布分别取 p99 后再相除，两者不是同一个统计量。例如 60% 长流中，
几何均值为 1.0032，表示总体持平且略偏 SGLB，但 p99 比值为 0.9796，
表示 MRC 的该组尾部低约 2.0%。因此本轮结论应保留为“总体接近，部分负载
和尾部各有优劣”，而不是只选其中一个指标宣布胜负。

## 实验限制

- 本轮按“跑一组类似实验”的范围复用原聚焦实验，只使用 seed 13；
- 30 MiB 点只有 90/131 条流，5.9%/3.1% 的差异需要三个 seed 才能判断
  是否稳定；
- SGLB cache 通过仿真中的交换机对象共享，没有计入真实 GCN 控制报文的
  传播、带宽、排队和丢失成本；
- 本轮仍是持续带宽差异，不包含动态故障；
- 结果不能用于证明跨 QP 共享一定更优，只能说明当前完整 SGLB 在该输入下
  已经达到与 MRC 相当或略好的 FCT。

## 数据与复现记录

- 运行与聚合脚本：[`run_mrc_sglb_pressure_transition.py`](run_mrc_sglb_pressure_transition.py)
- cell 与 SHA 校验：[`cells.csv`](output/mrc_sglb_pressure_transition_examples_5ms/cells.csv)
- 分流大小汇总：[`focused_summary.csv`](output/mrc_sglb_pressure_transition_examples_5ms/focused_summary.csv)
- 逐流严格配对数据：`output/mrc_sglb_pressure_transition_examples_5ms/paired_flow_metrics.csv.gz`
- PNG 图：[`focused_transition.png`](output/mrc_sglb_pressure_transition_examples_5ms/focused_transition.png)
- PDF 图：[`focused_transition.pdf`](output/mrc_sglb_pressure_transition_examples_5ms/focused_transition.pdf)
- SGLB 原始命令、stdout 和 cell summary：`output/mrc_sglb_pressure_transition_examples_5ms/raw/`
