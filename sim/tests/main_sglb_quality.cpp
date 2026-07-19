// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <cstdlib>
#include <cmath>
#include <cstring>
#include <iostream>
#include <set>
#include "eventlist.h"
#include "network.h"
#include "compositequeue.h"
#include "queue.h"
#include "datacenter/fat_tree_switch.h"
#include "datacenter/fat_tree_topology.h"
#include "ecn.h"
#include "roce.h"
#include "rocepacket.h"
#include "tcppacket.h"

static void expect(bool condition, const char* message);

class DropSink : public PacketSink {
public:
    DropSink() : _name("drop_sink"), _packets(0) {}

    void receivePacket(Packet& pkt) {
        _packets++;
        pkt.free();
    }

    const string& nodename() { return _name; }

    string _name;
    uint32_t _packets;
};

class NoopTimer : public EventSource {
public:
    NoopTimer(EventList& eventlist) : EventSource(eventlist, "noop_timer") {}
    void doNextEvent() {}
};

class FastCnpCapture : public PacketSink {
public:
    FastCnpCapture() : _name("fast_cnp_capture") {}

    void receivePacket(Packet& pkt) {
        expect(pkt.type() == ROCEFASTCNP,
               "source host control route should carry FastCNP");
        RoceFastCnp& fast = (RoceFastCnp&)pkt;
        evs.push_back(fast.ev());
        latencies.push_back(EventList::now() - fast.trigger_time());
        pkt.free();
    }

    const string& nodename() { return _name; }

    string _name;
    vector<uint32_t> evs;
    vector<simtime_picosec> latencies;
};

class AckCapture : public PacketSink {
public:
    AckCapture() : _name("ack_capture"), ecn_echoes(0) {}

    void receivePacket(Packet& pkt) {
        expect(pkt.type() == ROCEACK,
               "RoCE receiver should return an ACK");
        if (pkt.flags() & ECN_ECHO)
            ecn_echoes++;
        pkt.free();
    }

    const string& nodename() { return _name; }

    string _name;
    uint32_t ecn_echoes;
};

class TestQueue : public BaseQueue {
public:
    TestQueue(linkspeed_bps bitrate, mem_b maxsize, EventList& eventlist)
        : BaseQueue(bitrate, eventlist, NULL), _queuesize(0), _maxsize(maxsize) {}

    void receivePacket(Packet& pkt) {
        _queuesize += pkt.size();
    }

    void doNextEvent() {}

    mem_b queuesize() const {
        return _queuesize;
    }

    mem_b maxsize() const {
        return _maxsize;
    }

    void set_queuesize(mem_b queuesize) {
        _queuesize = queuesize;
    }

    int utilization_history_samples() {
        return _busyend.size();
    }

private:
    mem_b _queuesize;
    mem_b _maxsize;
};

static Route* make_route(BaseQueue* queue) {
    Route* route = new Route();
    route->push_back(queue);
    return route;
}

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

static void expect_near(double actual, double expected, double tolerance,
                        const char* message) {
    if (std::fabs(actual - expected) > tolerance) {
        std::cerr << message << ": expected " << expected
                  << ", got " << actual << std::endl;
        std::exit(1);
    }
}

int main() {
    EventList eventlist;

    expect(FatTreeSwitch::_sglb_score_mode ==
               FatTreeSwitch::SGLB_SCORE_NMRC_QUANTIZED_TOPK,
           "four-level quantized top-k should be the SGLB default");
    expect(FatTreeSwitch::_sglb_nmrc_levels == 4,
           "default SGLB quantizer should use four levels");
    expect(!std::strcmp(FatTreeSwitch::sglb_score_mode_name(),
                        "nmrc_quantized_topk"),
           "default SGLB score mode should have a stable diagnostic name");
    expect(FatTreeSwitch::_sglb_gcn_update_interval == timeFromUs(5.0),
           "SGLB downstream GCN export should refresh every 5us by default");
    expect(FatTreeSwitch::_nmrc_reroute_policy ==
               FatTreeSwitch::NMRC_REROUTE_BETTER_GE3,
           "library and CLI hybrid n-MRC defaults must use better-ge3");

    expect_near(FatTreeSwitch::sglb_nmrc_queue_pressure(0.20), 0.0, 1e-12,
                "n-MRC grade pressure should start at the ECN minimum");
    expect_near(FatTreeSwitch::sglb_nmrc_queue_pressure(0.50), 0.5, 1e-12,
                "n-MRC grade pressure should interpolate across the ECN range");
    expect_near(FatTreeSwitch::sglb_nmrc_queue_pressure(0.80), 1.0, 1e-12,
                "n-MRC grade pressure should saturate at the ECN maximum");
    expect_near(FatTreeSwitch::sglb_nmrc_noisy_or(0.25, 0.50), 0.625, 1e-12,
                "n-MRC grade path score should use noisy-or hop coupling");
    expect(FatTreeSwitch::sglb_nmrc_level(0.09) == STOR_LEVEL_GOOD,
           "score below 0.10 should be GOOD");
    expect(FatTreeSwitch::sglb_nmrc_level(0.10) == STOR_LEVEL_DEGRADED,
           "score at 0.10 should be DEGRADED");
    expect(FatTreeSwitch::sglb_nmrc_level(0.40) == STOR_LEVEL_BAD,
           "score at 0.40 should be BAD");
    expect(FatTreeSwitch::sglb_nmrc_level(0.60) == STOR_LEVEL_AVOID,
           "score at 0.60 should be AVOID");
    expect(FatTreeSwitch::sglb_nmrc_quantized_level(0.09, 4) == 0,
           "four-level quantizer should preserve the GOOD boundary");
    expect(FatTreeSwitch::sglb_nmrc_quantized_level(0.10, 4) == 1,
           "four-level quantizer should enter level 1 at 0.10");
    expect(FatTreeSwitch::sglb_nmrc_quantized_level(0.40, 4) == 2,
           "four-level quantizer should enter level 2 at 0.40");
    expect(FatTreeSwitch::sglb_nmrc_quantized_level(0.60, 4) == 3,
           "four-level quantizer should enter level 3 at 0.60");
    expect(FatTreeSwitch::sglb_nmrc_quantized_level(0.049, 8) == 0,
           "eight-level quantizer should keep scores below 0.05 in level 0");
    expect(FatTreeSwitch::sglb_nmrc_quantized_level(0.05, 8) == 1,
           "eight-level quantizer should split the original GOOD level");
    expect(FatTreeSwitch::sglb_nmrc_quantized_level(0.10, 8) == 2,
           "eight-level quantizer should preserve the 0.10 boundary");
    expect(FatTreeSwitch::sglb_nmrc_quantized_level(0.25, 8) == 3,
           "eight-level quantizer should split the original DEGRADED level");
    expect(FatTreeSwitch::sglb_nmrc_quantized_level(0.40, 8) == 4,
           "eight-level quantizer should preserve the 0.40 boundary");
    expect(FatTreeSwitch::sglb_nmrc_quantized_level(0.50, 8) == 5,
           "eight-level quantizer should split the original BAD level");
    expect(FatTreeSwitch::sglb_nmrc_quantized_level(0.60, 8) == 6,
           "eight-level quantizer should preserve the 0.60 boundary");
    expect(FatTreeSwitch::sglb_nmrc_quantized_level(0.80, 8) == 7,
           "eight-level quantizer should split the original AVOID level");

    {
        for (uint32_t better = 1; better <= 3; better++) {
            vector<uint8_t> levels(4, STOR_LEVEL_BAD);
            vector<bool> available(4, true);
            for (uint32_t i = 1; i <= better; i++)
                levels[i] = i == 1 ? STOR_LEVEL_GOOD :
                    STOR_LEVEL_DEGRADED;
            FatTreeSwitch::NmrcRerouteDecision any =
                FatTreeSwitch::nmrc_select_better_path(
                    0, levels, available,
                    FatTreeSwitch::NMRC_REROUTE_ANY_BETTER,
                    3, 17);
            expect(any.reroute && any.better_count == better,
                   "any-better policy must reroute for one, two, or three strict upgrades");
            expect(any.selected_index != 0 &&
                       any.selected_level < any.original_level,
                   "hybrid selection must choose a different strict-better path");

            FatTreeSwitch::NmrcRerouteDecision ge3 =
                FatTreeSwitch::nmrc_select_better_path(
                    0, levels, available,
                    FatTreeSwitch::NMRC_REROUTE_BETTER_GE3,
                    3, 17);
            expect(ge3.reroute == (better >= 3),
                   "better-ge3 policy must require three strict-better candidates");
        }

        vector<uint8_t> same_level(4, STOR_LEVEL_DEGRADED);
        vector<bool> all_available(4, true);
        FatTreeSwitch::NmrcRerouteDecision same =
            FatTreeSwitch::nmrc_select_better_path(
                0, same_level, all_available,
                FatTreeSwitch::NMRC_REROUTE_ANY_BETTER,
                3, 0);
        expect(!same.reroute && same.better_count == 0,
               "same-level raw score differences must never trigger hybrid rerouting");

        vector<uint8_t> unavailable_levels;
        unavailable_levels.push_back(STOR_LEVEL_BAD);
        unavailable_levels.push_back(STOR_LEVEL_GOOD);
        vector<bool> unavailable;
        unavailable.push_back(true);
        unavailable.push_back(false);
        FatTreeSwitch::NmrcRerouteDecision down =
            FatTreeSwitch::nmrc_select_better_path(
                0, unavailable_levels, unavailable,
                FatTreeSwitch::NMRC_REROUTE_ANY_BETTER,
                3, 0);
        expect(!down.reroute && down.better_count == 0,
               "an unavailable strict-better path must not qualify");

        vector<uint8_t> kmin_levels(2, STOR_LEVEL_GOOD);
        vector<bool> kmin_available(2, true);
        FatTreeSwitch::NmrcRerouteDecision kmin =
            FatTreeSwitch::nmrc_select_better_path(
                0, kmin_levels, kmin_available,
                FatTreeSwitch::NMRC_REROUTE_ANY_BETTER,
                3, 1);
        expect(!kmin.reroute,
               "a path exactly at Kmin is GOOD and must not reroute to another GOOD path");
    }

    {
        TestQueue queue(
            speedFromMbps((uint64_t)10000), 65536, eventlist);
        NoopTimer timer(eventlist);
        eventlist.sourceIsPendingRel(timer, timeFromUs(1.0));
        eventlist.doNextEvent();
        queue.log_packet_send(timeFromUs(1.0));
        expect(queue.utilization_history_samples() == 1,
               "queue should retain the recorded utilization interval");

        eventlist.sourceIsPendingRel(timer, timeFromUs(31.0));
        eventlist.doNextEvent();
        expect(queue.peek_average_utilization() == 0,
               "expired trace utilization should read as zero");
        expect(queue.utilization_history_samples() == 1,
               "trace utilization must not consume live history");
        expect(queue.average_utilization() == 0,
               "live utilization should also exclude expired history");
        expect(queue.utilization_history_samples() == 0,
               "live utilization may prune expired history");
    }

    {
        CompositeQueue queue(speedFromMbps((uint64_t)10000), 65536, eventlist, NULL);
        DropSink sink;
        route_t route;
        route.push_back(&sink);

        PacketFlow flow(NULL);
        TcpPacket* pkt = TcpPacket::newpkt(flow, route, 1, 4096);
        queue.receivePacket(*pkt);
        eventlist.doNextEvent();

        expect(sink._packets == 1, "packet should leave the composite queue");
        expect(queue.average_utilization() > 0,
               "CompositeQueue should record service time for SGLB utilization scoring");
    }

    {
        FatTreeSwitch sw(eventlist, "sglb_nmrc_quantized_topk_sw",
                         FatTreeSwitch::AGG, 3, 0, NULL);
        TestQueue q0(speedFromMbps((uint64_t)10000), 65536, eventlist);
        TestQueue q1(speedFromMbps((uint64_t)10000), 65536, eventlist);
        TestQueue q2(speedFromMbps((uint64_t)10000), 65536, eventlist);
        TestQueue q3(speedFromMbps((uint64_t)10000), 65536, eventlist);
        q0.set_queuesize(0);
        q1.set_queuesize(20972);
        q2.set_queuesize(20972);
        q3.set_queuesize(20972);

        Route* r0 = make_route(&q0);
        Route* r1 = make_route(&q1);
        Route* r2 = make_route(&q2);
        Route* r3 = make_route(&q3);
        FibEntry e0(r0, 1, UP);
        FibEntry e1(r1, 1, UP);
        FibEntry e2(r2, 1, UP);
        FibEntry e3(r3, 1, UP);
        vector<FibEntry*> routes;
        routes.push_back(&e0);
        routes.push_back(&e1);
        routes.push_back(&e2);
        routes.push_back(&e3);

        FatTreeSwitch::_sglb_update_interval = 0;
        FatTreeSwitch::_sglb_score_mode =
            FatTreeSwitch::SGLB_SCORE_NMRC_QUANTIZED_TOPK;
        FatTreeSwitch::_sglb_nmrc_levels = 4;
        FatTreeSwitch::_sglb_min_choices = 3;
        FatTreeSwitch::reset_sglb_route_diag();
        for (uint32_t i = 0; i < 64; i++)
            sw.sglb_route(&routes, 79);
        expect(FatTreeSwitch::_sglb_diag_candidate_choices == 64 * 4,
               "quantized top-k should add the whole next level to reach K");

        q1.set_queuesize(0);
        q2.set_queuesize(0);
        q3.set_queuesize(0);
        FatTreeSwitch::reset_sglb_route_diag();
        for (uint32_t i = 0; i < 64; i++)
            sw.sglb_route(&routes, 80);
        expect(FatTreeSwitch::_sglb_diag_candidate_choices == 64 * 4,
               "quantized top-k should not truncate a tied best level");

        FatTreeSwitch::_sglb_score_mode = FatTreeSwitch::SGLB_SCORE_LEGACY;
    }

    {
        FatTreeSwitch sw(eventlist, "sglb_cache_sw", FatTreeSwitch::AGG, 1, 0, NULL);
        TestQueue q0(speedFromMbps((uint64_t)10000), 65536, eventlist);
        TestQueue q1(speedFromMbps((uint64_t)10000), 65536, eventlist);
        q1.set_queuesize(4000);

        Route* r0 = make_route(&q0);
        Route* r1 = make_route(&q1);
        FibEntry e0(r0, 1, UP);
        FibEntry e1(r1, 1, UP);
        vector<FibEntry*> routes;
        routes.push_back(&e0);
        routes.push_back(&e1);

        FatTreeSwitch::_sglb_update_interval = timeFromUs(1.0);
        FatTreeSwitch::_sglb_queue_weight = 1.0;
        FatTreeSwitch::_sglb_util_weight = 0.0;
        FatTreeSwitch::_sglb_downstream_weight = 0.0;
        FatTreeSwitch::_sglb_quality_bucket = 1.0;
        FatTreeSwitch::_sglb_quality_levels = 64;
        FatTreeSwitch::_sglb_max_quality = 63;
        FatTreeSwitch::_sglb_min_choices = 1;

        FatTreeSwitch::reset_sglb_route_diag();
        expect(sw.sglb_route(&routes, 42) == 0,
               "SGLB should initially prefer the empty first path");
        expect(FatTreeSwitch::_sglb_diag_route_calls == 1,
               "SGLB diagnostics should count route decisions");
        expect(FatTreeSwitch::_sglb_diag_candidate_choices == 1,
               "SGLB diagnostics should count the selected candidate set");
        expect(FatTreeSwitch::_sglb_diag_all_same_quality_calls == 0,
               "SGLB diagnostics should distinguish unequal path qualities");

        q0.set_queuesize(16000);
        expect(sw.sglb_route(&routes, 42) == 0,
               "SGLB should keep cached path quality within the local update interval");

        NoopTimer timer(eventlist);
        eventlist.sourceIsPendingRel(timer, timeFromUs(2.0));
        eventlist.doNextEvent();

        expect(sw.sglb_route(&routes, 42) == 1,
               "SGLB should refresh local path quality after the local update interval");
    }

    expect(FatTreeSwitch::sglb_quality_from_score(0.0, 10.0, 8) == 0,
           "SGLB quality should keep an empty path in bucket 0");
    expect(FatTreeSwitch::sglb_quality_from_score(75.0, 10.0, 8) == 7,
           "SGLB quality should saturate at the last configured bucket");
    expect(FatTreeSwitch::sglb_downstream_scale_for_score(0.0, 20.0, false) == 1.0,
           "SGLB should use full downstream weight when the local port is empty");
    expect(FatTreeSwitch::sglb_downstream_scale_for_score(20.0, 20.0, false) < 1.0,
           "SGLB should damp downstream weight when local pressure is visible");
    expect(FatTreeSwitch::sglb_downstream_scale_for_score(40.0, 20.0, false) <
           FatTreeSwitch::sglb_downstream_scale_for_score(20.0, 20.0, false),
           "SGLB downstream damping should grow with local pressure");

    FatTreeSwitch::SglbPathState state;
    state.valid = true;
    state.last_update = 1000;
    expect(FatTreeSwitch::sglb_snapshot_usable(state, 1499, 500),
           "SGLB GCN state should be usable before the aging interval");
    expect(!FatTreeSwitch::sglb_snapshot_usable(state, 1501, 500),
           "SGLB GCN state should age out after the aging interval");

    {
        FatTreeTopology::set_tiers(2);
        FatTreeTopology topo(
            8, speedFromMbps((uint64_t)100000), 100000,
            NULL, &eventlist, NULL, COMPOSITE_ECN_LB,
            timeFromUs(1.0), timeFromUs(1.0));
        FatTreeSwitch* source_leaf =
            dynamic_cast<FatTreeSwitch*>(topo.switches_lp[0]);
        expect(source_leaf != NULL,
               "hybrid integration test needs a source leaf");

        RoceSrc source(NULL, NULL, eventlist,
                       speedFromMbps((uint64_t)100000));
        source.set_flowid(1601);
        source.set_src(0);
        source.set_dst(2);
        RoceSink receiver;
        receiver.set_src(0);
        AckCapture ack_capture;
        Route ack_route;
        ack_route.push_back(&ack_capture);
        DropSink source_route_drop;
        Route unused_source_route;
        unused_source_route.push_back(&source_route_drop);
        source.connect(&unused_source_route, &ack_route, receiver,
                       TRIGGER_START);
        FastCnpCapture fast_capture;
        source_leaf->addHostPort(0, source.flow_id(), &fast_capture);
        dynamic_cast<FatTreeSwitch*>(
            topo.switches_lp[topo.HOST_POD_SWITCH(2)])->addHostPort(
                2, source.flow_id(), &receiver);

        FatTreeSwitch::_strategy = FatTreeSwitch::ECMP;
        FatTreeSwitch::_pathid_only_hash = true;
        FatTreeSwitch::_sglb_score_mode =
            FatTreeSwitch::SGLB_SCORE_NMRC_QUANTIZED_TOPK;
        FatTreeSwitch::_sglb_nmrc_levels = 4;
        FatTreeSwitch::_sglb_min_choices = 3;
        FatTreeSwitch::_sglb_update_interval = 0;
        FatTreeSwitch::_nmrc_reroute_policy =
            FatTreeSwitch::NMRC_REROUTE_ANY_BETTER;
        FatTreeSwitch::_nmrc_hybrid_enabled = false;

        RocePacket* probe = RocePacket::newpkt(
            source._flow, 1, Packet::data_packet_size(), false, false, 2);
        probe->set_src(0);
        probe->set_pathid(0);
        probe->set_mrc_ev(0);
        Route* original_route = source_leaf->getNextHop(*probe, NULL);
        expect(original_route != NULL && original_route->size() > 0,
               "encoded EV must resolve to a source-leaf uplink");
        BaseQueue* original_queue =
            dynamic_cast<BaseQueue*>(original_route->at(0));
        expect(original_queue != NULL,
               "source-leaf route must begin with an egress queue");
        probe->free();

        DropSink background_drop;
        Route background_route;
        background_route.push_back(&background_drop);
        PacketFlow background_flow(NULL);
        for (uint32_t i = 0; i < 15; i++) {
            TcpPacket* background = TcpPacket::newpkt(
                background_flow, background_route, i + 1, 4096);
            original_queue->receivePacket(*background);
        }
        expect(original_queue->queuesize() > 40000,
               "test must place the original EV uplink above the BAD threshold");

        PacketFlow missing_reverse_flow(NULL);
        FatTreeSwitch::_nmrc_hybrid_enabled = false;
        RocePacket* missing_reverse = RocePacket::newpkt(
            missing_reverse_flow, 1, Packet::data_packet_size(),
            false, false, 2);
        missing_reverse->set_src(0);
        missing_reverse->set_pathid(0);
        missing_reverse->set_mrc_ev(0);
        Route* missing_original = source_leaf->getNextHop(
            *missing_reverse, NULL);
        missing_reverse->free();
        missing_reverse = RocePacket::newpkt(
            missing_reverse_flow, 1, Packet::data_packet_size(),
            false, false, 2);
        missing_reverse->set_src(0);
        missing_reverse->set_pathid(0);
        missing_reverse->set_mrc_ev(0);
        FatTreeSwitch::reset_nmrc_hybrid_diag();
        FatTreeSwitch::_nmrc_hybrid_enabled = true;
        FatTreeSwitch::_nmrc_fastcnp_enabled = true;
        Route* missing_selected = source_leaf->getNextHop(
            *missing_reverse, NULL);
        expect(missing_selected == missing_original,
               "FastCNP-on must not reroute when the reverse host route is missing");
        expect(FatTreeSwitch::_nmrc_diag_fastcnp_route_missing == 1,
               "missing reverse FastCNP route must be diagnosed once");
        expect(FatTreeSwitch::_nmrc_diag_reroutes == 0,
               "an unsignalled candidate must not count as an actual reroute");
        missing_reverse->free();

        FatTreeSwitch::reset_nmrc_hybrid_diag();
        FatTreeSwitch::_nmrc_hybrid_enabled = true;
        FatTreeSwitch::_nmrc_fastcnp_enabled = true;
        const uint32_t evs[] = {0, 2};
        for (uint32_t i = 0; i < 2; i++) {
            RocePacket* data = RocePacket::newpkt(
                source._flow,
                1 + i * Packet::data_packet_size(),
                Packet::data_packet_size(), false, false, 2);
            data->set_src(0);
            data->set_pathid(evs[i]);
            data->set_mrc_ev(evs[i]);
            data->set_flags(ECN_CE);
            source_leaf->receivePacket(*data);
        }

        FatTreeSwitch::_nmrc_fastcnp_enabled = false;
        RocePacket* no_signal = RocePacket::newpkt(
            source._flow, 1 + 2 * Packet::data_packet_size(),
            Packet::data_packet_size(), false, false, 2);
        no_signal->set_src(0);
        no_signal->set_pathid(4);
        no_signal->set_mrc_ev(4);
        no_signal->set_flags(ECN_CE);
        source_leaf->receivePacket(*no_signal);

        simtime_picosec deadline = EventList::now() + timeFromUs(50.0);
        while ((fast_capture.evs.size() < 2 || ack_capture.ecn_echoes < 3) &&
               EventList::now() < deadline && eventlist.doNextEvent()) {}

        expect(FatTreeSwitch::_nmrc_diag_route_checks == 3,
               "only source-leaf data ingress may run the hybrid route check");
        expect(FatTreeSwitch::_nmrc_diag_reroutes == 3,
               "each BAD-to-GOOD test packet should actually reroute");
        expect(FatTreeSwitch::_nmrc_diag_fastcnp_generated == 2,
               "FastCNP-off must preserve rerouting without generating control traffic");
        expect(fast_capture.evs.size() == 2,
               "two different rerouted EVs must produce two real control packets");
        expect(std::set<uint32_t>(fast_capture.evs.begin(),
                                  fast_capture.evs.end()).size() == 2,
               "physical aliases must still notify each logical EV separately");
        expect(fast_capture.latencies[0] > 0 &&
                   fast_capture.latencies[1] > 0,
               "FastCNP must incur switch, queue, and link delivery latency");
        expect(ack_capture.ecn_echoes == 3,
               "hybrid rerouting must not clear CE or suppress receiver ECN echo");
    }
    return 0;
}
