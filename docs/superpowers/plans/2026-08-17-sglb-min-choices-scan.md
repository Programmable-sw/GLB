# SGLB Min-Choices Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a reproducible 84-cell scan that selects the CCT-optimal SGLB `min_choices` value subject to 1% healthy P2P and WebSearch guardrails, while retaining complete metric rankings.

**Architecture:** Add one focused Python runner that owns traffic generation, command construction, cell validation, caching, aggregation, ranking, Pareto analysis, and report generation. Reuse the repository's existing traffic and metric helpers, but do not depend on deleted historical runners or raw output directories. Add a fast runner contract test, then run one real gate cell before the complete matrix.

**Tech Stack:** Python 3 standard library, existing `feedback_eval_common.py` and `experiment_metrics.py`, htsim RoCE simulator, CSV/JSON/Markdown outputs, unittest-style executable Python tests.

## Global Constraints

- Scan exactly `min_choices = 16,20,24,28,32,36,40` on 64 paths.
- Use exactly seeds `13,29,47` for the formal matrix.
- Change only `-sglb_min_choices`; retain current `-lb sglb` semantics.
- Primary winner: lowest three-seed geometric-mean high-pressure A2A CCT.
- Eligibility: healthy P2P p99 and healthy WebSearch p99 ratios versus paired min24 are both `<= 1.01`.
- Preserve complete per-cell metrics, per-metric rankings, and Pareto results.
- Do not stage or modify the user's unrelated deleted experiment files.

---

### Task 1: Runner Contract and Matrix Tests

**Files:**
- Create: `sim/tests/test_sglb_min_choices_64path_scan_256_runner.py`
- Test target: `experiments/n-mrc/run_sglb_min_choices_64path_scan_256.py`

**Interfaces:**
- Consumes: runner constants `MIN_CHOICES`, `SEEDS`, `WORKLOADS`; functions `parse_args(argv)`, `make_specs(args)`, `validate_specs(specs)`, `summarize(rows)`, and `rank_candidates(summary)`.
- Produces: executable contract test used by Task 2 and the experiment gate.

- [ ] **Step 1: Write the failing matrix test**

Create a test that imports the runner by absolute module path, constructs arguments with a temporary output directory, calls `make_specs`, and asserts:

```python
assert runner.MIN_CHOICES == (16, 20, 24, 28, 32, 36, 40)
assert runner.SEEDS == (13, 29, 47)
assert len(runner.WORKLOADS) == 4
assert len(specs) == 84
assert {spec.min_choices for spec in specs} == set(runner.MIN_CHOICES)
assert {spec.seed for spec in specs} == set(runner.SEEDS)
assert {spec.workload for spec in specs} == set(runner.WORKLOADS)
```

For every `(workload, seed)` block, assert all seven specs share one traffic
SHA-256 and that command differences, after removing `-o` and
`-sglb_min_choices` values, are empty.

- [ ] **Step 2: Write the failing ranking test**

Use synthetic rows where min20 has the best A2A CCT but a 1.02 WebSearch
ratio, min28 has the next-best CCT and both guardrails at 1.005, and min24 is
the baseline. Assert min20 is ineligible, min28 is selected, all seven
candidates receive every metric rank, and the Pareto result records the
dominating candidate IDs.

- [ ] **Step 3: Run the test and verify RED**

Run:

```bash
python3 sim/tests/test_sglb_min_choices_64path_scan_256_runner.py
```

Expected: failure because
`experiments/n-mrc/run_sglb_min_choices_64path_scan_256.py` does not exist.

- [ ] **Step 4: Commit the failing test**

```bash
git add sim/tests/test_sglb_min_choices_64path_scan_256_runner.py
git commit -m "test: define SGLB min-choice scan contract"
```

### Task 2: Focused Scan Runner

**Files:**
- Create: `experiments/n-mrc/run_sglb_min_choices_64path_scan_256.py`
- Test: `sim/tests/test_sglb_min_choices_64path_scan_256_runner.py`

**Interfaces:**
- Produces immutable `CellSpec` records with fields `workload`, `seed`, `min_choices`, `traffic_file`, `traffic_sha256`, `expected_flows`, `case_dir`, and `command`.
- Produces `cells.csv`, `seed_ratios.csv`, `summary.csv`, `rankings.csv`, `pareto.csv`, `commands.tsv`, `manifest.json`, and `report.md`.
- Supports `--dry-run`, `--gate`, `--workers`, `--timeout`, `--force`, `--seeds`, `--min-choices`, and `--workloads`.

- [ ] **Step 1: Implement constants, data classes, and deterministic traffic generation**

Define:

```python
MIN_CHOICES = (16, 20, 24, 28, 32, 36, 40)
SEEDS = (13, 29, 47)
NODES = 256
PATHS = 64
WORKLOADS = (
    "healthy_permutation_16mib",
    "healthy_websearch_100",
    "fixed_hotspot_permutation_16mib",
    "periodic_background_a2a_p16_256mib",
)

@dataclass(frozen=True)
class CellSpec:
    workload: str
    seed: int
    min_choices: int
    traffic_file: Path
    traffic_sha256: str
    expected_flows: int
    case_dir: Path
    command: tuple[str, ...]
```

Generate P2P permutation traffic with
`common.permutation_flows(topology, seed, 16 * common.MIB, 0)`, WebSearch with
`common.websearch_flows(topology, seed, 1.0, 10000)`, and A2A with
`common.alltoall_plan(256, 256, 16, 256 * common.MIB // 256, 0, seed)`. Reuse
the permutation matrix byte-for-byte for the healthy and fixed-hotspot cells.

- [ ] **Step 2: Implement exact command construction and validation**

Build every command with the fixed flags from the design, plus:

```text
-lb sglb
-paths 64
-sglb_min_choices <candidate>
-sglb_update_us 1
-sglb_gcn_update_us 15
-sglb_gcn_aging_us 30
```

For fixed hotspot add 16 paths at 390 Gbit/s with a persistent ON phase. For
high-pressure A2A use the canonical periodic background arguments returned by
`common.alltoall_background_args(256 * common.MIB, True)`. `validate_specs`
must require 84 cells for default arguments, one traffic hash per
`(workload, seed)`, all seven candidates per block, and exact equality for all
non-output/non-min-choice command fields.

- [ ] **Step 3: Implement fingerprinted execution and strict cell parsing**

Fingerprint each cell as SHA-256 of simulator SHA, traffic SHA, and the exact
command. Store `command.txt`, `fingerprint.txt`, `returncode.txt`,
`runtime.txt`, and deterministic `stdout.log.gz`. Reuse a cached cell only
when all fingerprints match and reparsing passes.

Parse flow completions and compute CCT and interpolated FCT percentiles. Parse
RoCE, queue, queue-CV, SGLB route, and GCN diagnostic lines with
`experiment_metrics.parse_key_values`. Reject a cell unless return code is
zero, all expected flows complete, the effective config reports current SGLB,
the requested min-choice value, real-GCN/raw-linear mode, 1/15/30 us timing,
nonzero GCN packets, `gcn_bytes == 256 * gcn_packets`, zero stale packets, and
zero missing remote snapshots.

- [ ] **Step 4: Implement aggregation, eligibility, rankings, and Pareto output**

Pair every candidate to min24 within `(workload, seed)`. Use geometric means
for ratios and for the three-seed primary CCT. Mark eligibility from the two
healthy p99 ratios. Sort eligible candidates by A2A CCT and apply the specified
0.1% tie breakers. Emit long-form rankings with columns
`metric,candidate,value,rank,eligible` and Pareto rows with
`candidate,is_pareto,dominated_by`.

- [ ] **Step 5: Implement manifest and answer-first report**

The manifest must include source revision, simulator SHA, traffic hashes,
fixed semantics, candidate list, seeds, workload definitions, completion
count, and selected winner. The report must lead with the eligible winner,
show guardrail ratios, full CCT/FCT/recovery/queue rankings, observed candidate
fractions, Pareto membership, and all interpretation limits from the design.

- [ ] **Step 6: Run the runner contract and verify GREEN**

Run:

```bash
python3 sim/tests/test_sglb_min_choices_64path_scan_256_runner.py
python3 experiments/n-mrc/run_sglb_min_choices_64path_scan_256.py --dry-run
```

Expected: contract passes; dry-run reports `validated 84 cells` and writes an
84-row `commands.tsv` without invoking the simulator.

- [ ] **Step 7: Run related regression tests**

Run:

```bash
python3 sim/tests/test_sglb_final_defaults.py
python3 sim/tests/test_mrc_sglb_steady_mixed_256_runner.py
```

Expected: both pass with no changes to SGLB behavior.

- [ ] **Step 8: Commit the runner**

```bash
git add experiments/n-mrc/run_sglb_min_choices_64path_scan_256.py \
  sim/tests/test_sglb_min_choices_64path_scan_256_runner.py
git commit -m "exp: add 64-path SGLB min-choice scan"
```

### Task 3: Gate Cell and Full Scan

**Files:**
- Generate: `experiments/n-mrc/output/sglb_min_choices_64path_scan_256/`

**Interfaces:**
- Consumes: validated runner and current simulator binary.
- Produces: validated gate evidence followed by the formal 84-cell dataset.

- [ ] **Step 1: Build the simulator and record its fingerprint**

Run:

```bash
make -C sim/datacenter -j2 htsim_roce
sha256sum sim/datacenter/htsim_roce
```

Expected: build succeeds and the SHA is recorded in the manifest.

- [ ] **Step 2: Run the real min24 gate**

Run:

```bash
python3 experiments/n-mrc/run_sglb_min_choices_64path_scan_256.py \
  --gate --workers 1
```

Expected: one seed13/min24 healthy-P2P cell completes 256/256 flows, emits
nonzero real GCN packets, has zero stale packets and zero missing remote
profiles, and passes all configuration checks.

- [ ] **Step 3: Launch the complete matrix**

Run:

```bash
python3 experiments/n-mrc/run_sglb_min_choices_64path_scan_256.py \
  --workers 3 --timeout 7200
```

Expected: all 84 cells validate. The runner reuses the gate cell because its
fingerprint matches.

- [ ] **Step 4: Verify artifact completeness**

Run a read-only verification that asserts 84 unique
`(workload,seed,min_choices)` keys, 28 summary blocks before aggregation, seven
candidate summary rows, complete per-metric rankings, one manifest winner,
and no invalid cells. Recompute traffic and command fingerprints from files.

### Task 4: Final Evidence Review

**Files:**
- Review: `experiments/n-mrc/output/sglb_min_choices_64path_scan_256/report.md`
- Review: all CSV/JSON artifacts from Task 3

**Interfaces:**
- Consumes: complete validated scan.
- Produces: user-facing conclusion with the winning floor, observed candidate fraction, uncertainty, guardrail status, and complete metric ranking links.

- [ ] **Step 1: Check ranking consistency manually**

Confirm the report winner is eligible, has the lowest eligible A2A CCT under
the tie rule, and agrees with `summary.csv`, `rankings.csv`, and `pareto.csv`.
Confirm min24 ratios are exactly 1 within floating-point tolerance.

- [ ] **Step 2: Check seed stability**

Report whether the aggregate winner also wins each individual seed. If not,
show all three seed ranks and avoid presenting the aggregate minimum as a
universal constant.

- [ ] **Step 3: Deliver the result**

State the winning min-choice floor and nominal percentage, observed average
candidate percentage in each workload, A2A CCT improvement versus min24,
healthy guardrail ratios, tail/recovery trade-offs, Pareto set, and evidence
limitations. Link the manifest, report, summary, and complete rankings.
