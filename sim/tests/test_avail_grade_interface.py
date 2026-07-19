#!/usr/bin/env python3

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def assert_contains(text, needle, context):
    if needle not in text:
        raise AssertionError(f"missing {needle!r} in {context}")


def assert_absent(text, needle, context):
    if needle in text:
        raise AssertionError(f"unexpected {needle!r} in {context}")


def main():
    main_roce = read("sim/datacenter/main_roce.cpp")
    roce_h = read("sim/roce.h")
    rocepacket_h = read("sim/rocepacket.h")
    roce_cpp = read("sim/roce.cpp")
    switch_h = read("sim/datacenter/fat_tree_switch.h")
    switch_cpp = read("sim/datacenter/fat_tree_switch.cpp")
    lit_script = read("experiments/n-mrc/run_literature_metric_compare.py")
    bg_script = read("experiments/n-mrc/run_packet_background_300g50.py")
    root_readme = read("README.md")
    experiment_readme = read("experiments/n-mrc/README.md")

    assert_contains(main_roce, "-lb ecmp|ecmp_rr|adaptive-routing|sglb|drill|reps|avail|grade|mrc|netaware|n-mrc|rr|ops|conweave|ndp", "main_roce usage")
    assert_contains(
        main_roce,
        'else if (!strcmp(argv[i+1], "avail")) {\n'
        '                route_strategy = ECMP_FIB;\n'
        '                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);\n'
        '                roce_lb_mode = RoceSrc::LB_STOR;',
        "avail source-ToR STOR parser",
    )
    assert_contains(
        main_roce,
        'else if (!strcmp(argv[i+1], "grade")) {\n'
        '                route_strategy = ECMP_FIB;\n'
        '                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);\n'
        '                roce_lb_mode = RoceSrc::LB_STOR;',
        "grade source-ToR STOR parser",
    )
    assert_contains(main_roce, 'lb_scheme_name = "avail"', "avail runtime name")
    assert_contains(main_roce, 'lb_scheme_name = "grade"', "grade runtime name")
    assert_contains(main_roce, "[-grade_complex_score]", "Grade complex override usage")
    assert_contains(main_roce, "[-avail_ecn_only]", "Avail ECN-only override usage")
    assert_contains(main_roce, 'else if (!strcmp(argv[i],"-avail_ecn_only"))', "Avail ECN-only override parser")
    assert_contains(main_roce, "avail ECN-only override requires -lb avail", "Avail ECN-only scope guard")
    assert_contains(main_roce, "bool avail_ecn_only = false;", "Avail ECN+TRIM default mode")
    assert_contains(main_roce, "binary_bad_signal ecn_trim", "Avail ECN+TRIM diagnostic")
    assert_contains(main_roce, 'else if (!strcmp(argv[i],"-grade_complex_score"))', "Grade complex override parser")
    assert_contains(main_roce, "grade complex override requires -lb grade", "Grade complex override scope guard")
    assert_contains(main_roce, "bool grade_complex_score = false;", "Grade simple default mode")
    assert_contains(
        main_roce,
        "grade_complex_score ?\n"
        "                FatTreeSwitch::STOR_SCORE_PROFILE_BALANCED :\n"
        "                FatTreeSwitch::STOR_SCORE_PROFILE_SIMPLE",
        "Grade score profile resolution",
    )
    assert_contains(switch_cpp, "_stor_simple_congestion_penalty = 4;", "tuned Grade simple penalty")
    assert_contains(switch_cpp, "_stor_binary_trim_bad", "Avail binary TRIM gate")
    assert_contains(switch_cpp, "_stor_binary_trim_bad = true;", "Avail ECN+TRIM switch default")
    assert_absent(main_roce, "-avail_trim_bad", "removed redundant Avail TRIM flag")
    assert_absent(main_roce, 'argv[i+1], "dtor"', "removed dToR CLI")
    assert_absent(main_roce, 'argv[i+1], "stor"', "removed legacy STOR CLI")
    assert_contains(main_roce, '<< " canonical: paths "', "branch canonical diagnostics")
    assert_contains(main_roce, '"source-ToR ACK-ECN availability feedback"', "avail diagnostic semantics")
    assert_contains(main_roce, '"source-ToR ACK/TRIM graded feedback"', "grade diagnostic semantics")
    assert_contains(main_roce, "bool stor_score_profile_user_set = false;", "order-independent profile selection")
    assert_contains(main_roce, "avail requires the binary availability profile", "avail profile guard")
    assert_contains(main_roce, "grade requires a graded score profile", "grade profile guard")
    assert_contains(roce_h, "LB_STOR", "RoceSrc lb enum")
    assert_contains(roce_cpp, "virtual_shuffle_index", "Avail/Grade virtual per-flow permutation")
    for path, text in {
        "sim/datacenter/main_roce.cpp": main_roce,
        "sim/roce.h": roce_h,
        "sim/roce.cpp": roce_cpp,
        "sim/rocepacket.h": rocepacket_h,
        "sim/datacenter/fat_tree_switch.h": switch_h,
        "sim/datacenter/fat_tree_switch.cpp": switch_cpp,
    }.items():
        for obsolete in (
                "LB_DTOR", "DtorState", "DtorBitmap", "dtor_feedback",
                "dtor_bitmap", "_dtor_enabled", "_dtor_states",
                "maybe_update_dtor_feedback", "shared_dtor_profile",
                "_dtor_shared_profiles"):
            assert_absent(text, obsolete, path)
    assert_contains(rocepacket_h, "set_stor_feedback", "RoCE packet feedback API")
    assert_contains(roce_h, "_stor_selected_good", "RoCE STOR selection diagnostics")
    assert_contains(roce_h, "_stor_selected_degraded", "RoCE STOR selection diagnostics")
    assert_contains(roce_h, "_stor_selected_bad", "RoCE STOR selection diagnostics")
    assert_contains(roce_h, "_stor_selected_avoid", "RoCE STOR selection diagnostics")
    assert_contains(switch_h, "StorEvState", "fat-tree switch state")
    assert_contains(switch_h, "collect_stor_diag", "fat-tree switch STOR diagnostics")
    assert_contains(switch_cpp, "maybe_update_stor_feedback", "fat-tree switch feedback generation")
    assert_contains(main_roce, "StorDiag", "main_roce STOR diagnostic output")
    assert_contains(main_roce, "-stor_feedback_pkts", "STOR CLI feedback packet window")
    assert_contains(main_roce, "-stor_score_profile", "STOR CLI scoring profile")
    assert_contains(main_roce, "-stor_score_params", "STOR CLI scoring parameters")
    assert_contains(main_roce, "-stor_level_thresholds", "STOR CLI level thresholds")
    assert_contains(main_roce, "-stor_level_weights", "STOR CLI endpoint weights")
    assert_contains(main_roce, "-stor_aging", "STOR aging profile CLI")
    assert_contains(main_roce, "-stor_time_ewma_us", "STOR time EWMA CLI")
    assert_contains(main_roce, "-stor_hybrid_hold_us", "STOR hybrid hold-down CLI")
    assert_contains(main_roce, "-stor_hybrid_probe", "STOR hybrid probe CLI")
    assert_contains(main_roce, "stor aging profile", "STOR canonical aging output")
    assert_contains(lit_script, '("avail", "Avail", "avail", [])', "literature comparison schemes")
    assert_contains(lit_script, '("grade", "Grade", "grade", [])', "literature comparison schemes")
    assert_contains(lit_script, "STOR_DIAG_FIELDS", "literature comparison STOR diagnostics")
    assert_contains(lit_script, "parse_stor_diag", "literature comparison STOR diagnostics")
    assert_contains(lit_script, 'COMPOSITE_DEFAULT_SCHEMES = {"mrc", "avail", "grade", "netaware"}', "literature comparison queue defaults")
    assert_contains(bg_script, '("avail", "Avail", "avail", [])', "packet background schemes")
    assert_contains(bg_script, '("grade", "Grade", "grade", ["-queue_type", "composite_ecn_lb"])', "packet background schemes")
    assert_contains(root_readme, "`avail`", "root README scheme table")
    assert_contains(root_readme, "`grade`", "root README scheme table")
    assert_contains(experiment_readme, "## `avail`", "experiment README")
    assert_contains(experiment_readme, "## `grade`", "experiment README")

    legacy_names = [
        "N" + "mrc",
        "N" + "MRC",
        "n" + "mrc",
    ]
    legacy_state_options = [
        "4" + "-state",
        "4" + "state",
        "2" + "bit",
        "state" + "_mode",
        "weak" + "_sample",
        "ecn" + "_degrade",
        "unknown" + "_reopen",
    ]

    for path, text in {
        "sim/datacenter/main_roce.cpp": main_roce,
        "sim/roce.h": roce_h,
        "sim/roce.cpp": roce_cpp,
        "sim/rocepacket.h": rocepacket_h,
        "sim/datacenter/fat_tree_switch.h": switch_h,
        "sim/datacenter/fat_tree_switch.cpp": switch_cpp,
    }.items():
        if path not in {
            "sim/datacenter/main_roce.cpp",
            "sim/roce.h",
            "sim/roce.cpp",
            "sim/rocepacket.h",
            "sim/datacenter/fat_tree_switch.h",
            "sim/datacenter/fat_tree_switch.cpp",
        }:
            for legacy in legacy_names:
                assert_absent(text, legacy, path)
        for legacy_state in legacy_state_options:
            assert_absent(text, legacy_state, path)


if __name__ == "__main__":
    main()
