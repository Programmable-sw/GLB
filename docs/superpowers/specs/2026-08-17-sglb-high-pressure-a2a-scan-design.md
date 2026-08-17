# SGLB High-Pressure A2A Min-Choice Scan Design

## Objective

Replace the low-pressure two-A2A quick scan with one canonical high-pressure
scenario that can actually exercise the SGLB candidate lower bound.  Compare
all retained `min_choices` values (`16, 20, 24, 28, 32, 36, 40`) using CCT as
the primary metric.

## Scenario

- Canonical 256-host, 4-Leaf, 64-Spine topology with 64 physical paths.
- Current `sglb` implementation and fixed production defaults.
- Global All-to-All with per-source parallelism 16.
- Full 256 MiB message volume per source, represented by 65,280 ordered flows
  of 1 MiB each.
- Seed 13 for one focused seven-cell candidate comparison.
- Historical periodic link background: 350 Gbit/s, two links per direction,
  400 us ON and 400 us OFF.
- Simulation end time 100,000 us.

Only `-sglb_min_choices` changes between the seven cells.  Traffic is
materialized once and reused byte-for-byte.

## Metrics and Interpretation

The runner records CCT; mean, p50, p95, p99, p99.9, and maximum completion
time; retransmissions, RTOs, TRIMs, and ECN marks; queue CV and queue
percentiles; observed SGLB candidate counts and selection fractions; and GCN
delivery, freshness, and remote-profile diagnostics.

The primary ordering is lower CCT.  Exact CCT ties are broken by lower p99,
then fewer TRIMs, retransmissions, and ECN marks.  Exact ties across all
tie-break fields remain explicitly tied rather than selecting the numerically
smallest candidate.

The report must state whether `avg_candidate_choices` enters the tested
16--40 interval.  If it stays above 40, the run is not evidence for an optimal
minimum even if all cells complete.

## Runtime and Failure Policy

Run at most three cells concurrently.  First run one `min24` timing gate.  If
that single cell exceeds 15 minutes, stop and report the observed runtime;
do not silently reduce traffic.  Otherwise run the remaining six cells.

A cell is valid only when all 65,280 flows complete, the runtime SGLB
configuration matches the requested value, GCN traffic is nonzero, stale GCN
packets are zero, and remote-profile misses are zero.  Invalid cells are
excluded and cause a nonzero runner exit.

Each cell retains compressed raw stdout, exact command, simulator/traffic
fingerprints, runtime, and parsed JSON.  Aggregate outputs include the
manifest, cells, complete rankings, summary, and Markdown report.

## Replacement and Deletion Scope

Remove the superseded focused experiment artifacts:

- `experiments/n-mrc/run_sglb_min_choices_two_a2a_256.py`;
- `sim/tests/test_sglb_min_choices_two_a2a_256_runner.py`;
- `experiments/n-mrc/output/sglb_min_choices_two_a2a_256_quick05/`.

The output directory is moved to the system trash when available so the raw
data remains recoverable outside the workspace.  The two tracked scripts also
remain recoverable from Git history.

The earlier four-workload scan runner and its partial/full logs are outside
this deletion scope and remain untouched.

## Deliverables

- `experiments/n-mrc/run_sglb_min_choices_pressure_a2a_256.py`;
- a focused contract test under `sim/tests/`;
- `experiments/n-mrc/output/sglb_min_choices_pressure_a2a_256/` containing
  the traceable raw and aggregate evidence.
