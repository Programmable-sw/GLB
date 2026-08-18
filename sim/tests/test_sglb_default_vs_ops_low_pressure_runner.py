#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/n-mrc/run_sglb_default_vs_ops_low_pressure.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("sglb_ops_low", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    runner = load_runner()
    with tempfile.TemporaryDirectory() as temporary:
        args = runner.parse_args(["--out", temporary, "--dry-run"])
        specs, artifacts = runner.make_specs(args)
        assert len(specs) == 6
        assert {spec.scheme for spec in specs} == {"sglb", "ops"}
        assert {spec.seed for spec in specs} == {13, 29, 47}
        assert {spec.scenario.name for spec in specs} == {
            "healthy_alltoall_64mib_p4"}
        for seed in (13, 29, 47):
            paired = [spec for spec in specs if spec.seed == seed]
            assert len({artifacts[(spec.scenario.name, seed)].sha256
                        for spec in paired}) == 1
        commands = [runner.command_for(spec, artifacts, args)
                    for spec in specs]
        assert all("-sglb_min_choices" not in command for command in commands)
        assert any(command[command.index("-lb") + 1] == "sglb"
                   for command in commands)
        assert any(command[command.index("-lb") + 1] == "ops"
                   for command in commands)


if __name__ == "__main__":
    main()
