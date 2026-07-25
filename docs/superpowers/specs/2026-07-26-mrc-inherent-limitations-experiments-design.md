# MRC Inherent Limitations Experiments Design

## Goal

Build a controlled 128-node experiment suite that tests three consequences of
MRC's feedback-driven, per-QP design:

1. short flows may finish before path feedback can affect later selections;
2. independent QPs may repeatedly discover the same poor EV;
3. a larger active EV set may exceed the exploration budget of short flows.

The goal is not to prove MRC fails. The report must identify where MRC helps,
where its benefit disappears, and where delayed or capacity-reducing feedback
can make it slower than its stateless control.

Failure injection is outside this scope. A later 512-node reproduction is also
outside the first implementation; 128 and 512 nodes do not change the tested
mechanisms.

## Controlled Schemes

### RR

RR is the stateless MRC control already validated on 128-node healthy traffic.
It uses the same per-QP deterministic EV permutation, cursor start, encoded
identity EV-to-path mapping, transport, and congestion control as healthy MRC.
It does not update path-quality state from ACK, ECN, NACK, or RTO feedback.

### MRC

MRC is the existing per-QP implementation with ACTIVE, COOLING, FAILED,
PROBING, and backup state. No MRC path state is shared between QPs.

### MRC-shared

MRC-shared differs from MRC only in feedback visibility. Its sharing key is:

```text
(source NIC, destination ToR, EV)
```

When one QP observes an MRC congestion or failure signal, the shared record
publishes the signal type, timestamp, source QP, and monotonically increasing
generation. Other QPs in the same sharing domain consume unseen generations
before their next path selection and apply the existing MRC transition to
their own local EV state:

- ECN or TRIM starts the existing local one-cycle cooldown;
- LOSS or RTO applies the existing failure and backup-promotion transition.

QP congestion-control, transport, sequence, retransmission, cursor, and
completion state remain private. MRC-shared receives no switch snapshot,
fabric oracle, or synthetic feedback. A shared update can only originate from
a real endpoint signal observed by an eligible QP.

This event dissemination preserves selection-count-based one-cycle semantics:
each consumer skips the EV for one of its own active rotations. It avoids
inventing a shared selection counter whose meaning would differ across QPs.

## Common Instrumentation

Each completed QP emits a machine-readable `MrcFlowDiag` line. Required fields:

```text
flow_id src dst dst_tor flow_size start_us finish_us
new_data_selections unique_active_evs full_sweeps
first_full_sweep_us unused_active_evs
quality_feedback_before_done effective_state_updates
first_state_update_us packets_before_first_update
new_selections_after_first_update actionable_feedback
feedback_age_sum_us feedback_age_max_us
cooldown_starts failure_starts forced_cooling_uses
max_simultaneous_cooling replacement_congestion
post_cooldown_first_clean
shared_updates_published shared_updates_consumed
shared_updates_from_other_qps redundant_discoveries
post_shared_bad_ev_sends
```

Definitions:

- `quality_feedback_before_done`: ECN ACK, TRIM NACK, accepted LOSS NACK, or
  attributable RTO received before QP completion.
- `effective_state_updates`: feedback that changes an EV state; duplicate
  signals ignored while cooling do not count.
- `actionable_feedback`: an effective update followed by at least one new data
  selection before completion.
- `feedback_age`: state-update time minus the triggering packet's send time.
- `replacement_congestion`: congestion feedback on an alternative EV while
  at least one original EV remains cooling.
- `post_cooldown_first_clean`: the first feedback after EV reactivation is a
  clean ACK; this is evidence consistent with stale feedback, not proof of the
  counterfactual path condition.
- `redundant_discoveries`: a local signal for an EV after another eligible QP
  already published the same or stronger condition.
- `post_shared_bad_ev_sends`: selections of an EV after another QP published
  its condition but before the local QP consumes/applies it.

The runner validates exactly one diagnostic per completed target flow. Global
diagnostics remain for cross-checking totals.

## Experiment 1: Flow Lifetime and Feedback Value

### Question

How does flow lifetime affect MRC learning, and under what conditions can
feedback reduce performance relative to stateless RR?

### Workloads

Reuse the repository's standard WebSearch CDF without adding artificial flow
sizes. Run 128-node traffic at offered loads 40%, 60%, 80%, and 100%, seeds
13/29/47, under:

- healthy topology, as a no-learning/control family;
- existing asymmetric topology with half-rate sparse uplinks, as a stable
  path-quality family rather than a failure injection.

Every `(family, load, seed)` pair reuses one traffic matrix for RR and MRC.
Flows are reported by their existing exact CDF size and by derived feedback
opportunity:

```text
no update before finish
update but no later new selection
1..active_EV_count later selections
more than one active-EV rotation after update
```

### Primary Evidence

- paired per-flow `FCT_MRC / FCT_RR`;
- p50/p95/p99 FCT by exact flow size;
- fraction of flows with actionable feedback;
- packets sent before first state update;
- new selections remaining after first state update;
- unique-EV coverage and complete-sweep fraction.

### Negative-Feedback Analysis

For the same flow ID and traffic matrix, report the fraction with MRC slowdown
greater than 1%, 5%, and 10%. Slower flows are classified using observed
mechanism evidence:

1. `too_late`: effective update followed by at most one new selection;
2. `stale_consistent`: reactivated EV's first subsequent feedback is clean;
3. `capacity_removal`: at least two EVs cool simultaneously and an alternative
   EV receives congestion feedback;
4. `fallback_pressure`: forced/all-cooling selection occurs;
5. `unexplained`: slowdown without the above evidence.

Categories may overlap; the report includes both individual indicators and an
ordered exclusive classification in the order above. These labels describe
observations and must not be written as counterfactual proof.

### Interpretation

Cold-start limitation is supported when short-flow groups have fewer
actionable updates and less MRC benefit than long-flow groups. Negative
feedback is supported only where paired slowdowns coincide with recorded
state activity; healthy/no-update equality remains a required control.
A workload with no state updates is marked `no learning opportunity`, not used
as evidence for or against MRC learning quality.

## Experiment 2: Repeated Per-QP Exploration

### Question

Does per-QP isolation cause multiple QPs to rediscover the same poor EV, and
does sharing only the original MRC feedback reduce that work?

### Workloads

Reuse existing 128-node asymmetric All-to-All generation:

- total message configuration: 256 MiB;
- parallelism P4, P8, and P16;
- seeds 13/29/47;
- schemes MRC and MRC-shared.

The runner records realized eligible-QP multiplicity for every
`(source NIC, destination ToR)` group. Groups with only one QP remain control
observations and are excluded from cross-QP reuse ratios.

### Primary Evidence

- distinct QPs independently discovering each `(sharing key, EV)`;
- redundant discoveries after the first publication;
- packets sent to the EV after first publication;
- updates published, consumed, and consumed from another QP;
- time from first publication to last redundant discovery;
- loss, retransmission, p99 FCT, and CCT.

The direct repeated-discovery counters are primary. Performance is secondary
because increasing parallelism also changes queue competition.

### Controls

- one-QP MRC and MRC-shared must be identical;
- healthy MRC and MRC-shared must be identical when no update is published;
- each shared signal must map to a real local MRC signal;
- publishing must not mutate DCQCN or transport state of another QP.

## Experiment 3: Active EV Count versus Flow Granularity

### Question

Does a larger active EV set provide useful diversity when a flow lacks enough
packets and feedback rounds to explore it?

### Mechanism

Add:

```text
-mrc_active_evs K
```

with valid range `1..min(path_space, 32)`. Default zero/unset preserves the
current `min(path_space, 32)` behavior.

The physical topology, `-paths 8`, full EV namespace, and identity EV-to-path
mapping remain unchanged. MRC selects the first K EVs from its existing
full-path deterministic permutation as active; remaining EVs are backups. RR
uses the same K-EV prefix and cycles it without quality state, preserving the
stateless-control relationship at each K.

The parameter is named `active_evs`, not `ev_set_size`, because the full
encoded namespace remains present. Claims concern active state/exploration
budget, not physical path count or total namespace allocation.

### Workloads

Reuse Experiment 1 WebSearch traffic matrices and run active EV counts 2, 4,
and 8. The first pass uses asymmetric 40% and 80% loads, seeds 13/29/47. Other
loads are added only if these points fail to provide both low- and
high-feedback regimes.

### Primary Evidence

- `unique_active_evs / active_evs`;
- complete-sweep fraction and first-sweep time;
- unused active EV count at completion;
- feedback events per active EV;
- paired MRC/RR FCT by flow size and K;
- active state count divided by used state count.

Mismatch is supported when larger K lowers short-flow coverage, increases
unused active state, and does not consistently improve paired FCT. The design
does not assume that more EVs must make performance worse.

## Aggregation and Statistics

- Seeds are exactly 13, 29, and 47.
- Every scheme comparison reuses byte-identical traffic matrices.
- Per-flow comparisons join on `(scenario, seed, flow_id, src, dst, size)`.
- Absolute latency reports p50/p95/p99 and maximum.
- Ratios are computed within each paired block before aggregation.
- Three-seed summaries report geometric mean plus per-seed min/max; raw
  per-seed values remain visible.
- A result is never called significant from three seeds alone. The report uses
  `consistent across seeds`, `mixed`, or `inconclusive`.

## Figures

1. Experiment 1 flow-size × offered-load heatmap of paired MRC/RR FCT ratio.
2. Experiment 1 actionable-feedback and EV-coverage curves by flow size.
3. Experiment 1 slowdown fraction and observed mechanism classification.
4. Experiment 1 remaining-packet fraction versus paired slowdown.
5. Experiment 2 repeated-discovery metrics versus P4/P8/P16.
6. Experiment 2 MRC/MRC-shared retransmission, p99 FCT, and CCT.
7. Experiment 3 flow-size × active-EV heatmaps for coverage and FCT ratio.
8. Experiment 3 unused-state and complete-sweep curves.

Figures show per-seed points or ranges and never hide missing/incomplete cells.
FCT curves alone are insufficient: every performance figure is paired with a
mechanism figure or table.

## Runner and Output Contract

One runner owns traffic materialization, simulator invocation, resume
fingerprints, parsing, validation, CSV output, plots, and Markdown reporting.
Results live under a new output directory and never overwrite retained data.

Required artifacts:

```text
manifest.json
summary.csv
flow_metrics.csv
shared_key_metrics.csv
figures/*.pdf
figures/*.png
mrc_inherent_limitations_report.md
raw/<experiment>/<scenario>/<scheme>/seed_<seed>/
```

In addition, create and track:

```text
experiments/n-mrc/mrc_inherent_limitations_and_evidence.md
```

This is the durable user-facing explanation. Its structure follows
`experiments/n-mrc/four_scheme_risks_and_evidence.md`:

1. conclusion first, with a scenario/applicability table;
2. explicit evidence grades (`causal`, `overall support`, `mechanism support`,
   `not established`);
3. one section per experiment containing mechanism, confirmed limitation,
   counterexamples, and applicability boundary;
4. a direct RR/MRC/MRC-shared comparison table;
5. current limitations and the most valuable follow-up experiments;
6. data sources, reproduction scope, CSV/figure links, and caveats.

The file must embed or link the final figures using paths relative to
`experiments/n-mrc/`. It must distinguish observed association from causal
isolation and must not copy raw runner prose without interpretation.

Every case validates:

- simulator and full command fingerprint;
- traffic SHA-256;
- zero process exit status;
- expected flow IDs complete exactly once;
- one `MrcFlowDiag` per target flow;
- positive FCT/CCT;
- canonical path count and active-EV configuration;
- required MRC diagnostics;
- no missing matrix cells before aggregate claims.

Failed or incomplete cells remain in the manifest and are excluded from
aggregates with an explicit reason.

## Testing

Unit tests cover:

- RR/MRC equality for every active-EV setting before feedback;
- default active-EV behavior remains unchanged;
- invalid active-EV values are rejected;
- MRC-shared one-QP and healthy equivalence;
- sharing-domain isolation;
- shared ECN/TRIM/failure propagation without CC/transport mutation;
- no duplicate generation consumption;
- per-flow diagnostic counter transitions.

Runner tests cover command construction, traffic reuse, parser completeness,
pairing, classification, aggregation, missing-cell rejection, and a tiny
end-to-end smoke matrix.

## Final Claims

The final report must distinguish:

- where feedback arrives early enough and MRC improves path choice;
- where flows finish before feedback becomes actionable;
- where observed feedback activity correlates with slowdown;
- where per-QP isolation causes repeated discovery;
- where larger active sets exceed a flow's exploration budget;
- where results are mixed or signals are absent.

The intended synthesis is conditional, not absolute: MRC is designed for
long-lived AI-training communication, while its feedback-driven per-QP state
can lose value for short flows, repeated independent QPs, and active-state
sizes larger than the available exploration budget.
