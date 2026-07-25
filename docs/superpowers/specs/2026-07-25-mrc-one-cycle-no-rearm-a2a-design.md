# MRC One-Cycle No-Rearm A2A Design

## Goal

Change the default MRC `one_cycle` cooldown so that the first ECN or TRIM
feedback for an active EV starts one selection cycle of cooldown, while
additional ECN or TRIM feedback received for that EV during the same cooldown
does not extend its deadline. Evaluate the change first on seed 13 across the
complete 128-node A2A matrix.

## Scope

The source change applies only to MRC `one_cycle` congestion handling.
It must not change:

- MRC EV initialization or round-robin selection order;
- the number of active EVs or the mapping from EVs to physical paths;
- ECN/TRIM classification or congestion-control behavior;
- retransmission, loss/failure handling, probing, or backup promotion;
- all-cooling fallback selection;
- the explicit `cwnd_scaled` cooldown mode.

The first experiment stage contains 18 A2A scenarios:

- topology families: `healthy_alltoall` and
  `asymmetric_alltoall_background`;
- message sizes: 64 MiB, 256 MiB, and 1024 MiB;
- parallelism: P=4, P=8, and P=16;
- seed: 13 only.

Seeds 29 and 47 are deferred until the user reviews the seed-13 result.

## Cooldown Semantics

For default `one_cycle`, an ECN or TRIM feedback event is handled as follows:

1. If the EV is active, set it to cooling and set its deadline to the current
   MRC selection counter plus the current active-EV count.
2. If the EV is already cooling and its deadline has not expired, count the
   feedback as ignored and leave the existing deadline unchanged.
3. When the deadline expires, the EV becomes active through the existing
   activation path.
4. A later congestion signal received after reactivation may start a new
   cooldown.

The ignored-feedback diagnostic must be mode-neutral in meaning and available
for the default `one_cycle` result. Existing result parsers must either retain
compatibility with the old diagnostic field or be updated together with the
source change.

## Implementation Boundaries

The behavior belongs in `RoceSrc::mrc_mark_congested`, where both ECN ACKs and
TRIM NACKs already converge. The implementation should test the EV state before
assigning a new selection-count deadline. No packet-sequence episode tracker,
RTT timer, or additional per-EV state is introduced.

The existing MRC semantics test will gain a regression case that:

- starts a default one-cycle cooldown;
- advances the selection counter without expiring the cooldown;
- delivers a second congestion signal for the same EV;
- verifies that the original deadline is unchanged and the ignored-feedback
  counter increases;
- advances to expiry and verifies that a new post-reactivation signal can start
  another cooldown.

Documentation will describe `one_cycle` as non-rearming instead of rearming.

## Experiment Method

The seed-13 runs must reuse the exact traffic matrices already stored under
`experiments/n-mrc/output/n-mrc-results/traffic`. Each run must use the same
simulator configuration as the archived baseline except for the new MRC
implementation. Results go to a new output directory and must not overwrite the
archived matrix.

Every run must validate:

- process exit status is zero;
- simulator configuration reports MRC with `one_cycle`;
- all expected flows complete exactly once;
- CCT is positive;
- traffic-matrix hash matches the archived baseline for the same scenario and
  seed.

## Seed-13 Report

For every scenario, report the new MRC CCT and its percentage difference
against archived default MRC, OPS, and REPS. Aggregate comparisons use the
geometric mean across the 18 scenarios, while per-scenario rows remain visible
so gains and regressions are not hidden.

Mechanism evidence includes:

- cooldown starts;
- feedback ignored while cooling;
- all-cooling fallback uses;
- ECN and TRIM feedback counts;
- retransmission and completion validation.

After delivering the seed-13 comparison, stop and wait for user approval before
running seeds 29 and 47.
