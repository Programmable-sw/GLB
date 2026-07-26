# 四方案参数—场景耦合与不足验证设计

## 目标

重做 `experiments/n-mrc/four_scheme_risks_and_evidence.md`。最终报告只保留经过干预实验验证的不足，并回答：

1. avail 的短时路径偏斜是否影响 FCT/CCT，以及永久降级、全路径失效回退、恢复策略分别造成什么后果；
2. grade 的权重和等级阈值在热点、均匀负载、短流/长流中如何翻转，哪些中间指标解释翻转；
3. netaware 的权重与 GoodCap 自适应在相同场景中如何翻转，快照偏置的收益和代价分别是什么；
4. n-mrc 的换路门槛、路径规模和流大小如何耦合，哪些已验证问题应保留；
5. 删除无法被本轮实验或既有因果数据支持的“可能不足”。

## 实验原则

- 所有结论使用同 seed 配对；
- 主要对照使用 seed 13、29、47；
- 单次只改变一个参数族；
- 性能变化必须同时检查 ECN、TRIM、重传、换路、路径恢复、等级转移、全失效回退或窗口 CV；
- 只有单变量干预且 3/3 seed 同向时写“因果证实”；
- 中间指标未被独立干预时只能写“机制支持”，不能写成已证明的中介链；
- WebSearch 必须按流大小分箱，至少区分短流、中流和 20–30 MiB 大流；
- 时序 CV 必须区分累计 CV、完整窗口 CV和局部源—目的对 CV。

## 参数矩阵

### grade 权重

固定等级阈值和 aging，比较：

- equal：`4/4/4/0`；
- gentle：`4/3/2/0`；
- default：`4/2/1/0`；
- sharp：`8/2/1/0`。

场景：

- 持续非对称热点；
- P16 均匀全局负载；
- P4 均匀全局负载；
- WebSearch 80% 混合流量。

### grade 阈值

固定默认权重，比较三组合法阈值：

- sensitive：更容易降级；
- default；
- conservative：更难降级。

新增 GOOD/MILD/BAD/AVOID 等级转移矩阵，统计总转移、相邻级往返和跨级跳变。至少覆盖非对称热点和 P16。

### netaware 权重和自适应

先关闭 GoodCap，仅比较 equal、gentle、default、sharp 四组权重；再加入默认权重 + GoodCap，分离“固定等级权重”和“自适应”：

- equal/off；
- gentle/off；
- default/off；
- sharp/off；
- default/GoodCap。

覆盖非对称热点、P16 和 WebSearch，并按流大小分析 WebSearch。

### avail 恢复和短时偏斜

比较：

- packet/default；
- ECN-only；
- hybrid-fast；
- hybrid-conservative。

新增全路径 AVOID/零权重回退计数。P4、P16、非对称热点和 WebSearch使用三 seed。P4/P16 对全部策略记录路径时序，比较策略变化造成的窗口 CV、回退计数与性能变化。只有同一干预同时改变 CV 和性能且方向稳定时，才把短时偏斜列为性能机制；否则明确否定。

### n-mrc 门槛和路径规模

比较 4、8、16 条路径下：

- never；
- better_ge3；
- any_better。

覆盖非对称热点和 WebSearch，记录 reroute、threshold_blocked、ECN、重传和按流大小 FCT。验证固定“三条更优候选”是否随路径规模改变相对严格度，以及混合流量回退是否集中于大流。

## 诊断新增

### 等级转移

RoCE 端在应用新的 STOR feedback profile 时，对每条路径比较前后等级，输出 4×4 转移矩阵和实际等级改变次数。reset 必须在每次仿真开始清零。

### avail 全失效回退

在 STOR weighted profile 所有路径权重为 0、实现被迫构造回退 bucket 时计数；同时记录最终选择是否仍为 AVOID。

### 路径时序

复用累计物理路径采样器；分析端支持多 seed、多 variant，并输出：

- 最终累计 CV；
- 完整窗口 CV 均值/P95；
- 与同 seed CCT/p99 FCT 的配对变化。

## 交付物

全部位于 `experiments/n-mrc/`：

- 更新后的 `four_scheme_risks_and_evidence.md`；
- 参数扫描逐次结果、配对效应和按流大小结果 CSV；
- grade/netaware 权重—场景热图；
- avail 窗口 CV 与性能对照图；
- n-mrc 路径规模—门槛图；
- 可复现 runner 和分析脚本；
- 原始命令、revision、流量哈希。

## 报告写法

每个方案只包含：

1. 已验证的问题；
2. 干预和性能效应；
3. 同步变化的机制指标；
4. 反例或翻转场景；
5. 当前能下的结论；
6. 被实验否定的旧解释。

未验证风险不再列入方案缺点表。
