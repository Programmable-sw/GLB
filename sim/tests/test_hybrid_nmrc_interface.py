#!/usr/bin/env python3

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def require(text, needle, context):
    if needle not in text:
        raise AssertionError(f"missing {needle!r} in {context}")


def reject(text, needle, context):
    if needle in text:
        raise AssertionError(f"unexpected {needle!r} in {context}")


def main():
    main_roce = read("sim/datacenter/main_roce.cpp")
    roce_h = read("sim/roce.h")
    switch_h = read("sim/datacenter/fat_tree_switch.h")
    packet_h = read("sim/rocepacket.h")

    require(
        main_roce,
        "mrc|netaware|n-mrc|n-mrc-fixed0.5|n-mrc-delta",
        "load-balancing usage",
    )
    for preset in ("n-mrc", "n-mrc-fixed0.5", "n-mrc-delta"):
        require(main_roce, f'argv[i+1], "{preset}"', f"{preset} parser")
    usage_start = main_roce.index("Usage ")
    usage = main_roce[usage_start:main_roce.index("exit(1);", usage_start)]
    for removed in (
        "n-mrc-allcool-rr-reset", "n-mrc1", "n-mrc2", "n-mrc4",
        "n-mrc5", "n-mrc6", "n-mrc7",
    ):
        reject(usage, removed, "public load-balancing usage")
        reject(main_roce, f'lb_scheme_name = "{removed}"', "preset parser")

    require(roce_h, "LB_NETAWARE", "separate NetAware endpoint mode")
    require(roce_h, "LB_NMRC", "separate N-MRC endpoint mode")
    require(roce_h, "NMRC_EV_ENCODED", "encoded EV mode")
    require(switch_h, "NMRC_NETWORK_GRADED", "canonical four-level mode")
    require(switch_h, "NMRC_NETWORK_FIXED_THRESHOLD", "fixed preset mode")
    require(switch_h, "NMRC_NETWORK_DELTA", "delta preset mode")
    require(packet_h, "class RoceFastCnp", "path notification packet")
    require(packet_h, "need_endpoint_cooldown", "FastCNP cooldown flag")

    require(
        main_roce,
        "-nmrc_ev_mode encoded|random_matched|random32",
        "N-MRC EV CLI",
    )
    require(main_roce, "-nmrc_absolute_threshold VALUE",
            "fixed threshold CLI")
    require(main_roce, "-nmrc_relative_delta VALUE", "relative delta CLI")
    require(main_roce, "trim_cooldown=actual_path",
            "default actual-path TRIM policy")
    require(main_roce, "HybridNmrcConfig", "resolved configuration")
    require(main_roce, "HybridNmrcDiag", "final diagnostics")


if __name__ == "__main__":
    main()
