// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <cstdlib>
#include <iostream>
#include <sstream>

#define private public
#include "roce.h"
#undef private

#include "ecn.h"
#include "eventlist.h"
#include "network.h"
#include "rocepacket.h"

class DropSink : public PacketSink {
public:
    DropSink() : _name("drop_sink") {}
    void receivePacket(Packet& pkt) { pkt.free(); }
    const string& nodename() { return _name; }
private:
    string _name;
};

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

static EventList& test_eventlist() {
    static EventList eventlist;
    return eventlist;
}

static void test_canonical_profile_has_64_identity_mapped_evs() {
    RoceSrc::setLoadBalancing(RoceSrc::LB_MRC);
    RoceSrc::setMrcCongestionPolicy(RoceSrc::MRC_POLICY_SKIP_TOKEN);
    RoceSrc::setPathEntropySize(64);

    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    src.set_src(1);
    src.set_dst(129);
    src.set_flowid(1701);
    src._flow_started = true;
    src.init_mrc_paths(64);

    expect(src._mrc_evs.size() == 64,
           "canonical MRC profile must contain 64 EVs");
    expect(src._mrc_active.size() == 64,
           "canonical MRC profile must activate all 64 EVs");
    expect(src._mrc_backup.empty(),
           "canonical MRC profile must not create backup EVs");
    for (uint32_t ev = 0; ev < 64; ++ev) {
        expect(src._mrc_evs[ev].logical_ev == ev,
               "canonical logical EV identity mismatch");
        expect(src._mrc_evs[ev].physical_path == ev,
               "canonical EV-to-path identity mismatch");
    }
}

static void test_explicit_legacy_ablation_uses_all_64_evs() {
    RoceSrc::setLoadBalancing(RoceSrc::LB_MRC);
    RoceSrc::setMrcCongestionPolicy(RoceSrc::MRC_POLICY_ONE_CYCLE);
    RoceSrc::setMrcActiveEvs(64);
    RoceSrc::setPathEntropySize(64);

    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    src.set_src(2);
    src.set_dst(130);
    src.set_flowid(1711);
    src._flow_started = true;
    src.init_mrc_paths(64);

    expect(src._mrc_active.size() == 64,
           "explicit 64-EV legacy ablation must activate all EVs");
    expect(src._mrc_backup.empty(),
           "explicit 64-EV legacy ablation must not create backups");
    RoceSrc::setMrcActiveEvs(0);
}

static void test_congestion_transition_is_exact_and_non_rearming() {
    RoceSrc::setLoadBalancing(RoceSrc::LB_MRC);
    RoceSrc::setMrcCongestionPolicy(RoceSrc::MRC_POLICY_SKIP_TOKEN);
    RoceSrc::setPathEntropySize(64);

    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    src.set_src(7);
    src.set_dst(199);
    src.set_flowid(1702);
    src._flow_started = true;
    src.init_mrc_paths(64);

    const uint32_t ev = 17;
    expect(src._mrc_evs[ev].state == RoceSrc::MRC_EV_GOOD,
           "canonical EV must start GOOD");
    expect(src.mrc_mark_congested(ev, RoceSrc::MRC_CONGESTION_ECN),
           "first exact-EV feedback must start a skip episode");
    expect(src._mrc_evs[ev].state == RoceSrc::MRC_EV_SKIP,
           "congested EV must enter SKIP");
    expect(src._mrc_evs[ev].skip_pending,
           "skip-token feedback must arm one token");
    expect(src._mrc_evs[ev].congestion_epoch == 1,
           "first skip episode must increment the epoch once");

    const uint64_t resume_rotation = src._mrc_evs[ev].resume_rotation;
    const uint64_t cooldown_deadline =
        src._mrc_evs[ev].cool_until_select_count;
    expect(!src.mrc_mark_congested(ev, RoceSrc::MRC_CONGESTION_TRIM),
           "feedback while SKIP must be ignored");
    expect(src._mrc_evs[ev].state == RoceSrc::MRC_EV_SKIP,
           "duplicate feedback must preserve SKIP");
    expect(src._mrc_evs[ev].skip_pending,
           "duplicate feedback must preserve the armed token");
    expect(src._mrc_evs[ev].resume_rotation == resume_rotation,
           "duplicate feedback must not refresh a rotation deadline");
    expect(src._mrc_evs[ev].cool_until_select_count == cooldown_deadline,
           "duplicate feedback must not refresh a legacy deadline");
    expect(src._mrc_evs[ev].congestion_epoch == 1,
           "duplicate feedback must not create a new episode");
    expect(src._mrc_duplicate_feedback_ignored == 1,
           "ignored feedback must be counted once");
}

static void test_skip_token_is_consumed_only_at_its_nominal_opportunity() {
    RoceSrc::setMrcCongestionPolicy(RoceSrc::MRC_POLICY_SKIP_TOKEN);
    RoceSrc::setPathEntropySize(64);
    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    src.set_src(9);
    src.set_dst(201);
    src.set_flowid(1703);
    src._flow_started = true;
    src.init_mrc_paths(64);

    const uint32_t target = src._mrc_active[10];
    src.mrc_mark_congested(target, RoceSrc::MRC_CONGESTION_ECN);
    expect(!src.mrc_ev_selectable(target),
           "a read-only eligibility scan must report a token EV ineligible");
    expect(src._mrc_evs[target].skip_pending,
           "an eligibility scan must not consume the token");

    for (uint32_t slot = 0; slot < 10; ++slot) {
        RoceSrc::MrcChoice choice = src.choose_mrc_ev(64);
        expect(choice.logical_ev == src._mrc_active[slot],
               "GOOD EVs before the token must keep their nominal slots");
        expect(src._mrc_evs[target].skip_pending,
               "token must remain armed before its nominal slot");
    }

    RoceSrc::MrcChoice after_skip = src.choose_mrc_ev(64);
    expect(after_skip.logical_ev == src._mrc_active[11],
           "the token EV must forfeit exactly its own opportunity");
    expect(!src._mrc_evs[target].skip_pending &&
               src._mrc_evs[target].state == RoceSrc::MRC_EV_GOOD,
           "consuming the opportunity must restore the EV to GOOD");
    expect(src._mrc_skip_opportunities_consumed == 1,
           "one token must consume exactly one opportunity");
    expect(src._mrc_data_on_non_good_violations == 0,
           "selector must never return non-GOOD DATA choices");
}

static void test_all_skip_tokens_resolve_by_ordinary_rotation_progress() {
    RoceSrc::setMrcCongestionPolicy(RoceSrc::MRC_POLICY_SKIP_TOKEN);
    RoceSrc::setPathEntropySize(64);
    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    src.set_src(11);
    src.set_dst(203);
    src.set_flowid(1704);
    src._flow_started = true;
    src.init_mrc_paths(64);

    for (uint32_t ev = 0; ev < 64; ++ev)
        expect(src.mrc_mark_congested(ev, RoceSrc::MRC_CONGESTION_ECN),
               "each GOOD EV must accept its first token");

    const uint32_t expected = src._mrc_active[0];
    RoceSrc::MrcChoice choice = src.choose_mrc_ev(64);
    expect(choice.logical_ev == expected,
           "all-SKIP token traversal must wrap to the first restored EV");
    expect(src._mrc_rotation == 1,
           "all-SKIP traversal must cross one ordinary rotation boundary");
    expect(src._mrc_skip_opportunities_consumed == 64,
           "all-SKIP traversal must consume all 64 nominal opportunities");
    expect(src._mrc_evs[choice.logical_ev].state == RoceSrc::MRC_EV_GOOD,
           "selected DATA EV must be GOOD");
    expect(src._mrc_forced_cooling_use == 0,
           "new token policy must not enter the legacy forced-use path");
    expect(src._mrc_data_on_non_good_violations == 0,
           "all-SKIP resolution must not send DATA on a non-GOOD EV");
}

static void test_skip_rotation_restores_only_at_the_next_rotation() {
    RoceSrc::setMrcCongestionPolicy(RoceSrc::MRC_POLICY_SKIP_ROTATION);
    RoceSrc::setPathEntropySize(64);
    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    src.set_src(13);
    src.set_dst(205);
    src.set_flowid(1705);
    src._flow_started = true;
    src.init_mrc_paths(64);
    src._mrc_rotation = 10;

    const uint32_t target = src._mrc_active[10];
    src.mrc_mark_congested(target, RoceSrc::MRC_CONGESTION_ECN);
    expect(src._mrc_evs[target].resume_rotation == 11,
           "rotation feedback must target the next logical rotation");
    src.mrc_mark_congested(target, RoceSrc::MRC_CONGESTION_TRIM);
    expect(src._mrc_evs[target].resume_rotation == 11,
           "duplicate feedback must not extend rotation recovery");

    for (uint32_t slot = 0; slot < 10; ++slot)
        expect(src.choose_mrc_ev(64).logical_ev == src._mrc_active[slot],
               "GOOD slots before the skipped EV must remain ordered");
    expect(src.choose_mrc_ev(64).logical_ev == src._mrc_active[11],
           "rotation-10 opportunity for the target must be skipped");
    expect(src._mrc_evs[target].state == RoceSrc::MRC_EV_SKIP,
           "target must remain SKIP for the rest of rotation 10");

    for (uint32_t slot = 12; slot < 64; ++slot)
        src.choose_mrc_ev(64);
    expect(src._mrc_rotation == 11,
           "ordinary slot progress must enter rotation 11");
    for (uint32_t slot = 0; slot < 10; ++slot)
        src.choose_mrc_ev(64);
    RoceSrc::MrcChoice restored = src.choose_mrc_ev(64);
    expect(restored.logical_ev == target,
           "target must return at its nominal slot in rotation 11");
    expect(src._mrc_evs[target].state == RoceSrc::MRC_EV_GOOD,
           "next-rotation selection must restore target to GOOD");
}

static void test_all_skip_rotation_resolves_without_a_fallback_branch() {
    RoceSrc::setMrcCongestionPolicy(RoceSrc::MRC_POLICY_SKIP_ROTATION);
    RoceSrc::setPathEntropySize(64);
    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    src.set_src(15);
    src.set_dst(207);
    src.set_flowid(1706);
    src._flow_started = true;
    src.init_mrc_paths(64);
    for (uint32_t ev = 0; ev < 64; ++ev)
        src.mrc_mark_congested(ev, RoceSrc::MRC_CONGESTION_ECN);

    const uint32_t expected = src._mrc_active[0];
    RoceSrc::MrcChoice restored = src.choose_mrc_ev(64);
    expect(restored.logical_ev == expected,
           "all-SKIP rotation must wrap to the first naturally restored EV");
    expect(src._mrc_rotation == 1,
           "all-SKIP rotation must cross one ordinary boundary");
    expect(src._mrc_forced_cooling_use == 0,
           "skip-rotation must not use the legacy all-cooling path");
    expect(src._mrc_data_on_non_good_violations == 0,
           "skip-rotation must never select non-GOOD DATA paths");
}

static void configure_bounded_skip_source(RoceSrc& src,
                                          uint32_t packet_count) {
    RoceSrc::setTransportSemantics(
        RoceSrc::TRANSPORT_MRC_EXACT_BOUNDED);
    RoceSrc::setReceiveMode(RoceSrc::RX_SP_RETX_QUEUE);
    RoceSrc::setCongestionControl(RoceSrc::CC_NONE);
    RoceSrc::setLoadBalancing(RoceSrc::LB_MRC);
    RoceSrc::setMrcCongestionPolicy(RoceSrc::MRC_POLICY_SKIP_TOKEN);
    RoceSrc::setPathEntropySize(64);
    src._flow_lb_mode = RoceSrc::LB_MRC;
    src._flow_started = true;
    src._state_send = RoceSrc::PAUSED;
    src._highest_sent = packet_count * 4096;
    src._last_acked = 0;
    src._flow_size = packet_count * 4096;
    src.init_mrc_paths(64);
    for (uint32_t packet = 0; packet < packet_count; ++packet)
        src.bounded_note_new_send(1 + (uint64_t)packet * 4096);
}

static void test_trim_sack_reliability_and_ev_state_are_independent() {
    DropSink drop;
    Route route;
    route.push_back(&drop);
    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    configure_bounded_skip_source(src, 3);

    RoceNack* nack = RoceNack::newpkt(
        src._flow, route, 0, 7, 0x6, 0, true, 0, 1, 3, 64);
    nack->set_reason(RoceNack::TRIM);
    nack->set_missing_psn(1);
    nack->set_attempt_id(0);
    nack->set_mrc_ev(17);
    src.processNack(*nack);
    nack->free();

    expect(src._rtx_queue.size() == 1 &&
               *src._rtx_queue._seqs.begin() == 1,
           "TRIM+SACK must queue only the exact missing PSN");
    expect(src._bounded_unique_acks == 2,
           "SACK reliability must confirm the two received PSNs once");
    expect(src._mrc_evs[17].state == RoceSrc::MRC_EV_SKIP &&
               src._mrc_evs[17].congestion_epoch == 1,
           "the same NACK must independently skip exact EV17 once");
    for (uint32_t ev = 0; ev < 64; ++ev)
        if (ev != 17)
            expect(src._mrc_evs[ev].state == RoceSrc::MRC_EV_GOOD,
                   "LB feedback must not mutate unrelated EVs");

    const uint64_t unique_before = src._bounded_unique_acks;
    RoceNack* duplicate = RoceNack::newpkt(
        src._flow, route, 0, 7, 0x6, 0, true, 0, 1, 3, 64);
    duplicate->set_reason(RoceNack::TRIM);
    duplicate->set_missing_psn(1);
    duplicate->set_attempt_id(0);
    duplicate->set_mrc_ev(17);
    src.processNack(*duplicate);
    duplicate->free();
    expect(src._rtx_queue.size() == 1 &&
               src._bounded_unique_acks == unique_before,
           "duplicate NACK must not duplicate reliability state");
    expect(src._mrc_evs[17].congestion_epoch == 1,
           "duplicate NACK must not create a second LB episode");
}

static void test_ecn_ack_reliability_and_ev_state_are_independent() {
    DropSink drop;
    Route route;
    route.push_back(&drop);
    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    configure_bounded_skip_source(src, 1);

    RoceAck* ack = RoceAck::newpkt(src._flow, route, 4096, 7);
    ack->set_delivered_psn(1);
    ack->set_mrc_ev(17);
    ack->set_flags(ECN_ECHO);
    src.processAck(*ack);
    ack->free();
    expect(src._last_acked == 4096 && src._bounded_unique_acks == 1,
           "ECN ACK must advance reliability exactly once");
    expect(src._mrc_evs[17].state == RoceSrc::MRC_EV_SKIP &&
               src._mrc_evs[17].congestion_epoch == 1,
           "ECN ACK must independently skip exact EV17 once");

    RoceAck* duplicate = RoceAck::newpkt(src._flow, route, 4096, 7);
    duplicate->set_delivered_psn(1);
    duplicate->set_mrc_ev(17);
    duplicate->set_flags(ECN_ECHO);
    duplicate->set_old_duplicate_ack();
    src.processAck(*duplicate);
    duplicate->free();
    expect(src._bounded_unique_acks == 1,
           "duplicate ECN ACK must not release reliability credit twice");
    expect(src._mrc_evs[17].congestion_epoch == 1,
           "duplicate ECN ACK must not rearm EV17");
}

static void test_failure_recovery_interface_is_disabled_by_default() {
    RoceSrc::setMrcCongestionPolicy(RoceSrc::MRC_POLICY_SKIP_TOKEN);
    RoceSrc::setMrcFailureRecoveryEnabled(false);
    RoceSrc::setMrcProbeSuccessThreshold(3);
    RoceSrc::setPathEntropySize(64);
    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    src._flow_lb_mode = RoceSrc::LB_MRC;
    src._flow_started = true;
    src.init_mrc_paths(64);

    expect(!src.mrc_mark_failed(17),
           "disabled failure recovery must ignore failure transitions");
    expect(src._mrc_evs[17].state == RoceSrc::MRC_EV_GOOD,
           "disabled failure recovery must leave EV17 GOOD");
    expect(src.mrc_choose_probe_ev(64) == UINT32_MAX,
           "disabled failure recovery must not schedule probes");
}

static void test_probe_interface_uses_independent_ids_and_threshold() {
    DropSink drop;
    Route route;
    route.push_back(&drop);
    PacketFlow flow(NULL);
    RoceEvProbe* probe = RoceEvProbe::newpkt(
        flow, route, 0x123456789ULL, 17, false, true);
    expect(probe->type() == ROCEEVPROBE && probe->header_only(),
           "EV Probe must be a control-only packet type");
    expect(probe->request_id() == 0x123456789ULL &&
               probe->target_ev() == 17,
           "EV Probe identity must be independent of data PSN");
    expect(!probe->response() && probe->success(),
           "EV Probe request fields must round-trip");
    expect(probe->priority() == Packet::PRIO_HI,
           "EV Probe must use control priority");
    probe->free();

    RoceSrc::setMrcCongestionPolicy(RoceSrc::MRC_POLICY_SKIP_TOKEN);
    RoceSrc::setMrcFailureRecoveryEnabled(true);
    RoceSrc::setMrcProbeSuccessThreshold(3);
    RoceSrc::setPathEntropySize(64);
    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    src._flow_lb_mode = RoceSrc::LB_MRC;
    src._flow_started = true;
    src.init_mrc_paths(64);
    expect(src.mrc_mark_failed(17),
           "enabled failure recovery must enter ASSUMED_BAD");
    expect(src._mrc_evs[17].state == RoceSrc::MRC_EV_ASSUMED_BAD,
           "failed EV must enter ASSUMED_BAD");
    expect(!src.mrc_note_probe_result(17, true) &&
               !src.mrc_note_probe_result(17, true),
           "fewer than three successes must not restore the EV");
    expect(src._mrc_evs[17].state != RoceSrc::MRC_EV_GOOD &&
               src._mrc_evs[17].probe_successes == 2,
           "probe success count must accumulate below threshold");
    expect(!src.mrc_note_probe_result(17, false) &&
               src._mrc_evs[17].probe_successes == 0,
           "a failed probe must reset consecutive successes");
    expect(!src.mrc_note_probe_result(17, true) &&
               !src.mrc_note_probe_result(17, true) &&
               src.mrc_note_probe_result(17, true),
           "three consecutive successes must restore the EV");
    expect(src._mrc_evs[17].state == RoceSrc::MRC_EV_GOOD,
           "probe threshold must return EV17 to GOOD");
    RoceSrc::setMrcFailureRecoveryEnabled(false);
}

int main() {
    Packet::set_packet_size(4096);
    test_canonical_profile_has_64_identity_mapped_evs();
    test_explicit_legacy_ablation_uses_all_64_evs();
    test_congestion_transition_is_exact_and_non_rearming();
    test_skip_token_is_consumed_only_at_its_nominal_opportunity();
    test_all_skip_tokens_resolve_by_ordinary_rotation_progress();
    test_skip_rotation_restores_only_at_the_next_rotation();
    test_all_skip_rotation_resolves_without_a_fallback_branch();
    test_trim_sack_reliability_and_ev_state_are_independent();
    test_ecn_ack_reliability_and_ev_state_are_independent();
    test_failure_recovery_interface_is_disabled_by_default();
    test_probe_interface_uses_independent_ids_and_threshold();
    return 0;
}
