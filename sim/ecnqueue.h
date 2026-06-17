// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-        
#ifndef _ECN_QUEUE_H
#define _ECN_QUEUE_H
#include "queue.h"
/*
 * A simple ECN queue that marks on dequeue as soon as the packet occupancy exceeds the set threshold. 
 */

#include <list>
#include "config.h"
#include "eventlist.h"
#include "network.h"
#include "loggertypes.h"

class ECNQueue : public Queue {
public:
    ECNQueue(linkspeed_bps bitrate, mem_b maxsize, EventList &eventlist,
             QueueLogger* logger, mem_b drop);
    ECNQueue(linkspeed_bps bitrate, mem_b maxsize, EventList &eventlist,
             QueueLogger* logger, mem_b Kmin, mem_b Kmax);
    void receivePacket(Packet & pkt);
    void completeService();
    int drop_count() const { return _num_drops; }
    uint64_t ecn_mark_count() const { return _ecn_marks; }
private:
    mem_b _K;
    mem_b _ecn_minthresh;
    mem_b _ecn_maxthresh;
    bool _use_red;
    int _state_send;
    uint64_t _ecn_marks;
    bool should_mark_ecn() const;
};

#endif
