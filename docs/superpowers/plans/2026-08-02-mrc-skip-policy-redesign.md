# MRC 64-Path Skip-Policy Redesign Implementation Plan

> **历史记录：已被取代。** 当前实现与设计以
> [`2026-08-18-mrc-ocp-skip-once-consolidation-design.md`](../specs/2026-08-18-mrc-ocp-skip-once-consolidation-design.md)
> 为准；本文中的多策略、32 active EV、backup、cooldown deadline 与公开故障 CLI 不再是当前方案。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make plain `-lb mrc` use a 64-EV identity-mapped profile with `skip_token` as the default congestion reaction, add `skip_rotation`, preserve legacy cooldown ablations, and keep failure recovery disabled in normal congestion experiments.

**Architecture:** Keep the existing `RoceSrc` ACK/NACK processing boundary: reliability and congestion control run first, then exact-EV MRC feedback updates per-QP path eligibility. Replace the canonical MRC active/backup congestion path with a shared 64-EV universe plus per-QP `GOOD/SKIP` state and one rotation cursor. Natural cursor progress consumes skip tokens or crosses a rotation boundary; there is no special all-SKIP policy in the new modes.

**Tech Stack:** C++11 htsim core, Python 3 experiment/test runners, GNU Make, existing RoCE SP/SACK tests.

## Global Constraints

- Plain `-lb mrc` defaults to `mrc_congestion_policy=skip_token`.
- Canonical topology is two-tier with 64 Spines, 64 hosts/downlinks per Leaf, 64 uplinks per Leaf, and 64 EVs.
- Node counts are `256/512/1024/2048/4096/8192` for `4/8/16/32/64/128` Leaves.
- `EV i == physical path i == Spine i` for `i in [0,63]`; no aliases and no backup EVs.
- `skip_token` consumes one token only when the rotation cursor reaches that EV's nominal slot.
- `skip_rotation` restores an EV on entry to the next logical rotation.
- Feedback received while an EV is SKIP never refreshes or extends its state.
- All-SKIP is handled by ordinary cursor/rotation progress, not a separate fallback branch.
- Existing SACK/selective-recovery state remains separate from MRC EV/LB state.
- Failure recovery is off by default; normal experiments produce no ASSUMED_BAD state or probe packets.
- n-MRC, SGLB, Avail, Grade, REPS, RR, queue semantics, and congestion-control behavior are unchanged.
- Historical output directories are read-only; every new experiment writes a new manifest and output root.

---

## File Structure

**Core transport and selector**

- Modify `sim/roce.h`: policy enum, EV eligibility state, per-EV skip fields, rotation state, diagnostics, and public test hooks.
- Modify `sim/roce.cpp`: 64-EV initialization, unified congestion transition, token/rotation selection, natural all-SKIP progress, disabled failure gate, and diagnostics.
- Modify `sim/datacenter/main_roce.cpp`: CLI parsing, default resolution, topology/profile validation, and runtime summaries.

**Packet abstraction**

- Modify `sim/network.h` and `sim/network.cpp`: add the future probe packet type/name.
- Modify `sim/rocepacket.h` and `sim/rocepacket.cpp`: define a control-only EV Probe abstraction with an independent request id.

**Focused tests**

- Create `sim/tests/main_roce_mrc_skip_semantics.cpp`: event-level profile, token, rotation, all-SKIP, no-rearm, attribution, and probe-interface tests.
- Modify `sim/tests/Makefile`: build the focused test binary.
- Create `sim/tests/test_mrc_skip_policy_cli.py`: CLI/default/legacy/topology diagnostics tests.
- Modify `sim/tests/test_final_dcqcn_mrc_semantics.py`: replace old default/fallback assertions with the new default while retaining explicit legacy checks.
- Modify `sim/tests/main_roce_reps_mrc_semantics.cpp`: preserve only cross-scheme regressions that belong in the existing broad suite.

**Experiments and docs**

- Modify `experiments/n-mrc/feedback_eval_common.py`: add a separate canonical 64-path topology mapping without rewriting historical `TOPOLOGIES`.
- Create `experiments/n-mrc/run_mrc_skip_policy_comparison.py`: paired four-policy runner and manifest writer.
- Create `sim/tests/test_mrc_skip_policy_comparison_runner.py`: runner unit tests with a fake simulator.
- Update the local, gitignored `MRC_IMPLEMENTATION.md` and the tracked `experiments/n-mrc/README.md` only after core tests pass.

---

### Task 1: Add the policy CLI and preserve legacy aliases

**Files:**
- Modify: `sim/roce.h:55-70,235-295,570-585`
- Modify: `sim/roce.cpp:90-106`
- Modify: `sim/datacenter/main_roce.cpp:830-845,1000-1020,1360-1435,3520-3615`
- Create: `sim/tests/test_mrc_skip_policy_cli.py`

**Interfaces:**
- Produces: `RoceSrc::mrc_congestion_policy_t`, `setMrcCongestionPolicy()`, `mrcCongestionPolicy()`, `mrcCongestionPolicyName()`.
- Consumes later: selector and feedback tasks branch only on this enum.

- [ ] **Step 1: Write failing CLI tests**

Create a runner that invokes an empty two-node MRC simulation and asserts:

```python
assert "MrcPolicyDiag policy=skip_token" in default.stdout
assert "all_skip_resolution=natural_rotation" in default.stdout
assert run(["-mrc_congestion_policy", "skip_rotation"]).returncode == 0
assert run(["-mrc_congestion_policy", "one_cycle"]).returncode == 0
assert run(["-mrc_congestion_policy", "cwnd_scaled"]).returncode == 0
assert run(["-mrc_congestion_policy", "invalid"]).returncode != 0
assert run(["-mrc_congestion_policy", "skip_token",
            "-mrc_cooldown_mode", "one_cycle"]).returncode != 0
```

- [ ] **Step 2: Run the test and verify the new CLI is absent**

Run: `python3 sim/tests/test_mrc_skip_policy_cli.py`

Expected: FAIL because `-mrc_congestion_policy` and `MrcPolicyDiag policy=skip_token` do not exist.

- [ ] **Step 3: Add the enum and default**

Use this exact public shape:

```cpp
typedef enum {
    MRC_POLICY_SKIP_TOKEN = 0,
    MRC_POLICY_SKIP_ROTATION = 1,
    MRC_POLICY_ONE_CYCLE = 2,
    MRC_POLICY_CWND_SCALED = 3
} mrc_congestion_policy_t;
```

Default `_mrc_congestion_policy` to `MRC_POLICY_SKIP_TOKEN`. Parse only `skip_token|skip_rotation|one_cycle|cwnd_scaled`. Track whether either the new or old option was supplied and reject mixed use. Map the old `-mrc_cooldown_mode one_cycle|cwnd_scaled` to the two legacy enum values with a deprecation message.

- [ ] **Step 4: Print one authoritative policy diagnostic**

For new policies print `all_skip_resolution=natural_rotation`; for legacy policies print the legacy deadline and fallback fields. Do not print an all-cooling fallback for `skip_token` or `skip_rotation`.

- [ ] **Step 5: Run the focused CLI test**

Run: `make -C sim/datacenter -j2 htsim_roce && python3 sim/tests/test_mrc_skip_policy_cli.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add sim/roce.h sim/roce.cpp sim/datacenter/main_roce.cpp sim/tests/test_mrc_skip_policy_cli.py
git commit -m "feat: add MRC skip policy interface"
```

---

### Task 2: Establish the canonical 64-EV profile and topology contract

**Files:**
- Modify: `sim/roce.h:280-295,790-875,980-1020`
- Modify: `sim/roce.cpp:2855-2980`
- Modify: `sim/datacenter/main_roce.cpp:3318-3340,3520-3585`
- Create: `sim/tests/main_roce_mrc_skip_semantics.cpp`
- Modify: `sim/tests/Makefile:1-45`
- Modify: `experiments/n-mrc/feedback_eval_common.py:156-190`
- Modify: `sim/tests/test_feedback_eval_common.py:45-105`

**Interfaces:**
- Produces: `MRC_CANONICAL_EV_COUNT = 64`, `init_mrc_paths(64)`, shared EV universe, per-QP deterministic order.
- Invariant: `logical_ev == physical_path == spine` and `_mrc_backup.empty()`.

- [ ] **Step 1: Add failing event-level profile tests**

The new C++ test must construct an MRC source with path space 64 and assert:

```cpp
expect(src._mrc_evs.size() == 64, "profile must contain 64 EVs");
expect(src._mrc_active.size() == 64, "all 64 paths must be eligible initially");
expect(src._mrc_backup.empty(), "canonical profile has no backup EVs");
for (uint32_t ev = 0; ev < 64; ++ev) {
    expect(src._mrc_evs[ev].logical_ev == ev, "logical EV mismatch");
    expect(src._mrc_evs[ev].physical_path == ev, "identity path mismatch");
}
```

Also assert path spaces other than 64 are rejected for the new two policies, while explicit legacy policies retain existing diagnostic flexibility.

- [ ] **Step 2: Add the Makefile target and prove the test fails**

Add `htsim_roce_mrc_skip_semantics` to `all`, link `main_roce_mrc_skip_semantics.o` against `../libhtsim.a`, then run:

`make -C sim/tests -B htsim_roce_mrc_skip_semantics && sim/tests/htsim_roce_mrc_skip_semantics`

Expected: FAIL because active EVs are capped at 32.

- [ ] **Step 3: Replace the 32-active/backup initialization for new policies**

For `skip_token` and `skip_rotation`, require `path_space == 64`, build exactly 64 `MrcEv` entries, and put all entries in the rotation order. Preserve `build_mrc_ev_order()` for deterministic per-QP order, but never change each entry's identity mapping.

- [ ] **Step 4: Add canonical experiment topologies without changing history**

Add:

```python
MRC_SKIP_TOPOLOGIES = MappingProxyType({
    leaves * 64: Topology(leaves * 64, 64, leaves, 64)
    for leaves in (4, 8, 16, 32, 64, 128)
})
```

Keep the existing `TOPOLOGIES` mapping untouched for archived runners. Extend `test_feedback_eval_common.py` to assert node/Leaf counts and `topology.paths == 64` for every new entry.

- [ ] **Step 5: Run profile and topology tests**

Run:

```bash
make -C sim/tests -B htsim_roce_mrc_skip_semantics
sim/tests/htsim_roce_mrc_skip_semantics
python3 sim/tests/test_feedback_eval_common.py
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add sim/roce.h sim/roce.cpp sim/datacenter/main_roce.cpp sim/tests/Makefile \
  sim/tests/main_roce_mrc_skip_semantics.cpp \
  experiments/n-mrc/feedback_eval_common.py sim/tests/test_feedback_eval_common.py
git commit -m "feat: use a canonical 64-EV MRC profile"
```

---

### Task 3: Implement unified GOOD-to-SKIP no-rearm feedback

**Files:**
- Modify: `sim/roce.h:790-830,980-1020`
- Modify: `sim/roce.cpp:2970-3155,3458-3535`
- Modify: `sim/tests/main_roce_mrc_skip_semantics.cpp`

**Interfaces:**
- Produces: `MRC_EV_GOOD`, `MRC_EV_SKIP`, `MRC_EV_ASSUMED_BAD`, `MRC_EV_DENIED`, and `mark_ev_congested(ev, signal)`.
- Preserves: explicit `mrc_ev` attribution in `update_mrc_on_ack()` and `update_mrc_on_nack()`.

- [ ] **Step 1: Write failing transition tests**

Test ECN and TRIM separately. The first signal must transition GOOD→SKIP and increment `congestion_epoch`; a second signal while SKIP must leave token/rotation/deadline and epoch unchanged while incrementing `duplicate_feedback_ignored`. Reactivate the EV, deliver a new signal, and assert a new epoch starts.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `make -C sim/tests -B htsim_roce_mrc_skip_semantics && sim/tests/htsim_roce_mrc_skip_semantics`

Expected: FAIL because the new eligibility states and policy-specific fields do not exist.

- [ ] **Step 3: Add the eligibility state and centralized transition**

Use one function for ECN ACK and TRIM NACK. It must return immediately for any state other than GOOD. Initialize exactly one policy field:

```cpp
case MRC_POLICY_SKIP_TOKEN:
    ev.skip_pending = true;
    break;
case MRC_POLICY_SKIP_ROTATION:
    ev.resume_rotation = _mrc_rotation + 1;
    break;
```

Legacy cases set their existing selection deadline. Clear inactive policy fields when starting a new episode.

- [ ] **Step 4: Preserve exact feedback attribution**

Keep `mrc_resolve_encoded_feedback_ev()` unchanged in priority: explicit EV, then sequence mapping for legacy packets, then physical fallback. Add an assertion/counter proving cumulative ACK identity is never used when explicit EV is present.

- [ ] **Step 5: Run the focused and existing broad semantic tests**

Run:

```bash
make -C sim/tests -B htsim_roce_mrc_skip_semantics htsim_roce_reps_mrc_semantics
sim/tests/htsim_roce_mrc_skip_semantics
sim/tests/htsim_roce_reps_mrc_semantics
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add sim/roce.h sim/roce.cpp sim/tests/main_roce_mrc_skip_semantics.cpp
git commit -m "feat: centralize MRC skip transitions"
```

---

### Task 4: Implement `skip_token` and natural all-SKIP progress

**Files:**
- Modify: `sim/roce.h:700-750,810-875,980-1025`
- Modify: `sim/roce.cpp:2980-3330`
- Modify: `sim/tests/main_roce_mrc_skip_semantics.cpp`

**Interfaces:**
- Produces: `advance_mrc_nominal_slot()`, `_mrc_rotation`, `_mrc_rotation_slot`, `skip_opportunities_consumed`.
- Contract: health scans are read-only; only cursor advancement consumes tokens.

- [ ] **Step 1: Write failing token tests**

Cover: one token, duplicate feedback, diagnostics scan, consecutive tokens, just-consumed EV excluded from the same packet, and all 64 EVs tokenized. Assert every returned DATA choice is GOOD at selection time and `data_on_non_good_violations == 0`.

- [ ] **Step 2: Run and observe the old forced-cooling behavior**

Run: `sim/tests/htsim_roce_mrc_skip_semantics`

Expected: FAIL because the current selector uses `earliest_cooling` and can return a cooling EV.

- [ ] **Step 3: Implement slot-driven token consumption**

On each nominal slot:

```cpp
if (ev.eligibility == MRC_EV_SKIP && ev.skip_pending) {
    ev.skip_pending = false;
    ev.eligibility = MRC_EV_GOOD;
    ev.last_skip_consumed_selection = _mrc_select_counter;
    ++_mrc_skip_opportunities_consumed;
    continue; // cannot use this EV for the current packet
}
```

Advance `_mrc_rotation` only when 64 nominal slots have been visited. A full all-SKIP traversal naturally restores token EVs, wraps once, and selects a GOOD EV. Bound the loop to at most 128 slot visits and assert if no recovery boundary exists.

- [ ] **Step 4: Keep all-SKIP inside ordinary new-policy control flow**

The `skip_token` selector needs no all-SKIP branch: ordinary bounded cursor advancement consumes the 64 nominal opportunities, wraps, and selects a restored GOOD EV. Keep the pre-existing legacy fallback code reachable only when the explicitly selected policy is `one_cycle` or `cwnd_scaled`; it is not part of either new policy's semantics.

- [ ] **Step 5: Run focused tests**

Run: `make -C sim/tests -B htsim_roce_mrc_skip_semantics && sim/tests/htsim_roce_mrc_skip_semantics`

Expected: PASS with no non-GOOD DATA selection.

- [ ] **Step 6: Commit**

```bash
git add sim/roce.h sim/roce.cpp sim/tests/main_roce_mrc_skip_semantics.cpp
git commit -m "feat: implement MRC skip-token selection"
```

---

### Task 5: Implement `skip_rotation` and retain legacy ablations

**Files:**
- Modify: `sim/roce.cpp:2980-3330`
- Modify: `sim/tests/main_roce_mrc_skip_semantics.cpp`
- Modify: `sim/tests/test_final_dcqcn_mrc_semantics.py`
- Modify: `sim/tests/main_roce_reps_mrc_semantics.cpp:55-135,720-875`

**Interfaces:**
- Produces: next-rotation recovery using `resume_rotation`.
- Preserves: explicit `one_cycle` and `cwnd_scaled` behavior and diagnostics.

- [ ] **Step 1: Write failing rotation tests**

Assert congestion in rotation 10 sets `resume_rotation=11`, duplicate feedback leaves it 11, EV remains ineligible through the rest of rotation 10, and becomes GOOD before its rotation-11 selection. Test all 64 EVs in SKIP and prove normal cursor progress crosses the boundary without a fallback branch.

- [ ] **Step 2: Implement rotation-boundary refresh**

When advancing slot 63→0, increment `_mrc_rotation`. Before evaluating a slot, change `SKIP` to `GOOD` only when the policy is `skip_rotation` and `_mrc_rotation >= ev.resume_rotation`.

- [ ] **Step 3: Update old default tests**

Change default expectations to `skip_token`. Retain explicit test cases for `one_cycle`, `cwnd_scaled`, and their legacy all-cooling diagnostics. Remove any assertion that an all-cooling option affects the two new policies.

- [ ] **Step 4: Run new and legacy policy tests**

Run:

```bash
make -C sim/tests -B htsim_roce_mrc_skip_semantics htsim_roce_reps_mrc_semantics
sim/tests/htsim_roce_mrc_skip_semantics
sim/tests/htsim_roce_reps_mrc_semantics
python3 sim/tests/test_final_dcqcn_mrc_semantics.py
python3 sim/tests/test_mrc_skip_policy_cli.py
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sim/roce.cpp sim/tests/main_roce_mrc_skip_semantics.cpp \
  sim/tests/main_roce_reps_mrc_semantics.cpp \
  sim/tests/test_final_dcqcn_mrc_semantics.py
git commit -m "feat: add MRC skip-rotation policy"
```

---

### Task 6: Prove the existing SACK/LB separation remains intact

**Files:**
- Modify: `sim/tests/main_roce_mrc_skip_semantics.cpp`
- Modify: `sim/tests/main_roce_sp_sack_correctness.cpp`
- Verify only: `sim/roce.cpp:1151-1345,1355-1450,3458-3535`

**Interfaces:**
- Preserves: `processNack()` reliability first, `update_mrc_on_nack()` afterward; `processAck()` confirmation/CC first, `update_mrc_on_ack()` afterward.

- [ ] **Step 1: Add a combined TRIM regression**

Construct a TRIM NACK carrying a SACK bitmap and explicit EV17. Assert the missing PSN enters the retransmission state exactly once and EV17 enters SKIP exactly once. Assert no other EV changes and the LB update does not change `_last_acked`, unique inflight, or retransmission membership.

- [ ] **Step 2: Add an ECN ACK regression**

Deliver an ACK that advances cumulative reliability state and carries `ECN_ECHO + mrc_ev=17`. Assert ACK progress occurs once, then EV17 changes; a duplicate ACK can carry LB feedback without releasing reliability credit twice.

- [ ] **Step 3: Run tests before changing production code**

Run:

```bash
make -C sim/tests -B htsim_roce_mrc_skip_semantics htsim_roce_sp_sack_correctness
sim/tests/htsim_roce_mrc_skip_semantics
sim/tests/htsim_roce_sp_sack_correctness trim
sim/tests/htsim_roce_sp_sack_correctness mrc_ev
```

Expected: PASS without production-code changes. If either test fails, stop and trace the existing ordering before modifying `processAck()` or `processNack()`.

- [ ] **Step 4: Commit tests only**

```bash
git add sim/tests/main_roce_mrc_skip_semantics.cpp sim/tests/main_roce_sp_sack_correctness.cpp
git commit -m "test: preserve MRC SACK and LB separation"
```

---

### Task 7: Add a disabled-by-default ASSUMED_BAD/EV Probe interface

**Files:**
- Modify: `sim/network.h:45-60`
- Modify: `sim/network.cpp:190-225`
- Modify: `sim/rocepacket.h:360-450`
- Modify: `sim/rocepacket.cpp`
- Modify: `sim/roce.h:290-305,790-830,980-1030`
- Modify: `sim/roce.cpp:3150-3225,3945-3990`
- Modify: `sim/datacenter/main_roce.cpp:830-845,1000-1025,1400-1445,3580-3620`
- Modify: `sim/tests/main_roce_mrc_skip_semantics.cpp`

**Interfaces:**
- Produces: `ROCEEVPROBE`, `RoceEvProbe`, `mrc_failure_recovery_enabled=false`, `mrc_probe_success_threshold=3`.
- Invariant: probe request id is independent of data PSN; probes never touch application bytes or data cwnd.

- [ ] **Step 1: Write failing packet and state tests**

Assert a normal default run prints `enabled=0 assumed_bad=0 probe_packets=0`. In a unit test, manually set EV17 to ASSUMED_BAD, enable failure recovery, send probe results, and assert exactly three consecutive successes restore GOOD; a failed response resets the consecutive count.

- [ ] **Step 2: Add the control packet abstraction**

Define `RoceEvProbe` with `request_id`, `target_ev`, `response`, and `success`. Allocate it as a header/control packet with high priority. Do not expose a data `seqno`, retransmitted flag, or application size.

- [ ] **Step 3: Gate all failure transitions**

When `mrc_failure_recovery_enabled` is false, LOSS/RTO must not move a canonical congestion-policy EV into ASSUMED_BAD and no probe scheduler may run. Legacy policy behavior remains available only under explicit legacy configuration.

- [ ] **Step 4: Run probe and default-isolation tests**

Run:

```bash
make -C sim/tests -B htsim_roce_mrc_skip_semantics
sim/tests/htsim_roce_mrc_skip_semantics
python3 sim/tests/test_mrc_skip_policy_cli.py
```

Expected: PASS; default diagnostics report zero failure/probe activity.

- [ ] **Step 5: Commit**

```bash
git add sim/network.h sim/network.cpp sim/rocepacket.h sim/rocepacket.cpp \
  sim/roce.h sim/roce.cpp sim/datacenter/main_roce.cpp \
  sim/tests/main_roce_mrc_skip_semantics.cpp
git commit -m "feat: add an optional MRC EV probe interface"
```

---

### Task 8: Build the paired four-policy experiment runner

**Files:**
- Create: `experiments/n-mrc/run_mrc_skip_policy_comparison.py`
- Create: `sim/tests/test_mrc_skip_policy_comparison_runner.py`
- Modify: `experiments/n-mrc/README.md`

**Interfaces:**
- Consumes: `MRC_SKIP_TOPOLOGIES`, four policy CLI values, existing traffic generators and metric parsers.
- Produces: one manifest plus per-cell stdout/data and a paired summary keyed by `(nodes, workload, load, seed, flow_id)`.

- [ ] **Step 1: Write a fake-simulator runner test**

Assert the runner creates exactly four commands per cell, changes only `-mrc_congestion_policy`, uses `-paths 64`, uses the same traffic hash, and rejects outputs whose diagnostics do not match the requested policy or whose `data_on_non_good_violations` is nonzero.

- [ ] **Step 2: Run and verify failure**

Run: `python3 sim/tests/test_mrc_skip_policy_comparison_runner.py`

Expected: FAIL because the runner does not exist.

- [ ] **Step 3: Implement the runner**

Stage 1 supports nodes `256,512,1024` and seeds `13,29,47`. Write every run under `experiments/n-mrc/output/mrc_skip_policy_comparison/` with simulator hash, traffic hash, complete command, runtime diagnostics, exit status, and expected flow count.

- [ ] **Step 4: Add paired metrics**

Report CCT, FCT p50/p95/p99/p999, queue p50/p95/p99/p999, per-path Jain fairness/CV, ECN/TRIM/NACK/retransmission/RTO, skip episodes/opportunities, duplicate feedback ignored, consecutive SKIP slots, and non-GOOD DATA violations.

- [ ] **Step 5: Run runner tests and a dry-run**

Run:

```bash
python3 sim/tests/test_mrc_skip_policy_comparison_runner.py
python3 experiments/n-mrc/run_mrc_skip_policy_comparison.py --dry-run --nodes 256 --seed 13
```

Expected: test PASS; dry-run prints four commands with identical non-policy arguments.

- [ ] **Step 6: Commit**

```bash
git add experiments/n-mrc/run_mrc_skip_policy_comparison.py \
  sim/tests/test_mrc_skip_policy_comparison_runner.py experiments/n-mrc/README.md
git commit -m "exp: add MRC skip policy comparison"
```

---

### Task 9: Add Static MPR as a separately gated P2 change

**Files:**
- Modify: `sim/roce.h:420-450,1020-1050`
- Modify: `sim/roce.cpp:600-1025,1151-1450,4400-4625`
- Modify: `sim/datacenter/main_roce.cpp:1000-1030,1430-1460,3600-3640`
- Modify: `sim/tests/main_roce_exact_bounded_recovery.cpp`
- Modify: `sim/tests/test_exact_bounded_before_after_runner.py`

**Interfaces:**
- Produces: `mrc_static_mpr_pkts`, requester upper horizon, responder finite receive bitmap.
- Gate: execute only after Tasks 1–8 pass and measured outstanding PSN span justifies MPR work.

- [ ] **Step 1: Add failing MPR boundary tests**

Set `mpr_pkts=8`; assert PSN 9 cannot be sent while `cack_psn=0`, SACK/ACK advancement opens the corresponding slot, out-of-order receive beyond the upper edge is rejected, and 24-bit PSN wrap is handled with unsigned modular comparison.

- [ ] **Step 2: Run and verify the current exact-bounded approximation fails**

Run: `make -C sim/tests -B htsim_roce_exact_bounded_recovery && sim/tests/htsim_roce_exact_bounded_recovery`

Expected: new boundary cases FAIL because current code only applies `cwnd - bounded_inflight`.

- [ ] **Step 3: Implement the fixed responder-advertised horizon**

Enforce `next_psn <= cack_psn + mrc_static_mpr_pkts * mss` independently of cwnd. Bound the responder's internal received bitmap to the same PSN range; do not use the wire SACK bitmap width as the internal MPR size.

- [ ] **Step 4: Run exact-bounded, SACK, and skip-policy regressions**

Run:

```bash
make -C sim/tests -B htsim_roce_exact_bounded_recovery \
  htsim_roce_sp_sack_correctness htsim_roce_mrc_skip_semantics
sim/tests/htsim_roce_exact_bounded_recovery
for mode in duplicate ooo loss trim trim_tail trim_stale mrc_ev; do
  sim/tests/htsim_roce_sp_sack_correctness "$mode"
done
sim/tests/htsim_roce_mrc_skip_semantics
python3 sim/tests/test_exact_bounded_before_after_runner.py
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add sim/roce.h sim/roce.cpp sim/datacenter/main_roce.cpp \
  sim/tests/main_roce_exact_bounded_recovery.cpp \
  sim/tests/test_exact_bounded_before_after_runner.py
git commit -m "feat: enforce static MRC PSN range"
```

---

### Task 10: Final documentation and verification

**Files:**
- Update locally (gitignored, do not stage): `MRC_IMPLEMENTATION.md`
- Modify: `experiments/n-mrc/README.md`
- Modify: `sim/datacenter/main_roce.cpp` CLI help and diagnostics
- Verify: all files changed by Tasks 1–9

**Interfaces:**
- Produces: canonical implementation documentation that matches tested runtime behavior.

- [ ] **Step 1: Update documentation only after implementation tests pass**

Document the 64-EV identity profile, default `skip_token`, `skip_rotation`, legacy labels, natural all-SKIP progress, no-rearm rule, preserved SACK/LB separation, failure recovery default off, and Static MPR status.

- [ ] **Step 2: Scan for stale canonical claims**

Run:

```bash
rg -n "min\(path_space, 32\)|default.*one_cycle|default.*cwnd_scaled|all_cooling_fallback=earliest|data packet.*probe" \
  MRC_IMPLEMENTATION.md experiments/n-mrc/README.md sim/datacenter/main_roce.cpp
```

Expected: matches remain only in explicitly labeled historical/legacy sections.

- [ ] **Step 3: Run the full targeted verification suite**

```bash
make -C sim -j2
make -C sim/datacenter -j2 htsim_roce
make -C sim/tests -B htsim_roce_mrc_skip_semantics \
  htsim_roce_reps_mrc_semantics htsim_roce_sp_sack_correctness \
  htsim_roce_exact_bounded_recovery
sim/tests/htsim_roce_mrc_skip_semantics
sim/tests/htsim_roce_reps_mrc_semantics
sim/tests/htsim_roce_exact_bounded_recovery
for mode in duplicate ooo loss trim trim_tail trim_tail_legacy \
  trim_stale trim_already_sacked trim_unaligned mrc_ev; do
  sim/tests/htsim_roce_sp_sack_correctness "$mode"
done
python3 sim/tests/test_mrc_skip_policy_cli.py
python3 sim/tests/test_final_dcqcn_mrc_semantics.py
python3 sim/tests/test_feedback_eval_common.py
python3 sim/tests/test_mrc_skip_policy_comparison_runner.py
python3 sim/tests/test_exact_bounded_before_after_runner.py
```

Expected: every command exits 0; every SP scenario prints `pass=1`; default diagnostics report `skip_token`, 64 EVs, zero backups, natural rotation, and zero failure/probe activity.

- [ ] **Step 4: Check the worktree and commit docs**

```bash
git diff --check
git status --short
git add experiments/n-mrc/README.md sim/datacenter/main_roce.cpp
git commit -m "docs: publish the MRC skip-token baseline"
```

Do not force-add `MRC_IMPLEMENTATION.md`; it is a local research note under the repository's ignore policy. Do not stage pre-existing experiment outputs or unrelated user edits.
