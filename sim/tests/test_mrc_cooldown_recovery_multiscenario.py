#!/usr/bin/env python3
import importlib.util
import math
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/n-mrc/run_mrc_cooldown_recovery_multiscenario.py"


def option_value(command, option):
    index = command.index(option)
    return command[index + 1]


def load_module():
    spec = importlib.util.spec_from_file_location("mrc_recovery_matrix", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    module = load_module()
    assert module.SEEDS == [13, 29, 47]
    assert [variant.key for variant in module.VARIANTS] == [
        "mrc_one_cycle", "mrc_bdp86", "mrc_ref108", "mrc_ref129",
        "mrc_ref155", "mrc_ref172", "mrc_ref215", "mrc_ref344",
        "mrc_ref688",
    ]
    assert [variant.skip_selections for variant in module.VARIANTS] == [
        16, 96, 112, 144, 160, 176, 224, 352, 688]

    scenarios = {scenario.name: scenario for scenario in module.SCENARIOS}
    assert scenarios["healthy_permutation_4m"].size_bytes == 4 * 1024 * 1024
    assert scenarios["sparse_hotspot_8m_300g_20pct"].size_bytes == 8 * 1024 * 1024
    transient = scenarios["bursty_hotspot_4m_300g_50pct"]
    assert (transient.hotspot_rate_gbps, transient.hotspot_on_us,
            transient.hotspot_off_us) == (300, 20, 20)
    sparse = scenarios["sparse_hotspot_4m_300g_20pct"]
    assert (sparse.hotspot_on_us, sparse.hotspot_off_us) == (20, 80)
    assert sparse.target_start_us == 250.0
    assert scenarios[
        "sparse_hotspot_4m_300g_20pct_onphase"].target_start_us == 210.0
    assert scenarios[
        "sparse_hotspot_8m_300g_20pct_onphase"].target_start_us == 210.0
    assert scenarios[
        "bursty_hotspot_4m_300g_50pct_offphase"].target_start_us == 270.0
    moderate = scenarios["bursty_hotspot_4m_200g_50pct"]
    assert moderate.hotspot_rate_gbps == 200
    assert scenarios[
        "bursty_hotspot_4m_200g_50pct_onphase"].target_start_us == 225.0

    runner = module.configured_runner(13)
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        flows = runner.make_flows(transient)
        assert len(flows) == 512
        assert all(flow.size == 4 * 1024 * 1024 for flow in flows)
        assert all(flow.start_us == 250.0 for flow in flows)

        schemes = {scheme.key: scheme for scheme in runner.SCHEMES}
        one_cycle = runner.build_command(
            transient, schemes["mrc_one_cycle"], temp / "traffic.cm",
            temp / "logout.dat", len(flows))
        assert option_value(one_cycle, "-mrc_cooldown_mode") == "one_cycle"
        assert option_value(one_cycle, "-path_hotspot_bg_rate_gbps") == "300"
        assert option_value(one_cycle, "-path_hotspot_bg_on_us") == "20"
        assert option_value(one_cycle, "-path_hotspot_bg_off_us") == "20"
        assert option_value(one_cycle, "-end") == "10000"

        default = runner.build_command(
            transient, schemes["mrc_bdp86"], temp / "traffic.cm",
            temp / "logout.dat", len(flows))
        assert option_value(default, "-mrc_cooldown_mode") == "cwnd_scaled"
        assert "-mrc_cooldown_reference_pkts" not in default
        four_bdp = runner.build_command(
            transient, schemes["mrc_ref344"], temp / "traffic.cm",
            temp / "logout.dat", len(flows))
        assert option_value(
            four_bdp, "-mrc_cooldown_reference_pkts") == "344"

        runner.DRY_RUN = True
        runner.OUT = temp / "dry_run"
        dry_row = runner.run_case(transient, schemes["mrc_ref344"])
        assert dry_row["seed"] == 13
        assert dry_row["category"] == "transient"
        assert dry_row["flow_size_bytes"] == 4 * 1024 * 1024
        assert dry_row["reference_pkts"] == 344
        assert dry_row["skip_selections"] == 352

    hist = module.parse_physical_hist(
        "PathSelectDiag selected_total=100 unique_physical_mods=4 "
        "physical_mod_hist=0:10/1:20/2:30/3:40\n")
    assert hist == {0: 10, 1: 20, 2: 30, 3: 40}
    metrics = module.path_distribution_metrics(hist)
    assert math.isclose(metrics["physical_path_jain"], 10000 / 12000)
    assert math.isclose(metrics["physical_path_max_share"], 0.4)
    assert metrics["physical_path_cv"] > 0.0


if __name__ == "__main__":
    main()
