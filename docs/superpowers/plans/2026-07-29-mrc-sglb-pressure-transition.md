# MRC–SGLB Pressure-Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run and document a same-input MRC-versus-SGLB flow-size transition experiment.

**Architecture:** A dedicated standard-library Python runner reuses validated
MRC caches and existing traffic matrices, runs only missing SGLB cells, strictly
pairs flows, aggregates by flow size, and renders the same transition view.

**Tech Stack:** Python 3 standard library, htsim RoCE simulator, matplotlib for figures, pytest for runner tests.

## Global Constraints

- Do not modify simulator behavior.
- Do not overwrite the existing MRC-versus-RR artifacts.
- Preserve existing unrelated worktree changes.
- Treat the result as a complete-scheme comparison, not a control-variable ablation.

---

### Task 1: Tested parsing and aggregation

**Files:**
- Create: `sim/tests/test_mrc_sglb_pressure_transition_runner.py`
- Create: `experiments/n-mrc/run_mrc_sglb_pressure_transition.py`

**Interfaces:**
- Consumes: traffic matrix text, simulator completion text, MRC flow dictionaries.
- Produces: `parse_traffic`, `parse_completions`, `pair_flows`, and `aggregate_pairs`.

- [ ] Write tests for traffic parsing, completion parsing, strict pairing, and geometric-mean aggregation.
- [ ] Run pytest and verify failure because the runner does not exist.
- [ ] Implement the minimal pure functions.
- [ ] Run the focused pytest file and verify all tests pass.

### Task 2: Reproducible SGLB execution

**Files:**
- Modify: `experiments/n-mrc/run_mrc_sglb_pressure_transition.py`

**Interfaces:**
- Consumes: the existing 60%/80% traffic and MRC raw cells.
- Produces: two validated SGLB raw cells and `cells.csv`.

- [ ] Add command construction and hash-validated MRC cache loading.
- [ ] Add atomic SGLB stdout, command, return-code, and summary writes.
- [ ] Add exact completion and effective-configuration validation.
- [ ] Run the focused tests, then run the two SGLB cells.

### Task 3: Results, visualization, and explanation

**Files:**
- Modify: `experiments/n-mrc/run_mrc_sglb_pressure_transition.py`
- Create: `experiments/n-mrc/mrc_sglb_pressure_transition_example.md`
- Create: `experiments/n-mrc/output/mrc_sglb_pressure_transition_examples_5ms/focused_summary.csv`
- Create: `experiments/n-mrc/output/mrc_sglb_pressure_transition_examples_5ms/focused_transition.png`
- Create: `experiments/n-mrc/output/mrc_sglb_pressure_transition_examples_5ms/focused_transition.pdf`

**Interfaces:**
- Consumes: strictly paired per-flow MRC and SGLB data.
- Produces: auditable tables, figures, and mechanism-aware conclusions.

- [ ] Aggregate each load and discrete flow size with the specified metrics.
- [ ] Render MRC/SGLB FCT ratio and MRC actionable-flow fraction.
- [ ] Write the Markdown interpretation and comparison caveat.
- [ ] Validate row counts, hashes, finite metrics, visual layout, links, and repository diff.
- [ ] Commit only files belonging to this experiment.
