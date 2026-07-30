# Warmed Short-Flow MRC–SGLB Experiment Design

## Question

Test the conditional claim that short flows with too few endpoint feedback
opportunities are disadvantaged under MRC when SGLB already has a
discriminating, cross-flow fabric view.

The experiment must not claim that SGLB is omniscient or that every shortest
flow is always faster. Existing data contains a 6 KiB counterexample and shows
that default SGLB frequently sees all paths in the same quality grade.

## Controlled input

- 128 nodes, eight paths, seeds 13/29/47.
- Exactly 1,000 foreground flows at each existing size 6, 13, 19, 33, 53, and
  133 KiB per seed.
- Reuse the same traffic matrix for MRC and SGLB.
- Start foreground flows uniformly from 100 to 600 μs.
- From time zero, apply fixed-link background at 420 Gbit/s to the same five
  of eight spine choices in both directions, leaving exactly three clean
  choices. The background remains on for 1,000 μs.
- Keep topology, queues, DCQCN, MTU, exact-bounded reliability, and latency
  identical. Change only `-lb mrc` versus `-lb sglb`.

The 100 μs offset is longer than SGLB's 1 μs local and 5 μs remote cache
periods. Five hot and three clean choices match SGLB's minimum candidate count
of three, so a discriminating cache can exclude the hot grade without pulling
it back into the candidate set.

## Evidence

Pair each flow on `(seed, flow_id, src, dst, start, flow_size)` and report:

- geometric mean `MRC FCT / SGLB FCT`, with per-seed values;
- MRC actionable-flow fraction;
- MRC EV coverage and complete-sweep fraction;
- p50 and p99 FCT for both schemes;
- SGLB all-zero-quality and candidate-choice diagnostics.

The claim is supported only if the short-flow ratio is consistently above one
across seeds while MRC actionable feedback remains zero or negligible and SGLB
reports a nontrivial reduction in candidate choices. Any contrary size or seed
must be reported.

## Artifacts

Create a reproducible runner, tests, raw cell summaries, paired-flow data,
CSV/PNG/PDF results, and append the conditional result to
`experiments/n-mrc/mrc_sglb_pressure_transition_example.md`.
