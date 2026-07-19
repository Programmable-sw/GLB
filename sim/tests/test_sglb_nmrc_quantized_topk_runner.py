#!/usr/bin/env python3
import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/n-mrc/run_sglb_nmrc_quantized_topk_compare.py"


def main():
    spec = importlib.util.spec_from_file_location("sglb_topk_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    args = SimpleNamespace(
        sim=(ROOT / "sim/datacenter/htsim_roce").resolve(),
        out=Path("/tmp/sglb_nmrc_quantized_topk_manifest"),
    )
    specs = module.make_specs(args)
    module.validate_specs(specs)
    assert len(specs) == 18
    assert set(item["scenario"] for item in specs) == set(module.SCENARIOS)
    assert set(item["variant"] for item in specs) == set(module.VARIANTS)

    for item in specs:
        argv = item["command"]
        expected = module.VARIANTS[item["variant"]]
        assert argv[argv.index("-lb") + 1] == "sglb"
        assert argv[argv.index("-roce_transport_semantics") + 1] == \
            "mrc_exact_bounded"
        assert argv[argv.index("-roce_trim_recovery") + 1] == "exact"
        assert argv[argv.index("-sglb_update_us") + 1] == "1"
        assert argv[argv.index("-sglb_gcn_update_us") + 1] == "5"
        assert argv[argv.index("-sglb_score_mode") + 1] == expected[0]
        assert int(argv[argv.index("-sglb_nmrc_levels") + 1]) == expected[1]
        assert int(argv[argv.index("-sglb_min_choices") + 1]) == 3


if __name__ == "__main__":
    main()
