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
