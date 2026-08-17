# SGLB Min-Choices Scan Design

## Objective

Determine the best candidate-set lower bound for the current 64-path SGLB
implementation.  The primary objective is All-to-All collective completion
time (CCT).  A candidate is eligible only when its aggregate healthy P2P and
healthy WebSearch primary metrics regress by no more than 1% relative to the
current `min_choices=24` default.

The scan evaluates lower bounds `16, 20, 24, 28, 32, 36, 40`, corresponding
to nominal floors of `25%, 31.25%, 37.5%, 43.75%, 50%, 56.25%, 62.5%` of the
64 paths.  These are lower bounds, not fixed spraying fractions: all paths in
the best quality level remain candidates even when their count exceeds the
configured minimum.

## Fixed SGLB Semantics

Only `-sglb_min_choices` changes between candidates.  Every cell uses the
current `-lb sglb` implementation and therefore retains:

- 64 physical paths on the canonical 256-host, 4-Leaf, 64-Spine topology;
- real 256-byte high-priority GCN packets;
- independent, versioned `(Spine, destination Leaf)` producers;
- change-triggered notification with a 15 us minimum interval per producer;
- 1 us local quality refresh and 30 us remote-state aging;
- raw linear local/remote queue pressure with 5/10/20% quality thresholds;
- four quality levels;
- exact minimum fill: keep the entire best level and fill only the missing
  number of boundary-level paths;
- per-destination shuffled round-robin, with a new candidate order each cycle;
- `dcqcn_variant`, SP receive, 64-bit SACK, `mrc_exact_bounded`, and exact
  TRIM recovery.

The runner must validate the effective configuration from simulator output and
reject any cell whose runtime configuration differs.

## Experiment Matrix

Each workload is run with seeds `13, 29, 47` and all seven min-choice values,
for 84 cells in total.

1. `healthy_permutation_16mib`: 256 one-to-one permutation flows, 16 MiB per
   flow, all starting at 0 us.  This is the healthy P2P guardrail workload.
2. `healthy_websearch_100`: the retained 100%-load WebSearch workload on the
   same canonical topology.  This is the healthy mixed-size guardrail.
3. `fixed_hotspot_permutation_16mib`: the P2P permutation workload with 16 of
   64 fixed paths carrying continuous 390 Gbit/s background pressure.  It
   checks candidate behavior under persistent path imbalance.
4. `periodic_background_a2a_p16_256mib`: global All-to-All with parallelism 16,
   256 MiB messages, and the retained periodic high-pressure background used
   by the historical min-choice experiments.  This is the primary CCT
   workload.

Traffic is materialized once per `(workload, seed)` and reused byte-for-byte
across all seven candidates.  The manifest records traffic SHA-256 values,
simulator SHA-256, source revision, commands, seeds, and all fixed parameters.

## Metrics and Eligibility

The runner records, per cell:

- completed and expected flows;
- CCT, mean, p50, p95, p99, p99.9, and maximum FCT;
- retransmissions, RTOs, trims, and ECN marks;
- Spine queue coefficient of variation, mean queue, and queue p99 fraction;
- average available, best-level, and candidate path counts;
- non-best, GOOD, DEGRADED, BAD, and AVOID selection counts and fractions;
- GCN updates, packets, bytes, deliveries, accepted profile updates, stale
  packets, remote-profile hits, and misses.

For each candidate, seed-level ratios are computed against `min24` using the
same workload and seed, then combined by geometric mean.

Eligibility requires both:

- healthy P2P p99-FCT geometric-mean ratio `<= 1.01`;
- healthy WebSearch p99-FCT geometric-mean ratio `<= 1.01`.

Among eligible candidates, the winner is the lowest geometric-mean CCT on
`periodic_background_a2a_p16_256mib`.  Ties within 0.1% are broken by, in
order: lower A2A p99 FCT, lower fixed-hotspot CCT, fewer trims, fewer
retransmissions, and lower ECN marks.  The report also retains unrestricted
rankings for every metric and a Pareto frontier over A2A CCT, A2A p99,
retransmissions, trims, ECN, and the two healthy guardrail ratios.

## Runner and Outputs

Create a focused runner under `experiments/n-mrc/` and a matching runner test
under `sim/tests/`.  Reuse archived workload generators where their traffic
definitions are still canonical, but copy the minimal required generation and
parsing logic into the focused runner so the experiment remains runnable after
archive cleanup.

The output directory is
`experiments/n-mrc/output/sglb_min_choices_64path_scan_256/` and contains:

- `commands.tsv`: exact command for every cell;
- `manifest.json`: immutable experiment configuration and fingerprints;
- `cells.csv`: one validated row per cell;
- `seed_ratios.csv`: paired ratios against min24;
- `summary.csv`: one row per min-choice candidate;
- `rankings.csv`: complete per-metric rankings;
- `pareto.csv`: Pareto membership and dominated-by information;
- `report.md`: answer-first conclusions, guardrail decisions, rankings, and
  limitations;
- compressed raw stdout for each cell, while large reconstructable simulator
  output files are omitted after successful parsing.

## Execution and Failure Handling

Before the full scan, run a gate cell for `seed=13`, `min_choices=24`, and the
healthy P2P workload.  It must complete all flows, report the fixed SGLB
configuration, emit nonzero real GCN traffic, have zero stale GCN packets, and
show no missing remote profile lookups.

The full run uses bounded worker concurrency.  Completed, validated cells are
reused on restart only when their command fingerprint, traffic fingerprint,
and simulator fingerprint match.  A failed, incomplete, mismatched, or
malformed cell is never included in summaries and causes the run to finish
with a nonzero status after preserving its diagnostic log.

## Interpretation Limits

- This identifies the best tested lower bound for the specified workloads and
  primary CCT objective; it does not prove a universal constant for every
  topology or traffic distribution.
- Seven candidates were selected before observing this scan, but choosing the
  minimum still introduces selection bias.  All candidate results and ranks
  must remain visible.
- The three traffic seeds are the independent replication unit.  Per-flow
  samples must not be presented as independent experimental replications.
- The min-choice value is a candidate floor.  Reports must show the observed
  average candidate fraction rather than describing it as a fixed percentage.
