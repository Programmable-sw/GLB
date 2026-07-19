// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include "config.h"

#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include <list>
#include <map>
#include <set>
#include <array>
#include <ostream>

#include "network.h"
#include "rocepacket.h"
#include "queue.h"
#include "eventlist.h"
#include "eth_pause_packet.h"
#include "trigger.h"

#define private public
#include "roce.h"
#undef private

using namespace std;

static const uint32_t kMss = 1000;
static const uint32_t kDst = 2;

class AdvanceEvent : public EventSource {
public:
    explicit AdvanceEvent(EventList& eventlist)
        : EventSource(eventlist, "advance_event") {}

    void doNextEvent() {}
};

struct ControlRecord {
    bool is_nack;
    bool duplicate_ack;
    bool old_duplicate_ack;
    RocePacket::seq_t ackno;
    RoceNack::nack_reason_t reason;
    bool has_mrc_ev;
    uint32_t mrc_ev;
    bool has_sack;
    RocePacket::seq_t sack_start_psn;
    uint16_t sack_valid_length;
    uint64_t sack_low;
    uint64_t sack_high;
};

class ControlCaptureSink : public PacketSink {
public:
    explicit ControlCaptureSink(RoceSrc* src)
        : _name("control_capture"), _src(src) {}

    void receivePacket(Packet& pkt) {
        ControlRecord rec;
        if (pkt.type() == ROCEACK) {
            RoceAck& ack = (RoceAck&)pkt;
            rec.is_nack = false;
            rec.duplicate_ack = ack.is_duplicate_ack();
            rec.old_duplicate_ack = ack.is_old_duplicate_ack();
            rec.ackno = ack.ackno();
            rec.reason = RoceNack::LOSS;
            rec.has_mrc_ev = ack.has_mrc_ev();
            rec.mrc_ev = ack.mrc_ev();
            rec.has_sack = false;
            rec.sack_start_psn = 0;
            rec.sack_valid_length = 0;
            rec.sack_low = 0;
            rec.sack_high = 0;
            records.push_back(rec);
            _src->processAck(ack);
        } else if (pkt.type() == ROCENACK) {
            RoceNack& nack = (RoceNack&)pkt;
            rec.is_nack = true;
            rec.duplicate_ack = false;
            rec.old_duplicate_ack = false;
            rec.ackno = nack.ackno();
            rec.reason = nack.reason();
            rec.has_mrc_ev = nack.has_mrc_ev();
            rec.mrc_ev = nack.mrc_ev();
            rec.has_sack = nack.has_sack();
            rec.sack_start_psn = nack.sack_bitmap_start_psn();
            rec.sack_valid_length = nack.sack_bitmap_valid_length();
            rec.sack_low = nack.sack_bitmap_low();
            rec.sack_high = nack.sack_bitmap_high();
            records.push_back(rec);
            _src->processNack(nack);
        } else {
            cerr << "Unexpected control packet type " << pkt.type() << endl;
            exit(2);
        }
        pkt.free();
    }

    const string& nodename() { return _name; }

    uint32_t nack_count() const {
        uint32_t count = 0;
        for (size_t i = 0; i < records.size(); i++)
            if (records[i].is_nack)
                count++;
        return count;
    }

    const ControlRecord* last_nack() const {
        for (size_t i = records.size(); i > 0; i--) {
            if (records[i - 1].is_nack)
                return &records[i - 1];
        }
        return NULL;
    }

    string _name;
    RoceSrc* _src;
    vector<ControlRecord> records;
};

class DataCaptureSink : public PacketSink {
public:
    DataCaptureSink() : _name("data_capture") {}

    void receivePacket(Packet& pkt) {
        if (pkt.type() != ROCE) {
            cerr << "Unexpected data packet type " << pkt.type() << endl;
            exit(2);
        }
        RocePacket& data = (RocePacket&)pkt;
        seqs.push_back(data.seqno());
        retransmitted.push_back(data.retransmitted());
        pkt.free();
    }

    const string& nodename() { return _name; }

    string _name;
    vector<RocePacket::seq_t> seqs;
    vector<bool> retransmitted;
};

static void expect(bool condition, const char* message) {
    if (!condition) {
        cerr << message << endl;
        exit(1);
    }
}

static uint32_t pkt_index(RocePacket::seq_t seq) {
    if (seq == 0)
        return 0;
    return (uint32_t)((seq - 1) / kMss) + 1;
}

static string format_seq_vector(const vector<RocePacket::seq_t>& seqs) {
    ostringstream out;
    out << "[";
    for (size_t i = 0; i < seqs.size(); i++) {
        if (i)
            out << ",";
        out << pkt_index(seqs[i]);
    }
    out << "]";
    return out.str();
}

static string format_seq_set(const set<RocePacket::seq_t>& seqs) {
    ostringstream out;
    out << "[";
    bool first = true;
    for (set<RocePacket::seq_t>::const_iterator it = seqs.begin();
         it != seqs.end(); ++it) {
        if (!first)
            out << ",";
        first = false;
        out << pkt_index(*it);
    }
    out << "]";
    return out.str();
}

static string format_ooo(const RoceSink& sink) {
    ostringstream out;
    out << "[";
    bool first = true;
    for (map<RocePacket::seq_t, RoceSink::OooPacketInfo>::const_iterator it =
             sink._ooo_packets.begin();
         it != sink._ooo_packets.end(); ++it) {
        if (!first)
            out << ",";
        first = false;
        out << pkt_index(it->first);
    }
    out << "]";
    return out.str();
}

static string reason_name(RoceNack::nack_reason_t reason) {
    switch (reason) {
    case RoceNack::LOSS:
        return "LOSS";
    case RoceNack::TRIM:
        return "TRIM";
    case RoceNack::OOO:
        return "OOO";
    }
    return "UNKNOWN";
}

static string format_sack(const ControlRecord* rec) {
    if (!rec || !rec->has_sack)
        return "none";

    ostringstream out;
    out << "start_pkt=" << pkt_index(rec->sack_start_psn)
        << " valid=" << rec->sack_valid_length
        << " received=[";
    bool first = true;
    for (uint32_t bit = 0; bit < rec->sack_valid_length; bit++) {
        bool set = bit < 64 ?
            (rec->sack_low & (1ULL << bit)) :
            (rec->sack_high & (1ULL << (bit - 64)));
        if (!set)
            continue;
        if (!first)
            out << ",";
        first = false;
        out << pkt_index(rec->sack_start_psn + (uint64_t)bit * kMss);
    }
    out << "]";
    return out.str();
}

static string format_last_nack(const ControlCaptureSink& control,
                               uint32_t before_nacks) {
    if (control.nack_count() == before_nacks)
        return "none";
    const ControlRecord* rec = control.last_nack();
    ostringstream out;
    out << reason_name(rec->reason)
        << " ack_pkt=" << pkt_index(rec->ackno)
        << " sack=" << format_sack(rec);
    return out.str();
}

static void emit_state(const string& scenario, const string& step,
                       const RoceSink& sink, const RoceSrc& src,
                       const ControlCaptureSink& control,
                       const DataCaptureSink& data,
                       uint32_t before_nacks, size_t before_retx) {
    vector<RocePacket::seq_t> new_retx;
    for (size_t i = before_retx; i < data.seqs.size(); i++)
        new_retx.push_back(data.seqs[i]);

    cout << "STATE scenario=" << scenario
         << " step=" << step
         << " cum_ack_pkt=" << pkt_index(sink._cumulative_ack)
         << " cum_ack_bytes=" << sink._cumulative_ack
         << " ooo_buffer=" << format_ooo(sink)
         << " ooo_nack_sent="
         << (control.nack_count() > before_nacks ? "yes" : "no")
         << " last_nack=" << format_last_nack(control, before_nacks)
         << " rtx_queue=" << format_seq_set(src._rtx_queue._seqs)
         << " retransmitted_psns=" << format_seq_vector(new_retx)
         << " src_last_acked_pkt=" << pkt_index(src._last_acked)
         << " src_highest_sent_pkt=" << pkt_index(src._highest_sent)
         << endl;
}

static void advance_to_nonzero_time(EventList& eventlist) {
    AdvanceEvent advance(eventlist);
    EventList::sourceIsPending(advance, timeFromUs((uint32_t)1));
    expect(EventList::doNextEvent(), "failed to advance EventList time");
}

static void setup_transport() {
    Packet::set_packet_size(kMss);
    RoceSrc::setTransportSemantics(RoceSrc::TRANSPORT_LEGACY);
    RoceSrc::setReceiveMode(RoceSrc::RX_SP_RETX_QUEUE);
    RoceSrc::setSackBitmapBits(64);
    RoceSrc::setNackInterval(timeFromUs((uint32_t)4));
    RoceSrc::setLoadBalancing(RoceSrc::LB_ECMP);
    RoceSrc::setCongestionControl(RoceSrc::CC_NONE);
    RoceSrc::_global_rto_count = 0;
}

static void prepare_src(RoceSrc& src) {
    src.set_src(1);
    src.set_dst(kDst);
    src.set_flowsize(5 * kMss);
    src._flow_started = true;
    src._highest_sent = 5 * kMss;
    src._last_acked = 0;
    src._state_send = RoceSrc::PAUSED;
}

static void deliver(RoceSink& sink, PacketFlow& flow, uint32_t packet_index,
                    bool trimmed = false, uint32_t path_id = 0,
                    uint32_t mrc_ev = UINT32_MAX) {
    RocePacket::seq_t seq = (packet_index - 1) * kMss + 1;
    RocePacket* pkt = RocePacket::newpkt(flow, seq, kMss, false,
                                         packet_index == 5, kDst);
    pkt->set_src(1);
    pkt->set_pathid(path_id);
    if (mrc_ev != UINT32_MAX)
        pkt->set_mrc_ev(mrc_ev);
    pkt->set_ts(EventList::now());
    if (trimmed)
        pkt->strip_payload();
    sink.receivePacket(*pkt);
}

static void run_mrc_ev_feedback() {
    EventList eventlist;
    setup_transport();
    advance_to_nonzero_time(eventlist);

    {
        RoceSrc::setReceiveMode(RoceSrc::RX_GBN);

        DataCaptureSink data;
        route_t routeout;
        routeout.push_back(&data);
        RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
        prepare_src(src);
        ControlCaptureSink control(&src);
        route_t routeback;
        routeback.push_back(&control);
        RoceSink sink;
        sink.set_src(1);
        src.connect(&routeout, &routeback, sink, TRIGGER_START);

        PacketFlow flow(NULL);
        deliver(sink, flow, 3, false, 7, 37);
        const ControlRecord* nack = control.last_nack();
        expect(nack && nack->has_mrc_ev && nack->mrc_ev == 37,
               "GBN OOO NACK should echo the triggering packet EV");
    }

    {
        RoceSrc::setReceiveMode(RoceSrc::RX_SP_RETX_QUEUE);
        RoceSrc::setOooTolerance(timeFromUs((uint32_t)5));
        RoceSrc::setOooWindowPkts(64);

        DataCaptureSink data;
        route_t routeout;
        routeout.push_back(&data);
        RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
        prepare_src(src);
        ControlCaptureSink control(&src);
        route_t routeback;
        routeback.push_back(&control);
        RoceSink sink;
        sink.set_src(1);
        src.connect(&routeout, &routeback, sink, TRIGGER_START);

        PacketFlow flow(NULL);
        deliver(sink, flow, 1, false, 1, 11);
        expect(!control.records.empty() &&
               !control.records.back().is_nack &&
               control.records.back().has_mrc_ev &&
               control.records.back().mrc_ev == 11,
               "ACK should echo the received packet EV");
        deliver(sink, flow, 3, false, 3, 33);
        deliver(sink, flow, 5, false, 5, 55);
        deliver(sink, flow, 2, false, 2, 22);

        expect(sink._cumulative_ack == 3 * kMss,
               "Partial gap closure should cumulatively ACK through packet 3");
        expect(sink._ooo_packets.size() == 1 &&
               sink._ooo_packets.begin()->first == 4 * kMss + 1,
               "Partial gap closure should leave only packet 5 buffered");

        uint32_t before_nacks = control.nack_count();
        while (control.nack_count() == before_nacks)
            expect(EventList::doNextEvent(),
                   "OOO timer did not fire after partial gap closure");
        const ControlRecord* nack = control.last_nack();
        expect(nack && nack->reason == RoceNack::OOO,
               "Partial gap closure should retain OOO recovery");
        expect(nack->has_mrc_ev && nack->mrc_ev == 55,
               "OOO timer should refresh EV metadata from the first remaining packet");
    }
}

static void run_ooo_reorder() {
    EventList eventlist;
    setup_transport();
    RoceSrc::setOooTolerance(timeFromUs((uint32_t)100));
    RoceSrc::setOooWindowPkts(32);
    advance_to_nonzero_time(eventlist);

    DataCaptureSink data;
    route_t routeout;
    routeout.push_back(&data);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    prepare_src(src);
    ControlCaptureSink control(&src);
    route_t routeback;
    routeback.push_back(&control);
    RoceSink sink;
    sink.set_src(1);
    src.connect(&routeout, &routeback, sink, TRIGGER_START);

    PacketFlow flow(NULL);
    uint32_t seq[] = {1, 3, 4, 2, 5};
    for (size_t i = 0; i < 5; i++) {
        uint32_t before_nacks = control.nack_count();
        size_t before_retx = data.seqs.size();
        deliver(sink, flow, seq[i]);
        ostringstream step;
        step << "arrive_" << seq[i];
        emit_state("ooo_reorder", step.str(), sink, src, control, data,
                   before_nacks, before_retx);
    }

    expect(sink._cumulative_ack == 5 * kMss,
           "OOO reorder should cumulatively ACK through packet 5");
    expect(sink._ooo_packets.empty(),
           "OOO reorder should drain the OOO buffer");
    expect(control.nack_count() == 0,
           "Harmless OOO reorder should not emit an OOO NACK before gap fill");
    expect(data.seqs.empty(),
           "Harmless OOO reorder should not retransmit anything");
    expect(src._highest_sent == 5 * kMss,
           "SP should not use GBN rewind during harmless OOO reorder");
    expect(src._ooo_nacks_received == 0,
           "OOO NACK counter should remain zero for harmless OOO reorder");
}

static void run_duplicate_ack_classification() {
    EventList eventlist;
    setup_transport();
    RoceSrc::setOooTolerance(timeFromUs((uint32_t)100));
    RoceSrc::setOooWindowPkts(32);
    advance_to_nonzero_time(eventlist);

    DataCaptureSink data;
    route_t routeout;
    routeout.push_back(&data);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    prepare_src(src);
    ControlCaptureSink control(&src);
    route_t routeback;
    routeback.push_back(&control);
    RoceSink sink;
    sink.set_src(1);
    src.connect(&routeout, &routeback, sink, TRIGGER_START);

    PacketFlow flow(NULL);
    uint32_t seq[] = {1, 3, 3, 2, 2};
    for (size_t i = 0; i < 5; i++)
        deliver(sink, flow, seq[i]);

    expect(control.records.size() == 5,
           "duplicate classification scenario should emit five ACKs");
    expect(!control.records[1].is_nack &&
           !control.records[1].duplicate_ack,
           "first OOO arrival must not mark its ACK duplicate");
    expect(!control.records[2].is_nack &&
           control.records[2].duplicate_ack &&
           !control.records[2].old_duplicate_ack,
           "repeated OOO arrival must be duplicate but not old");
    expect(!control.records[3].is_nack &&
           !control.records[3].duplicate_ack,
           "gap-filling arrival must carry a normal cumulative ACK");
    expect(!control.records[4].is_nack &&
           control.records[4].duplicate_ack &&
           control.records[4].old_duplicate_ack,
           "copy below cumulative ACK must be classified as old");
}

static void run_loss_gap() {
    EventList eventlist;
    setup_transport();
    RoceSrc::setOooTolerance(timeFromUs((uint32_t)5));
    RoceSrc::setOooWindowPkts(64);
    advance_to_nonzero_time(eventlist);

    DataCaptureSink data;
    route_t routeout;
    routeout.push_back(&data);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    prepare_src(src);
    ControlCaptureSink control(&src);
    route_t routeback;
    routeback.push_back(&control);
    RoceSink sink;
    sink.set_src(1);
    src.connect(&routeout, &routeback, sink, TRIGGER_START);

    PacketFlow flow(NULL);
    uint32_t seq[] = {1, 3, 4, 5};
    for (size_t i = 0; i < 4; i++) {
        uint32_t before_nacks = control.nack_count();
        size_t before_retx = data.seqs.size();
        deliver(sink, flow, seq[i]);
        ostringstream step;
        step << "arrive_" << seq[i];
        emit_state("loss_gap", step.str(), sink, src, control, data,
                   before_nacks, before_retx);
    }

    uint32_t before_nacks = control.nack_count();
    size_t before_retx = data.seqs.size();
    while (control.nack_count() == before_nacks)
        expect(EventList::doNextEvent(), "OOO timer did not fire");
    emit_state("loss_gap", "ooo_timer", sink, src, control, data,
               before_nacks, before_retx);

    const ControlRecord* nack = control.last_nack();
    expect(nack && nack->reason == RoceNack::OOO,
           "Loss/gap recovery should emit an OOO NACK");
    expect(nack->has_sack, "Loss/gap OOO NACK should carry SACK state");
    expect(nack->sack_valid_length == 4,
           "Loss/gap SACK should cover packets 2 through 5");
    expect((nack->sack_low & (1ULL << 1)) &&
           (nack->sack_low & (1ULL << 2)) &&
           (nack->sack_low & (1ULL << 3)),
           "Loss/gap SACK should mark packets 3, 4, and 5 received");
    expect(src._rtx_queue._seqs.size() == 1 &&
           *src._rtx_queue._seqs.begin() == 1001,
           "Loss/gap should queue only packet 2 for retransmission");
    expect(src._highest_sent == 5 * kMss,
           "Loss/gap SP processing should not rewind highest_sent");
    expect(src._ooo_nacks_received == 1,
           "OOO NACK counter should count the loss/gap recovery NACK");

    before_nacks = control.nack_count();
    before_retx = data.seqs.size();
    expect(src.send_packet(), "Loss/gap should send one selective retransmission");
    emit_state("loss_gap", "send_retx", sink, src, control, data,
               before_nacks, before_retx);
    expect(data.seqs.size() == 1 && data.seqs[0] == 1001 &&
           data.retransmitted[0],
           "Loss/gap should retransmit packet 2 only");

    before_nacks = control.nack_count();
    before_retx = data.seqs.size();
    deliver(sink, flow, 2);
    emit_state("loss_gap", "arrive_retx_2", sink, src, control, data,
               before_nacks, before_retx);
    expect(sink._cumulative_ack == 5 * kMss,
           "Loss/gap should ACK through packet 5 after packet 2 arrives");

    route_t synthetic_route;
    PacketFlow synthetic_flow(NULL);
    RoceNack* synthetic_loss = RoceNack::newpkt(
        synthetic_flow, synthetic_route, 0, 1);
    synthetic_loss->set_reason(RoceNack::LOSS);
    src.processNack(*synthetic_loss);
    synthetic_loss->free();
    expect(src._loss_nacks_received == 1,
           "LOSS NACK counter should distinguish LOSS from OOO/TRIM");
}

static void run_trim_gap() {
    EventList eventlist;
    setup_transport();
    RoceSrc::setOooTolerance(timeFromUs((uint32_t)100));
    RoceSrc::setOooWindowPkts(32);
    advance_to_nonzero_time(eventlist);

    DataCaptureSink data;
    route_t routeout;
    routeout.push_back(&data);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    prepare_src(src);
    ControlCaptureSink control(&src);
    route_t routeback;
    routeback.push_back(&control);
    RoceSink sink;
    sink.set_src(1);
    src.connect(&routeout, &routeback, sink, TRIGGER_START);

    PacketFlow flow(NULL);
    uint32_t seq[] = {1, 3, 4};
    for (size_t i = 0; i < 3; i++) {
        uint32_t before_nacks = control.nack_count();
        size_t before_retx = data.seqs.size();
        deliver(sink, flow, seq[i]);
        ostringstream step;
        step << "arrive_" << seq[i];
        emit_state("trim_gap", step.str(), sink, src, control, data,
                   before_nacks, before_retx);
    }

    uint32_t before_nacks = control.nack_count();
    size_t before_retx = data.seqs.size();
    deliver(sink, flow, 2, true, 2, 22);
    emit_state("trim_gap", "arrive_trimmed_2", sink, src, control, data,
               before_nacks, before_retx);

    const ControlRecord* nack = control.last_nack();
    expect(nack && nack->reason == RoceNack::TRIM,
           "Trimmed packet should emit a TRIM NACK");
    expect(nack->has_mrc_ev && nack->mrc_ev == 22,
           "TRIM NACK should echo the trimmed packet EV");
    expect(nack->has_sack, "TRIM NACK should carry SACK state in SP mode");
    expect(nack->sack_valid_length == 3,
           "TRIM SACK should cover packets 2 through 4");
    expect((nack->sack_low & (1ULL << 1)) &&
           (nack->sack_low & (1ULL << 2)),
           "TRIM SACK should mark packets 3 and 4 received");
    expect(src._rtx_queue._seqs.size() == 1 &&
           *src._rtx_queue._seqs.begin() == 1001,
           "TRIM should queue only packet 2 for retransmission");
    expect(src._trim_nacks_received == 1,
           "TRIM NACK counter should count the trim recovery NACK");
    expect(src._loss_nacks_received == 0,
           "LOSS NACK counter should remain zero in trim scenario");

    before_nacks = control.nack_count();
    before_retx = data.seqs.size();
    expect(src.send_packet(), "TRIM should send one selective retransmission");
    emit_state("trim_gap", "send_retx", sink, src, control, data,
               before_nacks, before_retx);
    expect(data.seqs.size() == 1 && data.seqs[0] == 1001 &&
           data.retransmitted[0],
           "TRIM should retransmit packet 2 only");
}

static void run_trim_tail() {
    EventList eventlist;
    setup_transport();
    RoceSrc::setTrimRecoveryMode(RoceSrc::TRIM_RECOVERY_EXACT_PSN);
    advance_to_nonzero_time(eventlist);

    DataCaptureSink data;
    route_t routeout;
    routeout.push_back(&data);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    prepare_src(src);
    ControlCaptureSink control(&src);
    route_t routeback;
    routeback.push_back(&control);
    RoceSink sink;
    sink.set_src(1);
    src.connect(&routeout, &routeback, sink, TRIGGER_START);

    PacketFlow flow(NULL);
    deliver(sink, flow, 1);
    deliver(sink, flow, 2, true);
    deliver(sink, flow, 3, true);

    expect(src._rtx_queue._seqs.size() == 2 &&
           src._rtx_queue._seqs.count(1001) == 1 &&
           src._rtx_queue._seqs.count(2001) == 1,
           "Consecutive trailing TRIM NACKs should queue each exact trimmed PSN");
}

static void run_trim_tail_legacy() {
    EventList eventlist;
    setup_transport();
    RoceSrc::setTrimRecoveryMode(RoceSrc::TRIM_RECOVERY_CUMULATIVE);
    advance_to_nonzero_time(eventlist);

    DataCaptureSink data;
    route_t routeout;
    routeout.push_back(&data);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    prepare_src(src);
    ControlCaptureSink control(&src);
    route_t routeback;
    routeback.push_back(&control);
    RoceSink sink;
    sink.set_src(1);
    src.connect(&routeout, &routeback, sink, TRIGGER_START);

    PacketFlow flow(NULL);
    deliver(sink, flow, 1);
    deliver(sink, flow, 2, true);
    deliver(sink, flow, 3, true);

    expect(src._rtx_queue._seqs.size() == 1 &&
           src._rtx_queue._seqs.count(1001) == 1,
           "Legacy cumulative TRIM recovery must ignore a later exact missing PSN");
}

static void run_trim_stale() {
    EventList eventlist;
    setup_transport();
    RoceSrc::setTrimRecoveryMode(RoceSrc::TRIM_RECOVERY_EXACT_PSN);

    DataCaptureSink data;
    route_t routeout;
    routeout.push_back(&data);
    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    prepare_src(src);
    src._last_acked = kMss;

    route_t routeback;
    PacketFlow flow(NULL);
    RoceNack* trim = RoceNack::newpkt(flow, routeback, 0, 1);
    trim->set_reason(RoceNack::TRIM);
    trim->set_missing_psn(2 * kMss + 1);
    src.processNack(*trim);
    trim->free();

    expect(src._rtx_queue._seqs.size() == 1 &&
           src._rtx_queue._seqs.count(2 * kMss + 1) == 1,
           "A stale cumulative ACK must not hide a still-unacked exact TRIM PSN");
}

static void run_trim_already_sacked() {
    EventList eventlist;
    setup_transport();
    RoceSrc::setTrimRecoveryMode(RoceSrc::TRIM_RECOVERY_EXACT_PSN);

    DataCaptureSink data;
    route_t routeout;
    routeout.push_back(&data);
    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    prepare_src(src);

    route_t routeback;
    PacketFlow flow(NULL);
    RoceNack* trim = RoceNack::newpkt(
        flow, routeback, 0, 1, 0x3, 0, true, 0, 1, 2, 64);
    trim->set_reason(RoceNack::TRIM);
    trim->set_missing_psn(kMss + 1);
    src.processNack(*trim);
    trim->free();

    expect(src._rtx_queue._seqs.count(kMss + 1) == 0,
           "An exact TRIM PSN already marked received by SACK must not be requeued");
}

static void run_trim_unaligned() {
    EventList eventlist;
    setup_transport();
    RoceSrc::setTrimRecoveryMode(RoceSrc::TRIM_RECOVERY_EXACT_PSN);

    DataCaptureSink data;
    route_t routeout;
    routeout.push_back(&data);
    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    prepare_src(src);

    route_t routeback;
    PacketFlow flow(NULL);
    RoceNack* trim = RoceNack::newpkt(flow, routeback, 0, 1);
    trim->set_reason(RoceNack::TRIM);
    trim->set_missing_psn(1500);
    src.processNack(*trim);
    trim->free();

    expect(src._rtx_queue._seqs.count(1500) == 0,
           "An unaligned exact TRIM PSN must not enter the retransmission queue");
}

int main(int argc, char** argv) {
    if (argc != 2) {
        cerr << "usage: " << argv[0]
             << " duplicate|ooo|loss|trim|trim_tail|trim_tail_legacy|trim_stale|"
             << "trim_already_sacked|trim_unaligned|mrc_ev" << endl;
        return 2;
    }

    string scenario = argv[1];
    if (scenario == "duplicate")
        run_duplicate_ack_classification();
    else if (scenario == "ooo")
        run_ooo_reorder();
    else if (scenario == "loss")
        run_loss_gap();
    else if (scenario == "trim")
        run_trim_gap();
    else if (scenario == "trim_tail")
        run_trim_tail();
    else if (scenario == "trim_tail_legacy")
        run_trim_tail_legacy();
    else if (scenario == "trim_stale")
        run_trim_stale();
    else if (scenario == "trim_already_sacked")
        run_trim_already_sacked();
    else if (scenario == "trim_unaligned")
        run_trim_unaligned();
    else if (scenario == "mrc_ev")
        run_mrc_ev_feedback();
    else {
        cerr << "unknown scenario " << scenario << endl;
        return 2;
    }

    cout << "RESULT scenario=" << scenario << " pass=1" << endl;
    return 0;
}
