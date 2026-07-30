# Remote-State Synchronization 15 us Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set every active remote-state synchronization cadence used by SGLB, Avail, Grade, and n-MRC to 15 us, then produce a paired 5 us versus 15 us performance report across three scenarios and three seeds.

**Architecture:** Add one common `-remote_sync_us` simulator option that atomically selects all seven scoped remote publication/feedback intervals while retaining the existing narrow options for compatibility. Build a dedicated comparison runner on the repository's canonical 128-node traffic generators and exact-bounded parsers so every 5/15 pair shares one binary, traffic matrix, seed, transport, and scoring configuration.

**Tech Stack:** C++11 htsim simulator, Python 3 experiment runners and tests, GNU Make, CSV/Markdown artifacts.

## Global Constraints

- Change only periodic cross-node or remote-state synchronization intervals from 5 us to 15 us.
- Keep local state refresh at 1 us and SGLB remote aging at 30 us.
- Keep scoring, transport, hold-down, decay, and logging intervals unchanged.
- Compare SGLB, Avail, Grade, and n-MRC.
- Use 128 nodes; scenarios `healthy_permutation_16mib`, `asymmetric_permutation_16mib`, and `full_global_p16_256mib_background_on`; seeds 13, 29, and 47.
- Run both 5 us and 15 us for every scenario/seed/scheme cell: 72 total runs.
- Do not modify or stage the pre-existing user changes in `experiments/n-mrc/four_scheme_risks_and_evidence.md` or `experiments/n-mrc/plot_nmrc_routing_cooldown_matrix.py`.

---

### Task 1: Simulator defaults and unified remote cadence option

**Files:**
- Modify: `sim/tests/main_nmrc_score.cpp`
- Modify: `sim/tests/main_sglb_quality.cpp`
- Modify: `sim/tests/test_nmrc_interface.py`
- Modify: `sim/datacenter/fat_tree_switch.cpp`
- Modify: `sim/datacenter/main_roce.cpp`

**Interfaces:**
- Consumes: existing `timeFromUs`, SGLB GCN, NetAware export/feedback, and STOR feedback static interval fields.
- Produces: CLI `-remote_sync_us <non-negative microseconds>`; default effective value 15 us for all seven scoped fields.

- [ ] **Step 1: Change unit-test expectations to the required 15 us defaults**

In `main_nmrc_score.cpp`, require:

```cpp
FatTreeSwitch::_netaware_feedback_min_interval == timeFromUs(15.0)
FatTreeSwitch::_netaware_feedback_max_interval == timeFromUs(15.0)
FatTreeSwitch::_stor_feedback_min_interval == timeFromUs(15.0)
FatTreeSwitch::_stor_feedback_max_interval == timeFromUs(15.0)
FatTreeSwitch::_stor_trim_feedback_min_interval == timeFromUs(15.0)
FatTreeSwitch::_netaware_remote_update_interval == timeFromUs(15.0)
FatTreeSwitch::_netaware_state_update_interval == timeFromUs(1.0)
```

Change the remote snapshot boundary case to remain cached at 24.999 us and
refresh at 25.0 us when the prior update was 10.0 us and the interval is
15.0 us. Require the NetAware default feedback helper to return 15.0.

In `main_sglb_quality.cpp`, require:

```cpp
FatTreeSwitch::_sglb_gcn_update_interval == timeFromUs(15.0)
FatTreeSwitch::_sglb_update_interval == timeFromUs(1.0)
FatTreeSwitch::_sglb_gcn_aging_interval == timeFromUs(30.0)
```

In `test_nmrc_interface.py`, replace the stale “5us spine export” context
with “15us spine export” and require the new `-remote_sync_us` parser token.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
make -C sim/tests htsim_nmrc_score htsim_sglb_quality
sim/tests/htsim_nmrc_score
sim/tests/htsim_sglb_quality
python3 sim/tests/test_nmrc_interface.py
```

Expected: at least the C++ default assertions and the Python CLI assertion
fail because production still uses 5 us and has no unified option.

- [ ] **Step 3: Implement the 15 us defaults**

In `fat_tree_switch.cpp`, set:

```cpp
_sglb_gcn_update_interval = timeFromUs(15.0);
_netaware_feedback_min_interval = timeFromUs(15.0);
_netaware_feedback_max_interval = timeFromUs(15.0);
_netaware_remote_update_interval = timeFromUs(15.0);
_stor_feedback_min_interval = timeFromUs(15.0);
_stor_feedback_max_interval = timeFromUs(15.0);
_stor_trim_feedback_min_interval = timeFromUs(15.0);
```

Return 15.0 from `netaware_default_feedback_min_us` and
`netaware_default_feedback_max_us`.

In `main_roce.cpp`, change the three local STOR defaults to 15.0 and add:

```text
-remote_sync_us x
```

Parsing this option must clamp negative input to zero, assign SGLB GCN and
NetAware remote export statics, set the three STOR local option variables,
and record a NetAware feedback override. During post-parse NetAware setup,
use the common override instead of the default helper when supplied. Print:

```text
remote state synchronization interval <x>us
```

Narrow options supplied later on the command line may override their own
subsystem, preserving existing command compatibility.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the four commands from Step 2 again.

Expected: both binaries exit 0 and the Python interface test exits 0.

- [ ] **Step 5: Commit the simulator behavior**

```bash
git add sim/tests/main_nmrc_score.cpp sim/tests/main_sglb_quality.cpp \
  sim/tests/test_nmrc_interface.py sim/datacenter/fat_tree_switch.cpp \
  sim/datacenter/main_roce.cpp
git commit -m "feat: default remote state synchronization to 15us"
```

---

### Task 2: Active runner defaults and documentation

**Files:**
- Modify: `sim/tests/test_sglb_nmrc_quantized_topk_runner.py`
- Modify: `sim/tests/test_feedback_eval_common.py`
- Modify: `experiments/n-mrc/run_sglb_nmrc_quantized_topk_compare.py`
- Modify: `experiments/n-mrc/run_mrc_exact_bounded_recovery.py`
- Modify: `experiments/n-mrc/run_packet_lb_common_workloads_512.py`
- Modify: `experiments/n-mrc/feedback_eval_common.py`
- Modify: `experiments/n-mrc/README.md`
- Modify: `NMRC_IMPLEMENTATION.md`

**Interfaces:**
- Consumes: `-remote_sync_us` from Task 1.
- Produces: active default experiment commands with explicit 15 us cadence; historical `fixed5` analysis remains an explicit 5 us baseline and historical output directories are not renamed.

- [ ] **Step 1: Update runner tests to require active 15 us commands**

Require the SGLB quantized top-K manifest to contain:

```python
assert argv[argv.index("-remote_sync_us") + 1] == "15"
assert argv[argv.index("-sglb_gcn_update_us") + 1] == "15"
```

Add feedback helper coverage for a new `fixed15` cadence:

```python
assert module.feedback_args("avail", "fixed15", 7.0) == [
    "-stor_feedback_pkts", "1",
    "-stor_feedback_min_us", "15",
    "-stor_feedback_max_us", "15",
    "-stor_trim_feedback_min_us", "15",
]
```

- [ ] **Step 2: Run runner tests and verify RED**

Run:

```bash
python3 sim/tests/test_sglb_nmrc_quantized_topk_runner.py
pytest -q sim/tests/test_feedback_eval_common.py
```

Expected: failures because the active SGLB runner and feedback helper do not
yet expose 15 us.

- [ ] **Step 3: Update active command construction**

Make these active runners append `-remote_sync_us 15`:

- `run_sglb_nmrc_quantized_topk_compare.py`
- `run_mrc_exact_bounded_recovery.py`
- `run_packet_lb_common_workloads_512.py`

Change the SGLB runner's narrow GCN override and stdout validation to 15 us.
Change packet-workload configuration validation to require SGLB GCN and
NetAware remote export at 15 us.

Add `fixed15` to `feedback_eval_common.feedback_args` as packet trigger 1 and
15/15/15 us. Keep `fixed5` intact for the baseline experiment.

Update current README and implementation descriptions to state 15 us as the
default, while explicitly describing `fixed5` as a retained comparison
cadence.

- [ ] **Step 4: Run runner tests and verify GREEN**

Run the commands from Step 2.

Expected: both commands exit 0.

- [ ] **Step 5: Commit active defaults and documentation**

```bash
git add sim/tests/test_sglb_nmrc_quantized_topk_runner.py \
  sim/tests/test_feedback_eval_common.py \
  experiments/n-mrc/run_sglb_nmrc_quantized_topk_compare.py \
  experiments/n-mrc/run_mrc_exact_bounded_recovery.py \
  experiments/n-mrc/run_packet_lb_common_workloads_512.py \
  experiments/n-mrc/feedback_eval_common.py \
  experiments/n-mrc/README.md NMRC_IMPLEMENTATION.md
git commit -m "exp: use 15us remote synchronization defaults"
```

---

### Task 3: Paired 3-by-3 cadence comparison runner

**Files:**
- Create: `sim/tests/test_remote_sync_15us_comparison_runner.py`
- Create: `experiments/n-mrc/run_remote_sync_5us_vs_15us.py`

**Interfaces:**
- Consumes: canonical traffic generation and parsing functions from `run_nmrc_canonical_six_compare.py`; common `-remote_sync_us`.
- Produces: 72 validated commands, `summary.csv`, `paired_changes.csv`, `commands.tsv`, and `remote_sync_5us_vs_15us_for_gpt.md`.

- [ ] **Step 1: Write failing manifest and aggregation tests**

The test must import the new runner and assert:

```python
SCENARIOS == (
    "healthy_permutation_16mib",
    "asymmetric_permutation_16mib",
    "full_global_p16_256mib_background_on",
)
SEEDS == (13, 29, 47)
SCHEMES == ("sglb", "avail", "grade", "netaware")
CADENCES_US == (5, 15)
len(make_specs(args)) == 72
```

For every `(scenario, seed)` block, all eight scheme/cadence runs must share
one traffic SHA-256. Every command must contain exactly one
`-remote_sync_us`, with the value matching its cadence.

Use synthetic rows to verify paired percent change and the median:

```python
remote5 primary values:  [100.0, 200.0, 400.0]
remote15 primary values: [110.0, 180.0, 400.0]
paired changes:          [10.0, -10.0, 0.0]
median change:            0.0
```

Also require duplicate, missing, incomplete, nonzero-return, and
misconfigured result rows to raise `ValueError`.

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
python3 sim/tests/test_remote_sync_15us_comparison_runner.py
```

Expected: import failure because the runner does not exist.

- [ ] **Step 3: Implement command generation and validation**

Create a runner that:

- reuses canonical traffic materialization for the three selected scenarios;
- uses exact-bounded transport, SP/SACK64, composite ECN LB, DCQCN variant,
  P=8, 128 nodes, and the canonical scenario cutoffs/background arguments;
- maps schemes to `sglb`, `avail`, `grade`, and `netaware`;
- adds NetAware `good_share_cap`;
- appends exactly one `-remote_sync_us` with 5 or 15;
- fingerprints simulator, traffic file, and full command;
- rejects any matrix other than the exact 72 identities.

- [ ] **Step 4: Implement parsing, paired summaries, and report**

Reuse canonical FCT/CCT and recovery parsers. Retain:

```text
avg_fct_us, p99_fct_us, p999_fct_us, max_fct_us,
all_to_all_cct_us, rtos, retx_ratio,
composite_trims, composite_ecn_marks,
feedback_acks, runtime_s, completed, flows,
SGLB/NetAware remote snapshot diagnostics when present
```

For each scenario/scheme/seed pair, calculate:

```python
100.0 * (remote15 / remote5 - 1.0)
```

Group by scenario/scheme and report the median, minimum, and maximum paired
change across seeds. The Markdown report must include latency, recovery,
feedback/snapshot, completion, and runtime sections, and must state that lower
latency/recovery values are better.

- [ ] **Step 5: Run the new test and dry-run manifest**

Run:

```bash
python3 sim/tests/test_remote_sync_15us_comparison_runner.py
python3 experiments/n-mrc/run_remote_sync_5us_vs_15us.py \
  --dry-run --out /tmp/remote-sync-15us-manifest
```

Expected:

```text
validated 72 commands
```

- [ ] **Step 6: Commit the comparison runner**

```bash
git add sim/tests/test_remote_sync_15us_comparison_runner.py \
  experiments/n-mrc/run_remote_sync_5us_vs_15us.py
git commit -m "exp: compare 5us and 15us remote synchronization"
```

---

### Task 4: Build, run the 72-cell matrix, and verify results

**Files:**
- Generated: `experiments/n-mrc/output/remote_sync_5us_vs_15us_128_3x3/`

**Interfaces:**
- Consumes: simulator and runner from Tasks 1–3.
- Produces: complete measured performance comparison and final evidence.

- [ ] **Step 1: Run focused regression tests**

Run:

```bash
make -C sim/tests htsim_nmrc_score htsim_sglb_quality htsim_stor_feedback
sim/tests/htsim_nmrc_score
sim/tests/htsim_sglb_quality
sim/tests/htsim_stor_feedback
python3 sim/tests/test_nmrc_interface.py
python3 sim/tests/test_sglb_nmrc_quantized_topk_runner.py
python3 sim/tests/test_remote_sync_15us_comparison_runner.py
pytest -q sim/tests/test_feedback_eval_common.py
```

Expected: all commands exit 0.

- [ ] **Step 2: Build the simulator**

Run:

```bash
make -C sim/datacenter -j4 htsim_roce
```

Expected: exit 0 and `sim/datacenter/htsim_roce` is executable.

- [ ] **Step 3: Run the complete paired experiment**

Run:

```bash
python3 experiments/n-mrc/run_remote_sync_5us_vs_15us.py \
  --sim sim/datacenter/htsim_roce \
  --out experiments/n-mrc/output/remote_sync_5us_vs_15us_128_3x3 \
  --workers 4 --timeout 3600
```

Expected: 72/72 runs return 0, validate their effective cadence, and complete
all expected flows. If a run times out or is incomplete, diagnose and rerun
only that exact fingerprint; do not silently drop it.

- [ ] **Step 4: Verify result completeness and fresh report generation**

Run:

```bash
python3 experiments/n-mrc/run_remote_sync_5us_vs_15us.py \
  --sim sim/datacenter/htsim_roce \
  --out experiments/n-mrc/output/remote_sync_5us_vs_15us_128_3x3 \
  --workers 4 --timeout 3600
```

Expected: all 72 cells are reused from validated fingerprints; the command
exits 0 and regenerates the CSV/Markdown summaries.

- [ ] **Step 5: Audit repository diff and results**

Run:

```bash
git diff --check
git status --short
git log -4 --oneline
```

Confirm the two pre-existing user-modified files remain unstaged and
unchanged by this work. Read the paired CSV and Markdown report before stating
any performance conclusion.

- [ ] **Step 6: Report measured results**

Give the user:

- median/min/max 15 us versus 5 us primary-metric change for every
  scenario/scheme;
- per-seed changes when directions disagree;
- recovery and feedback/snapshot changes;
- completion or timeout anomalies;
- simulator runtime change;
- links to `summary.csv`, `paired_changes.csv`, the Markdown report, and the
  modified source files.
