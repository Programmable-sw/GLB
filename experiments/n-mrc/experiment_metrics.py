"""Shared parsing helpers for retained n-MRC experiment runners."""

import hashlib
import re


FINISH_RE = re.compile(
    r"Flow Roce_(\d+)_(\d+)\s+(\d+) finished at ([0-9.]+)"
)
KEY_VALUE_RE = re.compile(
    r"([A-Za-z0-9_]+)=(-?[0-9]+(?:\.[0-9]+)?)"
)
NEW_RETX_RE = re.compile(r"New:\s+(\d+)\s+Rtx:\s+(\d+)")
HOTSPOT_RE = re.compile(
    r"Path hotspot background installed (\d+) fixed-link sources"
)
IDMAP_RE = re.compile(r"^(\d+)\s+Roce_(\d+)_(\d+)$")

ROCE_FIELDS = (
    "acks", "nacks", "nacks_ooo", "nacks_trim", "nacks_loss", "rtos",
    "ecn_echo_acks", "feedback_acks", "stor_selected_good",
    "stor_selected_degraded", "stor_selected_bad", "stor_selected_avoid",
)
QUEUE_FIELDS = (
    "lossless_overflows", "lossless_ecn_marks", "lossy_drops",
    "lossy_ecn_marks", "composite_trims", "composite_drops",
    "composite_ecn_marks",
)
NMRC_FIELDS = (
    "samples", "sample_zero_bits", "feedbacks", "packet_feedbacks",
    "time_feedbacks", "feedback_packets_sum", "feedback_zero_bits",
    "all_good_feedbacks",
)


def percentile(values, quantile):
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low = int(position)
    high = min(len(ordered) - 1, low + 1)
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def parse_key_values(text, prefix, fields):
    result = {field: 0 for field in fields}
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        for key, raw in KEY_VALUE_RE.findall(line):
            if key in result:
                result[key] = float(raw) if "." in raw else int(raw)
    return result


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_id_map(idmap_file, flows):
    if not idmap_file.exists() or flows is None:
        return {}
    sources = []
    seen = set()
    for line in idmap_file.read_text(errors="ignore").splitlines():
        match = IDMAP_RE.fullmatch(line.strip())
        if match is None:
            continue
        source_id = int(match.group(1))
        if source_id in seen:
            continue
        seen.add(source_id)
        sources.append((source_id, int(match.group(2)), int(match.group(3))))
    if len(sources) != len(flows):
        return {}
    for (_source_id, src, dst), flow in zip(sources, flows):
        if src != flow.src or dst != flow.dst:
            return {}
    return {item[0]: flow for item, flow in zip(sources, flows)}
