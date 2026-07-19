// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <cstdlib>
#include <iostream>

#include "ecn.h"
#include "eventlist.h"
#include "queue_lossless_output.h"
#include "rocepacket.h"

class TestVirtualQueue : public VirtualQueue {
public:
    TestVirtualQueue() : completed(0) {}

    void completedService(Packet& pkt) {
        completed++;
    }

    int completed;
};

class TestSink : public PacketSink {
public:
    TestSink() : received(0), saw_ecn(false), _nodename("test_sink") {}

    void receivePacket(Packet& pkt) {
        received++;
        saw_ecn = (pkt.flags() & ECN_CE) != 0;
        pkt.free();
    }

    const string& nodename() { return _nodename; }

    int received;
    bool saw_ecn;
    string _nodename;
};

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

int main() {
    EventList eventlist;
    PacketFlow flow(NULL);
    Route route;
    TestSink sink;
    route.push_back(&sink);

    LosslessOutputQueue q(speedFromMbps((uint64_t)100000), 10000,
                          eventlist, NULL, 1, 1, 1);
    TestVirtualQueue vq;

    RocePacket* pkt = RocePacket::newpkt(flow, route, 1, 1000, false, true);
    q.receivePacket(*pkt, &vq);
    q.completeService();

    expect(vq.completed == 1, "virtual queue should be notified");
    expect(sink.received == 1, "packet should leave the queue");
    expect(sink.saw_ecn, "lossless output queue should mark ECN above threshold");
    expect(q.ecn_mark_count() == 1,
           "lossless output queue should count ECN marks");

    return 0;
}
