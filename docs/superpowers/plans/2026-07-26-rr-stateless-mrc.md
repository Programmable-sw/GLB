# RR as Stateless MRC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make endpoint RR use exactly the same deterministic healthy-state EV/path order as MRC, then verify the equivalence with unit tests and healthy 128-node simulations.

**Architecture:** Extract MRC's current per-QP deterministic permutation into one shared order builder. MRC continues to layer active/cooling/failed/probing state over that order, while RR owns only an order vector and cursor. No feedback or quality state is added to RR.

**Tech Stack:** C++11 htsim/RoCE simulator, existing C++ semantic-test binary, Python traffic helpers, Bash validation commands, Markdown result report.

## Global Constraints

- Strict RR/MRC sequence equivalence applies to new data packets with at most 32 candidate EVs and before any MRC state-changing feedback.
- EV `i` must continue to map to physical path `i`.
- RR must not construct or update MRC quality state.
- MRC feedback, cooldown, failure, probing, backup promotion, retransmission, and congestion-control semantics must remain unchanged.
- Do not change `ecmp_rr`, OPS, REPS, N-MRC, STOR, NetAware, topology construction, queue configuration, or traffic generation.
- The retained multi-QP smoke workload is `healthy_permutation_4mib`, 128 nodes, seed 13, using the already-generated traffic matrix.

---

### Task 1: Lock the Stateless Equivalence Contract with Failing Tests

**Files:**
- Modify: `sim/tests/main_roce_reps_mrc_semantics.cpp`

**Interfaces:**
- Consumes: `RoceSrc::choose_path_for_test(Packet::PktPriority, bool)`, `RoceSrc::choose_mrc_ev(uint32_t)`, and the test file's existing `#define private public` access.
- Produces: `test_rr_matches_healthy_mrc_order()` and `test_rr_diverges_only_after_mrc_feedback()`.

- [ ] **Step 1: Add helpers that construct matched RR and MRC sources**

Add the following helpers after `reset_mrc_config`:

```cpp
static void configure_identity(RoceSrc& src, uint32_t source,
                               uint32_t destination, uint32_t flow_id) {
    src.set_src(source);
    src.set_dst(destination);
    src.set_flowid(flow_id);
    src._flow_started = true;
}

static std::vector<uint32_t> select_paths(RoceSrc& src,
                                          RoceSrc::lb_mode_t mode,
                                          uint32_t count) {
    src._flow_lb_mode = mode;
    std::vector<uint32_t> result;
    for (uint32_t i = 0; i < count; i++)
        result.push_back(
            src.choose_path_for_test(Packet::PRIO_LO, false));
    return result;
}
```

- [ ] **Step 2: Add the healthy-order equivalence test**

Add a test that checks four full rotations for 8 and 16 paths and checks two
different QP identities:

```cpp
static void test_rr_matches_healthy_mrc_order() {
    for (uint32_t paths = 8; paths <= 16; paths *= 2) {
        reset_mrc_config(paths);
        for (uint32_t flow_id = 801; flow_id <= 802; flow_id++) {
            RoceSrc rr(NULL, NULL, test_eventlist(),
                       speedFromMbps((uint64_t)100000));
            RoceSrc mrc(NULL, NULL, test_eventlist(),
                        speedFromMbps((uint64_t)100000));
            configure_identity(rr, 11, 27, flow_id);
            configure_identity(mrc, 11, 27, flow_id);

            std::vector<uint32_t> rr_paths =
                select_paths(rr, RoceSrc::LB_RR, paths * 4);
            std::vector<uint32_t> mrc_paths =
                select_paths(mrc, RoceSrc::LB_MRC, paths * 4);
            expect(rr_paths == mrc_paths,
                   "RR must match healthy MRC EV/path order exactly");
        }
    }

    reset_mrc_config(8);
    RoceSrc first(NULL, NULL, test_eventlist(),
                  speedFromMbps((uint64_t)100000));
    RoceSrc second(NULL, NULL, test_eventlist(),
                   speedFromMbps((uint64_t)100000));
    configure_identity(first, 11, 27, 801);
    configure_identity(second, 11, 27, 802);
    expect(select_paths(first, RoceSrc::LB_RR, 8) !=
               select_paths(second, RoceSrc::LB_RR, 8),
           "different QP identities should retain distinct permutations");
}
```

- [ ] **Step 3: Add the intended-divergence test**

Add a test that synchronizes both schemes for one rotation, cools the next MRC
EV, and proves that only MRC skips it:

```cpp
static void test_rr_diverges_only_after_mrc_feedback() {
    reset_mrc_config(8);
    RoceSrc rr(NULL, NULL, test_eventlist(),
               speedFromMbps((uint64_t)100000));
    RoceSrc mrc(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    configure_identity(rr, 11, 27, 803);
    configure_identity(mrc, 11, 27, 803);

    expect(select_paths(rr, RoceSrc::LB_RR, 8) ==
               select_paths(mrc, RoceSrc::LB_MRC, 8),
           "matched schemes must complete the first healthy rotation");

    uint32_t cooled = mrc._mrc_active[mrc._mrc_active_cursor];
    mrc.mrc_mark_congested(cooled, RoceSrc::MRC_CONGESTION_ECN);
    uint32_t rr_next =
        select_paths(rr, RoceSrc::LB_RR, 1).front();
    uint32_t mrc_next =
        select_paths(mrc, RoceSrc::LB_MRC, 1).front();
    expect(rr_next == cooled,
           "stateless RR must keep the common healthy order");
    expect(mrc_next != cooled,
           "MRC must skip the EV after effective congestion feedback");
}
```

Call both tests from `main()` immediately after
`test_mrc_deterministic_active_subset_and_unique_backups()`.

- [ ] **Step 4: Build and run the test to verify the healthy equivalence fails**

Run:

```bash
make -C sim -j"$(nproc)" libhtsim.a
make -C sim/tests htsim_roce_reps_mrc_semantics
sim/tests/htsim_roce_reps_mrc_semantics
```

Expected: the binary exits nonzero at
`RR must match healthy MRC EV/path order exactly`, because RR still uses its
affine selector.

- [ ] **Step 5: Commit the failing contract test**

```bash
git add sim/tests/main_roce_reps_mrc_semantics.cpp
git commit -m "test: require RR to match healthy MRC order"
```

---

### Task 2: Share the Deterministic EV Order without Sharing MRC State

**Files:**
- Modify: `sim/roce.h`
- Modify: `sim/roce.cpp`
- Test: `sim/tests/main_roce_reps_mrc_semantics.cpp`

**Interfaces:**
- Produces: `std::vector<uint32_t> build_mrc_ev_order(uint32_t path_space) const`, `void reset_rr_paths()`, `void init_rr_paths(uint32_t path_space)`, and `uint32_t choose_rr_path(uint32_t path_space)`.
- Internal RR state: `_rr_evs`, `_rr_cursor`, `_rr_path_space`, and `_rr_paths_ready`.
- Consumes: existing `selector_mix`, source/destination addresses, flow ID, and MRC encoded identity mapping.

- [ ] **Step 1: Declare the common order and stateless RR selector**

In `sim/roce.h`, next to the current MRC selector declarations, add:

```cpp
std::vector<uint32_t> build_mrc_ev_order(uint32_t path_space) const;
void reset_rr_paths();
void init_rr_paths(uint32_t path_space);
uint32_t choose_rr_path(uint32_t path_space);
```

Next to the MRC path fields, add:

```cpp
std::vector<uint32_t> _rr_evs;
uint32_t _rr_cursor;
uint32_t _rr_path_space;
bool _rr_paths_ready;
```

- [ ] **Step 2: Initialize and reset RR's selector-only state**

In the `RoceSrc` constructor, initialize:

```cpp
_rr_cursor = 0;
_rr_path_space = 0;
_rr_paths_ready = false;
```

In `RoceSrc::startflow`, reset RR alongside the existing scheme-specific
resets:

```cpp
if (_flow_lb_mode == LB_RR)
    reset_rr_paths();
```

- [ ] **Step 3: Extract MRC's deterministic shuffle**

Before `reset_mrc_paths` in `sim/roce.cpp`, add:

```cpp
std::vector<uint32_t> RoceSrc::build_mrc_ev_order(
        uint32_t path_space) const {
    if (path_space == 0)
        path_space = 1;
    std::vector<uint32_t> ids(path_space);
    for (uint32_t i = 0; i < path_space; i++)
        ids[i] = i;

    uint32_t src = _srcaddr == UINT32_MAX ? _node_num : _srcaddr;
    uint32_t dst = _dstaddr == UINT32_MAX ?
        (_node_num ^ 0x5bd1e995) : _dstaddr;
    uint32_t seed =
        selector_mix(src, dst, _flow.flow_id() ^ 0x4d524300);
    for (uint32_t i = 0; i < path_space; i++) {
        uint32_t remaining = path_space - i;
        uint32_t ix = i +
            (selector_mix(seed, i, _flow.flow_id()) % remaining);
        std::swap(ids[i], ids[ix]);
    }
    return ids;
}
```

Replace the local `ids` construction and shuffle in `init_mrc_paths` with:

```cpp
std::vector<uint32_t> ids = build_mrc_ev_order(logical_count);
```

- [ ] **Step 4: Implement the RR selector-only state**

Add:

```cpp
void RoceSrc::reset_rr_paths() {
    _rr_evs.clear();
    _rr_cursor = 0;
    _rr_path_space = 0;
    _rr_paths_ready = false;
}

void RoceSrc::init_rr_paths(uint32_t path_space) {
    if (path_space == 0)
        path_space = 1;
    if (_rr_paths_ready && _rr_path_space == path_space)
        return;
    _rr_evs = build_mrc_ev_order(path_space);
    _rr_cursor = 0;
    _rr_path_space = path_space;
    _rr_paths_ready = true;
}

uint32_t RoceSrc::choose_rr_path(uint32_t path_space) {
    init_rr_paths(path_space);
    uint32_t selected = _rr_evs[_rr_cursor];
    _rr_cursor = (_rr_cursor + 1) % _rr_evs.size();
    return selected % path_space;
}
```

- [ ] **Step 5: Route public RR through the common order**

Replace the existing `LB_RR` affine selection block in `choose_path` with:

```cpp
if (_flow_lb_mode == LB_RR) {
    uint32_t path = choose_rr_path(path_space);
    record_path_selection(path, path);
    return path;
}
```

Do not remove `init_selector_priority` or its fields because STOR and NetAware
still consume the affine selector.

- [ ] **Step 6: Build and run the focused semantic test**

Run:

```bash
make -C sim -j"$(nproc)" libhtsim.a
make -C sim/tests htsim_roce_reps_mrc_semantics
sim/tests/htsim_roce_reps_mrc_semantics
```

Expected: exit code 0 with no output.

- [ ] **Step 7: Run adjacent selector tests**

Run:

```bash
make -C sim/tests htsim_nmrc_shuffled_bucket htsim_stor_feedback
sim/tests/htsim_nmrc_shuffled_bucket
sim/tests/htsim_stor_feedback
```

Expected: both binaries exit 0. These tests guard the affine selector users
that RR no longer invokes.

- [ ] **Step 8: Commit the implementation**

```bash
git add sim/roce.h sim/roce.cpp
git commit -m "feat: make RR the stateless MRC selector"
```

---

### Task 3: Document the Controlled Baseline

**Files:**
- Modify: `experiments/n-mrc/README.md`
- Modify: `MRC_IMPLEMENTATION.md`

**Interfaces:**
- Consumes: the exact behavior implemented in Task 2.
- Produces: public documentation of RR/MRC equivalence and its validity boundary.

- [ ] **Step 1: Replace the RR description**

Replace the existing `## rr` paragraph in `experiments/n-mrc/README.md` with:

```markdown
Source 端 RR 是 MRC 的无状态对照。RR 与 MRC 使用相同的 per-QP
确定性 EV permutation、相同游标起点以及 encoded identity EV→path
映射；RR 不维护 ACTIVE/COOLING/FAILED/PROBING 路径质量状态，也不根据
ACK/ECN/NACK/RTO 改变候选集合。因此在 EV 数不超过 MRC 的 32 个 active
EV 上限、且 MRC 尚未收到有效状态更新时，两者对新 data packet 的 EV/path
序列逐包一致。MRC 收到有效反馈后可以跳过或替换 EV，RR 则继续原始轮转。
```

- [ ] **Step 2: Add the baseline statement to the MRC implementation document**

In the section that describes MRC EV selection in `MRC_IMPLEMENTATION.md`, add:

```markdown
### Stateless control

`-lb rr` reuses MRC's deterministic per-QP EV order and encoded identity
mapping but does not own or update MRC path-quality state. It is therefore the
controlled no-learning baseline for MRC experiments. Exact packet-order
equivalence applies before the first effective MRC state update and while the
candidate set does not exceed the 32-active-EV limit.
```

- [ ] **Step 3: Check documentation consistency**

Run:

```bash
rg -n "stateless|无状态|32.*active|32 个 active" \
  experiments/n-mrc/README.md MRC_IMPLEMENTATION.md
git diff --check
```

Expected: both documents describe RR as the stateless MRC control, mention the
32-active-EV boundary, and `git diff --check` prints nothing.

- [ ] **Step 4: Commit the documentation**

```bash
git add experiments/n-mrc/README.md MRC_IMPLEMENTATION.md
git commit -m "docs: describe RR as stateless MRC"
```

---

### Task 4: Build and Run the Healthy Validation

**Files:**
- Create ignored raw results under: `experiments/n-mrc/output/rr_stateless_mrc_healthy_seed13/`
- Create and force-track: `experiments/n-mrc/output/rr_stateless_mrc_healthy_seed13/rr_stateless_mrc_validation.md`

**Interfaces:**
- Consumes: `sim/datacenter/htsim_roce`, `feedback_eval_common.write_traffic_matrix`, `PathSelectDiag`, `RoceDiag`, `QueueDiag`, and the retained healthy permutation traffic matrix.
- Produces: raw RR/MRC stdout plus one validation report.

- [ ] **Step 1: Build the simulator from the modified source**

Run:

```bash
make -C sim -j"$(nproc)"
make -C sim/datacenter -j"$(nproc)" htsim_roce
```

Expected: both commands exit 0 and relink `sim/datacenter/htsim_roce`.

- [ ] **Step 2: Generate the lightweight healthy traffic matrix**

Run:

```bash
mkdir -p experiments/n-mrc/output/rr_stateless_mrc_healthy_seed13/light
python3 -c 'import sys; from pathlib import Path; sys.path.insert(0, "experiments/n-mrc"); import feedback_eval_common as c; c.write_traffic_matrix(Path(sys.argv[1]), c.TOPOLOGIES[128], [c.Flow(0, 9, 256 * 1024)])' \
  experiments/n-mrc/output/rr_stateless_mrc_healthy_seed13/light/oneflow.cm
```

Expected: `oneflow.cm` declares 128 nodes and one 256 KiB connection.

- [ ] **Step 3: Run RR and MRC on the lightweight input**

For each `scheme` in `rr mrc`, run:

```bash
sim/datacenter/htsim_roce \
  -o experiments/n-mrc/output/rr_stateless_mrc_healthy_seed13/light/${scheme}.dat \
  -tm experiments/n-mrc/output/rr_stateless_mrc_healthy_seed13/light/oneflow.cm \
  -nodes 128 -conns 1 -tiers 2 -lb "${scheme}" \
  -linkspeed 400000 -queue_type composite_ecn_lb \
  -host_queue_type prio -mtu 4096 -end 1000 -paths 8 -seed 13 \
  -cc dcqcn_variant -roce_rx_mode sp -roce_sack_bitmap_bits 64 \
  -roce_transport_semantics mrc_exact_bounded \
  -roce_trim_recovery exact -hop_latency 0.5 -switch_latency 0.5 \
  > experiments/n-mrc/output/rr_stateless_mrc_healthy_seed13/light/${scheme}.stdout \
  2>&1
```

Expected: both commands exit 0.

- [ ] **Step 4: Verify strict lightweight equivalence**

Extract `Flow Roce_`, `RoceDiag`, `PathSelectDiag`, and `QueueDiag` from both
stdout files. Acceptance requires:

```text
acks=64
nacks=0
rtos=0
ecn_echo_acks=0
composite_trims=0
composite_drops=0
composite_ecn_marks=0
selected_total=64
unique_evs=8
unique_physical_mods=8
```

The complete flow-finish line, `first128`, `ev_hist_top`, and
`physical_mod_hist` values must match between RR and MRC.

- [ ] **Step 5: Run the retained multi-QP healthy workload**

Use:

```text
experiments/n-mrc/output/nmrc_communication_p2p_128/raw/simple/nodes_128/seed_13/healthy_permutation_4mib/mrc/traffic.cm
```

Run RR and MRC with the same options as the lightweight case except:

```text
-conns 128
-end 10000
```

Write outputs under:

```text
experiments/n-mrc/output/rr_stateless_mrc_healthy_seed13/healthy_permutation_4mib/{rr,mrc}.{dat,stdout}
```

Expected: both commands exit 0 and exactly 128 flows complete once.

- [ ] **Step 6: Classify and explain any realistic-workload divergence**

Compare:

- the 128 completion lines and p50/p95/p99/max FCT;
- `PathSelectDiag`;
- `MrcPathStateDiag`, including ECN/TRIM/failure state events;
- `RoceDiag` and `QueueDiag`.

If MRC has no state-changing event, require exact selection and FCT equality.
If MRC has a state-changing event, report that RR/MRC divergence is expected
after that event and quantify the resulting FCT difference; do not label the
pair as a failed healthy-order validation.

- [ ] **Step 7: Write and commit the validation report**

Write
`experiments/n-mrc/output/rr_stateless_mrc_healthy_seed13/rr_stateless_mrc_validation.md`
with:

- pre-change sequences: RR `0/3/6/1/4/7/2/5`, MRC
  `6/7/5/2/4/0/3/1`;
- post-change lightweight sequences and equality checks;
- all health counters;
- realistic-workload FCT statistics and feedback classification;
- conclusion: whether RR is a valid stateless MRC control.

Then run:

```bash
git add -f \
  experiments/n-mrc/output/rr_stateless_mrc_healthy_seed13/rr_stateless_mrc_validation.md
git commit -m "analysis: validate RR as stateless MRC"
```

---

### Task 5: Final Regression and Evidence Check

**Files:**
- Verify all files modified in Tasks 1–4.

**Interfaces:**
- Consumes: all previous task outputs.
- Produces: final verified branch state and user-facing evidence.

- [ ] **Step 1: Run focused and adjacent tests from a clean rebuild**

Run:

```bash
make -C sim clean
make -C sim -j"$(nproc)"
make -C sim/tests \
  htsim_roce_reps_mrc_semantics \
  htsim_nmrc_shuffled_bucket \
  htsim_stor_feedback
sim/tests/htsim_roce_reps_mrc_semantics
sim/tests/htsim_nmrc_shuffled_bucket
sim/tests/htsim_stor_feedback
```

Expected: the build succeeds and all three binaries exit 0.

- [ ] **Step 2: Re-run the lightweight healthy pair with the clean binary**

Repeat Task 4 Steps 3–4.

Expected: exact RR/MRC flow completion, selection sequence, EV histogram, and
physical-path histogram equality with zero health violations.

- [ ] **Step 3: Inspect the final diff and repository state**

Run:

```bash
git diff --check HEAD~3..HEAD
git status --short
git log -5 --oneline
```

Expected: no whitespace errors; the status contains no unintended files; the
recent commits separately show tests/implementation, documentation, and
validation evidence.

- [ ] **Step 4: Report the outcome**

Report:

- exact unit and simulator commands that passed;
- the final post-change EV sequence;
- whether the realistic healthy workload activated MRC learning;
- FCT and mechanism differences;
- the validation report path;
- any pre-existing or unrelated working-tree changes left untouched.
