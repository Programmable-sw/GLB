// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <cstdlib>
#include <iostream>

#include "eventlist.h"
#include "datacenter/fat_tree_switch.h"
#include "datacenter/fat_tree_topology.h"
#include "tcppacket.h"

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

class NoopTimer : public EventSource {
public:
    explicit NoopTimer(EventList& eventlist)
        : EventSource(eventlist, "sglb_gcn_test_noop") {}
    void doNextEvent() {}
};

class DropSink : public PacketSink {
public:
    DropSink() : _name("sglb_gcn_test_drop") {}
    void receivePacket(Packet& packet) { packet.free(); }
    const std::string& nodename() { return _name; }
private:
    std::string _name;
};

int main() {
    FatTreeSwitch::SglbPathState advertised;
    advertised.valid = true;
    advertised.link_available = true;
    advertised.queue_fraction = 0.10;
    advertised.avg_busy = 0.20;
    advertised.quality = STOR_LEVEL_DEGRADED;

    FatTreeSwitch::SglbPathState unchanged = advertised;
    unchanged.version = 99;
    unchanged.last_update = timeFromUs(7.0);
    expect(!FatTreeSwitch::sglb_gcn_export_changed(
               advertised, unchanged),
           "GCN change detection must ignore local metadata");

    FatTreeSwitch::SglbPathState changed = advertised;
    changed.queue_fraction = 0.11;
    expect(FatTreeSwitch::sglb_gcn_export_changed(advertised, changed),
           "a changed exported queue value must trigger GCN advertisement");
    changed.quality = STOR_LEVEL_BAD;
    expect(FatTreeSwitch::sglb_gcn_export_changed(advertised, changed),
           "GCN change detection must include advertised quality changes");

    FatTreeSwitch::SglbGcnProducerState first;
    first.current = advertised;
    first.advertised = advertised;
    first.valid = true;
    first.dirty = false;
    FatTreeSwitch::SglbGcnProducerState second = first;
    expect(FatTreeSwitch::sglb_gcn_observe_export(
               first, changed, timeFromUs(2.0), timeFromUs(1.0)),
           "a due producer sample must be accepted");
    expect(first.dirty,
           "a changed producer sample must become dirty");
    expect(!second.dirty,
           "one destination producer must not dirty another producer");
    FatTreeSwitch::sglb_gcn_mark_advertised(first, timeFromUs(2.0));
    expect(first.version == 1 && !first.dirty && first.has_sent,
           "advertising must advance only that producer's version");
    expect(second.version == 0 && !second.has_sent,
           "producer versions and send state must remain independent");

    EventList eventlist;
    eventlist.setEndtime(timeFromUs(60.0));

    FatTreeTopology::set_tiers(2);
    FatTreeSwitch::configure_sglb_scheme_defaults(false);
    FatTreeSwitch::_strategy = FatTreeSwitch::SGLB;
    FatTreeSwitch::_paper_sglb_diag_gcn_packets = 0;
    FatTreeSwitch::_paper_sglb_diag_gcn_bytes = 0;
    FatTreeSwitch::_paper_sglb_diag_gcn_updates = 0;

    FatTreeTopology topology(
        8, speedFromMbps((uint64_t)100000), 100000,
        NULL, &eventlist, NULL, COMPOSITE_ECN_LB,
        timeFromUs(1.0), timeFromUs(1.0));
    FatTreeSwitch::initialize_sglb_real_gcn_profiles(&topology);

    NoopTimer idle_deadline(eventlist);
    eventlist.sourceIsPendingRel(idle_deadline, timeFromUs(31.0));
    while (EventList::now() < timeFromUs(31.0) &&
           eventlist.doNextEvent()) {}

    expect(FatTreeSwitch::_paper_sglb_diag_gcn_packets == 0,
           "idle real-GCN profiles must not emit periodic packets");
    expect(FatTreeSwitch::_paper_sglb_diag_gcn_bytes == 0,
           "idle real-GCN profiles must consume no control bandwidth");
    expect(FatTreeSwitch::_paper_sglb_diag_gcn_updates == 0,
           "idle real-GCN profiles must not advance producer versions");

    NoopTimer first_sample(eventlist);
    eventlist.sourceIsPendingRel(first_sample, timeFromUs(1.1));
    expect(eventlist.doNextEvent(),
           "failed to advance to the first producer sample");
    const simtime_picosec first_change_time = EventList::now();

    const uint32_t destination_leaf = 1;
    const uint32_t destination_host =
        destination_leaf * topology.radix_down(TOR_TIER);
    FatTreeSwitch* spine = dynamic_cast<FatTreeSwitch*>(
        topology.switches_up[0]);
    expect(spine != NULL, "change-trigger test needs a spine producer");
    BaseQueue* remote_queue =
        topology.queues_nup_nlp[0][destination_leaf][0];
    expect(remote_queue != NULL,
           "change-trigger test needs a remote output queue");

    DropSink sink;
    Route sink_route;
    sink_route.push_back(&sink);
    PacketFlow flow(NULL);
    TcpPacket* queued = TcpPacket::newpkt(flow, sink_route, 1, 8192);
    remote_queue->receivePacket(*queued);
    TcpPacket* probe = TcpPacket::newpkt(flow, sink_route, 2, 64);
    probe->set_dst(destination_host);
    expect(spine->getNextHop(*probe, NULL) != NULL,
           "spine lookup must observe the changed destination export");
    probe->free();

    const uint64_t receivers = topology.switches_lp.size();
    expect(FatTreeSwitch::_paper_sglb_diag_gcn_packets == receivers,
           "a first changed producer must emit one record per receiver");
    expect(FatTreeSwitch::_paper_sglb_diag_gcn_updates == 1,
           "one changed destination must advance one producer version");

    NoopTimer second_sample(eventlist);
    eventlist.sourceIsPendingRel(second_sample, timeFromUs(1.1));
    while (EventList::now() < first_change_time + timeFromUs(1.1) &&
           eventlist.doNextEvent()) {}
    TcpPacket* queued_again = TcpPacket::newpkt(flow, sink_route, 3, 16384);
    remote_queue->receivePacket(*queued_again);
    TcpPacket* second_probe = TcpPacket::newpkt(flow, sink_route, 4, 64);
    second_probe->set_dst(destination_host);
    spine->getNextHop(*second_probe, NULL);
    second_probe->free();
    expect(FatTreeSwitch::_paper_sglb_diag_gcn_packets == receivers,
           "a changed producer must respect its 15us holdoff");

    while (EventList::now() < first_change_time + timeFromUs(16.0) &&
           eventlist.doNextEvent()) {}
    expect(FatTreeSwitch::_paper_sglb_diag_gcn_packets == 2 * receivers,
           "a dirty producer must emit once at its independent deadline");
    expect(FatTreeSwitch::_paper_sglb_diag_gcn_updates == 2,
           "a deferred changed destination must advance only once");
    return 0;
}
