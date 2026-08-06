# Canonical 64-Spine Topology Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make 256 nodes the default and restrict generated two-tier topologies to six canonical 64-Spine scales.

**Architecture:** `main_roce.cpp` validates generated two-tier node counts before topology construction and selects the existing fixed-radix constructor. `test_standard_leaf_spine_topology.py` exercises the binary as the public interface, covering the default, every supported scale, and invalid-scale rejection.

**Tech Stack:** C++11, Python 3 subprocess tests, GNU Make

## Global Constraints

- Default node count is exactly 256.
- Supported generated two-tier node counts are exactly 256, 512, 1024, 2048, 4096, and 8192.
- Every supported scale uses 64 Spines, 64 hosts per Leaf, and 64 uplinks per Leaf.
- Explicit topology files and generated three-tier topologies retain existing behavior.
- Implementation changes remain unstaged and uncommitted for direct `git add .` handoff.

---

### Task 1: Public topology regression contract

**Files:**
- Modify: `sim/tests/test_standard_leaf_spine_topology.py`

**Interfaces:**
- Consumes: `sim/datacenter/htsim_roce` command-line interface and its `Standard 2-tier leaf-spine` diagnostic.
- Produces: regression coverage for the default scale, supported scales, and rejected scales.

- [ ] **Step 1: Write the failing tests**

Change the runner so `-nodes` is optional, verify the default diagnostic is 256 nodes and 4 Leaves, enumerate all six supported node/Leaf pairs, and assert that 128 and 432 exit nonzero with a diagnostic listing all supported values.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 tests/test_standard_leaf_spine_topology.py` from `sim/`.

Expected: FAIL because the current default is 432 and unsupported scales still use the legacy generated topology.

- [ ] **Step 3: Keep the test focused on observable behavior**

Use real binary invocations with empty traffic matrices. Do not inspect source text or mock topology construction.

### Task 2: Canonical generated topology selection

**Files:**
- Modify: `sim/datacenter/main_roce.cpp:50`
- Modify: `sim/datacenter/main_roce.cpp:3375`

**Interfaces:**
- Consumes: parsed `no_of_nodes`, `tiers`, and the absence of an explicit topology file.
- Produces: `bool is_canonical_two_tier_scale(uint32_t nodes)` and a fixed-radix topology selection or actionable nonzero exit.

- [ ] **Step 1: Change the default**

Set `DEFAULT_NODES` to 256.

- [ ] **Step 2: Add exact scale validation**

Add a small helper whose switch accepts only 256, 512, 1024, 2048, 4096, and 8192. Before generated two-tier construction, reject any other value and print all supported values.

- [ ] **Step 3: Select fixed radix for every accepted scale**

For accepted generated two-tier runs, call `FatTreeTopology::set_two_tier_leaf_spine_radix(64)` unconditionally. Leave explicit topology files and three-tier construction untouched.

- [ ] **Step 4: Run the topology test to verify it passes**

Run: `make -C datacenter -j2 htsim_roce && python3 tests/test_standard_leaf_spine_topology.py` from `sim/`.

Expected: PASS for the default and all six scales; invalid values exit nonzero with the supported-scale diagnostic.

### Task 3: Regression and handoff hygiene

**Files:**
- Verify: `sim/datacenter/main_roce.cpp`
- Verify: `sim/tests/test_standard_leaf_spine_topology.py`

**Interfaces:**
- Consumes: the completed canonical topology behavior.
- Produces: verified, unstaged implementation changes ready for `git add .`.

- [ ] **Step 1: Run focused topology and load-balancing tests**

Run the topology test plus MRC skip-token, MRC/RR semantics, SGLB quality, SGLB defaults, and the two MRC/SGLB runner tests.

- [ ] **Step 2: Run build and diff checks**

Run `make -j2`, `git diff --check`, and inspect `git status --short` plus `git diff --stat`.

- [ ] **Step 3: Leave changes unstaged**

Confirm `git diff --cached --quiet` succeeds and report the exact files ready for `git add .`.
