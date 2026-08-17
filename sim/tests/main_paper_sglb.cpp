#include "../datacenter/fat_tree_switch.h"
#include "../sglbpacket.h"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <iostream>
#include <vector>

using std::vector;

int main() {
    FatTreeSwitch::configure_sglb_scheme_defaults(false);
    assert(FatTreeSwitch::_sglb_ofat_factor ==
           FatTreeSwitch::SGLB_OFAT_REAL_GCN_RAW_LINEAR);
    assert(FatTreeSwitch::_sglb_min_choices == 24);
    assert(FatTreeSwitch::_sglb_gcn_update_interval == timeFromUs(15.0));
    assert(FatTreeSwitch::_sglb_nmrc_degraded_threshold == 0.05);
    assert(FatTreeSwitch::_sglb_nmrc_bad_threshold == 0.10);
    assert(FatTreeSwitch::_sglb_nmrc_avoid_threshold == 0.20);

    FatTreeSwitch::configure_sglb_scheme_defaults(true);
    assert(FatTreeSwitch::_sglb_ofat_factor ==
           FatTreeSwitch::SGLB_OFAT_BASELINE);
    assert(FatTreeSwitch::_sglb_min_choices == 3);
    assert(FatTreeSwitch::_sglb_gcn_update_interval == timeFromUs(15.0));

    assert(FatTreeSwitch::_paper_sglb_ablation ==
           FatTreeSwitch::PAPER_SGLB_ABLATION_NONE);
    assert(std::string(FatTreeSwitch::paper_sglb_ablation_name()) == "none");
    PacketFlow gcn_flow(NULL);
    Route gcn_route;
    vector<SglbGcnRecord> gcn_records;
    gcn_records.push_back(SglbGcnRecord(
        9, 1, true, 0.025, 0.05, 0.075, 0));
    gcn_records.push_back(SglbGcnRecord(
        10, 2, true, 0.10, 0.20, 0.30, 1));
    gcn_records.push_back(SglbGcnRecord(
        11, 3, true, 0.125, 0.25, 0.375, 2));
    SglbGcnPacket* gcn = SglbGcnPacket::newpkt(
        gcn_flow, gcn_route, 7, gcn_records, 19, 123456);
    assert(gcn->type() == SGLB_GCN);
    assert(gcn->size() == 256);
    assert(gcn->header_only());
    assert(gcn->priority() == Packet::PRIO_HI);
    assert(gcn->sender_switch_id() == 7);
    assert(gcn->record_count() == 3);
    assert(gcn->record(0).destination_switch_id == 9);
    assert(gcn->record(1).port_quality == 1);
    assert(gcn->record(2).destination_switch_id == 11);
    assert(gcn->record(2).port_id == 3);
    assert(gcn->record(2).link_up);
    assert(std::fabs(gcn->record(2).remote_queue - 0.125) < 1e-12);
    assert(std::fabs(gcn->record(2).remote_utilization - 0.25) < 1e-12);
    assert(std::fabs(gcn->record(2).remote_busyness - 0.375) < 1e-12);
    assert(gcn->record(2).port_quality == 2);
    assert(gcn->version() == 19);
    assert(gcn->generated_at() == 123456);
    gcn->free();

    SglbGcnPacket* zero_gcn = SglbGcnPacket::newpkt(
        gcn_flow, gcn_route, 7, gcn_records, 20, 123457, 0);
    assert(zero_gcn->size() == 0);
    zero_gcn->free();

    FatTreeSwitch::_paper_sglb_weight_local_queue = 0.5;
    FatTreeSwitch::_paper_sglb_weight_local_util = 0.0;
    FatTreeSwitch::_paper_sglb_weight_remote_queue = 0.5;
    FatTreeSwitch::_paper_sglb_weight_remote_util = 0.0;
    FatTreeSwitch::_paper_sglb_weight_remote_busy = 0.0;
    FatTreeSwitch::PaperSglbFactors factors;
    factors.local_queue = 0.08;
    factors.local_utilization = 0.90;
    factors.remote_queue = 0.12;
    factors.remote_utilization = 0.80;
    factors.remote_busyness = 0.70;
    assert(std::fabs(FatTreeSwitch::paper_sglb_value(factors) - 0.10) < 1e-12);

    FatTreeSwitch::_paper_sglb_ablation =
        FatTreeSwitch::PAPER_SGLB_ABLATION_LEGACY_NOISY_OR;
    assert(std::fabs(FatTreeSwitch::paper_sglb_value(factors) - 0.1904) < 1e-12);
    FatTreeSwitch::_paper_sglb_ablation =
        FatTreeSwitch::PAPER_SGLB_ABLATION_LEGACY_QUEUE_PRESSURE;
    assert(FatTreeSwitch::paper_sglb_value(factors) == 0.0);
    FatTreeSwitch::_paper_sglb_ablation =
        FatTreeSwitch::PAPER_SGLB_ABLATION_LEGACY_LEVELS;
    assert(FatTreeSwitch::paper_sglb_ar_level(0.20) == 1);
    FatTreeSwitch::_paper_sglb_ablation =
        FatTreeSwitch::PAPER_SGLB_ABLATION_NONE;

    assert(FatTreeSwitch::paper_sglb_ar_level(0.0) == 0);
    assert(FatTreeSwitch::paper_sglb_ar_level(0.049999) == 0);
    assert(FatTreeSwitch::paper_sglb_ar_level(0.05) == 1);
    assert(FatTreeSwitch::paper_sglb_ar_level(0.099999) == 1);
    assert(FatTreeSwitch::paper_sglb_ar_level(0.10) == 2);
    assert(FatTreeSwitch::paper_sglb_ar_level(0.199999) == 2);
    assert(FatTreeSwitch::paper_sglb_ar_level(0.20) == 3);
    assert(FatTreeSwitch::paper_sglb_ar_level(1.0) == 3);

    vector<uint8_t> ar_levels{2, 0, 1, 0, 0, 3};
    vector<bool> ar_available{true, true, true, true, false, true};
    vector<uint32_t> best = FatTreeSwitch::paper_sglb_best_level(
        ar_levels, ar_available, 1);
    assert((best == vector<uint32_t>{1, 3}));

    vector<uint8_t> fill_levels{0, 0, 0, 0, 1, 1, 1, 1, 1, 2};
    vector<bool> fill_available(fill_levels.size(), true);
    vector<uint32_t> filled = FatTreeSwitch::paper_sglb_best_level(
        fill_levels, fill_available, 8);
    assert((filled == vector<uint32_t>{0, 1, 2, 3, 4, 5, 6, 7, 8}));

    vector<uint64_t> fill_keys{90, 80, 70, 60, 50, 40, 30, 20, 10, 0};
    vector<uint32_t> exact_filled =
        FatTreeSwitch::paper_sglb_exact_min_by_level(
            fill_levels, fill_available, fill_keys, 8);
    assert(exact_filled.size() == 8);
    for (uint32_t i = 0; i < 4; ++i)
        assert(std::find(exact_filled.begin(), exact_filled.end(), i) !=
               exact_filled.end());
    assert(std::find(exact_filled.begin(), exact_filled.end(), 8) !=
           exact_filled.end());
    assert(std::find(exact_filled.begin(), exact_filled.end(), 7) !=
           exact_filled.end());
    assert(std::find(exact_filled.begin(), exact_filled.end(), 6) !=
           exact_filled.end());
    assert(std::find(exact_filled.begin(), exact_filled.end(), 5) !=
           exact_filled.end());
    assert(std::find(exact_filled.begin(), exact_filled.end(), 4) ==
           exact_filled.end());

    vector<uint32_t> strict_filled =
        FatTreeSwitch::paper_sglb_strict_k_by_level(
            fill_levels, fill_available, fill_keys, 3);
    assert((strict_filled == vector<uint32_t>{3, 2, 1}));

    assert(FatTreeSwitch::_sglb_candidate_policy ==
           FatTreeSwitch::SGLB_CANDIDATE_EXACT_MIN);

    vector<uint8_t> oversized_best_levels{0, 0, 0, 0, 1};
    vector<bool> oversized_best_available(oversized_best_levels.size(), true);
    vector<uint64_t> oversized_best_keys{4, 3, 2, 1, 0};
    vector<uint32_t> oversized_best =
        FatTreeSwitch::paper_sglb_exact_min_by_level(
            oversized_best_levels, oversized_best_available,
            oversized_best_keys, 3);
    assert((oversized_best == vector<uint32_t>{3, 2, 1, 0}));

    FatTreeSwitch::SglbShuffledRrState rr_state;
    vector<uint32_t> rr_candidates{2, 5, 7, 11};
    vector<uint32_t> rr_first_cycle;
    vector<uint32_t> rr_second_cycle;
    for (uint32_t i = 0; i < rr_candidates.size(); ++i) {
        rr_first_cycle.push_back(FatTreeSwitch::sglb_shuffled_rr_select(
            rr_candidates, 3, 9, 17, rr_state));
    }
    for (uint32_t i = 0; i < rr_candidates.size(); ++i) {
        rr_second_cycle.push_back(FatTreeSwitch::sglb_shuffled_rr_select(
            rr_candidates, 3, 9, 17, rr_state));
    }
    std::sort(rr_first_cycle.begin(), rr_first_cycle.end());
    std::sort(rr_second_cycle.begin(), rr_second_cycle.end());
    assert(rr_first_cycle == rr_candidates);
    assert(rr_second_cycle == rr_candidates);

    vector<uint32_t> changed_candidates{2, 7, 13};
    uint32_t changed_selected = FatTreeSwitch::sglb_shuffled_rr_select(
        changed_candidates, 3, 9, 18, rr_state);
    assert(std::find(changed_candidates.begin(), changed_candidates.end(),
                     changed_selected) != changed_candidates.end());
    assert(rr_state.cursor == 1);

    vector<uint8_t> topk_levels{0, 0, 0, 0, 1, 1, 1, 1, 1, 2};
    vector<bool> topk_available(topk_levels.size(), true);
    vector<uint64_t> tie_keys{9, 8, 7, 6, 5, 4, 3, 2, 1, 0};
    vector<uint32_t> ar_top = FatTreeSwitch::paper_sglb_topk_by_level(
        topk_levels, topk_available, tie_keys, 8);
    assert(ar_top.size() == 8);
    for (uint32_t i = 0; i < 4; ++i)
        assert(std::find(ar_top.begin(), ar_top.end(), i) != ar_top.end());
    assert(std::find(ar_top.begin(), ar_top.end(), 8) != ar_top.end());
    assert(std::find(ar_top.begin(), ar_top.end(), 7) != ar_top.end());
    assert(std::find(ar_top.begin(), ar_top.end(), 6) != ar_top.end());
    assert(std::find(ar_top.begin(), ar_top.end(), 5) != ar_top.end());
    assert(std::find(ar_top.begin(), ar_top.end(), 4) == ar_top.end());
    assert(std::find(ar_top.begin(), ar_top.end(), 9) == ar_top.end());

    assert(std::fabs(FatTreeSwitch::paper_sglb_noisy_or(0.2, 0.5) - 0.6) < 1e-12);
    FatTreeSwitch::_paper_sglb_q_low = 0.2;
    FatTreeSwitch::_paper_sglb_q_high = 0.8;
    assert(FatTreeSwitch::paper_sglb_queue_pressure(0.1) == 0.0);
    assert(std::fabs(FatTreeSwitch::paper_sglb_queue_pressure(0.5) - 0.5) < 1e-12);
    assert(FatTreeSwitch::paper_sglb_queue_pressure(0.9) == 1.0);
    assert(FatTreeSwitch::paper_sglb_quantized_level(0.0, 8) == 0);
    assert(FatTreeSwitch::paper_sglb_quantized_level(0.124, 8) == 0);
    assert(FatTreeSwitch::paper_sglb_quantized_level(0.125, 8) == 1);
    assert(FatTreeSwitch::paper_sglb_quantized_level(1.0, 8) == 7);

    vector<uint8_t> levels{1, 0, 0, 2, 0};
    vector<double> scores{0.2, 0.10, 0.10, 0.4, 0.05};
    vector<bool> available{true, true, true, true, false};
    vector<uint64_t> stable_keys{30, 20, 10, 40, 0};
    vector<uint32_t> top = FatTreeSwitch::paper_sglb_strict_topk(
        levels, scores, available, stable_keys, 3);
    assert(top.size() == 3);
    assert(top[0] == 2); // same level/score: stable key wins
    assert(top[1] == 1);
    assert(top[2] == 0);

    FatTreeSwitch::PaperSglbRemoteState remote;
    assert(FatTreeSwitch::paper_sglb_snapshot_refresh_due(
        false, 0, 100, timeFromUs(1.0)));
    assert(!FatTreeSwitch::paper_sglb_snapshot_refresh_due(
        true, 100, 100 + timeFromUs(1.0) - 1, timeFromUs(1.0)));
    assert(FatTreeSwitch::paper_sglb_snapshot_refresh_due(
        true, 100, 100 + timeFromUs(1.0), timeFromUs(1.0)));
    assert(FatTreeSwitch::paper_sglb_emit_due(
        false, 0, 100, timeFromUs(15.0)));
    assert(!FatTreeSwitch::paper_sglb_emit_due(
        true, 100, 100 + timeFromUs(15.0) - 1, timeFromUs(15.0)));
    assert(FatTreeSwitch::paper_sglb_emit_due(
        true, 100, 100 + timeFromUs(15.0), timeFromUs(15.0)));

    assert(FatTreeSwitch::paper_sglb_accept_gcn(
        remote, true, 0.125, 0.25, 0.375, 2, 3, 100));
    assert(remote.valid && remote.version == 3 && remote.level == 2);
    assert(remote.link_up);
    assert(std::fabs(remote.remote_queue - 0.125) < 1e-12);
    assert(std::fabs(remote.remote_utilization - 0.25) < 1e-12);
    assert(std::fabs(remote.remote_busyness - 0.375) < 1e-12);
    assert(!FatTreeSwitch::paper_sglb_accept_gcn(
        remote, true, 0.01, 0.02, 0.03, 0, 2, 200));
    assert(remote.version == 3 && remote.level == 2);
    assert(FatTreeSwitch::paper_sglb_accept_gcn(
        remote, false, 0.2, 0.3, 0.4, 3, 4, 300));
    assert(remote.version == 4 && remote.level == 3 &&
           remote.received_at == 300 && !remote.link_up);

    FatTreeSwitch::_paper_sglb_ablation =
        FatTreeSwitch::PAPER_SGLB_ABLATION_NO_VERSION;
    assert(FatTreeSwitch::paper_sglb_accept_gcn(
        remote, true, 0.1, 0.1, 0.1, 1, 2, 400));
    FatTreeSwitch::_paper_sglb_ablation =
        FatTreeSwitch::PAPER_SGLB_ABLATION_NONE;

    std::cout << "paper SGLB unit tests passed\n";
    return 0;
}
