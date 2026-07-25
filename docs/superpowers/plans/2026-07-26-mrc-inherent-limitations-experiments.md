# MRC Inherent Limitations Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add controlled MRC diagnostics, MRC-shared feedback visibility, an independent active-EV control, and a reproducible runner that produces the three MRC limitation experiments.

**Architecture:** Keep transport and MRC state transitions in `RoceSrc`, adding one shared-event registry that disseminates only real endpoint feedback. Emit one per-flow diagnostic at completion and analyze it in a dedicated Python runner. RR remains the matched stateless selector for every active-EV setting.

**Tech Stack:** C++11 htsim/RoCE, Python 3 standard library, matplotlib, existing n-MRC traffic helpers and metrics parsers, CSV/JSON/Markdown.

## Global Constraints

- Initial implementation and full result matrix use 128 nodes and paths=8.
- Failure injection and 512-node reproduction are out of scope.
- RR and MRC must be identical before effective feedback for each active-EV setting.
- MRC-shared may share only real MRC feedback under `(source NIC, destination ToR, EV)`.
- Congestion control, transport, retransmission, sequence, cursor, and completion state remain per QP.
- The default active-EV count remains `min(path_space, 32)`.
- Every scheme comparison reuses a byte-identical traffic matrix.
- Missing or incomplete cells must block aggregate conclusions.

---

### Task 1: Independent Active-EV Control

**Files:**
- Modify: `sim/roce.h`
- Modify: `sim/roce.cpp`
- Modify: `sim/datacenter/main_roce.cpp`
- Modify: `sim/tests/main_roce_reps_mrc_semantics.cpp`
- Modify: `sim/tests/test_nmrc_interface.py`

**Interfaces:**
- Produces: `RoceSrc::setMrcActiveEvs(uint32_t)`,
  `RoceSrc::mrcActiveEvs()`, `RoceSrc::resolvedMrcActiveEvs(uint32_t)`.
- CLI: `-mrc_active_evs K`, valid only for `-lb mrc`,
  `-lb mrc-shared`, or `-lb rr`; K must be in `1..min(paths,32)`.

- [ ] **Step 1: Write failing C++ tests**

Add tests that set path space 8 and assert:

```cpp
RoceSrc::setMrcActiveEvs(0);
expect(src.mrc_desired_active_paths(8) == 8,
       "unset active-EV override must preserve the default");
RoceSrc::setMrcActiveEvs(4);
expect(src.mrc_desired_active_paths(8) == 4,
       "active-EV override must limit MRC active state");
```

Construct matched RR/MRC QPs with K=2,4,8 and compare four complete rotations.
Assert RR uses only the same K-EV prefix as MRC. Reset the static override to
zero at the end of every test.

- [ ] **Step 2: Verify RED**

Run:

```bash
make -C sim/tests htsim_roce_reps_mrc_semantics
```

Expected: compilation fails because the active-EV API is absent.

- [ ] **Step 3: Implement the minimal selector behavior**

Add static `_mrc_active_evs` and:

```cpp
static void setMrcActiveEvs(uint32_t value) { _mrc_active_evs = value; }
static uint32_t mrcActiveEvs() { return _mrc_active_evs; }
static uint32_t resolvedMrcActiveEvs(uint32_t path_space) {
    uint32_t limit = std::min(path_space ? path_space : 1U, 32U);
    return _mrc_active_evs ? std::min(_mrc_active_evs, limit) : limit;
}
```

Make `mrc_desired_active_paths` return the resolved value. In
`init_rr_paths`, build the full MRC order and resize it to the same resolved
active count.

- [ ] **Step 4: Add CLI validation**

Parse `-mrc_active_evs`, reject zero, reject values above
`min(path_space,32)` after topology resolution, and reject use with unrelated
schemes. Print the resolved value in MRC/RR canonical configuration lines.

- [ ] **Step 5: Verify GREEN and commit**

Run:

```bash
make -C sim -j"$(nproc)" libhtsim.a
make -C sim/tests htsim_roce_reps_mrc_semantics
sim/tests/htsim_roce_reps_mrc_semantics
python3 -m pytest sim/tests/test_nmrc_interface.py -q
```

Expected: all pass.

Commit:

```bash
git add sim/roce.h sim/roce.cpp sim/datacenter/main_roce.cpp \
  sim/tests/main_roce_reps_mrc_semantics.cpp sim/tests/test_nmrc_interface.py
git commit -m "feat: control MRC active EV count"
```

---

### Task 2: MRC-Shared Feedback Dissemination

**Files:**
- Modify: `sim/roce.h`
- Modify: `sim/roce.cpp`
- Modify: `sim/datacenter/main_roce.cpp`
- Modify: `sim/tests/main_roce_reps_mrc_semantics.cpp`
- Modify: `sim/tests/test_nmrc_interface.py`

**Interfaces:**
- Add `LB_MRC_SHARED` and CLI `-lb mrc-shared`.
- Produce:

```cpp
enum mrc_shared_signal_t {
    MRC_SHARED_ECN,
    MRC_SHARED_TRIM,
    MRC_SHARED_FAILURE
};
struct MrcSharedEvent {
    uint64_t generation;
    mrc_shared_signal_t signal;
    simtime_picosec published_at;
    flowid_t publisher_flow;
};
static void resetMrcSharedState();
void publish_mrc_shared(uint32_t ev, mrc_shared_signal_t signal);
void consume_mrc_shared();
```

- [ ] **Step 1: Write failing sharing tests**

Tests must create two QPs with the same source host and destination ToR and a
third QP outside the domain. Verify:

1. a real ECN transition in QP A publishes one generation;
2. QP B consumes it once before selection and cools the same EV;
3. QP C does not consume it;
4. consuming again does not rearm cooldown;
5. QP B's congestion-window, retransmit queue, ACK state, and inflight bytes
   do not change.

Add single-QP and no-feedback sequence equality tests for MRC and MRC-shared.

- [ ] **Step 2: Verify RED**

Run:

```bash
make -C sim/tests htsim_roce_reps_mrc_semantics
```

Expected: compilation fails because `LB_MRC_SHARED` and the registry API are
absent.

- [ ] **Step 3: Generalize MRC scheme checks**

Add:

```cpp
bool mrc_path_state_enabled() const {
    return _flow_lb_mode == LB_MRC ||
           _flow_lb_mode == LB_MRC_SHARED;
}
```

Use it in packet-EV tracking, ACK/NACK/RTO updates, cleanup, retransmission
selection, and start-flow reset. Do not include RR.

- [ ] **Step 4: Implement the registry and lazy consumption**

Key the static registry by:

```cpp
typedef std::tuple<uint32_t, uint32_t, uint32_t> MrcSharedKey;
```

where fields are source host, destination ToR, and logical EV. Each QP stores
the last consumed generation per EV. `consume_mrc_shared()` runs immediately
before new or retransmission MRC selection and applies the existing local
transition. Publication occurs only when `mrc_mark_congested` or
`mrc_mark_failed` returns true for a real local signal.

Change those transition methods to return `bool` without altering their
existing state effects.

- [ ] **Step 5: Add the public preset**

Map `-lb mrc-shared` to `LB_MRC_SHARED`, ECMP forwarding, and the same MRC
defaults as `-lb mrc`. Reset the shared registry once before each simulation.
Print:

```text
MrcSharedConfig enabled=1 key=source_nic,destination_tor,ev
```

- [ ] **Step 6: Verify GREEN and commit**

Run focused C++ and CLI tests plus the one-QP simulator smoke input for MRC and
MRC-shared. Require identical completion and path diagnostics when no feedback
occurs.

Commit:

```bash
git add sim/roce.h sim/roce.cpp sim/datacenter/main_roce.cpp \
  sim/tests/main_roce_reps_mrc_semantics.cpp sim/tests/test_nmrc_interface.py
git commit -m "feat: share MRC feedback across eligible QPs"
```

---

### Task 3: Per-Flow MRC Mechanism Diagnostics

**Files:**
- Modify: `sim/roce.h`
- Modify: `sim/roce.cpp`
- Modify: `sim/tests/main_roce_reps_mrc_semantics.cpp`
- Create: `sim/tests/test_mrc_flow_diag.py`

**Interfaces:**
- Produce exactly one `MrcFlowDiag` line at target-flow completion for RR,
  MRC, and MRC-shared.
- Produce `MrcFlowDiag` key/value fields listed in the design spec.

- [ ] **Step 1: Add a failing parser contract test**

Create a Python test with a representative line and require the exact field
set, integer/float conversion, and rejection of duplicate flow IDs or missing
fields.

- [ ] **Step 2: Add failing C++ counter-transition tests**

Test:

- first effective update timestamp and packets-before-update;
- an update with no later selection is not actionable;
- a later new-data selection consumes pending actionable updates;
- unique active EV count and first complete sweep;
- shared publication/consumption and redundant discovery counters.

- [ ] **Step 3: Implement diagnostic state**

Add a focused `MrcFlowMetrics` struct owned by each `RoceSrc`, with reset,
selection, effective-update, clean-feedback, shared-event, and emit methods.
Track packet send timestamps beside `_mrc_seq_ev` so feedback age uses the
actual triggering packet timestamp.

Call `emit_mrc_flow_diag()` immediately before `_done = true`. RR emits
selection/coverage values and zero feedback values.

- [ ] **Step 4: Verify and commit**

Run:

```bash
make -C sim -j"$(nproc)" libhtsim.a
make -C sim/tests htsim_roce_reps_mrc_semantics
sim/tests/htsim_roce_reps_mrc_semantics
python3 -m pytest sim/tests/test_mrc_flow_diag.py -q
```

Require one diagnostic for the one-QP RR, MRC, and MRC-shared smoke runs.

Commit:

```bash
git add sim/roce.h sim/roce.cpp \
  sim/tests/main_roce_reps_mrc_semantics.cpp \
  sim/tests/test_mrc_flow_diag.py
git commit -m "feat: emit per-flow MRC mechanism diagnostics"
```

---

### Task 4: Experiment Runner, Validation, and Aggregation

**Files:**
- Create: `experiments/n-mrc/run_mrc_inherent_limitations.py`
- Create: `sim/tests/test_mrc_inherent_limitations_runner.py`
- Modify: `experiments/n-mrc/README.md`

**Interfaces:**
- Runner subcommands: `smoke`, `run`, `report`.
- Outputs: `manifest.json`, `summary.csv`, `flow_metrics.csv`,
  `shared_key_metrics.csv`, figures, and Markdown report.

- [ ] **Step 1: Write failing runner tests**

Tests cover:

- canonical case counts and seeds;
- one traffic artifact reused by schemes in a paired block;
- CLI construction for RR, MRC, MRC-shared, and active EV 2/4/8;
- complete `MrcFlowDiag` parsing;
- duplicate/missing completion rejection;
- flow-ID pairing;
- slowdown threshold and exclusive mechanism classification;
- missing-cell rejection;
- geometric mean and seed min/max.

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m pytest sim/tests/test_mrc_inherent_limitations_runner.py -q
```

Expected: import failure because the runner is absent.

- [ ] **Step 3: Implement catalogs and commands**

Experiment 1 catalog:

```text
families=healthy,asymmetric
loads=40,60,80,100
seeds=13,29,47
schemes=rr,mrc
```

Experiment 2 catalog:

```text
family=asymmetric_alltoall
message=256MiB
parallel=4,8,16
seeds=13,29,47
schemes=mrc,mrc-shared
```

Experiment 3 catalog:

```text
family=asymmetric_websearch
loads=40,80
active_evs=2,4,8
seeds=13,29,47
schemes=rr,mrc
```

Use simulator/traffic/command SHA-256 fingerprints and atomic JSON/CSV writes.

- [ ] **Step 4: Implement parsing and classification**

Join paired flows on:

```python
(scenario, seed, flow_id, src, dst, flow_size)
```

Compute paired ratio, slowdown thresholds, feedback-opportunity bucket, and
the ordered exclusive classification:

```python
if new_selections_after_first_update <= 1:
    "too_late"
elif post_cooldown_first_clean:
    "stale_consistent"
elif max_simultaneous_cooling >= 2 and replacement_congestion:
    "capacity_removal"
elif forced_cooling_uses:
    "fallback_pressure"
else:
    "unexplained"
```

- [ ] **Step 5: Verify GREEN and commit**

Run runner unit tests and:

```bash
python3 experiments/n-mrc/run_mrc_inherent_limitations.py smoke \
  --out experiments/n-mrc/output/mrc_inherent_limitations_smoke
```

Require all smoke cells complete and all validation checks pass.

Commit runner, tests, and README documentation.

---

### Task 5: Full 128-Node Matrix and Report

**Files:**
- Create ignored raw/result artifacts under:
  `experiments/n-mrc/output/mrc_inherent_limitations_128/`
- Force-track final report:
  `experiments/n-mrc/output/mrc_inherent_limitations_128/mrc_inherent_limitations_report.md`

- [ ] **Step 1: Build the final simulator**

Run a clean simulator build and focused/adjacent tests.

- [ ] **Step 2: Run the complete matrix**

Run:

```bash
python3 experiments/n-mrc/run_mrc_inherent_limitations.py run \
  --seeds 13,29,47 --workers 3 --timeout 3600 \
  --out experiments/n-mrc/output/mrc_inherent_limitations_128
```

The runner resumes valid cells and refuses partial aggregation.

- [ ] **Step 3: Generate and audit the report**

Run:

```bash
python3 experiments/n-mrc/run_mrc_inherent_limitations.py report \
  --out experiments/n-mrc/output/mrc_inherent_limitations_128
```

Audit every claimed conclusion against `flow_metrics.csv` and
`shared_key_metrics.csv`. Label mixed or absent-signal results explicitly.

- [ ] **Step 4: Commit durable evidence**

Force-track the Markdown report and figure files referenced by it. Do not
track raw simulator logs.

- [ ] **Step 5: Final verification**

Run all new and adjacent tests, `git diff --check`, validate the complete
manifest, and reproduce at least one cell from a clean output directory.
