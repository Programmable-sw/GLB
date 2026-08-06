# MRC Cold-QP Lifecycle Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce reproducible data, figures, and a technical report testing whether cold per-QP MRC needs at least one RTT and one 64-EV rotation before feedback can help, and whether its relative value increases with communication lifetime.

**Architecture:** Preserve the local latest simulator semantics and extend the existing 256-host steady WebSearch runner. Use identical measured flows for RR, MRC, and SGLB; add dedicated per-flow-ECMP large background connections only in the mixed condition; aggregate same-flow ratios and MRC mechanism diagnostics by exact flow size.

**Tech Stack:** C++ htsim RoCE simulator, Python 3 standard library, Matplotlib, CSV/GZIP/JSON, Markdown.

## Global Constraints

- Use 256 hosts, four Leaves, 64 hosts per Leaf, 64 Spines, and 64 physical paths.
- Use one 400 Gbit/s plane and 4096-byte DATA MTU.
- Require MRC `skip_token`, 64 active EVs, identity EV/path mapping, and failure recovery off.
- Require SGLB real 256-byte high-priority GCN, dToR profiles, 15 us remote cadence, exact-min24 selection, and shuffled RR.
- Start with seed 13; add seed 29 only if seed 13 cannot support a defensible conclusion.
- Preserve all unrelated dirty-worktree changes and all historical output directories.

---

### Task 1: Lock the runner contract with failing tests

**Files:**
- Modify: `sim/tests/test_mrc_sglb_steady_mixed_256_runner.py`
- Modify: `sim/tests/test_mrc_sglb_cold_qp_256_runner.py`

**Interfaces:**
- Consumes: constants, `build_steady_flows`, `build_ecmp_mixed_flows`, `build_command`, `make_steady_rows`, and `flow_size_plot_rows` from the two runners.
- Produces: executable assertions for topology, scheme semantics, ECMP isolation, flow-size bins, and lifecycle metrics.

- [ ] **Step 1: Add assertions for the ECMP-mixed traffic contract**

  Assert that `CONDITIONS` contains `ecmp_mixed`; every connection whose zero-based matrix index is divisible by ten has `cohort == "ecmp_background"`, begins before `STEADY_START_US`, is at least `LONG_DISTRIBUTION[0][0]` bytes, and is excluded by `select_steady_flows`. Assert all other connections are measured RR/MRC/SGLB flows.

- [ ] **Step 2: Add assertions for exact lifecycle outputs**

  For a synthetic diagnostic with `new_data_selections=100`, `new_selections_after_first_update=30`, `packets_before_first_update=70`, and `full_sweeps=1`, assert `nominal_rotations == 100/64`, `post_feedback_selections == 30`, `pre_feedback_selections == 70`, `actual_full_sweeps == 1`, and `actionable == 1`.

- [ ] **Step 3: Run tests and observe the missing behavior**

  Run:

  ```bash
  python3 sim/tests/test_mrc_sglb_cold_qp_256_runner.py
  python3 sim/tests/test_mrc_sglb_steady_mixed_256_runner.py
  ```

  Expected: the cold-runner test passes; the steady-runner test fails because `ecmp_mixed` and `build_ecmp_mixed_flows` are absent.

- [ ] **Step 4: Commit the test contract**

  ```bash
  git add sim/tests/test_mrc_sglb_cold_qp_256_runner.py sim/tests/test_mrc_sglb_steady_mixed_256_runner.py
  git commit -m "test: define MRC lifecycle evidence matrix"
  ```

### Task 2: Add isolated per-flow ECMP large-flow background

**Files:**
- Modify: `experiments/n-mrc/run_mrc_sglb_steady_mixed_256.py`

**Interfaces:**
- Consumes: the simulator's existing `-mixed_lb_traffic` rule, which gives matrix entries `index % 10 == 0` a per-flow `LB_ECMP` override and marks them as background.
- Produces: `build_ecmp_mixed_flows(seed, measured_flows)` and an `ecmp_mixed` command/validation path.

- [ ] **Step 1: Define background-flow constants**

  Add `ECMP_BACKGROUND_START_US = 25.0`, `ECMP_BACKGROUND_SIZE = 64 * 1024 * 1024`, and destinations spanning Leaves 1--3. Keep background sources on Leaf 0 so they share the measured source uplinks.

- [ ] **Step 2: Build an explicitly indexed mixed matrix**

  Implement `build_ecmp_mixed_flows` by emitting one dedicated `ecmp_background` connection followed by at most nine measured connections per group. Assign fresh unique flow IDs after construction. This makes exactly the dedicated connections satisfy the simulator's existing `index % 10 == 0` ECMP override without routing a measured short flow through ECMP.

- [ ] **Step 3: Add the condition command and validation**

  For `ecmp_mixed`, call the healthy base command and append `-mixed_lb_traffic`. Require `MixedLbDiag enabled=on`, the exact total/background/main counts, and completion lines with `bg traffic 1` only for dedicated background flow IDs.

- [ ] **Step 4: Keep measured-flow pairing independent of background flows**

  Generate a separate traffic matrix for `ecmp_mixed`; pass only the original measured flow specs to `make_steady_rows`. Preserve all background completions and traffic rows in raw artifacts and the manifest.

- [ ] **Step 5: Run the runner tests**

  Run:

  ```bash
  python3 sim/tests/test_mrc_sglb_cold_qp_256_runner.py
  python3 sim/tests/test_mrc_sglb_steady_mixed_256_runner.py
  ```

  Expected: both commands exit 0.

- [ ] **Step 6: Commit the mixed condition**

  ```bash
  git add experiments/n-mrc/run_mrc_sglb_steady_mixed_256.py sim/tests/test_mrc_sglb_steady_mixed_256_runner.py
  git commit -m "exp: add isolated ECMP background flows"
  ```

### Task 3: Complete lifecycle metrics, figures, and theory checks

**Files:**
- Modify: `experiments/n-mrc/run_mrc_sglb_steady_mixed_256.py`
- Modify: `sim/tests/test_mrc_sglb_steady_mixed_256_runner.py`
- Create at runtime: `experiments/n-mrc/output/mrc_cold_qp_lifecycle_evidence_256/`

**Interfaces:**
- Consumes: paired completions and `MrcFlowDiag` fields from `cold.parse_mrc_diags`.
- Produces: exact-size rows, theory-check results, two PNG/PDF figure pairs, Markdown report, and manifest.

- [ ] **Step 1: Expose unambiguous MRC control metrics**

  In each paired row store `nominal_rotations = new_data_selections / 64`, `actual_full_sweeps = full_sweeps`, `coverage = unique_active_evs / 64`, `pre_feedback_selections = packets_before_first_update`, `post_feedback_selections = new_selections_after_first_update`, `first_feedback_delay_us`, and `actionable = effective_state_updates > 0 and post_feedback_selections > 0`.

- [ ] **Step 2: Aggregate exact flow sizes**

  For 6, 33, 133, 256, 512, 667 KiB and 1, 1.3, 2, 3.3, 6.5 MiB, emit arithmetic average and p99 FCT per scheme plus paired geometric means for `MRC/RR`, `MRC/SGLB`, and `condition/healthy`. Include sample counts so sparse bins cannot masquerade as stable results.

- [ ] **Step 3: Implement falsifiable checks**

  Emit `theory_checks.json` with booleans and evidence values for: zero-actionable short flows; first feedback no earlier than measured RTT; 256 KiB equals one nominal rotation but does not imply useful post-feedback DATA; actionable fraction rising with size; long-flow MRC/RR improving relative to short-flow MRC/RR; and short-flow MRC/SGLB gap narrowing at longer sizes.

- [ ] **Step 4: Generate figures**

  Create `mrc_lifecycle_by_flow_size.png/.pdf` with four aligned panels: FCT degradation by scheme, paired MRC/RR and MRC/SGLB, nominal/actual rotations plus coverage, and actionable fraction. Create `mrc_lifecycle_robustness.png/.pdf` comparing fixed hotspot, ECMP mixed, and slow uplinks.

- [ ] **Step 5: Write the technical report**

  Lead with measured outcomes, define denominators, distinguish average from p99, connect each theory claim to mechanism data, and explicitly report any failed check or noisy boundary. Do not attribute zero-actionable short-flow differences to the flow's own MRC feedback.

- [ ] **Step 6: Run tests and commit**

  Run both runner tests and `python3 -m py_compile` on both runners. Expected: all commands exit 0.

  ```bash
  git add experiments/n-mrc/run_mrc_sglb_steady_mixed_256.py sim/tests/test_mrc_sglb_steady_mixed_256_runner.py
  git commit -m "exp: report MRC lifecycle evidence"
  ```

### Task 4: Verify simulator semantics and execute the focused matrix

**Files:**
- Runtime outputs only under `experiments/n-mrc/output/mrc_cold_qp_lifecycle_evidence_256/`.

**Interfaces:**
- Consumes: current local simulator sources and the completed runner.
- Produces: validated evidence package for seed 13, with seed 29 only if required.

- [ ] **Step 1: Run semantic unit tests and a clean incremental build**

  Run the MRC skip-policy, SGLB quality/default, topology, runner, and cadence tests selected by their Makefile targets, then run `make -C sim -j4`. Expected: every test exits 0 and `sim/datacenter/htsim_roce` links successfully.

- [ ] **Step 2: Run a small pilot**

  Run the steady runner with `--seeds 13 --sample-scale 0.05 --workers 3` into a new pilot directory. Require all twelve scheme/condition cells to complete, nonzero real-GCN counters for SGLB, correct MRC policy diagnostics, and background-only ECMP completion markers.

- [ ] **Step 3: Run the focused seed-13 experiment**

  Run the full sample with `--seeds 13 --workers 3` into `experiments/n-mrc/output/mrc_cold_qp_lifecycle_evidence_256`. Require complete flow-ID pairing, positive FCT, valid fingerprints, exact topology diagnostics, and complete size bins.

- [ ] **Step 4: Decide whether one additional seed is necessary**

  Inspect `theory_checks.json`, per-flow ratios, and sample counts. Run seed 29 only if a key curve direction is within observed dispersion, a required bin is sparse, or a mechanism/performance result conflicts. Never add a seed solely to search for a preferred direction.

- [ ] **Step 5: Reproduce from cache**

  Re-run the identical final command without `--force`. Expected: cached fingerprints match and the CSV/JSON/report/figure hashes remain identical.

- [ ] **Step 6: Inspect both PNG figures at original resolution**

  Confirm labels, legends, denominators, 256 KiB marker, scales, and absence of clipping. Fix and regenerate if any visual defect is present.

### Task 5: Final evidence audit

**Files:**
- Modify only if validation finds an error: runner, tests, or generated report inputs.

**Interfaces:**
- Consumes: final manifest, theory checks, CSVs, raw logs, and figures.
- Produces: an evidence-backed handoff with direct artifact links.

- [ ] **Step 1: Validate numerical consistency**

  Recompute representative same-flow ratios from `steady_paired_flow_metrics.csv.gz`, verify aggregates in `flow_size_focus.csv`, and check average/p99 definitions against the report.

- [ ] **Step 2: Validate causal wording**

  Ensure every claim about feedback is backed by post-feedback DATA selections, every rotation claim distinguishes nominal cursor progress from actual coverage, and MRC/SGLB is labelled a full-scheme rather than pure sharing comparison.

- [ ] **Step 3: Run the final relevant test suite**

  Run the selected semantic tests, both runner tests, Python compilation, and the cache-reproduction command. Record exact pass/fail output before claiming completion.
