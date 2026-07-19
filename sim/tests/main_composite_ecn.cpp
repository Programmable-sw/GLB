// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <cstdlib>
#include <iostream>

#define protected public
#include "compositequeue.h"
#undef protected

#include "datacenter/fat_tree_switch.h"
#include "datacenter/fat_tree_topology.h"
#include "eventlist.h"

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

static void expect_eq(mem_b actual, mem_b expected, const char* message) {
    if (actual != expected) {
        std::cerr << message << ": got " << actual
                  << " expected " << expected << std::endl;
        std::exit(1);
    }
}

int main() {
    EventList eventlist;
    mem_b queuesize = 1000;

    FatTreeTopology::set_tiers(2);
    FatTreeSwitch::_ecn_threshold_fraction = 0.8;

    FatTreeTopology topo(2, speedFromMbps((uint64_t)100000), queuesize,
                         NULL, &eventlist, NULL, COMPOSITE_ECN_LB,
                         timeFromUs(1.0), 0);

    CompositeQueue* tor_uplink =
        dynamic_cast<CompositeQueue*>(topo.queues_nlp_nup[0][0][0]);
    expect(tor_uplink != NULL, "ToR uplink should use CompositeQueue");
    expect_eq(tor_uplink->_ecn_minthresh, queuesize / 5,
              "composite_ecn_lb uplink should use RED Kmin at 20% queue");
    expect_eq(tor_uplink->_ecn_maxthresh,
              (mem_b)(FatTreeSwitch::_ecn_threshold_fraction * queuesize),
              "composite_ecn_lb uplink should use configured RED Kmax");

    CompositeQueue* tor_downlink =
        dynamic_cast<CompositeQueue*>(topo.queues_nlp_ns[0][0][0]);
    expect(tor_downlink != NULL, "ToR downlink should use CompositeQueue");
    expect_eq(tor_downlink->_ecn_minthresh, queuesize * 2,
              "ToR downlink should keep ECN disabled");
    expect_eq(tor_downlink->_ecn_maxthresh, queuesize * 2,
              "ToR downlink should keep ECN disabled");

    return 0;
}
