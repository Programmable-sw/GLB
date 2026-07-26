# Four-Scheme Parameter Coupling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace speculative four-scheme limitations with multi-scenario, three-seed intervention evidence covering weights, thresholds, recovery, path scale, and flow-size effects.

**Architecture:** Add two read-only diagnostic families to the simulator, then use one manifest-driven runner to execute bounded parameter matrices with shared traffic hashes. A separate analyzer produces paired effects, flow-size cuts, mechanism tables, and figures consumed by the final Markdown.

**Tech Stack:** C++11 htsim, Python 3 standard library, Matplotlib, CSV, Markdown.

## Global Constraints

- All conclusions use same-seed comparisons.
- Primary seeds are 13, 29, and 47.
- Only one parameter family changes within a causal contrast.
- Unverified risks are removed from the final limitations table.
- Cumulative CV, complete-window CV, and local source-destination CV are distinct metrics.
- Raw commands, revision, traffic hashes, results, and analysis outputs are saved under `experiments/n-mrc/`.

---

### Task 1: STOR transition and all-zero fallback diagnostics

**Files:**
- Modify: `sim/roce.h`
- Modify: `sim/roce.cpp`
- Modify: `sim/datacenter/main_roce.cpp`
- Test: `sim/tests/main_stor_feedback.cpp`
- Test: `sim/tests/test_stor_transition_diag.py`

**Interfaces:**
- Produces: `StorProfileDiag level_transitions=... level_changes=N all_zero_profiles=N all_zero_selections=N`.

- [ ] Add failing unit assertions that applying GOOD→MILD→GOOD feedback produces the expected 4×4 transition counts and reset clears them.
- [ ] Run the unit test and confirm missing diagnostic APIs fail compilation.
- [ ] Implement static transition counters in `apply_stor_feedback`, excluding unchanged diagonal entries from `level_changes`.
- [ ] Add failing runtime test that all-AVOID feedback followed by selection increments all-zero profile and selection counters.
- [ ] Implement all-zero fallback counters at weighted-profile rebuild/selection without changing the selected path.
- [ ] Print and parse `StorProfileDiag`; run new and existing STOR/SGLB tests.
- [ ] Commit the diagnostic change.

### Task 2: Manifest-driven parameter runner

**Files:**
- Create: `experiments/n-mrc/run_four_scheme_parameter_coupling.py`
- Test: `sim/tests/test_four_scheme_parameter_coupling_runner.py`

**Interfaces:**
- Produces: `commands.tsv`, `results.csv`, `flows.csv`, `revision.txt`, traffic matrices and raw logs.

- [ ] Write a failing runner contract test for unique `(family,scenario,variant,seed)` identities, required flags, and traffic-hash equality.
- [ ] Define grade weight variants equal `4/4/4/0`, gentle `4/3/2/0`, default `4/2/1/0`, sharp `8/2/1/0`.
- [ ] Define grade threshold variants from current defaults after reading effective configuration; enforce legal ordering.
- [ ] Define netaware equal/gentle/default/sharp with adaptation off plus default/GoodCap.
- [ ] Define avail default, ECN-only, hybrid-fast, hybrid-conservative.
- [ ] Define n-mrc never/better_ge3/any_better at 4/8/16 paths.
- [ ] Generate asymmetric, P16, P4, and WebSearch specs with seeds 13/29/47; save per-flow size and finish time.
- [ ] Run dry-run validation and commit runner/test.

### Task 3: Execute matrix with checkpointed validation

**Files:**
- Create: `experiments/n-mrc/output/four_scheme_parameter_coupling_20260726/`

**Interfaces:**
- Consumes: Task 2 runner.
- Produces: complete raw experiment evidence.

- [ ] Build `htsim_roce` and run one smoke case per parameter family.
- [ ] Execute the full matrix with two workers and resume-safe fingerprints.
- [ ] After each family, require return code 0, effective config match, all flows completed, unique identities, and matching traffic hashes.
- [ ] Re-run only failed/missing identities; never silently drop runs.
- [ ] Freeze `results.csv`, `flows.csv`, `commands.tsv`, and revision.

### Task 4: Analyze coupling and mechanism effects

**Files:**
- Create: `experiments/n-mrc/analyze_four_scheme_parameter_coupling.py`
- Test: `sim/tests/test_four_scheme_parameter_coupling_analysis.py`
- Create: `experiments/n-mrc/four_scheme_parameter_effects.csv`
- Create: `experiments/n-mrc/four_scheme_flow_size_effects.csv`
- Create: `experiments/n-mrc/four_scheme_mechanism_effects.csv`

**Interfaces:**
- Produces paired geometric-mean effects and mechanism deltas by family/scenario/variant/flow-size bin.

- [ ] Write failing tests for same-seed pairing, geometric means, flow-size bins, transition reversal rates, and incomplete-pair rejection.
- [ ] Implement paired effect calculations with explicit baseline and sign.
- [ ] Compute short, medium, 20–30 MiB and other-long flow bins for WebSearch.
- [ ] Compute ECN/TRIM/retransmit/reroute/recovery/fallback/transition/CV deltas per million packets or feedback observations.
- [ ] Mark a mechanism confirmed only when the intervention, performance, and corresponding mechanism metric align; otherwise mark supported, falsified, or unresolved.
- [ ] Run analysis tests and commit CSV outputs.

### Task 5: Figures and corrected report

**Files:**
- Create: `experiments/n-mrc/four_scheme_weight_scenario_heatmap.png`
- Create: `experiments/n-mrc/four_scheme_avail_cv_effect.png`
- Create: `experiments/n-mrc/four_scheme_nmrc_scale_effect.png`
- Modify: `experiments/n-mrc/four_scheme_risks_and_evidence.md`

**Interfaces:**
- Consumes: Task 4 CSVs.
- Produces: final verified report and three evidence figures.

- [ ] Render weight×scenario performance heatmap for grade and netaware.
- [ ] Render avail paired window-CV/fallback/performance comparison.
- [ ] Render n-mrc policy×path-count performance and reroute comparison.
- [ ] Inspect every image for readable labels, honest scales, and consistent units.
- [ ] Rewrite each scheme section to include only verified limitations, intervention, effect, mechanism indicators, counterexample, and falsified old explanations.
- [ ] Remove every remaining unverified “possible limitation” from the comparison table.
- [ ] Recompute all headline values from saved CSVs, check links, regenerate figures byte-for-byte, build, run regression tests, and request independent review.
- [ ] Commit and merge the verified report into `main-htsim`.
