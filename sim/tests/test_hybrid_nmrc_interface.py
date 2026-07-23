#!/usr/bin/env python3

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def require(text, needle, context):
    if needle not in text:
        raise AssertionError(f"missing {needle!r} in {context}")


def reject(text, needle, context):
    if needle in text:
        raise AssertionError(f"unexpected {needle!r} in {context}")


def main():
    main_roce = read("sim/datacenter/main_roce.cpp")
    roce_h = read("sim/roce.h")
    switch_h = read("sim/datacenter/fat_tree_switch.h")
    packet_h = read("sim/rocepacket.h")

    require(
        main_roce,
        "adaptive-routing|sglb|drill|reps|avail|grade|mrc|netaware|n-mrc|n-mrc-allcool-rr-reset|n-mrc1|n-mrc2|n-mrc4",
        "load-balancing usage",
    )
    require(main_roce, 'argv[i+1], "netaware"', "NetAware parser")
    require(main_roce, 'roce_lb_mode = RoceSrc::LB_NETAWARE;', "NetAware mode")
    require(main_roce, 'lb_scheme_name = "netaware";', "NetAware runtime name")
    require(main_roce, 'argv[i+1], "n-mrc"', "hybrid n-MRC parser")
    require(main_roce, 'roce_lb_mode = RoceSrc::LB_NMRC;', "hybrid n-MRC mode")
    require(main_roce, 'lb_scheme_name = "n-mrc";', "hybrid n-MRC runtime name")
    for preset in ("n-mrc-allcool-rr-reset", "n-mrc1", "n-mrc2", "n-mrc4"):
        require(main_roce, f'argv[i+1], "{preset}"', f"{preset} parser")

    require(roce_h, "LB_NETAWARE", "separate NetAware endpoint mode")
    require(roce_h, "LB_NMRC", "separate hybrid n-MRC endpoint mode")
    require(roce_h, "NMRC_EV_ENCODED", "encoded EV mode")
    require(roce_h, "NMRC_EV_RANDOM_MATCHED", "matched random EV mode")
    require(roce_h, "NMRC_EV_RANDOM32", "fixed random32 EV mode")
    require(switch_h, "NMRC_REROUTE_ANY_BETTER", "eager reroute policy")
    require(switch_h, "NMRC_REROUTE_BETTER_GE3", "three-path reroute policy")
    require(switch_h, "_nmrc_hybrid_enabled", "source-leaf hybrid gate")
    require(packet_h, "class RoceFastCnp", "real path-notification packet")
    require(packet_h, "ROCEFASTCNP", "dedicated path-notification type")

    require(
        main_roce,
        "-nmrc_ev_mode encoded|random_matched|random32",
        "hybrid EV CLI",
    )
    require(
        main_roce,
        "-nmrc_reroute_policy any_better|better_ge3",
        "hybrid reroute CLI",
    )
    require(main_roce, "-nmrc_fastcnp on|off", "hybrid FastCNP CLI")
    require(
        main_roce,
        "-nmrc_endpoint_policy rr_cooldown|random_stateless",
        "hybrid endpoint-policy CLI",
    )
    require(
        main_roce,
        "-nmrc_all_cooling_policy earliest|rr_reset",
        "hybrid all-cooling-policy CLI",
    )
    require(
        main_roce,
        "-nmrc_network_decision graded|binary_score",
        "hybrid network-decision CLI",
    )
    require(main_roce, "-nmrc_binary_threshold 0.5", "binary threshold CLI")
    require(main_roce, "-nmrc_relative_delta VALUE", "relative delta CLI")
    require(main_roce, "trim_cooldown=actual_path", "default actual-path TRIM policy")
    reject(main_roce, "-nmrc_trim_cooldown", "removed TRIM policy CLI")
    reject(main_roce, "nominal_only", "removed nominal-only policy")
    reject(roce_h, "NMRC_TRIM_COOLDOWN_", "removed TRIM policy modes")
    require(main_roce, "HybridNmrcConfig", "hybrid resolved configuration")
    require(main_roce, "HybridNmrcDiag", "hybrid final diagnostics")
    for field in (
        "threshold_blocked=",
        "level_transitions=",
        "fastcnp_bytes=",
        "fastcnp_latency_avg_us=",
        "cooldown_starts=",
        "cooling_skips=",
        "cooling_recoveries=",
        "duplicate_notifications=",
        "all_cooling_fallbacks=",
        "all_cooling_rr_episodes=",
        "all_cooling_rr_selections=",
        "all_cooling_rr_resets=",
        "fastcnp_policy_ignored=",
        "trim_policy_ignored=",
        "trim_non_detour=",
        "trim_detour=",
        "trim_nominal_cooldown_starts=",
        "trim_actual_cooldown_starts=",
        "trim_duplicate_stale_ignored=",
        "trim_actual_unresolved=",
        "observed_first_hop_alias_ratio=",
        "binary_original_safe=",
        "binary_original_congested=",
        "binary_no_safe=",
        "binary_route_missing=",
        "binary_paired_actions=",
        "binary_original_score_count=",
        "binary_original_score_sum=",
        "binary_original_score_max=",
        "binary_selected_score_count=",
        "binary_selected_score_sum=",
        "binary_selected_score_max=",
        "binary_actual_egress_hist=",
    ):
        require(main_roce, field, "hybrid concise diagnostics")

    for field in (
        "preset=",
        "endpoint_policy=",
        "all_cooling_policy=",
        "network_decision=",
        "binary_threshold=",
        "relative_delta=",
    ):
        require(main_roce, field, "hybrid resolved policy fields")

    require(
        main_roce,
        "RoceSrc::setNmrcEndpointPolicy",
        "resolved endpoint-policy wiring",
    )
    require(
        main_roce,
        "RoceSrc::setNmrcAllCoolingPolicy",
        "resolved all-cooling-policy wiring",
    )
    require(
        main_roce,
        "FatTreeSwitch::_nmrc_network_decision_mode",
        "resolved network-decision wiring",
    )

    require(
        main_roce,
        "nmrc_ev_mode == RoceSrc::NMRC_EV_ENCODED",
        "encoded-only path-id mapping",
    )

    require(main_roce, "-netaware_score_mode", "NetAware snapshot options")
    reject(main_roce, "-nmrc_score_mode", "snapshot options under hybrid prefix")
    reject(main_roce, "-nmrc_feedback_", "hybrid time/packet feedback cadence")


if __name__ == "__main__":
    main()
