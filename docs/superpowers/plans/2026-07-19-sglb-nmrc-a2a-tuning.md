# SGLB / n-MRC All-to-All Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a reproducible parameter search that retains SGLB and n-MRC only when their two-hop queue awareness beats per-packet AR under the approved All-to-All geometric-mean gates.

**Architecture:** Add one standalone tuning runner that imports the canonical-six runner for traffic generation, command construction, parsing, and Exact+Bounded validation. The runner owns a fixed candidate registry, a seed-13 All-to-All screen, a three-seed All-to-All validation, and a final six-scenario validation; selection is deterministic and uses paired geometric-mean ratios to AR. Only winning universal parameters are copied into the canonical runner; a failing pure scheme is removed from the canonical recommendation matrix.

**Tech Stack:** Python 3 standard library, htsim RoCE simulator, existing n-MRC experiment helpers, standalone assertion-based Python tests, Git.

---

### Task 1: Define the fixed tuning matrix and commands

**Files:**
- Create: `experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py`
- Create: `sim/tests/test_sglb_nmrc_a2a_tuning_runner.py`

- [ ] **Step 1: Write the failing candidate-registry test**

Create `sim/tests/test_sglb_nmrc_a2a_tuning_runner.py` with a loader matching the
other n-MRC runner tests, then assert the fixed score profiles and selector matrix:

```python
assert tuple(runner.SCORE_PROFILES) == (
    "current",
    "fresh_current",
    "wide_low",
    "tail_low",
    "sparse_low",
)
assert runner.SCORE_PROFILES["current"] == runner.ScoreProfile(
    local_update_us=1.0,
    gcn_update_us=5.0,
    gcn_aging_us=30.0,
    q_min=0.20,
    q_max=0.80,
    degraded=0.10,
    bad=0.40,
    avoid=0.60,
)
assert runner.SCORE_PROFILES["fresh_current"] == runner.ScoreProfile(
    0.5, 1.0, 6.0, 0.20, 0.80, 0.10, 0.40, 0.60)
assert runner.SCORE_PROFILES["wide_low"] == runner.ScoreProfile(
    0.5, 1.0, 6.0, 0.05, 0.50, 0.05, 0.25, 0.50)
assert runner.SCORE_PROFILES["tail_low"] == runner.ScoreProfile(
    0.5, 1.0, 6.0, 0.01, 0.35, 0.05, 0.25, 0.55)
assert runner.SCORE_PROFILES["sparse_low"] == runner.ScoreProfile(
    0.5, 1.0, 6.0, 0.00, 0.20, 0.05, 0.30, 0.60)

sglb = [item for item in runner.candidate_matrix() if item.scheme == "sglb"]
nmrc = [item for item in runner.candidate_matrix() if item.scheme == "nmrc"]
assert len(sglb) == 30
assert len(nmrc) == 15
assert {(item.levels, item.min_choices) for item in sglb} == {
    (4, 1), (4, 2), (4, 3), (8, 1), (8, 2), (8, 3),
}
assert {(item.levels, item.min_choices) for item in nmrc} == {
    (4, 1), (4, 2), (4, 3),
}
assert all(item.reroute_policy == "better_ge3" for item in nmrc)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 sim/tests/test_sglb_nmrc_a2a_tuning_runner.py
```

Expected: fail because `run_sglb_nmrc_a2a_tuning.py` does not exist.

- [ ] **Step 3: Implement the candidate registry**

Create `experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py` with immutable records and
the exact candidate construction:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ScoreProfile:
    local_update_us: float
    gcn_update_us: float
    gcn_aging_us: float
    q_min: float
    q_max: float
    degraded: float
    bad: float
    avoid: float

@dataclass(frozen=True)
class Candidate:
    key: str
    scheme: str
    profile: str
    levels: int
    min_choices: int
    reroute_policy: str = "better_ge3"

SCORE_PROFILES = {
    "current": ScoreProfile(1.0, 5.0, 30.0, 0.20, 0.80, 0.10, 0.40, 0.60),
    "fresh_current": ScoreProfile(0.5, 1.0, 6.0, 0.20, 0.80, 0.10, 0.40, 0.60),
    "wide_low": ScoreProfile(0.5, 1.0, 6.0, 0.05, 0.50, 0.05, 0.25, 0.50),
    "tail_low": ScoreProfile(0.5, 1.0, 6.0, 0.01, 0.35, 0.05, 0.25, 0.55),
    "sparse_low": ScoreProfile(0.5, 1.0, 6.0, 0.00, 0.20, 0.05, 0.30, 0.60),
}

def candidate_matrix():
    result = []
    for profile in SCORE_PROFILES:
        for levels in (4, 8):
            for min_choices in (1, 2, 3):
                result.append(Candidate(
                    f"sglb_{profile}_l{levels}_k{min_choices}",
                    "sglb", profile, levels, min_choices))
        for min_choices in (1, 2, 3):
            result.append(Candidate(
                f"nmrc_{profile}_l4_k{min_choices}",
                "nmrc", profile, 4, min_choices))
    return tuple(result)
```

Add `candidate_args(candidate)` that always emits the following explicit options:

```python
(
    "-sglb_score_mode", "nmrc_quantized_topk",
    "-sglb_update_us", fmt(profile.local_update_us),
    "-sglb_gcn_update_us", fmt(profile.gcn_update_us),
    "-sglb_gcn_aging_us", fmt(profile.gcn_aging_us),
    "-sglb_nmrc_q_range", fmt(profile.q_min), fmt(profile.q_max),
    "-sglb_nmrc_level_thresholds",
    fmt(profile.degraded), fmt(profile.bad), fmt(profile.avoid),
    "-sglb_nmrc_levels", str(candidate.levels),
    "-sglb_min_choices", str(candidate.min_choices),
)
```

For `candidate.scheme == "nmrc"`, append the existing canonical options
`-nmrc_ev_mode encoded -nmrc_reroute_policy better_ge3 -nmrc_fastcnp on`.

- [ ] **Step 4: Extend the test to assert command isolation**

Build one AR, SGLB, and n-MRC command for the same materialized traffic and assert:

```python
assert option_value(ar_command, "-lb") == "adaptive-routing"
assert option_value(ar_command, "-ar_granularity") == "packet"
assert not any(token.startswith("-sglb_") for token in ar_command)
assert option_value(sglb_command, "-lb") == "sglb"
assert "-nmrc_ev_mode" not in sglb_command
assert option_value(nmrc_command, "-lb") == "n-mrc"
assert option_value(nmrc_command, "-nmrc_reroute_policy") == "better_ge3"
```

Also assert the three screen scenarios are exactly the canonical All-to-All scenarios
and every `(scenario, seed)` block has one traffic SHA-256 across all candidates.

- [ ] **Step 5: Run the test and verify GREEN**

Run:

```bash
python3 sim/tests/test_sglb_nmrc_a2a_tuning_runner.py
```

Expected: exit 0 with no output.

- [ ] **Step 6: Commit Task 1**

```bash
git add experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py \
  sim/tests/test_sglb_nmrc_a2a_tuning_runner.py
git commit -m "test: define SGLB n-MRC tuning matrix"
```

### Task 2: Implement paired geometric-mean selection and retention gates

**Files:**
- Modify: `experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py`
- Modify: `sim/tests/test_sglb_nmrc_a2a_tuning_runner.py`

- [ ] **Step 1: Write failing aggregation tests**

Add synthetic rows for three scenarios, three seeds, AR, one passing SGLB candidate,
one failing SGLB candidate, current n-MRC, and a regressed n-MRC candidate. Assert:

```python
summary = runner.summarize_candidate(rows, "sglb_pass")
assert math.isclose(summary["a2a_ratio_gm"], 0.97)
assert summary["a2a_scenario_wins"] == 2
assert summary["worst_a2a_scenario_ratio"] == 1.02
assert runner.retention_passes(summary, scheme="sglb")

assert not runner.retention_passes(
    runner.summarize_candidate(rows, "sglb_slow"), scheme="sglb")
assert not runner.nmrc_not_regressed(
    runner.summarize_candidate(rows, "nmrc_regressed"),
    runner.summarize_candidate(rows, "nmrc_current"))
```

The synthetic ratios must be generated as exact positive primary values, not inserted
as already aggregated values, so the test exercises paired cell aggregation.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 sim/tests/test_sglb_nmrc_a2a_tuning_runner.py
```

Expected: fail because the aggregation functions are absent.

- [ ] **Step 3: Implement the metric functions**

Add:

```python
def geometric_mean(values):
    values = tuple(values)
    if not values or any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("geometric mean requires finite positive values")
    return math.exp(sum(math.log(value) for value in values) / len(values))

def paired_ratios(rows, candidate_key, scenarios=None, seeds=None):
    by_cell = {(row["scenario"], row["seed"], row["candidate"]):
               float(row["primary_us"]) for row in rows}
    scenarios = tuple(scenarios or sorted({row["scenario"] for row in rows}))
    seeds = tuple(seeds or sorted({int(row["seed"]) for row in rows}))
    result = []
    for scenario in scenarios:
        for seed in seeds:
            candidate = by_cell[scenario, seed, candidate_key]
            ar = by_cell[scenario, seed, "ar"]
            result.append((scenario, seed, candidate / ar))
    return result
```

Implement `summarize_candidate` from the scenarios and seeds actually present in the
phase with exact fields:

- `overall_ratio_gm` over all available paired cells;
- `a2a_ratio_gm` over All-to-All cells;
- `p2p_ratio_gm` over P2P/WebSearch cells;
- `scenario_ratio_gm` mapping each scenario to its three-seed GM;
- `a2a_scenario_wins` count where scenario GM `< 1.0`;
- `worst_a2a_scenario_ratio` maximum All-to-All scenario GM.

Apply the approved full retention gates only to `validate-six`, where all six scenarios
are present. The screen and `validate-a2a` phases rank by `a2a_ratio_gm`, then by worst
A2A scenario and candidate key; they do not pretend to have an overall six-scenario GM.
Implement the approved gates exactly:

```python
def retention_passes(summary, scheme):
    return (
        summary["a2a_ratio_gm"] <= 0.98 and
        summary["a2a_scenario_wins"] >= 2 and
        summary["worst_a2a_scenario_ratio"] <= 1.03 and
        summary["overall_ratio_gm"] < 1.0
    )

def nmrc_not_regressed(candidate, current):
    return (
        candidate["a2a_ratio_gm"] <= current["a2a_ratio_gm"] and
        candidate["overall_ratio_gm"] <= current["overall_ratio_gm"]
    )
```

- [ ] **Step 4: Add strict matrix and diagnostics validation**

Reject duplicate/missing `(scenario, seed, candidate)`, nonpositive primary values,
failed return/config/completion flags, traffic-hash mismatches within a cell, and SGLB/n-MRC
runs where:

```python
remote_snapshot_used <= 0
remote_snapshot_missing / (remote_snapshot_used + remote_snapshot_missing) > 0.01
```

For n-MRC additionally retain the canonical FastCNP and Exact+Bounded invariants.

- [ ] **Step 5: Run the test and verify GREEN**

```bash
python3 sim/tests/test_sglb_nmrc_a2a_tuning_runner.py
```

Expected: exit 0.

- [ ] **Step 6: Commit Task 2**

```bash
git add experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py \
  sim/tests/test_sglb_nmrc_a2a_tuning_runner.py
git commit -m "feat: enforce global-LB retention gates"
```

### Task 3: Implement resumable screen and validation phases

**Files:**
- Modify: `experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py`
- Modify: `sim/tests/test_sglb_nmrc_a2a_tuning_runner.py`
- Modify: `sim/tests/test_nmrc_consolidated_assets.py`

- [ ] **Step 1: Write failing phase/CLI tests**

Assert:

```python
screen = runner.parse_args(["--phase", "screen", "--seeds", "13"])
assert screen.phase == "screen"
assert screen.seeds == (13,)
assert len(runner.make_specs(screen)) == 138  # 45 candidates + AR, 3 A2A cells

validate = runner.parse_args([
    "--phase", "validate-a2a",
    "--candidate-file", "/tmp/selected.json",
    "--seeds", "13,29,47",
])
assert validate.phase == "validate-a2a"
```

Add the runner and its test to `sim/tests/test_nmrc_consolidated_assets.py`.

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 sim/tests/test_sglb_nmrc_a2a_tuning_runner.py
python3 sim/tests/test_nmrc_consolidated_assets.py
```

Expected: the phase API or consolidated asset assertion fails.

- [ ] **Step 3: Implement execution and reuse**

Follow `run_nmrc_canonical_six_compare.py` for:

- command, simulator, and traffic SHA-256 fingerprints;
- per-case `stdout.log`, `command.txt`, `returncode.txt`, `runtime.txt`,
  `fingerprint.txt`, and `idmap.txt`;
- `ThreadPoolExecutor` execution;
- exact parsing and configuration echo validation;
- `summary.csv`, `ranking.csv`, `selected_candidates.json`, and Markdown report.

Use these phase matrices:

```python
PHASE_SCENARIOS = {
    "screen": tuple(s for s in canonical.SCENARIOS
                    if s not in canonical.P2P_SCENARIOS),
    "validate-a2a": tuple(s for s in canonical.SCENARIOS
                          if s not in canonical.P2P_SCENARIOS),
    "validate-six": canonical.SCENARIOS,
}
```

`screen` runs all 45 candidates plus AR with seed 13. Rank candidates separately by
scheme using A2A paired GM, then write the best two keys per scheme to JSON:

```json
{
  "sglb": ["first", "second"],
  "nmrc": ["first", "second"]
}
```

`validate-a2a` runs those four candidates plus AR for seeds 13/29/47 and selects the
best candidate per scheme by A2A paired GM. `validate-six` runs the two winners plus AR
for all six scenarios and three seeds, then applies the full retention gates.

- [ ] **Step 4: Implement dry-run manifest validation**

`--dry-run` must write `commands.tsv`, validate the complete phase matrix and traffic
hash reuse, print `validated <N> commands`, and launch no simulator process.

- [ ] **Step 5: Run tests and verify GREEN**

```bash
python3 sim/tests/test_sglb_nmrc_a2a_tuning_runner.py
python3 sim/tests/test_nmrc_consolidated_assets.py
python3 experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py \
  --phase screen --seeds 13 --dry-run \
  --out experiments/n-mrc/output/sglb_nmrc_a2a_tuning/screen
```

Expected: both tests exit 0 and dry-run prints `validated 138 commands`.

- [ ] **Step 6: Commit Task 3**

```bash
git add experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py \
  sim/tests/test_sglb_nmrc_a2a_tuning_runner.py \
  sim/tests/test_nmrc_consolidated_assets.py
git commit -m "feat: add resumable global-LB tuning runner"
```

### Task 4: Run the parameter search and three-seed All-to-All validation

**Files:**
- Generated locally: `experiments/n-mrc/output/sglb_nmrc_a2a_tuning/screen/*`
- Generated locally: `experiments/n-mrc/output/sglb_nmrc_a2a_tuning/validate-a2a/*`

- [ ] **Step 1: Build the simulator and run the screen**

```bash
make -C sim/datacenter -j4 htsim_roce
python3 experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py \
  --phase screen --seeds 13 --workers 6 --timeout 3600 \
  --out experiments/n-mrc/output/sglb_nmrc_a2a_tuning/screen
```

Expected: 138 valid cells, no incomplete flows, and
`screen/selected_candidates.json` contains two SGLB and two n-MRC keys.

- [ ] **Step 2: Review diagnostics before validation**

Run:

```bash
python3 experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py \
  --phase report --input \
  experiments/n-mrc/output/sglb_nmrc_a2a_tuning/screen/summary.csv
```

Expected: selected candidates have remote snapshot miss ratio at most 1%, finite
positive CCT, and nonzero `avg_distinct_qualities`/`avg_score_spread`.

- [ ] **Step 3: Run three-seed All-to-All validation**

```bash
python3 experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py \
  --phase validate-a2a --seeds 13,29,47 --workers 6 --timeout 3600 \
  --candidate-file \
  experiments/n-mrc/output/sglb_nmrc_a2a_tuning/screen/selected_candidates.json \
  --out experiments/n-mrc/output/sglb_nmrc_a2a_tuning/validate-a2a
```

Expected: 45 valid cells and one selected candidate per scheme.

- [ ] **Step 4: Independently recompute the A2A result**

Use a short read-only Python check over `validate-a2a/summary.csv` to recompute each
candidate/AR ratio at `(scenario, seed)` grain, its geometric mean, scenario wins, and
worst scenario. Assert the values equal `ranking.csv` within `1e-12`.

- [ ] **Step 5: Record the checkpoint**

Do not change default source yet. Commit only source/test improvements needed to make
the completed screen/validation reproducible; experiment outputs remain ignored.

### Task 5: Run full six-scenario validation and apply retention decisions

**Files:**
- Generated locally: `experiments/n-mrc/output/sglb_nmrc_a2a_tuning/validate-six/*`
- Modify on pass/removal: `experiments/n-mrc/run_nmrc_canonical_six_compare.py`
- Modify on pass/removal: `sim/tests/test_nmrc_canonical_six_compare_runner.py`
- Modify: `experiments/n-mrc/README.md`

- [ ] **Step 1: Run the two winners against fixed AR**

```bash
python3 experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py \
  --phase validate-six --seeds 13,29,47 --workers 6 --timeout 3600 \
  --candidate-file \
  experiments/n-mrc/output/sglb_nmrc_a2a_tuning/validate-a2a/selected_candidates.json \
  --out experiments/n-mrc/output/sglb_nmrc_a2a_tuning/validate-six
```

Expected: 54 valid cells and a final report with an explicit PASS/FAIL for SGLB and
n-MRC under every retention predicate.

- [ ] **Step 2: Write the failing canonical-default test for the measured decision**

If a scheme passes, assert its complete measured option tuple is present in
`SCHEME_ARGS`. If pure SGLB fails, assert `"sglb"` is absent from `SCHEMES`; do not
delete the shared SGLB scoring implementation because n-MRC depends on it. If n-MRC
fails, remove it from `SCHEMES` and the default recommendation, while retaining only
the baseline mechanisms needed by remaining schemes.

For a passing candidate the test must assert every explicit value, for example:

```python
args = runner.SCHEME_ARGS["sglb"]
assert option_value(args, "-sglb_update_us") == measured["local_update_us"]
assert option_value(args, "-sglb_gcn_update_us") == measured["gcn_update_us"]
assert option_value(args, "-sglb_gcn_aging_us") == measured["gcn_aging_us"]
assert option_values(args, "-sglb_nmrc_q_range", 2) == measured["q_range"]
assert option_values(args, "-sglb_nmrc_level_thresholds", 3) == measured["thresholds"]
assert option_value(args, "-sglb_nmrc_levels") == measured["levels"]
assert option_value(args, "-sglb_min_choices") == measured["min_choices"]
```

- [ ] **Step 3: Run the canonical test and verify RED**

```bash
python3 sim/tests/test_nmrc_canonical_six_compare_runner.py
```

Expected: fail because the measured decision has not been applied.

- [ ] **Step 4: Apply only the measured universal configuration**

Update `SCHEME_ARGS`, configuration validation, report labels, and README. Do not add
scenario-specific branches. Update the README with:

- SGLB and n-MRC selected parameter tuples;
- All-to-All, overall, and per-scenario geometric-mean ratios to AR;
- PASS/FAIL status and any removed scheme;
- GCN-state abstraction and update-period caveat.

- [ ] **Step 5: Run canonical tests and verify GREEN**

```bash
python3 sim/tests/test_nmrc_canonical_six_compare_runner.py
python3 sim/tests/test_sglb_nmrc_a2a_tuning_runner.py
python3 sim/tests/test_nmrc_consolidated_assets.py
```

Expected: all exit 0.

- [ ] **Step 6: Commit the retention decision**

```bash
git add experiments/n-mrc/run_nmrc_canonical_six_compare.py \
  experiments/n-mrc/run_sglb_nmrc_a2a_tuning.py \
  experiments/n-mrc/README.md \
  sim/tests/test_nmrc_canonical_six_compare_runner.py \
  sim/tests/test_sglb_nmrc_a2a_tuning_runner.py \
  sim/tests/test_nmrc_consolidated_assets.py
git commit -m "perf: select global-LB parameters against AR"
```

### Task 6: Final verification and evidence handoff

**Files:**
- Verify: `experiments/n-mrc/output/sglb_nmrc_a2a_tuning/validate-six/summary.csv`
- Verify: `experiments/n-mrc/output/sglb_nmrc_a2a_tuning/validate-six/ranking.csv`
- Verify: `experiments/n-mrc/output/sglb_nmrc_a2a_tuning/validate-six/sglb_nmrc_a2a_tuning_for_gpt.md`

- [ ] **Step 1: Run focused tests and build checks**

```bash
python3 sim/tests/test_sglb_nmrc_a2a_tuning_runner.py
python3 sim/tests/test_nmrc_canonical_six_compare_runner.py
python3 sim/tests/test_nmrc_consolidated_assets.py
make -C sim/datacenter -j4 htsim_roce
git diff --check HEAD~1 HEAD
```

Expected: all commands exit 0.

- [ ] **Step 2: Re-parse every final raw cell**

Load the final specs, parse every raw log through the tuning runner, call the same
matrix/config/completion validators used by the run, and require zero invalid rows.

- [ ] **Step 3: Independently verify all headline ratios**

Recompute from `summary.csv` and require exact agreement with `ranking.csv` at displayed
precision. Verify that every reported cross-seed metric is geometric mean and that no
median field appears in the final report.

- [ ] **Step 4: Request code and experiment review**

Dispatch a reviewer with the approved retention gates, base SHA, head SHA, source diff,
and final `summary.csv`/`ranking.csv`. Resolve every Critical or Important issue before
claiming completion.

- [ ] **Step 5: Report the actual outcome**

State separately for SGLB and n-MRC:

- chosen universal parameters or removal decision;
- A2A paired GM vs AR;
- overall paired GM vs AR;
- each All-to-All scenario three-seed GM vs AR;
- diagnostics proving remote snapshots were used;
- validation counts and remaining simulation abstraction caveats.

Do not claim GitHub publication unless `git push` succeeds; if credentials remain
unavailable, provide the local commit SHA and exact push command.
