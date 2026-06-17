// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#ifndef ROCEPACKET_H
#define ROCEPACKET_H

#include <list>
#include <vector>
#include "network.h"

typedef std::vector<uint8_t> NmrcBitmap;

// NdpPacket and NdpAck are subclasses of Packet.
// They incorporate a packet database, to reuse packet objects that are no longer needed.
// Note: you never construct a new NdpPacket or NdpAck directly; 
// rather you use the static method newpkt() which knows to reuse old packets from the database.

#define VALUE_NOT_SET -1
//#define PULL_MAXPATHS 256 // maximum path ID that can be pulled

class RocePacket : public Packet {
 public:
    typedef uint64_t seq_t;

    // pseudo-constructor for a routeless packet - routing information
    // must be filled in later
    inline static RocePacket* newpkt(PacketFlow &flow, 
                                    seq_t seqno, int size, 
                                    bool retransmitted, 
                                    bool last_packet,
                                    uint32_t destination = UINT32_MAX) {
                RocePacket* p = _packetdb.allocPacket();
                p->set_attrs(flow, size+ACKSIZE, seqno+size-1); // The NDP sequence number is the first byte of the packet; I will ID the packet by its last byte.
                p->_type = ROCE;
                p->_is_header = false;
                p->_seqno = seqno;
                p->_retransmitted = retransmitted;
                p->_last_packet = last_packet;
                p->_path_len = 0;
                p->_direction = NONE;
                p->_srcaddr = UINT32_MAX;
                p->_has_nmrc_feedback = false;
                p->_nmrc_bitmap.clear();
                p->set_dst(destination);
                return p;
    }
  
    inline static RocePacket* newpkt(PacketFlow &flow, const Route &route, 
                                    seq_t seqno, int size, 
                                    bool retransmitted,
                                    bool last_packet,
                                    uint32_t destination = UINT32_MAX) {
                RocePacket* p = _packetdb.allocPacket();
                p->set_route(flow,route,size+ACKSIZE,seqno+size-1); // The NDP sequence number is the first byte of the packet; I will ID the packet by its last byte.
                p->_type = ROCE;
                p->_seqno = seqno;
                p->_is_header = false;
                p->_direction = NONE;        
                p->_retransmitted = retransmitted;
                p->_last_packet = last_packet;
                p->_path_len = route.size();
                p->_srcaddr = UINT32_MAX;
                p->_has_nmrc_feedback = false;
                p->_nmrc_bitmap.clear();
                p->set_dst(destination);
                return p;
    }
  
    void free() {_packetdb.freePacket(this);}
    virtual ~RocePacket(){}
    virtual inline void strip_payload() {
        Packet::strip_payload();
        _size = ACKSIZE;
    }
    
        inline seq_t seqno() const {return _seqno;}
    inline bool retransmitted() const {return _retransmitted;}
    inline bool last_packet() const {return _last_packet;}
    inline simtime_picosec ts() const {return _ts;}
    inline void set_ts(simtime_picosec ts) {_ts = ts;}
    inline uint32_t src() const {return _srcaddr;}
    inline void set_src(uint32_t src) {_srcaddr = src;}
    inline uint32_t path_id() const {if (_pathid!=UINT32_MAX) return _pathid; else return _route->path_id();}
    inline void set_nmrc_feedback(const NmrcBitmap& bitmap) {
        _nmrc_bitmap = bitmap;
        _has_nmrc_feedback = true;
    }
    inline bool has_nmrc_feedback() const {return _has_nmrc_feedback;}
    inline const NmrcBitmap& nmrc_bitmap() const {return _nmrc_bitmap;}
    virtual PktPriority priority() const {return Packet::PRIO_LO;}
    const static int ACKSIZE=64;
 protected:
    seq_t _seqno;
    simtime_picosec _ts;
    bool _retransmitted;
    bool _last_packet;  // set to true in the last packet in a flow.
    uint32_t _srcaddr;
    bool _has_nmrc_feedback;
    NmrcBitmap _nmrc_bitmap;
    static PacketDB<RocePacket> _packetdb;
};

class RoceAck : public Packet {
 public:
    typedef RocePacket::seq_t seq_t;
  
    inline static RoceAck* newpkt(PacketFlow &flow, const Route &route, 
                                 seq_t ackno,
                                 uint32_t destination = UINT32_MAX) {
                RoceAck* p = _packetdb.allocPacket();
                p->set_route(flow,route,RocePacket::ACKSIZE,ackno);
                p->_type = ROCEACK;
                p->_is_header = true;
                p->_ackno = ackno;
                p->_path_len = 0;
                p->_direction = NONE;
                p->_has_nmrc_feedback = false;
                p->_nmrc_bitmap.clear();
                p->set_dst(destination);
                return p;
    }
  
    void free() {_packetdb.freePacket(this);}
    inline seq_t ackno() const {return _ackno;}
    inline simtime_picosec ts() const {return _ts;}
    inline void set_ts(simtime_picosec ts) {_ts = ts;}
    inline void set_nmrc_feedback(const NmrcBitmap& bitmap) {
        _nmrc_bitmap = bitmap;
        _has_nmrc_feedback = true;
    }
    inline bool has_nmrc_feedback() const {return _has_nmrc_feedback;}
    inline const NmrcBitmap& nmrc_bitmap() const {return _nmrc_bitmap;}
    virtual PktPriority priority() const {return Packet::PRIO_HI;}

    virtual ~RoceAck(){}

 protected:
    seq_t _ackno;
    simtime_picosec _ts;
    bool _has_nmrc_feedback;
    NmrcBitmap _nmrc_bitmap;
    static PacketDB<RoceAck> _packetdb;
};


class RoceNack : public Packet {
 public:
    typedef RocePacket::seq_t seq_t;
    typedef enum {LOSS = 0, TRIM = 1, OOO = 2} nack_reason_t;

    inline static RoceNack* newpkt(PacketFlow &flow, const Route &route,
                                  seq_t ackno,
                                  uint32_t destination = UINT32_MAX,
                                  uint64_t sack_bitmap = 0,
                                  uint16_t sack_offset = 0,
                                  bool has_sack = false,
                                  uint64_t sack_bitmap_high = 0,
                                  seq_t sack_bitmap_start_psn = 0,
                                  uint16_t sack_bitmap_valid_length = 0,
                                  uint16_t sack_bitmap_encoded_bits = 64) {
                RoceNack* p = _packetdb.allocPacket();
                uint32_t packet_size = RocePacket::ACKSIZE;
                if (has_sack && sack_bitmap_encoded_bits > 64)
                    packet_size += sizeof(uint64_t);
                p->set_route(flow,route,packet_size,ackno);
                p->_type = ROCENACK;
                p->_is_header = true;
                p->_ackno = ackno;
                p->_sack_bitmap = sack_bitmap;
                p->_sack_bitmap_high = sack_bitmap_high;
                p->_sack_offset = sack_offset;
                p->_sack_bitmap_start_psn = sack_bitmap_start_psn ?
                    sack_bitmap_start_psn : ackno + 1;
                p->_sack_bitmap_valid_length = has_sack ?
                    sack_bitmap_valid_length : 0;
                p->_has_sack = has_sack;
                p->_reason = LOSS;
                p->_direction = NONE;
                p->set_dst(destination);
                return p;
    }

    void free() {_packetdb.freePacket(this);}
    inline seq_t ackno() const {return _ackno;}
    inline seq_t apsn() const {return _ackno;}
    inline uint64_t sack_bitmap() const {return _sack_bitmap;}
    inline uint64_t sack_bitmap_low() const {return _sack_bitmap;}
    inline uint64_t sack_bitmap_high() const {return _sack_bitmap_high;}
    inline uint16_t sack_offset() const {return _sack_offset;}
    inline seq_t sack_bitmap_start_psn() const {return _sack_bitmap_start_psn;}
    inline uint16_t sack_bitmap_valid_length() const {return _sack_bitmap_valid_length;}
    inline bool has_sack() const {return _has_sack;}
    inline void set_sack_bitmap(uint64_t bitmap) {_sack_bitmap = bitmap;}
    inline void set_sack_bitmap_high(uint64_t bitmap) {_sack_bitmap_high = bitmap;}
    inline void set_sack_offset(uint16_t offset) {_sack_offset = offset;}
    inline void set_sack_bitmap_start_psn(seq_t psn) {_sack_bitmap_start_psn = psn;}
    inline void set_sack_bitmap_valid_length(uint16_t length) {_sack_bitmap_valid_length = length;}
    inline void set_has_sack(bool has_sack) {_has_sack = has_sack;}
    inline bool sack_bit(uint32_t bit) const {
        if (bit >= 128)
            return false;
        return bit < 64 ? (_sack_bitmap & (1ULL << bit)) :
            (_sack_bitmap_high & (1ULL << (bit - 64)));
    }
    inline nack_reason_t reason() const {return _reason;}
    inline void set_reason(nack_reason_t reason) {_reason = reason;}
    inline simtime_picosec ts() const {return _ts;}
    inline void set_ts(simtime_picosec ts) {_ts = ts;}
    virtual PktPriority priority() const {return Packet::PRIO_HI;}
  
    virtual ~RoceNack(){}

protected:
    seq_t _ackno;
    uint64_t _sack_bitmap;
    uint64_t _sack_bitmap_high;
    uint16_t _sack_offset;
    seq_t _sack_bitmap_start_psn;
    uint16_t _sack_bitmap_valid_length;
    bool _has_sack;
    nack_reason_t _reason;
    simtime_picosec _ts;
    static PacketDB<RoceNack> _packetdb;
};


#endif
