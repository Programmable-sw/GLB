# n-MRC Routing and Cooldown Matrix

## Objective

Separate the effect of n-MRC's packet reroute predicate from the effect of
endpoint EV cooldown, then compare the resulting mechanisms on the complete
128-node All-to-All heatmap matrix and healthy/asymmetric WebSearch loads
40%, 60%, 80%, and 100%.

The first pass uses seed 13 only. No additional seeds run before the user
reviews the single-seed results.

## Variants

All graded variants retain encoded EVs, deterministic endpoint RR,
`better_ge3`, FastCNP transport isolation, and no cooldown rearming.

1. `graded_selective`: current workspace behavior. A packet reroutes when at
   least three available candidates have a strictly better four-level grade.
   The original EV receives a full-cycle cooldown only when the selected
   path's valid continuous score is at least 0.25 better.
2. `graded_full`: the same four-level reroute predicate, with a full-cycle
   cooldown after every committed reroute.
3. `graded_none`: the same four-level reroute predicate, with
   `need_endpoint_cooldown=0` after every committed reroute.
4. `graded_delta025`: a candidate qualifies only when its grade is strictly
   better and its valid continuous score improves by at least 0.25. At least
   three such candidates must exist. Every committed reroute requests a
   full-cycle cooldown.
5. `fixed05`: existing `n-mrc-fixed0.5`; original score is at least 0.5,
   selected score is below 0.5, and the score gap is at least 0.25.
6. `delta025`: existing `n-mrc-delta`; the selected path improves the
   continuous score by at least 0.25, without an absolute threshold.

For `graded_delta025`, `better_count` counts paths satisfying both the strict
grade improvement and the delta requirement. Invalid or stale two-hop scores
cannot qualify.

## Simulator Interface

The canonical `-lb n-mrc` preset gains experiment controls:

```text
-nmrc_graded_cooldown selective|full|none
-nmrc_graded_reroute_delta VALUE
```

The cooldown default remains `selective` so the current workspace behavior
does not change. The reroute delta default is zero, which preserves the
current four-level selector. These controls are rejected for noncanonical
n-MRC presets.

## Workloads

Use the archived main-matrix traffic files and seed 13:

- All-to-All: healthy and asymmetric periodic-background scenarios for
  64/256/1024 MiB and P=4/8/16, 18 cells.
- WebSearch: healthy and asymmetric scenarios at 40/60/80/100% offered load,
  8 cells.

This gives 26 cells per variant and 156 simulator runs. All runs use identical
topology, traffic hashes, transport semantics, queue configuration, and seed.

## Metrics and Outputs

All-to-All uses collective CCT. WebSearch uses foreground p99 FCT. Results
include absolute values, ratios to the best variant in each cell, completed
flow counts, reroutes, FastCNPs, cooldown requests/suppressions, cooldown
starts, cooling skips, all-cooling fallbacks, ECN marks, TRIMs, and runtime.

Produce:

- raw commands, compressed logs, and per-run summaries;
- validated `results.csv` and `comparison.csv`;
- an All-to-All heatmap;
- a WebSearch heatmap;
- a concise mechanism comparison separating observed performance from
  interpretation.

Every cell must complete its archived expected flow count and match the
archived traffic SHA-256 before it enters a figure.
