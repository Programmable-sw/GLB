# avail、grade、netaware、n-mrc：已验证的不足与对比

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
| P16 CCT | **7244.9** | 7331.2 | 7522.9 | 7729.1 | equal |
| P4 CCT | **2158.5** | 2254.4 | 2245.7 | 2311.9 | equal |

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

非对称场景有固定慢路径，拉大 GOOD 与其他等级的权重差可避开慢路径，因此 ECN 和 trim 同时下降。P16 没有固定坏路径，拥塞状态快速变化；sharp 会把大量选择集中到当前 GOOD 路径，随后产生更多 ECN、trim 和 NACK，CCT 变差。

所以旧结论“加权一定比等权好”是错的，“等权一定更好”也同样错。正确结论是：固定异构路径偏好陡权重，全局动态拥塞偏好平缓权重。

### 短流与长流也有相反取舍

WebSearch 按流大小重新分桶后的 p99 FCT：

| grade 权重 | ≤100 KB | 100 KB–1 MB | >1 MB |
|---|---:|---:|---:|
| equal | 77.0 | 212.2 | **6072.4** |
| gentle | 77.3 | 208.3 | 6116.8 |
| default | 75.5 | 206.4 | 6143.4 |
| sharp | **70.3** | **190.9** | 6201.4 |

sharp 实际改善了短、中流，却恶化了长流尾部。它优先把短流送上当前最好路径，短流更容易在拥塞变化前完成；长流持续时间长，会经历路径热点转移和更高 ECN，最终整体 WebSearch p99 反而最差。

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

### 已验证机制：全零可用路径表滞留

`all_zero_selections` 表示所有路径权重均为 0 时仍不得不执行回退选择的次数：

| 场景 | packet | hybrid | 降幅 |
|---|---:|---:|---:|
| P16 | 59,389 | 19,087 | 67.9% |
| P4 | 77,119 | 22,514 | 70.8% |
| WebSearch | 132,498 | 13,742 | 89.6% |

P4/P16 中，hybrid 同时减少全零回退并改善 CCT，支持以下链路：

`坏信号把路径判为不可用 → packet 恢复不足 → 全零表长期滞留 → 被迫回退选路 → hybrid 加速恢复 → CCT 改善`

但非对称固定慢路径中 hybrid 反而变差：恢复过快会让真正的慢路径重新进入候选。avail 的已验证不足不是简单“恢复慢”，而是恢复速度无法同时适配固定故障和暂态拥塞。

ECN-only 也不是通用修复：P16、P4 的三 seed 几何均值分别比 packet 慢 1.8%、2.5%，且 P4 seed 间方向反转。

## 3. netaware：权重和 good-share-cap 都依赖负载

| 场景 | equal | gentle | default-off | sharp | good-cap |
|---|---:|---:|---:|---:|---:|
| 非对称 16 MiB | 513.5 | 462.5 | 424.4 | **420.1** | 424.4 |
| WebSearch | 3822.2 | **3777.0** | 3837.3 | 3814.0 | 3822.9 |
| P16 | 7185.5 | 7313.0 | **6891.5** | 7267.3 | 7292.5 |

三类场景分别偏好 sharp、gentle 和 default-off。对应机制指标也不同：

- 非对称：equal 的 ECN 为 10,882，sharp 为 7,114；识别并避开固定慢路径有效。
- WebSearch：sharp 的 ECN 为 90,063，gentle 为 50,870；陡权重加重动态热点。
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

在真正存在小等级改善的 seed 13/29，`better_ge3` 阻止部分换路，同时减少 reroute、ECN、NACK 并改善 FCT；seed 47 没有被阈值阻止的机会，两者完全相同。这证明阈值不是“根本不换路”，而是在本场景中过滤无收益的小改善。

n-mrc 当前被验证的不足是：收益依赖是否存在足够大的等级差；若没有阈值可阻止的小改善，两种策略没有差别。本轮没有找到 `better_ge3` 因过于保守而稳定变差的场景，因此不把这一点列为已验证缺点。

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
