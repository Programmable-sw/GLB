#!/usr/bin/env python3
import importlib.util
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/n-mrc/run_avail_feedback_5us_ablation.py"


def option_value(command, option):
    index = command.index(option)
    return command[index + 1]


def main():
    spec = importlib.util.spec_from_file_location("avail_feedback_5us", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.SEEDS == [13, 29, 47]
    assert [key for key, _label, _args in module.VARIANTS] == [
        "avail_default_5_20", "avail_fixed_5"
    ]

    runner = module.configured_runner(13)
    assert len(runner.SCHEMES) == 2
    workload = next(
        item for item in runner.WORKLOADS
        if item.name == "healthy_permutation_1m")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        commands = {}
        for scheme in runner.SCHEMES:
            commands[scheme.key] = runner.build_command(
                workload, scheme, temp / "traffic.cm", temp / "logout.dat",
                len(runner.make_flows(workload)))

    default = commands["avail_default_5_20"]
    assert "-stor_feedback_min_us" not in default
    assert "-stor_feedback_max_us" not in default
    assert "-stor_trim_feedback_min_us" not in default

    fixed = commands["avail_fixed_5"]
    assert option_value(fixed, "-stor_feedback_min_us") == "5"
    assert option_value(fixed, "-stor_feedback_max_us") == "5"
    assert "-stor_trim_feedback_min_us" not in fixed
    assert "-avail_ecn_only" not in fixed


if __name__ == "__main__":
    main()
