// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <map>
#include <vector>

#include "eventlist.h"
#include "network.h"
#include "roce.h"

static void expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(1);
    }
}

static EventList& test_eventlist() {
    static EventList eventlist;
    return eventlist;
}

static RoceSrc* make_src(uint32_t flow_id) {
    RoceSrc* src = new RoceSrc(
        NULL, NULL, test_eventlist(), 400000000000ULL);
    src->set_flowid(flow_id);
    src->set_src(17);
    src->set_dst(113);
    return src;
}

static std::vector<uint32_t> allocation_for(
        const std::vector<uint8_t>& levels,
        uint32_t good, uint32_t degraded, uint32_t bad, uint32_t avoid) {
    RoceSrc::setNetawareWeightAdaptation(
        RoceSrc::NETAWARE_WEIGHT_ADAPTATION_OFF);
    RoceSrc::resetNetawareSharedState();
    RoceSrc::setLoadBalancing(RoceSrc::LB_NETAWARE);
    RoceSrc::setPathEntropySize((uint32_t)levels.size());
    RoceSrc::setNetawareWrrMode(RoceSrc::NETAWARE_WRR_SHUFFLED_BUCKET);
    RoceSrc::setNetawareLevelWeights(good, degraded, bad, avoid);
    RoceSrc* src = make_src(91);
    src->apply_netaware_feedback_for_test(levels);
    return src->netaware_bucket_tickets_for_test(
        Packet::PRIO_LO, (uint32_t)levels.size());
}

static std::vector<uint32_t> good_share_cap_allocation_for(
        const std::vector<uint8_t>& levels,
        uint32_t good = 4, uint32_t degraded = 2,
        uint32_t bad = 1, uint32_t avoid = 0) {
    RoceSrc::setNetawareWeightAdaptation(
        RoceSrc::NETAWARE_WEIGHT_ADAPTATION_GOOD_SHARE_CAP);
    RoceSrc::resetNetawareSharedState();
    RoceSrc::setLoadBalancing(RoceSrc::LB_NETAWARE);
    RoceSrc::setPathEntropySize((uint32_t)levels.size());
    RoceSrc::setNetawareWrrMode(RoceSrc::NETAWARE_WRR_SHUFFLED_BUCKET);
    RoceSrc::setNetawareLevelWeights(good, degraded, bad, avoid);
    RoceSrc* src = make_src(91);
    src->apply_netaware_feedback_for_test(levels);
    return src->netaware_bucket_tickets_for_test(
        Packet::PRIO_LO, (uint32_t)levels.size());
}

static uint32_t ticket_sum(const std::vector<uint32_t>& tickets) {
    uint32_t total = 0;
    for (uint32_t i = 0; i < tickets.size(); i++)
        total += tickets[i];
    return total;
}

static uint32_t max_tickets(const std::vector<uint32_t>& tickets) {
    return tickets.empty() ? 0 :
        *std::max_element(tickets.begin(), tickets.end());
}

static std::vector<uint32_t> sequence_for(
        const std::vector<uint8_t>& levels,
        uint32_t good, uint32_t degraded, uint32_t bad, uint32_t avoid,
        RoceSrc::netaware_wrr_mode_t mode, uint32_t count) {
    RoceSrc::setNetawareWeightAdaptation(
        RoceSrc::NETAWARE_WEIGHT_ADAPTATION_OFF);
    RoceSrc::resetNetawareSharedState();
    RoceSrc::setLoadBalancing(RoceSrc::LB_NETAWARE);
    RoceSrc::setPathEntropySize((uint32_t)levels.size());
    RoceSrc::setNetawareWrrMode(mode);
    RoceSrc::setNetawareLevelWeights(good, degraded, bad, avoid);
    RoceSrc* src = make_src(91);
    src->apply_netaware_feedback_for_test(levels);

    std::vector<uint32_t> out;
    for (uint32_t i = 0; i < count; i++)
        out.push_back(src->choose_path_for_test(Packet::PRIO_LO, false));
    return out;
}

static std::map<uint32_t, uint32_t> counts(
        const std::vector<uint32_t>& sequence) {
    std::map<uint32_t, uint32_t> result;
    for (uint32_t i = 0; i < sequence.size(); i++)
        result[sequence[i]]++;
    return result;
}

static void test_virtual_permutation_is_random_access_and_bijective() {
    const uint32_t domain = 32;
    std::vector<uint32_t> first;
    std::vector<uint32_t> replay;
    std::vector<uint32_t> other_flow;
    std::vector<uint32_t> other_epoch;
    for (uint32_t pos = 0; pos < domain; pos++) {
        first.push_back(
            RoceSrc::virtualShuffleIndexForTest(17, 0, pos, domain));
        replay.push_back(
            RoceSrc::virtualShuffleIndexForTest(17, 0, pos, domain));
        other_flow.push_back(
            RoceSrc::virtualShuffleIndexForTest(29, 0, pos, domain));
        other_epoch.push_back(
            RoceSrc::virtualShuffleIndexForTest(17, 1, pos, domain));
    }
    expect(first == replay,
           "virtual shuffle should be deterministic within one epoch");
    expect(first != other_flow,
           "different flow keys should produce different permutations");
    expect(first != other_epoch,
           "different epochs should produce different permutations");
    std::sort(first.begin(), first.end());
    for (uint32_t i = 0; i < domain; i++)
        expect(first[i] == i,
               "virtual shuffle should visit every index exactly once");
}

static void test_virtual_permutation_handles_arbitrary_domains() {
    for (uint32_t domain = 2; domain <= 100; domain++) {
        std::vector<uint32_t> seen(domain, 0);
        for (uint32_t pos = 0; pos < domain; pos++) {
            uint32_t index = RoceSrc::virtualShuffleIndexForTest(
                101, 7, pos, domain);
            expect(index < domain,
                   "virtual shuffle index should stay inside its domain");
            seen[index]++;
        }
        for (uint32_t index = 0; index < domain; index++)
            expect(seen[index] == 1,
                   "virtual shuffle should be bijective for every domain");
    }
}

static void test_fixed_bucket_is_scale_invariant() {
    std::vector<uint8_t> levels;
    levels.push_back(STOR_LEVEL_GOOD);
    levels.push_back(STOR_LEVEL_GOOD);
    levels.push_back(STOR_LEVEL_DEGRADED);
    levels.push_back(STOR_LEVEL_DEGRADED);
    levels.push_back(STOR_LEVEL_BAD);
    levels.push_back(STOR_LEVEL_BAD);
    levels.push_back(STOR_LEVEL_AVOID);
    levels.push_back(STOR_LEVEL_AVOID);

    std::vector<uint32_t> a = allocation_for(levels, 4, 2, 1, 0);
    std::vector<uint32_t> b = allocation_for(levels, 8, 4, 2, 0);
    expect(a == b,
           "fixed shuffled-bucket allocation should ignore weight scale");
    expect(ticket_sum(a) == 32,
           "fixed shuffled-bucket allocation should fill K=4P");

    std::vector<uint32_t> seq_a = sequence_for(
        levels, 4, 2, 1, 0, RoceSrc::NETAWARE_WRR_SHUFFLED_BUCKET, 128);
    std::vector<uint32_t> seq_b = sequence_for(
        levels, 8, 4, 2, 0, RoceSrc::NETAWARE_WRR_SHUFFLED_BUCKET, 128);
    expect(seq_a == seq_b,
           "bucket consumption should ignore proportional weight scale");
}

static void test_epoch_changes_order_without_changing_allocation() {
    std::vector<uint8_t> all_good(8, STOR_LEVEL_GOOD);
    std::vector<uint32_t> two_epochs = sequence_for(
        all_good, 4, 2, 1, 0,
        RoceSrc::NETAWARE_WRR_SHUFFLED_BUCKET, 64);
    std::vector<uint32_t> first(two_epochs.begin(), two_epochs.begin() + 32);
    std::vector<uint32_t> second(two_epochs.begin() + 32, two_epochs.end());
    expect(first != second, "each epoch should reshuffle the bucket");
    expect(counts(first) == counts(second),
           "epoch reshuffle should preserve the ticket allocation");
}

static void test_direct_mode_keeps_absolute_ticket_behavior() {
    std::vector<uint8_t> all_good(8, STOR_LEVEL_GOOD);
    std::vector<uint32_t> a = sequence_for(
        all_good, 10, 8, 4, 0, RoceSrc::NETAWARE_WRR_DIRECT, 80);
    std::vector<uint32_t> b = sequence_for(
        all_good, 5, 4, 2, 0, RoceSrc::NETAWARE_WRR_DIRECT, 80);
    expect(a != b,
           "explicit direct mode should retain absolute-ticket behavior");
}

static void test_good_share_cap_all_good_is_uniform() {
    std::vector<uint8_t> levels(8, STOR_LEVEL_GOOD);
    std::vector<uint32_t> tickets =
        good_share_cap_allocation_for(levels);
    expect(ticket_sum(tickets) == 32 && max_tickets(tickets) == 4,
           "GoodCap should preserve all-GOOD uniform spraying");
}

static void test_good_share_cap_tracks_good_fraction() {
    std::vector<uint8_t> one_good(8, STOR_LEVEL_AVOID);
    one_good[0] = STOR_LEVEL_GOOD;
    std::vector<uint32_t> sparse =
        good_share_cap_allocation_for(one_good);
    for (uint32_t path = 0; path < sparse.size(); path++)
        expect(sparse[path] == 4,
               "one GOOD path should be softened to a uniform K=32 bucket");

    std::vector<uint8_t> four_good(8, STOR_LEVEL_AVOID);
    for (uint32_t path = 0; path < 4; path++)
        four_good[path] = STOR_LEVEL_GOOD;
    std::vector<uint32_t> balanced =
        good_share_cap_allocation_for(four_good);
    for (uint32_t path = 0; path < 4; path++) {
        expect(balanced[path] == 6,
               "four GOOD paths should each receive six tickets");
        expect(balanced[path + 4] == 2,
               "four AVOID paths should each receive two tickets");
    }
}

static void test_good_share_cap_preserves_no_good_ordering() {
    const uint8_t raw[] = {
        STOR_LEVEL_DEGRADED, STOR_LEVEL_DEGRADED,
        STOR_LEVEL_BAD, STOR_LEVEL_BAD,
        STOR_LEVEL_AVOID, STOR_LEVEL_AVOID,
        STOR_LEVEL_AVOID, STOR_LEVEL_AVOID,
    };
    std::vector<uint8_t> levels(raw, raw + 8);
    std::vector<uint32_t> tickets =
        good_share_cap_allocation_for(levels);
    expect(tickets[0] > tickets[2] && tickets[2] > tickets[4],
           "no-GOOD profile should preserve DEGRADED > BAD > AVOID");
}

static void test_good_share_cap_is_scale_invariant() {
    const uint8_t raw[] = {
        STOR_LEVEL_GOOD, STOR_LEVEL_GOOD,
        STOR_LEVEL_DEGRADED, STOR_LEVEL_DEGRADED,
        STOR_LEVEL_BAD, STOR_LEVEL_BAD,
        STOR_LEVEL_AVOID, STOR_LEVEL_AVOID,
    };
    std::vector<uint8_t> levels(raw, raw + 8);
    std::vector<uint32_t> a =
        good_share_cap_allocation_for(levels, 4, 2, 1, 0);
    std::vector<uint32_t> b =
        good_share_cap_allocation_for(levels, 8, 4, 2, 0);
    expect(a == b,
           "GoodCap allocation should ignore proportional weight scale");
}

int main() {
    expect(RoceSrc::netawareWeightAdaptation() ==
               RoceSrc::NETAWARE_WEIGHT_ADAPTATION_GOOD_SHARE_CAP,
           "NetAware default weight adaptation should be GoodCap");
    expect(RoceSrc::weightedShuffledBucketSize(8) == 32 &&
           RoceSrc::weightedShuffledBucketSize(16) == 64 &&
           RoceSrc::weightedShuffledBucketSize(64) == 256,
           "weighted shuffled bucket should use K=4*path_count");
    expect(RoceSrc::netawareLevelWeight(STOR_LEVEL_GOOD) == 4 &&
           RoceSrc::netawareLevelWeight(STOR_LEVEL_DEGRADED) == 2 &&
           RoceSrc::netawareLevelWeight(STOR_LEVEL_BAD) == 1 &&
           RoceSrc::netawareLevelWeight(STOR_LEVEL_AVOID) == 0,
           "NetAware default level weights should be 4/2/1/0");

    test_virtual_permutation_is_random_access_and_bijective();
    test_virtual_permutation_handles_arbitrary_domains();
    test_fixed_bucket_is_scale_invariant();
    test_epoch_changes_order_without_changing_allocation();
    test_direct_mode_keeps_absolute_ticket_behavior();
    test_good_share_cap_all_good_is_uniform();
    test_good_share_cap_tracks_good_fraction();
    test_good_share_cap_preserves_no_good_ordering();
    test_good_share_cap_is_scale_invariant();
    return 0;
}
