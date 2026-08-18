# MRC OCP SKIP_ONCE Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make plain `-lb mrc` the only public MRC congestion mode, fixed at 64 active EVs with OCP-style SKIP_ONCE behavior and at most one SKIP recovery per packet selection.

**Architecture:** Collapse the production MRC selector to one 64-EV state machine and remove policy/deadline/fallback configuration. Preserve only the dormant internal failure-state types and artificial transition tests; normal MRC traffic cannot enable failure recovery or probing. Align CLI diagnostics, active runners, and current documentation with that single model.

**Tech Stack:** C++ htsim simulator, Python `unittest` CLI/runner tests, CMake, Markdown.

## Global Constraints

- Each QP has exactly 64 EVs, all GOOD/active at initialization, with zero backup EVs.
- EV-to-path mapping is identity; deterministic per-QP shuffling changes traversal order only.
- Congestion behavior is only OCP SKIP_ONCE; `skip_rotation`, `one_cycle`, and `cwnd_scaled` are deleted.
- One packet-selection call may restore at most one SKIP EV to GOOD, and DATA may use only a GOOD EV.
- Duplicate feedback while an EV is SKIP is ignored and must not rearm or accumulate a token.
- Failure-recovery types and pure artificial transition tests remain, but no CLI exposes failure recovery and normal LOSS/RTO does not enter failure states.
- Existing experiment output files are user-owned historical artifacts and must not be edited or deleted.

---

### Task 1: Mark superseded design records

**Files:**
- Modify: `docs/superpowers/specs/2026-08-02-mrc-skip-policy-redesign.md`
- Modify: `docs/superpowers/plans/2026-08-02-mrc-skip-policy-redesign.md`

**Interfaces:**
- Consumes: `docs/superpowers/specs/2026-08-18-mrc-ocp-skip-once-consolidation-design.md`
- Produces: An explicit historical-record boundary so obsolete modes are not mistaken for current behavior.

- [ ] **Step 1: Add the supersession notice to both records**

Insert immediately below each title:

```markdown
> **历史记录：已被取代。** 当前实现与设计以
> [`2026-08-18-mrc-ocp-skip-once-consolidation-design.md`](../specs/2026-08-18-mrc-ocp-skip-once-consolidation-design.md)
> 为准；本文中的多策略、32 active EV、backup、cooldown deadline 与公开故障 CLI 不再是当前方案。
```

For the old plan, use `../specs/...`; for the old spec, use the local filename.

- [ ] **Step 2: Verify both notices resolve to the new design**

Run:

```bash
rg -n "历史记录：已被取代|2026-08-18-mrc-ocp" docs/superpowers/{specs,plans}/2026-08-02-mrc-skip-policy-redesign.md
```

Expected: each old record contains one supersession notice and one link to the 2026-08-18 design.

- [ ] **Step 3: Commit the record update**

```bash
git add -f docs/superpowers/specs/2026-08-02-mrc-skip-policy-redesign.md docs/superpowers/plans/2026-08-02-mrc-skip-policy-redesign.md
git commit -m "docs: supersede legacy MRC policy design"
```

### Task 2: Specify the single SKIP_ONCE state machine in tests

**Files:**
- Modify: `sim/tests/main_roce_mrc_skip_semantics.cpp`
- Modify: `sim/tests/test_mrc_skip_policy_cli.py`

**Interfaces:**
- Consumes: Existing `RoceSrc` MRC test helpers and `htsim_roce` command-line harness.
- Produces: Executable requirements for `choose_mrc_ev()` and the public CLI.

- [ ] **Step 1: Remove legacy policy cases and add the one-reset assertion**

Keep ECN/TRIM, duplicate-feedback, nominal-slot, DATA-on-GOOD, reliability-separation, and internal artificial failure-transition tests. Delete tests selecting `SKIP_ROTATION`, `ONE_CYCLE`, or `CWND_SCALED`. Replace the all-SKIP expectation with:

```cpp
for (uint16_t ev = 0; ev < 64; ++ev) {
    src.mrc_mark_congested(ev, MrcFeedbackCause::ECN);
}
const auto good_before = count_state(src, MRC_EV_GOOD);
const uint16_t selected = src.choose_mrc_ev();
assert(good_before == 0);
assert(count_state(src, MRC_EV_GOOD) == 1);
assert(src.mrc_ev_state(selected) == MRC_EV_GOOD);
assert(src.mrc_data_on_non_good_violations() == 0);
```

- [ ] **Step 2: Rewrite CLI expectations around plain `-lb mrc`**

Assert the default run reports these stable substrings:

```python
self.assertIn("MrcEvModel logical=64 active=64 backup=0", output)
self.assertIn("MrcPolicyDiag policy=skip_once", output)
self.assertIn("failure_recovery=0", output)
```

For each removed flag, run the binary and assert a nonzero return code:

```python
REMOVED_FLAGS = [
    "-mrc_congestion_policy", "-mrc_cooldown_mode",
    "-mrc_cooldown_reference_pkts", "-mrc_all_cooling_fallback",
    "-mrc_active_evs", "-mrc_failed_retry_us",
    "-mrc_probe_interval_pkts", "-mrc_failure_recovery",
    "-mrc_probe_success_threshold",
]
```

- [ ] **Step 3: Run tests and record the expected red state**

Run:

```bash
cmake --build sim/build -j2 --target main_roce_mrc_skip_semantics htsim_roce
./sim/build/main_roce_mrc_skip_semantics
python3 sim/tests/test_mrc_skip_policy_cli.py
```

Expected before production changes: the all-SKIP one-reset assertion fails and removed CLI flags are still accepted.

### Task 3: Collapse the C++ MRC implementation to SKIP_ONCE

**Files:**
- Modify: `sim/roce.h`
- Modify: `sim/roce.cpp`
- Modify: `sim/datacenter/main_roce.cpp`

**Interfaces:**
- Consumes: `MRC_EV_GOOD`, `MRC_EV_SKIP`, `MRC_EV_ASSUMED_BAD`, `MRC_EV_PROBING`, and existing feedback attribution.
- Produces: `RoceSrc::choose_mrc_ev()` with a fixed 64-EV SKIP_ONCE selector and a CLI with no MRC policy/failure knobs.

- [ ] **Step 1: Remove policy, cooldown, fallback, active/backup configuration declarations**

Delete the policy/cooldown/fallback enums, their name/setter/parser helpers, deadline/reference members, legacy active/backup counters, and branches that exist only for deleted modes. Define the canonical EV count once:

```cpp
static constexpr uint16_t MRC_EV_COUNT = 64;
```

Retain failure-state types and the test-only/internal transition hooks needed by artificial failure tests, but do not retain command-line wiring.

- [ ] **Step 2: Implement a one-reset selector**

Make `choose_mrc_ev()` scan the QP rotation with a local `restored_ev` sentinel. On the first SKIP, clear its token and set it GOOD without selecting it at that nominal encounter. Do not mutate later SKIP entries in the same call. Select the first pre-existing GOOD; if a full rotation has none, wrap to the one restored GOOD EV. Assert or count a violation if DATA would use a non-GOOD state.

Core control flow:

```cpp
std::optional<uint16_t> restored_ev;
for (uint16_t scanned = 0; scanned < MRC_EV_COUNT; ++scanned) {
    const uint16_t ev = next_rotation_ev();
    if (_mrc_evs[ev].state == MRC_EV_GOOD) return ev;
    if (_mrc_evs[ev].state == MRC_EV_SKIP && !restored_ev) {
        _mrc_evs[ev].state = MRC_EV_GOOD;
        _mrc_evs[ev].skip_pending = false;
        restored_ev = ev;
    }
}
if (restored_ev) return select_restored_ev_after_wrap(*restored_ev);
```

The concrete implementation must preserve the existing cursor and accounting semantics while guaranteeing only one SKIP→GOOD transition.

- [ ] **Step 3: Delete public CLI parsing and print fixed diagnostics**

Remove all nine flags from help, parsing, validation, and setter calls. Reject non-64 MRC path topology as before. Emit:

```text
MrcEvModel logical=64 active=64 backup=0
MrcPolicyDiag policy=skip_once failure_recovery=0
```

- [ ] **Step 4: Build and run the focused tests to green**

Run:

```bash
cmake --build sim/build -j2 --target main_roce_mrc_skip_semantics htsim_roce
./sim/build/main_roce_mrc_skip_semantics
python3 sim/tests/test_mrc_skip_policy_cli.py
```

Expected: both test programs pass; every removed flag is rejected.

- [ ] **Step 5: Commit the state-machine consolidation**

```bash
git add sim/roce.h sim/roce.cpp sim/datacenter/main_roce.cpp sim/tests/main_roce_mrc_skip_semantics.cpp sim/tests/test_mrc_skip_policy_cli.py
git commit -m "refactor: consolidate MRC on OCP skip once"
```

### Task 4: Remove obsolete MRC comparisons without touching n-MRC

**Files:**
- Modify or delete obsolete MRC-only tests under `sim/tests/`
- Modify active Python runners under `experiments/n-mrc/`
- Modify: `sim/tests/CMakeLists.txt`

**Interfaces:**
- Consumes: Plain `-lb mrc` CLI from Task 3.
- Produces: Active tests and runners that never pass a deleted MRC flag; n-MRC cooldown experiments remain unchanged.

- [ ] **Step 1: Inventory references with ownership context**

Run:

```bash
rg -n "mrc_(congestion_policy|cooldown_mode|cooldown_reference_pkts|all_cooling_fallback|active_evs|failed_retry_us|probe_interval_pkts|failure_recovery|probe_success_threshold)|skip_rotation|one_cycle|cwnd_scaled" sim/tests experiments/n-mrc --glob '!output/**'
```

Classify each hit as MRC or n-MRC. Only MRC hits are removed; strings and options belonging to n-MRC stay.

- [ ] **Step 2: Delete MRC-only policy comparison tests and runner arguments**

Delete tests whose sole purpose is comparing MRC cooldown policies, including the BDP/reference/cooldown sweep tests. Remove deleted MRC arguments from retained runner command construction and update their assertions to expect plain `-lb mrc`.

- [ ] **Step 3: Run the retained runner/interface tests**

Run the exact retained files reported by the inventory, for example:

```bash
python3 -m unittest discover -s sim/tests -p 'test_*mrc*runner.py'
python3 sim/tests/test_nmrc_interface.py
```

Expected: retained MRC runner tests and n-MRC interface tests pass.

- [ ] **Step 4: Commit runner/test cleanup**

```bash
git add sim/tests experiments/n-mrc
git commit -m "test: remove obsolete MRC policy comparisons"
```

### Task 5: Align current documentation with the fixed model

**Files:**
- Modify: `README.md`
- Modify: `MRC_IMPLEMENTATION.md`
- Modify: `experiments/n-mrc/README.md`
- Modify: current MRC audit/experiment notes returned by the reference scan

**Interfaces:**
- Consumes: Fixed runtime behavior and diagnostics from Tasks 3–4.
- Produces: Current documentation that states 64 active EVs, zero backups, SKIP_ONCE, no public failure interface.

- [ ] **Step 1: Rewrite current behavior sections**

Use these exact facts consistently:

```text
64 EV per QP
64 active / 0 backup
identity EV-to-path mapping
OCP SKIP_ONCE
duplicate SKIP feedback does not rearm
at most one SKIP recovery per packet selection
failure recovery unavailable from CLI and disabled in normal runs
```

Remove example commands containing any deleted flag.

- [ ] **Step 2: Scan current sources and docs for stale claims**

Run:

```bash
rg -n "32 active|backup EV|skip_rotation|one_cycle|cwnd_scaled|mrc_congestion_policy|mrc_cooldown|mrc_all_cooling|mrc_failure_recovery" README.md MRC_IMPLEMENTATION.md experiments/n-mrc/README.md sim experiments/n-mrc --glob '!output/**'
```

Expected: no production/current MRC claim or command uses a deleted concept; any remaining hit is explicitly historical or belongs to n-MRC.

- [ ] **Step 3: Commit documentation alignment**

```bash
git add README.md MRC_IMPLEMENTATION.md experiments/n-mrc/README.md
git add -f docs/superpowers
git commit -m "docs: align MRC guidance with skip once"
```

### Task 6: Full verification and scope audit

**Files:**
- Verify only; do not modify historical output artifacts.

**Interfaces:**
- Consumes: All prior tasks.
- Produces: Evidence that the consolidated MRC implementation passes focused and regression checks without changing unrelated load balancers.

- [ ] **Step 1: Build all simulator tests**

Run:

```bash
cmake --build sim/build -j2
```

Expected: exit status 0.

- [ ] **Step 2: Run CTest and Python MRC regressions**

Run:

```bash
ctest --test-dir sim/build --output-on-failure
python3 -m unittest discover -s sim/tests -p 'test_*mrc*.py'
```

Expected: all selected tests pass.

- [ ] **Step 3: Prove obsolete production tokens are absent**

Run:

```bash
rg -n "skip_rotation|one_cycle|cwnd_scaled|mrc_congestion_policy|mrc_cooldown_mode|mrc_all_cooling_fallback" sim/roce.h sim/roce.cpp sim/datacenter/main_roce.cpp
```

Expected: no matches.

- [ ] **Step 4: Review the final diff without including user-owned deletions**

Run:

```bash
git diff --stat HEAD~4..HEAD
git status --short
```

Expected: intended source/test/doc changes are committed; pre-existing deleted `experiments/n-mrc/output/**` files remain unstaged and untouched.
