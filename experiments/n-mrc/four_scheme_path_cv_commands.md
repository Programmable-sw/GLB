# P16 路径 CV 时序复现命令

在仓库根目录先执行：

```bash
make -C sim -j2
```

公共参数：

```text
-tm experiments/n-mrc/evidence/full_global_p16_256mib_background_off_seed13.cm
-nodes 128 -conns 16256 -tiers 2 -linkspeed 400000
-queue_type composite_ecn_lb -host_queue_type prio -mtu 4096
-end 100000 -paths 8 -seed 13 -cc dcqcn_variant
-roce_rx_mode sp -roce_sack_bitmap_bits 64
-roce_transport_semantics mrc_exact_bounded -roce_trim_recovery exact
-hop_latency 0.5 -switch_latency 0.5 -queue_cv_sample_us 100
-path_selection_timeline_every 100000
```

四个方案分别在公共参数后加入：

| 方案 | 方案参数 | trace 输出 |
|---|---|---|
| avail | `-lb avail` | `-path_selection_timeline avail.csv` |
| grade | `-lb grade` | `-path_selection_timeline grade.csv` |
| netaware | `-lb netaware -netaware_weight_adaptation good_share_cap` | `-path_selection_timeline netaware.csv` |
| n-mrc | `-lb n-mrc -nmrc_ev_mode encoded -nmrc_reroute_policy better_ge3 -nmrc_fastcnp on` | `-path_selection_timeline n-mrc.csv` |

生成派生 CSV 和 PNG：

```bash
python3 experiments/n-mrc/analyze_four_scheme_path_cv.py \
  --trace avail=avail.csv \
  --trace grade=grade.csv \
  --trace netaware=netaware.csv \
  --trace n-mrc=n-mrc.csv \
  --window-selections 100000 \
  --csv experiments/n-mrc/four_scheme_path_cv_timeline.csv \
  --png experiments/n-mrc/four_scheme_path_cv_timeline.png
```
