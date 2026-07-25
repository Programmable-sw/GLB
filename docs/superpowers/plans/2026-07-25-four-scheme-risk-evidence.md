# Four-Scheme Risk Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a source-backed Markdown report comparing the limitations, causal evidence, counterexamples, and operating boundaries of avail, grade, netaware, and n-mrc.

**Architecture:** Add one optional, read-only cumulative path-selection sampler to RoCE diagnostics. Run a bounded P16 four-scheme trace experiment, transform snapshots into cumulative and window CV, render one static chart, then combine that evidence with the existing 72-run causal ablations.

**Tech Stack:** C++11 htsim diagnostics, Python 3 standard library, Matplotlib, Markdown, CSV.

## Global Constraints

- Final report and supporting chart/data must live under `/home/user/workspace/csg-htsim/experiments/n-mrc`.
- Cumulative CV and window-increment CV must be named separately.
- Every major claim must be labeled causal-confirmed, supported, or unverified.
- Diagnostic sampling must not change path selection.

---

### Task 1: Read-only path-selection timeline

**Files:**
- Modify: `sim/roce.h`
- Modify: `sim/roce.cpp`
- Modify: `sim/datacenter/main_roce.cpp`
- Test: `sim/tests/test_path_selection_timeline.py`

**Interfaces:**
- Consumes: existing `RoceSrc::record_path_selection(uint32_t, uint32_t)`.
- Produces: optional CSV columns `time_us,selected_total,path_0...path_7,cumulative_cv`.

- [ ] Write a failing CLI/CSV contract test invoking `htsim_roce` with `-path_selection_timeline`.
- [ ] Run the test and confirm it fails because the option is unknown.
- [ ] Add a static trace stream and selection interval; emit a snapshot without changing the selected path.
- [ ] Parse `-path_selection_timeline FILE` and `-path_selection_timeline_every N`, with tracing disabled by default.
- [ ] Rebuild and run the contract test; require monotonically increasing totals and eight non-negative path counts.
- [ ] Run existing SGLB quality and public preset tests.
- [ ] Commit the diagnostic implementation.

### Task 2: Four-scheme P16 timeline experiment and chart

**Files:**
- Create: `experiments/n-mrc/analyze_four_scheme_path_cv.py`
- Create: `experiments/n-mrc/four_scheme_path_cv_timeline.csv`
- Create: `experiments/n-mrc/four_scheme_path_cv_timeline.png`
- Test: `sim/tests/test_four_scheme_path_cv_analysis.py`

**Interfaces:**
- Consumes: four raw timeline CSVs from identical P16 seed=13 traffic.
- Produces: long-form rows containing `scheme,time_us,selected_total,cumulative_cv,window_cv`.

- [ ] Write a failing unit test for CV and adjacent-window CV calculations.
- [ ] Implement population CV, count-difference validation, and long-form export.
- [ ] Run P16 background-off seed=13 for avail, grade, netaware, and n-mrc with the same traffic hash.
- [ ] Generate the combined CSV and a two-panel line chart: cumulative CV and window CV.
- [ ] Inspect the PNG for readable labels, honest scales, and distinct non-color line styles.
- [ ] Commit script, compact CSV, and PNG.

### Task 3: Risk and evidence Markdown

**Files:**
- Create: `experiments/n-mrc/four_scheme_risks_and_evidence.md`

**Interfaces:**
- Consumes: existing 72-run causal `results.csv` and `effects.csv`, plus Task 2 CV timeline.
- Produces: final Chinese report.

- [ ] Reconcile all headline values against source CSVs.
- [ ] For each scheme, write mechanism, limitations, causal evidence, counterexamples, scenario boundaries, and remaining risks.
- [ ] Add a direct four-way comparison and selection guidance.
- [ ] Embed `four_scheme_path_cv_timeline.png` with an adjacent explanation distinguishing cumulative and window CV.
- [ ] Run a numeric consistency check, Markdown link check, `git diff --check`, and relevant simulator tests.
- [ ] Commit the report and verified evidence.
