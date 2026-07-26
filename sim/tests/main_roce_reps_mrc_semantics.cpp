// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <set>
#include <sstream>
#include <vector>

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

class DataCaptureSink : public PacketSink {
public:
    DataCaptureSink() : _name("data_capture") {}

    void receivePacket(Packet& pkt) {
        if (pkt.type() != ROCE) {
            std::cerr << "expected ROCE data packet" << std::endl;
            std::exit(1);
        }
        RocePacket& data = (RocePacket&)pkt;
        seqs.push_back(data.seqno());
        retransmitted.push_back(data.retransmitted());
        pkt.free();
    }

    const string& nodename() { return _name; }

    std::vector<RocePacket::seq_t> seqs;
    std::vector<bool> retransmitted;

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

static void reset_mrc_config(uint32_t paths) {
    RoceSrc::setTransportSemantics(RoceSrc::TRANSPORT_LEGACY);
    RoceSrc::setLoadBalancing(RoceSrc::LB_MRC);
    RoceSrc::setReceiveMode(RoceSrc::RX_SP_RETX_QUEUE);
    RoceSrc::setCongestionControl(RoceSrc::CC_NONE);
    RoceSrc::setPathEntropySize(paths);
    RoceSrc::setMrcCooldownMode(RoceSrc::MRC_COOLDOWN_ONE_CYCLE);
    RoceSrc::setMrcCooldownReferencePkts(86);
    RoceSrc::setMrcAllCoolingFallback(
        RoceSrc::MRC_ALL_COOLING_EARLIEST);
    RoceSrc::setMrcFailedRetry(timeFromUs(100.0));
    RoceSrc::setMrcProbeIntervalPkts(0);
    RoceSrc::resetPathSelectionDiag();
}

static void configure_identity(RoceSrc& src, uint32_t source,
                               uint32_t destination, uint32_t flow_id) {
    src.set_src(source);
    src.set_dst(destination);
    src.set_flowid(flow_id);
    src._flow_started = true;
}

static std::vector<uint32_t> select_paths(RoceSrc& src,
                                          RoceSrc::lb_mode_t mode,
                                          uint32_t count) {
    src._flow_lb_mode = mode;
    std::vector<uint32_t> result;
    for (uint32_t i = 0; i < count; i++)
        result.push_back(
            src.choose_path_for_test(Packet::PRIO_LO, false));
    return result;
}

static bool active_contains(const RoceSrc& src, uint32_t logical_ev) {
    return std::find(src._mrc_active.begin(), src._mrc_active.end(),
                     logical_ev) != src._mrc_active.end();
}

static RoceAck* make_ack(PacketFlow& flow, Route& route,
                         RocePacket::seq_t ackno, uint32_t pathid,
                         uint32_t flags = 0) {
    RoceAck* ack = RoceAck::newpkt(flow, route, ackno, 7);
    ack->set_pathid(pathid);
    ack->set_flags(flags);
    return ack;
}

static RoceNack* make_nack(PacketFlow& flow, Route& route,
                           RocePacket::seq_t ackno, uint32_t pathid,
                           RoceNack::nack_reason_t reason) {
    RoceNack* nack = RoceNack::newpkt(flow, route, ackno, 7);
    nack->set_pathid(pathid);
    nack->set_reason(reason);
    return nack;
}

static void test_mrc_default_is_one_cycle() {
    expect(RoceSrc::mrcCooldownMode() ==
               RoceSrc::MRC_COOLDOWN_ONE_CYCLE,
           "default MRC cooldown should be one-cycle non-rearming");
    RoceSrc::setMrcCooldownReferencePkts(86);
    expect(RoceSrc::mrcCooldownReferencePkts() == 86,
           "MRC should expose the resolved BDP reference window");
    expect(RoceSrc::mrcCwndScaledSkipSelections(4) == 88,
           "four active EVs should cover the 86-packet BDP window");
    expect(RoceSrc::mrcCwndScaledSkipSelections(16) == 96,
           "sixteen active EVs should skip six complete BDP rotations");
    RoceSrc::setMrcCooldownReferencePkts(100);
    expect(RoceSrc::mrcCwndScaledSkipSelections(16) == 112,
           "an explicit 100-packet reference should reproduce the old behavior");
    RoceSrc::setMrcCooldownReferencePkts(86);
    expect(RoceSrc::mrcAllCoolingFallback() ==
               RoceSrc::MRC_ALL_COOLING_EARLIEST,
           "default all-cooling fallback should be earliest");
}

static void test_mrc_one_cycle_duplicate_feedback_does_not_rearm() {
    reset_mrc_config(8);
    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    src.set_flowid(700);
    src._flow_started = true;
    src.init_mrc_paths(8);

    uint32_t ev = src._mrc_active[0];
    src.mrc_mark_congested(ev, RoceSrc::MRC_CONGESTION_ECN);
    uint64_t original_deadline =
        src._mrc_evs[ev].cool_until_select_count;
    expect(original_deadline == 8,
           "the first one-cycle signal should skip eight selections");

    src._mrc_select_counter = 3;
    src.mrc_mark_congested(ev, RoceSrc::MRC_CONGESTION_TRIM);
    expect(src._mrc_evs[ev].cool_until_select_count == original_deadline,
           "feedback received while cooling must not extend the deadline");
    expect(src._mrc_duplicate_feedback_ignored == 1,
           "ignored one-cycle feedback should be counted");

    src._mrc_select_counter = original_deadline;
    expect(src.mrc_ev_selectable(ev),
           "the EV should reactivate at its original deadline");
    src.mrc_mark_congested(ev, RoceSrc::MRC_CONGESTION_ECN);
    expect(src._mrc_evs[ev].cool_until_select_count ==
               original_deadline + 8,
           "feedback after reactivation should start a new cooldown");
}

static void test_reps_clean_ack_recycling() {
    DropSink drop;
    Route route;
    route.push_back(&drop);

    RoceSrc::setLoadBalancing(RoceSrc::LB_REPS);
    RoceSrc::setPathEntropySize(16);
    RoceSrc::setRepsBufferSize(4);
    RoceSrc::setRepsWarmupPkts(0);

    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_flowid(11);
    src._flow_started = true;
    src.reset_reps_buffer();

    RoceAck* clean_a = make_ack(src._flow, route, 1000, 3);
    src.update_reps(*clean_a);
    clean_a->free();
    RoceAck* ecn_b = make_ack(src._flow, route, 2000, 5, ECN_ECHO);
    src.update_reps(*ecn_b);
    ecn_b->free();
    RoceAck* clean_c = make_ack(src._flow, route, 3000, 7);
    src.update_reps(*clean_c);
    clean_c->free();

    expect(src.choose_path(Packet::PRIO_LO, false) == 3,
           "REPS should consume the oldest clean cached EV first");
    expect(src.choose_path(Packet::PRIO_LO, false) == 7,
           "REPS should not cache an ECN-marked EV");
    expect(src._reps_cached_sends == 2,
           "REPS cached-send counter should include both clean ACKs");
}

static void test_mrc_encoded_path_identity() {
    reset_mrc_config(16);
    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_src(11);
    src.set_dst(27);
    src.set_flowid(701);
    src._flow_started = true;
    src.init_mrc_paths(16);

    expect(src._mrc_evs.size() == 16,
           "MRC should create one EV per unique physical path");
    expect(src._mrc_active.size() == 16,
           "MRC should activate every path when the path space is at most 32");
    expect(src._mrc_backup.empty(),
           "MRC should not create duplicate-path backup EVs");
    for (uint32_t i = 0; i < src._mrc_evs.size(); i++) {
        expect(src._mrc_evs[i].logical_ev == i,
               "MRC EV IDs should be stable path encodings");
        expect(src._mrc_evs[i].physical_path == i,
               "single-plane MRC EVs should decode directly to path IDs");
    }
}

static void test_mrc_deterministic_active_subset_and_unique_backups() {
    reset_mrc_config(64);
    RoceSrc a(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    RoceSrc b(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    a.set_src(3);
    a.set_dst(49);
    a.set_flowid(702);
    b.set_src(3);
    b.set_dst(49);
    b.set_flowid(702);
    a._flow_started = true;
    b._flow_started = true;
    a.init_mrc_paths(64);
    b.init_mrc_paths(64);

    expect(a._mrc_active.size() == 32,
           "MRC active set should be capped at 32 encoded EVs");
    expect(a._mrc_backup.size() == 32,
           "remaining unique encoded paths should form the backup set");
    expect(a._mrc_active == b._mrc_active && a._mrc_backup == b._mrc_backup,
           "the same QP identity should reproduce the same deterministic permutation");

    std::set<uint32_t> active(a._mrc_active.begin(), a._mrc_active.end());
    std::set<uint32_t> backup(a._mrc_backup.begin(), a._mrc_backup.end());
    expect(active.size() == 32 && backup.size() == 32,
           "active and backup sets should contain unique EV IDs");
    for (std::set<uint32_t>::const_iterator it = active.begin();
         it != active.end(); ++it)
        expect(backup.count(*it) == 0,
               "backup EVs should not duplicate active physical paths");
}

static void test_rr_matches_healthy_mrc_order() {
    for (uint32_t paths = 8; paths <= 16; paths *= 2) {
        reset_mrc_config(paths);
        for (uint32_t flow_id = 801; flow_id <= 802; flow_id++) {
            RoceSrc rr(NULL, NULL, test_eventlist(),
                       speedFromMbps((uint64_t)100000));
            RoceSrc mrc(NULL, NULL, test_eventlist(),
                        speedFromMbps((uint64_t)100000));
            configure_identity(rr, 11, 27, flow_id);
            configure_identity(mrc, 11, 27, flow_id);

            std::vector<uint32_t> rr_paths =
                select_paths(rr, RoceSrc::LB_RR, paths * 4);
            std::vector<uint32_t> mrc_paths =
                select_paths(mrc, RoceSrc::LB_MRC, paths * 4);
            expect(rr_paths == mrc_paths,
                   "RR must match healthy MRC EV/path order exactly");
        }
    }

    reset_mrc_config(8);
    RoceSrc first(NULL, NULL, test_eventlist(),
                  speedFromMbps((uint64_t)100000));
    RoceSrc second(NULL, NULL, test_eventlist(),
                   speedFromMbps((uint64_t)100000));
    configure_identity(first, 11, 27, 801);
    configure_identity(second, 11, 27, 802);
    expect(select_paths(first, RoceSrc::LB_RR, 8) !=
               select_paths(second, RoceSrc::LB_RR, 8),
           "different QP identities should retain distinct permutations");
}

static void test_mrc_active_ev_override_and_rr_equivalence() {
    reset_mrc_config(8);
    RoceSrc::setMrcActiveEvs(0);
    RoceSrc default_mrc(NULL, NULL, test_eventlist(),
                        speedFromMbps((uint64_t)100000));
    configure_identity(default_mrc, 11, 27, 804);
    default_mrc.init_mrc_paths(8);
    expect(default_mrc._mrc_active.size() == 8 &&
               default_mrc._mrc_backup.empty(),
           "unset active-EV override must preserve the default");

    for (uint32_t active_evs = 2; active_evs <= 8; active_evs *= 2) {
        RoceSrc::setMrcActiveEvs(active_evs);
        RoceSrc rr(NULL, NULL, test_eventlist(),
                   speedFromMbps((uint64_t)100000));
        RoceSrc mrc(NULL, NULL, test_eventlist(),
                    speedFromMbps((uint64_t)100000));
        configure_identity(rr, 11, 27, 805);
        configure_identity(mrc, 11, 27, 805);
        mrc.init_mrc_paths(8);

        expect(mrc._mrc_active.size() == active_evs,
               "active-EV override must limit MRC active state");
        expect(mrc._mrc_backup.size() == 8 - active_evs,
               "non-active EVs must remain unique MRC backups");
        expect(select_paths(rr, RoceSrc::LB_RR, active_evs * 4) ==
                   select_paths(mrc, RoceSrc::LB_MRC, active_evs * 4),
               "RR must match MRC for each active-EV setting");
    }
    RoceSrc::setMrcActiveEvs(0);
}

static void test_rr_diverges_only_after_mrc_feedback() {
    reset_mrc_config(8);
    RoceSrc rr(NULL, NULL, test_eventlist(),
               speedFromMbps((uint64_t)100000));
    RoceSrc mrc(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    configure_identity(rr, 11, 27, 803);
    configure_identity(mrc, 11, 27, 803);

    expect(select_paths(rr, RoceSrc::LB_RR, 8) ==
               select_paths(mrc, RoceSrc::LB_MRC, 8),
           "matched schemes must complete the first healthy rotation");

    uint32_t cooled = mrc._mrc_active[mrc._mrc_active_cursor];
    mrc.mrc_mark_congested(cooled, RoceSrc::MRC_CONGESTION_ECN);
    uint32_t rr_next =
        select_paths(rr, RoceSrc::LB_RR, 1).front();
    uint32_t mrc_next =
        select_paths(mrc, RoceSrc::LB_MRC, 1).front();
    expect(rr_next == cooled,
           "stateless RR must keep the common healthy order");
    expect(mrc_next != cooled,
           "MRC must skip the EV after effective congestion feedback");
}

static void test_mrc_shared_disseminates_only_path_feedback() {
    reset_mrc_config(4);
    RoceSrc::setHostsPerTor(4);
    RoceSrc::resetMrcSharedState();

    RoceSrc publisher(NULL, NULL, test_eventlist(), speedFromMbps(100000.0));
    RoceSrc consumer(NULL, NULL, test_eventlist(), speedFromMbps(100000.0));
    RoceSrc outside(NULL, NULL, test_eventlist(), speedFromMbps(100000.0));
    configure_identity(publisher, 1, 8, 100);
    configure_identity(consumer, 1, 8, 101);
    configure_identity(outside, 2, 8, 102);
    publisher._flow_lb_mode = RoceSrc::LB_MRC_SHARED;
    consumer._flow_lb_mode = RoceSrc::LB_MRC_SHARED;
    outside._flow_lb_mode = RoceSrc::LB_MRC_SHARED;
    publisher.init_mrc_paths(4);
    consumer.init_mrc_paths(4);
    outside.init_mrc_paths(4);

    uint32_t ev = publisher._mrc_active[0];
    publisher.note_mrc_packet_ev(1, ev);
    Route route;
    RoceAck* ecn = make_ack(publisher._flow, route, 1, ev, ECN_ECHO);
    ecn->set_mrc_ev(ev);
    publisher.update_mrc_on_ack(*ecn);
    ecn->free();

    expect(RoceSrc::_mrc_shared_events.size() == 1,
           "a real MRC-shared ECN transition must publish one shared key");
    expect(publisher._mrc_flow_metrics.shared_updates_published == 1,
           "the publishing QP must count its shared state update");
    double cwnd_before = consumer._cc_cwnd_pkts;
    size_t rtx_before = consumer._rtx_queue.size();
    uint64_t ack_before = consumer._last_acked;
    uint64_t inflight_before = consumer._bounded_inflight_pkts;

    consumer.choose_mrc_ev(4);
    outside.consume_mrc_shared();
    expect(consumer._mrc_evs[ev].state == RoceSrc::MRC_PATH_COOLING,
           "an eligible QP must consume the shared ECN before selection");
    expect(consumer._mrc_flow_metrics.shared_updates_consumed == 1 &&
               consumer._mrc_flow_metrics.shared_updates_from_other_qps == 1,
           "the consuming QP must attribute a shared update from another QP");
    expect(outside._mrc_evs[ev].state == RoceSrc::MRC_PATH_ACTIVE,
           "a QP on another source NIC must not consume the shared ECN");
    expect(consumer._cc_cwnd_pkts == cwnd_before &&
               consumer._rtx_queue.size() == rtx_before &&
               consumer._last_acked == ack_before &&
               consumer._bounded_inflight_pkts == inflight_before,
           "shared MRC feedback must not mutate transport or CC state");

    uint64_t deadline = consumer._mrc_evs[ev].cool_until_select_count;
    consumer.consume_mrc_shared();
    expect(consumer._mrc_evs[ev].cool_until_select_count == deadline,
           "a shared generation must be consumed at most once per QP");

    RoceSrc redundant(NULL, NULL, test_eventlist(), speedFromMbps(100000.0));
    configure_identity(redundant, 1, 8, 103);
    redundant._flow_lb_mode = RoceSrc::LB_MRC;
    redundant.init_mrc_paths(4);
    redundant.note_mrc_packet_ev(1, ev);
    RoceAck* duplicate_discovery =
        make_ack(redundant._flow, route, 1, ev, ECN_ECHO);
    duplicate_discovery->set_mrc_ev(ev);
    redundant.update_mrc_on_ack(*duplicate_discovery);
    duplicate_discovery->free();
    expect(redundant._mrc_flow_metrics.redundant_discoveries == 1,
           "a later local signal must count prior cross-QP discovery");
}

static void test_mrc_shared_matches_mrc_without_feedback() {
    reset_mrc_config(8);
    RoceSrc::resetMrcSharedState();
    for (uint32_t flow = 1; flow <= 4; flow++) {
        RoceSrc mrc(NULL, NULL, test_eventlist(), speedFromMbps(100000.0));
        RoceSrc shared(NULL, NULL, test_eventlist(), speedFromMbps(100000.0));
        configure_identity(mrc, 1, 8, flow);
        configure_identity(shared, 1, 8, flow);
        expect(select_paths(mrc, RoceSrc::LB_MRC, 32) ==
                   select_paths(shared, RoceSrc::LB_MRC_SHARED, 32),
               "MRC-shared must match MRC when no feedback is published");
    }
}

static void test_mrc_flow_mechanism_counters() {
    reset_mrc_config(4);
    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps(100000.0));
    configure_identity(src, 1, 8, 200);
    src._flow_lb_mode = RoceSrc::LB_MRC;
    src.init_mrc_paths(4);
    src.reset_mrc_flow_metrics();
    src._mrc_flow_metrics.active_evs = 4;
    src._mrc_flow_metrics.initial_active.insert(
        src._mrc_active.begin(), src._mrc_active.end());

    for (uint32_t i = 0; i < 4; i++)
        src.mrc_flow_note_new_selection(src._mrc_active[i]);
    expect(src._mrc_flow_metrics.unique_active.size() == 4 &&
               src._mrc_flow_metrics.full_sweeps == 1 &&
               src._mrc_flow_metrics.first_full_sweep_set,
           "MRC flow diagnostics must record EV coverage and first sweep");

    src.mrc_flow_note_effective_update(
        src._mrc_active[0], 1, false, false);
    expect(src._mrc_flow_metrics.effective_state_updates == 1 &&
               src._mrc_flow_metrics.actionable_feedback == 0,
           "an update without a later new-data selection is not actionable");
    src.mrc_flow_note_new_selection(src._mrc_active[1]);
    expect(src._mrc_flow_metrics.actionable_feedback == 1 &&
               src._mrc_flow_metrics.new_selections_after_first_update == 1,
           "the next new-data selection must make a pending update actionable");
}

static void test_mrc_ecn_uses_echoed_ev() {
    DropSink drop;
    Route route;
    route.push_back(&drop);
    reset_mrc_config(4);

    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_flowid(704);
    src._flow_started = true;
    src.init_mrc_paths(4);
    uint32_t cumulative_ev = src._mrc_active[0];
    uint32_t feedback_ev = src._mrc_active[1];
    src.note_mrc_packet_ev(1, cumulative_ev);

    RoceAck* ecn = make_ack(src._flow, route, 1, feedback_ev, ECN_ECHO);
    ecn->set_mrc_ev(feedback_ev);
    src.update_mrc_on_ack(*ecn);
    ecn->free();

    expect(src._mrc_evs[cumulative_ev].state == RoceSrc::MRC_PATH_ACTIVE,
           "ECN must not cool the EV inferred from a cumulative ACK");
    expect(src._mrc_evs[feedback_ev].state == RoceSrc::MRC_PATH_COOLING,
           "ECN should cool the explicitly echoed EV");
    expect(src._mrc_feedback_exact_ev_events == 1 &&
           src._mrc_feedback_cumulative_mismatch_events == 1,
           "exact feedback attribution should record cumulative-ACK mismatches");
}

static void test_mrc_trim_soft_skips_exact_ev() {
    DropSink drop;
    Route route;
    route.push_back(&drop);
    reset_mrc_config(4);
    RoceSrc::setMrcCooldownMode(RoceSrc::MRC_COOLDOWN_CWND_SCALED);

    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_flowid(705);
    src._flow_started = true;
    src.init_mrc_paths(4);
    src._last_acked = 0;
    src._highest_sent = Packet::data_packet_size();
    uint32_t missing_ev = src._mrc_active[0];
    uint32_t feedback_ev = src._mrc_active[1];
    src.note_mrc_packet_ev(1, missing_ev);

    RoceNack* trim = make_nack(src._flow, route, 0, feedback_ev,
                               RoceNack::TRIM);
    trim->set_mrc_ev(feedback_ev);
    src.processNack(*trim);
    trim->free();

    expect(src._mrc_evs[missing_ev].state == RoceSrc::MRC_PATH_ACTIVE,
           "TRIM must not cool an EV inferred from first-missing sequence");
    expect(src._mrc_evs[feedback_ev].state == RoceSrc::MRC_PATH_COOLING,
           "TRIM should soft-skip the explicitly echoed EV");
    expect(src._mrc_evs[feedback_ev].cool_until_select_count == 88,
           "TRIM should use the same cwnd-scaled penalty as ECN");
    expect(src._mrc_trim_events == 1 && src._mrc_trim_cooling_events == 1,
           "TRIM should count one soft-skip event");
    expect(src._rtx_queue._seqs.size() == 1 &&
           *src._rtx_queue._seqs.begin() == 1,
           "TRIM should queue only the missing packet for SP retransmission");
}

static void test_dcqcn_variant_nacks_keep_natural_inflate() {
    DropSink drop;
    Route route;
    route.push_back(&drop);

    RoceSrc::setLoadBalancing(RoceSrc::LB_ECMP);
    RoceSrc::setReceiveMode(RoceSrc::RX_SP_RETX_QUEUE);
    RoceSrc::setCongestionControl(RoceSrc::CC_DCQCN_VARIANT);

    const RoceNack::nack_reason_t reasons[] = {
        RoceNack::OOO, RoceNack::TRIM, RoceNack::LOSS
    };
    for (size_t i = 0; i < 3; i++) {
        RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
        src._flow_started = true;
        src._state_send = RoceSrc::PAUSED;
        src._highest_sent = 7 * Packet::data_packet_size();
        src._last_acked = 0;
        src._cc_cwnd_pkts = 10.0;
        src._cc_inflate_pkts = 5.0;

        RoceNack* nack = RoceNack::newpkt(
            src._flow, route, 0, 7, 0x76, 0, true, 0, 1, 7, 64);
        nack->set_reason(reasons[i]);
        src.processNack(*nack);
        nack->free();

        expect(src._cc_cwnd_pkts == 9.0,
               "every variant NACK should reduce cwnd by one");
        expect(src._cc_inflate_pkts == 5.0,
               "the final natural variant must not reset inflate on NACK");
    }

    for (size_t i = 0; i < 3; i++) {
        RoceSrc no_sack_src(NULL, NULL, test_eventlist(),
                            speedFromMbps((uint64_t)100000));
        no_sack_src._flow_started = true;
        no_sack_src._state_send = RoceSrc::PAUSED;
        no_sack_src._highest_sent = 7 * Packet::data_packet_size();
        no_sack_src._last_acked = 0;
        no_sack_src._cc_cwnd_pkts = 10.0;
        no_sack_src._cc_inflate_pkts = 5.0;

        RoceNack* nack = make_nack(no_sack_src._flow, route, 0, 7,
                                   reasons[i]);
        no_sack_src.processNack(*nack);
        nack->free();

        expect(no_sack_src._cc_cwnd_pkts == 9.0,
               "NACK without SACK should reduce variant cwnd by one");
        expect(no_sack_src._cc_inflate_pkts == 5.0,
               "natural inflate must not depend on a SACK bitmap");
    }

    RoceSrc::setCongestionControl(RoceSrc::CC_NONE);
}

static void test_dcqcn_variant_old_duplicate_narrow_mode() {
    DropSink drop;
    Route route;
    route.push_back(&drop);

    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src._flow_started = true;
    src._cc_cwnd_pkts = 10.0;

    RoceAck* ooo_duplicate = make_ack(src._flow, route, 0, 0);
    ooo_duplicate->set_duplicate_ack();
    RoceAck* old_duplicate = make_ack(src._flow, route, 0, 0);
    old_duplicate->set_old_duplicate_ack();

    RoceSrc::setCongestionControl(RoceSrc::CC_DCQCN_VARIANT_NODUP_OLD);
    src._cc_inflate_pkts = 5.0;
    src.update_congestion_control_on_ack(*ooo_duplicate, 0.0);
    expect(src._cc_inflate_pkts == 6.0,
           "narrow mode must retain OOO duplicate ACK inflation");

    src._cc_inflate_pkts = 5.0;
    src.update_congestion_control_on_ack(*old_duplicate, 0.0);
    expect(src._cc_inflate_pkts == 5.0,
           "narrow mode must suppress old duplicate ACK inflation");

    src._cc_inflate_pkts = 5.0;
    src.update_congestion_control_on_ack(*old_duplicate, 2.0);
    expect(src._cc_inflate_pkts == 3.0,
           "old duplicate ACK must retain cumulative deflation");

    old_duplicate->set_flags(ECN_ECHO);
    src._cc_cwnd_pkts = 10.0;
    src._cc_inflate_pkts = 5.0;
    src.update_congestion_control_on_ack(*old_duplicate, 0.0);
    expect(src._cc_cwnd_pkts == 9.5,
           "ECN on an old duplicate ACK must still reduce cwnd");
    expect(src._cc_inflate_pkts == 5.0,
           "ECN old duplicate ACK must not create inflate credit");

    ooo_duplicate->free();
    old_duplicate->free();
    RoceSrc::setCongestionControl(RoceSrc::CC_NONE);
}

static void test_mrc_ooo_is_retransmit_only() {
    DropSink drop;
    Route route;
    route.push_back(&drop);
    reset_mrc_config(4);

    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_flowid(55);
    src._flow_started = true;
    src.init_mrc_paths(4);
    src._last_acked = 0;
    src._highest_sent = Packet::data_packet_size();
    uint32_t ev = src._mrc_active[0];
    size_t active_before = src._mrc_active.size();
    src.note_mrc_packet_ev(1, ev);

    RoceNack* ooo = make_nack(src._flow, route, 0, ev, RoceNack::OOO);
    ooo->set_mrc_ev(ev);
    src.processNack(*ooo);
    ooo->free();

    expect(src._rtx_queue._seqs.size() == 1 &&
           *src._rtx_queue._seqs.begin() == 1,
           "OOO should queue only the missing packet for SP retransmission");
    expect(src._mrc_evs[ev].state == RoceSrc::MRC_PATH_ACTIVE,
           "OOO should not change EV state");
    expect(src._mrc_nack_ooo_ignored_for_failure == 1,
           "OOO should be explicitly counted as neutral to MRC state");
    expect(src._mrc_backup_replacement_events == 0 &&
           src._mrc_active.size() == active_before,
           "OOO should neither promote backups nor change active-set size");
}

static void test_mrc_loss_marks_exact_ev_failed_and_promotes_backup() {
    DropSink drop;
    Route route;
    route.push_back(&drop);
    reset_mrc_config(64);

    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_flowid(703);
    src._flow_started = true;
    src.init_mrc_paths(64);
    src._last_acked = 0;
    src._highest_sent = Packet::data_packet_size();
    uint32_t ev = src._mrc_active[0];
    src.note_mrc_packet_ev(1, ev);

    RoceNack* loss = make_nack(src._flow, route, 0, ev, RoceNack::LOSS);
    loss->set_mrc_ev(ev);
    src.processNack(*loss);
    loss->free();

    expect(src._mrc_evs[ev].state == RoceSrc::MRC_PATH_FAILED,
           "LOSS should mark the explicitly echoed EV assumed bad");
    expect(!active_contains(src, ev) && src._mrc_active.size() == 32,
           "LOSS should replace a failed active EV with one unique backup");
    expect(src._mrc_nack_loss_fail_events == 1 &&
           src._mrc_failure_backup_promotions == 1,
           "LOSS failure and backup promotion should be counted");
}

static void test_mrc_rto_marks_mapped_ev_failed_and_promotes_backup() {
    reset_mrc_config(64);
    RoceSrc::setCongestionControl(RoceSrc::CC_DCQCN_VARIANT);
    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_flowid(706);
    src._flow_started = true;
    src.init_mrc_paths(64);
    src._last_acked = 0;
    src._highest_sent = Packet::data_packet_size();
    src._cc_cwnd_pkts = 10.0;
    src._cc_inflate_pkts = 5.0;
    src.set_flowsize(2 * Packet::data_packet_size());
    uint32_t ev = src._mrc_active[0];
    src.note_mrc_packet_ev(1, ev);

    simtime_picosec now = timeFromUs(1.0);
    src._rtx_timeout = now;
    src.rtx_timer_hook(now, timeFromUs(1.0));

    expect(src._rtx_queue._seqs.size() == 1 &&
           *src._rtx_queue._seqs.begin() == 1,
           "RTO should queue only the first missing packet");
    expect(src._mrc_evs[ev].state == RoceSrc::MRC_PATH_FAILED &&
           !active_contains(src, ev),
           "RTO should mark the mapped EV assumed bad");
    expect(src._mrc_active.size() == 32 &&
           src._mrc_rto_fail_events == 1 &&
           src._mrc_failure_backup_promotions == 1,
           "RTO should promote one unique backup and preserve active-set size");
    expect(src._cc_cwnd_pkts == 9.0 && src._cc_inflate_pkts == 5.0,
           "natural inflate mode should preserve credit across RTO");
    RoceSrc::setCongestionControl(RoceSrc::CC_NONE);
}

static void test_mrc_cycle_cooldown_skips_one_rotation() {
    reset_mrc_config(4);
    RoceSrc::setMrcCooldownMode(RoceSrc::MRC_COOLDOWN_ONE_CYCLE);
    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_flowid(22);
    src._flow_started = true;
    src.init_mrc_paths(4);
    uint32_t ev = src._mrc_active[0];
    uint64_t before = src._mrc_select_counter;
    src.mrc_mark_congested(ev);

    expect(src._mrc_evs[ev].state == RoceSrc::MRC_PATH_COOLING,
           "congestion should move the exact EV to cooling");
    expect(src._mrc_evs[ev].cool_until_select_count == before + 4,
           "cycle cooldown should skip one active-EV rotation");
    for (uint32_t i = 0; i < 4; i++)
        expect(src.choose_mrc_ev(4).logical_ev != ev,
               "a cooling EV should be skipped while alternatives exist");
}

static void test_mrc_cwnd_scaled_covers_reference_window() {
    reset_mrc_config(4);
    RoceSrc::setMrcCooldownMode(RoceSrc::MRC_COOLDOWN_CWND_SCALED);
    RoceSrc four(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    four.set_flowid(26);
    four._flow_started = true;
    four.init_mrc_paths(4);
    uint32_t four_ev = four._mrc_active[0];
    four.mrc_mark_congested(four_ev, RoceSrc::MRC_CONGESTION_ECN);
    expect(four._mrc_evs[four_ev].cool_until_select_count == 88,
           "four EVs should skip twenty-two rotations covering one BDP");
    four.mrc_mark_congested(four_ev, RoceSrc::MRC_CONGESTION_ECN);
    expect(four._mrc_evs[four_ev].cool_until_select_count == 88,
           "in-flight ECN must not extend a cwnd-scaled cooldown");

    reset_mrc_config(16);
    RoceSrc::setMrcCooldownMode(RoceSrc::MRC_COOLDOWN_CWND_SCALED);
    RoceSrc sixteen(NULL, NULL, test_eventlist(),
                    speedFromMbps((uint64_t)100000));
    sixteen.set_flowid(27);
    sixteen._flow_started = true;
    sixteen.init_mrc_paths(16);
    uint32_t sixteen_ev = sixteen._mrc_active[0];
    sixteen.mrc_mark_congested(sixteen_ev, RoceSrc::MRC_CONGESTION_ECN);
    expect(sixteen._mrc_evs[sixteen_ev].cool_until_select_count == 96,
           "sixteen EVs should skip six complete BDP rotations");
}

static void test_mrc_cwnd_scaled_trim_matches_ecn() {
    reset_mrc_config(16);
    RoceSrc::setMrcCooldownMode(RoceSrc::MRC_COOLDOWN_CWND_SCALED);
    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_flowid(28);
    src._flow_started = true;
    src.init_mrc_paths(16);
    uint32_t ev = src._mrc_active[0];
    src.mrc_mark_congested(ev, RoceSrc::MRC_CONGESTION_TRIM);
    expect(src._mrc_evs[ev].cool_until_select_count == 96,
           "cwnd-scaled mode must apply the ECN penalty to TRIM");
}

static void test_mrc_clean_ack_is_state_neutral() {
    DropSink drop;
    Route route;
    route.push_back(&drop);
    reset_mrc_config(4);

    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_flowid(68);
    src._flow_started = true;
    src.init_mrc_paths(4);
    src._last_acked = 0;
    src._highest_sent = Packet::data_packet_size();
    uint32_t ev = src._mrc_active[0];
    src.note_mrc_packet_ev(1, ev);
    src._rtx_queue.insert(1);

    RoceAck* ack = make_ack(src._flow, route, Packet::data_packet_size(), ev);
    ack->set_mrc_ev(ev);
    src.processAck(*ack);
    ack->free();

    expect(src._rtx_queue._seqs.empty(),
           "clean ACK should clean the selective retransmission queue");
    expect(src._mrc_evs[ev].state == RoceSrc::MRC_PATH_ACTIVE,
           "clean ACK should not change MRC EV state");
}

static void test_mrc_retransmit_queue_has_send_priority() {
    DataCaptureSink data;
    Route route;
    route.push_back(&data);
    reset_mrc_config(4);

    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_flowid(69);
    src._flow_started = true;
    src._route = &route;
    src._last_acked = 0;
    src._highest_sent = Packet::data_packet_size();
    src.set_flowsize(3 * Packet::data_packet_size());
    src.init_mrc_paths(4);
    src.note_mrc_packet_ev(1, src._mrc_active[0]);
    src._rtx_queue.insert(1);

    expect(src.send_packet(), "send_packet should serve retransmission work");
    expect(data.seqs.size() == 1 && data.seqs[0] == 1 &&
           data.retransmitted[0],
           "selective retransmission should be sent before new data");
}

static void test_mrc_all_cooling_earliest_fallback_is_counted() {
    reset_mrc_config(2);
    RoceSrc::setMrcAllCoolingFallback(
        RoceSrc::MRC_ALL_COOLING_EARLIEST);
    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_flowid(77);
    src._flow_started = true;
    src.init_mrc_paths(2);
    uint32_t late = src._mrc_active[0];
    uint32_t early = src._mrc_active[1];
    src._mrc_evs[late].state = RoceSrc::MRC_PATH_COOLING;
    src._mrc_evs[late].cool_until_select_count = src._mrc_select_counter + 10;
    src._mrc_evs[early].state = RoceSrc::MRC_PATH_COOLING;
    src._mrc_evs[early].cool_until_select_count = src._mrc_select_counter + 5;

    expect(src.choose_mrc_ev(2).logical_ev == early,
           "all-cooling fallback should choose the earliest cycle expiry");
    expect(src._mrc_forced_cooling_use == 1,
           "all-cooling fallback should be counted");
}

static void test_mrc_all_cooling_round_robin_preserves_spraying() {
    reset_mrc_config(2);
    RoceSrc::setMrcAllCoolingFallback(
        RoceSrc::MRC_ALL_COOLING_ROUND_ROBIN);
    RoceSrc src(NULL, NULL, test_eventlist(), speedFromMbps((uint64_t)100000));
    src.set_flowid(78);
    src._flow_started = true;
    src.init_mrc_paths(2);
    uint32_t first = src._mrc_active[0];
    uint32_t second = src._mrc_active[1];
    src._mrc_active_cursor = 0;
    src._mrc_evs[first].state = RoceSrc::MRC_PATH_COOLING;
    src._mrc_evs[first].cool_until_select_count =
        src._mrc_select_counter + 100;
    src._mrc_evs[second].state = RoceSrc::MRC_PATH_COOLING;
    src._mrc_evs[second].cool_until_select_count =
        src._mrc_select_counter + 50;

    expect(src.choose_mrc_ev(2).logical_ev == first,
           "round-robin fallback should start at the active cursor");
    expect(src.choose_mrc_ev(2).logical_ev == second,
           "round-robin fallback should advance through cooling EVs");
    expect(src.choose_mrc_ev(2).logical_ev == first,
           "round-robin fallback should wrap through active order");
    expect(src._mrc_evs[first].state == RoceSrc::MRC_PATH_COOLING &&
           src._mrc_evs[second].state == RoceSrc::MRC_PATH_COOLING,
           "forced round-robin use must not clear cooling state");
    expect(src._mrc_forced_cooling_use == 3,
           "each all-cooling round-robin selection should be counted");
}

int main() {
    Packet::set_packet_size(1000);
    test_mrc_default_is_one_cycle();
    test_mrc_one_cycle_duplicate_feedback_does_not_rearm();
    test_reps_clean_ack_recycling();
    test_mrc_encoded_path_identity();
    test_mrc_deterministic_active_subset_and_unique_backups();
    test_rr_matches_healthy_mrc_order();
    test_mrc_active_ev_override_and_rr_equivalence();
    test_rr_diverges_only_after_mrc_feedback();
    test_mrc_shared_disseminates_only_path_feedback();
    test_mrc_shared_matches_mrc_without_feedback();
    test_mrc_flow_mechanism_counters();
    test_mrc_ecn_uses_echoed_ev();
    test_mrc_trim_soft_skips_exact_ev();
    test_dcqcn_variant_nacks_keep_natural_inflate();
    test_dcqcn_variant_old_duplicate_narrow_mode();
    test_mrc_ooo_is_retransmit_only();
    test_mrc_loss_marks_exact_ev_failed_and_promotes_backup();
    test_mrc_rto_marks_mapped_ev_failed_and_promotes_backup();
    test_mrc_cycle_cooldown_skips_one_rotation();
    test_mrc_cwnd_scaled_covers_reference_window();
    test_mrc_cwnd_scaled_trim_matches_ecn();
    test_mrc_clean_ack_is_state_neutral();
    test_mrc_retransmit_queue_has_send_priority();
    test_mrc_all_cooling_earliest_fallback_is_counted();
    test_mrc_all_cooling_round_robin_preserves_spraying();
    return 0;
}
