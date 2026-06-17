// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include "config.h"
#include <cstdlib>
#include <iostream>
#include <vector>
#include "eventlist.h"
#include "network.h"
#include "roce.h"
#include "rocepacket.h"
#include "trigger.h"

class ControlCaptureSink : public PacketSink {
public:
    ControlCaptureSink()
        : _name("control_capture"), nacks(0), last_has_sack(false),
          last_apsn(0), last_start_psn(0), last_valid_length(0),
          last_bitmap_low(0), last_bitmap_high(0) {}

    void receivePacket(Packet& pkt) {
        if (pkt.type() != ROCENACK) {
            std::cerr << "Expected ROCENACK, got " << pkt.type() << std::endl;
            std::exit(2);
        }

        RoceNack& nack = (RoceNack&)pkt;
        nacks++;
        last_has_sack = nack.has_sack();
        last_apsn = nack.apsn();
        last_start_psn = nack.sack_bitmap_start_psn();
        last_valid_length = nack.sack_bitmap_valid_length();
        last_bitmap_low = nack.sack_bitmap_low();
        last_bitmap_high = nack.sack_bitmap_high();
        pkt.free();
    }

    const string& nodename() { return _name; }

    string _name;
    uint32_t nacks;
    bool last_has_sack;
    RocePacket::seq_t last_apsn;
    RocePacket::seq_t last_start_psn;
    uint16_t last_valid_length;
    uint64_t last_bitmap_low;
    uint64_t last_bitmap_high;
};

class DataCaptureSink : public PacketSink {
public:
    DataCaptureSink() : _name("data_capture") {}

    void receivePacket(Packet& pkt) {
        if (pkt.type() != ROCE) {
            std::cerr << "Expected ROCE, got " << pkt.type() << std::endl;
            std::exit(2);
        }
        seqs.push_back(((RocePacket&)pkt).seqno());
        pkt.free();
    }

    const string& nodename() { return _name; }

    string _name;
    std::vector<RocePacket::seq_t> seqs;
};

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

static void test_default_sack_packet_uses_64bit_window(EventList& eventlist) {
    RoceSrc::setSackBitmapBits(64);
    RoceSrc::setReceiveMode(RoceSrc::RX_SP_RETX_QUEUE);
    RoceSrc::setOooWindowPkts(1);
    RoceSrc::setOooTolerance(timeFromUs((uint32_t)1000));

    ControlCaptureSink control;
    route_t routeout;
    route_t routeback;
    routeback.push_back(&control);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    RoceSink sink;
    src.set_src(1);
    src.set_dst(2);
    sink.set_src(1);
    src.connect(&routeout, &routeback, sink, TRIGGER_START);

    PacketFlow flow(NULL);
    RocePacket* ooo = RocePacket::newpkt(flow, 70001, 1000, false, false, 2);
    ooo->set_pathid(0);
    ooo->set_ts(0);
    sink.receivePacket(*ooo);

    expect(control.nacks == 1, "SP should send one SACK NACK for a large OOO gap");
    expect(control.last_has_sack, "SACK feedback should be present");
    expect(control.last_apsn == 0, "aPSN should be the cumulative ACK PSN");
    expect(control.last_start_psn == 7001, "64-bit SACK should slide the bitmap start to cover bit 63");
    expect(control.last_valid_length == 64, "64-bit SACK valid length should be capped to one 64-bit word");
    expect(control.last_bitmap_low == (1ULL << 63), "offset 70 should land on bit 63 after the block slides");
    expect(control.last_bitmap_high == 0, "64-bit SACK should not use the high bitmap word");
}

static void test_128bit_sack_packet_preserves_high_word_coverage(EventList& eventlist) {
    RoceSrc::setSackBitmapBits(128);
    RoceSrc::setReceiveMode(RoceSrc::RX_SP_RETX_QUEUE);
    RoceSrc::setOooWindowPkts(1);
    RoceSrc::setOooTolerance(timeFromUs((uint32_t)1000));

    ControlCaptureSink control;
    route_t routeout;
    route_t routeback;
    routeback.push_back(&control);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    RoceSink sink;
    src.set_src(1);
    src.set_dst(2);
    sink.set_src(1);
    src.connect(&routeout, &routeback, sink, TRIGGER_START);

    PacketFlow flow(NULL);
    RocePacket* ooo = RocePacket::newpkt(flow, 70001, 1000, false, false, 2);
    ooo->set_pathid(0);
    ooo->set_ts(0);
    sink.receivePacket(*ooo);

    expect(control.nacks == 1, "SP should send one SACK NACK for a large OOO gap");
    expect(control.last_has_sack, "SACK feedback should be present");
    expect(control.last_apsn == 0, "aPSN should be the cumulative ACK PSN");
    expect(control.last_start_psn == 1, "128-bit SACK should cover the first missing PSN directly");
    expect(control.last_valid_length == 71, "128-bit SACK valid length should cover through bit 70");
    expect(control.last_bitmap_low == 0, "low 64 bits should be empty for offset 70");
    expect(control.last_bitmap_high == (1ULL << 6), "bit 70 should be represented in the high bitmap word");
}

static void test_rxtpsn_suppresses_duplicate_sack_retransmission(EventList& eventlist) {
    RoceSrc::setSackBitmapBits(64);
    RoceSrc::setReceiveMode(RoceSrc::RX_SP_RETX_QUEUE);
    RoceSrc::setCongestionControl(RoceSrc::CC_NONE);

    DataCaptureSink data;
    ControlCaptureSink control;
    RoceSink sink;
    route_t routeout;
    route_t routeback;
    routeout.push_back(&data);
    routeback.push_back(&control);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    src.set_src(1);
    src.set_dst(2);
    sink.set_src(1);
    src.connect(&routeout, &routeback, sink, TRIGGER_START);
    src._flow_started = true;
    src._last_acked = 0;
    src._highest_sent = 130000;
    src.set_flowsize(130000);

    uint64_t bitmap_low = 1ULL << 63;
    uint64_t bitmap_high = 0;
    RoceNack* sack = RoceNack::newpkt(src._flow, routeback, 0, 1,
                                      bitmap_low, 0, true,
                                      bitmap_high, 7001, 64);

    src.processNack(*sack);
    expect(src.send_packet(), "first retransmission should be available");
    expect(data.seqs.size() == 1 && data.seqs[0] == 1,
           "first SACK should retransmit the first missing packet");

    src.processNack(*sack);
    expect(src.send_packet(), "second retransmission should be available");
    expect(data.seqs.size() == 2 && data.seqs[1] == 1001,
           "duplicate SACK should not requeue seq 1 while RxtPSN covers it");

    sack->free();
}

static void test_sack_nack_size_tracks_encoded_bitmap_width() {
    PacketFlow flow(NULL);
    route_t route;

    RoceNack* sack64 = RoceNack::newpkt(flow, route, 0, 1,
                                        1, 0, true, 0, 1, 1, 64);
    RoceNack* sack128 = RoceNack::newpkt(flow, route, 0, 1,
                                         1, 0, true, 0, 1, 1, 128);

    expect(sack64->size() == RocePacket::ACKSIZE,
           "64-bit SACK NACK should use the base control header size");
    expect(sack128->size() == RocePacket::ACKSIZE + sizeof(uint64_t),
           "128-bit SACK NACK should account for the extra high bitmap word");

    sack64->free();
    sack128->free();
}

int main() {
    EventList eventlist;
    Packet::set_packet_size(1000);
    test_default_sack_packet_uses_64bit_window(eventlist);
    test_128bit_sack_packet_preserves_high_word_coverage(eventlist);
    test_rxtpsn_suppresses_duplicate_sack_retransmission(eventlist);
    test_sack_nack_size_tracks_encoded_bitmap_width();
    return 0;
}
