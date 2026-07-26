// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <cstdlib>
#include <iostream>
#include <vector>

#include "datacenter/fat_tree_switch.h"
#include "eventlist.h"
#include "network.h"
#include "roce.h"
#include "rocepacket.h"

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

static void reset_stor_aging_defaults() {
    FatTreeSwitch::_stor_aging_profile = FatTreeSwitch::STOR_AGING_PACKET;
    FatTreeSwitch::_stor_time_ecn_tau = timeFromUs(20.0);
    FatTreeSwitch::_stor_time_trim_tau = timeFromUs(50.0);
    FatTreeSwitch::_stor_time_score_tau = timeFromUs(50.0);
    FatTreeSwitch::_stor_hybrid_bad_hold = timeFromUs(10.0);
    FatTreeSwitch::_stor_hybrid_avoid_hold = timeFromUs(20.0);
    FatTreeSwitch::_stor_hybrid_probe_interval_pkts = 64;
    FatTreeSwitch::_stor_hybrid_probe_clean_promote = 2;
}

class DropSink : public PacketSink {
public:
    DropSink() : _name("drop_sink") {}
    void receivePacket(Packet& pkt) { pkt.free(); }
    const string& nodename() { return _name; }
private:
    string _name;
};

static Route* make_route(PacketSink* sink) {
    Route* route = new Route();
    route->push_back(sink);
    return route;
}

static void test_trim_nack_feedback_is_counted_at_endpoint(EventList& eventlist) {
    DropSink sink;
    Route* route = make_route(&sink);
    RoceSrc::setLoadBalancing(RoceSrc::LB_STOR);
    RoceSrc::setPathEntropySize(2);
    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    src._flow_started = true;
    src._highest_sent = Packet::data_packet_size();

    StorFeedbackLevels levels;
    levels.push_back(STOR_LEVEL_GOOD);
    levels.push_back(STOR_LEVEL_BAD);
    RoceNack* nack = RoceNack::newpkt(src._flow, *route, 0, 7);
    nack->set_reason(RoceNack::TRIM);
    nack->set_stor_feedback(levels);
    src.processNack(*nack);
    nack->free();

    expect(src._feedback_acks_received == 0,
           "feedback-bearing TRIM NACK must not increment feedback ACKs");
    expect(src._feedback_nacks_received == 1,
           "feedback-bearing TRIM NACK must increment feedback NACKs");
    expect(src._feedback_acks_received + src._feedback_nacks_received == 1,
           "one feedback-bearing TRIM NACK must count exactly once");
}

static void test_packet_feedback_fields() {
    DropSink sink;
    Route* route = make_route(&sink);
    PacketFlow flow(NULL);

    RoceAck* ack = RoceAck::newpkt(flow, *route, 1, 7);
    StorFeedbackLevels levels;
    levels.push_back(STOR_LEVEL_GOOD);
    levels.push_back(STOR_LEVEL_AVOID);
    ack->set_stor_peer(99);
    ack->set_stor_feedback(levels);

    expect(ack->stor_peer() == 99, "RoCE ACK should carry the STOR peer host");
    expect(ack->has_stor_feedback(), "RoCE ACK should report STOR feedback presence");
    expect(ack->stor_feedback().size() == 2, "RoCE ACK should carry all STOR levels");
    expect(ack->stor_feedback()[0] == STOR_LEVEL_GOOD,
           "RoCE ACK should preserve the GOOD level");
    expect(ack->stor_feedback()[1] == STOR_LEVEL_AVOID,
           "RoCE ACK should preserve the AVOID level");
    ack->free();

    RoceNack* nack = RoceNack::newpkt(flow, *route, 1, 7);
    nack->set_stor_peer(123);
    nack->set_stor_feedback(levels);

    expect(nack->stor_peer() == 123, "RoCE NACK should carry the STOR peer host");
    expect(nack->has_stor_feedback(), "RoCE NACK should report STOR feedback presence");
    expect(nack->stor_feedback()[1] == STOR_LEVEL_AVOID,
           "RoCE NACK should preserve the AVOID level");
    nack->free();
}

static void test_shift_decay_scoring() {
    FatTreeSwitch::StorEvState state;
    FatTreeSwitch::_stor_clean_gain = 4;
    FatTreeSwitch::_stor_ecn_acc_add = 24;
    FatTreeSwitch::_stor_trim_acc_add = 48;
    FatTreeSwitch::_stor_ecn_base_penalty = 16;
    FatTreeSwitch::_stor_trim_base_penalty = 48;
    FatTreeSwitch::_stor_ecn_decay_shift = 3;
    FatTreeSwitch::_stor_trim_decay_shift = 2;
    FatTreeSwitch::_stor_ecn_penalty_shift = 3;
    FatTreeSwitch::_stor_trim_penalty_shift = 2;
    FatTreeSwitch::_stor_degraded_threshold = 160;
    FatTreeSwitch::_stor_bad_threshold = 80;

    FatTreeSwitch::stor_apply_signal(state, FatTreeSwitch::STOR_SIGNAL_CLEAN);
    expect(state.score == 255, "clean feedback should saturate score at 255");
    expect(state.ecn_acc == 0, "clean feedback should keep empty ECN accumulator at 0");
    expect(state.trim_acc == 0, "clean feedback should keep empty trim accumulator at 0");

    FatTreeSwitch::stor_apply_signal(state, FatTreeSwitch::STOR_SIGNAL_ECN);
    expect(state.ecn_acc == 24, "ECN feedback should add the configured ECN accumulator delta");
    expect(state.trim_acc == 0, "ECN feedback should decay trim accumulator only");
    expect(state.score == 236, "ECN feedback should apply configured base plus accumulator penalty");
    expect(FatTreeSwitch::stor_level_from_score(state.score) == STOR_LEVEL_DEGRADED,
           "one ECN should degrade a fresh EV with the default thresholds");

    FatTreeSwitch::stor_apply_signal(state, FatTreeSwitch::STOR_SIGNAL_CLEAN);
    expect(state.score == 240, "one clean ACK should restore a one-ECN EV to the GOOD threshold");
    expect(FatTreeSwitch::stor_level_from_score(state.score) == STOR_LEVEL_GOOD,
           "one clean ACK should restore a one-ECN EV to GOOD");

    FatTreeSwitch::StorEvState repeated_ecn;
    FatTreeSwitch::stor_apply_signal(repeated_ecn, FatTreeSwitch::STOR_SIGNAL_ECN);
    FatTreeSwitch::stor_apply_signal(repeated_ecn, FatTreeSwitch::STOR_SIGNAL_ECN);
    expect(repeated_ecn.ecn_acc == 45, "second ECN should retain decayed ECN history");
    expect(repeated_ecn.score == 215, "two ECNs should keep a fresh EV below GOOD");
    expect(FatTreeSwitch::stor_level_from_score(repeated_ecn.score) == STOR_LEVEL_DEGRADED,
           "two ECNs should keep a fresh EV degraded");

    expect(FatTreeSwitch::stor_level_from_score(239) == STOR_LEVEL_DEGRADED,
           "score below GOOD threshold should map to DEGRADED");
    expect(FatTreeSwitch::stor_level_from_score(240) == STOR_LEVEL_GOOD,
           "score at the GOOD threshold should map to GOOD");

    FatTreeSwitch::StorEvState trimmed;
    FatTreeSwitch::stor_apply_signal(trimmed, FatTreeSwitch::STOR_SIGNAL_TRIM);
    expect(trimmed.trim_acc == 48, "TRIM feedback should add the configured trim accumulator delta");
    expect(trimmed.score == 195, "TRIM should apply a heavier configured penalty than ECN");
    expect(FatTreeSwitch::stor_level_from_score(219) == STOR_LEVEL_DEGRADED,
           "score below GOOD threshold should map to DEGRADED");
    expect(FatTreeSwitch::stor_level_from_score(159) == STOR_LEVEL_BAD,
           "score below DEGRADED threshold should map to BAD");
    expect(FatTreeSwitch::stor_level_from_score(79) == STOR_LEVEL_AVOID,
           "score below BAD threshold should map to AVOID");
}

static void test_score_profiles() {
    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_ORIGINAL);
    expect(FatTreeSwitch::_stor_clean_gain == 4,
           "original STOR profile should use the image clean recovery");
    expect(FatTreeSwitch::_stor_ecn_acc_add == 16,
           "original STOR profile should use the image ECN accumulator delta");
    expect(FatTreeSwitch::_stor_trim_acc_add == 32,
           "original STOR profile should use the image TRIM accumulator delta");
    expect(FatTreeSwitch::_stor_ecn_base_penalty == 8,
           "original STOR profile should use the image ECN base penalty");
    expect(FatTreeSwitch::_stor_trim_base_penalty == 32,
           "original STOR profile should use the image TRIM base penalty");
    expect(FatTreeSwitch::_stor_good_threshold == 200,
           "original STOR profile should use the image GOOD threshold");
    expect(FatTreeSwitch::_stor_degraded_threshold == 120,
           "original STOR profile should use the image DEGRADED threshold");
    expect(FatTreeSwitch::_stor_bad_threshold == 50,
           "original STOR profile should use the image BAD threshold");

    FatTreeSwitch::StorEvState original_once;
    FatTreeSwitch::stor_apply_signal(original_once, FatTreeSwitch::STOR_SIGNAL_ECN);
    expect(original_once.score == 245,
           "one ECN in the original profile should leave a fresh EV at score 245");
    expect(FatTreeSwitch::stor_level_from_score(original_once.score) == STOR_LEVEL_GOOD,
           "one ECN in the original profile should not downgrade a fresh EV");

    FatTreeSwitch::StorEvState original_repeat;
    for (uint32_t i = 0; i < 5; i++)
        FatTreeSwitch::stor_apply_signal(original_repeat, FatTreeSwitch::STOR_SIGNAL_ECN);
    expect(original_repeat.score == 191,
           "five ECNs in the original profile should lower a fresh EV to 191");
    expect(FatTreeSwitch::stor_level_from_score(original_repeat.score) == STOR_LEVEL_DEGRADED,
           "five ECNs in the original profile should downgrade a fresh EV");

    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_BALANCED);
    expect(FatTreeSwitch::_stor_ecn_acc_add == 24,
           "balanced STOR profile should use the current ECN accumulator delta");
    expect(FatTreeSwitch::_stor_trim_acc_add == 48,
           "balanced STOR profile should use the current TRIM accumulator delta");
    expect(FatTreeSwitch::_stor_good_threshold == 240,
           "balanced STOR profile should keep the current GOOD threshold");

    FatTreeSwitch::StorEvState balanced_once;
    FatTreeSwitch::stor_apply_signal(balanced_once, FatTreeSwitch::STOR_SIGNAL_ECN);
    expect(balanced_once.score == 236,
           "one ECN in the balanced profile should lower a fresh EV to 236");
    expect(FatTreeSwitch::stor_level_from_score(balanced_once.score) == STOR_LEVEL_DEGRADED,
           "one ECN in the balanced profile should downgrade a fresh EV");

    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_SIMPLE);
    expect(std::string(FatTreeSwitch::stor_score_profile_name()) == "simple",
           "simple STOR profile should report its runtime name");
    expect(FatTreeSwitch::_stor_simple_max_score == 15 &&
           FatTreeSwitch::_stor_simple_clean_gain == 1 &&
           FatTreeSwitch::_stor_simple_congestion_penalty == 4,
           "simple Grade profile should use one 4-bit score with +1/-4 updates");
    expect(FatTreeSwitch::_stor_good_threshold == 12 &&
           FatTreeSwitch::_stor_degraded_threshold == 7 &&
           FatTreeSwitch::_stor_bad_threshold == 3,
           "simple STOR profile should use 12/7/3 level thresholds");

    FatTreeSwitch::StorEvState simple_ecn;
    FatTreeSwitch::stor_apply_signal(simple_ecn, FatTreeSwitch::STOR_SIGNAL_ECN);
    expect(simple_ecn.score == 11 && simple_ecn.ecn_acc == 0 &&
           simple_ecn.trim_acc == 0,
           "simple ECN should subtract four without accumulator state");
    FatTreeSwitch::StorEvState simple_trim;
    FatTreeSwitch::stor_apply_signal(simple_trim, FatTreeSwitch::STOR_SIGNAL_TRIM);
    expect(simple_trim.score == 11,
           "simple TRIM should have the same penalty as ECN");
    FatTreeSwitch::stor_apply_signal(simple_trim, FatTreeSwitch::STOR_SIGNAL_TRIM);
    expect(simple_trim.score == 7 &&
           FatTreeSwitch::stor_level_from_score(simple_trim.score) ==
               STOR_LEVEL_DEGRADED,
           "two simple congestion signals should move an EV to DEGRADED");
    FatTreeSwitch::stor_apply_signal(simple_trim, FatTreeSwitch::STOR_SIGNAL_CLEAN);
    expect(simple_trim.score == 8,
           "simple packet aging should recover one score step per clean ACK");

    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_BALANCED);
}

static void test_binary_stor_profile() {
    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_BINARY);
    expect(std::string(FatTreeSwitch::stor_score_profile_name()) == "binary",
           "binary STOR profile should report its runtime name");

    FatTreeSwitch::StorEvState clean;
    FatTreeSwitch::stor_apply_signal(clean, FatTreeSwitch::STOR_SIGNAL_CLEAN);
    expect(FatTreeSwitch::stor_level_from_score(clean.score) == STOR_LEVEL_GOOD,
           "binary STOR clean feedback should leave a path usable");

    FatTreeSwitch::StorEvState ecn;
    FatTreeSwitch::stor_apply_signal(ecn, FatTreeSwitch::STOR_SIGNAL_ECN);
    expect(FatTreeSwitch::stor_level_from_score(ecn.score) == STOR_LEVEL_AVOID,
           "binary STOR ECN should mark the exact path bad");

    FatTreeSwitch::StorEvState trim;
    FatTreeSwitch::stor_apply_signal(trim, FatTreeSwitch::STOR_SIGNAL_TRIM);
    expect(FatTreeSwitch::stor_level_from_score(trim.score) == STOR_LEVEL_AVOID,
           "Avail should treat TRIM like ECN by default");

    FatTreeSwitch::_stor_binary_trim_bad = false;
    FatTreeSwitch::StorEvState trim_ecn_only;
    FatTreeSwitch::stor_apply_signal(trim_ecn_only, FatTreeSwitch::STOR_SIGNAL_TRIM);
    expect(FatTreeSwitch::stor_level_from_score(trim_ecn_only.score) == STOR_LEVEL_GOOD,
           "Avail ECN-only override should ignore TRIM for availability");
    FatTreeSwitch::_stor_binary_trim_bad = true;

    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_BALANCED);
}

static void test_binary_stor_feedback_window_resets(EventList& eventlist) {
    FatTreeSwitch::_stor_feedback_pkts = 2;
    FatTreeSwitch::_stor_feedback_min_interval = 0;
    FatTreeSwitch::_stor_feedback_max_interval = timeFromUs(1000.0);
    FatTreeSwitch::_stor_trim_feedback_min_interval = 0;
    FatTreeSwitch::_stor_feedback_on_trim = true;
    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_BINARY);

    FatTreeSwitch sw(eventlist, "stor_binary_window", FatTreeSwitch::TOR,
                     0, 0, NULL);
    StorFeedbackLevels first = sw.stor_feedback_after_signal(
        0, 8, 1, 4, FatTreeSwitch::STOR_SIGNAL_ECN);
    expect(first.empty(), "binary STOR should wait for its feedback window");
    StorFeedbackLevels second = sw.stor_feedback_after_signal(
        0, 8, 2, 4, FatTreeSwitch::STOR_SIGNAL_CLEAN);
    expect(second.size() == 4 && second[1] == STOR_LEVEL_AVOID &&
           second[0] == STOR_LEVEL_GOOD && second[2] == STOR_LEVEL_GOOD &&
           second[3] == STOR_LEVEL_GOOD,
           "binary STOR should export only paths observed bad in the window");

    sw.stor_feedback_after_signal(0, 8, 0, 4,
                                  FatTreeSwitch::STOR_SIGNAL_CLEAN);
    StorFeedbackLevels reset = sw.stor_feedback_after_signal(
        0, 8, 3, 4, FatTreeSwitch::STOR_SIGNAL_CLEAN);
    expect(reset.size() == 4,
           "binary STOR should emit the next complete bitmap window");
    for (uint32_t i = 0; i < reset.size(); i++)
        expect(reset[i] == STOR_LEVEL_GOOD,
               "binary STOR should reset bad bits after feedback");

    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_BALANCED);
}

static void test_binary_stor_endpoint_skips_bad_paths(EventList& eventlist) {
    RoceSrc::resetStorSharedState();
    RoceSrc::setLoadBalancing(RoceSrc::LB_STOR);
    RoceSrc::setPathEntropySize(4);
    RoceSrc::setHostsPerTor(2);
    RoceSrc::setStorBinarySelector(true);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)10000));
    src.set_flowid(41);
    src.set_src(0);
    src.set_dst(6);
    StorFeedbackLevels levels;
    levels.push_back(STOR_LEVEL_GOOD);
    levels.push_back(STOR_LEVEL_AVOID);
    levels.push_back(STOR_LEVEL_GOOD);
    levels.push_back(STOR_LEVEL_AVOID);
    src.apply_stor_feedback_for_test(levels);
    for (uint32_t i = 0; i < 16; i++) {
        uint32_t selected = src.choose_path_for_test(Packet::PRIO_LO, false);
        expect(selected == 0 || selected == 2,
               "binary STOR endpoint should skip paths marked bad");
    }

    RoceSrc::setStorBinarySelector(false);
}

static void test_stor_aging_profile_names() {
    reset_stor_aging_defaults();

    FatTreeSwitch::set_stor_aging_profile(FatTreeSwitch::STOR_AGING_PACKET);
    expect(std::string(FatTreeSwitch::stor_aging_profile_name()) == "packet",
           "packet STOR aging profile should print as packet");

    FatTreeSwitch::set_stor_aging_profile(FatTreeSwitch::STOR_AGING_TIME_EWMA);
    expect(std::string(FatTreeSwitch::stor_aging_profile_name()) == "time_ewma",
           "time EWMA STOR aging profile should print as time_ewma");

    FatTreeSwitch::set_stor_aging_profile(FatTreeSwitch::STOR_AGING_HYBRID);
    expect(std::string(FatTreeSwitch::stor_aging_profile_name()) == "hybrid",
           "hybrid STOR aging profile should print as hybrid");
}

static uint32_t signals_to_level(FatTreeSwitch::StorSignal signal, uint8_t level) {
    FatTreeSwitch::StorEvState state;
    for (uint32_t i = 1; i <= 64; i++) {
        FatTreeSwitch::stor_apply_signal(state, signal);
        if (FatTreeSwitch::stor_level_from_score(state.score) == level)
            return i;
    }
    return 0;
}

static void test_packet_aging_reaches_avoid() {
    reset_stor_aging_defaults();
    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_BALANCED);
    FatTreeSwitch::set_stor_aging_profile(FatTreeSwitch::STOR_AGING_PACKET);

    expect(signals_to_level(FatTreeSwitch::STOR_SIGNAL_ECN, STOR_LEVEL_AVOID) == 7,
           "packet aging should reach AVOID after seven consecutive ECN signals");
    expect(signals_to_level(FatTreeSwitch::STOR_SIGNAL_TRIM, STOR_LEVEL_AVOID) == 3,
           "packet aging should reach AVOID after three consecutive TRIM signals");
}

static void test_time_ewma_recovers_silent_bad_evidence(EventList& eventlist) {
    reset_stor_aging_defaults();
    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_BALANCED);
    FatTreeSwitch::set_stor_aging_profile(FatTreeSwitch::STOR_AGING_TIME_EWMA);
    FatTreeSwitch::_stor_time_score_tau = timeFromUs(20.0);

    FatTreeSwitch sw(eventlist, "stor_time_aging_tor", FatTreeSwitch::TOR, 0, 0, NULL);
    for (uint32_t i = 0; i < 7; i++)
        sw.stor_feedback_after_signal(0, 8, 1, 4, FatTreeSwitch::STOR_SIGNAL_ECN);

    const FatTreeSwitch::StorEvState* before = sw.stor_state_for_test(8, 1);
    expect(before && FatTreeSwitch::stor_level_from_score(before->score) == STOR_LEVEL_AVOID,
           "time EWMA test should drive EV to AVOID before silence");
    uint8_t before_score = before->score;

    sw.stor_apply_time_aging_for_test(8, timeFromUs(100.0));

    const FatTreeSwitch::StorEvState* after = sw.stor_state_for_test(8, 1);
    expect(after && after->score > before_score,
           "time EWMA should increase score after a silent interval");
}

static void test_hybrid_hold_down_blocks_direct_good_recovery(EventList& eventlist) {
    reset_stor_aging_defaults();
    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_BALANCED);
    FatTreeSwitch::set_stor_aging_profile(FatTreeSwitch::STOR_AGING_HYBRID);
    FatTreeSwitch::_stor_hybrid_avoid_hold = timeFromUs(20.0);
    FatTreeSwitch::_stor_hybrid_probe_interval_pkts = 1;

    FatTreeSwitch sw(eventlist, "stor_hybrid_aging_tor", FatTreeSwitch::TOR, 0, 0, NULL);
    for (uint32_t i = 0; i < 7; i++)
        sw.stor_feedback_after_signal(0, 8, 1, 4, FatTreeSwitch::STOR_SIGNAL_ECN);

    sw.stor_apply_time_aging_for_test(8, timeFromUs(10.0));
    const FatTreeSwitch::StorEvState* early = sw.stor_state_for_test(8, 1);
    expect(early && FatTreeSwitch::stor_level_from_score(early->score) == STOR_LEVEL_AVOID,
           "hybrid aging should keep AVOID during hold-down");

    sw.stor_apply_time_aging_for_test(8, timeFromUs(25.0));
    const FatTreeSwitch::StorEvState* late = sw.stor_state_for_test(8, 1);
    expect(late && FatTreeSwitch::stor_level_from_score(late->score) == STOR_LEVEL_BAD,
           "hybrid aging should release AVOID EVs only to BAD after hold-down");
}

static void test_hybrid_probe_interval_gates_hold_release(EventList& eventlist) {
    reset_stor_aging_defaults();
    FatTreeSwitch::set_stor_score_profile(FatTreeSwitch::STOR_SCORE_PROFILE_BALANCED);
    FatTreeSwitch::set_stor_aging_profile(FatTreeSwitch::STOR_AGING_HYBRID);
    FatTreeSwitch::_stor_hybrid_avoid_hold = timeFromUs(20.0);
    FatTreeSwitch::_stor_hybrid_probe_interval_pkts = 8;

    FatTreeSwitch sw(eventlist, "stor_hybrid_probe_tor", FatTreeSwitch::TOR, 0, 0, NULL);
    for (uint32_t i = 0; i < 7; i++)
        sw.stor_feedback_after_signal(0, 8, 1, 4, FatTreeSwitch::STOR_SIGNAL_ECN);

    sw.stor_apply_time_aging_for_test(8, timeFromUs(25.0));
    const FatTreeSwitch::StorEvState* blocked = sw.stor_state_for_test(8, 1);
    expect(blocked && FatTreeSwitch::stor_level_from_score(blocked->score) == STOR_LEVEL_AVOID,
           "hybrid probe interval should block hold release before enough peer signals");

    sw.stor_feedback_after_signal(0, 8, 0, 4, FatTreeSwitch::STOR_SIGNAL_CLEAN);
    sw.stor_apply_time_aging_for_test(8, timeFromUs(26.0));
    const FatTreeSwitch::StorEvState* released = sw.stor_state_for_test(8, 1);
    expect(released && FatTreeSwitch::stor_level_from_score(released->score) == STOR_LEVEL_BAD,
           "hybrid probe interval should release AVOID after enough peer signals");
}

static void test_feedback_window(EventList& eventlist) {
    reset_stor_aging_defaults();
    FatTreeSwitch::_stor_feedback_pkts = 2;
    FatTreeSwitch::_stor_feedback_min_interval = 0;
    FatTreeSwitch::_stor_feedback_max_interval = timeFromUs(1000.0);
    FatTreeSwitch::_stor_trim_feedback_min_interval = 0;
    FatTreeSwitch::_stor_feedback_on_trim = true;

    FatTreeSwitch sw(eventlist, "stor_test_tor", FatTreeSwitch::TOR, 0, 0, NULL);
    StorFeedbackLevels first = sw.stor_feedback_after_signal(0, 8, 1, 4,
                                                             FatTreeSwitch::STOR_SIGNAL_ECN);
    expect(first.empty(), "first STOR signal in a window should update score without feedback");

    StorFeedbackLevels second = sw.stor_feedback_after_signal(0, 8, 2, 4,
                                                              FatTreeSwitch::STOR_SIGNAL_ECN);
    expect(second.size() == 4, "STOR should feedback the full EV table at the packet window");

    StorFeedbackLevels trim = sw.stor_feedback_after_signal(0, 8, 3, 4,
                                                            FatTreeSwitch::STOR_SIGNAL_TRIM);
    expect(trim.size() == 4, "TRIM should trigger early STOR feedback when enabled");
}

static void test_endpoint_level_selection(EventList& eventlist) {
    RoceSrc::resetStorSharedState();
    RoceSrc::setLoadBalancing(RoceSrc::LB_STOR);
    RoceSrc::setPathEntropySize(4);
    RoceSrc::setHostsPerTor(2);
    RoceSrc::setStorLevelWeights(8, 3, 1, 0);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)10000));
    src.set_src(0);
    src.set_dst(6);

    StorFeedbackLevels levels;
    levels.push_back(STOR_LEVEL_GOOD);
    levels.push_back(STOR_LEVEL_DEGRADED);
    levels.push_back(STOR_LEVEL_BAD);
    levels.push_back(STOR_LEVEL_AVOID);
    src.apply_stor_feedback_for_test(levels);

    uint32_t counts[4] = {0, 0, 0, 0};
    for (uint32_t i = 0; i < 24; i++) {
        uint32_t selected = src.choose_path_for_test(Packet::PRIO_LO, false);
        expect(selected < 4, "STOR should return an EV in the configured EV set");
        counts[selected]++;
    }

    expect(counts[0] > counts[1], "GOOD EV should receive more selections than DEGRADED");
    expect(counts[1] > counts[2], "DEGRADED EV should receive more selections than BAD");
    expect(counts[3] == 1,
           "STOR should send one rotating AVOID probe per K=4P selections");

    levels.assign(4, STOR_LEVEL_AVOID);
    src.apply_stor_feedback_for_test(levels);
    uint32_t fallback = src.choose_path_for_test(Packet::PRIO_LO, false);
    expect(fallback < 4, "STOR should fall back to probing when every EV is AVOID");
}

static std::vector<uint32_t> stor_sequence(EventList& eventlist,
                                           uint32_t good,
                                           uint32_t degraded,
                                           uint32_t bad) {
    RoceSrc::resetStorSharedState();
    RoceSrc::setLoadBalancing(RoceSrc::LB_STOR);
    RoceSrc::setPathEntropySize(4);
    RoceSrc::setHostsPerTor(2);
    RoceSrc::setStorLevelWeights(good, degraded, bad, 0);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)10000));
    src.set_flowid(77);
    src.set_src(0);
    src.set_dst(6);
    StorFeedbackLevels levels;
    levels.push_back(STOR_LEVEL_GOOD);
    levels.push_back(STOR_LEVEL_GOOD);
    levels.push_back(STOR_LEVEL_DEGRADED);
    levels.push_back(STOR_LEVEL_BAD);
    src.apply_stor_feedback_for_test(levels);

    std::vector<uint32_t> sequence;
    for (uint32_t i = 0; i < 32; i++)
        sequence.push_back(src.choose_path_for_test(Packet::PRIO_LO, false));
    return sequence;
}

static void test_stor_shuffled_bucket_is_scale_invariant(EventList& eventlist) {
    std::vector<uint32_t> a = stor_sequence(eventlist, 8, 4, 2);
    std::vector<uint32_t> b = stor_sequence(eventlist, 4, 2, 1);
    expect(a == b,
           "STOR shuffled bucket should ignore absolute level-weight scale");
}

static void test_stor_profile_is_shared_but_virtual_order_is_per_qp(
        EventList& eventlist) {
    RoceSrc::resetStorSharedState();
    RoceSrc::setLoadBalancing(RoceSrc::LB_STOR);
    RoceSrc::setPathEntropySize(4);
    RoceSrc::setHostsPerTor(2);
    RoceSrc::setStorLevelWeights(4, 2, 1, 0);
    RoceSrc::setStorBinarySelector(false);

    RoceSrc first(NULL, NULL, eventlist, speedFromMbps((uint64_t)10000));
    first.set_flowid(71);
    first.set_src(0);
    first.set_dst(6);
    RoceSrc second(NULL, NULL, eventlist, speedFromMbps((uint64_t)10000));
    second.set_flowid(72);
    second.set_src(1);
    second.set_dst(7);

    StorFeedbackLevels levels;
    levels.push_back(STOR_LEVEL_GOOD);
    levels.push_back(STOR_LEVEL_DEGRADED);
    levels.push_back(STOR_LEVEL_BAD);
    levels.push_back(STOR_LEVEL_GOOD);
    first.apply_stor_feedback_for_test(levels);

    std::vector<uint32_t> first_sequence;
    std::vector<uint32_t> second_sequence;
    for (uint32_t i = 0; i < 16; i++) {
        first_sequence.push_back(
            first.choose_path_for_test(Packet::PRIO_LO, false));
        second_sequence.push_back(
            second.choose_path_for_test(Packet::PRIO_LO, false));
    }
    expect(first_sequence != second_sequence,
           "two QPs should virtually permute one shared STOR bucket differently");
    uint32_t first_counts[4] = {0, 0, 0, 0};
    uint32_t second_counts[4] = {0, 0, 0, 0};
    for (uint32_t i = 0; i < 16; i++) {
        first_counts[first_sequence[i]]++;
        second_counts[second_sequence[i]]++;
    }
    for (uint32_t path = 0; path < 4; path++)
        expect(first_counts[path] == second_counts[path],
               "two QPs should consume the same shared ticket allocation");
}

static void test_stor_profile_diagnostics(EventList& eventlist) {
    RoceSrc::resetStorProfileDiag();
    RoceSrc::resetStorSharedState();
    RoceSrc::setLoadBalancing(RoceSrc::LB_STOR);
    RoceSrc::setPathEntropySize(2);
    RoceSrc::setStorLevelWeights(4, 2, 1, 0);
    RoceSrc::setStorBinarySelector(false);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)10000));
    src.set_flowid(91);
    src.set_src(0);
    src.set_dst(6);

    StorFeedbackLevels mild;
    mild.push_back(STOR_LEVEL_DEGRADED);
    mild.push_back(STOR_LEVEL_GOOD);
    src.apply_stor_feedback_for_test(mild);
    expect(RoceSrc::storLevelTransition(STOR_LEVEL_GOOD,
                                       STOR_LEVEL_DEGRADED) == 1,
           "GOOD to DEGRADED feedback should count one STOR transition");
    expect(RoceSrc::storLevelChanges() == 1,
           "one changed path should count one STOR level change");

    StorFeedbackLevels recovered;
    recovered.push_back(STOR_LEVEL_GOOD);
    recovered.push_back(STOR_LEVEL_GOOD);
    src.apply_stor_feedback_for_test(recovered);
    expect(RoceSrc::storLevelTransition(STOR_LEVEL_DEGRADED,
                                       STOR_LEVEL_GOOD) == 1,
           "DEGRADED to GOOD feedback should count recovery");

    StorFeedbackLevels all_avoid;
    all_avoid.push_back(STOR_LEVEL_AVOID);
    all_avoid.push_back(STOR_LEVEL_AVOID);
    src.apply_stor_feedback_for_test(all_avoid);
    expect(RoceSrc::storAllZeroProfiles() == 1,
           "all-AVOID feedback should count one all-zero STOR profile");
    src.choose_path_for_test(Packet::PRIO_LO, false);
    expect(RoceSrc::storAllZeroSelections() == 1,
           "selection under all-AVOID feedback should count one fallback");

    RoceSrc::resetStorProfileDiag();
    expect(RoceSrc::storLevelChanges() == 0 &&
           RoceSrc::storAllZeroProfiles() == 0 &&
           RoceSrc::storAllZeroSelections() == 0,
           "reset should clear STOR profile diagnostics");
}

int main() {
    EventList eventlist;
    expect(RoceSrc::storLevelWeight(STOR_LEVEL_GOOD) == 4 &&
           RoceSrc::storLevelWeight(STOR_LEVEL_DEGRADED) == 2 &&
           RoceSrc::storLevelWeight(STOR_LEVEL_BAD) == 1 &&
           RoceSrc::storLevelWeight(STOR_LEVEL_AVOID) == 0,
           "STOR default level weights should be 4/2/1/0");
    test_packet_feedback_fields();
    test_trim_nack_feedback_is_counted_at_endpoint(eventlist);
    test_shift_decay_scoring();
    test_score_profiles();
    test_binary_stor_profile();
    test_binary_stor_feedback_window_resets(eventlist);
    test_binary_stor_endpoint_skips_bad_paths(eventlist);
    test_stor_aging_profile_names();
    test_packet_aging_reaches_avoid();
    test_time_ewma_recovers_silent_bad_evidence(eventlist);
    test_hybrid_hold_down_blocks_direct_good_recovery(eventlist);
    test_hybrid_probe_interval_gates_hold_release(eventlist);
    test_feedback_window(eventlist);
    test_endpoint_level_selection(eventlist);
    test_stor_shuffled_bucket_is_scale_invariant(eventlist);
    test_stor_profile_is_shared_but_virtual_order_is_per_qp(eventlist);
    test_stor_profile_diagnostics(eventlist);
    return 0;
}
