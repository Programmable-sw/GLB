# Grade Collective Scale/Concurrency Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find reproducible All-to-All scenarios where a grade weight profile other than equal minimizes CCT, and explain the associated scale/concurrency mechanism indicators.

**Architecture:** Add a focused runner that reuses the existing 128-node traffic generator and parser, materializes two orthogonal All-to-All sweeps, and validates a paired 3-seed matrix. Add an analyzer that ranks weights, requires non-equal winners to be supported by at least two seeds, and aggregates CCT plus congestion diagnostics before updating the existing Markdown report.

**Tech Stack:** Python 3 standard library, htsim RoCE simulator, CSV, Markdown.

## Global Constraints

- 128 nodes, 8 paths, no background traffic.
- Weights are equal 4/4/4/0, gentle 4/3/2/0, default 4/2/1/0, sharp 8/2/1/0.
- Seeds are exactly 13, 29, and 47 with paired traffic hashes.
- Concurrency sweep fixes each flow at 0.5 MiB and scans P1/P2/P4/P8/P16/P32.
- Size sweep fixes P4 and scans 0.125/0.5/2/8 MiB per flow.
- Do not modify unrelated user changes.

---

### Task 1: Focused matrix runner

**Files:**
- Create: `experiments/n-mrc/run_grade_collective_scale_concurrency.py`
- Test: runner `--dry-run`

**Interfaces:**
- Consumes: `run_final_512_comparison.materialize_traffic`, `build_command`, and diagnostic parsers.
- Produces: `results.csv` with scenario, axis, per-flow MiB, parallelism, variant, seed, paired traffic hash, CCT, and diagnostics.

- [ ] **Step 1:** Define the ten unique scenarios, four weights, and three seeds; deduplicate the common P4/0.5 MiB cell.
- [ ] **Step 2:** Validate that the command matrix contains exactly 120 cells and that every scenario/seed has one traffic hash.
- [ ] **Step 3:** Add cache validation using traffic hash, exact command, and simulator SHA.
- [ ] **Step 4:** Run `python3 experiments/n-mrc/run_grade_collective_scale_concurrency.py --dry-run`; expect `validated 120 paired commands`.
- [ ] **Step 5:** Commit the runner.

### Task 2: Execute and analyze the sweep

**Files:**
- Create: `experiments/n-mrc/analyze_grade_collective_scale_concurrency.py`
- Create: `experiments/n-mrc/output/grade_collective_scale_concurrency_20260726/results.csv`
- Create: `experiments/n-mrc/output/grade_collective_scale_concurrency_20260726/summary.csv`
- Create: `experiments/n-mrc/output/grade_collective_scale_concurrency_20260726/winners.csv`

**Interfaces:**
- Consumes: the runner result rows.
- Produces: a complete scenario/variant summary and a winner table with seed support and mechanism deltas versus equal.

- [ ] **Step 1:** Run the 120-cell matrix with four workers.
- [ ] **Step 2:** Reject missing cells, invalid completion, duplicate seeds, or traffic hash mismatch.
- [ ] **Step 3:** Compute three-seed geometric-mean CCT and mean ECN, trim, NACK, level-change, all-zero, and queue-CV metrics.
- [ ] **Step 4:** Mark a non-equal winner valid only when it has the lowest geometric-mean CCT and beats equal in at least two seeds.
- [ ] **Step 5:** Run the analyzer and verify all ten scenarios and forty summary groups are present.
- [ ] **Step 6:** Commit runner, analyzer, and compact CSV evidence.

### Task 3: Update and verify the report

**Files:**
- Modify: `experiments/n-mrc/four_scheme_risks_and_evidence.md`

**Interfaces:**
- Consumes: `summary.csv` and `winners.csv`.
- Produces: a reader-facing section identifying non-equal collective winners and separating size effects from concurrency effects.

- [ ] **Step 1:** Add the complete CCT ranking table for both scan axes.
- [ ] **Step 2:** For each supported non-equal winner, report paired effect versus equal and the associated congestion indicators.
- [ ] **Step 3:** State whether the scan supports size coupling, concurrency coupling, both, or neither; avoid isolated-causality claims.
- [ ] **Step 4:** Run Python compilation, runner dry-run, analyzer validation, `git diff --check`, and verify CSV hashes.
- [ ] **Step 5:** Commit the report and final evidence.
