# n-MRC Routing and Cooldown Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add isolated controls for graded n-MRC reroute delta and cooldown policy, then run and visualize six mechanisms over seed-13 All-to-All and WebSearch matrices.

**Architecture:** Keep the existing four-level selector as the single source of reroute semantics. Filter candidate availability by continuous-score delta only for the graded-delta variant, and select the FastCNP cooldown bit independently through a three-mode policy. A dedicated runner reuses archived traffic files and the main-matrix parser.

**Tech Stack:** C++11 htsim simulator, existing C++ n-MRC tests, Python 3 experiment runner, Matplotlib.

## Global Constraints

- Preserve the current canonical `n-mrc` default: graded `better_ge3` rerouting and selective cooldown at a 0.25 valid score gap.
- Reject graded experiment controls for `n-mrc-fixed0.5` and `n-mrc-delta`.
- Use seed 13 and archived traffic SHA-256 values for all 156 runs.
- Require every run to complete its archived expected flow count before aggregation.
- Do not run seeds 29 or 47 before user review.

---

### Task 1: Simulator controls

**Files:**
- Modify: `sim/datacenter/fat_tree_switch.h`
- Modify: `sim/datacenter/fat_tree_switch.cpp`
- Modify: `sim/datacenter/main_roce.cpp`
- Test: `sim/tests/main_sglb_quality.cpp`

**Interfaces:**
- Produces: `NmrcGradedCooldownMode { SELECTIVE, FULL, NONE }`
- Produces: `nmrc_select_graded_delta_path(...) -> NmrcRerouteDecision`
- Produces CLI: `-nmrc_graded_cooldown selective|full|none`
- Produces CLI: `-nmrc_graded_reroute_delta VALUE`

- [ ] **Step 1: Write failing selector and policy tests**

Add a selector case with four strictly better grades where only two paths have
`gap >= 0.25`; require no reroute. Change a third score to meet the delta and
require a reroute to one of the three qualifying paths. Add assertions for
selective/full/none cooldown decisions.

- [ ] **Step 2: Verify RED**

Run:

```bash
make -C sim/tests htsim_sglb_quality -j4
```

Expected: compilation fails because the new selector and policy enum are
absent.

- [ ] **Step 3: Implement the minimal controls**

For graded-delta, derive an eligible vector:

```cpp
eligible[i] = available[i] && i != original_index &&
    levels[i] < levels[original_index] &&
    std::isfinite(scores[original_index]) &&
    std::isfinite(scores[i]) &&
    scores[original_index] - scores[i] >= delta - 1e-12;
```

Pass the eligible vector through the existing `nmrc_select_better_path`, so
`better_count` and candidate hashing use only paths satisfying both predicates.
Choose the cooldown bit as:

```cpp
full       -> true
none       -> false
selective  -> valid selected gap >= 0.25
```

Parse, validate, print, and assign the two CLI controls only for canonical
`-lb n-mrc`.

- [ ] **Step 4: Verify GREEN and regressions**

Run:

```bash
make -C sim/tests htsim_sglb_quality htsim_hybrid_nmrc -j4
sim/tests/htsim_sglb_quality
sim/tests/htsim_hybrid_nmrc
make -C sim/datacenter htsim_roce -j4
```

Expected: both test binaries exit zero and the simulator builds.

- [ ] **Step 5: Commit**

Commit message: `feat: expose graded n-MRC routing cooldown controls`.

### Task 2: Validated experiment runner

**Files:**
- Create: `experiments/n-mrc/run_nmrc_routing_cooldown_matrix.py`

**Interfaces:**
- Consumes: archived `n-mrc-results` traffic and expected-flow metadata.
- Produces: six variants × 26 scenarios × seed 13.

- [ ] **Step 1: Implement variant and scenario manifests**

Define the variants exactly as `graded_selective`, `graded_full`,
`graded_none`, `graded_delta025`, `fixed05`, and `delta025`. Select all 18
All-to-All cells plus healthy/asymmetric WebSearch 40/60/80/100 cells.

- [ ] **Step 2: Validate without simulation**

Run:

```bash
python3 experiments/n-mrc/run_nmrc_routing_cooldown_matrix.py --list-only
```

Expected: 6 variants, 26 scenarios, 156 unique cases, one seed, and 26 traffic
hashes matching the archive.

- [ ] **Step 3: Run the matrix**

Run with bounded parallelism and resume:

```bash
python3 experiments/n-mrc/run_nmrc_routing_cooldown_matrix.py --seed 13 --workers 6
```

Expected: 156 valid summaries with exact archived completion counts.

- [ ] **Step 4: Commit**

Commit message: `analysis: add n-MRC routing cooldown matrix`.

### Task 3: Figures and validated comparison

**Files:**
- Create: `experiments/n-mrc/build_nmrc_routing_cooldown_figures.py`
- Create under ignored output: `experiments/n-mrc/output/nmrc_routing_cooldown_matrix_seed13_20260726/figures/`

**Interfaces:**
- Consumes: validated Task 2 `results.csv`.
- Produces: absolute/normalized source CSVs, All-to-All heatmap, WebSearch heatmap, and validation JSON.

- [ ] **Step 1: Build aggregation and chart code**

Use CCT for All-to-All and foreground p99 FCT for WebSearch. Normalize each
cell by the best of the six variants after validating one row per
variant/scenario.

- [ ] **Step 2: Render and inspect**

Run the builder, verify both matrices have shapes `6×18` and `6×8`, and inspect
the PNGs for labels, clipping, consistent scales, and correct lower-is-better
direction.

- [ ] **Step 3: Recompute headline comparisons**

Independently recompute geometric-mean ratios for All-to-All, healthy
WebSearch, asymmetric WebSearch, and WebSearch by offered load. Reconcile
reroute and cooldown diagnostic partitions.

- [ ] **Step 4: Commit**

Commit message: `analysis: visualize n-MRC routing cooldown matrix`.
