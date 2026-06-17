// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <cstdlib>
#include <iostream>
#include "eventlist.h"
#include "network.h"
#include "compositequeue.h"
#include "datacenter/fat_tree_switch.h"
#include "tcppacket.h"

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

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

int main() {
    {
        EventList eventlist;
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
               "CompositeQueue should record service time for GLB utilization scoring");
    }

    expect(FatTreeSwitch::glb_quality_from_score(0.0, 10.0, 8) == 0,
           "GLB quality should keep an empty path in bucket 0");
    expect(FatTreeSwitch::glb_quality_from_score(75.0, 10.0, 8) == 7,
           "GLB quality should saturate at the last configured bucket");
    expect(FatTreeSwitch::glb_downstream_scale_for_score(0.0, 20.0, false) == 1.0,
           "GLB should use full downstream weight when the local port is empty");
    expect(FatTreeSwitch::glb_downstream_scale_for_score(20.0, 20.0, false) < 1.0,
           "GLB should damp downstream weight when local pressure is visible");
    expect(FatTreeSwitch::glb_downstream_scale_for_score(40.0, 20.0, false) <
           FatTreeSwitch::glb_downstream_scale_for_score(20.0, 20.0, false),
           "GLB downstream damping should grow with local pressure");

    FatTreeSwitch::GlbPathState state;
    state.valid = true;
    state.last_update = 1000;
    expect(FatTreeSwitch::glb_snapshot_usable(state, 1499, 500),
           "GLB GCN state should be usable before the aging interval");
    expect(!FatTreeSwitch::glb_snapshot_usable(state, 1501, 500),
           "GLB GCN state should age out after the aging interval");
    return 0;
}
