// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <cstdlib>
#include <array>
#include <iostream>
#include <list>
#include <map>
#include <set>
#include <sstream>
#include <vector>

#define private public
#include "roce.h"
#undef private

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

int main() {
    EventList eventlist;
    RoceSrc src(NULL, NULL, eventlist, speedFromMbps((uint64_t)100000));

    src._send_event_pending = true;
    src._send_event_time = timeFromUs(1.0);
    src.schedule_send(eventlist.now());

    expect(src._send_event_pending, "send event should remain pending");
    expect(src._send_event_time == eventlist.now(),
           "stale send event should be replaced by the earlier send time");
    EventList::cancelPendingSourceByHandle(src, src._send_event_handle);
    src._send_event_pending = false;
    src._send_event_handle = EventList::nullHandle();
    return 0;
}
