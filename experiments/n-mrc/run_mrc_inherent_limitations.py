#!/usr/bin/env python3
"""Run and analyze the controlled MRC inherent-limitations experiments."""

MRC_FLOW_DIAG_INT_FIELDS = (
    "flow_id",
    "src",
    "dst",
    "dst_tor",
    "flow_size",
    "new_data_selections",
    "unique_active_evs",
    "full_sweeps",
    "unused_active_evs",
    "quality_feedback_before_done",
    "effective_state_updates",
    "packets_before_first_update",
    "new_selections_after_first_update",
    "actionable_feedback",
    "cooldown_starts",
    "failure_starts",
    "forced_cooling_uses",
    "max_simultaneous_cooling",
    "replacement_congestion",
    "post_cooldown_first_clean",
    "shared_updates_published",
    "shared_updates_consumed",
    "shared_updates_from_other_qps",
    "redundant_discoveries",
    "post_shared_bad_ev_sends",
)

MRC_FLOW_DIAG_FLOAT_FIELDS = (
    "start_us",
    "finish_us",
    "first_full_sweep_us",
    "first_state_update_us",
    "feedback_age_sum_us",
    "feedback_age_max_us",
)

MRC_FLOW_DIAG_FIELDS = (
    MRC_FLOW_DIAG_INT_FIELDS + MRC_FLOW_DIAG_FLOAT_FIELDS
)


def parse_mrc_flow_diags(text):
    """Parse and strictly validate all MrcFlowDiag records in simulator text."""
    records = []
    seen = set()
    expected = set(MRC_FLOW_DIAG_FIELDS)
    for line in text.splitlines():
        if not line.startswith("MrcFlowDiag "):
            continue
        raw = {}
        for token in line.split()[1:]:
            if "=" not in token:
                raise ValueError("malformed MrcFlowDiag token: " + token)
            key, value = token.split("=", 1)
            if key in raw:
                raise ValueError("duplicate MrcFlowDiag field: " + key)
            raw[key] = value
        actual = set(raw)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                "MrcFlowDiag missing={} extra={}".format(missing, extra)
            )
        record = {
            key: int(raw[key]) for key in MRC_FLOW_DIAG_INT_FIELDS
        }
        record.update({
            key: float(raw[key]) for key in MRC_FLOW_DIAG_FLOAT_FIELDS
        })
        flow_id = record["flow_id"]
        if flow_id in seen:
            raise ValueError("duplicate MrcFlowDiag flow_id {}".format(flow_id))
        seen.add(flow_id)
        records.append(record)
    return records


if __name__ == "__main__":
    raise SystemExit(
        "runner catalog is not implemented yet; use this module's parser API"
    )
