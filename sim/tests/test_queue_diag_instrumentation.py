#!/usr/bin/env python3

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def assert_contains(text, needle, context):
    if needle not in text:
        raise AssertionError(f"missing {needle!r} in {context}")


def main():
    main_roce = read("sim/datacenter/main_roce.cpp")
    roce_h = read("sim/roce.h")
    roce_cpp = read("sim/roce.cpp")
    lossless_h = read("sim/queue_lossless_output.h")
    lit_script = read("experiments/n-mrc/run_literature_metric_compare.py")

    assert_contains(lossless_h, "ecn_mark_count() const", "LosslessOutputQueue API")
    assert_contains(main_roce, "lossless_ecn_marks", "QueueDiag output")
    assert_contains(main_roce, "ecn_echo_acks", "RoceDiag output")
    assert_contains(main_roce, "feedback_acks", "RoceDiag output")
    assert_contains(main_roce, "feedback_zero_bits", "RoceDiag output")

    assert_contains(roce_h, "_ecn_echo_acks_received", "RoceSrc counters")
    assert_contains(roce_h, "_feedback_acks_received", "RoceSrc counters")
    assert_contains(roce_h, "_feedback_zero_bits_received", "RoceSrc counters")
    assert_contains(roce_cpp, "_ecn_echo_acks_received++", "RoceSrc ack accounting")
    assert_contains(roce_cpp, "bool has_feedback_ack = ack.has_stor_feedback() || ack.has_netaware_feedback();", "shared feedback ack accounting")
    assert_contains(roce_cpp, "ack.has_netaware_feedback();", "independent NetAware feedback ack accounting")
    assert_contains(roce_cpp, "_feedback_acks_received++;", "shared feedback ack accounting")

    assert_contains(lit_script, "SCENARIO_QUEUE_TYPES", "experiment queue sweep")
    assert_contains(lit_script, "parse_queue_diag", "experiment QueueDiag parser")
    assert_contains(lit_script, "lossless_ecn_marks", "experiment QueueDiag fields")
    assert_contains(lit_script, "composite_ecn_marks", "experiment QueueDiag fields")
    assert_contains(lit_script, "ecn_echo_acks", "experiment RoceDiag fields")
    assert_contains(lit_script, "feedback_acks", "experiment RoceDiag fields")
    assert_contains(lit_script, "feedback_zero_bits", "experiment RoceDiag fields")


if __name__ == "__main__":
    main()
