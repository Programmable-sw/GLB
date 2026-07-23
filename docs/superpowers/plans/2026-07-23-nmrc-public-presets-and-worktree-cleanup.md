# N-MRC Public Presets and Worktree Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep canonical `n-mrc` unchanged, add `n-mrc-fixed0.5` and `n-mrc-delta` as tested public presets, document future mechanisms, consolidate ignored experiment data, and safely retire the completed ablation worktree.

**Architecture:** Reuse the already exercised preset implementation from `codex/nmrc-websearch-ablation-20260721`, but extract only the parser, decision policy, packet feedback metadata, and tests required by the two public presets. Preserve historical output directory names and copy ignored data independently of tracked source changes.

**Tech Stack:** C++ htsim simulator, Python interface tests, CMake/Make build, Git worktrees.

## Global Constraints

- `n-mrc` remains the default canonical four-level scheme with unchanged defaults.
- Public entries are exactly `n-mrc`, `n-mrc-fixed0.5`, and `n-mrc-delta`.
- `n-mrc-fixed0.5` defaults to absolute threshold `0.5` and relative delta `0.25`.
- `n-mrc-delta` defaults to relative delta `0.25`.
- Historical `n-mrc4` output names remain unchanged.
- Experiment output stays below ignored `experiments/n-mrc/output/` and must not be committed.
- Do not delete unrelated worktrees, branches, scripts, or unowned dirty changes.

---

### Task 1: Establish Failing Public-Preset Tests

**Files:**
- Create: `sim/tests/test_nmrc_public_presets.py`
- Modify: `sim/tests/test_nmrc_interface.py`

**Interfaces:**
- Consumes: `sim/datacenter/main_roce.cpp` command-line parser and `experiments/n-mrc/README.md`.
- Produces: assertions for the three public names, defaults, legacy-name removal, and option validation.

- [ ] **Step 1: Copy only the public-preset assertions from the ablation worktree**

Use the tested assertions that require `"n-mrc-fixed0.5"`, `"n-mrc-delta"`, `double nmrc_absolute_threshold = 0.50`, and `double nmrc_relative_delta = 0.25`; exclude tests for plotting and historical compatibility names.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 sim/tests/test_nmrc_public_presets.py
python3 sim/tests/test_nmrc_interface.py
```

Expected: failure because main currently has neither public preset string nor the new threshold configuration.

- [ ] **Step 3: Commit the failing tests**

```bash
git add sim/tests/test_nmrc_public_presets.py sim/tests/test_nmrc_interface.py
git commit -m "test: specify public nmrc presets"
```

### Task 2: Implement the Two Public Presets

**Files:**
- Modify: `sim/datacenter/main_roce.cpp`
- Modify: `sim/datacenter/fat_tree_switch.cpp`
- Modify: `sim/datacenter/fat_tree_switch.h`
- Modify: `sim/network.cpp`
- Modify: `sim/network.h`
- Modify: `sim/roce.cpp`
- Modify: `sim/roce.h`
- Modify: `sim/tests/main_sglb_quality.cpp`

**Interfaces:**
- Consumes: existing canonical N-MRC EV selection, FastCNP injection, endpoint cooldown, and SGLB quality score.
- Produces: `n-mrc-fixed0.5`, `n-mrc-delta`, `_nmrc_absolute_threshold`, `_nmrc_relative_delta`, and the preset-specific network decision.

- [ ] **Step 1: Extract the minimum source diff**

Take the relevant hunks from `.worktrees/nmrc-websearch-ablation-20260721`, retaining the current main branch’s canonical `n-mrc` actual-egress TRIM handling and retransmission reselection. Do not replace entire files.

- [ ] **Step 2: Run focused Python tests to verify GREEN**

Run:

```bash
python3 sim/tests/test_nmrc_public_presets.py
python3 sim/tests/test_nmrc_interface.py
```

Expected: both exit `0`.

- [ ] **Step 3: Build and run the quality selector tests**

Run the repository’s existing build target for `main_sglb_quality`, followed by the binary. Expected: compilation succeeds and all assertions exit `0`.

- [ ] **Step 4: Verify canonical defaults**

Compare canonical `n-mrc` configuration output before and after the source merge. It must still report the existing four-level policy and must not inherit `fixed0.5` or `delta` thresholds.

- [ ] **Step 5: Commit implementation**

```bash
git add sim/datacenter/main_roce.cpp sim/datacenter/fat_tree_switch.cpp sim/datacenter/fat_tree_switch.h sim/network.cpp sim/network.h sim/roce.cpp sim/roce.h sim/tests/main_sglb_quality.cpp
git commit -m "feat: add fixed and delta nmrc presets"
```

### Task 3: Update Root and Experiment Documentation

**Files:**
- Modify: `README.md`
- Modify: `experiments/n-mrc/README.md`

**Interfaces:**
- Consumes: the final parser names and default threshold values from Task 2.
- Produces: user-facing usage and future-design notes.

- [ ] **Step 1: Add the three-entry overview to root README**

Document canonical `n-mrc`, `n-mrc-fixed0.5`, and `n-mrc-delta`; add concise non-implemented notes for separate reroute/cooldown thresholds and piecewise delta below/above `0.5`.

- [ ] **Step 2: Reconcile the experiment README**

Preserve useful existing content from the dirty main copy and add exact commands, defaults, action coupling, and the historical `n-mrc4` mapping. Do not reintroduce old public names.

- [ ] **Step 3: Run documentation/interface tests**

Run:

```bash
python3 sim/tests/test_nmrc_public_presets.py
python3 sim/tests/test_nmrc_interface.py
git diff --check
```

Expected: both tests and whitespace check exit `0`.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md experiments/n-mrc/README.md
git commit -m "docs: document public nmrc variants"
```

### Task 4: Consolidate Ignored Experiment Outputs

**Files:**
- Copy into: `experiments/n-mrc/output/`
- Source: `.worktrees/nmrc-websearch-ablation-20260721/experiments/n-mrc/output/`
- Inspect other N-MRC worktree output directories before cleanup.

**Interfaces:**
- Consumes: historical output directories and plots.
- Produces: one non-destructive ignored output collection in the main workspace.

- [ ] **Step 1: Inventory source and destination by relative path, size, and checksum**

Generate manifests without modifying either tree. Identify collisions whose contents differ.

- [ ] **Step 2: Copy missing files without overwriting**

Use archive-preserving copy semantics. For a differing collision, copy under a source-labelled sibling path instead of replacing the destination.

- [ ] **Step 3: Verify the copy**

Regenerate manifests and check that every source checksum is represented in the destination collection.

- [ ] **Step 4: Verify ignored status**

Run:

```bash
git check-ignore -v experiments/n-mrc/output/
git status --short
```

Expected: output is matched by `/experiments/*/output*/`, and no experiment result appears as tracked or untracked status.

### Task 5: Final Verification and Safe Cleanup

**Files:**
- Remove only superseded temporary scripts confirmed to have no unique functionality.
- Remove worktree: `.worktrees/nmrc-websearch-ablation-20260721`
- Delete branch: `codex/nmrc-websearch-ablation-20260721` only after its unique content is merged or preserved.

**Interfaces:**
- Consumes: verified commits and copied data from Tasks 1–4.
- Produces: clean tracked main branch and no completed ablation worktree.

- [ ] **Step 1: Audit dirty and unique content**

Run `git status`, compare every dirty tracked file with main, list untracked files, and inspect `git log main-htsim..codex/nmrc-websearch-ablation-20260721`. Any unique useful item must be merged, copied, or retained.

- [ ] **Step 2: Run the full relevant verification suite**

Run focused Python interface tests, C++ SGLB/N-MRC quality tests, and a simulator build. Expected: all commands exit `0`.

- [ ] **Step 3: Remove the completed worktree safely**

Only after the audit has no unpreserved content, remove the registered worktree and then delete its local branch. Do not touch unrelated worktrees or branches.

- [ ] **Step 4: Verify final repository state**

Run:

```bash
git status --short --branch
git worktree list
git branch --format='%(refname:short) %(worktreepath)'
```

Expected: main has no unintended tracked/untracked changes; ignored experiment outputs remain on disk; the completed ablation worktree/branch is absent; unrelated worktrees remain.
