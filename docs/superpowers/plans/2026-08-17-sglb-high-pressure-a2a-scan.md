# SGLB High-Pressure A2A Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the low-pressure two-A2A experiment with a traceable seven-candidate SGLB scan under full-volume periodic-background A2A pressure.

**Architecture:** A focused Python runner reuses the retained traffic generators and parsing helpers, materializes one seed-13 A2A matrix, and changes only `-sglb_min_choices` across seven cells. It runs a min24 timing/validity gate before the remaining cells, caches validated raw evidence, and emits complete tied-aware rankings.

**Tech Stack:** Python 3 standard library, existing `feedback_eval_common.py` and `experiment_metrics.py`, `htsim_roce`, Git, deterministic gzip/CSV/JSON/Markdown artifacts.

## Global Constraints

- Use the canonical 256-host, 4-Leaf, 64-Spine topology and 64 paths.
- Use global All-to-All, p16, seed 13, and full 256 MiB per source (65,280 ordered 1 MiB flows).
- Enable the historical periodic background at 350 Gbit/s, two links per direction, 400 us ON/OFF.
- Scan exactly `16,20,24,28,32,36,40`; only `-sglb_min_choices` may vary between cells.
- Stop after the min24 gate if its runtime exceeds 15 minutes; never silently reduce traffic.
- Preserve compressed stdout and fingerprints for every attempted cell.
- Keep exact ties tied; do not manufacture a winner by min-choice number.
- Delete only the superseded two-A2A runner, its contract test, and its `quick05` output directory.

---

### Task 1: Remove Superseded Two-A2A Experiment

**Files:**
- Delete: `experiments/n-mrc/run_sglb_min_choices_two_a2a_256.py`
- Delete: `sim/tests/test_sglb_min_choices_two_a2a_256_runner.py`
- Trash: `experiments/n-mrc/output/sglb_min_choices_two_a2a_256_quick05/`

**Interfaces:**
- Consumes: exact deletion scope approved in the design.
- Produces: a workspace without the superseded runner or visible raw directory.

- [ ] **Step 1: Verify exact targets and absence of running owners**

Run:
```bash
ps -eo pid,cmd | rg 'run_sglb_min_choices_two_a2a_256|htsim_roce.*two_a2a_256' || true
git status --short -- experiments/n-mrc/run_sglb_min_choices_two_a2a_256.py sim/tests/test_sglb_min_choices_two_a2a_256_runner.py
du -sh experiments/n-mrc/output/sglb_min_choices_two_a2a_256_quick05
```
Expected: no running process; the two tracked files exist; the output resolves to one exact directory.

- [ ] **Step 2: Delete the tracked files with `apply_patch` and trash the data directory**

Use `apply_patch` with `Delete File` for both tracked files. Then run:
```bash
gio trash experiments/n-mrc/output/sglb_min_choices_two_a2a_256_quick05
```
Expected: all three paths are absent; the data directory is recoverable from the system trash.

- [ ] **Step 3: Commit the scoped removal**

```bash
git add -u experiments/n-mrc/run_sglb_min_choices_two_a2a_256.py sim/tests/test_sglb_min_choices_two_a2a_256_runner.py
git commit -m "chore: remove low-pressure two-A2A scan"
```

### Task 2: Define the High-Pressure Runner Contract

**Files:**
- Create: `sim/tests/test_sglb_min_choices_pressure_a2a_256_runner.py`

**Interfaces:**
- Consumes: `parse_args(argv)`, `make_specs(args)`, and `summarize(rows)` from the future runner.
- Produces: a contract requiring seven seed-13 cells, one traffic hash, periodic background flags, full 1 MiB flows, and tied-aware ranking.

- [ ] **Step 1: Write the failing contract test**

The test must load `experiments/n-mrc/run_sglb_min_choices_pressure_a2a_256.py`, create dry-run specs, and assert:
```python
assert runner.MIN_CHOICES == (16, 20, 24, 28, 32, 36, 40)
assert len(specs) == 7
assert {spec.seed for spec in specs} == {13}
assert {spec.connections for spec in specs} == {65280}
assert len({spec.traffic_sha256 for spec in specs}) == 1
assert all("-sglb_background" in spec.command for spec in specs)
assert all(spec.command[spec.command.index("-sglb_bg_rate_gbps") + 1] == "350" for spec in specs)
assert all(spec.command[spec.command.index("-sglb_bg_on_us") + 1] == "400" for spec in specs)
```
It must also feed identical synthetic metrics for all candidates into `summarize` and assert no row is selected and every CCT rank is 1.

- [ ] **Step 2: Run the test and confirm the intended failure**

Run:
```bash
python3 sim/tests/test_sglb_min_choices_pressure_a2a_256_runner.py
```
Expected: `FileNotFoundError` for the not-yet-created runner.

- [ ] **Step 3: Commit the red test**

```bash
git add sim/tests/test_sglb_min_choices_pressure_a2a_256_runner.py
git commit -m "test: define high-pressure SGLB A2A scan contract"
```

### Task 3: Implement the Focused Runner

**Files:**
- Create: `experiments/n-mrc/run_sglb_min_choices_pressure_a2a_256.py`
- Test: `sim/tests/test_sglb_min_choices_pressure_a2a_256_runner.py`

**Interfaces:**
- Consumes: `feedback_eval_common.alltoall_plan`, `write_alltoall_matrix`, and `alltoall_background_args`; parsing primitives from `experiment_metrics.py`.
- Produces: `parse_args`, `make_specs`, `validate_specs`, `run_cell`, `summarize`, `rank_candidates`, and `main`.

- [ ] **Step 1: Implement deterministic traffic and commands**

Create one plan with:
```python
plan = common.alltoall_plan(
    256, 256, 16, 256 * common.MIB // 256, 0, 13)
```
Append `common.alltoall_background_args(256 * common.MIB, True)`, which must resolve to two links per direction, 350 Gbit/s, and 400 us ON/OFF. Use the fixed current SGLB transport/configuration flags and `-end 100000`.

- [ ] **Step 2: Implement validation, caching, and metrics**

Require all 65,280 completions, matching effective SGLB config, nonzero `gcn_packets`, `gcn_stale == 0`, and `remote_snapshot_missing == 0`. Fingerprint simulator SHA-256, traffic SHA-256, and the exact command. Store deterministic `stdout.log.gz`, `parsed.json`, `command.txt`, `fingerprint.txt`, `returncode.txt`, and `runtime.txt`; remove only reconstructable `logout.dat` after successful parsing.

- [ ] **Step 3: Implement tied-aware aggregation and artifacts**

Rank lower values first for CCT; mean/p95/p99/max completion; retransmissions, RTOs, TRIMs, and ECN; queue p99/CV; average candidates; non-best and AVOID fractions. Assign the same rank to exact equal values. Select a candidate only when its `(CCT, p99, trims, retransmissions, ECN)` tuple is uniquely best. Write `commands.tsv`, `manifest.json`, `cells.csv`, `summary.csv`, `rankings.csv`, and `report.md`.

- [ ] **Step 4: Run focused and regression tests**

```bash
python3 sim/tests/test_sglb_min_choices_pressure_a2a_256_runner.py
python3 sim/tests/test_sglb_min_choices_64path_scan_256_runner.py
python3 sim/tests/test_sglb_final_defaults.py
python3 sim/tests/test_mrc_sglb_steady_mixed_256_runner.py
python3 -m py_compile experiments/n-mrc/run_sglb_min_choices_pressure_a2a_256.py
git diff --check
```
Expected: every command exits 0 with no test output or syntax/diff errors.

- [ ] **Step 5: Commit the runner**

```bash
git add experiments/n-mrc/run_sglb_min_choices_pressure_a2a_256.py
git commit -m "feat: add high-pressure SGLB A2A scan"
```

### Task 4: Run the Min24 Timing Gate

**Files:**
- Create: `experiments/n-mrc/output/sglb_min_choices_pressure_a2a_256/`

**Interfaces:**
- Consumes: runner `--gate` mode selecting seed13/min24.
- Produces: one validated timing measurement and raw audit trail.

- [ ] **Step 1: Build and run the gate**

```bash
make -C sim/datacenter -j4 htsim_roce
python3 experiments/n-mrc/run_sglb_min_choices_pressure_a2a_256.py --gate --workers 1
```
Expected: 65,280/65,280 flows, valid diagnostics, and `gate passed`.

- [ ] **Step 2: Enforce the runtime stop condition**

Read `runs/seed13_min24/runtime.txt`. If the value exceeds 900 seconds, stop without launching the other cells and report the observed runtime. Otherwise continue.

### Task 5: Execute and Validate the Seven-Cell Scan

**Files:**
- Populate: `experiments/n-mrc/output/sglb_min_choices_pressure_a2a_256/`

**Interfaces:**
- Consumes: the validated gate cache and six remaining candidates.
- Produces: final traceable evidence and the answer about whether the minimum became active.

- [ ] **Step 1: Run the full matrix with bounded concurrency**

```bash
python3 experiments/n-mrc/run_sglb_min_choices_pressure_a2a_256.py --workers 3
```
Expected: min24 is reused from cache and all seven cells validate.

- [ ] **Step 2: Verify evidence independently**

Use a Python check over CSV/JSON files to assert: seven unique min choices; every cell has 65,280/65,280 completions; all configs pass; all GCN stale/missing values are zero; seven compressed logs and parsed JSON files exist; traffic hashes are identical; rankings contain every requested metric and candidate; the report's selection statement matches `summary.csv`.

- [ ] **Step 3: Run final regressions and inspect repository boundaries**

```bash
python3 sim/tests/test_sglb_min_choices_pressure_a2a_256_runner.py
python3 sim/tests/test_sglb_min_choices_64path_scan_256_runner.py
python3 sim/tests/test_sglb_final_defaults.py
python3 sim/tests/test_mrc_sglb_steady_mixed_256_runner.py
git diff --check
git status --short
```
Expected: tests pass; only previously existing unrelated user changes remain unstaged.

- [ ] **Step 4: Report the decision with limitations**

State the winner or explicit tie, complete CCT/p99/recovery/queue ranks, observed average candidate count, whether the 16--40 floor became active, runtime, output paths, raw-log count, and the fact that this is a one-seed focused result rather than a universal optimum.
