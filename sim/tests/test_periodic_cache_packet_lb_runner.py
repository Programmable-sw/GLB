#!/usr/bin/env python3
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/n-mrc/run_periodic_cache_packet_lb_512.py"


def main():
    spec = importlib.util.spec_from_file_location("periodic_cache_runner", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    runner = module.configured_runner()
    assert [scheme.key for scheme in runner.SCHEMES] == [
        "ecmp_rr", "ops", "reps", "mrc", "netaware", "sglb", "ar"]
    assert len(runner.WORKLOADS) == 7
    assert len(runner.run_specs()) == 49
    assert module.OUT.name == "nmrc_periodic_cache_packet_lb_512"

    by_key = {scheme.key: scheme for scheme in runner.SCHEMES}
    workload = runner.WORKLOADS[0]
    for key in ("netaware", "sglb", "ar"):
        command = runner.build_command(
            workload, by_key[key], Path("traffic.cm"), Path("logout.dat"), 512)
        assert command[command.index("-lb") + 1] == by_key[key].lb
    nmrc_command = runner.build_command(
        workload, by_key["netaware"], Path("traffic.cm"), Path("logout.dat"), 512)
    assert not any(option.startswith("-netaware_") for option in nmrc_command)


if __name__ == "__main__":
    main()
