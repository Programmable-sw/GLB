#!/usr/bin/env python3

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def require(haystack, needle, context):
    if needle not in haystack:
        raise AssertionError("missing %r in %s" % (needle, context))


def forbid(haystack, needle, context):
    if needle in haystack:
        raise AssertionError("unexpected %r in %s" % (needle, context))


def main():
    main_roce = text("sim/datacenter/main_roce.cpp")
    switch_h = text("sim/datacenter/fat_tree_switch.h")
    switch_cpp = text("sim/datacenter/fat_tree_switch.cpp")
    readme = text("experiments/n-mrc/README.md")

    usage_start = main_roce.index("Usage ")
    usage = main_roce[usage_start:main_roce.index("exit(1);", usage_start)]
    for preset in ('"n-mrc"', '"n-mrc-fixed0.5"', '"n-mrc-delta"'):
        require(main_roce, preset, "public preset parser")
    for removed in (
        "n-mrc-allcool-rr-reset", "n-mrc1", "n-mrc2", "n-mrc4",
        "n-mrc5", "n-mrc6", "n-mrc7",
    ):
        forbid(usage, removed, "usage")
        forbid(main_roce, 'lb_scheme_name = "%s"' % removed, "preset parser")

    require(main_roce, "double nmrc_absolute_threshold = 0.50",
            "fixed threshold default")
    require(main_roce, "double nmrc_relative_delta = 0.25", "delta default")
    require(main_roce, 'lb_scheme_name == "n-mrc-fixed0.5"',
            "fixed preset resolution")
    require(main_roce, 'lb_scheme_name == "n-mrc-delta"',
            "delta preset resolution")
    require(switch_h, "NMRC_NETWORK_FIXED_THRESHOLD", "fixed decision mode")
    require(switch_h, "NMRC_NETWORK_DELTA", "delta decision mode")
    require(switch_cpp, "nmrc_select_fixed_threshold_path", "fixed selector")
    require(switch_cpp, "nmrc_select_delta_path", "delta selector")
    require(switch_cpp, "scores[i] >= absolute_threshold",
            "fixed candidate absolute gate")
    require(switch_cpp, "gap >= delta", "relative improvement gate")

    for heading in (
        "## `netaware`", "## `n-mrc`", "## `n-mrc-fixed0.5`",
        "## `n-mrc-delta`",
    ):
        require(readme, heading, "README scheme catalog")
    require(readme, "原来的 full path-profile snapshot",
            "NetAware rename history")
    require(readme, "后续考虑方向", "future directions")
    require(readme, "0.5 前后使用不同 delta",
            "piecewise future direction")
    require(readme, "cooldown 标志位",
            "FastCNP cooldown-bit future direction")

    print("N-MRC public preset contract passed")


if __name__ == "__main__":
    main()
