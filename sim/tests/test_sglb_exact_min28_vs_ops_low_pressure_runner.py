#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/n-mrc/run_sglb_exact_min28_vs_ops_low_pressure.py"


def main():
    spec = importlib.util.spec_from_file_location("sglb_exact28_ops", RUNNER)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    with tempfile.TemporaryDirectory() as temporary:
        args = runner.parse_args(["--out", temporary, "--dry-run"])
        specs, artifacts = runner.make_specs(args)
        assert len(specs) == 6
        assert {item.seed for item in specs} == {13, 29, 47}
        for item in specs:
            command = runner.command_for(item, artifacts, args)
            if item.scheme == "sglb":
                assert command[command.index("-sglb_min_choices") + 1] == "28"
                assert command[command.index("-sglb_candidate_policy") + 1] == "exact_min"
            else:
                assert "-sglb_min_choices" not in command
                assert "-sglb_candidate_policy" not in command


if __name__ == "__main__":
    main()
