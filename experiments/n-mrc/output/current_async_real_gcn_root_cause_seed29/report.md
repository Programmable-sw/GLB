# Current asynchronous real-GCN comparison

Run date: 2026-08-17.

Source commit: `7eda2d2df13e2a8e95d2c3b285f8e771204f1d73`.
Simulator SHA-256:
`b211c731d4c2cd8166a3878250bd765f304c8af89cd6b63b2392e4f85c4d5780`.

This adds the current default SGLB implementation to the earlier seed-29
root-cause table. The current row uses exactly the same traffic matrix as the
historical rows (`SHA-256
472a244b549667c5740d911b3fdd4ccece4d8c987e112350b77f67d0a6636a28`).

## Comparison

| Variant | p99 FCT | Retransmissions | Trim | ECN | Spine queue CV | Peak queue |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Original SGLB | 357.365 us | 0 | 0 | 5 | 0.570 | 41.8 kB |
| Synchronized batched GCN, 15 us cache | 652.847 us | 6344 | 6188 | 61296 | 1.600 | 341.1 kB |
| GCN traffic retained, remote queue read live | 357.271 us | 0 | 0 | 0 | 0.580 | 41.6 kB |
| Local `Q(L)` only | 357.377 us | 0 | 0 | 0 | 0.529 | 41.7 kB |
| GCN-fed remote `Q(R)` only | 717.257 us | 7482 | 7188 | 74204 | 1.411 | 345.3 kB |
| **Current default: independent change-triggered real GCN** | **355.873 us** | **0** | **0** | **0** | **0.454** | **20.9 kB** |

The current row is numerically the lowest in this one-seed screen: 0.42%
below original SGLB, 0.39% below the live-remote oracle ablation, and 45.49%
below the old synchronized batched-GCN implementation.

This is not a pure synchronization-only comparison. The current default also
uses `real_gcn_raw_linear`, four quantized levels, and `min_choices=24`, while
the historical paper-like row used TopK=8. The row answers "how does the
current default perform on the identical workload?"; it does not attribute
the full difference solely to asynchronous sending.

## Current GCN validation

- One independently versioned producer is maintained per
  `(spine, destination leaf)`.
- Changes are sent immediately when eligible; changes within 15 us coalesce
  to that producer's independent deadline.
- The run emitted 6222 producer updates, 24888 GCN packets, and 6371328 GCN
  bytes. The byte count equals `24888 * 256` exactly.
- All 24888 deliveries were accepted; stale GCN count was zero.
- Remote snapshots were used 268480 times and were never missing.
- All 256 flows completed. Independent recomputation produced mean FCT
  354.046152 us and p99 FCT 355.872900 us.

## Experiment settings

| Setting | Value |
| --- | --- |
| Topology | Two-tier leaf-spine |
| Hosts | 256 |
| Leaf switches | 4 |
| Hosts/downlinks per leaf | 64 |
| Uplinks per leaf | 64 |
| Spine switches / equal-cost paths | 64 / 64 |
| Traffic | One-to-one random permutation |
| Flows | 256, all starting at 0 us |
| Bytes per flow | 16 MiB (16777216 B) |
| Traffic seed | 29 |
| Link rate | 400 Gbit/s |
| Link and switch latency | 0.5 us each |
| Switch queue | `composite_ecn_lb`, 352256 B |
| Host queue | `prio` |
| MTU | 4096 B |
| Congestion control | `dcqcn_variant` |
| Receive mode | SP with 64-bit SACK bitmap |
| Transport/recovery | `mrc_exact_bounded`, exact TRIM recovery |
| Simulation horizon | 10000 us |
| Queue-CV sampling | 100 us |
| SGLB score | `nmrc_quantized_topk`, `real_gcn_raw_linear` |
| Local / GCN update | 1 us / 15 us |
| GCN aging | 30 us |
| Thresholds | 0.05 / 0.10 / 0.20 |
| Minimum choices | 24 |
| Primary metric | Linear-interpolated p99 of the 256 per-flow FCT values |

The exact command is stored in
`raw/healthy_permutation_16mib/sglb/seed_29/command.txt`; the compressed
stdout, parsed row, and traffic matrix are retained beside this report.

## Caveat

This is a deterministic single-seed comparison. It is suitable for inserting
the current implementation into the original root-cause screen, but it is not
enough to establish a statistically robust universal ranking.
