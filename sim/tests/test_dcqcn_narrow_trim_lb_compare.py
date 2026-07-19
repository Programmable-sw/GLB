#!/usr/bin/env python3
import importlib.util
import tempfile
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/n-mrc/run_dcqcn_narrow_trim_lb_compare.py"


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "dcqcn_narrow_trim_lb_compare", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def option_value(command, option):
    index = command.index(option)
    return command[index + 1]


def main():
    runner = load_runner()
    assert runner.SCENARIOS == (
        "asymmetric_permutation_16mib",
        "standard_websearch_proxy_80pct",
        "full_global_p16_256mib_background_on",
        "full_global_p4_64mib_background_off",
    )
    assert tuple(runner.SCHEMES) == ("sglb", "ar", "reps", "mrc")
    assert tuple(runner.VARIANTS) == (
        "natural_cumulative", "natural_exact",
        "narrow_cumulative", "narrow_exact",
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        args = Namespace(
            out=Path(temp_dir),
            sim=ROOT / "sim/datacenter/htsim_roce",
        )
        specs = runner.make_specs(args)
        assert len(specs) == 64
        runner.validate_variant_commands(specs)

        grouped = {}
        for spec in specs:
            command = spec["command"]
            grouped.setdefault((spec["scenario"], spec["scheme"]), []).append(spec)
            assert option_value(command, "-nodes") == "128"
            assert option_value(command, "-seed") == "13"
            assert option_value(command, "-queue_type") == "composite_ecn_lb"
            assert option_value(command, "-host_queue_type") == "prio"
            assert option_value(command, "-roce_rx_mode") == "sp"
            assert option_value(command, "-roce_sack_bitmap_bits") == "64"
            assert option_value(command, "-cc") == spec["cc_mode"]
            assert option_value(command, "-roce_trim_recovery") == spec["trim_mode"]

        assert len(grouped) == 16
        assert all(len(group) == 4 for group in grouped.values())

    print("dcqcn narrow/TRIM LB comparison runner tests passed")


if __name__ == "__main__":
    main()
