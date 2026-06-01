# N-MRC Idea 与详细设计

本文记录 N-MRC 的核心 idea 和机制设计，用于继续打磨方案本身。

## 1. 核心 Idea

N-MRC 的核心想法是：源端不为每条 flow/QP 单独维护路径状态，而是按 `(src ToR, dst ToR)` 共享一份 EV/path 健康视图；目的 ToR 负责聚合同一 source ToR 过来的 packet 观测，把多条 EV 的 clean/ECN 信息批量反馈给源端；源端再根据这份共享状态做 packet-level EV 选择。

这样一来，同一个 ToR-pair 下的多个 QP 可以复用路径经验：

- 某条 EV 最近出现 ECN，后续同 ToR-pair 的 QP 都能少用它。
- 某条 EV 最近被观察为 clean，后续同 ToR-pair 的 QP 都能优先使用它。
- 反馈不是每个 ACK 只更新一条路径，而是一次 bitmap feedback 更新多个 EV。
- 源端不需要 per-flow path table，只需要共享 bitmap 加少量 per-flow 派生游标。

一句话概括：

> N-MRC = ToR-pair shared path state + ToR aggregated bitmap feedback + source-controlled packet-level EV selection。

## 2. 设计目标

- 在 packet 级别选择 EV/path，让大流能够利用多路径。
- 在 ToR-pair 粒度共享 path health，减少 per-QP 状态。
- 用 bitmap feedback 批量反馈多个 EV 的近期状态。
- 用 2-bit soft-state 表达 path 的健康程度，而不是简单好/坏二值。
- 通过 per-flow seed/stride 打散同一 ToR-pair 内不同 QP 的 EV 选择序列。
- 让 ECN 反馈快速影响选路，让 clean 反馈逐步恢复路径优先级。

## 3. 关键对象

| 对象 | 含义 |
| --- | --- |
| EV/pathid | 源端选择的端到端路径组合编号 |
| path space | 当前 ToR-pair 可选择的 EV 总数 |
| ToR-pair | `(src ToR, dst ToR)`，N-MRC 共享状态的 key |
| path bitmap | 每个 EV 对应一个状态值 |
| cursor | 当前 flow/priority 在 EV 空间里的扫描位置 |
| stride | 当前 flow/priority 扫描 EV 空间时的步长 |
| feedback bitmap | 目的 ToR 聚合后回传给源端的 path 观测结果 |

## 4. EV/path 空间设计

N-MRC 把一个 EV 看作完整的端到端路径组合，而不是某一级交换机的局部端口选择。

### 4.1 2-tier leaf-spine

2-tier 场景中，EV 空间可以表示为：

```text
path_space = ToR uplink choices * Agg-down bundle
```

默认 bundle 为 1 时，path space 等于 ToR 上行可选数。

### 4.2 3-tier fat-tree

3-tier 场景中，EV 空间可以表示为：

```text
path_space =
    ToR uplink choices *
    Agg uplink choices *
    Core-down bundle *
    Agg-down bundle
```

例如 generated 1024-node 3-tier topology 中，bundle 为 1 时，inter-pod EV 数为：

```text
8 * 8 = 64
```

### 4.3 分段解释

源端只写入一个整数 EV/pathid。交换机在不同 hop 上读取 EV 的不同段，并映射到当前 ECMP group：

```text
selected_port = ev_segment % ecmp_group_size
```

因此，同一个 EV 在每一级交换机上都有稳定含义。源端选择 EV，交换机负责把 EV 解释成逐跳端口选择。

## 5. Path State 设计

N-MRC 使用 2-bit 状态表示每条 EV 的近期健康程度。

| 状态 | 名称 | 含义 |
| --- | --- | --- |
| `11` | strong-good | 优先使用的路径 |
| `10` | usable | 可用路径，优先级低于 strong-good |
| `01` | suspect/low | 被降级的路径，只在 good/usable 不足时使用 |
| `00` | bad/reserved | 保留给不可用或二值模式语义 |

初始化时，所有 EV 都为 `11`：

```text
state[ev] = 11 for all ev
```

状态存储粒度：

```text
shared_state[(src_tor, dst_tor)][ev] = 2-bit value
```

同一 `(src ToR, dst ToR)` 下的所有 flow/QP 共享这份状态。

## 6. 源端选路设计

源端发送 packet 时，按以下顺序选择 EV。

### 6.1 派生 cursor 和 stride

每个 flow/priority 通过 hash 派生自己的起点和步长：

```text
seed = hash(src, dst, flow_id, priority)
cursor = seed % path_space
stride = choose_coprime_stride(seed, path_space)
```

`stride` 与 `path_space` 互质，保证扫描序列能够遍历完整 EV 空间。

### 6.2 加载共享 bitmap

发送前加载 ToR-pair 共享状态：

```text
bitmap = shared_state[(src_tor, dst_tor)]
```

如果本地没有该 ToR-pair 的状态，则初始化为全 `11`。

### 6.3 选择优先级

源端优先使用 `11`，当 `11` 数量不足时逐级放宽：

```text
if count(state >= 11) >= min_good_paths:
    min_state = 11
else if count(state >= 10) >= min_good_paths:
    min_state = 10
else if count(state >= 01) > 0:
    min_state = 01
else:
    fallback = random EV
```

### 6.4 伪轮询扫描

确定 `min_state` 后，从当前 flow 的 cursor 开始按 stride 扫描：

```text
for offset in 1..path_space:
    candidate = (cursor + offset * stride) % path_space
    if bitmap[candidate] >= min_state:
        cursor = candidate
        return candidate
```

这样同一 ToR-pair 下不同 QP 共享 path health，但不会完全同步选择同一条 EV。

## 7. 目的 ToR Feedback 设计

N-MRC 的 feedback 在目的 ToR 聚合生成。

### 7.1 聚合 key

目的 ToR 以 source ToR 为 key 维护窗口状态：

```text
feedback_state[src_tor] = {
    bitmap,
    packet_count,
    last_feedback_time
}
```

本地 ToR 自身就是 `dst_tor`，所以 key 中只需要 `src_tor`。

### 7.2 packet 观测

目的 ToR 收到 RoCE data packet 后：

```text
path = packet.pathid % path_count
```

如果 packet 带 ECN CE：

```text
feedback_bitmap[path] = bad
```

如果 packet 未带 ECN：

```text
feedback_bitmap[path] = observed-clean
```

在当前 2-bit feedback 语义中：

- bad 用 `0` 表示。
- observed-clean 用 `2` 表示。
- unknown/default 用 `1` 表示。

### 7.3 feedback 触发

目的 ToR 在两个条件之一满足时发出 feedback：

```text
packet_count >= feedback_pkts
```

或：

```text
now - last_feedback_time >= feedback_max_interval
```

同时使用 `feedback_min_interval` 控制 feedback 的最小间隔。

触发后，目的 ToR 把 feedback bitmap 附到当前 data packet 上，并重置本窗口：

```text
packet.feedback_bitmap = feedback_bitmap
feedback_bitmap = all unknown/default
packet_count = 0
last_feedback_time = now
```

## 8. ACK 回传设计

接收端收到带 N-MRC feedback 的 data packet 后，把 feedback bitmap copy 到 ACK：

```text
ack.feedback_bitmap = packet.feedback_bitmap
```

ACK 返回源端后，源端根据 feedback 更新 ToR-pair 共享 path state。

## 9. 源端状态更新设计

源端收到 ACK 后，若 ACK 携带 feedback bitmap，则更新对应 `(src ToR, dst ToR)` 的共享 bitmap。

### 9.1 ECN/bad 更新

如果 feedback 中某条 EV 为 bad：

```text
state[ev] = 01
```

也可以使用 graded degrade：

```text
state[ev] = max(01, state[ev] - 1)
```

当前主线配置采用直接降到 `01`。

### 9.2 observed-clean 更新

如果 feedback 中某条 EV 为 observed-clean：

```text
state[ev] = min(11, state[ev] + 1)
```

也就是：

```text
01 -> 10
10 -> 11
11 -> 11
```

### 9.3 unknown reopen

如果 feedback 中某条 EV 为 unknown，且开启 unknown reopen：

```text
state[ev] = min(10, state[ev] + 1)
```

也就是 unknown 可以让低状态路径重新回到 usable，但不直接升到 `11`。

### 9.4 更新后写回共享状态

```text
shared_state[(src_tor, dst_tor)] = state
```

后续同一 ToR-pair 下所有 QP 都能使用这份更新后的路径状态。

## 10. 参数设计

| 参数 | 含义 | 当前主线取值 |
| --- | --- | --- |
| `dtor_state_mode` | path state 模式 | `2bit-ecn01` |
| `dtor_unknown_reopen` | unknown 是否温和复开低状态路径 | enabled |
| `min_good_paths` | strong-good 集合的最低期望数量 | `clamp(path_space / 2, 1, 16)` |
| `feedback_pkts` | packet 数触发阈值 | `clamp(path_space / 2, 32, 128)` |
| `feedback_min_interval` | feedback 最小间隔 | `5us` |
| `feedback_max_interval` | feedback 最大间隔 | `20us` |
| `dtor_weak_sample_pkts` | 主动采样低状态路径的间隔 | 默认关闭 |

## 11. 完整流程

```text
1. 源端准备发送 packet
2. 源端根据 src/dst/flow/priority 派生 cursor 和 stride
3. 源端读取 ToR-pair shared bitmap
4. 源端优先从 11 状态路径中选择 EV
5. 若 11 不足，则放宽到 10，再放宽到 01
6. packet 携带 EV/pathid 进入网络
7. 每一级交换机按 EV 分段选择输出端口
8. 若队列拥塞，交换机对 packet 打 ECN CE
9. 目的 ToR 记录该 EV 的 CE 或 clean 观测
10. 目的 ToR 达到触发条件后，把 bitmap 附到 data packet
11. 接收端把 bitmap copy 到 ACK
12. 源端收到 ACK 后更新 ToR-pair shared bitmap
13. 后续 packet 使用更新后的 path state 继续选路
```

## 12. 与 OPS / REPS 的设计差异

| 方案 | 选路状态 | feedback 粒度 | 路径复用方式 |
| --- | --- | --- | --- |
| OPS | 无路径健康状态 | 无 | 每包随机 EV |
| REPS | per-QP clean EV buffer | ACK 对应单条 EV | 复用近期 clean EV |
| N-MRC | per-ToR-pair shared 2-bit bitmap | ToR 聚合多条 EV bitmap | 同 ToR-pair 多 QP 共享 path health |

N-MRC 相比 REPS 的关键变化是：

- 状态从 per-QP buffer 变为 per-ToR-pair shared bitmap。
- ACK 反馈从单 EV clean 信号变为多 EV bitmap。
- path 选择从缓存复用变为按 path state 分层伪轮询。
- path 恢复从一次 clean 直接可用变为 2-bit 渐进恢复。

## 13. 伪代码

### 13.1 源端 choose_path

```text
choose_path(src, dst, flow_id, priority):
    path_space = get_path_space(src, dst)
    key = (src_tor(src), dst_tor(dst))
    bitmap = shared_state.get_or_init(key, all_11(path_space))

    cursor, stride = get_or_derive_cursor_stride(
        src, dst, flow_id, priority, path_space
    )

    if count(bitmap >= 11) >= min_good_paths:
        min_state = 11
    else if count(bitmap >= 10) >= min_good_paths:
        min_state = 10
    else if count(bitmap >= 01) > 0:
        min_state = 01
    else:
        return random_ev(path_space)

    for offset in 1..path_space:
        ev = (cursor + offset * stride) % path_space
        if bitmap[ev] >= min_state:
            cursor = ev
            return ev

    return random_ev(path_space)
```

### 13.2 目的 ToR update_feedback

```text
update_feedback(packet):
    if packet is not data:
        return

    src_tor = host_to_tor(packet.src)
    path = packet.pathid % path_count
    state = feedback_state[src_tor]

    if state.bitmap is empty:
        state.bitmap = all_unknown(path_count)

    if packet.has_ecn_ce:
        state.bitmap[path] = bad
    else:
        state.bitmap[path] = observed_clean

    state.packet_count += 1

    if should_emit_feedback(state):
        packet.feedback_bitmap = state.bitmap
        state.bitmap = all_unknown(path_count)
        state.packet_count = 0
        state.last_feedback_time = now
```

### 13.3 源端 update_state_on_ack

```text
update_state_on_ack(ack):
    if ack has no feedback:
        return

    key = (src_tor(local_src), dst_tor(remote_dst))
    state = shared_state.get_or_init(key, all_11(path_space))

    for ev in 0..path_space-1:
        fb = ack.feedback_bitmap[ev]

        if fb == bad:
            state[ev] = 01
        else if fb == observed_clean:
            state[ev] = min(11, state[ev] + 1)
        else if fb == unknown and unknown_reopen:
            state[ev] = min(10, state[ev] + 1)

    shared_state[key] = state
```

## 14. 当前代码映射

| 设计模块 | 当前代码位置 |
| --- | --- |
| CLI 和 canonical 参数 | `sim/datacenter/main_roce.cpp` |
| 源端 EV 选择 | `sim/roce.cpp` 的 `RoceSrc::choose_path` |
| 源端 N-MRC 状态更新 | `sim/roce.cpp` 的 `RoceSrc::update_dtor` |
| ToR-pair shared bitmap | `sim/roce.cpp` / `sim/roce.h` 的 `_dtor_shared_bitmaps` |
| cursor/stride 派生 | `sim/roce.cpp` 的 `init_dtor_priority` |
| packet/ACK feedback 字段 | `sim/rocepacket.h` |
| 目的 ToR feedback 聚合 | `sim/datacenter/fat_tree_switch.cpp` 的 `maybe_update_dtor_feedback` |
| EV 分段映射 | `sim/datacenter/fat_tree_switch.cpp` |

## 15. 文档中的命名

代码里仍沿用历史名称 `dtor`，本文统一写作 N-MRC。

| 文档名 | 代码/CLI 名 |
| --- | --- |
| N-MRC | `-lb dtor` |
| N-MRC state mode | `-dtor_state_mode` |
| unknown reopen | `-dtor_unknown_reopen` |
| path bitmap | `DtorBitmap` |
| ToR-pair shared bitmap | `_dtor_shared_bitmaps` |
