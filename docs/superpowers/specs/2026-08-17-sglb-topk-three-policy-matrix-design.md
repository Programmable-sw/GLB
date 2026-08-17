# SGLB GCN TopK Three-Policy Experiment Design

## Goal

Re-run the six SGLB/GCN variants under one reproducible root-cause workload and compare three candidate-selection policies. Preserve every raw result and highlight, rather than exclusively report, the lowest p99 flow-completion time for each variant.

## Experiment matrix

The six variants are:

1. Original SGLB using directly available state.
2. Synchronous batched GCN with a 15 us remote-state cache.
3. GCN packets retained while routing reads remote queues live.
4. Local-queue-only scoring.
5. GCN-remote-cache-only scoring.
6. Current asynchronous real-packet GCN.

Each variant is run with these three policies, for 18 cells total:

- `strict_topk_8`: rank candidates with the variant's normal ranking inputs and retain exactly the best 8 when at least 8 are available.
- `strict_topk_24`: the same rule with 24 candidates.
- `native`: reproduce the historical/native selector for variants 1-5 and the current default `min_choices=24` selector for variant 6.

The strict policies use one shared selection helper so that `TopK` has the same meaning across all six variants. Existing default behavior remains unchanged unless the experiment-only selector flag is supplied.

## Implementation approach

Use an isolated Git worktree based on the current branch. Restore the already-implemented historical paper-SGLB CLI controls from the archive branch into the experimental binary, without changing the underlying algorithms or production defaults. Add an explicit candidate-policy switch for current asynchronous SGLB and route strict policies through the same exact-K helper used by the historical variants.

The runner records the resolved configuration for every cell. It must fail rather than silently substitute a different selection policy or GCN mode.

## Fixed workload

All cells reuse the same generated traffic file and its recorded SHA-256 digest:

- 256 hosts in a two-tier Clos: 4 leaves, 64 hosts and 64 uplinks per leaf, 64 spines, 64 equal-cost paths.
- 256 one-to-one permutation flows generated with seed 29.
- 16 MiB per flow, all starting at time zero.
- 400 Gb/s links; 0.5 us link latency and 0.5 us switch latency.
- 4096-byte MTU, 352256-byte queues, host-priority queueing, DCQCN variant, shortest-path routing, 64-bit SACK, exact-bounded and exact-trim behavior.
- 10 ms simulation end time.
- SGLB quantized levels and thresholds remain at the selected variant's recorded configuration; current asynchronous GCN retains 1 us local sampling, 15 us remote GCN sampling, and 30 us aging.

No cell may regenerate traffic independently.

## Metrics and winner selection

The primary metric is p99 FCT in microseconds. Secondary diagnostics include mean FCT, completed-flow count, retransmissions, trims, ECN marks, queue CV and peak, GCN update/packet/byte counts, accepted deliveries, and stale-update drops.

For each of the six variants, the reported winner is the policy with the lowest p99 FCT among its three cells. All 18 values remain in the report to make the selection visible and avoid hiding selection bias. The report also includes an overall best cell, but does not treat it as an algorithm-only conclusion because this is a single-seed experiment.

## Validation and failure handling

Before the full run:

- Unit-test or focused-test exact-K behavior, including tied levels and fewer-than-K candidates.
- Verify the default selector remains byte-for-byte behaviorally unchanged when no experiment flag is present.
- Compile the experimental binary and run a short smoke cell for each GCN mode.

For every measured cell:

- Record commit, binary digest, traffic digest, exact command, stdout/stderr, and parsed metrics.
- Require 256/256 flows to complete.
- Confirm the logged selector, K, remote-state mode, ablation, and scoring weights match the matrix cell.
- For real GCN modes, reconcile GCN packet, byte, delivery, and stale-update counters.
- Mark a failed or invalid cell explicitly; never replace it with a different configuration.

## Deliverables

- A machine-readable CSV and JSON containing all 18 cells.
- Raw compressed logs and exact commands for every cell.
- A Markdown report with the complete matrix, per-variant winners, workload definition, hashes, diagnostics, and caveats.
- Experimental code changes isolated from the user's main worktree until results are reviewed.
