#!/usr/bin/env python3

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def main():
    main_roce = text("sim/datacenter/main_roce.cpp")
    switch_h = text("sim/datacenter/fat_tree_switch.h")
    roce_cpp = text("sim/roce.cpp")
    packet_h = text("sim/rocepacket.h")

    for mode in ("sglb-ecn-filter", "sglb-ecn-clear"):
        assert mode in main_roce
    for removed in (
        "sglb-ecn-observe", "sglb-ecn-all-good-clear",
        "sglb-ecn-no-low-clear", "sglb-ecn-scope-clear",
        "sglb-ecn-revalidate",
    ):
        assert removed not in main_roce

    assert "SGLB_ECN_NEUTRAL" in switch_h
    assert "SGLB_ECN_CLEAR" in switch_h
    assert "SGLB_ECN_OBSERVE" not in switch_h
    assert "ack.set_neutral_ecn(true)" in text(
        "sim/datacenter/fat_tree_switch.cpp")
    assert "ack.set_flags(ack.flags() & ~ECN_ECHO)" in text(
        "sim/datacenter/fat_tree_switch.cpp")
    assert "if (!ack.neutral_ecn())" in roce_cpp
    assert "copy_sglb_tx_metadata" in packet_h


if __name__ == "__main__":
    main()
