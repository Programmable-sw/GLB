// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <string>
#include "datacenter/fat_tree_switch.h"

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

static bool close_to(double a, double b) {
    return std::fabs(a - b) < 1e-9;
}

int main() {
    expect(FatTreeSwitch::_netaware_feedback_pkts == 1 &&
           FatTreeSwitch::_netaware_feedback_min_interval == timeFromUs(5.0) &&
           FatTreeSwitch::_netaware_feedback_max_interval == timeFromUs(5.0),
           "NetAware should default to fixed 5us feedback");
    expect(FatTreeSwitch::_stor_feedback_pkts == 1 &&
           FatTreeSwitch::_stor_feedback_min_interval == timeFromUs(5.0) &&
           FatTreeSwitch::_stor_feedback_max_interval == timeFromUs(5.0) &&
           FatTreeSwitch::_stor_trim_feedback_min_interval == timeFromUs(5.0),
           "Avail and Grade should default to fixed 5us feedback");
    expect(FatTreeSwitch::_netaware_state_update_interval == timeFromUs(1.0),
           "NetAware leaf-local state should refresh every 1us by default");
    expect(FatTreeSwitch::_netaware_remote_update_interval == timeFromUs(5.0),
           "NetAware spine export state should refresh every 5us by default");

    FatTreeSwitch::NetawarePortSnapshot cached_port;
    cached_port.valid = true;
    cached_port.last_update = timeFromUs(10.0);
    expect(!FatTreeSwitch::netaware_snapshot_refresh_due(
               cached_port, timeFromUs(14.999), timeFromUs(5.0)),
           "NetAware remote snapshot should remain stable inside its 5us window");
    expect(FatTreeSwitch::netaware_snapshot_refresh_due(
               cached_port, timeFromUs(15.0), timeFromUs(5.0)),
           "NetAware remote snapshot should refresh when its 5us window expires");
    expect(FatTreeSwitch::_netaware_score_mode ==
           FatTreeSwitch::NETAWARE_SCORE_SGLB_QUANTIZED,
           "NetAware default score mode should be SGLB-style quantized");
    expect(std::string(FatTreeSwitch::netaware_score_mode_name()) ==
           "sglb_quantized",
           "NetAware score mode name should report sglb_quantized");
    expect(FatTreeSwitch::_netaware_path_coupling ==
           FatTreeSwitch::NETAWARE_PATH_COUPLING_NOISY_OR,
           "NetAware default path coupling should be noisy-OR");
    expect(close_to(FatTreeSwitch::_netaware_score_weight_local_q, 0.50) &&
           close_to(FatTreeSwitch::_netaware_score_weight_remote_q, 0.50) &&
           close_to(FatTreeSwitch::_netaware_score_weight_local_util, 0.00) &&
           close_to(FatTreeSwitch::_netaware_score_weight_remote_util, 0.00),
           "NetAware default score should use two-hop queue pressure only");
    expect(close_to(FatTreeSwitch::_netaware_score_degraded_threshold, 0.10) &&
           close_to(FatTreeSwitch::_netaware_score_bad_threshold, 0.40) &&
           close_to(FatTreeSwitch::_netaware_score_avoid_threshold, 0.60),
           "NetAware default level thresholds should match tuned queue semantics");
    expect(FatTreeSwitch::netaware_default_feedback_pkts(8) == 1 &&
           FatTreeSwitch::netaware_default_feedback_pkts(16) == 1 &&
           FatTreeSwitch::netaware_default_feedback_pkts(32) == 1,
           "NetAware should use one packet trigger with fixed 5us feedback");
    expect(close_to(FatTreeSwitch::netaware_default_feedback_min_us(16), 5.0) &&
           close_to(FatTreeSwitch::netaware_default_feedback_max_us(16), 5.0) &&
           close_to(FatTreeSwitch::netaware_default_feedback_min_us(32), 5.0) &&
           close_to(FatTreeSwitch::netaware_default_feedback_max_us(32), 5.0),
           "NetAware default feedback interval should be fixed at 5us");
    expect(FatTreeSwitch::avail_default_feedback_pkts(8) == 1 &&
           FatTreeSwitch::avail_default_feedback_pkts(16) == 1 &&
           FatTreeSwitch::avail_default_feedback_pkts(32) == 1,
           "Avail should use one packet trigger with fixed 5us feedback");
    expect(FatTreeSwitch::grade_default_feedback_pkts(8) == 1 &&
           FatTreeSwitch::grade_default_feedback_pkts(16) == 1 &&
           FatTreeSwitch::grade_default_feedback_pkts(32) == 1,
           "Grade should use one packet trigger with fixed 5us feedback");
    FatTreeSwitch::_netaware_score_mode = FatTreeSwitch::NETAWARE_SCORE_GATED;
    expect(std::string(FatTreeSwitch::netaware_score_mode_name()) == "gated",
           "NetAware score mode name should report gated");
    FatTreeSwitch::_netaware_score_mode = FatTreeSwitch::NETAWARE_SCORE_SGLB_QUANTIZED;

    expect(close_to(FatTreeSwitch::netaware_pressure_from_range(0.10, 0.20, 0.80), 0.0),
           "pressure should clamp below range to zero");
    expect(close_to(FatTreeSwitch::netaware_pressure_from_range(0.50, 0.20, 0.80), 0.5),
           "pressure should scale linearly inside range");
    expect(close_to(FatTreeSwitch::netaware_pressure_from_range(0.90, 0.20, 0.80), 1.0),
           "pressure should clamp above range to one");

    FatTreeSwitch::_netaware_score_weight_local_q = 0.35;
    FatTreeSwitch::_netaware_score_weight_remote_q = 0.35;
    FatTreeSwitch::_netaware_score_weight_local_util = 0.15;
    FatTreeSwitch::_netaware_score_weight_remote_util = 0.15;
    double score = FatTreeSwitch::netaware_composite_score(1.0, 0.5, 0.25, 0.0);
    expect(close_to(score, 0.5625),
           "NetAware composite score should use local/remote queue/util weights");

    FatTreeSwitch::_netaware_score_weight_local_q = 0.45;
    FatTreeSwitch::_netaware_score_weight_remote_q = 0.45;
    FatTreeSwitch::_netaware_score_weight_local_util = 0.05;
    FatTreeSwitch::_netaware_score_weight_remote_util = 0.05;
    FatTreeSwitch::_netaware_path_coupling =
        FatTreeSwitch::NETAWARE_PATH_COUPLING_ADDITIVE;
    expect(std::string(FatTreeSwitch::netaware_path_coupling_name()) == "additive",
           "NetAware additive coupling should report its runtime name");
    expect(close_to(FatTreeSwitch::netaware_couple_hop_scores(1.0, 0.0, 0.0, 0.0),
                    0.45),
           "additive coupling should preserve the legacy weighted sum");

    FatTreeSwitch::_netaware_path_coupling =
        FatTreeSwitch::NETAWARE_PATH_COUPLING_BOTTLENECK;
    expect(std::string(FatTreeSwitch::netaware_path_coupling_name()) == "bottleneck",
           "NetAware bottleneck coupling should report its runtime name");
    expect(close_to(FatTreeSwitch::netaware_couple_hop_scores(1.0, 0.0, 0.0, 0.0),
                    0.90),
           "bottleneck coupling should not dilute one fully pressured hop");
    expect(close_to(FatTreeSwitch::netaware_couple_hop_scores(1.0, 1.0, 0.0, 0.0),
                    0.90),
           "bottleneck coupling should use the worse normalized hop");

    FatTreeSwitch::_netaware_path_coupling =
        FatTreeSwitch::NETAWARE_PATH_COUPLING_NOISY_OR;
    expect(std::string(FatTreeSwitch::netaware_path_coupling_name()) == "noisy_or",
           "NetAware noisy-OR coupling should report its runtime name");
    expect(close_to(FatTreeSwitch::netaware_couple_hop_scores(1.0, 0.0, 0.0, 0.0),
                    0.90),
           "noisy-OR should preserve a single pressured hop");
    expect(close_to(FatTreeSwitch::netaware_couple_hop_scores(1.0, 1.0, 0.0, 0.0),
                    0.99),
           "noisy-OR should accumulate independent two-hop pressure");

    FatTreeSwitch::_netaware_path_coupling =
        FatTreeSwitch::NETAWARE_PATH_COUPLING_ADDITIVE;

    FatTreeSwitch::_netaware_score_degraded_threshold = 0.20;
    FatTreeSwitch::_netaware_score_bad_threshold = 0.50;
    FatTreeSwitch::_netaware_score_avoid_threshold = 0.75;
    expect(FatTreeSwitch::netaware_level_from_score(0.19) == STOR_LEVEL_GOOD,
           "score below degraded threshold should be GOOD");
    expect(FatTreeSwitch::netaware_level_from_score(0.20) == STOR_LEVEL_DEGRADED,
           "score at degraded threshold should be DEGRADED");
    expect(FatTreeSwitch::netaware_level_from_score(0.50) == STOR_LEVEL_BAD,
           "score at bad threshold should be BAD");
    expect(FatTreeSwitch::netaware_level_from_score(0.75) == STOR_LEVEL_AVOID,
           "score at avoid threshold should be AVOID");

    expect(FatTreeSwitch::netaware_gated_level_from_inputs(
               false, 0.50, 0.0, 0.0, false) == STOR_LEVEL_BAD,
           "old gated mode should mark slow ports BAD");
    expect(FatTreeSwitch::netaware_gated_level_from_inputs(
               false, 1.00, 0.90, 0.0, false) == STOR_LEVEL_GOOD,
           "old gated mode should ignore queue pressure until the slow-link gate opens");
    expect(FatTreeSwitch::netaware_gated_level_from_inputs(
               false, 1.00, 0.70, 0.0, true) == STOR_LEVEL_BAD,
           "old gated mode should apply queue BAD threshold after the gate opens");
    expect(FatTreeSwitch::netaware_gated_level_from_inputs(
               false, 1.00, 0.90, 0.0, true) == STOR_LEVEL_AVOID,
           "old gated mode should apply queue AVOID threshold after the gate opens");
    return 0;
}
