// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <set>
#include <vector>

#include "eventlist.h"
#include "network.h"
#define private public
#include "roce.h"
#undef private

static void expect(bool condition, const char* message);

class DataCaptureSink : public PacketSink {
public:
    DataCaptureSink() : _name("hybrid_nmrc_data_capture") {}

    void receivePacket(Packet& pkt) {
        expect(pkt.type() == ROCE,
               "hybrid n-MRC test route must carry RoCE data");
        RocePacket& data = (RocePacket&)pkt;
        pathids.push_back(data.pathid());
        evs.push_back(data.mrc_ev());
        seqs.push_back(data.seqno());
        pkt.free();
    }

    const string& nodename() { return _name; }

    std::vector<uint32_t> pathids;
    std::vector<uint32_t> evs;
    std::vector<RocePacket::seq_t> seqs;

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

static RoceSrc* make_src(uint32_t flow_id) {
    RoceSrc* src = new RoceSrc(
        NULL, NULL, test_eventlist(), 400000000000ULL);
    src->set_flowid(flow_id);
    src->set_src(17);
    src->set_dst(113);
    return src;
}

static std::vector<uint32_t> values_for(RoceSrc::nmrc_ev_mode_t mode,
                                        uint32_t paths,
                                        uint32_t flow_id) {
    RoceSrc::setNmrcEvMode(mode);
    RoceSrc* src = make_src(flow_id);
    src->init_nmrc_evs_for_test(paths);
    return src->nmrc_ev_values_for_test();
}

static std::vector<uint32_t> mappings_for(RoceSrc::nmrc_ev_mode_t mode,
                                          uint32_t paths,
                                          uint32_t flow_id) {
    RoceSrc::setNmrcEvMode(mode);
    RoceSrc* src = make_src(flow_id);
    src->init_nmrc_evs_for_test(paths);
    return src->nmrc_physical_paths_for_test();
}

static void test_set_sizes_and_uniqueness() {
    const uint32_t path_counts[] = {1, 8, 32, 64};
    const RoceSrc::nmrc_ev_mode_t modes[] = {
        RoceSrc::NMRC_EV_ENCODED,
        RoceSrc::NMRC_EV_RANDOM_MATCHED,
        RoceSrc::NMRC_EV_RANDOM32
    };

    for (uint32_t mode_ix = 0; mode_ix < 3; mode_ix++) {
        for (uint32_t path_ix = 0; path_ix < 4; path_ix++) {
            uint32_t paths = path_counts[path_ix];
            std::vector<uint32_t> values = values_for(
                modes[mode_ix], paths, 700 + path_ix);
            uint32_t expected = modes[mode_ix] == RoceSrc::NMRC_EV_RANDOM32 ?
                32 : std::min(paths, 32U);
            expect(values.size() == expected,
                   "hybrid n-MRC EV set size does not match its mode");
            std::set<uint32_t> unique(values.begin(), values.end());
            expect(unique.size() == values.size(),
                   "EV values must be unique within one QP");
            expect(values.empty() ||
                       *std::max_element(values.begin(), values.end()) <= 65535,
                   "logical EV values must fit the 16-bit EV space");

            std::vector<uint32_t> physical = mappings_for(
                modes[mode_ix], paths, 700 + path_ix);
            expect(physical.size() == values.size(),
                   "each logical EV needs one diagnostic physical mapping");
            for (uint32_t i = 0; i < physical.size(); i++)
                expect(physical[i] < paths,
                       "diagnostic physical mappings must stay in path space");
            if (modes[mode_ix] == RoceSrc::NMRC_EV_ENCODED) {
                std::set<uint32_t> unique_paths(
                    physical.begin(), physical.end());
                expect(unique_paths.size() == physical.size(),
                       "encoded EVs must map one-to-one to physical paths");
            }
        }
    }

    std::vector<uint32_t> aliased = mappings_for(
        RoceSrc::NMRC_EV_RANDOM32, 8, 811);
    std::set<uint32_t> unique_paths(aliased.begin(), aliased.end());
    expect(unique_paths.size() < aliased.size(),
           "random 32-EV mode must permit physical aliases");
}

static void test_per_qp_determinism_and_dephasing() {
    RoceSrc::setNmrcEvSeed(0x12345678);

    std::vector<uint32_t> small_a = values_for(
        RoceSrc::NMRC_EV_ENCODED, 8, 901);
    std::vector<uint32_t> small_replay = values_for(
        RoceSrc::NMRC_EV_ENCODED, 8, 901);
    std::vector<uint32_t> small_b = values_for(
        RoceSrc::NMRC_EV_ENCODED, 8, 902);
    expect(small_a == small_replay,
           "the same seed and QP must recreate the same encoded order");
    expect(small_a != small_b,
           "small-topology encoded QPs must use different permutations");
    expect(std::set<uint32_t>(small_a.begin(), small_a.end()) ==
               std::set<uint32_t>(small_b.begin(), small_b.end()),
           "small-topology encoded QPs must retain the same path members");

    std::vector<uint32_t> large_a = values_for(
        RoceSrc::NMRC_EV_ENCODED, 64, 903);
    std::vector<uint32_t> large_b = values_for(
        RoceSrc::NMRC_EV_ENCODED, 64, 904);
    expect(std::set<uint32_t>(large_a.begin(), large_a.end()) !=
               std::set<uint32_t>(large_b.begin(), large_b.end()),
           "large-topology encoded QPs must sample independent subsets");

    std::vector<uint32_t> random_a = values_for(
        RoceSrc::NMRC_EV_RANDOM_MATCHED, 8, 905);
    std::vector<uint32_t> random_replay = values_for(
        RoceSrc::NMRC_EV_RANDOM_MATCHED, 8, 905);
    std::vector<uint32_t> random_b = values_for(
        RoceSrc::NMRC_EV_RANDOM_MATCHED, 8, 906);
    expect(random_a == random_replay,
           "the same seed and QP must recreate the same random EV set");
    expect(random_a != random_b,
           "random EV sets must be independent across QPs");

    std::vector<uint32_t> matched_64 = values_for(
        RoceSrc::NMRC_EV_RANDOM_MATCHED, 64, 907);
    std::vector<uint32_t> random32_64 = values_for(
        RoceSrc::NMRC_EV_RANDOM32, 64, 907);
    expect(matched_64 == random32_64,
           "random-matched and random32 must be equivalent when P >= 32");
}

static void test_ev_initialization_does_not_consume_global_random() {
    RoceSrc* src = make_src(1001);
    RoceSrc::setNmrcEvMode(RoceSrc::NMRC_EV_RANDOM32);
    RoceSrc::setNmrcEvSeed(0xabcdef01);

    srandom(417);
    long expected_before = random();
    long expected_after = random();

    srandom(417);
    long actual_before = random();
    src->init_nmrc_evs_for_test(8);
    long actual_after = random();

    expect(actual_before == expected_before && actual_after == expected_after,
           "hybrid n-MRC EV initialization must not consume global random state");
}

static void test_one_round_cooling_and_idempotence() {
    RoceSrc::setNmrcEvMode(RoceSrc::NMRC_EV_ENCODED);
    RoceSrc::setNmrcEvSeed(0x1234);
    RoceSrc* src = make_src(1101);
    src->init_nmrc_evs_for_test(4);

    uint32_t bad_ev = src->choose_nmrc_ev_for_test(4);
    expect(src->nmrc_selection_ordinal_for_test() == 1,
           "a selected EV must advance the selection ordinal");
    expect(src->notify_nmrc_ev_for_test(bad_ev),
           "the first path notification must start cooldown");
    uint64_t expiry = src->nmrc_ev_cool_until_for_test(bad_ev);
    expect(expiry == 5,
           "cooldown must expire after exactly one four-EV selection round");

    for (uint32_t i = 0; i < 2; i++)
        expect(src->choose_nmrc_ev_for_test(4) != bad_ev,
               "a cooling EV must be skipped");
    expect(!src->notify_nmrc_ev_for_test(bad_ev),
           "an in-flight duplicate notification must be idempotent");
    expect(src->nmrc_ev_cool_until_for_test(bad_ev) == expiry,
           "a duplicate notification must not extend cooldown");
    for (uint32_t i = 2; i < 4; i++)
        expect(src->choose_nmrc_ev_for_test(4) != bad_ev,
               "the EV must stay absent for the full cooldown round");

    expect(!src->nmrc_ev_cooling_for_test(bad_ev),
           "the EV must become eligible after one full round");
    expect(src->notify_nmrc_ev_for_test(bad_ev),
           "a notification after recovery must start a fresh cooldown");
    expect(src->nmrc_ev_cool_until_for_test(bad_ev) == 9,
           "a post-recovery notification must use the current ordinal");
}

static void test_all_cooling_uses_earliest_expiry_fallback() {
    RoceSrc::setNmrcEvMode(RoceSrc::NMRC_EV_ENCODED);
    RoceSrc* src = make_src(1201);
    src->init_nmrc_evs_for_test(4);
    std::vector<uint32_t> values = src->nmrc_ev_values_for_test();
    for (uint32_t i = 0; i < values.size(); i++)
        expect(src->notify_nmrc_ev_for_test(values[i]),
               "each active EV should enter cooldown once");

    uint32_t selected = src->choose_nmrc_ev_for_test(4);
    expect(std::find(values.begin(), values.end(), selected) != values.end(),
           "all-cooling fallback must keep the QP live");
    expect(src->nmrc_all_cooling_fallbacks_for_test() == 1,
           "all-cooling fallback must have a dedicated diagnostic counter");
}

static void test_data_packet_carries_exact_ev_and_retransmission_reselects_it() {
    DataCaptureSink capture;
    Route route;
    route.push_back(&capture);

    RoceSrc::setLoadBalancing(RoceSrc::LB_NMRC);
    RoceSrc::setPathEntropySize(8);
    RoceSrc::setNmrcEvMode(RoceSrc::NMRC_EV_RANDOM_MATCHED);
    RoceSrc::setCongestionControl(RoceSrc::CC_NONE);
    RoceSrc::setTransportSemantics(RoceSrc::TRANSPORT_LEGACY);
    RoceSrc::setReceiveMode(RoceSrc::RX_SP_RETX_QUEUE);
    RoceSrc* src = make_src(1301);
    src->_route = &route;
    src->_flow_started = true;

    expect(src->send_packet(),
           "hybrid n-MRC source should send a data packet");
    expect(capture.pathids.size() == 1 && capture.evs.size() == 1,
           "hybrid n-MRC packet should reach the capture route");
    expect(capture.pathids[0] == capture.evs[0],
           "packet path ID and MRC metadata must carry the same logical EV");
    expect(capture.evs[0] <= 65535,
           "packet metadata must carry a 16-bit logical EV");

    src->_rtx_queue.insert(capture.seqs[0]);
    expect(src->send_packet(),
           "hybrid n-MRC source should send the queued retransmission");
    expect(capture.seqs.size() == 2 && capture.seqs[1] == capture.seqs[0],
           "retransmission must preserve the packet sequence number");
    expect(capture.evs[1] != capture.evs[0],
           "n-MRC retransmission must select the next eligible EV");
}

static RoceFastCnp* make_fast_cnp(PacketFlow& flow, Route& route,
                                  uint32_t source_host, uint32_t ev,
                                  RocePacket::seq_t psn) {
    return RoceFastCnp::newpkt(
        flow, route, source_host, ev, psn,
        7, 3, STOR_LEVEL_BAD, STOR_LEVEL_GOOD,
        41, timeFromUs(9.0));
}

static void test_fast_cnp_metadata_priority_and_recycling() {
    DataCaptureSink sink;
    Route route;
    route.push_back(&sink);
    PacketFlow flow(NULL);
    flow.set_flowid(1401);

    RoceFastCnp* first = make_fast_cnp(flow, route, 17, 0xabcd, 9001);
    expect(first->type() == ROCEFASTCNP,
           "path notification needs its own RoCE FastCNP packet type");
    expect(first->size() == RocePacket::ACKSIZE,
           "path FastCNP must reuse the 64-byte RoCE control size");
    expect(first->priority() == Packet::PRIO_HI,
           "path FastCNP must use the high-priority control queue");
    expect(first->source_host() == 17 && first->ev() == 0xabcd &&
               first->psn() == 9001,
           "path FastCNP must identify source host, EV, and PSN");
    expect(first->original_egress() == 7 && first->selected_egress() == 3,
           "path FastCNP must carry original and selected egresses");
    expect(first->original_level() == STOR_LEVEL_BAD &&
               first->selected_level() == STOR_LEVEL_GOOD,
           "path FastCNP must carry the strict grade improvement");
    expect(first->trigger_switch() == 41 &&
               first->trigger_time() == timeFromUs(9.0),
           "path FastCNP must carry trigger switch and timestamp");
    first->free();

    RoceFastCnp* recycled = RoceFastCnp::newpkt(
        flow, route, 29, 0x1234, 10003,
        5, 6, STOR_LEVEL_AVOID, STOR_LEVEL_DEGRADED,
        77, timeFromUs(13.0));
    expect(recycled->source_host() == 29 && recycled->ev() == 0x1234 &&
               recycled->psn() == 10003 &&
               recycled->original_egress() == 5 &&
               recycled->selected_egress() == 6 &&
               recycled->original_level() == STOR_LEVEL_AVOID &&
               recycled->selected_level() == STOR_LEVEL_DEGRADED &&
               recycled->trigger_switch() == 77 &&
               recycled->trigger_time() == timeFromUs(13.0),
           "recycled FastCNP packets must reset every metadata field");
    recycled->free();
}

static void test_fast_cnp_only_cools_ev_without_transport_or_cc_changes() {
    DataCaptureSink sink;
    Route route;
    route.push_back(&sink);

    RoceSrc::setLoadBalancing(RoceSrc::LB_NMRC);
    RoceSrc::setPathEntropySize(8);
    RoceSrc::setNmrcEvMode(RoceSrc::NMRC_EV_ENCODED);
    RoceSrc::setCongestionControl(RoceSrc::CC_DCQCN_VARIANT);
    RoceSrc* src = make_src(1501);
    src->_flow_started = true;
    src->init_nmrc_evs_for_test(8);
    uint32_t ev = src->nmrc_ev_values_for_test()[2];

    uint32_t acks = src->_acks_received;
    uint32_t nacks = src->_nacks_received;
    uint64_t last_acked = src->_last_acked;
    uint64_t highest_sent = src->_highest_sent;
    simtime_picosec rtt = src->_rtt;
    simtime_picosec rto = src->_rto;
    simtime_picosec rtx_timeout = src->_rtx_timeout;
    size_t rtx_size = src->_rtx_queue.size();
    double cwnd = src->_cc_cwnd_pkts;
    double inflate = src->_cc_inflate_pkts;
    double alpha = src->_dcqcn_alpha;
    double current_rate = src->_dcqcn_current_rate;
    double target_rate = src->_dcqcn_target_rate;
    simtime_picosec last_cnp = src->_dcqcn_last_cnp;

    RoceFastCnp* fast = make_fast_cnp(
        src->_flow, route, 17, ev, 11001);
    src->receivePacket(*fast);

    expect(src->nmrc_ev_cooling_for_test(ev),
           "a valid path FastCNP must cool its named EV");
    expect(src->_nmrc_fastcnp_arrived == 1 &&
               src->_nmrc_cooldown_starts == 1,
           "a valid path FastCNP must update only path-notification counters");
    expect(src->_acks_received == acks && src->_nacks_received == nacks &&
               src->_last_acked == last_acked &&
               src->_highest_sent == highest_sent &&
               src->_rtt == rtt && src->_rto == rto &&
               src->_rtx_timeout == rtx_timeout &&
               src->_rtx_queue.size() == rtx_size,
           "path FastCNP must not acknowledge, time, or retransmit data");
    expect(src->_cc_cwnd_pkts == cwnd && src->_cc_inflate_pkts == inflate &&
               src->_dcqcn_alpha == alpha &&
               src->_dcqcn_current_rate == current_rate &&
               src->_dcqcn_target_rate == target_rate &&
               src->_dcqcn_last_cnp == last_cnp,
           "path FastCNP must not enter DCQCN or alter the send window");

    uint64_t expiry = src->nmrc_ev_cool_until_for_test(ev);
    RoceFastCnp* duplicate = make_fast_cnp(
        src->_flow, route, 17, ev, 11002);
    src->receivePacket(*duplicate);
    expect(src->nmrc_ev_cool_until_for_test(ev) == expiry &&
               src->_nmrc_duplicate_notifications == 1,
           "an in-flight FastCNP duplicate must not extend cooldown");

    RoceFastCnp* unknown_ev = make_fast_cnp(
        src->_flow, route, 17, 0xffff, 11003);
    src->receivePacket(*unknown_ev);
    expect(src->_nmrc_fastcnp_unknown_ev == 1,
           "an unknown EV FastCNP must be counted and ignored");

    PacketFlow other_flow(NULL);
    other_flow.set_flowid(99991);
    RoceFastCnp* unknown_qp = make_fast_cnp(
        other_flow, route, 17, ev, 11004);
    src->receivePacket(*unknown_qp);
    expect(src->_nmrc_fastcnp_unknown_qp == 1 &&
               src->nmrc_ev_cool_until_for_test(ev) == expiry,
           "an unknown QP FastCNP must not alter this source's EV state");

    uint64_t arrived_before_done = src->_nmrc_fastcnp_arrived;
    uint64_t cooldowns_before_done = src->_nmrc_cooldown_starts;
    src->_done = true;
    RoceFastCnp* after_done = make_fast_cnp(
        src->_flow, route, 17, ev, 11005);
    src->receivePacket(*after_done);
    expect(src->_nmrc_fastcnp_arrived == arrived_before_done + 1 &&
               src->_nmrc_fastcnp_after_done == 1,
           "a physically arrived FastCNP must be counted after QP completion");
    expect(src->_nmrc_cooldown_starts == cooldowns_before_done,
           "a post-completion FastCNP must not restart EV cooling");
}

static RoceNack* make_trim_nack(PacketFlow& flow, Route& route,
                                uint32_t ev, bool detour,
                                uint32_t actual_egress) {
    RoceNack* nack = RoceNack::newpkt(flow, route, 0);
    nack->set_reason(RoceNack::TRIM);
    nack->set_missing_psn(1);
    nack->set_attempt_id(0);
    nack->set_mrc_ev(ev);
    nack->set_nmrc_detour(detour);
    nack->set_nmrc_actual_egress(actual_egress);
    return nack;
}

static void test_trim_path_metadata_resets_on_packet_reuse() {
    DataCaptureSink sink;
    Route route;
    route.push_back(&sink);
    PacketFlow flow(NULL);
    flow.set_flowid(1601);

    RocePacket* data = RocePacket::newpkt(flow, route, 1, 4096, false, false);
    data->set_nmrc_detour(true);
    data->set_nmrc_actual_egress(3);
    expect(data->nmrc_detour() && data->nmrc_actual_egress() == 3,
           "data packets must expose the actual n-MRC detour path");
    data->free();

    RocePacket* recycled_data =
        RocePacket::newpkt(flow, route, 4097, 4096, false, false);
    expect(!recycled_data->nmrc_detour() &&
               !recycled_data->has_nmrc_actual_egress(),
           "recycled data packets must clear n-MRC detour metadata");
    recycled_data->free();

    RoceNack* nack = make_trim_nack(flow, route, 2, true, 3);
    expect(nack->nmrc_detour() && nack->nmrc_actual_egress() == 3,
           "TRIM NACKs must carry the data packet's actual detour path");
    nack->free();

    RoceNack* recycled_nack = RoceNack::newpkt(flow, route, 0);
    expect(!recycled_nack->nmrc_detour() &&
               !recycled_nack->has_nmrc_actual_egress(),
           "recycled NACKs must clear n-MRC detour metadata");
    recycled_nack->free();
}

static void test_actual_path_trim_is_default_nmrc_feedback() {
    DataCaptureSink sink;
    Route route;
    route.push_back(&sink);

    RoceSrc::setLoadBalancing(RoceSrc::LB_NMRC);
    RoceSrc::setNmrcEvMode(RoceSrc::NMRC_EV_ENCODED);
    RoceSrc* src = make_src(1701);
    src->init_nmrc_evs_for_test(8);
    std::vector<uint32_t> evs = src->nmrc_ev_values_for_test();
    std::vector<uint32_t> paths = src->nmrc_physical_paths_for_test();

    RoceNack* direct =
        make_trim_nack(src->_flow, route, evs[1], false, paths[1]);
    src->process_nmrc_trim_feedback(*direct, true);
    expect(src->nmrc_ev_cooling_for_test(evs[1]) &&
               src->_nmrc_trim_nominal_cooldown_starts == 1,
           "direct TRIM must cool its nominal EV by default");
    direct->free();

    RoceNack* detour =
        make_trim_nack(src->_flow, route, evs[2], true, paths[3]);
    src->process_nmrc_trim_feedback(*detour, true);
    expect(!src->nmrc_ev_cooling_for_test(evs[2]) &&
               src->nmrc_ev_cooling_for_test(evs[3]) &&
               src->_nmrc_trim_actual_cooldown_starts == 1,
           "detour TRIM must cool the EV mapped to actual egress by default");
    detour->free();

    RoceNack* rejected =
        make_trim_nack(src->_flow, route, evs[4], false, paths[4]);
    src->process_nmrc_trim_feedback(*rejected, false);
    expect(!src->nmrc_ev_cooling_for_test(evs[4]) &&
               src->_nmrc_trim_duplicate_stale_ignored == 1,
           "a rejected duplicate or stale TRIM must not start cooldown");
    rejected->free();
}

static void test_actual_path_trim_requires_unique_mapping() {
    DataCaptureSink sink;
    Route route;
    route.push_back(&sink);

    RoceSrc::setLoadBalancing(RoceSrc::LB_NMRC);
    RoceSrc::setNmrcEvMode(RoceSrc::NMRC_EV_RANDOM32);
    RoceSrc* src = make_src(1801);
    src->init_nmrc_evs_for_test(8);
    std::vector<uint32_t> evs = src->nmrc_ev_values_for_test();
    std::vector<uint32_t> paths = src->nmrc_physical_paths_for_test();

    RoceNack* ambiguous =
        make_trim_nack(src->_flow, route, evs[0], true, paths[0]);
    src->process_nmrc_trim_feedback(*ambiguous, true);
    expect(src->_nmrc_trim_actual_unresolved == 1,
           "aliased actual paths must be counted as unresolved");
    for (uint32_t i = 0; i < evs.size(); i++)
        expect(!src->nmrc_ev_cooling_for_test(evs[i]),
               "an ambiguous actual path must not cool any EV");
    ambiguous->free();
}

int main() {
    test_set_sizes_and_uniqueness();
    test_per_qp_determinism_and_dephasing();
    test_ev_initialization_does_not_consume_global_random();
    test_one_round_cooling_and_idempotence();
    test_all_cooling_uses_earliest_expiry_fallback();
    test_data_packet_carries_exact_ev_and_retransmission_reselects_it();
    test_fast_cnp_metadata_priority_and_recycling();
    test_fast_cnp_only_cools_ev_without_transport_or_cc_changes();
    test_trim_path_metadata_resets_on_packet_reuse();
    test_actual_path_trim_is_default_nmrc_feedback();
    test_actual_path_trim_requires_unique_mapping();
    std::cout << "Hybrid n-MRC EV-state tests passed" << std::endl;
    return 0;
}
