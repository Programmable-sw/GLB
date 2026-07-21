# N-MRC WebSearch Four-Way Ablation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement and compare the existing N-MRC, an all-cooling RR-reset ablation, stateless-random N-MRC1, and binary-score N-MRC2 on the identical 128-node healthy WebSearch 100% workload for seeds 13/29/47.

**Architecture:** Keep all four CLI presets mapped to the existing internal `LB_NMRC`. Split behavior through three orthogonal resolved policies: endpoint EV selection, all-cooling fallback, and source-ToR reroute decision. Preserve the current `n-mrc` defaults byte-for-byte at the policy boundary, add focused unit/interface tests first, then run all 12 cells from one newly built simulator against the same traffic matrices and generate reproducible CSV/figures.

**Tech Stack:** C++17 htsim, existing C++ unit harnesses, Python 3, pytest/unittest-style interface checks, matplotlib, CSV/JSON manifests.

## Global Constraints

- Existing `-lb n-mrc` behavior and defaults remain unchanged: endpoint RR/cooldown, earliest-deadline all-cooling fallback, graded source-ToR score, `better_ge3`, FastCNP on, encoded EVs.
- `n-mrc-allcool-rr-reset` changes only the all-cooling fallback. It performs exactly one per-QP RR episode of `P = _nmrc_evs.size()` selections from the current cursor, ignores cooldown during that episode, visits every encoded EV once, ignores duplicate cooldown extensions during it, then clears every EV cooldown/deadline after the P-th selection.
- `n-mrc1` chooses a deterministic stateless pseudo-random encoded EV in `[0,P)` for every new/retransmitted packet, creates no endpoint cooldown from FastCNP or accepted TRIM, and otherwise keeps the current N-MRC source-ToR GLB fallback. It must not consume global RNG state.
- `n-mrc2` keeps the exact legacy endpoint RR/cooldown and earliest fallback. Its network decision uses the existing continuous noisy-OR full-path score, with fixed `q_min=0.20`, `q_max=0.80`, and one boundary: SAFE iff a complete, finite two-hop snapshot has score `< 0.5`; CONGESTED iff that valid score is `>= 0.5`. Missing/stale/non-finite telemetry is UNKNOWN and cannot trigger or receive a reroute. It reroutes only from CONGESTED to an available SAFE path and emits FastCNP as the same committed action. If reverse FastCNP routing cannot be prepared, neither action commits.
- Do not add `LB_NMRC1`/`LB_NMRC2`; all aliases resolve to `LB_NMRC` and retain their user-facing preset name in resolved output.
- Do not modify or stage the user's existing `experiments/n-mrc/README.md` changes or `README.mdnt=` file.
- Follow strict TDD for production behavior: add a focused failing test, capture the expected RED, implement minimally, then capture GREEN.
- All four WebSearch groups must use one new simulator binary and the same existing traffic matrix per seed. Do not reuse an old `n-mrc` result row.
- The experiment driver uses four workers grouped one per scheme, with each worker running seeds 13, 29, and 47 sequentially. It writes atomic/cache-safe cell artifacts and supports resume.
- Primary comparison is the three-seed geometric mean of `p99_fct_us`; also retain seed-level p99, p99.9/max, reroute/check, FastCNP, cooldown, all-cooling, ECN/TRIM, and queue diagnostics.

## Task 1: Endpoint policies and all-cooling RR-reset

**Files:**

- Modify: `sim/roce.h`
- Modify: `sim/roce.cpp`
- Modify: `sim/tests/main_hybrid_nmrc.cpp`

- [ ] Add RED tests for legacy all-cooling behavior, RR-reset episodes, and stateless random selection.

The tests must first reference the following public policy/test interfaces so the pre-implementation build fails:

```cpp
typedef enum {
    NMRC_ENDPOINT_RR_COOLDOWN = 0,
    NMRC_ENDPOINT_RANDOM_STATELESS = 1
} nmrc_endpoint_policy_t;

typedef enum {
    NMRC_ALL_COOLING_EARLIEST = 0,
    NMRC_ALL_COOLING_RR_RESET = 1
} nmrc_all_cooling_policy_t;

static void setNmrcEndpointPolicy(nmrc_endpoint_policy_t policy);
static nmrc_endpoint_policy_t nmrcEndpointPolicy();
static const char* nmrcEndpointPolicyName();
static void setNmrcAllCoolingPolicy(nmrc_all_cooling_policy_t policy);
static nmrc_all_cooling_policy_t nmrcAllCoolingPolicy();
static const char* nmrcAllCoolingPolicyName();

bool nmrc_ev_cooling_flag_for_test(uint32_t ev) const;
bool nmrc_all_cooling_rr_active_for_test() const;
uint32_t nmrc_all_cooling_rr_progress_for_test() const;
```

Cover at least:

- legacy `earliest` tie behavior remains the same;
- after moving the cursor once and cooling all eight EVs, RR-reset returns the remaining seven shuffled entries then the first, with all raw cooldown flags/deadlines intact for selections 1--7 and cleared after selection 8;
- duplicate notification during the episode does not extend a deadline;
- selection 9 returns to normal RR/cooldown;
- two QPs retain independent shuffled orders;
- stateless random selection is reproducible for equal seed/src/dst/QP, differs for a different QP, stays in `[0,P)`, and is not the legacy cursor sequence;
- stateless random ignores FastCNP and accepted TRIM for cooldown purposes.

Run and retain the RED output:

```bash
make -C sim/tests -j4 htsim_hybrid_nmrc
```

Expected RED: compile errors for missing policy enums/accessors/test helpers.

- [ ] Implement the two policy enums, defaults, selection dispatch, episode state, and diagnostics.

Use process-wide defaults:

```cpp
nmrc_endpoint_policy_t RoceSrc::_nmrc_endpoint_policy =
    NMRC_ENDPOINT_RR_COOLDOWN;
nmrc_all_cooling_policy_t RoceSrc::_nmrc_all_cooling_policy =
    NMRC_ALL_COOLING_EARLIEST;
```

For RR-reset, capture `episode_size = _nmrc_evs.size()` on entry. Each episode selection returns `_nmrc_evs[_nmrc_cursor]`, advances the cursor modulo that captured size, increments the normal select ordinal and episode counters, and clears all cooldown flags/deadlines only after the captured number of selections. Reset unfinished episode state on flow completion or EV-set rebuild without counting it as a completed reset.

For stateless random, hash `_nmrc_ev_seed`, source, destination, flow/QP identity, and selection ordinal using the existing integer mix helper; do not build or index `_nmrc_evs`, call `random()`, or call `drand()`. Return `NmrcChoice(ev, ev)` and increment the ordinal exactly once.

Gate cooldown creation in both FastCNP and accepted-TRIM paths when endpoint policy is `random_stateless`. Keep arrival/ignored diagnostics machine-readable.

- [ ] Run GREEN tests and commit.

```bash
make -C sim/tests -j4 htsim_hybrid_nmrc
./sim/tests/htsim_hybrid_nmrc
```

Expected GREEN: exit 0 with the new exact-sequence/state assertions and all legacy assertions passing.

Commit:

```bash
git add sim/roce.h sim/roce.cpp sim/tests/main_hybrid_nmrc.cpp
git commit -m "feat: add nmrc endpoint and all-cooling policies"
```

## Task 2: Binary full-path decision and paired FastCNP

**Files:**

- Modify: `sim/datacenter/fat_tree_switch.h`
- Modify: `sim/datacenter/fat_tree_switch.cpp`
- Modify: `sim/tests/main_sglb_quality.cpp`

- [ ] Force-refresh the existing switch test object once, then verify the baseline before adding RED tests.

```bash
make -C sim/tests -B main_sglb_quality.o
make -C sim/tests -j4 htsim_sglb_quality
./sim/tests/htsim_sglb_quality
```

This refresh avoids the known stale `network.h` ABI object; any later test failure is treated normally.

- [ ] Add RED tests for the binary classifier, selector boundary, and paired-action integration.

Add a separate N-MRC network decision mode, not a global SGLB score mode:

```cpp
enum NmrcNetworkDecisionMode {
    NMRC_NETWORK_GRADED = 0,
    NMRC_NETWORK_BINARY_SCORE = 1
};
```

Tests must assert:

- `0.499999` is SAFE; `0.5` is CONGESTED; positive infinity, NaN, or an incomplete two-hop snapshot are UNKNOWN;
- a SAFE original never reroutes;
- a CONGESTED original reroutes only to an available SAFE candidate;
- if no SAFE candidate exists, neither reroute nor FastCNP commits;
- if the reverse control route is missing, packet detour metadata and both committed counters remain unchanged;
- successful action sets actual egress/detour metadata and emits one FastCNP with matching original/selected egress and binary transition `1 -> 0`;
- binary committed reroutes, paired actions, and FastCNP generated remain equal;
- reset clears all new diagnostics.

Run and retain RED:

```bash
make -C sim/tests -j4 htsim_sglb_quality
```

Expected RED: missing decision mode/classifier/diagnostic interfaces.

- [ ] Implement binary classification on the already computed continuous `SglbQualitySnapshot::score`.

Use a classifier over a complete snapshot:

```cpp
bool nmrc_binary_safe(double score, bool two_hop_valid) {
    return two_hop_valid && std::isfinite(score) && score < 0.5;
}
```

Extend the cached N-MRC snapshot just enough to retain whether its local queue and downstream snapshot were valid within the current TTL. Build candidate levels `0` for SAFE and `1` for CONGESTED so the existing FastCNP packet fields and transition matrix remain compatible; exclude UNKNOWN. Candidate choice is deterministic among every available SAFE path; it must not add the legacy graded `better_ge3` gate. Resolve the reverse control route before mutating packet metadata or committed counters. Preserve the legacy graded branch without semantic changes.

Add machine-readable diagnostics for binary original safe/congested, no-safe, route-missing, paired actions, original/selected score sum/max, and committed actual-egress histogram.

- [ ] Run GREEN tests and commit.

```bash
make -C sim/tests -j4 htsim_sglb_quality
./sim/tests/htsim_sglb_quality
```

Commit:

```bash
git add sim/datacenter/fat_tree_switch.h sim/datacenter/fat_tree_switch.cpp sim/tests/main_sglb_quality.cpp
git commit -m "feat: add nmrc binary paired reroute policy"
```

## Task 3: CLI presets, validation, and resolved diagnostics

**Files:**

- Modify: `sim/datacenter/main_roce.cpp`
- Modify: `sim/tests/test_hybrid_nmrc_interface.py`
- Modify: `sim/tests/test_mainline_compatibility_modes.py`

- [ ] Add RED interface assertions for all four user-facing presets.

The exact resolved mappings are:

```text
n-mrc: endpoint=rr_cooldown all_cooling=earliest network=graded fastcnp=on
n-mrc-allcool-rr-reset: endpoint=rr_cooldown all_cooling=rr_reset network=graded fastcnp=on
n-mrc1: endpoint=random_stateless all_cooling=earliest network=graded fastcnp=off
n-mrc2: endpoint=rr_cooldown all_cooling=earliest network=binary_score threshold=0.5 fastcnp=on
```

Tests must verify aliases remain internal `LB_NMRC`, option order does not affect resolution, N-MRC-specific flags are accepted for every alias, conflicting preset overrides fail with a nonzero exit and a precise message, and plain `n-mrc` output/defaults remain unchanged apart from appended policy fields.

Run RED:

```bash
python3 -B sim/tests/test_hybrid_nmrc_interface.py
python3 -B sim/tests/test_mainline_compatibility_modes.py
```

Expected RED: the new aliases/options are unrecognized or missing in resolved output.

- [ ] Implement parse-then-resolve preset handling.

Add usage and parsers for:

```text
-nmrc_endpoint_policy rr_cooldown|random_stateless
-nmrc_all_cooling_policy earliest|rr_reset
-nmrc_network_decision graded|binary_score
-nmrc_binary_threshold 0.5
```

Track user-set flags independently, resolve after parsing so flag order is irrelevant, validate using `roce_lb_mode == LB_NMRC`, and wire policies into `RoceSrc`/`FatTreeSwitch`. N-MRC2 fixes the threshold at exactly 0.5 and requires FastCNP on. N-MRC1 requires random-stateless, FastCNP off, encoded mode, and graded network fallback. The allcool preset permits only its single all-cooling change from legacy N-MRC.

Append resolved fields and all new endpoint/switch diagnostics to existing `HybridNmrcConfig` and `HybridNmrcDiag` lines without renaming old keys.

- [ ] Build and run GREEN interface/regression tests, then commit.

```bash
make -C sim/datacenter -j4 htsim_roce
python3 -B sim/tests/test_hybrid_nmrc_interface.py
python3 -B sim/tests/test_mainline_compatibility_modes.py
python3 -B sim/tests/test_nmrc_interface.py
./sim/tests/htsim_hybrid_nmrc
./sim/tests/htsim_sglb_quality
```

Commit:

```bash
git add sim/datacenter/main_roce.cpp sim/tests/test_hybrid_nmrc_interface.py sim/tests/test_mainline_compatibility_modes.py
git commit -m "feat: expose nmrc four-way experiment presets"
```

## Task 4: Focused resumable WebSearch experiment driver

**Files:**

- Create: `experiments/n-mrc/run_nmrc_websearch100_fourway.py`
- Create: `experiments/n-mrc/tests/test_nmrc_websearch100_fourway.py`

- [ ] Add RED parser/fingerprint/aggregation tests using synthetic simulator logs.

Tests must cover exact scheme list/order, seeds 13/29/47, 128 nodes, 8 paths, the same traffic path per seed across schemes, unique fingerprints including simulator and traffic SHA256, missing/failed cell rejection, diagnostic key parsing, geometric means, speedup defined as `n-mrc_p99 / scheme_p99`, and resume only on a matching complete fingerprint.

Run RED:

```bash
python3 -B experiments/n-mrc/tests/test_nmrc_websearch100_fourway.py
```

Expected RED: module/file absent.

- [ ] Implement the focused runner.

Use the existing matrices:

```text
experiments/n-mrc/output/final_packet_lb_comparison_20260720/traffic/n128_healthy_p2p_websearch_100pct_seed13.cm
experiments/n-mrc/output/final_packet_lb_comparison_20260720/traffic/n128_healthy_p2p_websearch_100pct_seed29.cm
experiments/n-mrc/output/final_packet_lb_comparison_20260720/traffic/n128_healthy_p2p_websearch_100pct_seed47.cm
```

Use one simulator path supplied by CLI/defaulting to the current worktree build and the canonical 128-node WebSearch command settings already used by the final comparison: 400G, 2 tiers, composite ECN LB, priority host queue, MTU 4096, 8 paths, DCQCN variant, SP, SACK 64, exact bounded MRC/trim, hop and switch latency 0.5, queue CV period 100, and the same end time. Refuse to run if a matrix is absent.

Output under `experiments/n-mrc/output/nmrc_websearch100_fourway_20260721/`:

```text
raw/<scheme>/seed<seed>.log.gz
cells/<scheme>_seed<seed>.json
results.csv
summary.csv
manifest.json
commands.json
```

Each cell records command, exit status, elapsed time, binary SHA, traffic SHA, resolved config, completion metrics, parsed diagnostics, and fingerprint. Write cell JSON atomically. Start four scheme workers concurrently; each scheme worker runs its three seeds sequentially.

- [ ] Run GREEN tests and a dry-run command preview, then commit.

```bash
python3 -B experiments/n-mrc/tests/test_nmrc_websearch100_fourway.py
python3 -B experiments/n-mrc/run_nmrc_websearch100_fourway.py --dry-run
```

Commit:

```bash
git add experiments/n-mrc/run_nmrc_websearch100_fourway.py experiments/n-mrc/tests/test_nmrc_websearch100_fourway.py
git commit -m "feat: add nmrc websearch four-way runner"
```

## Task 5: Figures and analytical report

**Files:**

- Create: `experiments/n-mrc/plot_nmrc_websearch100_fourway.py`
- Create: `experiments/n-mrc/tests/test_plot_nmrc_websearch100_fourway.py`

- [ ] Add RED tests for summary validation and expected artifact generation.

Use a temporary synthetic `results.csv`; assert rejection of missing schemes/seeds, correct geometric means and speedups, and creation of nonempty PNG/PDF outputs without relying on the production results directory.

- [ ] Implement figures with readable scheme labels and seed points.

Generate:

```text
figures/websearch100_p99_absolute.png
figures/websearch100_p99_absolute.pdf
figures/websearch100_speedup_vs_nmrc.png
figures/websearch100_speedup_vs_nmrc.pdf
figures/websearch100_mechanism_rates.png
figures/websearch100_mechanism_rates.pdf
report.md
```

The absolute plot shows all seed points plus geometric mean. The speedup plot uses current rerun `n-mrc` as 1.0 and clearly indicates `>1` is better. The mechanism plot reports reroute/check, FastCNP generated/check, cooldown starts/selection, all-cooling fallbacks or episodes/selection, and ECN/TRIM where available. The report is answer-first, includes exact seed values and geomeans, states uncertainty from three seeds, and does not generalize beyond healthy WebSearch 100%.

- [ ] Run GREEN tests and commit.

```bash
MPLBACKEND=Agg python3 -B experiments/n-mrc/tests/test_plot_nmrc_websearch100_fourway.py
```

Commit:

```bash
git add experiments/n-mrc/plot_nmrc_websearch100_fourway.py experiments/n-mrc/tests/test_plot_nmrc_websearch100_fourway.py
git commit -m "feat: plot nmrc websearch four-way results"
```

## Task 6: Full verification and smoke qualification

**Files:** No planned production changes; fix only demonstrated regressions.

- [ ] Build clean targets and run focused/full relevant tests.

```bash
make -C sim/tests -B main_sglb_quality.o
make -C sim/tests -j4 htsim_sglb_quality htsim_hybrid_nmrc htsim_nmrc_score
make -C sim/datacenter -j4 htsim_roce
./sim/tests/htsim_sglb_quality
./sim/tests/htsim_hybrid_nmrc
./sim/tests/htsim_nmrc_score
python3 -B sim/tests/test_hybrid_nmrc_interface.py
python3 -B sim/tests/test_mainline_compatibility_modes.py
python3 -B sim/tests/test_nmrc_interface.py
python3 -B experiments/n-mrc/tests/test_nmrc_websearch100_fourway.py
MPLBACKEND=Agg python3 -B experiments/n-mrc/tests/test_plot_nmrc_websearch100_fourway.py
```

- [ ] Run one tiny/single-seed smoke per preset and verify resolved configuration plus invariants.

Required invariants:

```text
n-mrc: legacy policy fields
n-mrc-allcool-rr-reset: only all_cooling differs
n-mrc1: fastcnp_generated=0 and cooldown start/skip/recovery=0
n-mrc2: binary_paired_actions=committed_binary_reroutes=fastcnp_generated
```

- [ ] Record verification commands/results in the branch ledger and commit any test-only correction separately.

## Task 7: Run 12 cells, validate data, and render deliverables

**Files:** Generated artifacts only under the experiment output directory.

- [ ] Launch the four scheme workers and monitor until every seed succeeds.

```bash
python3 -B experiments/n-mrc/run_nmrc_websearch100_fourway.py \
  --output experiments/n-mrc/output/nmrc_websearch100_fourway_20260721 \
  --simulator sim/datacenter/htsim_roce \
  --workers 4
```

Do not stop after process launch. Poll workers, investigate any failed cell with the raw log, rerun only after a concrete fix, and use the fingerprinted resume path for completed cells.

- [ ] Validate the completed dataset before plotting.

Assert programmatically:

- exactly 12 successful cells;
- each scheme has seeds `{13,29,47}`;
- for each seed, all four traffic SHA256 values are identical;
- all cells share one simulator SHA256;
- no missing/non-finite p99/p99.9/max metrics;
- resolved preset/policy fields match the four mappings;
- N-MRC1 cooldown/FastCNP invariants and N-MRC2 paired-action equality hold;
- every completed RR-reset episode has selection count divisible by eight and reset count no greater than episode count.

- [ ] Render figures/report and inspect PNGs at full resolution.

```bash
MPLBACKEND=Agg python3 -B experiments/n-mrc/plot_nmrc_websearch100_fourway.py \
  --input experiments/n-mrc/output/nmrc_websearch100_fourway_20260721/results.csv \
  --output experiments/n-mrc/output/nmrc_websearch100_fourway_20260721
```

Open all three PNG files, check labels/units/no clipping, and rerender if needed.

- [ ] Provide the user the absolute output links, a compact seed/geomean table, the speedup interpretation, and mechanism evidence explaining whether RR-reset, stateless random, or binary paired signaling improved WebSearch 100%.
