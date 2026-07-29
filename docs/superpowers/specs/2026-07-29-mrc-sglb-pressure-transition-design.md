# MRC–SGLB Pressure-Transition Experiment Design

## Goal

Repeat the existing 5 ms pressure-transition comparison with default SGLB as
the baseline, while preserving the existing MRC cells, traffic matrices, seed,
topology, transport, persistent path asymmetry, and flow-size reporting.

## Experimental control

- Reuse the existing 128-node, eight-path WebSearch traffic matrices at 60% and
  80% offered load, seed 13, with a 5 ms arrival window.
- Reuse the validated MRC cell only when its simulator and traffic SHA-256
  match the current binary and selected traffic matrix.
- Run SGLB with the same common command as MRC, changing only `-lb sglb`.
- Keep the four random-sparse half-rate ToR uplinks and the exact-bounded RoCE
  transport configuration.
- Use the current default SGLB configuration: 1 μs local quality cache, 5 μs
  GCN export cache, four score levels, and minimum candidate count three.

This is a comparison between two complete schemes, not a single-variable
mechanism ablation: MRC is endpoint, per-QP, feedback-driven routing; SGLB is
switch-side, cross-flow, periodically refreshed routing.

## Pairing and metrics

Parse each traffic matrix into `flow_id`, source, destination, start time, and
flow size. Parse SGLB completion records and strictly require one completion
for every foreground flow. Pair SGLB with cached MRC on flow ID and validate
source, destination, start, and size.

For every offered-load and flow-size cell, report:

- paired-flow geometric mean of `MRC FCT / SGLB FCT`;
- arithmetic mean and p99 FCT for each scheme;
- paired-flow count;
- MRC actionable-flow fraction and mean actionable update count.

Values below one mean MRC is faster than SGLB.

## Artifacts

Create a new output directory,
`experiments/n-mrc/output/mrc_sglb_pressure_transition_examples_5ms`, containing
the SGLB raw command/output, a strict cell manifest, a focused CSV, and PNG/PDF
figures. Write the result interpretation to
`experiments/n-mrc/mrc_sglb_pressure_transition_example.md`.

## Validation

- Unit-test traffic/completion parsing, strict pairing, and geometric summaries.
- Require matching simulator and traffic hashes for cached MRC.
- Require SGLB effective-configuration diagnostics and exact flow completion.
- Verify all expected load/size cells, finite positive FCTs, artifact links,
  and a clean staged diff before committing.
