// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <cstdlib>
#include <iostream>

#include "datacenter/fat_tree_switch.h"
#include "eventlist.h"

static const char* level_name(uint8_t level) {
    switch (level) {
    case STOR_LEVEL_GOOD:
        return "GOOD";
    case STOR_LEVEL_DEGRADED:
        return "DEGRADED";
    case STOR_LEVEL_BAD:
        return "BAD";
    default:
        return "AVOID";
    }
}

static uint32_t clean_to_good_after(FatTreeSwitch::StorScoreProfile profile,
                                    FatTreeSwitch::StorSignal signal,
                                    uint32_t count) {
    FatTreeSwitch::set_stor_score_profile(profile);
    FatTreeSwitch::StorEvState state;
    for (uint32_t i = 0; i < count; i++)
        FatTreeSwitch::stor_apply_signal(state, signal);

    uint32_t clean = 0;
    while (FatTreeSwitch::stor_level_from_score(state.score) != STOR_LEVEL_GOOD &&
           clean < 256) {
        FatTreeSwitch::stor_apply_signal(state, FatTreeSwitch::STOR_SIGNAL_CLEAN);
        clean++;
    }
    return clean;
}

static uint32_t signals_to_degrade(FatTreeSwitch::StorScoreProfile profile,
                                   FatTreeSwitch::StorSignal signal) {
    FatTreeSwitch::set_stor_score_profile(profile);
    FatTreeSwitch::StorEvState state;
    for (uint32_t count = 1; count <= 256; count++) {
        FatTreeSwitch::stor_apply_signal(state, signal);
        if (FatTreeSwitch::stor_level_from_score(state.score) != STOR_LEVEL_GOOD)
            return count;
    }
    return 0;
}

static uint32_t signals_to_level(FatTreeSwitch::StorScoreProfile profile,
                                 FatTreeSwitch::StorSignal signal,
                                 uint8_t level) {
    FatTreeSwitch::set_stor_score_profile(profile);
    FatTreeSwitch::StorEvState state;
    for (uint32_t count = 1; count <= 256; count++) {
        FatTreeSwitch::stor_apply_signal(state, signal);
        if (FatTreeSwitch::stor_level_from_score(state.score) == level)
            return count;
    }
    return 0;
}

static void reset_aging_defaults() {
    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_BALANCED);
    FatTreeSwitch::set_stor_aging_profile(FatTreeSwitch::STOR_AGING_PACKET);
    FatTreeSwitch::_stor_time_ecn_tau = timeFromUs(20.0);
    FatTreeSwitch::_stor_time_trim_tau = timeFromUs(50.0);
    FatTreeSwitch::_stor_time_score_tau = timeFromUs(50.0);
    FatTreeSwitch::_stor_hybrid_bad_hold = timeFromUs(10.0);
    FatTreeSwitch::_stor_hybrid_avoid_hold = timeFromUs(20.0);
    FatTreeSwitch::_stor_hybrid_probe_interval_pkts = 1;
    FatTreeSwitch::_stor_hybrid_probe_clean_promote = 2;
    FatTreeSwitch::_stor_feedback_pkts = 100;
    FatTreeSwitch::_stor_feedback_min_interval = 0;
    FatTreeSwitch::_stor_feedback_max_interval = timeFromUs(1000.0);
    FatTreeSwitch::_stor_trim_feedback_min_interval = 0;
}

static void print_trace(FatTreeSwitch::StorScoreProfile profile) {
    FatTreeSwitch::set_stor_score_profile(profile);
    std::cout << "profile=" << FatTreeSwitch::stor_score_profile_name()
              << " params clean=" << (uint32_t)FatTreeSwitch::_stor_clean_gain
              << " ecn_add=" << (uint32_t)FatTreeSwitch::_stor_ecn_acc_add
              << " trim_add=" << (uint32_t)FatTreeSwitch::_stor_trim_acc_add
              << " ecn_base=" << (uint32_t)FatTreeSwitch::_stor_ecn_base_penalty
              << " trim_base=" << (uint32_t)FatTreeSwitch::_stor_trim_base_penalty
              << " thresholds="
              << (uint32_t)FatTreeSwitch::_stor_good_threshold << "/"
              << (uint32_t)FatTreeSwitch::_stor_degraded_threshold << "/"
              << (uint32_t)FatTreeSwitch::_stor_bad_threshold
              << std::endl;

    std::cout << "  first_degrade_ecn=" << signals_to_degrade(
                     profile, FatTreeSwitch::STOR_SIGNAL_ECN)
              << " first_bad_ecn=" << signals_to_level(
                     profile, FatTreeSwitch::STOR_SIGNAL_ECN, STOR_LEVEL_BAD)
              << " first_avoid_ecn=" << signals_to_level(
                     profile, FatTreeSwitch::STOR_SIGNAL_ECN, STOR_LEVEL_AVOID)
              << " clean_to_good_after_1ecn=" << clean_to_good_after(
                     profile, FatTreeSwitch::STOR_SIGNAL_ECN, 1)
              << " clean_to_good_after_2ecn=" << clean_to_good_after(
                     profile, FatTreeSwitch::STOR_SIGNAL_ECN, 2)
              << " first_degrade_trim=" << signals_to_degrade(
                     profile, FatTreeSwitch::STOR_SIGNAL_TRIM)
              << " first_bad_trim=" << signals_to_level(
                     profile, FatTreeSwitch::STOR_SIGNAL_TRIM, STOR_LEVEL_BAD)
              << " first_avoid_trim=" << signals_to_level(
                     profile, FatTreeSwitch::STOR_SIGNAL_TRIM, STOR_LEVEL_AVOID)
              << " clean_to_good_after_1trim=" << clean_to_good_after(
                     profile, FatTreeSwitch::STOR_SIGNAL_TRIM, 1)
              << std::endl;

    FatTreeSwitch::StorEvState state;
    for (uint32_t i = 1; i <= 6; i++) {
        FatTreeSwitch::stor_apply_signal(state, FatTreeSwitch::STOR_SIGNAL_ECN);
        std::cout << "  ecn" << i
                  << " score=" << (uint32_t)state.score
                  << " ecn_acc=" << (uint32_t)state.ecn_acc
                  << " level=" << level_name(
                         FatTreeSwitch::stor_level_from_score(state.score))
                  << " clean_to_good=" << clean_to_good_after(
                         profile, FatTreeSwitch::STOR_SIGNAL_ECN, i)
                  << std::endl;
    }
}

static void print_aging_trace(EventList& eventlist,
                              FatTreeSwitch::StorAgingProfile profile) {
    reset_aging_defaults();
    FatTreeSwitch::set_stor_aging_profile(profile);
    FatTreeSwitch sw(eventlist, "stor_score_trace_tor", FatTreeSwitch::TOR, 0, 0, NULL);

    for (uint32_t i = 0; i < 7; i++)
        sw.stor_feedback_after_signal(0, 8, 1, 4, FatTreeSwitch::STOR_SIGNAL_ECN);

    const FatTreeSwitch::StorEvState* before = sw.stor_state_for_test(8, 1);
    std::cout << "aging=" << FatTreeSwitch::stor_aging_profile_name()
              << " after_7ecn_score=" << (before ? (uint32_t)before->score : 0)
              << " level=" << (before ? level_name(
                     FatTreeSwitch::stor_level_from_score(before->score)) : "missing")
              << std::endl;

    sw.stor_apply_time_aging_for_test(8, timeFromUs(25.0));
    const FatTreeSwitch::StorEvState* after_25 = sw.stor_state_for_test(8, 1);
    std::cout << "  after_25us_score=" << (after_25 ? (uint32_t)after_25->score : 0)
              << " level=" << (after_25 ? level_name(
                     FatTreeSwitch::stor_level_from_score(after_25->score)) : "missing")
              << " avoid_in_out=" << (after_25 ? after_25->avoid_entries : 0)
              << "/" << (after_25 ? after_25->avoid_exits : 0)
              << std::endl;

    sw.stor_apply_time_aging_for_test(8, timeFromUs(100.0));
    const FatTreeSwitch::StorEvState* after_100 = sw.stor_state_for_test(8, 1);
    std::cout << "  after_100us_score=" << (after_100 ? (uint32_t)after_100->score : 0)
              << " level=" << (after_100 ? level_name(
                     FatTreeSwitch::stor_level_from_score(after_100->score)) : "missing")
              << " avoid_in_out=" << (after_100 ? after_100->avoid_entries : 0)
              << "/" << (after_100 ? after_100->avoid_exits : 0)
              << std::endl;
}

int main() {
    EventList eventlist;
    print_trace(FatTreeSwitch::STOR_SCORE_PROFILE_ORIGINAL);
    print_trace(FatTreeSwitch::STOR_SCORE_PROFILE_BALANCED);
    print_aging_trace(eventlist, FatTreeSwitch::STOR_AGING_PACKET);
    print_aging_trace(eventlist, FatTreeSwitch::STOR_AGING_TIME_EWMA);
    print_aging_trace(eventlist, FatTreeSwitch::STOR_AGING_HYBRID);
    return 0;
}
