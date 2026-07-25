# n-MRC Four-Level Reroute with Selective Cooldown

## Objective

Preserve the canonical n-MRC four-level `better_ge3` reroute decision while
preventing small continuous-score differences across a quantization boundary
from cooling an endpoint EV for a full selection cycle.

## Behavior

The source leaf continues to:

1. Quantize every available path as GOOD, DEGRADED, BAD, or AVOID.
2. Use the existing `better_ge3` rule and existing candidate selection to
   reroute the current packet.
3. Send a PATH_REROUTE FastCNP for every successful reroute.

The FastCNP `need_endpoint_cooldown` bit is false by default. After selecting
the actual replacement path, the source leaf compares the continuous scores
used to produce the four-level classifications:

```text
score_gap = original_score - selected_score
need_endpoint_cooldown = score_gap >= 0.25
```

An unavailable, invalid, or non-finite score does not request endpoint
cooldown. The reroute itself is not cancelled by a missing continuous score.
FastCNP remains isolated from DCQCN state and only controls endpoint EV
cooldown through this bit.

## Scope

The change applies only to the canonical `-lb n-mrc` graded decision. Existing
`n-mrc-fixed0.5`, `n-mrc-delta`, MRC, and other load-balancing modes retain
their current behavior.

Diagnostics distinguish:

- reroutes whose FastCNP requests endpoint cooldown;
- reroutes whose FastCNP reports the reroute without requesting cooldown.

## Tests

Unit tests cover:

- a four-level reroute with a continuous gap below 0.25: reroute occurs and
  FastCNP does not request cooldown;
- a gap exactly equal to 0.25: reroute occurs and cooldown is requested;
- a gap above 0.25: reroute occurs and cooldown is requested;
- invalid continuous scores: reroute occurs and cooldown is not requested;
- non-n-MRC presets retain existing behavior.

## Experiment

Run one seed for every P=4 All-to-All cell:

- healthy: 64, 256, and 1024 MiB;
- asymmetric degraded links with periodic background traffic: 64, 256, and
  1024 MiB.

Use the same seed, traffic matrices, transport configuration, and topology as
the archived main matrix. Compare absolute CCT against canonical archived
n-MRC and the established schemes. Also report reroute count, cooldown-request
count, no-cooldown reroute count, cooling skips, all-cooling fallback count,
ECN, and TRIM.

The first decision is whether selective cooldown removes healthy-scenario
regressions without losing the asymmetric-scenario benefit. No additional
seeds or scenarios run until these six results are reviewed.
