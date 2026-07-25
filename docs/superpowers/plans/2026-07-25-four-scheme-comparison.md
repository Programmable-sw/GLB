# Four-Scheme htsim Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a validated 72-cell comparison of the current `avail`, `grade`, `netaware`, and `n-mrc` presets and a reproducible evidence report.

**Architecture:** Add a narrow runner that configures and reuses the existing canonical-six traffic, execution, parsing, caching, and validation functions. Add only the missing four-scheme configuration checks and result summaries, then run the unchanged simulator and analyze its validated CSV output.

**Tech Stack:** Python 3 standard library, existing htsim runner helpers, C++ htsim binary, CSV, Matplotlib, portable Data Analytics HTML report builder.

## Global Constraints

- Use 128 nodes, eight paths, seeds 13/29/47, and the six canonical workloads.
- Keep the common transport and topology command line identical across schemes.
- Use current public presets and canonical scheme-specific defaults.
- Reject incomplete, duplicated, misconfigured, or cross-scheme traffic-mismatched cells.
- Do not use the old single-seed communication preview as current proof.

---

### Task 1: Four-Scheme Runner Contract

**Files:**
- Create: `sim/tests/test_four_exploration_compare_runner.py`
- Test: `sim/tests/test_four_exploration_compare_runner.py`

**Interfaces:**
- Consumes: `experiments/n-mrc/run_four_exploration_compare.py`
- Produces: executable contract checks for `SCHEMES`, `make_specs`,
  `validate_specs`, `config_ok`, `aggregate`, and
  `summarize_three_seed_cells`

- [ ] **Step 1: Write the failing runner contract test**

The test imports the new runner, asserts:

```python
assert runner.SCHEMES == ("avail", "grade", "netaware", "n-mrc")
assert len(runner.make_specs(args)) == 72
```

It then checks `-lb`, canonical NetAware/N-MRC arguments, shared traffic hashes,
the four runtime diagnostic signatures, 72-cell completeness rejection, and
normalized aggregation with one synthetic winner per block.

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python3 sim/tests/test_four_exploration_compare_runner.py
```

Expected: failure because
`experiments/n-mrc/run_four_exploration_compare.py` does not exist.

- [ ] **Step 3: Commit the failing test**

```bash
git add sim/tests/test_four_exploration_compare_runner.py
git commit -m "test: define four-scheme comparison contract"
```

### Task 2: Implement The Reusable Runner

**Files:**
- Create: `experiments/n-mrc/run_four_exploration_compare.py`
- Test: `sim/tests/test_four_exploration_compare_runner.py`

**Interfaces:**
- Consumes: canonical-six functions for traffic materialization, command
  construction, cached execution, parsing, and base invariants
- Produces: `commands.tsv`, `results.csv`, `summary.csv`, `ranking.csv`,
  `report.md`, and `raw/`

- [ ] **Step 1: Configure the four public presets**

Define:

```python
SCHEMES = ("avail", "grade", "netaware", "n-mrc")
LB_NAMES = {
    "avail": "avail",
    "grade": "grade",
    "netaware": "netaware",
    "n-mrc": "n-mrc",
}
SCHEME_ARGS = {
    "avail": (),
    "grade": (),
    "netaware": ("-netaware_weight_adaptation", "good_share_cap"),
    "n-mrc": (
        "-nmrc_ev_mode", "encoded",
        "-nmrc_reroute_policy", "better_ge3",
        "-nmrc_fastcnp", "on",
    ),
}
```

- [ ] **Step 2: Add scheme-aware runtime validation**

Require the common exact-bounded diagnostic fields plus:

```python
scheme_tokens = {
    "avail": ("stor profile binary",),
    "grade": ("stor profile graded",),
    "netaware": ("weight_adaptation good_share_cap", "NetawareDiag "),
    "n-mrc": (
        "HybridNmrcConfig ev_mode=encoded ",
        "reroute_policy=better_ge3 fastcnp=on",
        "HybridNmrcDiag",
    ),
}
```

Use the actual runtime strings discovered from a dry one-cell run if the
public preset banner differs, and update the test to match that authoritative
output.

- [ ] **Step 3: Add four-scheme aggregation and output**

Generate normalized geometric means and win counts from the 72 validated raw
cells. Generate three-seed scenario summaries with geometric mean, min, max,
and coefficient of variation. Write all output tables before the textual
report.

- [ ] **Step 4: Run the contract test**

Run:

```bash
python3 sim/tests/test_four_exploration_compare_runner.py
```

Expected: PASS.

- [ ] **Step 5: Validate all commands without simulation**

Run:

```bash
python3 experiments/n-mrc/run_four_exploration_compare.py \
  --dry-run \
  --out experiments/n-mrc/output/four_exploration_128_p8_3seed_20260725
```

Expected: `validated 72 commands`.

- [ ] **Step 6: Commit the runner**

```bash
git add experiments/n-mrc/run_four_exploration_compare.py \
  sim/tests/test_four_exploration_compare_runner.py
git commit -m "feat: add four-scheme htsim comparison"
```

### Task 3: Execute, Analyze, And Deliver Evidence

**Files:**
- Create: `experiments/n-mrc/output/four_exploration_128_p8_3seed_20260725/`
- Create: `reports/four-exploration-comparison-2026-07-25/artifact.json`
- Create: `reports/four-exploration-comparison-2026-07-25/report.html`
- Create: `reports/four-exploration-comparison-2026-07-25/analysis.csv`

**Interfaces:**
- Consumes: validated runner and current `sim/datacenter/htsim_roce`
- Produces: raw proof, QA tables, charts, and Chinese reader-facing report

- [ ] **Step 1: Rebuild the simulator**

Run:

```bash
make -C sim -j2
make -C sim/datacenter htsim_roce -j2
```

Expected: both commands exit zero and the binary timestamp is current.

- [ ] **Step 2: Execute the 72-cell matrix**

Run:

```bash
python3 experiments/n-mrc/run_four_exploration_compare.py \
  --workers 2 \
  --timeout 3600 \
  --out experiments/n-mrc/output/four_exploration_128_p8_3seed_20260725
```

Expected: all 72 cells complete with no invalid-run exception.

- [ ] **Step 3: Recompute the decision metrics independently**

Read `results.csv`, verify exactly 72 unique cells, identical traffic hashes
within each scenario/seed block, positive finite primary metrics, and all
completion/configuration gates. Recompute normalized ratios, geometric means,
win counts, and seed coefficients of variation into `analysis.csv`; compare
them with `ranking.csv` and `summary.csv`.

- [ ] **Step 4: Build the Chinese report artifact**

Use a horizontal bar chart for overall normalized score and a grouped
scenario comparison chart or heatmap for per-scenario ratios. Keep exact
values in a supporting table. State the pre-registered hypothesis, observed
exceptions, mechanism interpretation, and scope caveats.

- [ ] **Step 5: Package and verify the HTML**

Run the Data Analytics report builder:

```bash
npm run report:deliver -- \
  --input /home/user/workspace/csg-htsim/reports/four-exploration-comparison-2026-07-25/artifact.json \
  --output /home/user/workspace/csg-htsim/reports/four-exploration-comparison-2026-07-25/report.html
```

Expected: validation succeeds and verification is `passed` or
`structural_only`; report the latter as a QA limitation.

- [ ] **Step 6: Run final verification**

Run:

```bash
python3 sim/tests/test_four_exploration_compare_runner.py
git diff --check
```

Expected: PASS and no whitespace errors.
