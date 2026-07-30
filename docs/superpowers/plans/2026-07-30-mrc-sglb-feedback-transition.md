# MRC/SGLB Feedback Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a controlled three-seed experiment showing MRC's initial disadvantage to warmed SGLB and its FCT recovery after per-QP feedback becomes actionable.

**Architecture:** Add a standalone experiment runner that reuses the existing
warmed-short-flow parsing and deterministic artifact helpers. It runs MRC,
SGLB, and RR on identical mixed-size traffic, records first-update phase
diagnostics, strictly pairs flows, aggregates the two FCT ratios, and renders
one transition figure. A small seed-13 pilot chooses one sustained hotspot
rate before the fixed three-seed matrix.

**Tech Stack:** Python 3 standard library, htsim RoCE binary, matplotlib,
pytest, CSV and deterministic gzip artifacts.

## Global Constraints

- Use 128 nodes, two tiers, eight paths, exact-bounded RoCE, DCQCN variant,
  4 KiB MTU, and identical transport, queue, topology, and latency options.
- Reuse existing WebSearch sizes from 6 KiB through 30 MiB.
- Commands differ only in `-lb` and `-o`.
- Keep five hot paths active for the full measurement window and leave three
  clean paths.
- Final evidence uses seeds 13, 29, and 47.
- Preserve unrelated modifications in
  `experiments/n-mrc/four_scheme_risks_and_evidence.md` and
  `experiments/n-mrc/plot_nmrc_routing_cooldown_matrix.py`.

---

### Task 1: Transition runner contracts

**Files:**
- Create: `sim/tests/test_mrc_sglb_feedback_transition_runner.py`
- Create: `experiments/n-mrc/run_mrc_sglb_feedback_transition.py`

**Interfaces:**
- Consumes: parsing, percentile, atomic-write, hashing, and deterministic-gzip
  helpers from `run_mrc_sglb_warmed_short_flow.py`.
- Produces:
  `build_transition_flows(seed: int, sample_scale: float) -> list[dict]`,
  `build_command(sim, scheme, traffic, output, connections, seed, rate) -> list[str]`,
  `parse_mrc_phase_diags(text: str) -> dict[int, dict]`,
  `pair_flows(seed, traffic, completions, diags) -> list[dict]`, and
  `aggregate(rows) -> tuple[list[dict], list[dict]]`.

- [ ] **Step 1: Write failing contract tests**

```python
def test_transition_traffic_has_existing_sizes_and_adaptive_counts():
    flows = runner.build_transition_flows(13, sample_scale=0.01)
    assert set(f["flow_size"] for f in flows) == set(runner.SIZE_COUNTS)
    assert min(f["start_us"] for f in flows) >= 100
    assert max(f["start_us"] for f in flows) <= 5100
    assert all(f["src"] != f["dst"] for f in flows)


def test_three_commands_differ_only_by_lb_and_output(tmp_path):
    commands = {
        scheme: runner.build_command(
            Path("/sim"), scheme, Path("/traffic"),
            tmp_path / scheme, 100, 13, 340)
        for scheme in ("mrc", "sglb", "rr")
    }
    normalized = []
    for scheme, command in commands.items():
        options = {command[i]: command[i + 1]
                   for i in range(1, len(command), 2)}
        assert options.pop("-lb") == scheme
        options.pop("-o")
        normalized.append(options)
    assert normalized[0] == normalized[1] == normalized[2]
    assert normalized[0]["-path_hotspot_bg_on_us"] == "40000"


def test_phase_fraction_and_paired_ratios():
    paired = runner.pair_flows(
        13, TRAFFIC, COMPLETIONS_FOR_MRC_SGLB_RR, MRC_DIAGS)
    assert paired[0]["mrc_sglb_ratio"] == 2.0
    assert paired[0]["mrc_rr_ratio"] == 0.8
    assert paired[0]["post_update_selection_fraction"] == 0.75
```

- [ ] **Step 2: Verify the tests fail before implementation**

Run:

```bash
uv run --with pytest python -m pytest -q \
  sim/tests/test_mrc_sglb_feedback_transition_runner.py
```

Expected: collection fails because
`run_mrc_sglb_feedback_transition.py` does not exist.

- [ ] **Step 3: Implement deterministic traffic and command construction**

Use this fixed size/count map:

```python
SIZE_COUNTS = {
    6144: 1000, 13312: 1000, 19456: 1000, 33792: 1000,
    54272: 1000, 136192: 1000, 683008: 300, 1364992: 300,
    3412992: 100, 6827008: 60, 20480000: 30, 30720000: 20,
}
SEEDS = (13, 29, 47)
SCHEMES = ("mrc", "sglb", "rr")
```

`build_transition_flows` must use a seed-local `random.Random`, uniformly
select distinct endpoints, uniformly choose start times from 100–5,100 us,
and retain at least one flow per size when scaled for a pilot.

`build_command` must include a 40,000 us simulation end and a 40,000 us
continuous hotspot ON interval. The selected pilot rate is passed explicitly;
all other options match the warmed-short-flow runner.

- [ ] **Step 4: Implement phase diagnostics and aggregation**

Parse these existing `MrcFlowDiag` fields:

```python
PHASE_FIELDS = (
    "new_data_selections",
    "packets_before_first_update",
    "new_selections_after_first_update",
    "actionable_feedback",
    "quality_feedback_before_done",
    "effective_state_updates",
    "unique_active_evs",
    "full_sweeps",
)
```

For each paired flow compute:

```python
mrc_sglb_ratio = mrc_fct_us / sglb_fct_us
mrc_rr_ratio = mrc_fct_us / rr_fct_us
post_update_selection_fraction = (
    new_selections_after_first_update / new_data_selections
    if new_data_selections else 0.0
)
```

Aggregate per seed and size, then across seeds, using the geometric mean of
strictly paired FCT ratios. Include p50/p99 per scheme, actionable fraction,
quality-feedback fraction, post-update selection fraction, EV coverage,
seed min/max, and count of seeds with a ratio below one.

- [ ] **Step 5: Implement cell validation and deterministic artifacts**

Validate exact completion-ID equality for all schemes. Require MRC diagnostics
for every foreground flow and `SglbRouteDiag` for every SGLB cell. Cache a cell
only when simulator SHA, traffic SHA, command SHA, completion count, and
scheme-specific diagnostics all match.

Write:

```text
cells.csv
seed_summary.csv
summary.csv
sglb_route_summary.csv
paired_flow_metrics.csv.gz
focused_transition.png
focused_transition.pdf
```

The figure has two panels on one log-size x-axis:

1. MRC/SGLB and MRC/RR paired FCT geometric means with a ratio-one line.
2. MRC actionable-flow fraction and mean post-update selection fraction.

- [ ] **Step 6: Run tests and commit the runner**

Run:

```bash
uv run --with pytest python -m pytest -q \
  sim/tests/test_mrc_sglb_feedback_transition_runner.py \
  sim/tests/test_mrc_sglb_warmed_short_flow_runner.py
python3 -m py_compile \
  experiments/n-mrc/run_mrc_sglb_feedback_transition.py
```

Expected: all tests pass and compilation exits zero.

Commit only the runner and its test:

```bash
git add -f experiments/n-mrc/run_mrc_sglb_feedback_transition.py
git add sim/tests/test_mrc_sglb_feedback_transition_runner.py
git commit -m "exp: add MRC SGLB feedback transition runner"
```

### Task 2: Pressure calibration

**Files:**
- Generated: `experiments/n-mrc/output/mrc_sglb_feedback_transition_pilot_<rate>/`
- Modify: `docs/superpowers/specs/2026-07-30-mrc-sglb-feedback-transition-design.md`

**Interfaces:**
- Consumes: runner `--hotspot-rate`, `--sample-scale`, and `--seeds` options.
- Produces: one fixed rate for the full matrix and a written rejection record.

- [ ] **Step 1: Run seed-13 pilot rates**

Run rates 300, 340, and 380 Gbit/s with `--sample-scale 0.05 --seeds 13`.
Each run must finish all MRC, SGLB, and RR flows.

- [ ] **Step 2: Select the rate using predeclared rules**

Read each pilot's `summary.csv` and `sglb_route_summary.csv`. Select the
highest rate that:

- has no incomplete flows;
- has short-flow actionable fraction at or near zero;
- has long-flow actionable and post-update fractions materially above zero;
- has SGLB all-zero-quality calls below 50%;
- does not leave MRC/SGLB elevated without recovery at every long size.

Record every tried rate and the exact rejection reason in the design's
calibration section.

- [ ] **Step 3: Commit the calibration record**

```bash
git add -f \
  docs/superpowers/specs/2026-07-30-mrc-sglb-feedback-transition-design.md
git commit -m "docs: calibrate MRC SGLB transition pressure"
```

### Task 3: Final matrix and analytical validation

**Files:**
- Generate and force-add:
  `experiments/n-mrc/output/mrc_sglb_feedback_transition/`

**Interfaces:**
- Consumes: fixed calibrated hotspot rate and seeds 13, 29, 47.
- Produces: final paired data, summaries, route diagnostics, and figure.

- [ ] **Step 1: Run the fixed final matrix**

Run the runner with the calibrated rate, full sample scale, and all three
seeds. Do not change the rate or flow counts after seeing individual size
results.

- [ ] **Step 2: Independently recompute the headline metrics**

Read `paired_flow_metrics.csv.gz` in a separate Python process. For every size,
recompute both geometric-mean ratios, actionable fraction, post-update
selection fraction, and seed min/max. Assert equality with `summary.csv` to
`1e-12` relative or absolute tolerance.

- [ ] **Step 3: Validate reproducibility and figure**

Run the cached matrix twice and compare SHA-256 for all seven final artifacts.
Open `focused_transition.png` and check that both ratios, the ratio-one line,
both mechanism fractions, axis labels, and direction notes are readable.

- [ ] **Step 4: Commit final data**

Force-add only the seven final artifacts. Do not add raw stdout, generated
traffic, pilot artifacts, or unrelated user modifications.

### Task 4: Report the transition, not a universal win

**Files:**
- Modify: `experiments/n-mrc/mrc_sglb_pressure_transition_example.md`

**Interfaces:**
- Consumes: final `summary.csv`, `seed_summary.csv`,
  `sglb_route_summary.csv`, and `focused_transition.png`.
- Produces: the durable explanation requested by the user.

- [ ] **Step 1: Replace the short-only proof framing**

Lead with the transition:

- MRC may lose during short-flow/long-QP warm-up because feedback cannot affect
  a later new-data choice.
- Once post-update choices occupy a material share of the flow, MRC recovers
  and MRC/SGLB FCT approaches one.
- MRC/RR improving at the same point attributes the recovery to feedback.

State separately that receiving feedback is weaker than feedback being
actionable.

- [ ] **Step 2: Add exact tables, chart, and boundaries**

Include per-size flow counts, actionable fraction, post-update fraction,
MRC/SGLB ratio, MRC/RR ratio, p99 ratios, and seed range. State whether MRC
ever beats SGLB, but do not make that a success requirement.

Retain these limits:

- SGLB must already have discriminative shared state;
- its real control-plane cost is not modeled;
- the hotspot is sustained path asymmetry, not a failure experiment;
- the result demonstrates a scenario and mechanism boundary, not universal
  dominance.

- [ ] **Step 3: Run final verification and commit**

Run all runner tests, `py_compile`, `git diff --check`, report-link checks,
independent metric recomputation, and a cached experiment replay. Commit the
report and final artifacts, then verify only the two pre-existing unrelated
modified files remain in `git status --short`.

