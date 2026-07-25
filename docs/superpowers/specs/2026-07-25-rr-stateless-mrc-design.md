# RR as Stateless MRC Design

## Goal

Define the public `rr` scheme as the stateless form of MRC. In a healthy run
where MRC receives no ECN, TRIM, loss, NACK, or RTO signal that changes path
state, RR and MRC must select the same EV and physical path for every new data
packet. MRC may diverge only after its path-state machine receives an effective
quality signal.

This alignment establishes RR as the controlled baseline for the later MRC
cold-start and EV-coverage experiments. It replaces the need for a separate
`mrc-static` scheme.

## Current Behavior and Motivation

Both existing schemes use encoded EVs with identity EV-to-path mapping and
cover all eight paths once per cycle on the 128-node topology. They do not,
however, use the same per-QP order:

```text
RR,  first cycle: 0 3 6 1 4 7 2 5
MRC, first cycle: 6 7 5 2 4 0 3 1
```

These sequences were observed with seed 13 in a one-QP, 256 KiB, 128-node
healthy run with eight paths and zero ECN marks, drops, trims, NACKs, RTOs, or
retransmissions. RR currently derives an affine start and stride, whereas MRC
builds a deterministic shuffled EV vector. A comparison between them is
therefore not yet a packet-for-packet control.

## Unified Selection Semantics

The MRC deterministic EV permutation becomes the common healthy-state order.
It is keyed by the existing QP identity inputs and path-space size, and it is
used by both RR and MRC.

For a path space no larger than MRC's active-EV limit:

1. Both schemes create the same encoded EV set.
2. Both schemes create the same deterministic EV permutation.
3. Both schemes start at the same position and advance one position for every
   new data-packet selection.
4. EV `i` maps to physical path `i`.
5. RR ignores all MRC path-quality state because it does not own such state.
6. MRC applies its existing active, cooling, failed, probing, and backup rules
   on top of the common order.

The 128-node and 512-node topologies expose 8 and 16 paths respectively, so all
candidate EVs are active and the equivalence is exact before a state-changing
feedback event. Behavior above the 32-active-EV limit is outside this
experiment and must not be used to claim exact RR/MRC equivalence.

Only new data-packet selection is covered by the strict equivalence. Once a
loss or trim causes retransmission, MRC may use its existing
state-aware retransmission selection. Such a run is no longer a healthy
no-learning validation case.

## Implementation Boundaries

Extract or reuse one deterministic MRC-order initializer rather than
maintaining separate RR and MRC permutation formulas. RR should consume that
order without constructing MRC quality state. MRC retains its existing EV
records and consumes the same order as its active-vector order.

The change must not alter:

- MRC feedback attribution or state transitions;
- cooldown, failure, probing, or backup-promotion semantics;
- retransmission and transport correctness;
- congestion-control behavior;
- EV-to-physical-path encoding;
- `ecmp_rr`, OPS, REPS, N-MRC, STOR, or NetAware;
- topology construction, queue configuration, or traffic generation.

The public documentation will state that RR is MRC's stateless baseline and
will state the 32-active-EV boundary of strict equivalence.

## Verification

### Unit-level equivalence

Add a regression test that constructs matched RR and MRC QPs and compares their
selected EV/path sequence for at least four complete rotations with path
spaces 8 and 16. The test must cover:

- identical source, destination, flow ID, priority, and seed;
- two different QP identities, proving that each pair remains equal while
  different QPs may receive different deterministic permutations;
- zero MRC state-changing feedback;
- exact sequence equality, not only equal coverage histograms.

A second test delivers an effective MRC congestion signal for one EV and
verifies that MRC skips or delays that EV while RR keeps following the common
stateless order. This proves that feedback-driven state is the intended and
only divergence in the test.

### Lightweight healthy simulation

Repeat the existing one-QP, 256 KiB, 128-node, eight-path seed-13 run for RR and
MRC. Acceptance requires:

- both processes exit successfully and the flow completes exactly once;
- zero ECN marks, trims, drops, NACKs, RTOs, and retransmissions;
- identical `PathSelectDiag first128` sequences;
- identical EV and physical-path histograms;
- identical flow completion time.

### Existing healthy workload smoke test

Run the retained 128-node `healthy_p2p_permutation_4mib` workload at seed 13
with its existing traffic matrix and 128 connections for both RR and MRC. This
smoke test checks that both schemes remain runnable in a realistic multi-QP
configuration.

If the workload produces an effective MRC state transition, packet-sequence or
FCT equality is not required; the report must identify the first divergence
and its feedback cause. If it produces no state transition, RR and MRC must
remain sequence-equivalent and produce identical completion statistics.

## Output

The validation report will include:

- the pre-change lightweight sequences shown above;
- the post-change RR and MRC sequences;
- health counters proving whether learning was activated;
- completion and FCT checks;
- the existing-workload smoke-test result;
- a clear conclusion on whether RR is now a valid stateless MRC control.

This stage does not implement MRC-shared and does not run the full limitation
experiment matrix.
