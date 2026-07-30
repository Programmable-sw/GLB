# MRC/SGLB feedback-transition experiment design

## Claim under test

The experiment must test a transition, not just produce a short-flow case in
which MRC loses:

1. A short flow, or the initial phase of a long-lived QP, finishes its new-data
   path selections before MRC feedback can affect a later selection. SGLB may
   already use warmed shared fabric state, so MRC can have worse FCT.
2. As the QP lifetime grows, MRC makes more new-data selections after its first
   effective state update. Its closed loop can then avoid degraded EVs.
3. MRC should first improve relative to the feedback-free RR ablation. Because
   cold-start queueing and congestion-window damage are sunk costs,
   MRC/SGLB FCT need not fall immediately when feedback becomes actionable;
   it should fall toward one once the post-update phase is long enough. The
   claim does not require MRC to beat SGLB.

## Alternatives considered

### Cross-size FCT only

Extend the warmed short-flow matrix to long flows and plot MRC/SGLB FCT versus
flow size. This is simple, but a falling ratio alone does not prove that MRC
feedback caused the recovery.

### Within-flow phase timing only

Instrument a long QP before and after its first state update. This directly
shows when feedback can act, but phase-local latency is not the same metric as
complete-flow FCT and would require more invasive packet-level accounting.

### Combined transition with RR feedback ablation

Use the cross-size matrix, retain minimal first-update diagnostics, and run RR
on the identical traffic. RR is the no-state version of the same MRC EV
encoding and round-robin selector. This is the selected design because it
links the MRC/SGLB FCT transition to both feedback opportunity and the
incremental value of MRC feedback.

## Controlled input

- 128 nodes, two tiers, eight physical paths.
- Reuse the existing WebSearch discrete sizes:
  6, 13, 19, 33, 53, 133, 667, 1,333, 3,333, 6,667, 20,000, and 30,000 KiB.
- Use seeds 13, 29, and 47.
- Start fixed-link background traffic at time zero on five of eight spine
  choices, leaving three clean choices. Foreground flows start from 100 to
  600 us, after SGLB's 1 us local and 5 us downstream state periods begin.
- Keep the path-pressure burst active from 0 to 1,000 us. Short flows finish
  inside the QP warm-up interval; long flows cross both the first actionable
  feedback point and the end of the burst. RR separates feedback recovery from
  the unavoidable amortization of a finite burst.
- Reuse each seed's exact traffic file for MRC, SGLB, and RR. Commands must be
  identical except for `-lb` and output path.
- Use exact-bounded RoCE recovery, DCQCN variant, 4 KiB MTU, identical queues,
  link rate, topology, and latency.
- Use size-dependent sample counts so every size has useful replication
  without allowing 20–30 MiB flows to dominate aggregate offered bytes. All
  schemes receive the same counts.

## Calibration policy

The background rate and sustained-versus-burst shape may be selected by a
small seed-13 pilot. Scaling the pilot must preserve aggregate foreground
arrival intensity by scaling both count and arrival window. A valid input
must:

- complete every foreground flow in every scheme;
- make SGLB state discriminative for a non-trivial fraction of route calls;
- retain at least three clean candidates under SGLB's default top-k rule;
- exhibit both non-actionable short flows and actionable long flows.

The pilot selects network pressure, not individual favorable flows. The final
matrix uses the chosen rate unchanged for all three seeds. Any tried rates and
rejections are recorded.

### Calibration record

- The first 5% pilot incorrectly retained the full 5 ms arrival window. Its
  aggregate foreground arrival rate was 246.5 Gbit/s versus 4,573.5 Gbit/s in
  the full matrix, so all three rates were rejected before interpretation.
- After count and arrival-window scaling were coupled, sustained 5-hot-path
  inputs at 300/340 Gbit/s left SGLB almost entirely all-zero, while
  380 Gbit/s made MRC/SGLB grow with flow size even though MRC/RR improved.
- Sustained 3-hot-path inputs at 360/380/390 Gbit/s showed the same boundary:
  MRC feedback beat RR, but an always-current SGLB view retained an
  irreducible advantage. These are retained as a counterexample to universal
  MRC/SGLB convergence.
- A finite 1,000 us burst was then tested because it matches the requested
  long-QP warm-up claim. Five hot paths at 340/360/380 Gbit/s all completed.
  The 340 Gbit/s point was selected before the three-seed run: short flows
  were non-actionable and mildly disadvantaged; MRC/RR improved when feedback
  became actionable; and the seed-13 MRC/SGLB ratio fell from 1.696 at
  3.3 MiB to 1.153 at 30 MiB. SGLB saw non-zero path differentiation in 27.0%
  of its route calls. Higher rates made the final ratio materially larger and
  were rejected.

## Metrics

For each strictly paired flow:

- MRC, SGLB, and RR FCT;
- `MRC/SGLB FCT` and `MRC/RR FCT`;
- whether MRC received quality feedback before completion;
- whether feedback was actionable;
- total new-data selections;
- selections before the first effective state update;
- selections after the first update;
- post-update selection fraction;
- EV coverage and complete sweep count.

Aggregate each exact flow size with:

- paired FCT-ratio geometric mean;
- per-scheme p50 and p99 FCT;
- actionable-flow fraction;
- mean post-update selection fraction;
- three-seed range and direction count.

SGLB route diagnostics must report candidate count, distinct quality count, and
all-zero-quality fraction to prove that the shared view was actually useful.

## Evidence criteria

The mechanism claim is supported only if the final matrix shows all of:

1. Short sizes have zero or near-zero actionable feedback and zero or near-zero
   post-update selection fraction.
2. Those short sizes have a material MRC/SGLB FCT disadvantage in the warmed,
   discriminative SGLB environment.
3. Actionable and post-update fractions rise for longer flows.
4. MRC/RR improves when feedback becomes actionable, providing
   feedback-ablation
   evidence that MRC's recovery is caused by its closed loop.
5. After any intermediate cold-start/recovery peak, MRC/SGLB FCT falls toward
   one for the longest sizes and is not driven by one seed.

If any criterion fails, the report states the observed boundary instead of
claiming the requested transition.

## Deliverables

- Reproducible runner and unit tests.
- Per-flow paired compressed data, per-seed and cross-seed CSV summaries.
- A figure aligning MRC/SGLB FCT, MRC/RR FCT, actionable fraction, and
  post-update selection fraction on the same flow-size axis.
- A Markdown explanation that distinguishes feedback receipt, state update,
  actionable feedback, and eventual FCT impact.
