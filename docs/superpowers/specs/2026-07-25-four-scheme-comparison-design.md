# Four-Scheme htsim Comparison Design

## Purpose

Determine whether `avail`, `grade`, `netaware`, or `n-mrc` performs best in
the current `csg-htsim` implementation, and test the pre-registered mechanism
hypothesis that `n-mrc` should have the best overall tail-latency result while
`netaware` may win stable, persistent-asymmetry cases.

## Experiment Matrix

- Simulator revision: the checked-out revision at experiment start.
- Scale: 128 hosts, two-tier Clos, eight physical paths.
- Link and transport configuration: 400 Gbit/s links, 4096-byte MTU,
  `composite_ecn_lb`, `dcqcn_variant`, selective receive, 64-bit SACK,
  `mrc_exact_bounded`, exact TRIM recovery, 0.5 us hop and switch latency.
- Schemes: the current public presets `avail`, `grade`, `netaware`, and
  `n-mrc`. Scheme-specific defaults remain canonical; `netaware` explicitly
  uses `good_share_cap`, and `n-mrc` explicitly uses
  `encoded + better_ge3 + FastCNP on`.
- Seeds: 13, 29, and 47.
- Workloads:
  - healthy permutation, 16 MiB per flow;
  - asymmetric permutation, 16 MiB per flow, with four randomly selected
    source-ToR uplinks slowed by 2x;
  - WebSearch proxy at 80% offered load;
  - global All-to-All, P16, 256 MiB, background off;
  - global All-to-All, P16, 256 MiB, fixed-link background on;
  - global All-to-All, P4, 64 MiB, background off.

Every scheme in a scenario/seed block consumes the same materialized traffic
matrix. The runner records its SHA-256 hash and rejects a block if hashes
differ.

## Metrics And Decision Rule

The primary metric is p99 flow-completion time for point-to-point and
WebSearch workloads, and collective completion time for All-to-All workloads.
Lower is better.

Each raw primary value is divided by the best value in its scenario/seed
block. The overall ranking is the geometric mean of those 18 normalized
ratios. The report also shows:

- separate P2P/WebSearch and All-to-All normalized geometric means;
- per-scheme scenario/seed win counts;
- per-scenario three-seed geometric mean, minimum, maximum, and coefficient
  of variation;
- retransmission ratio, TRIM count, queue CV, and feedback bandwidth as
  explanatory diagnostics.

No scheme is declared universally best merely from the overall score.
Scenario exceptions and seed instability remain visible.

## Runner And Outputs

Create `experiments/n-mrc/run_four_exploration_compare.py` by reusing the
traffic generation, command construction, completion parsing, fingerprinted
cache, and validation conventions of
`run_nmrc_canonical_six_compare.py`.

The runner writes:

- `commands.tsv` with the exact command and traffic hash for every cell;
- `results.csv` with all per-seed metrics and configuration checks;
- `summary.csv` with the three-seed scenario aggregates;
- `ranking.csv` with normalized overall and family scores plus win counts;
- `report.md` with the reproducible textual result;
- raw logs and simulator artifacts under `raw/`.

Analysis and Chinese visualization/report generation occur after the runner
has produced and validated the raw matrix. They must read `results.csv`
rather than scrape prose.

## Validation

Before accepting a cell, require:

- simulator exit code zero;
- all expected flows complete exactly once;
- final inflight bytes are zero and recovery reserve bounds hold;
- the expected load-balancing preset and common transport configuration are
  present in runtime diagnostics;
- `n-mrc` FastCNP generation/arrival and reroute transition invariants hold;
- non-`n-mrc` cells report no n-MRC reroute or cooldown activity.

Before accepting the matrix, require exactly 72 unique
scenario/scheme/seed cells and identical traffic hashes within every
scenario/seed block.

## Scope

This experiment validates the current htsim defaults at one canonical scale
and six representative workloads. It does not prove universal superiority
for other topologies, path counts, load levels, parameter tunings, or real
hardware.
