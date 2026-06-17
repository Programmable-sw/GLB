// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include "config.h"
#include <cstdlib>
#include <iostream>
#include "eventlist.h"
#include "network.h"
#include "roce.h"
#include "rocepacket.h"
#include "trigger.h"

class CaptureSink : public PacketSink {
public:
    CaptureSink() : _name("capture"), acks(0), nacks(0), last_ackno(0) {}

    void receivePacket(Packet& pkt) {
        if (pkt.type() == ROCEACK) {
            acks++;
            last_ackno = ((RoceAck&)pkt).ackno();
        } else if (pkt.type() == ROCENACK) {
            nacks++;
            last_ackno = ((RoceNack&)pkt).ackno();
        } else {
            std::cerr << "Unexpected packet type " << pkt.type() << std::endl;
            std::exit(2);
        }
        pkt.free();
    }

    const string& nodename() { return _name; }

    string _name;
    uint32_t acks;
    uint32_t nacks;
    RocePacket::seq_t last_ackno;
};

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

int main() {
    Packet::set_packet_size(1000);
    EventList eventlist;
    RoceSrc::setReceiveMode(RoceSrc::RX_GBN);

    CaptureSink capture;
    route_t routeout;
    route_t routeback;
    routeback.push_back(&capture);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    RoceSink sink;
    src.set_src(1);
    src.set_dst(2);
    sink.set_src(1);
    src.connect(&routeout, &routeback, sink, TRIGGER_START);

    PacketFlow flow(NULL);
    RocePacket* second = RocePacket::newpkt(flow, 1001, 1000, false, false, 2);
    second->set_pathid(0);
    second->set_ts(0);
    sink.receivePacket(*second);

    expect(capture.nacks == 1, "GBN should NACK the first out-of-order packet");
    expect(capture.acks == 0, "GBN should not ACK a dropped out-of-order packet");
    expect(sink.cumulative_ack() == 0, "GBN should not advance cumulative ACK on out-of-order packet");

    RocePacket* first = RocePacket::newpkt(flow, 1, 1000, false, false, 2);
    first->set_pathid(0);
    first->set_ts(0);
    sink.receivePacket(*first);

    expect(sink.cumulative_ack() == 1000,
           "GBN should not retain the previously dropped out-of-order packet");
    expect(capture.acks == 1, "GBN should ACK the first in-order packet");
    return 0;
}
