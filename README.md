# n-MRC on htsim

本仓库在 Broadcom `csg-htsim` 的 RoCE datacenter 仿真器基础上，评估 n-MRC 相关的多路径负载均衡方案。

原 full-snapshot `n-mrc` 已改名为 `netaware`：leaf 维护 ToR-pair 的完整四级 path profile，并以固定 5us ACK snapshot 反馈给 NIC。新的 `n-mrc` 融合源端 MRC EV 轮询和源侧第一跳 Leaf 的 SGLB 严格升档换路，并以 FastCNP 格式的路径通知让端侧冷却坏 EV 一轮；它不替代 ECN，也不触发 DCQCN。

独立 `mrc` 模式的 RoCE/队列默认底座、encoded EV 状态机以及 `-lb mrc` 到 MRC 论文机制的映射，见 `MRC_IMPLEMENTATION.md`。MRC 参考文献的整理稿见 `docs/papers/MRC.md`。各负载均衡方案、NetAware full snapshot 和新 n-MRC FastCNP 路径通知的实际逻辑，见 `experiments/n-mrc/README.md`。

Avail、Grade 和 n-MRC 在 corrected stack 下的参数筛选、三 seed 验证、128/512/2048 scaling 及最终机制结论，见 `experiments/n-mrc/output/branch123_ai_tuning/branch123_ai_tuning_for_gpt.md`。

## 仓库结构

- `sim/`：htsim C++ 离散事件仿真器。
- `sim/datacenter/htsim_roce`：编译后的 RoCE 仿真程序。
- `experiments/n-mrc/run_literature_metric_compare.py`：README 标准场景统一验证脚本，负责逐包方案仿真、CSV/report 和图表输出。
- `experiments/n-mrc/run_hybrid_nmrc_compare.py`：新 n-MRC 的 EV/换路策略矩阵及 SGLB、AR、NetAware、MRC、REPS 同栈对比。
- `experiments/n-mrc/run_sglb_factor_compare.py`：sglb 参数对比脚本。

## avail 机制简介

avail 把一个 EV 看作完整的端到端路径组合。

avail 使用 source-ToR bad-cache bitmap。源端按 `(src ToR, dst ToR)` 共享最近一个反馈窗口的 bad EV bitmap；source ToR 在返回 ACK 带 ECN 或收到 TRIM NACK 时把对应 EV 标为 bad。源端发包时避开 recent-bad EV，未被报告 bad 的 EV 在下一反馈窗口自然回到候选池。默认反馈节奏为 packet trigger `1`、固定 `5us`。旧的 `32 pkt + 5--20us` 窗口仍可通过 `-stor_feedback_pkts 32 -stor_feedback_min_us 5 -stor_feedback_max_us 20 -stor_trim_feedback_min_us 5` 显式启用。

avail 的共享 bitmap 不复制到每个 QP。每个 QP 只维护 selection counter，用 `src/dst/flow_id/priority/profile_version/epoch` 对路径下标做可随机访问的虚拟 permutation，并跳过 recent-bad EV。

## grade 方案设计

grade 是一个 source-ToR monitoring and feedback 分支。端侧按预选 EV set 逐包填 EV，交换机仍按 ECMP hash 转发；source ToR 观察 ACK ECN 和 TRIM NACK，把每个 EV 量化为 GOOD、DEGRADED、BAD 或 AVOID，再 piggyback 回端侧并按等级加权。默认反馈节奏同样为 packet trigger `1`、固定 `5us`。公式和当前参数不在这里展开，统一放在 `experiments/n-mrc/README.md`。

## 已实现负载均衡方案

| 方案 | 机制简述 |
| --- | --- |
| `ecmp` | 每条 flow 使用固定 pathid，交换机用 ecmp hash 映射到下一跳。 |
| `ecmp_rr` | 每个交换机在当前 ecmp next-hop 集合内做 round-robin；多 tier / bundle 场景下，每一跳分别轮转本跳可选端口。 |
| `ops` | 源端每个 packet 随机选择 EV/pathid，实现无反馈 packet spraying。 |
| `rr` | 源端按确定性序列轮转 EV/pathid，实现无反馈但覆盖更均匀的 source-controlled rr；随机 EV spraying 对应 `ops`。 |
| `reps` | ACK 回传近期未 ECN 的 pathid，源端优先复用 clean EV；没有反馈时随机。 |
| `avail` | source ToR 根据 ACK ECN 或 TRIM NACK 生成 ToR-pair 共享的 1-bit recent-bad EV bitmap；每 QP 只用 selection counter 虚拟打乱路径下标并跳过 bad EV。默认 ECN 与 TRIM 都会在同一短反馈窗口内把精确路径标为暂时不可用；`-avail_ecn_only` 仅用于恢复旧 ECN-only 语义的消融实验。 |
| `grade` | source ToR 用默认 4-bit simple scorer（clean `+1`，ECN/TRIM `-4`，阈值 `12/7/3`）量化共享 EV 状态；NIC 按 4/2/1/0、`K=4*path_count` 的共享 base bucket 和 per-QP 虚拟 permutation 加权选路。原 balanced 三字段评分仅由 `-grade_complex_score` 启用。 |
| `netaware` | source leaf 组合 1us 本地端口 snapshot 与 5us spine 下游 export snapshot，生成完整 ToR-pair 四级 profile；NIC 默认用 GoodCap 对共享 4/2/1/0 分布做 GOOD-share 限流，再映射到 `K=4*path_count` bucket。 |
| `n-mrc` | 每 QP 轮询 encoded 或 16-bit random EV set；源侧第一跳 Leaf 在存在足量严格更优四级路径时用 SGLB 换路，并以 FastCNP 格式通知端侧冷却原 EV 一轮。后续 ECN 仍完整端到端 echo。 |
| `mrc` | 每个 QP 将 EV 与单平面物理 path-id 一一编码，按确定性排列循环使用不超过 32 个 active EV。ECN 与 TRIM 使用相同处罚：默认进入 one-cycle soft skip，冷却期间后续拥塞反馈会续期，反馈排空后自然恢复；显式 `-mrc_cooldown_mode cwnd_scaled` 保留按拓扑 BDP 取整的固定长 cooldown 诊断。全部 EV 冷却时使用最早到期 EV 保活，不清除状态或 deadline。OOO 只进入 SP/SACK 选择重传，LOSS/RTO 标记 failed 并换入剩余唯一路径。 |
| `conweave` | RTT 超过阈值后切换 pathid，减少持续走拥塞路径的概率。 |
| `adaptive-routing` | 交换机按本地队列拥塞情况在可用下一跳中选择端口。 |
| `drill` | 交换机结合随机候选和历史候选端口，优先选择拥塞较低的下一跳。 |
| `sglb` | 交换机默认将 1us 本地队列压力与 next-hop 每 5us 导出的下游队列压力按 noisy-or 合成，量化为四档后按 SGLB top-K 整档扩展候选；LSN 钩子可硬屏蔽故障邻居。原五因子评分和八档量化分别由显式 flag 启用。 |

从粒度上看，`ops`、`rr`、`reps`、`avail`、`grade`、`n-mrc` 和 `mrc` 都是源端逐包选择 EV/pathid；`ecmp` 使用固定 pathid，`conweave` 只在 RTT 触发时切换 pathid，不做逐包 spraying。`ecmp_rr`、`drill`、`sglb` 和当前 `adaptive-routing` 默认都属于交换机侧逐包/逐跳选择 next-hop；`adaptive-routing` 仍可通过 `-ar_granularity flowlet` 切到 flowlet sticky。

全局 RoCE 传输默认使用 `mrc_exact_bounded`：SP/SACK、exact-PSN TRIM recovery、`awnd=cwnd-inflight` 和每 QP 最多 1 MTU 的 bounded recovery reserve。复现此前 Natural+Cumulative 数据时必须显式使用：

```bash
-roce_transport_semantics legacy -roce_trim_recovery cumulative
```

该历史入口保留 Natural inflate；Exact+Bounded 不接受 cumulative TRIM recovery。

## 标准测试场景

| 场景 | 拓扑 | 链路条件 | Traffic | Flow size | 对比方案 |
| --- | --- | --- | --- | --- | --- |
| 健康网络 | 2048 nodes / 2-tier | 全链路 400Gbps | tornado / permutation | 4/8/16/32MiB，默认启用 4/32MiB | ecmp_rr / ops / rr / adaptive-routing / drill / sglb / reps / mrc / avail / grade / n-mrc |
| 非对称带宽 | 2048 nodes / 2-tier | `floor(2048 * 10%) = 204` 条 ToR 上行随机稀疏半带宽，按 ToR 均匀分散 | tornado / permutation | 4/8/16/32MiB，默认启用 4/32MiB | ecmp_rr / ops / rr / adaptive-routing / drill / sglb / reps / mrc / avail / grade / n-mrc |

常用负载样例：

| 负载 | 构造方式 | 主要测试目标 |
| --- | --- | --- |
| `healthy` | 所有 fabric 链路等速；使用 permutation 一一映射或 tornado 固定偏移，每个 host 产生一个 foreground flow。 | 检查无持续路径不均衡时的基础 FCT、乱序和重传开销。 |
| `degraded` | 保持 healthy traffic matrix，但随机稀疏选择部分 ToR-to-spine 上行并将其带宽降低，未选链路保持原速。慢链路仍可用，不表示断链。 | 检查方案能否识别并减少使用长期慢路径。 |
| `mixed` | background long flows 与 target short/foreground flows 同时运行，两类真实 RoCE flow 都按被测 LB 选路，不固定到特定 path。mixed 本身不保证形成热点。 | 检查背景负载下的短流尾延迟、长流干扰和公平性。 |
| `path_hotspot` | 与 selector 无关的 fixed-link background source 对所有 leaf 的少数选定 spine links 注入相同 offered load；target flows 保持普通 permutation destination，并使用完整 spine 集合。 | 制造可绕开的中间路径热点，检查 target 是否识别并避开已占用 spine。 |
| `incast` | 16/32/64/128 个 source 同时向同一个 destination host 发送；所有路径最终共享该 host 的最后一跳。 | 检查不可绕开的最后一跳瓶颈、TRIM/NACK/RTO 和尾延迟。 |

`path_hotspot` 与 `incast` 的区别是：前者的拥塞位于少数 spine 路径，target 可以改走其他 spine；后者的拥塞位于所有路径共享的 destination-host downlink，换 EV/path 不能消除瓶颈。

历史 512-node 周期交换机状态矩阵中的 `n-mrc` 结果现在归入 `netaware`：SGLB 使用 1us local/5us GCN cache，NetAware 使用 1us leaf-local/5us spine-export cache。新 n-MRC 用下面的专用 runner 做 EV/换路策略与 baseline 对比：

```bash
python3 experiments/n-mrc/run_periodic_cache_packet_lb_512.py
python3 experiments/n-mrc/run_hybrid_nmrc_compare.py --dry-run --quick
python3 experiments/n-mrc/run_hybrid_nmrc_compare.py --quick --seeds 13,29,47
```

结果保存在 `experiments/n-mrc/output/nmrc_periodic_cache_packet_lb_512/`。其中 controlled `path_hotspot` 使用与 selector 无关的 fixed-link background，向 4/16 个 spine paths 提供相同背景负载；mixed 使用真实 rate-limited RoCE background flows。背景 packet 被 composite queue trim 后实际 payload load 可能不同，因此报告同时给出 target NACK 和全局 QueueDiag。

通用参数：

- 链路速率：400Gbps。
- 链路延迟：每跳 wire latency 0.5us，每台 switch latency 0.5us；2-tier 为 7us，3-tier 为 11us。
- 队列：`lossless_input_ecn`，链路侧 lossless input queue + 输出队列 ECN 标记。
- 队列长度：1BDP。
- ECN：Kmin/Kmax = 0.2/0.8 * queue。
- PFC：low/high threshold = 0.2/0.8 * queue。
- Host queue：`prio`，host 发送侧优先级队列，data 低优先级，ACK/NACK 高优先级，响应 PFC PAUSE。
- MTU：4096 bytes。
- RoCE RTO：70us。
- 拥塞控制：`DCQCN_variant`。
- 流量：`tornado` 或 `permutation`，每个 host 一个 foreground flow；`tornado` 目的端为 `(src + nodes/2) % nodes`，`permutation` 使用固定 seed 的随机一一映射。
- 图表指标：avg FCT、p99 FCT、p99.9 FCT。

标准实验使用 lossless + ECN + bitmap重传，lossy 版本正在改进。

非对称带宽场景按所有 ToR-to-aggregation uplink 计算慢链路数量。1024-node / 3-tier generated fat-tree 的 ToR 上行总数为 1024 条，因此默认慢链路数量为 `ceil(1024 * 0.03) = 31`。非对称带宽2使用 2048-node / 2-tier，慢链路数量为 `floor(2048 * 0.10) = 204`，并通过 `-slow_tor_uplink_select random-sparse` 按 seed 随机稀疏选择；该模式先把慢链路数量按 ToR 均匀分配，再在每个 ToR 内随机选具体上行。因此 2048-node / 2-tier 下的 204 条慢上行会分散到 64 个 ToR 上，每个 ToR 3 或 4 条。

## 环境准备

推荐直接 clone 当前实验分支：

```bash
git clone -b main-htsim https://github.com/Programmable-sw/SGLB.git csg-htsim
cd csg-htsim
git remote add upstream https://github.com/Broadcom/csg-htsim.git || true
git fetch --all --prune
```

Ubuntu/Debian 依赖：

```bash
sudo apt-get update
sudo apt-get install -y build-essential make g++ git python3 python3-pip
python3 -m pip install --user matplotlib
```

构建仿真器：

```bash
make -C sim -j"$(nproc)"
```

检查 RoCE 仿真程序和脚本：

```bash
./sim/datacenter/htsim_roce -h | head -n 40
 python3 -m py_compile \
  experiments/n-mrc/run_literature_metric_compare.py \
  experiments/n-mrc/run_sglb_factor_compare.py
```

## 运行实验

运行 README 标准逐包方案测试：

```bash
python3 experiments/n-mrc/run_literature_metric_compare.py
```

默认会展开：

- 场景：健康网络、非对称带宽、非对称带宽2。
- Traffic：`tornado`、`permutation`。
- Flow size：默认启用 `4,32`MiB；脚本支持 `4,8,16,32`MiB。
- 每个 case 默认跑逐包方案 `ecmp_rr`、`ops`、`rr`、`reps`、`avail`、`grade`、`n-mrc`、`mrc`、`adaptive-routing`、`drill`、`sglb`。

只跑非对称带宽2的 8MiB 快速检查：

```bash
SCENARIO_SET=asym2_tor10pct_sparse \
SCENARIO_TRAFFICS=tornado \
SCENARIO_FLOW_SIZE_MIBS=8 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

保留每次仿真的原始输出：

```bash
KEEP_RAW_OUTPUT=1 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

常用环境变量：

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `SCENARIO_SET` | `healthy,asym_tor3pct,asym2_tor10pct_sparse` | 要启用的场景集合。 |
| `SCENARIO_FLOW_SIZE_MIBS` | `4,32` | 启用的 flow size 列表；支持集合为 `4,8,16,32`。 |
| `SCENARIO_TRAFFICS` | `tornado,permutation` | 启用的 traffic 集合；支持 `tornado` / `permutation`。 |
| `SCENARIO_SCHEMES` | `packet` | 默认逐包方案集合；也可设为逗号分隔 scheme 列表。 |
| `SCENARIO_CC` | `dcqcn_variant` | 可改为 `dcqcn`。 |
| `SCENARIO_RX_MODE` | `sp` | RoCE 接收/重传模式；默认使用 SACK bitmap 选择性重传。 |
| `SCENARIO_SACK_BITMAP_BITS` | `64` | SACK bitmap 位宽；默认 64-bit 对齐 MRC spec，可设 `128` 做覆盖度/性能对照。 |
| `SCENARIO_WORKERS` | `4` | 同一 case 内并发运行的仿真进程数。 |
| `SCENARIO_OUT` | 自动生成 | 输出目录。 |
| `KEEP_RAW_OUTPUT` | `1` | 保留 `.cmd`、`.stdout`、`.dat` 和 `.cm` 文件并支持缓存；设为 `0` 时清理 raw 输出。 |

显式使用 `SCENARIO_CC=dcqcn` 时，当前 rate-based DCQCN 默认参数按
`400 Gbit/s / 7 us RTT / 350000-byte BDP` 校准：`initial_alpha=0.6`、
`min_rate=80 Gbit/s`、`alpha/rate interval=28 us`、`CNP interval=16.8 us`、
`byte_counter=1.4 MB`。其他链路速率或 RTT 应通过 `-dcqcn_*` 参数覆盖并
重新验证；默认实验口径仍是 `dcqcn_variant`。

## 输出文件

主测试输出目录包含：

- `summary.csv`：每个场景、每个方案的原始指标。
- `normalized.csv`：场景内相对最优值的归一化结果。
- `per_flow.csv`：逐流 FCT 明细。
- `comparison_report.md`：自动生成的 Markdown 汇总报告。
- `scenario_plan.md`：脚本本次展开的场景列表。
- `*_packet_lb_slowdown.png/pdf`：README 风格归一化对比图，指标为 avg FCT、p99 FCT、p99.9 FCT。

## 辅助 sglb 参数脚本

SGLB 默认使用四档 noisy-or top-K。原五因子评分保留为显式对照：

```bash
-sglb_score_mode legacy
```

同一 noisy-or top-K 的八档量化由 `-sglb_nmrc_levels 8` 启用。五因子参数对比脚本保留为辅助实验：

```bash
python3 experiments/n-mrc/run_sglb_factor_compare.py
```

示例：

```bash
SGLB_FACTOR_NODES=1024 SGLB_FACTOR_TIERS=3 SGLB_FACTOR_FLOW_SIZE=$((32 * 1024 * 1024)) \
python3 experiments/n-mrc/run_sglb_factor_compare.py
```

## 核心代码

- `experiments/n-mrc/run_literature_metric_compare.py`：README 标准场景生成、命令拼接、指标解析、报告生成和图表输出。
- `experiments/n-mrc/run_sglb_factor_compare.py`：sglb 参数对比。
- `sim/datacenter/main_roce.cpp`：RoCE CLI 参数、LB 模式选择、EV 空间按拓扑自动校准。
- `sim/roce.cpp` / `sim/roce.h`：源端 `ecmp`、`ops`、`reps`、`avail`/`grade`（内部复用 STOR）、`mrc` 选路状态机和 ACK/NACK feedback 更新。
- `sim/rocepacket.h`：RoCE packet/ACK/NACK 上携带 pathid、Grade/Avail 的 STOR feedback 和独立 n-MRC snapshot。
- `sim/datacenter/fat_tree_switch.cpp` / `sim/datacenter/fat_tree_switch.h`：交换机 ecmp / adaptive-routing / drill / sglb 转发、source-controlled pathid 分段映射、Avail/Grade 的 source-ToR feedback 生成。
- `sim/datacenter/fat_tree_topology.cpp` / `sim/datacenter/fat_tree_topology.h`：generated fat-tree 拓扑、bundle/radix 参数、慢链路注入。
- `MRC_IMPLEMENTATION.md`：独立 `mrc` 模式的实现细节、默认 CC/队列/参数、已实现机制和当前近似范围。

## 清理

```bash
rm -rf experiments/n-mrc/output experiments/n-mrc/output_*
make -C sim clean
```

## htsim 背景

htsim 是一个高性能离散事件仿真器，设计目标是快速研究拥塞控制算法行为。本 fork 保留 htsim/RoCE 仿真器结构，并在其上加入 n-MRC 相关实验。
