# Mainline and Experiment Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the canonical 64-spine MRC/SGLB implementation and reproducible experiment evidence onto `main-htsim`, archive research history, and remove redundant raw outputs and worktrees.

**Architecture:** Treat `main-htsim` as the only executable implementation. Preserve historical code through Git archive refs and preserve experiment conclusions through a tracked asset ledger plus curated manifests, summaries, reports, figures, and hashes. Delete only after the ledger, archive refs, and retained-file hashes are committed.

**Tech Stack:** Git, GNU Make, C++11 htsim tests, Python 3/pytest, Markdown/JSON/CSV experiment artifacts.

## Global Constraints

- Preserve the canonical 64-spine/64-path topology and all four approved MRC/SGLB semantics.
- Do not merge historical C++ implementations into `main-htsim` wholesale.
- Preserve current P2P/A2A conclusions and n-MRC exploration history.
- Treat `sample_scale < 1.0`, `quick`, and `pilot` outputs as smoke-only evidence.
- Preserve commands, configuration, hashes, summaries, reports, and necessary compressed logs before deleting raw directories.
- Do not modify remote refs, push, or create a PR.
- Do not remove a dirty worktree until its source changes have been compared and either archived or proven redundant.

---

### Task 1: Establish Safety Refs and the Pre-Cleanup Inventory

**Files:**
- Create: `experiments/n-mrc/evidence/consolidation/pre_cleanup_inventory.md`
- Create: `experiments/n-mrc/evidence/consolidation/worktrees_before.tsv`
- Create: `experiments/n-mrc/evidence/consolidation/branches_before.tsv`
- Create: `experiments/n-mrc/evidence/consolidation/storage_before.tsv`

**Interfaces:**
- Consumes: current `main-htsim`, local refs, worktree list, output directories.
- Produces: `archive/pre-consolidation-20260817` and the authoritative before-state used by every cleanup task.

- [ ] **Step 1: Verify the starting commit and tracked cleanliness**

Run:

```bash
git status --short --branch
git rev-parse HEAD
git worktree list --porcelain
```

Expected: `main-htsim` at or after design commit `ce30ac7`; no unrelated tracked changes.

- [ ] **Step 2: Create the safety ref**

Run:

```bash
git branch archive/pre-consolidation-20260817 HEAD
git show-ref --verify refs/heads/archive/pre-consolidation-20260817
```

Expected: the archive ref resolves to the pre-implementation `main-htsim` commit.

- [ ] **Step 3: Write the inventory files**

Record exact worktree paths, HEADs, branch names, dirty counts, local branches, ahead/behind counts relative to `main-htsim`, output-directory sizes, and the total size of all auxiliary worktrees. `pre_cleanup_inventory.md` must explain that ignored files were explicitly inventoried because the old `.gitignore` hid experiment runners.

- [ ] **Step 4: Validate and commit the inventory**

Run:

```bash
git diff --check
git add experiments/n-mrc/evidence/consolidation
git commit -m "docs: inventory branches worktrees and experiment assets"
```

Expected: one commit containing only the before-state inventory.

### Task 2: Track the Canonical MRC Evidence Runners

**Files:**
- Modify: `.gitignore:41-47`
- Add: `experiments/n-mrc/run_mrc_sglb_cold_qp_256.py`
- Add: `experiments/n-mrc/run_mrc_sglb_steady_mixed_256.py`
- Test: `sim/tests/test_mrc_sglb_cold_qp_256_runner.py`
- Test: `sim/tests/test_mrc_sglb_steady_mixed_256_runner.py`

**Interfaces:**
- Consumes: canonical topology helpers from `feedback_eval_common.py` and the canonical simulator CLI.
- Produces: Git-tracked runners that reproduce `mrc_cold_qp_flow_size_evidence_256`.

- [ ] **Step 1: Add a tracking-policy test**

Add this contract to `sim/tests/test_mrc_sglb_steady_mixed_256_runner.py`:

```python
def test_canonical_runners_are_tracked_by_policy():
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "experiments/n-mrc/run_mrc_sglb_cold_qp_256.py",
        "experiments/n-mrc/run_mrc_sglb_steady_mixed_256.py",
    ):
        result = subprocess.run(
            ["git", "check-ignore", "-q", relative], cwd=root,
            check=False)
        assert result.returncode != 0, relative
    ignored = subprocess.run(
        ["git", "check-ignore", "-q",
         "experiments/n-mrc/output/local-smoke/result.json"],
        cwd=root, check=False)
    assert ignored.returncode == 0
```

- [ ] **Step 2: Run the test and verify the current failure**

Run:

```bash
python3 sim/tests/test_mrc_sglb_steady_mixed_256_runner.py
```

Expected: failure because the runner is currently matched by `/experiments/n-mrc/*.py`.

- [ ] **Step 3: Replace the blanket Python ignore rule**

Remove `/experiments/n-mrc/*.py`. Keep output directories ignored. Immediately classify every newly visible Python file: reusable current runners are tracked on `main-htsim`; historical runners are matched byte-for-byte to an archive ref and then removed from the main worktree; an unmatched research runner is preserved on the appropriate archive branch before removal. No generic Python ignore rule is reintroduced.

- [ ] **Step 4: Validate runner semantics**

Run:

```bash
python3 sim/tests/test_mrc_sglb_cold_qp_256_runner.py
python3 sim/tests/test_mrc_sglb_steady_mixed_256_runner.py
python3 -m py_compile experiments/n-mrc/run_mrc_sglb_cold_qp_256.py experiments/n-mrc/run_mrc_sglb_steady_mixed_256.py
```

Expected: PASS; commands require 256 nodes, 64 paths, `skip_token`, real GCN, 15 us cadence, and min24 SGLB.

- [ ] **Step 5: Commit the reproducibility fix**

Run:

```bash
git add .gitignore experiments/n-mrc/run_mrc_sglb_cold_qp_256.py experiments/n-mrc/run_mrc_sglb_steady_mixed_256.py sim/tests/test_mrc_sglb_steady_mixed_256_runner.py
git commit -m "test: track canonical MRC lifecycle runners"
```

Expected: the canonical runner source is no longer local-only.

### Task 3: Create the Experiment Asset Ledger

**Files:**
- Create: `experiments/n-mrc/evidence/consolidation/experiment_assets.csv`
- Create: `experiments/n-mrc/evidence/consolidation/experiment_assets.md`
- Create: `experiments/n-mrc/evidence/consolidation/retained_sha256.tsv`
- Create: `sim/tests/test_experiment_asset_ledger.py`

**Interfaces:**
- Consumes: output names, manifests, reports, summaries, Git refs, runner paths, and SHA-256 values.
- Produces: one durable lookup for current, historical, smoke-only, and superseded evidence.

- [ ] **Step 1: Write the failing ledger-contract test**

The test must require these CSV columns:

```text
asset_id,original_path,class,topology,schemes,seeds,sample_scale,runner,source_ref,manifest,summary,report,status,cleanup_action,bytes_before
```

Create `sim/tests/test_experiment_asset_ledger.py` with:

```python
#!/usr/bin/env python3
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "experiments/n-mrc/evidence/consolidation/experiment_assets.csv"
OUTPUT = ROOT / "experiments/n-mrc/output"
REQUIRED = {
    "asset_id", "original_path", "class", "topology", "schemes",
    "seeds", "sample_scale", "runner", "source_ref", "manifest",
    "summary", "report", "status", "cleanup_action", "bytes_before",
}


def main():
    with LEDGER.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert REQUIRED <= set(reader.fieldnames or ())
        rows = list(reader)
    by_id = {row["asset_id"]: row for row in rows}
    output_names = {path.name for path in OUTPUT.iterdir() if path.is_dir()}
    assert output_names <= set(by_id), sorted(output_names - set(by_id))
    assert by_id["mrc_cold_qp_flow_size_evidence_256"]["class"] == "current"
    assert by_id["mrc_cold_qp_flow_size_evidence_256_quick"]["class"] == "smoke-only"
    assert by_id["mrc_cold_qp_flow_size_evidence_256_quick_v2"]["class"] == "smoke-only"
    assert by_id["mrc-new-result"]["class"] == "current"
    assert by_id["n-mrc-results"]["class"] in {"current", "historical"}
    for row in rows:
        if "quick" in row["asset_id"] or "pilot" in row["asset_id"]:
            assert row["class"] == "smoke-only", row["asset_id"]


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test and verify it fails because the ledger is absent**

Run:

```bash
python3 sim/tests/test_experiment_asset_ledger.py
```

Expected: FAIL with the missing ledger path.

- [ ] **Step 3: Populate the ledger**

Include every immediate child directory of `experiments/n-mrc/output`. Mark all `quick` and `pilot` names `smoke-only`; mark the two user-selected MRC directories and current comparison output `current`; mark n-MRC implementation explorations `historical` unless they are the current consolidated comparison. Record the exact reason for each cleanup action.

- [ ] **Step 4: Record retained hashes**

Hash all retained manifests, summaries, reports, plotting CSVs, figures, and compressed paired-flow files. Do not hash raw stdout slated for deletion.

- [ ] **Step 5: Validate and commit the ledger**

Run:

```bash
python3 sim/tests/test_experiment_asset_ledger.py
git diff --check
git add experiments/n-mrc/evidence/consolidation sim/tests/test_experiment_asset_ledger.py
git commit -m "docs: catalog retained and superseded experiments"
```

Expected: PASS and one ledger commit.

### Task 4: Preserve the Curated Current Evidence

**Files:**
- Force-add selected files under `experiments/n-mrc/output/mrc_cold_qp_flow_size_evidence_256/`
- Force-add selected files under `experiments/n-mrc/output/mrc-new-result/heatmap/`
- Force-add selected files under `experiments/n-mrc/output/n-mrc-results/`
- Modify: `experiments/n-mrc/evidence/consolidation/retained_sha256.tsv`

**Interfaces:**
- Consumes: current local output directories.
- Produces: small Git-tracked evidence sufficient to inspect conclusions after raw directories are removed.

- [ ] **Step 1: Retain the MRC lifecycle evidence**

Keep exactly:

```text
manifest.json
theory_checks.json
steady_summary.csv
flow_size_focus.csv
steady_paired_flow_metrics.csv.gz
mrc_lifecycle_evidence_256.md
cold_qp_startup_and_feedback.png/.pdf
mrc_lifecycle_robustness.png/.pdf
```

- [ ] **Step 2: Retain `mrc-new-result`**

Keep the heatmap validation JSON, source CSVs, matrix CSV, and PNG/PDF figures.

- [ ] **Step 3: Retain the P2P/A2A and n-MRC summary layer**

Keep `manifest.json`, `report.md`, `results.csv`, `summary.csv`, diagnostic reports/CSVs, top-level heatmaps, and the small `paper_style` reports/CSVs/figures. Exclude `raw/`, `traffic/`, `results.partial.csv`, and the four trace raw directories enumerated in Task 6 Step 4.

- [ ] **Step 4: Verify all retained hashes before staging**

Run:

```bash
sha256sum -c experiments/n-mrc/evidence/consolidation/retained_sha256.tsv
```

Expected: every retained file reports `OK`.

- [ ] **Step 5: Force-add only the curated files and commit**

Run `git add -f` with explicit file paths from Steps 1-3; do not force-add directories recursively. Then run:

```bash
git diff --cached --stat
git diff --cached --check
git commit -m "docs: preserve canonical experiment evidence"
```

Expected: no raw logs, traffic matrices, or trace archives in the commit.

### Task 5: Archive Valuable Research Branches and Dirty Source Work

**Files:**
- Modify: `experiments/n-mrc/evidence/consolidation/branches_before.tsv`
- Create: `experiments/n-mrc/evidence/consolidation/worktree_disposition.md`

**Interfaces:**
- Consumes: every local branch and dirty worktree.
- Produces: archive refs for unique research history and a disposition for every dirty file set.

- [ ] **Step 1: Compare dirty source against `main-htsim`**

For each dirty worktree, compare tracked source and untracked scripts by content hash and semantic diff. Classify each set as `already-main`, `historical-unique`, `build-output`, or `discardable-smoke`.

- [ ] **Step 2: Snapshot historical-unique source on its existing branch**

Commit only source, tests, runner scripts, documentation, and compact reports. Never commit simulator binaries, object files, `diagnostics/`, raw output, or build logs. Use messages of the form:

```text
archive: snapshot <worktree-purpose> source
```

- [ ] **Step 3: Create archive refs**

Preserve unique histories under descriptive refs, including:

```text
archive/mrc-skip-policy-redesign
archive/remote-sync-and-paper-sglb
archive/four-scheme-causal-comparison
archive/sglb-nmrc-a2a-tuning
archive/nmrc-exploration-sandbox
archive/final-512-experiment-sandbox
```

Existing `checkpoint/*` refs remain unchanged.

- [ ] **Step 4: Document branch disposition**

For every original branch, state whether it is in main, renamed to archive, retained as checkpoint/upstream, or removable alias. Include HEAD SHA before and after archival.

- [ ] **Step 5: Commit the disposition document on `main-htsim`**

Run:

```bash
git add experiments/n-mrc/evidence/consolidation/worktree_disposition.md experiments/n-mrc/evidence/consolidation/branches_before.tsv
git commit -m "docs: record research branch disposition"
```

Expected: every branch and worktree has an explicit disposition.

### Task 6: Remove Superseded Outputs and Compact Current Results

**Files:**
- Modify: `experiments/n-mrc/evidence/consolidation/experiment_assets.csv`
- Modify: `experiments/n-mrc/evidence/consolidation/storage_after.tsv`

**Interfaces:**
- Consumes: committed ledger and verified retained hashes.
- Produces: compact output storage without quick/pilot runs or duplicated raw traces.

- [ ] **Step 1: Revalidate the deletion preconditions**

Run:

```bash
git status --short
sha256sum -c experiments/n-mrc/evidence/consolidation/retained_sha256.tsv
git show-ref --verify refs/heads/archive/pre-consolidation-20260817
```

Expected: clean tracked state, all hashes `OK`, safety ref present.

- [ ] **Step 2: Delete quick and pilot directories**

Delete only immediate output children whose ledger class is `smoke-only` and whose basename contains `quick` or `pilot`. Validate each resolved target is below `experiments/n-mrc/output/` before removal.

- [ ] **Step 3: Compact current MRC evidence**

Delete `mrc_cold_qp_flow_size_evidence_256/raw/` and `traffic/` after confirming the retained manifest contains simulator and traffic hashes and the tracked runner regenerates traffic deterministically.

- [ ] **Step 4: Compact P2P/A2A evidence**

Delete these explicit redundant trees/files:

```text
experiments/n-mrc/output/n-mrc-results/raw/
experiments/n-mrc/output/n-mrc-results/traffic/
experiments/n-mrc/output/n-mrc-results/results.partial.csv
experiments/n-mrc/output/n-mrc-results/figures/paper_style/traces/raw/
experiments/n-mrc/output/n-mrc-results/figures/paper_style/traces_256mib_bg/raw/
experiments/n-mrc/output/n-mrc-results/figures/paper_style/traces_alltoall_archive_20260722/raw/
experiments/n-mrc/output/n-mrc-results/figures/paper_style/traces_websearch_archive_20260722/raw/
```

Keep trace manifests and compact diagnostic CSVs/figures.

- [ ] **Step 5: Record cleanup results**

Update the ledger with removal status and bytes reclaimed. Write `storage_after.tsv` using the same units and paths as `storage_before.tsv`.

- [ ] **Step 6: Validate and commit the post-cleanup ledger**

Run:

```bash
python3 sim/tests/test_experiment_asset_ledger.py
sha256sum -c experiments/n-mrc/evidence/consolidation/retained_sha256.tsv
git add experiments/n-mrc/evidence/consolidation
git commit -m "docs: record experiment storage cleanup"
```

Expected: ledger test passes and retained hashes remain valid.

### Task 7: Remove Redundant Worktrees and Branch Aliases

**Files:**
- Modify: `experiments/n-mrc/evidence/consolidation/worktree_disposition.md`
- Modify: `experiments/n-mrc/evidence/consolidation/storage_after.tsv`

**Interfaces:**
- Consumes: archive refs and clean/snapshotted worktrees.
- Produces: one active main worktree plus archive/checkpoint/upstream refs.

- [ ] **Step 1: Verify every auxiliary worktree is clean or archived**

Run `git status --porcelain` in every worktree. A dirty worktree may proceed only if every dirty source file is listed in `worktree_disposition.md` with an archive commit or an `already-main` content comparison.

- [ ] **Step 2: Remove clean detached replay worktrees**

Remove the SGLB baseline replay, netaware top-k replay, routing-cooldown pinned, and MRC skip baseline worktrees after their refs/dispositions are recorded.

- [ ] **Step 3: Remove archived branch worktrees**

Remove final-512, ASAN reproduction, clean-build comparison, four-scheme comparison, MRC skip redesign, remote-sync, SGLB/n-MRC tuning, exact-bounded, avail/grade, and feedback-cadence worktrees. Use explicit absolute paths from `worktrees_before.tsv`; never use a glob as a deletion target.

- [ ] **Step 4: Delete only redundant branch aliases**

Delete local aliases whose tips are already reachable from `main-htsim` or the new archive refs. Preserve `main-htsim`, `master`, `archive/*`, `checkpoint/*`, and remote-tracking refs.

- [ ] **Step 5: Prune administrative metadata and verify**

Run:

```bash
git worktree prune
git worktree list --porcelain
git branch -vv --all
```

Expected: only the main worktree remains unless a documented active worktree is intentionally retained.

- [ ] **Step 6: Commit final disposition updates**

Run:

```bash
git add experiments/n-mrc/evidence/consolidation/worktree_disposition.md experiments/n-mrc/evidence/consolidation/storage_after.tsv
git commit -m "chore: finalize archived worktree cleanup"
```

### Task 8: Correct Documentation and Run Final Verification

**Files:**
- Modify: `README.md`
- Modify: `experiments/n-mrc/README.md`
- Modify: `experiments/n-mrc/evidence/consolidation/experiment_assets.md`
- Test: canonical C++ and Python semantic suites.

**Interfaces:**
- Consumes: consolidated mainline, ledger, curated evidence, archive refs.
- Produces: the final user-facing description and verification record.

- [ ] **Step 1: Correct stale semantic descriptions**

Document 64 active MRC EVs, natural skip-token all-cooldown handling, current SGLB real-GCN/min24/shuffled-RR behavior, and `sglb-old`. Remove or label statements that describe the old 8-path, 32-active-EV, direct-cache, min3, or random-candidate implementations as current defaults.

- [ ] **Step 2: Build the canonical semantic tests**

Run:

```bash
make -C sim/tests -j2 htsim_roce_mrc_skip_semantics htsim_sglb_quality htsim_sglb_gcn htsim_paper_sglb
```

Expected: exit 0.

- [ ] **Step 3: Run the canonical semantic tests**

Run:

```bash
sim/tests/htsim_roce_mrc_skip_semantics
sim/tests/htsim_sglb_quality
sim/tests/htsim_sglb_gcn
sim/tests/htsim_paper_sglb
python3 sim/tests/test_standard_leaf_spine_topology.py
python3 sim/tests/test_sglb_final_defaults.py
python3 sim/tests/test_mrc_skip_policy_cli.py
python3 sim/tests/test_mrc_sglb_cold_qp_256_runner.py
python3 sim/tests/test_mrc_sglb_steady_mixed_256_runner.py
python3 sim/tests/test_experiment_asset_ledger.py
```

Expected: all pass.

- [ ] **Step 4: Verify repository and evidence integrity**

Run:

```bash
git diff --check
git status --short --ignored
sha256sum -c experiments/n-mrc/evidence/consolidation/retained_sha256.tsv
git worktree list --porcelain
```

Expected: no unexplained tracked changes or important ignored source; retained hashes pass; only intended worktrees remain.

- [ ] **Step 5: Commit the documentation and verification record**

Run:

```bash
git add README.md experiments/n-mrc/README.md experiments/n-mrc/evidence/consolidation
git commit -m "docs: finalize canonical experiment archive"
```

Expected: final integration commit with no algorithm changes.
