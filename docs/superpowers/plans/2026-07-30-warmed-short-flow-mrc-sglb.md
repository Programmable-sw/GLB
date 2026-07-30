# Warmed Short-Flow MRC–SGLB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a three-seed controlled test of the conditional short-flow disadvantage of feedback-driven per-QP MRC versus warmed SGLB.

**Architecture:** A dedicated runner generates balanced short-flow matrices,
runs MRC and SGLB over identical fixed-link background, strictly pairs flows,
aggregates by size and seed, and renders mechanism and FCT evidence.

**Tech Stack:** Python 3 standard library, htsim RoCE, pytest, matplotlib.

## Global Constraints

- Do not modify simulator behavior.
- Preserve unrelated worktree changes.
- Report the existing 6 KiB counterexample and distinguish conditional from universal claims.
- Make compressed and PDF artifacts deterministic.

---

### Task 1: Testable traffic, diagnostics, and aggregation

**Files:**
- Create: `sim/tests/test_mrc_sglb_warmed_short_flow_runner.py`
- Create: `experiments/n-mrc/run_mrc_sglb_warmed_short_flow.py`

- [ ] Write failing tests for balanced traffic, command parity, MRC diagnostics, SGLB diagnostics, strict pairing, and per-seed aggregation.
- [ ] Implement the minimum pure functions and verify the tests pass.

### Task 2: Run the controlled matrix

**Files:**
- Modify: `experiments/n-mrc/run_mrc_sglb_warmed_short_flow.py`

- [ ] Add cache fingerprints and atomic raw artifacts.
- [ ] Run 3 seeds × 2 schemes.
- [ ] Require exact foreground completion and configuration diagnostics.

### Task 3: Validate and report

**Files:**
- Modify: `experiments/n-mrc/mrc_sglb_pressure_transition_example.md`
- Create: `experiments/n-mrc/output/mrc_sglb_warmed_short_flow/summary.csv`
- Create: `experiments/n-mrc/output/mrc_sglb_warmed_short_flow/focused_proof.png`
- Create: `experiments/n-mrc/output/mrc_sglb_warmed_short_flow/focused_proof.pdf`

- [ ] Independently recompute headline ratios from paired-flow data.
- [ ] Inspect SGLB route differentiation and MRC actionable/sweep metrics.
- [ ] Render and visually inspect the proof chart.
- [ ] State supported claim, counterexamples, and simulator caveats.
- [ ] Verify deterministic reruns and commit only experiment files.
