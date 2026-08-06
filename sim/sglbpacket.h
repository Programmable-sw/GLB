// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#ifndef SGLB_PACKET_H
#define SGLB_PACKET_H

#include "network.h"

struct SglbGcnRecord {
    uint32_t destination_switch_id;
    uint32_t port_id;
    bool link_up;
    double remote_queue;
    double remote_utilization;
    double remote_busyness;
    uint8_t port_quality;

    SglbGcnRecord(
        uint32_t destination = 0, uint32_t port = 0, bool up = true,
        double queue = 0.0, double utilization = 0.0,
        double busyness = 0.0, uint8_t quality = 0)
        : destination_switch_id(destination), port_id(port), link_up(up),
          remote_queue(queue), remote_utilization(utilization),
          remote_busyness(busyness), port_quality(quality) {}
};

class SglbGcnPacket : public Packet {
public:
    static const uint16_t PACKET_SIZE = 256;

    static SglbGcnPacket* newpkt(
        PacketFlow& flow, const Route& route, uint32_t sender_switch_id,
        const std::vector<SglbGcnRecord>& records, uint64_t version,
        simtime_picosec generated_at, uint16_t modeled_size = PACKET_SIZE) {
        SglbGcnPacket* packet = _packetdb.allocPacket();
        packet->set_route(flow, route, modeled_size,
                          static_cast<packetid_t>(version));
        packet->_type = SGLB_GCN;
        packet->Packet::strip_payload();
        packet->_sender_switch_id = sender_switch_id;
        packet->_records = records;
        packet->_version = version;
        packet->_generated_at = generated_at;
        return packet;
    }

    void free() { _packetdb.freePacket(this); }
    PktPriority priority() const { return Packet::PRIO_HI; }

    uint32_t sender_switch_id() const { return _sender_switch_id; }
    size_t record_count() const { return _records.size(); }
    const SglbGcnRecord& record(size_t index) const {
        return _records.at(index);
    }
    uint64_t version() const { return _version; }
    simtime_picosec generated_at() const { return _generated_at; }

private:
    static PacketDB<SglbGcnPacket> _packetdb;
    uint32_t _sender_switch_id;
    std::vector<SglbGcnRecord> _records;
    uint64_t _version;
    simtime_picosec _generated_at;
};

#endif

