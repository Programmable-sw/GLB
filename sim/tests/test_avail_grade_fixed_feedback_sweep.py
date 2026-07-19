#!/usr/bin/env python3
import importlib.util
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/n-mrc/run_avail_grade_fixed_feedback_sweep.py"


def option_value(command, option):
    index = command.index(option)
    return command[index + 1]


def main():
    spec = importlib.util.spec_from_file_location("fixed_feedback_sweep", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.SEEDS == [13, 29, 47]
    assert module.INTERVALS_US == [5, 10, 15, 20]
    assert len(module.VARIANTS) == 8
    assert module.fixed_metric_key("workload", "avail", 5) == (
        "workload", "avail", 5)

    runner = module.configured_runner(13)
    workload = next(
        item for item in runner.WORKLOADS
        if item.name == "degraded_tornado_1m")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        for scheme in runner.SCHEMES:
            command = runner.build_command(
                workload, scheme, temp / "traffic.cm", temp / "logout.dat",
                len(runner.make_flows(workload)))
            variant = module.variant_by_key(scheme.key)
            interval = str(variant.interval_us)
            assert option_value(command, "-lb") == variant.lb
            assert option_value(command, "-stor_feedback_min_us") == interval
            assert option_value(command, "-stor_feedback_max_us") == interval
            assert option_value(command, "-stor_trim_feedback_min_us") == interval
            assert "-stor_feedback_pkts" not in command
            assert "-avail_ecn_only" not in command
            assert "-grade_complex_score" not in command


if __name__ == "__main__":
    main()
