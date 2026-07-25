# RR 作为无状态 MRC 的健康场景验证

## 结论

RR 已统一为 MRC 的无状态版本。在没有触发 MRC 路径状态更新的健康场景
中，RR 与 MRC 使用完全相同的 per-QP EV permutation、EV→path 映射和
逐包选择序列，而不只是具有相同的路径覆盖率。

严格等价范围是：

- 新发送的 data packet；
- MRC 尚未收到有效 ECN、TRIM、LOSS 或 RTO 路径状态更新；
- 候选 EV 数不超过 MRC 的 32 个 active EV 上限。

MRC 收到有效反馈后会过滤或替换 EV，RR 则继续原始轮转。这是两者预期的
唯一机制差异。

## 修改前证据

环境为 128 节点、8 条路径、seed 13、单 QP、256 KiB，且无 ECN、丢包、
TRIM、NACK、RTO 或重传。

```text
RR:  0/3/6/1/4/7/2/5
MRC: 6/7/5/2/4/0/3/1
```

两者每轮都覆盖全部 8 个 EV，但初始排列不同，因此修改前 RR 不能作为
packet-for-packet 的 MRC 无状态控制组。

## 修改内容

- 提取 MRC 现有的 `src/dst/flow_id` 确定性 shuffle 作为公共 EV 顺序。
- MRC 继续在公共顺序上维护 ACTIVE/COOLING/FAILED/PROBING 和 backup。
- RR 只维护公共顺序和游标，不创建或更新任何 MRC 路径质量状态。
- 保留 STOR/NetAware 使用的原 affine selector，不改变其它方案。

单元测试验证：

1. 8 和 16 个 EV 下，RR/MRC 对两个不同 QP identity 均连续四轮逐包一致。
2. 不同 QP identity 仍产生各自的确定性 permutation。
3. 对一个 MRC EV 注入有效拥塞反馈后，MRC 跳过该 EV，而 RR 继续公共
   无状态顺序。

## 轻量健康验证

配置：

```text
nodes=128
connections=1
flow_size=256 KiB
paths=8
seed=13
linkspeed=400 Gbit/s
MTU=4096 B
CC=dcqcn_variant
RX=sp
transport=mrc_exact_bounded
```

结果：

| 指标 | RR | MRC |
|---|---:|---:|
| 完成 flow | 1 | 1 |
| FCT | 12.5795 us | 12.5795 us |
| data packet / ACK | 64 / 64 | 64 / 64 |
| unique EV | 8 | 8 |
| 每个 EV 使用次数 | 8 | 8 |
| ECN ACK | 0 | 0 |
| NACK / RTO | 0 / 0 | 0 / 0 |
| Trim / Drop / ECN mark | 0 / 0 / 0 | 0 / 0 / 0 |

修改后的共同序列：

```text
6/7/5/2/4/0/3/1
```

该序列循环 8 次；完整 `first128`、EV histogram、physical-path histogram
和 flow completion line 均完全相同。

## 128-QP 健康 permutation 验证

复用已有 `healthy_permutation_4mib`、seed 13 traffic matrix：

```text
SHA-256:
3330a9b22a106c99a8bd5bec71022ce0fbaefe9941f249b9c29a40eb8c993aa2
nodes=128
connections=128
per-flow size=4 MiB
paths=8
```

完成与路径统计：

| 指标 | RR | MRC |
|---|---:|---:|
| 完成 flow | 128 | 128 |
| 总 packet selection | 131,072 | 131,072 |
| unique EV / physical path | 8 / 8 | 8 / 8 |
| 每个 EV 使用次数 | 16,384 | 16,384 |
| ACK | 131,072 | 131,072 |
| ECN ACK | 0 | 0 |
| NACK / RTO | 0 / 0 | 0 / 0 |
| Trim / Drop / ECN mark | 0 / 0 / 0 | 0 / 0 / 0 |

FCT：

| FCT | RR | MRC | 差值 |
|---|---:|---:|---:|
| mean | 95.702309 us | 95.702309 us | 0 |
| p50 | 96.190400 us | 96.190400 us | 0 |
| p95 | 96.601445 us | 96.601445 us | 0 |
| p99 | 96.738490 us | 96.738490 us | 0 |
| max | 96.806200 us | 96.806200 us | 0 |

128 条 flow completion line 和全局 `PathSelectDiag` 完全相同。MRC 诊断为：

```text
ecn_cooldown_events=0
trim_events=0
rto_fail_events=0
nack_loss_fail_events=0
cycle_cooling_events=0
probe_events=0
backup_replacement_events=0
cooling_final=0
failed_final=0
```

因此该 workload 没有激活 MRC 学习，RR/MRC 的严格一致性符合预期。

## 对后续实验的含义

后续冷启动和 EV 覆盖实验可以直接把 RR 写为“无状态 MRC”：

- `MRC - RR` 的差异可以归因于 MRC 路径反馈状态，而不是初始编码、
  permutation、EV→path 映射或健康状态 spraying 不同。
- 健康场景应继续得到相同结果。
- 有 ECN/Trim/LOSS/RTO 的场景中，应结合 MRC 状态事件解释首次分叉，
  不能要求两者保持相同 FCT。
