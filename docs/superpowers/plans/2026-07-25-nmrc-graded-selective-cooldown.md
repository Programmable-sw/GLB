# n-MRC Graded Selective Cooldown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep canonical n-MRC four-level `better_ge3` packet rerouting while requesting endpoint EV cooldown only when the selected path's continuous score improves by at least 0.25.

**Architecture:** The source-leaf graded path keeps its existing quantized selector and additionally retains each candidate's continuous score. A small pure predicate determines the FastCNP cooldown bit; the FastCNP always reports a committed reroute, while the endpoint already honors the bit without touching DCQCN.

**Tech Stack:** C++11 htsim simulator, existing `main_sglb_quality` and `main_hybrid_nmrc` tests, Python experiment runner.

## Global Constraints

- Apply only to canonical `-lb n-mrc` graded decisions.
- Preserve the existing four-level `better_ge3` reroute and candidate selection.
- Use `score_gap >= 0.25`; invalid scores request no cooldown.
- Run seed 13 only for P=4 healthy and asymmetric-background All-to-All at 64, 256, and 1024 MiB.

---

### Task 1: Selective cooldown behavior

**Files:**
- Modify: `sim/datacenter/fat_tree_switch.h`
- Modify: `sim/datacenter/fat_tree_switch.cpp`
- Modify: `sim/tests/main_sglb_quality.cpp`

**Interfaces:**
- Produces: `FatTreeSwitch::nmrc_graded_requests_cooldown(double original_score, double selected_score, double threshold) -> bool`
- Consumes: existing `sglb_quality_snapshot`, `nmrc_select_better_path`, and `nmrc_inject_fastcnp`.

- [ ] **Step 1: Write the failing predicate tests**

Add assertions that `0.401 -> 0.399` does not cool, `0.65 -> 0.40` does cool, a gap above 0.25 cools, and NaN does not cool:

```cpp
expect(!FatTreeSwitch::nmrc_graded_requests_cooldown(0.401, 0.399, 0.25),
       "a tiny cross-bucket gap must not cool");
expect(FatTreeSwitch::nmrc_graded_requests_cooldown(0.65, 0.40, 0.25),
       "the cooldown threshold is inclusive");
expect(FatTreeSwitch::nmrc_graded_requests_cooldown(0.80, 0.40, 0.25),
       "a large improvement must cool");
expect(!FatTreeSwitch::nmrc_graded_requests_cooldown(
           std::numeric_limits<double>::quiet_NaN(), 0.10, 0.25),
       "invalid scores must not cool");
```

- [ ] **Step 2: Verify RED**

Run `make -C sim/tests main_sglb_quality -j4 && sim/tests/main_sglb_quality`.
Expected: compilation fails because `nmrc_graded_requests_cooldown` is absent.

- [ ] **Step 3: Implement the predicate and graded FastCNP bit**

Declare the predicate in `fat_tree_switch.h`. Implement it with finite-score
checks and the existing epsilon. In the graded branch, retain snapshot scores,
calculate the selected gap after the existing quantized selector commits, and
pass the resulting boolean plus continuous score metadata into FastCNP:

```cpp
bool request_cooldown = nmrc_graded_requests_cooldown(
    scores[original_choice], scores[decision.selected_index], 0.25);
```

The reroute remains committed whenever `decision.reroute` is true, regardless
of the predicate result.

- [ ] **Step 4: Verify GREEN**

Run `make -C sim/tests main_sglb_quality -j4 && sim/tests/main_sglb_quality`.
Expected: all SGLB/n-MRC quality tests pass.

### Task 2: FastCNP delivery and diagnostics

**Files:**
- Modify: `sim/datacenter/fat_tree_switch.h`
- Modify: `sim/datacenter/fat_tree_switch.cpp`
- Modify: `sim/datacenter/main_roce.cpp`
- Modify: `sim/tests/main_hybrid_nmrc.cpp`

**Interfaces:**
- Produces diagnostics `graded_cooldown_requested` and `graded_cooldown_suppressed`.
- Consumes `RoceFastCnp::need_endpoint_cooldown()` and the Task 1 predicate.

- [ ] **Step 1: Write failing integration assertions**

Extend the graded reroute integration test to inspect captured FastCNP flags
and require one reroute below the continuous threshold to have a false flag,
while a large-gap reroute has a true flag. Assert that the two diagnostic
counters partition generated graded FastCNPs.

- [ ] **Step 2: Verify RED**

Run `make -C sim/tests main_hybrid_nmrc -j4 && sim/tests/main_hybrid_nmrc`.
Expected: the new diagnostic symbols are absent or the old FastCNP flag is
always true.

- [ ] **Step 3: Add minimal transport plumbing and counters**

Extend `nmrc_inject_fastcnp` to accept continuous scores and a cooldown
boolean, construct the existing extended FastCNP tuple, increment exactly one
of:

```cpp
_nmrc_diag_graded_cooldown_requested++;
_nmrc_diag_graded_cooldown_suppressed++;
```

Reset and print both counters in `HybridNmrcDiag`. Do not change
`RoceSrc::processFastCnp`, which already calls `notify_nmrc_ev` only when the
bit is true.

- [ ] **Step 4: Verify GREEN and regression tests**

Run:

```bash
make -C sim/tests main_sglb_quality main_hybrid_nmrc -j4
sim/tests/main_sglb_quality
sim/tests/main_hybrid_nmrc
make -C sim -j4
```

Expected: both test binaries pass and the simulator builds.

- [ ] **Step 5: Commit implementation**

Commit the simulator and test changes with message
`feat: gate n-MRC cooldown on continuous score gap`.

### Task 3: Six-cell seed-13 experiment

**Files:**
- Create: `experiments/n-mrc/run_nmrc_graded_selective_cooldown_p4.py`
- Create under ignored output: `experiments/n-mrc/output/nmrc_graded_selective_cooldown_p4_seed13_20260725/`

**Interfaces:**
- Consumes the main-matrix traffic generator/configuration and archived
  seed-13 summaries.
- Produces `summary.csv`, per-run command/log/summary artifacts, and
  `comparison.csv`.

- [ ] **Step 1: Add a six-scenario runner**

Reuse the scenario construction and validation functions from
`run_final_512_comparison.py`. Select only P=4 healthy and
asymmetric-background scenarios at 64, 256, and 1024 MiB, scheme `n-mrc`, seed
13, and the current simulator binary.

- [ ] **Step 2: Validate the runner without simulation**

Run the runner's listing or dry-run mode and assert exactly six unique cells,
all with seed 13, P=4, and traffic hashes matching the archived matrix.

- [ ] **Step 3: Run all six cells**

Execute the runner with resume enabled. Expected: six successful simulations,
each completing all 16,256 foreground flows.

- [ ] **Step 4: Compare results**

Report absolute CCT and percent change versus archived canonical n-MRC, MRC,
REPS, OPS, and SGLB. Include reroutes, requested/suppressed cooldowns, cooling
skips, all-cooling fallbacks, ECN marks, and TRIM counts.

- [ ] **Step 5: Commit runner**

Commit the reproducible runner with message
`analysis: add n-MRC selective cooldown P4 experiment`.
