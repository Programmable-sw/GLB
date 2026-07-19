#!/usr/bin/env python3
import importlib.util
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/n-mrc/run_mrc_cooldown_reference_sweep.py"


def option_value(command, option):
    index = command.index(option)
    return command[index + 1]


def main():
    spec = importlib.util.spec_from_file_location("mrc_reference_sweep", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.SEEDS == [13, 29, 47]
    assert [variant.reference_pkts for variant in module.VARIANTS] == [
        64, 86, 100, 108, 129, 155, 172, 215, 258, 344, 516, 688]
    assert [variant.skip_selections for variant in module.VARIANTS] == [
        64, 96, 112, 112, 144, 160, 176, 224, 272, 352, 528, 688]

    runner = module.configured_runner(13)
    workload = next(
        item for item in runner.WORKLOADS
        if item.name == "path_hotspot_1m")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        for scheme in runner.SCHEMES:
            command = runner.build_command(
                workload, scheme, temp / "traffic.cm", temp / "logout.dat",
                len(runner.make_flows(workload)))
            variant = module.variant_by_key(scheme.key)
            assert option_value(command, "-lb") == "mrc"
            if variant.reference_pkts == 86:
                assert "-mrc_cooldown_reference_pkts" not in command
            else:
                assert option_value(
                    command, "-mrc_cooldown_reference_pkts") == str(
                        variant.reference_pkts)


if __name__ == "__main__":
    main()
