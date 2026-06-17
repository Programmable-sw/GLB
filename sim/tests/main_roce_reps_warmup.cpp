// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <cstdlib>
#include <iostream>
#include <list>
#include <map>
#include <set>
#include <sstream>
#include <vector>

#define private public
#include "roce.h"
#undef private

#include "config.h"
#include "eventlist.h"
#include "network.h"

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

int main() {
    Packet::set_packet_size(1000);
    EventList eventlist;

    RoceSrc::setLoadBalancing(RoceSrc::LB_REPS);
    RoceSrc::setPathEntropySize(1024);
    RoceSrc::setRepsBufferSize(8);
    RoceSrc::setRepsWarmupPkts(2);

    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));
    src._flow_started = true;
    src._state_send = RoceSrc::READY;
    src.reset_reps_buffer();

    src._reps_buffer[0].cached_ev = 0;
    src._reps_buffer[0].valid = true;
    src._reps_head = 1;
    src._reps_valid_count = 1;

    srandom(1);
    uint32_t first = src.choose_path(Packet::PRIO_LO, false);
    uint32_t second = src.choose_path(Packet::PRIO_LO, false);
    expect(first != 0, "first warmup packet should explore randomly, not consume cached EV");
    expect(second != 0, "second warmup packet should still explore randomly");
    expect(src._reps_valid_count == 1, "warmup exploration should not consume cached clean EVs");

    uint32_t third = src.choose_path(Packet::PRIO_LO, false);
    expect(third == 0, "after warmup, REPS should recycle the oldest cached clean EV");
    expect(src._reps_valid_count == 0, "cached clean EV should be consumed after warmup");
    return 0;
}
