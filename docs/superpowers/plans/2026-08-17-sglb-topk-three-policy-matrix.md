# SGLB GCN TopK Three-Policy Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a reproducible 18-cell experiment comparing six SGLB/GCN variants under native, strict TopK=8, and strict TopK=24 selection.

**Architecture:** An experiment-only selector flag is added to the existing SGLB switch implementation while leaving defaults unchanged. The dormant paper-SGLB implementation is made controllable again through its archived CLI switches, and a focused Python runner owns the fixed workload, 18-cell manifest, execution, validation, parsing, and report generation.

**Tech Stack:** C++11 htsim simulator, GNU Make, Python 3 standard library, pytest, Git worktrees.

## Global Constraints

- Run exactly six variants times three policies, for 18 cells.
- Reuse one seed-29, 256-flow, 16 MiB-per-flow traffic matrix with SHA-256 `472a244b549667c5740d911b3fdd4ccece4d8c987e112350b77f67d0a6636a28`.
- Keep current production defaults unchanged when no experiment-only selector flag is supplied.
- Require 256/256 completed flows and retain every result, command, configuration log, and raw output.
- Primary winner metric is linear-interpolated p99 FCT; highlight winners without deleting losing cells.
- Experimental implementation runs in an isolated Git worktree.

---

### Task 1: Explicit SGLB candidate policy

**Files:**
- Modify: `sim/datacenter/fat_tree_switch.h`
- Modify: `sim/datacenter/fat_tree_switch.cpp`
- Modify: `sim/tests/main_paper_sglb.cpp`

**Interfaces:**
- Produces: `FatTreeSwitch::SglbCandidatePolicy { SGLB_CANDIDATES_NATIVE, SGLB_CANDIDATES_STRICT_TOPK }`.
- Produces: static `_sglb_candidate_policy` and `sglb_candidate_policy_name()`.
- Consumes: existing `paper_sglb_topk_by_level(levels, available, tie_keys, k)` helper and `_sglb_min_choices` as K.

- [ ] **Step 1: Write the failing focused test**

Add assertions to `sim/tests/main_paper_sglb.cpp` that the default policy is native, its name is `native`, and the shared exact-K helper returns 3 available entries when K exceeds availability and exactly K entries when availability exceeds K:

```cpp
assert(FatTreeSwitch::_sglb_candidate_policy ==
       FatTreeSwitch::SGLB_CANDIDATES_NATIVE);
assert(std::string(FatTreeSwitch::sglb_candidate_policy_name()) == "native");
vector<uint8_t> policy_levels{0, 0, 1, 1, 2};
vector<bool> policy_available{true, false, true, true, false};
vector<uint64_t> policy_keys{50, 40, 30, 20, 10};
assert(FatTreeSwitch::paper_sglb_topk_by_level(
           policy_levels, policy_available, policy_keys, 8).size() == 3);
assert(FatTreeSwitch::paper_sglb_topk_by_level(
           policy_levels, policy_available, policy_keys, 2).size() == 2);
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `make -C sim/tests htsim_paper_sglb && sim/tests/htsim_paper_sglb`

Expected: compilation fails because `SGLB_CANDIDATES_NATIVE`, `_sglb_candidate_policy`, or `sglb_candidate_policy_name()` is not defined.

- [ ] **Step 3: Implement the policy without changing defaults**

Declare the enum, static field, and name accessor in `fat_tree_switch.h`; initialize the field to native in `fat_tree_switch.cpp`. In `sglb_route`, preserve the current branch byte-for-byte for native mode. For strict mode, construct deterministic tie keys using the existing switch/destination/path hash inputs, call:

```cpp
best_choices = paper_sglb_topk_by_level(
    qualities, available, tie_keys,
    _sglb_min_choices ? _sglb_min_choices : 1);
```

and retain the existing shuffled-round-robin selection over the resulting candidates for current asynchronous real-GCN SGLB.

- [ ] **Step 4: Run focused and regression tests**

Run: `make -C sim/tests htsim_paper_sglb htsim_sglb_quality && sim/tests/htsim_paper_sglb && sim/tests/htsim_sglb_quality`

Expected: both executables exit 0.

- [ ] **Step 5: Commit**

```bash
git add sim/datacenter/fat_tree_switch.h sim/datacenter/fat_tree_switch.cpp sim/tests/main_paper_sglb.cpp
git commit -m "feat: add explicit SGLB candidate policy"
```

### Task 2: Restore and validate experiment CLI controls

**Files:**
- Modify: `sim/datacenter/main_roce.cpp`
- Create: `sim/tests/test_sglb_topk_matrix_cli.py`

**Interfaces:**
- Consumes: `-sglb_candidate_policy native|strict_topk` and existing `_sglb_min_choices`.
- Restores: `-paper_sglb_gcn_us`, `-paper_sglb_sample_us`, `-paper_sglb_select`, `-paper_sglb_remote_mode`, `-paper_sglb_ablation`, `-paper_sglb_weights`, and `-paper_sglb_k` from `archive/remote-sync-and-paper-sglb`.
- Produces: effective-config lines containing the resolved policy, K, remote mode, ablation, and weights.

- [ ] **Step 1: Write the failing parser-source test**

Create `sim/tests/test_sglb_topk_matrix_cli.py`:

```python
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "datacenter" / "main_roce.cpp"


def test_matrix_cli_controls_are_present_and_logged():
    text = SOURCE.read_text()
    for token in (
        '"-sglb_candidate_policy"', '"strict_topk"',
        '"-paper_sglb_select"', '"-paper_sglb_remote_mode"',
        '"-paper_sglb_ablation"', '"-paper_sglb_weights"',
        '"-paper_sglb_k"', "candidate policy",
    ):
        assert token in text


def test_unknown_candidate_policy_is_fatal():
    text = SOURCE.read_text()
    assert "unknown SGLB candidate policy" in text
    assert "exit(1)" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest sim/tests/test_sglb_topk_matrix_cli.py -q`

Expected: both tests fail because the current parser lacks these switches.

- [ ] **Step 3: Add strict candidate-policy parsing**

After `-sglb_min_choices`, parse `native` and `strict_topk`, reject any other value, and log the resolved value. Extend `SGLB effective config` with:

```cpp
<< ", candidate policy "
<< FatTreeSwitch::sglb_candidate_policy_name()
```

- [ ] **Step 4: Restore paper-SGLB controls from the archive branch**

Port only the parser blocks shown by:

```bash
git show archive/remote-sync-and-paper-sglb:sim/datacenter/main_roce.cpp
```

for the seven listed paper switches. Preserve validation: K is at least 1, weights are in `[0,1]` with a positive sum, modes are enumerated, and unknown values exit 1. Do not change paper or current SGLB defaults.

- [ ] **Step 5: Run parser and focused C++ tests**

Run: `python3 -m pytest sim/tests/test_sglb_topk_matrix_cli.py -q && make -C sim/tests htsim_paper_sglb && sim/tests/htsim_paper_sglb`

Expected: pytest reports 2 passed and the C++ test exits 0.

- [ ] **Step 6: Commit**

```bash
git add sim/datacenter/main_roce.cpp sim/tests/test_sglb_topk_matrix_cli.py
git commit -m "feat: expose SGLB matrix experiment controls"
```

### Task 3: Reproducible 18-cell runner

**Files:**
- Create: `experiments/n-mrc/run_sglb_gcn_topk_matrix.py`
- Create: `sim/tests/test_sglb_gcn_topk_matrix_runner.py`

**Interfaces:**
- Produces: `build_cells() -> list[dict]` with 18 unique `(variant, policy)` cells.
- Produces: `parse_output(text: str) -> dict` for FCT and diagnostics.
- Produces: output tree containing `result.csv`, `result.json`, `report.md`, per-cell `command.txt`, `stdout.txt.gz`, and `metadata.json`.
- Consumes: the exact retained traffic matrix and simulator binary supplied through CLI options.

- [ ] **Step 1: Write failing manifest and parser tests**

The test imports the runner and asserts:

```python
cells = module.build_cells()
assert len(cells) == 18
assert len({(c["variant"], c["policy"]) for c in cells}) == 18
assert {c["policy"] for c in cells} == {
    "native", "strict_topk_8", "strict_topk_24"
}
assert all(c["expected_flows"] == 256 for c in cells)
```

It also feeds a minimal synthetic simulator log to `parse_output`, checks linear-interpolated p99 from 256 FCT samples, and asserts a missing effective-config line or fewer than 256 completions raises `ValueError`.

- [ ] **Step 2: Run tests to verify import failure**

Run: `python3 -m pytest sim/tests/test_sglb_gcn_topk_matrix_runner.py -q`

Expected: collection fails because `run_sglb_gcn_topk_matrix.py` does not exist.

- [ ] **Step 3: Implement the fixed cell manifest**

Define six variants with explicit commands:

```python
VARIANTS = {
    "original_sglb": {"lb": "sglb-old", "mode": "direct"},
    "sync_batched_gcn_15us": {"lb": "sglb-paper", "mode": "gcn-profile"},
    "gcn_traffic_live_remote": {
        "lb": "sglb-paper", "mode": "gcn-profile",
        "ablation": "legacy_transport",
    },
    "local_only": {"lb": "sglb-paper", "weights": [1, 0, 0, 0, 0]},
    "remote_gcn_only": {"lb": "sglb-paper", "weights": [0, 0, 1, 0, 0]},
    "async_real_gcn": {"lb": "sglb", "mode": "real_gcn_raw_linear"},
}
```

For strict policies pass the explicit candidate policy and K; for native use each variant's recorded native selector. Every cell references the same traffic path and expected digest.

- [ ] **Step 4: Implement execution, validation, and resumability**

Use `subprocess.run` without a shell. Write the exact argv before execution, gzip stdout after completion, and atomically update partial JSON after each cell. Validate binary/traffic digests, effective-config tokens, 256 completions, and real-GCN byte reconciliation. `--resume` skips only cells whose metadata and hashes validate.

- [ ] **Step 5: Implement report generation**

Write all 18 rows to CSV/JSON. Group by variant, calculate the minimum p99, and mark each per-variant winner in Markdown. Include workload settings, exact hashes, secondary diagnostics, invalid-cell reasons, the overall numeric minimum, and the single-seed caveat.

- [ ] **Step 6: Run runner tests**

Run: `python3 -m pytest sim/tests/test_sglb_gcn_topk_matrix_runner.py -q`

Expected: all runner tests pass.

- [ ] **Step 7: Commit**

```bash
git add experiments/n-mrc/run_sglb_gcn_topk_matrix.py sim/tests/test_sglb_gcn_topk_matrix_runner.py
git commit -m "feat: add reproducible SGLB TopK matrix runner"
```

### Task 4: Build and smoke-test the matrix

**Files:**
- Modify only if a verified defect is found in Tasks 1-3.

**Interfaces:**
- Consumes: experimental source and runner.
- Produces: one simulator binary whose SHA-256 is used by all 18 cells.

- [ ] **Step 1: Run the focused test suite**

Run:

```bash
python3 -m pytest sim/tests/test_sglb_topk_matrix_cli.py sim/tests/test_sglb_gcn_topk_matrix_runner.py -q
make -C sim/tests htsim_paper_sglb htsim_sglb_quality
sim/tests/htsim_paper_sglb
sim/tests/htsim_sglb_quality
```

Expected: all Python tests pass and both binaries exit 0.

- [ ] **Step 2: Build the simulator**

Run: `make -C sim/datacenter htsim_roce`

Expected: command exits 0 and `sim/datacenter/htsim_roce` exists.

- [ ] **Step 3: Run six short smoke cells**

Invoke the runner's `--smoke` mode once for each variant with a short horizon. Expected: each command logs the exact requested selector/mode/weights and exits without an unknown-option or assertion failure. Smoke results are stored separately and are not included in measured winners.

- [ ] **Step 4: Record the pre-run revision**

Run: `git status --short && git rev-parse HEAD && sha256sum sim/datacenter/htsim_roce`

Expected: source worktree is clean; revision and binary digest are captured in the runner manifest.

### Task 5: Execute and validate all 18 measured cells

**Files:**
- Create: `experiments/n-mrc/output/sglb_gcn_topk_three_policy_seed29/**` through the runner.

**Interfaces:**
- Consumes: one validated binary and the retained seed-29 traffic file.
- Produces: raw logs plus complete CSV, JSON, and Markdown deliverables.

- [ ] **Step 1: Verify the traffic digest**

Run:

```bash
sha256sum experiments/n-mrc/output/current_async_real_gcn_root_cause_seed29/traffic/n256_healthy_permutation_16mib_seed29.cm
```

Expected digest: `472a244b549667c5740d911b3fdd4ccece4d8c987e112350b77f67d0a6636a28`.

- [ ] **Step 2: Run the full matrix**

Run the runner with the built simulator, exact traffic file, output directory `experiments/n-mrc/output/sglb_gcn_topk_three_policy_seed29`, and `--resume`. Expected: 18 valid cells and no substitutions.

- [ ] **Step 3: Validate completeness and winner arithmetic**

Run the runner's `--validate-only` mode. Expected: 18 unique cells, 256/256 flows per cell, all hashes/configs valid, GCN counters reconciled where applicable, and each of six variants has exactly one lowest-p99 marker (ties are explicitly co-winners).

- [ ] **Step 4: Inspect the final report and machine-readable files**

Check that `report.md`, `result.csv`, and `result.json` agree on all p99 values and winners, and that the report states the single-seed limitation and the distinction between native minimum-choice and strict exact-K semantics.

### Task 6: Final verification and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-08-17-sglb-topk-three-policy-matrix.md` only to check completed steps if useful.

**Interfaces:**
- Produces: evidence-backed user handoff with clickable report and result links.

- [ ] **Step 1: Run final regression checks**

Run:

```bash
git diff --check
python3 -m pytest sim/tests/test_sglb_topk_matrix_cli.py sim/tests/test_sglb_gcn_topk_matrix_runner.py -q
make -C sim/tests htsim_paper_sglb htsim_sglb_quality
sim/tests/htsim_paper_sglb
sim/tests/htsim_sglb_quality
```

Expected: no whitespace errors, all Python tests pass, both focused C++ tests exit 0.

- [ ] **Step 2: Verify deliverable hashes and counts**

Run `--validate-only` once more and record its output. Expected: `valid_cells=18`, `invalid_cells=0`, and `variants_with_winner=6`.

- [ ] **Step 3: Commit experimental source and runner changes if uncommitted**

Commit only source, tests, runner, and compact report artifacts intended for version control; do not force-add bulky raw outputs ignored by repository policy.

- [ ] **Step 4: Report results**

Give the complete 18-cell p99 table, the best policy for each variant, the overall numeric minimum, the exact experimental settings and hashes, and links to the Markdown/CSV/JSON deliverables. Explicitly state that one seed is a controlled comparison, not a universal performance proof.
