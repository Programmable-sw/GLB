// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <cstdlib>
#include <iostream>
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

static RoceAck* make_ack(PacketFlow& flow, Route& route,
                         RocePacket::seq_t ackno, uint32_t pathid,
                         uint32_t flags = 0) {
    RoceAck* ack = RoceAck::newpkt(flow, route, ackno, 7);
    ack->set_pathid(pathid);
    ack->set_flags(flags);
    return ack;
}

static void test_reps_clean_ack_recycling() {
    DropSink drop;
    Route route;
    route.push_back(&drop);

    RoceSrc::setLoadBalancing(RoceSrc::LB_REPS);
    RoceSrc::setPathEntropySize(16);
    RoceSrc::setRepsBufferSize(4);
    RoceSrc::setRepsWarmupPkts(0);

    RoceSrc src(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
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

static void test_rr_and_healthy_mrc_share_the_64_path_rotation() {
    RoceSrc::setPathEntropySize(64);
    RoceSrc rr(NULL, NULL, test_eventlist(),
               speedFromMbps((uint64_t)100000));
    RoceSrc mrc(NULL, NULL, test_eventlist(),
                speedFromMbps((uint64_t)100000));
    rr.set_src(7);
    rr.set_dst(199);
    rr.set_flowid(1701);
    rr._flow_started = true;
    mrc.set_src(7);
    mrc.set_dst(199);
    mrc.set_flowid(1701);
    mrc._flow_started = true;

    for (uint32_t i = 0; i < 64; ++i) {
        const uint32_t rr_path = rr.choose_rr_path(64);
        const RoceSrc::MrcChoice mrc_choice = mrc.choose_mrc_ev(64);
        expect(rr_path == mrc_choice.physical_path,
               "healthy MRC and RR should traverse the same shuffled paths");
    }
}

int main() {
    Packet::set_packet_size(1000);
    test_reps_clean_ack_recycling();
    test_rr_and_healthy_mrc_share_the_64_path_rotation();
    return 0;
}
