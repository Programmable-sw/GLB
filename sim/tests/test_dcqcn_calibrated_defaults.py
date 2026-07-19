#!/usr/bin/env python3

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def require(text, marker, message):
    if marker not in text:
        raise AssertionError(message)


def main():
    roce_h = (ROOT / "sim/roce.h").read_text(encoding="utf-8")
    roce_cpp = (ROOT / "sim/roce.cpp").read_text(encoding="utf-8")
    main_roce = (ROOT / "sim/datacenter/main_roce.cpp").read_text(encoding="utf-8")

    expected_defaults = (
        "double RoceSrc::_dcqcn_g = 1.0 / 256.0;",
        "double RoceSrc::_dcqcn_initial_alpha = 0.6;",
        "linkspeed_bps RoceSrc::_dcqcn_ai_rate = 4000000000ULL;",
        "linkspeed_bps RoceSrc::_dcqcn_min_rate = 80000000000ULL;",
        "mem_b RoceSrc::_dcqcn_byte_counter = 1400000;",
        "simtime_picosec RoceSrc::_dcqcn_alpha_interval = timeFromUs(28.0);",
        "simtime_picosec RoceSrc::_dcqcn_rate_increase_interval = timeFromUs(28.0);",
        "simtime_picosec RoceSrc::_dcqcn_cnp_interval = timeFromUs(16.8);",
    )
    for marker in expected_defaults:
        require(roce_cpp, marker, f"missing calibrated DCQCN default: {marker}")

    require(
        roce_h,
        "static void printDcqcnConfiguration(std::ostream& out);",
        "missing DCQCN effective-configuration diagnostic API",
    )
    require(
        main_roce,
        "RoceSrc::printDcqcnConfiguration(cout);",
        "main_roce must print the effective DCQCN configuration",
    )


if __name__ == "__main__":
    main()
