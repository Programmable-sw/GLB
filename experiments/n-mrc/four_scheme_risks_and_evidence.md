# avail、grade、netaware、n-mrc：已验证的不足与对比

> **历史拓扑说明：** 本文现有数值来自旧的 128 节点、8-Spine 自动拓扑，
> 不属于当前固定 64-Spine、每 Leaf 64 上联/64 下联的标准拓扑。它们只保留
> 为历史机制证据；在标准拓扑重跑前，不得作为最终标准拓扑性能结论。

## 结论

没有一个方案或一组权重在所有负载中最优。

- `avail` 的长期累计路径 CV 最低，但不能由此推出 FCT/CCT 最好。真正被实验验证的问题是默认 packet 老化会长期保留全零可用路径表；hybrid 恢复在 P4、P16 中稳定改善 CCT。
- `grade` 的问题不是“分级太细”，而是权重和阈值与负载形态耦合。陡权重适合非对称 P2P，却损害 WebSearch 长流尾部和 All-to-All CCT。
- `netaware` 同样没有通用权重。非对称 P2P 偏好陡权重，WebSearch 偏好 gentle，P16 偏好默认权重且关闭 good-share-cap。
- `n-mrc` 的 `better_ge3` 不是“不换路”，而是只在四级差至少 3 时换路。在非对称 P2P 中，它比 `any_better` 少换路、少 ECN/重传且更快，说明该条件在这个场景并不保守过头。

本报告只把多 seed 干预实验支持的现象列为不足。未验证的推测单独放在文末。

## 实验口径

- 128 节点、8 路径、400 Gbit/s、DCQCN variant、精确有界恢复。
- seeds：13、29、47；同一场景和 seed 的所有变体使用同一个 traffic SHA。
- 非对称 16 MiB permutation 和 WebSearch 80% 使用 p99 FCT。
- P4 64 MiB、P16 256 MiB 使用 All-to-All CCT。
- 表中为三 seed 几何均值，百分比均为配对结果。

### 场景参数到底代表什么

| 场景 | 流数 | 单流大小 | 每源同时活动流上限 | 每源批次数 | 每源标称总量 | 拥塞形态 |
|---|---:|---:|---:|---:|---:|---|
| 非对称 16 MiB | 128 | 16 MiB | 1 | 1 | 16 MiB | 所有流同时开始；每个 ToR 的 8 条上联中随机固定 4 条降为半速 |
| WebSearch 80% | 28,640–29,191 | 长尾分布 | 到达驱动 | 不适用 | 80% offered load，持续注入 10 ms | 无固定慢链路；随机到达、流大小混合 |
| P4 64 MiB | 16,256 | 0.5 MiB | 4 | 32 | 约 63.5 MiB | 全局 All-to-All；每源一批 4 条，完成后触发下一批 |
| P16 256 MiB | 16,256 | 2 MiB | 16 | 8 | 约 254 MiB | 全局 All-to-All；每源一批 16 条，完成后触发下一批 |

说明：

- `P4/P16` 中的 `P` 是每个源同时发送的流数，不是路径数；两者都使用 8 条网络路径。
- `64/256 MiB` 是传给流量生成器的每源标称消息量。生成器除以 128 得到单连接大小，所以 P4 为 0.5 MiB，P16 为 2 MiB。
- 每个源最终都向另外 127 个节点发送，因此实际每源总量分别为 `127 × 0.5 = 63.5 MiB`、`127 × 2 = 254 MiB`。
- P4 每批全网最多有 `128 × 4 = 512` 条活动流；P16 最多有 `128 × 16 = 2048` 条。P16 的单批在途标称数据也从 P4 的 256 MiB 增至 4 GiB。

原始及汇总数据：

- [results.csv](output/four_scheme_parameter_matrix_20260726/results.csv)
- [summary.csv](output/four_scheme_parameter_matrix_20260726/summary.csv)
- [flow_size_bins.csv](output/four_scheme_parameter_matrix_20260726/flow_size_bins.csv)
- [transitions.csv](output/four_scheme_parameter_matrix_20260726/transitions.csv)

## 1. grade：权重没有全局最优

权重：

- equal：`4/4/4/0`
- gentle：`4/3/2/0`
- default：`4/2/1/0`
- sharp：`8/2/1/0`

| 场景 | equal | gentle | default | sharp | 最优 |
|---|---:|---:|---:|---:|---|
| 非对称 16 MiB p99 FCT | 527.5 | 488.6 | 467.4 | **461.6** | sharp |
| WebSearch p99 FCT | 3810.3 | **3808.1** | 3876.5 | 4045.3 | gentle/equal |
| 63.5 MiB P16 CCT | **7244.9** | 7331.2 | 7522.9 | 7729.1 | equal |
| 254 MiB P4 CCT | **2158.5** | 2254.4 | 2245.7 | 2311.9 | equal |

反转是三 seed 同向的：

- 非对称 P2P：sharp 比 equal 快 12.5%。
- P16：sharp 比 equal 慢 6.7%。
- P4：sharp 比 equal 慢 7.1%。
- WebSearch：sharp 比 gentle 慢 6.2%。

### 为什么会反转

| 场景/权重 | ECN marks | trims | NACK | 结果 |
|---|---:|---:|---:|---|
| 非对称 equal | 9,836 | 61 | 363 | 527.5 μs |
| 非对称 sharp | **7,343** | **2** | 458 | **461.6 μs** |
| P16 equal | 4,760,866 | **403,445** | **539,642** | **7244.9 μs** |
| P16 sharp | 5,531,291 | 468,772 | 596,636 | 7729.1 μs |

非对称场景中，sharp 与更低的 ECN、trim 同时出现；P16 中，sharp 则与更高的 ECN、trim、NACK 和更差 CCT 同时出现。数据证明了权重反转及这些机制指标的同向变化，但没有按时间记录 GOOD 路径流量集中度，因此不把“热点迁移”写成已隔离因果。

### 是流大小导致反转，还是拥塞模式

不能把反转只归因于流大小，理由如下：

1. 非对称场景的单流最大，为 16 MiB，却偏好 sharp。
2. P16 单流为 2 MiB，P4 单流只有 0.5 MiB，相差 4 倍，但两者都偏好 equal、都不喜欢 sharp。
3. WebSearch 在同一个拥塞模式内部又出现流大小取舍：sharp 改善短、中流，却恶化大于 1 MB 的长流尾部。

因此证据支持两层作用：

- **跨场景的主要区别是拥塞形态。** 非对称 P2P 有持续存在的半速路径，等级具有较强的长期含义；sharp 强化 GOOD 路径偏好时，ECN 从 9,836 降至 7,343、trim 从 61 降至 2，p99 FCT 改善 12.5%。
- **P4/P16 是同步、全局、多对多拥塞。** 路径好坏更多来自当前一批流共同制造的动态拥塞，而不是固定物理慢路。sharp 在 P4/P16 都产生更多 ECN、trim、NACK，并使 CCT 分别恶化 7.1%、6.7%。
- **流大小影响同一场景中的收益归属。** WebSearch 的 sharp 对短、中流有利，对长流尾部不利；所以它会改变公平性和尾延迟，但不能单独解释 P2P 与 All-to-All 的参数反转。

P4 和 P16 不是严格的单变量消融：从 P4 到 P16，单流大小、并发度、批次数和总数据量同时改变。它们共同偏好 equal 说明结论能跨两个 All-to-All 强度成立，但不能据此分离“2 MiB 单流”和“16 并发”各自贡献多少。若要做严格归因，需要额外运行：

- 固定 0.5 MiB 单流，只改变 P4/P16；
- 固定 P4，只改变单流 0.5/2 MiB；
- 固定总字节数，只改变批并发度。

所以旧结论“加权一定比等权好”是错的，“等权一定更好”也同样错。正确结论是：固定异构路径偏好陡权重，全局动态拥塞偏好平缓权重。

### 集合通信补充扫描：低并发或短流时并非 equal 最优

为分离并发与流规模，本轮增加两个 All-to-All 单变量扫描：

- 固定单流 0.5 MiB，扫描每源并发 P1/P2/P4/P8/P16/P32。
- 固定 P4，扫描单流 0.125/0.5/2/8 MiB。

所有场景均为 128 节点、8 路径、无背景流，使用 seeds 13/29/47；同一场景/seed 的四组权重共享 traffic hash。完整数据见：

- [collective results.csv](output/grade_collective_scale_concurrency_20260726/results.csv)
- [collective summary.csv](output/grade_collective_scale_concurrency_20260726/summary.csv)
- [collective winners.csv](output/grade_collective_scale_concurrency_20260726/winners.csv)

#### 固定 0.5 MiB，改变每源并发

| 并发 | equal | gentle | default | sharp | 三 seed 几何均值最优 |
|---:|---:|---:|---:|---:|---|
| P1 | 3808.8 | **3773.5** | 3833.6 | 3934.6 | gentle |
| P2 | **2666.7** | 2945.6 | 2992.0 | 2773.3 | equal |
| P4 | **2158.5** | 2254.4 | 2245.7 | 2311.9 | equal |
| P8 | **1993.9** | 2083.2 | 2141.5 | 2106.7 | equal |
| P16 | **1885.5** | 1908.0 | 1946.1 | 1956.4 | equal |
| P32 | **1930.8** | 1944.9 | 1987.9 | 2011.5 | equal |

P1 是可靠的非 equal 场景：gentle 比 equal 快 0.93%，seed 29/47 胜出，seed 13 略差。机制指标为：

| P1/0.5 MiB | ECN | trims | NACK | 等级变化 | spine queue CV |
|---|---:|---:|---:|---:|---:|
| equal | 3,893 | 113,374 | 114,211 | 72,338 | **0.373** |
| gentle | **3,877** | **110,418** | **111,188** | **70,324** | 0.454 |

gentle 的 CCT 收益与 trim、NACK、等级变化下降约 2.6%–2.8% 同时出现，但 queue CV 反而提高 21.5%。因此“更低 queue CV 导致它获胜”与数据不符。现有计数只能确认 gentle 同时具有较少恢复事件和较短 CCT，不能判断这些恢复事件是否是 CCT 改善的原因。

从 P2 开始 equal 转为最优，并一直保持到 P32。已验证的是并发提高后等级权重不再产生稳定收益。为什么 P1 到 P2 会改变排序仍未被隔离；需要路径等级驻留时间和逐批选择时间线才能检验“更多发送者使等级更偏短期状态”。P2 的单 seed 赢家在 sharp/default/equal 之间变化，说明该过渡区的结果随 seed 变化。

#### 固定 P4，改变单流大小

| 单流大小 | 每源总量 | equal | gentle | default | sharp | 三 seed 几何均值最优 |
|---:|---:|---:|---:|---:|---:|---|
| 0.125 MiB | 15.875 MiB | 490.7 | 493.7 | **482.1** | 488.9 | default |
| 0.5 MiB | 63.5 MiB | **2158.5** | 2254.4 | 2245.7 | 2311.9 | equal |
| 2 MiB | 254 MiB | **10664.1** | 11119.0 | 11765.4 | 11171.5 | equal |
| 8 MiB | 1016 MiB | 38283.8 | 40668.2 | **38007.6** | 38730.0 | default（仅 1/3 seed 胜 equal，不稳定） |

最可靠的规模反转出现在 0.125 MiB：default 三个 seed 全部优于 equal，几何均值改善 1.75%。

| P4/0.125 MiB | ECN | trims | NACK | 等级变化 | spine queue CV |
|---|---:|---:|---:|---:|---:|
| equal | 233 | 167 | 168 | 194 | 0.612 |
| default | **200** | **81** | **81** | **124** | **0.604** |
| 相对变化 | -14.3% | -51.4% | -51.7% | -36.4% | -1.4% |

equal 把 GOOD/MILD/BAD 一视同仁，而 default 保留等级差异；在 0.125 MiB 点，后者与更少的 ECN、trim、NACK 和更短 CCT 同时出现。由于没有记录每条流选择路径的等级，当前实验只能确认这些指标共同改善，不能确认 default 是否在流结束前避开了已出现坏信号的路径。

流增至 0.5/2 MiB 后，equal 重新最优。default 在 P4/0.5 MiB 中相对 equal 多 12.0% ECN、6.6% trim 和 6.4% NACK，CCT 慢 4.0%。这验证了指标方向随规模反转，但没有记录单流经历的反馈轮数或权重导致的流量迁移次数，因此“长流承受持续权重反馈代价”仍是待验证假设。

8 MiB 的三 seed 几何均值虽然由 default 小幅领先 0.72%，但它只在 seed 47 胜 equal，seed 13/29 均落后，而且 queue CV 比 equal 高 67%。因此该点只说明大流场景存在高方差，不能作为 default 稳定优于 equal 的证据。

综合两个扫描，最优权重确实随并发和流规模变化：

- 极低并发 P1：gentle 最优。
- 很短的 P4/0.125 MiB：default 最优。
- P2–P32 的 0.5 MiB 集合通信，以及 P4 的 0.5/2 MiB：equal 最稳定。
- 权重越陡并非越好；可靠非 equal 赢家是 gentle/default，而不是 sharp。

### 短流与长流也有相反取舍

WebSearch 按流大小重新分桶后的 p99 FCT：

| grade 权重 | ≤100 KB | 100 KB–1 MB | >1 MB |
|---|---:|---:|---:|
| equal | 77.0 | 212.2 | **6072.4** |
| gentle | 77.3 | 208.3 | 6116.8 |
| default | 75.5 | 206.4 | 6143.4 |
| sharp | **70.3** | **190.9** | 6201.4 |

sharp 实际改善了短、中流，却恶化了长流尾部，最终整体 WebSearch p99 最差。这证明同一权重存在流大小取舍；本轮没有记录“流大小 × 路径等级”的联合选择数据，因此不进一步声称是哪类流被优先送上 GOOD 路径。

### 阈值不是“越敏感越抖越差”

固定 `4/2/1/0`，比较 default、sensitive、conservative：

| 场景 | default | sensitive | conservative |
|---|---:|---:|---:|
| 非对称 16 MiB | **467.4** | 533.0 | 543.1 |
| WebSearch | 3876.5 | **3765.5** | 3783.0 |
| P16 | 7522.9 | **6800.5** | 6911.4 |
| P4 | 2245.7 | 2166.8 | 2336.0 |

新增转移矩阵确认等级确实会双向跨越边界，但“变化多”并不等于有害抖动：sensitive 在 P16 等级变化仍很多，CCT 却改善 9.6%。因此本实验只证明默认阈值存在场景耦合，没有证明“阈值边界抖动本身造成性能损失”。

## 2. avail：长期均衡不等于性能因果

已有路径时间线中，avail 的最终累计路径使用 CV 最低，但窗口 CV 明显高于最终 CV。这只证明“短期偏斜存在”，没有证明它导致 FCT/CCT 变差。四个方案之间做相关性也不能证明因果，因为选路机制同时改变了 ECN、trim 和恢复。

本轮真正验证的是恢复老化方式：

| 场景 | packet | ECN-only | hybrid | time-only |
|---|---:|---:|---:|---:|
| 非对称 16 MiB | **448.3** | **448.3** | 457.9 | **448.3** |
| WebSearch | 3832.0 | 3815.5 | **3805.0** | 3832.0 |
| P16 | 6962.0 | 7087.3 | **6750.1** | 6962.0 |
| P4 | 2402.3 | 2461.5 | **2184.1** | 2402.3 |

hybrid 相对 packet：

- P16 快 3.0%，三 seed 同向。
- P4 快 9.1%；两个 seed 约快 14%，第三 seed略慢，因此均值收益明确但不是三 seed 全同向。
- 非对称 P2P 慢 2.1%，三 seed 同向。
- WebSearch 仅快 0.7%，效应很小。

### 机制证据：全零可用路径表滞留

`all_zero_selections` 表示所有路径权重均为 0 时仍不得不执行回退选择的次数：

| 场景 | packet | hybrid | 降幅 |
|---|---:|---:|---:|
| P16 | 59,389 | 19,087 | 67.9% |
| P4 | 77,119 | 22,514 | 70.8% |
| WebSearch | 132,498 | 13,742 | 89.6% |

P4/P16 中，hybrid 同时减少全零回退并改善 CCT，支持以下机制假设：

`坏信号把路径判为不可用 → packet 恢复不足 → 全零表长期滞留 → 被迫回退选路 → hybrid 加速恢复 → CCT 改善`

这不是中介因果隔离：hybrid 还会同时改变其他老化状态，且 WebSearch 的全零回退下降 89.6% 时 FCT 只改善 0.7%。可以确认恢复策略和全零回退都发生变化，不能确认全部 CCT 收益都由该计数器造成。

非对称固定慢路径中 hybrid 反而变差。数据验证的是恢复策略的场景反转；“恢复过快让慢路径重新进入候选”仍是待路径级时间线验证的解释。

ECN-only 也不是通用修复：P16、P4 的三 seed 几何均值分别比 packet 慢 1.8%、2.5%，且 P4 seed 间方向反转。

## 3. netaware：权重和 good-share-cap 都依赖负载

| 场景 | equal | gentle | default-off | sharp | good-cap |
|---|---:|---:|---:|---:|---:|
| 非对称 16 MiB | 513.5 | 462.5 | 424.4 | **420.1** | 424.4 |
| WebSearch | 3822.2 | **3777.0** | 3837.3 | 3814.0 | 3822.9 |
| P16 | 7185.5 | 7313.0 | **6891.5** | 7267.3 | 7292.5 |

三类场景分别偏好 sharp、gentle 和 default-off。对应机制指标也不同：

- 非对称：equal 的 ECN 为 10,882，sharp 为 7,114，sharp 同时更快。
- WebSearch：sharp 的 ECN 为 90,063，gentle 为 50,870，sharp 同时更慢。
- P16：default-off 的 NACK 为 858,314，good-cap 为 1,083,568；good-share-cap 总体慢 5.8%，但只有两个 seed 明显恶化，一个 seed 基本持平。

因此 netaware 的已验证不足与 grade 类似：评分信息更多，但固定权重映射和 good-share-cap 并不跨负载稳定。

## 4. n-mrc：`better_ge3` 并不等于过度保守

当前实现只支持：

- `better_ge3`：候选路径等级至少好 3 级才换路。
- `any_better`：只要更好就换路。

非对称 16 MiB 三 seed：

| 策略 | p99 FCT 几何均值 | seed 13/29/47 |
|---|---:|---|
| better_ge3 | **403.9 μs** | 434.8 / 395.1 / 383.7 |
| any_better | 413.5 μs | 453.6 / 406.4 / 383.7 |

机制计数：

| seed | 策略 | reroutes | threshold blocked | ECN | NACK |
|---:|---|---:|---:|---:|---:|
| 13 | better_ge3 | 22,484 | 3,335 | 14,411 | 22 |
| 13 | any_better | 29,311 | 0 | 15,808 | 210 |
| 29 | better_ge3 | 8,847 | 456 | 4,501 | 1 |
| 29 | any_better | 9,722 | 0 | 4,731 | 33 |
| 47 | 两者 | 8,333 | 0 | 3,655 | 0 |

在存在被阈值阻止动作的 seed 13/29，`better_ge3` 同时减少 reroute、ECN、NACK 并改善 FCT；seed 47 没有被阈值阻止的机会，两者完全相同。这证明阈值不是“根本不换路”。本实验没有逐次反事实，不能断言每一个被阻止的换路都无收益。

n-mrc 当前被验证的局限是：在本场景中，若没有阈值实际阻止的动作，两种策略没有差别。本轮没有找到 `better_ge3` 因过于保守而稳定变差的场景，因此不把这一点列为已验证缺点。

注意：当前 CLI 不支持 `never`，因此正式矩阵没有把它当作可运行策略。

## 最终对比

| 方案 | 已验证的主要不足 | 已验证的适用倾向 |
|---|---|---|
| avail | 恢复速度无法同时适配暂态拥塞和固定慢路径；累计 CV 不能代表性能 | hybrid 适合 P4/P16 暂态拥塞，packet 适合固定慢路径 |
| grade | 权重、阈值与拥塞形态强耦合；sharp 牺牲 WebSearch 长流尾部和 All-to-All CCT | sharp 适合固定异构 P2P；equal/sensitive 更适合 All-to-All |
| netaware | 权重和 good-share-cap 不跨场景稳定 | sharp 适合固定异构；gentle 适合 WebSearch；default-off 适合 P16 |
| n-mrc | 只有足够等级差时阈值才产生作用；本轮未证明 `better_ge3` 过保守 | 非对称 P2P 中 better_ge3 优于 any_better |

## 未被本轮证明，因此不作为缺点

- “avail 的短时路径 CV 偏斜导致 FCT/CCT 变差”：只观察到短时偏斜，未建立因果。
- “grade 的等级边界抖动必然有害”：等级变化存在，但敏感阈值在 P16 反而更快。
- “n-mrc 四级差阈值过于保守”：本轮相反，阈值减少了无收益换路。

## 复现

```bash
make -C sim -j2
python3 experiments/n-mrc/run_four_scheme_parameter_matrix.py --workers 4
python3 experiments/n-mrc/analyze_four_scheme_parameter_matrix.py \
  experiments/n-mrc/output/four_scheme_parameter_matrix_20260726
```

数据 SHA-256：

- `results.csv`: `8913d1ad86587bddd0717440e04f301657c993cf857774d611a5ce3f5cb4fa2b`
- `summary.csv`: `b345d4505fd7c94b4ae6b95eddd3a7099fceca382283978a9b57b694132d8ef1`
- `transitions.csv`: `61a2e05239162e49097a06f9f32a4e96d672f30d4c35fe18eb06787078eedd5a`
- `flow_size_bins.csv`: `73470b115e472be6f049eaa4e30b8b200ecb6d700c81a953d5e400dc780519d2`
- `collective results.csv`: `4434a888ff054f1491ac2a40bd3f8a7e34749f21eb9006290633d8e505af5339`
- `collective summary.csv`: `152d0c87caa53c422eca26f44ed5c190164225aacf51e516b92a924de5a2ea18`
- `collective winners.csv`: `17179b8230550054e9df50d75099d65cda9e25b37a0050f44b3e48c6529b2554`
