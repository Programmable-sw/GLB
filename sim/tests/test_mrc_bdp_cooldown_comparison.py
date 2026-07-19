#!/usr/bin/env python3
import importlib.util
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/n-mrc/run_mrc_bdp_cooldown_comparison.py"


def option_value(command, option):
    index = command.index(option)
    return command[index + 1]


def main():
    spec = importlib.util.spec_from_file_location("mrc_bdp_comparison", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.SEEDS == [13, 29, 47]
    assert [key for key, _label, _args in module.VARIANTS] == [
        "mrc_reference100", "mrc_topology_bdp"
    ]

    runner = module.configured_runner(13)
    workload = next(
        item for item in runner.WORKLOADS
        if item.name == "degraded_permutation_1m")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        commands = {}
        for scheme in runner.SCHEMES:
            commands[scheme.key] = runner.build_command(
                workload, scheme, temp / "traffic.cm", temp / "logout.dat",
                len(runner.make_flows(workload)))

    old = commands["mrc_reference100"]
    assert option_value(old, "-lb") == "mrc"
    assert option_value(old, "-mrc_cooldown_reference_pkts") == "100"

    corrected = commands["mrc_topology_bdp"]
    assert option_value(corrected, "-lb") == "mrc"
    assert "-mrc_cooldown_reference_pkts" not in corrected


if __name__ == "__main__":
    main()
