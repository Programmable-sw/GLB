// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include "config.h"
#include <sstream>

#include <iostream>
#include <string.h>
#include <math.h>
#include <algorithm>
#include <unistd.h>
#include "network.h"
#include "randomqueue.h"
#include "queue_lossless_input.h"
#include "shortflows.h"
#include "pipe.h"
#include "eventlist.h"
#include "logfile.h"
#include "loggers.h"
#include "clock.h"
#include "roce.h"
#include "compositequeue.h"
#include "ecnqueue.h"
#include "firstfit.h"
#include "queue_lossless.h"
#include "queue_lossless_output.h"
#include "topology.h"
#include "connection_matrix.h"
#include "tcppacket.h"

#include "fat_tree_topology.h"
#include "fat_tree_switch.h"

#include <list>
#include <set>

// Simulation params

#define PRINT_PATHS 1

#define PERIODIC 0
#include "main.h"

uint32_t RTT = 1; // retained for legacy logfile metadata
int DEFAULT_NODES = 432;
#define DEFAULT_QUEUE_SIZE 100
#define REPS_LINKSPEED_MBPS 400000
#define REPS_MTU_BYTES 4096
#define REPS_HOP_LATENCY_US 0.5
#define REPS_SWITCH_LATENCY_US 0.5

//#define SWITCH_BUFFER (SERVICE * RTT / 1000)
#define USE_FIRST_FIT 0
#define FIRST_FIT_INTERVAL 100

EventList eventlist;

struct QueueDiag {
    uint64_t lossless_overflows;
    uint64_t lossy_drops;
    uint64_t lossy_ecn_marks;
    uint64_t composite_trims;
    uint64_t composite_drops;
    uint64_t composite_ecn_marks;

    QueueDiag()
        : lossless_overflows(0),
          lossy_drops(0),
          lossy_ecn_marks(0),
          composite_trims(0),
          composite_drops(0),
          composite_ecn_marks(0) {}
};

static void add_queue_diag(BaseQueue* queue, set<BaseQueue*>& seen, QueueDiag& diag) {
    if (!queue || !seen.insert(queue).second) {
        return;
    }

    LosslessOutputQueue* lossless_output = dynamic_cast<LosslessOutputQueue*>(queue);
    if (lossless_output) {
        diag.lossless_overflows += lossless_output->overflow_count();
    }

    LosslessQueue* lossless_queue = dynamic_cast<LosslessQueue*>(queue);
    if (lossless_queue) {
        diag.lossless_overflows += lossless_queue->overflow_count();
    }

    ECNQueue* ecn_queue = dynamic_cast<ECNQueue*>(queue);
    if (ecn_queue) {
        diag.lossy_drops += ecn_queue->drop_count();
        diag.lossy_ecn_marks += ecn_queue->ecn_mark_count();
    }

    CompositeQueue* composite_queue = dynamic_cast<CompositeQueue*>(queue);
    if (composite_queue) {
        diag.composite_trims += composite_queue->trim_count();
        diag.composite_drops += composite_queue->drop_count();
        diag.composite_ecn_marks += composite_queue->ecn_mark_count();
    }
}

static void add_queue_diag(const vector< vector< vector<BaseQueue*> > >& queues,
                           set<BaseQueue*>& seen, QueueDiag& diag) {
    for (size_t i = 0; i < queues.size(); i++) {
        for (size_t j = 0; j < queues[i].size(); j++) {
            for (size_t k = 0; k < queues[i][j].size(); k++) {
                add_queue_diag(queues[i][j][k], seen, diag);
            }
        }
    }
}

static QueueDiag collect_queue_diag(FatTreeTopology* top) {
    QueueDiag diag;
    set<BaseQueue*> seen;

    add_queue_diag(top->queues_nc_nup, seen, diag);
    add_queue_diag(top->queues_nup_nlp, seen, diag);
    add_queue_diag(top->queues_nlp_ns, seen, diag);
    add_queue_diag(top->queues_nup_nc, seen, diag);
    add_queue_diag(top->queues_nlp_nup, seen, diag);
    add_queue_diag(top->queues_ns_nlp, seen, diag);

    return diag;
}

class SglbBackgroundDropSink : public PacketSink {
public:
    SglbBackgroundDropSink() : _nodename("sglb_background_drop") {}

    void receivePacket(Packet& pkt) {
        pkt.free();
    }

    const string& nodename() {
        return _nodename;
    }

private:
    string _nodename;
};

class SglbBackgroundSource : public EventSource {
public:
    SglbBackgroundSource(EventList& eventlist, const string& name, Route* route,
                         double rate_gbps, uint32_t packet_size,
                         simtime_picosec on_time, simtime_picosec off_time)
        : EventSource(eventlist, name),
          _flow(NULL),
          _route(route),
          _packet_size(packet_size),
          _seq(1),
          _on_time(on_time),
          _off_time(off_time),
          _interval(1),
          _enabled(route && rate_gbps > 0.0 && packet_size > 0 && on_time > 0) {
        if (_enabled) {
            double ps = ceil(((double)packet_size * 8.0 * 1000.0) / rate_gbps);
            if (ps < 1.0)
                ps = 1.0;
            _interval = (simtime_picosec)ps;
        }
    }

    void start() {
        if (_enabled)
            eventlist().sourceIsPending(*this, EventList::now());
    }

    void doNextEvent() {
        if (!_enabled)
            return;

        simtime_picosec now = eventlist().now();
        simtime_picosec cycle = _on_time + _off_time;
        if (cycle > 0 && _off_time > 0) {
            simtime_picosec phase = now % cycle;
            if (phase >= _on_time) {
                eventlist().sourceIsPendingRel(*this, cycle - phase);
                return;
            }
        }

        TcpPacket* pkt = TcpPacket::newpkt(_flow, *_route, _seq, _packet_size);
        _seq += _packet_size;
        pkt->sendOn();

        simtime_picosec delay = _interval;
        if (cycle > 0 && _off_time > 0) {
            simtime_picosec phase = now % cycle;
            simtime_picosec remaining_on = _on_time - phase;
            if (delay > remaining_on)
                delay = remaining_on;
        }
        if (delay == 0)
            delay = 1;
        eventlist().sourceIsPendingRel(*this, delay);
    }

private:
    PacketFlow _flow;
    Route* _route;
    uint32_t _packet_size;
    TcpPacket::seq_t _seq;
    simtime_picosec _on_time;
    simtime_picosec _off_time;
    simtime_picosec _interval;
    bool _enabled;
};

class SglbQueueCvSampler : public EventSource {
public:
    SglbQueueCvSampler(EventList& eventlist, FatTreeTopology* top, simtime_picosec period)
        : EventSource(eventlist, "sglb_queue_cv_sampler"),
          _top(top),
          _period(period),
          _samples(0) {}

    void start() {
        if (_top && _period > 0)
            eventlist().sourceIsPendingRel(*this, _period);
    }

    void doNextEvent() {
        vector<double> sample;
        collect_spine_queues(sample);
        if (_sums.empty())
            _sums.assign(sample.size(), 0.0);
        if (sample.size() == _sums.size()) {
            for (size_t i = 0; i < sample.size(); i++)
                _sums[i] += sample[i];
            _samples++;
        }
        eventlist().sourceIsPendingRel(*this, _period);
    }

    double average_queue() const {
        if (_samples == 0 || _sums.empty())
            return 0.0;

        double total = 0.0;
        for (size_t i = 0; i < _sums.size(); i++)
            total += _sums[i] / (double)_samples;
        return total / (double)_sums.size();
    }

    double cv() const {
        if (_samples == 0 || _sums.empty())
            return 0.0;

        double mean = average_queue();
        if (mean <= 0.0)
            return 0.0;

        double variance = 0.0;
        for (size_t i = 0; i < _sums.size(); i++) {
            double avg = _sums[i] / (double)_samples;
            double delta = avg - mean;
            variance += delta * delta;
        }
        variance /= (double)_sums.size();
        return sqrt(variance) / mean;
    }

    uint32_t count() const {
        return (uint32_t)_sums.size();
    }

private:
    void collect_spine_queues(vector<double>& sample) {
        const vector< vector< vector<BaseQueue*> > >& queues = _top->queues_nc_nup;
        for (size_t core = 0; core < queues.size(); core++) {
            for (size_t agg = 0; agg < queues[core].size(); agg++) {
                for (size_t b = 0; b < queues[core][agg].size(); b++) {
                    BaseQueue* q = queues[core][agg][b];
                    if (q)
                        sample.push_back((double)q->queuesize());
                }
            }
        }
    }

    FatTreeTopology* _top;
    simtime_picosec _period;
    vector<double> _sums;
    uint64_t _samples;
};

static Route* make_sglb_background_route(BaseQueue* queue, Pipe* pipe, PacketSink* drop) {
    Route* route = new Route();
    route->push_back(queue);
    route->push_back(pipe);
    route->push_back(drop);
    return route;
}

static bool add_first_sglb_background_link(
        vector<SglbBackgroundSource*>& sources,
        EventList& eventlist,
        SglbBackgroundDropSink* drop,
        const string& label,
        const vector< vector< vector<BaseQueue*> > >& queues,
        const vector< vector< vector<Pipe*> > >& pipes,
        double rate_gbps,
        uint32_t packet_size,
        simtime_picosec on_time,
        simtime_picosec off_time) {
    for (size_t i = 0; i < queues.size(); i++) {
        for (size_t j = 0; j < queues[i].size(); j++) {
            for (size_t b = 0; b < queues[i][j].size(); b++) {
                BaseQueue* queue = queues[i][j][b];
                Pipe* pipe = NULL;
                if (i < pipes.size() && j < pipes[i].size() && b < pipes[i][j].size())
                    pipe = pipes[i][j][b];
                if (!queue || !pipe)
                    continue;

                Route* route = make_sglb_background_route(queue, pipe, drop);
                SglbBackgroundSource* source =
                    new SglbBackgroundSource(eventlist, "sglb_bg_" + label, route,
                                             rate_gbps, packet_size, on_time, off_time);
                sources.push_back(source);
                source->start();
                cout << "SGLB background " << label
                     << " on " << queue->nodename()
                     << " rate " << rate_gbps
                     << "Gbps on " << timeAsUs(on_time)
                     << "us off " << timeAsUs(off_time)
                     << "us packet " << packet_size
                     << " bytes" << endl;
                return true;
            }
        }
    }
    cout << "SGLB background " << label << " skipped: no live link" << endl;
    return false;
}

static uint32_t install_sglb_background(FatTreeTopology* top,
                                        EventList& eventlist,
                                        vector<SglbBackgroundSource*>& sources,
                                        SglbBackgroundDropSink* drop,
                                        double rate_gbps,
                                        uint32_t packet_size,
                                        simtime_picosec on_time,
                                        simtime_picosec off_time) {
    uint32_t added = 0;
    if (add_first_sglb_background_link(sources, eventlist, drop, "tor_to_leaf",
                                       top->queues_nlp_nup, top->pipes_nlp_nup,
                                       rate_gbps, packet_size, on_time, off_time))
        added++;
    if (add_first_sglb_background_link(sources, eventlist, drop, "leaf_to_tor",
                                       top->queues_nup_nlp, top->pipes_nup_nlp,
                                       rate_gbps, packet_size, on_time, off_time))
        added++;
    if (top->get_tiers() == 3) {
        if (add_first_sglb_background_link(sources, eventlist, drop, "leaf_to_spine",
                                           top->queues_nup_nc, top->pipes_nup_nc,
                                           rate_gbps, packet_size, on_time, off_time))
            added++;
        if (add_first_sglb_background_link(sources, eventlist, drop, "spine_to_leaf",
                                           top->queues_nc_nup, top->pipes_nc_nup,
                                           rate_gbps, packet_size, on_time, off_time))
            added++;
    }
    return added;
}

void exit_error(char* progr) {
    cout << "Usage " << progr << " [-nodes N]\n\t[-conns C]\n\t[-q queue_size]\n\t[-queue_type composite|composite_ecn|composite_ecn_lb|lossless|lossless_input|lossless_input_ecn|lossy_input_ecn|lossy_ecn]\n\t[-tm traffic_matrix_file]\n\t[-lb ecmp|ecmp_rr|adaptive-routing|glb|drill|reps|n-mrc|mrc|rr|ops|conweave|ndp]\n\t[-cc none|dcqcn|dcqcn_variant|mprdma]\n\t[-cc_iw_pkts pkts]\n\t[-cc_min_cwnd_pkts pkts]\n\t[-cc_max_cwnd_pkts pkts]\n\t[-dcqcn_g x]\n\t[-dcqcn_initial_alpha x]\n\t[-dcqcn_ai_mbps x]\n\t[-dcqcn_min_rate_mbps x]\n\t[-dcqcn_alpha_us x]\n\t[-dcqcn_rate_us x]\n\t[-dcqcn_cnp_us x]\n\t[-dcqcn_byte_counter bytes]\n\t[-dcqcn_fast_recovery_steps N]\n\t[-roce_rx_mode gbn|sp]\n\t[-roce_ooo_us x]\n\t[-roce_loss_trace_window_pkts N]\n\t[-roce_loss_trace_window_ratio x]\n\t[-roce_ooo_window_pkts N]\n\t[-roce_ooo_window_ratio x]\n\t[-roce_bdp_bytes bytes]\n\t[-roce_nack_interval_us x]\n\t[-roce_rto_us x]\n\t[-roce_rto_high_us x]\n\t[-strat route_strategy (single,\n\tecmp_host,ecmp_ar,\n\tecmp_host_ar ar_thresh)]\n\t[-log log_level]\n\t[-seed random_seed]\n\t[-end end_time_in_usec]\n\t[-mtu MTU] default 4096\n\t[-linkspeed Mbps] default 400000\n\t[-hop_latency x] per hop wire latency in us, default 0.5\n\t[-switch_latency x] switching latency in us, default 0.5\n\t[-start_delta] time in us to randomly delay the start of connections\n\t[-slow_core_downlinks N]\n\t[-slow_core_downlink_divisor N]\n\t[-slow_tor_uplinks N]\n\t[-slow_tor_uplink_divisor N]\n\t[-ecn_thresh fraction]\n\t[-nmrc_bad_hold_down_us x]\n\t[-nmrc_state_mode default|4-state]\n\t[-nmrc_weak_sample_pkts N]\n\t[-nmrc_ecn_degrade aggressive|graded]\n\t[-mrc_active_paths N]\n\t[-mrc_backup_paths N]\n\t[-mrc_min_active_paths N]\n\t[-mrc_ecn_cooldown_us x]\n\t[-mrc_failed_retry_us x]\n\t[-mrc_probe_interval_pkts N]\n\t[-glb_update_us x]\n\t[-glb_gcn_update_us x]\n\t[-glb_gcn_aging_us x]\n\t[-glb_weights q_weight util_weight remote_busy_weight]\n\t[-glb_factors local_q local_util remote_q remote_util remote_busy]\n\t[-glb_normalize]\n\t[-glb_downstream_weight x]\n\t[-glb_quality_bucket x]\n\t[-glb_quality_levels N]\n\t[-glb_min_choices N]\n\t[-conweave_rtt_us x]\n\t[-ndp_cwnd pkts]\n\t[-pfc_thresholds low high]" << endl;
    cout << "\t[-roce_sack_bitmap_bits 64|128]" << endl;
    cout << "\t[-dcqcn_nack_reaction cnp|ignore|rate_cut]" << endl;
    cout << "\t[-nmrc_unknown_reopen]" << endl;
    cout << "\t[-glb_local_damping]" << endl;
    cout << "\t[-sglb_background] [-sglb_bg_rate_gbps x] [-sglb_bg_on_us x] "
         << "[-sglb_bg_off_us x] [-sglb_bg_packet_size bytes]" << endl;
    cout << "\t[-queue_cv_sample_us x]" << endl;
    exit(1);
}

int main(int argc, char **argv) {
    Clock c(timeFromSec(5 / 100.), eventlist);
    mem_b queuesize = DEFAULT_QUEUE_SIZE;
    linkspeed_bps linkspeed = speedFromMbps((double)REPS_LINKSPEED_MBPS);
    int packet_size = REPS_MTU_BYTES;
    uint32_t path_entropy_size = 10000000;
    uint32_t no_of_conns = 0, no_of_nodes = DEFAULT_NODES;
    uint32_t tiers = 3; // we support 2 and 3 tier fattrees     
    double logtime = 0.25; // ms;
    stringstream filename(ios_base::out);
    simtime_picosec hop_latency = timeFromUs(REPS_HOP_LATENCY_US);
    simtime_picosec switch_latency = timeFromUs(REPS_SWITCH_LATENCY_US);
    simtime_picosec start_delta = 0;
    queue_type qt = LOSSLESS_INPUT_ECN;
    float ar_sticky_delta = 10;
    uint32_t ar_granularity = FatTreeSwitch::PER_PACKET;
    RoceSrc::lb_mode_t roce_lb_mode = RoceSrc::LB_ECMP;
    RoceSrc::cc_mode_t roce_cc_mode = RoceSrc::CC_DCQCN_VARIANT;
    bool queue_user_set = false;
    bool queue_type_user_set = false;
    bool roce_rx_mode_user_set = false;
    bool roce_cc_mode_user_set = false;
    bool ecn_thresh_user_set = false;
    bool path_entropy_user_set = false;
    bool source_pathid_lb = false;

    queue_type snd_type = FAIR_PRIO;

    uint64_t high_pfc = 80, low_pfc = 20;
    bool pfc_user_set = false;
    uint32_t reps_buffer = 8;
    double conweave_rtt_us = 16.0;
    double conweave_min_reroute_us = 4.0;
    uint32_t ndp_cwnd = 256;
    uint32_t cc_iw_pkts = 0;
    uint32_t cc_min_cwnd_pkts = 1;
    uint32_t cc_max_cwnd_pkts = 0;
    RoceSrc::rx_mode_t roce_rx_mode = RoceSrc::RX_GBN;
    uint32_t roce_sack_bitmap_bits = ROCE_SACK_BITMAP_BITS_DEFAULT;
    double roce_ooo_us = 15.0;
    uint32_t roce_ooo_window_pkts = 32;
    double roce_ooo_window_ratio = 0.0;
    uint64_t roce_bdp_bytes = 0;
    double roce_nack_interval_us = 4.0;
    uint32_t roce_rto_us = 70;
    uint32_t roce_rto_high_us = 0;
    bool roce_ooo_user_set = false;
    bool roce_ooo_window_user_set = false;
    bool roce_ooo_ratio_user_set = false;
    bool roce_rto_user_set = false;
    bool roce_rto_high_user_set = false;
    uint32_t slow_core_downlinks = 0;
    uint32_t slow_core_downlink_divisor = 10;
    uint32_t slow_tor_uplinks = 0;
    uint32_t slow_tor_uplink_divisor = 2;
    bool sglb_background = false;
    double sglb_bg_rate_gbps = 350.0;
    double sglb_bg_on_us = 200.0;
    double sglb_bg_off_us = 200.0;
    uint32_t sglb_bg_packet_size = 0;
    double queue_cv_sample_us = 0.0;
    double nmrc_bad_hold_down_us = 0.0;
    uint32_t nmrc_state_mode = 3;
    uint32_t nmrc_bad_cache_windows = 1;
    uint32_t nmrc_weak_sample_pkts = 0;
    uint32_t nmrc_ecn_degrade_mode = 0;
    bool nmrc_unknown_reopen = true;
    double nmrc_feedback_min_us = 5.0;
    double nmrc_feedback_max_us = 20.0;
    double ecn_thresh = 1.0;
    uint32_t mrc_active_paths = 256;
    uint32_t mrc_backup_paths = 256;
    uint32_t mrc_min_active_paths = 16;
    double mrc_ecn_cooldown_us = 20.0;
    double mrc_failed_retry_us = 100.0;
    uint32_t mrc_probe_interval_pkts = 256;

    bool log_sink = false;
    bool log_tor_downqueue = false;
    bool log_tor_upqueue = false;
    bool log_traffic = false;
    bool log_switches = false;
    bool log_queue_usage = false;
    RouteStrategy route_strategy = NOT_SET;
    int seed = 13;
    int i = 1;
    filename << "logout.dat";
    int end_time = 1000;//in microseconds

    char* tm_file = NULL;
    char* topo_file = NULL;

    while (i<argc) {
        if (!strcmp(argv[i],"-o")) {
            filename.str(std::string());
            filename << argv[i+1];
            i++;
        } else if (!strcmp(argv[i],"-conns")) {
            no_of_conns = atoi(argv[i+1]);
            cout << "no_of_conns "<<no_of_conns << endl;
            i++;
        } else if (!strcmp(argv[i],"-end")) {
            end_time = atoi(argv[i+1]);
            cout << "endtime(us) "<< end_time << endl;
            i++;            
        } else if (!strcmp(argv[i],"-nodes")) {
            no_of_nodes = atoi(argv[i+1]);
            cout << "no_of_nodes "<<no_of_nodes << endl;
            i++;
        } else if (!strcmp(argv[i],"-tiers")) {
            tiers = atoi(argv[i+1]);
            cout << "tiers "<< tiers << endl;
            assert(tiers == 2 || tiers == 3);
            i++;
        } else if (!strcmp(argv[i],"-queue_type")) {
            if (!strcmp(argv[i+1], "composite")) {
                qt = COMPOSITE;
            } 
            else if (!strcmp(argv[i+1], "composite_ecn")) {
                qt = COMPOSITE_ECN;
            }
            else if (!strcmp(argv[i+1], "composite_ecn_lb") ||
                     !strcmp(argv[i+1], "mrc_trim_ecn") ||
                     !strcmp(argv[i+1], "trim_ecn")) {
                qt = COMPOSITE_ECN_LB;
            }
            else if (!strcmp(argv[i+1], "lossless")) {
                qt = LOSSLESS;
            }
            else if (!strcmp(argv[i+1], "lossless_input")) {
                qt = LOSSLESS_INPUT;
            }
            else if (!strcmp(argv[i+1], "lossless_input_ecn")) {
                qt = LOSSLESS_INPUT_ECN;
            }
            else if (!strcmp(argv[i+1], "lossy_input_ecn") ||
                     !strcmp(argv[i+1], "lossy_ecn")) {
                qt = LOSSY_INPUT_ECN;
            }
            else {
                cout << "Unknown queue type " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "queue_type "<< qt << endl;
            queue_type_user_set = true;
            i++;
        } else if (!strcmp(argv[i],"-host_queue_type")) {
            if (!strcmp(argv[i+1], "swift")) {
                snd_type = SWIFT_SCHEDULER;
            } 
            else if (!strcmp(argv[i+1], "prio")) {
                snd_type = PRIORITY;
            }
            else if (!strcmp(argv[i+1], "fair_prio")) {
                snd_type = FAIR_PRIO;
            }
            else {
                cout << "Unknown host queue type " << argv[i+1] << " expecting one of swift|prio|fair_prio" << endl;
                exit_error(argv[0]);
            }
            cout << "host queue_type "<< snd_type << endl;
            i++;
        } else if (!strcmp(argv[i],"-log")){
            if (!strcmp(argv[i+1], "sink")) {
                log_sink = true;
            } else if (!strcmp(argv[i+1], "sink")) {
                cout << "logging sinks\n";
                log_sink = true;
            } else if (!strcmp(argv[i+1], "tor_downqueue")) {
                cout << "logging tor downqueues\n";
                log_tor_downqueue = true;
            } else if (!strcmp(argv[i+1], "tor_upqueue")) {
                cout << "logging tor upqueues\n";
                log_tor_upqueue = true;
            } else if (!strcmp(argv[i+1], "switch")) {
                cout << "logging total switch queues\n";
                log_switches = true;
            } else if (!strcmp(argv[i+1], "traffic")) {
                cout << "logging traffic\n";
                log_traffic = true;
            } else if (!strcmp(argv[i+1], "queue_usage")) {
                cout << "logging queue usage\n";
                log_queue_usage = true;
            } else {
                exit_error(argv[0]);
            }
            i++;
        } else if (!strcmp(argv[i],"-tm")){
            tm_file = argv[i+1];
            cout << "traffic matrix input file: "<< tm_file << endl;
            i++;
        } else if (!strcmp(argv[i],"-topo")){
            topo_file = argv[i+1];
            cout << "FatTree topology input file: "<< topo_file << endl;
            i++;
        } else if (!strcmp(argv[i],"-q")){
            queuesize = atoi(argv[i+1]);
            queue_user_set = true;
            i++;
        } else if (!strcmp(argv[i],"-lb")){
            const char* lb_scheme_name = "ecmp";
            if (!strcmp(argv[i+1], "ecmp")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_ECMP;
                lb_scheme_name = "ecmp";
            } else if (!strcmp(argv[i+1], "ecmp_rr")) {
                route_strategy = ECMP_FIB;
                path_entropy_size = 1;
                FatTreeSwitch::set_strategy(FatTreeSwitch::RR);
                roce_lb_mode = RoceSrc::LB_ECMP;
                lb_scheme_name = "ecmp_rr";
            } else if (!strcmp(argv[i+1], "glb")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::GLB);
                roce_lb_mode = RoceSrc::LB_ECMP;
                lb_scheme_name = "glb";
            } else if (!strcmp(argv[i+1], "reps")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_REPS;
                lb_scheme_name = "reps";
            } else if (!strcmp(argv[i+1], "n-mrc")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_NMRC;
                lb_scheme_name = "n-mrc";
            } else if (!strcmp(argv[i+1], "mrc")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_MRC;
                lb_scheme_name = "mrc";
            } else if (!strcmp(argv[i+1], "rr")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_RR;
                lb_scheme_name = "rr";
            } else if (!strcmp(argv[i+1], "ops")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_OPS;
                lb_scheme_name = "ops";
            } else if (!strcmp(argv[i+1], "conweave")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_CONWEAVE;
                lb_scheme_name = "conweave";
            } else if (!strcmp(argv[i+1], "ndp")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_NDP;
                lb_scheme_name = "ndp";
            } else if (!strcmp(argv[i+1], "adaptive-routing")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ADAPTIVE_ROUTING);
                roce_lb_mode = RoceSrc::LB_ECMP;
                lb_scheme_name = "adaptive-routing";
            } else if (!strcmp(argv[i+1], "drill")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::DRILL);
                roce_lb_mode = RoceSrc::LB_ECMP;
                lb_scheme_name = "drill";
            } else {
                cout << "Unknown lb mode " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "lb mode " << lb_scheme_name << endl;
            i++;
        } else if (!strcmp(argv[i],"-cc")){
            if (!strcmp(argv[i+1], "none")) {
                roce_cc_mode = RoceSrc::CC_NONE;
            } else if (!strcmp(argv[i+1], "dcqcn")) {
                roce_cc_mode = RoceSrc::CC_DCQCN;
            } else if (!strcmp(argv[i+1], "dcqcn_variant") ||
                       !strcmp(argv[i+1], "dcqcn-variant") ||
                       !strcmp(argv[i+1], "mprdma") ||
                       !strcmp(argv[i+1], "dctcp")) {
                roce_cc_mode = RoceSrc::CC_DCQCN_VARIANT;
            } else {
                cout << "Unknown cc mode " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "cc mode " << argv[i+1] << endl;
            roce_cc_mode_user_set = true;
            i++;
        } else if (!strcmp(argv[i],"-logtime")){
            logtime = atof(argv[i+1]);            
            cout << "logtime "<< logtime << " ms" << endl;
            i++;
        } else if (!strcmp(argv[i],"-linkspeed")){
            // linkspeed specified is in Mbps
            linkspeed = speedFromMbps(atof(argv[i+1]));
            i++;
        } else if (!strcmp(argv[i],"-seed")){
            seed = atoi(argv[i+1]);
            cout << "random seed "<< seed << endl;
            i++;
        } else if (!strcmp(argv[i],"-mtu")){
            packet_size = atoi(argv[i+1]);
            i++;
        } else if (!strcmp(argv[i],"-paths")){
            path_entropy_size = atoi(argv[i+1]);
            path_entropy_user_set = true;
            cout << "no of paths " << path_entropy_size << endl;
            i++;
        } else if (!strcmp(argv[i],"-hop_latency")){
            hop_latency = timeFromUs(atof(argv[i+1]));
            cout << "Hop latency set to " << timeAsUs(hop_latency) << endl;
            i++;
        } else if (!strcmp(argv[i],"-switch_latency")){
            switch_latency = timeFromUs(atof(argv[i+1]));
            cout << "Switch latency set to " << timeAsUs(switch_latency) << endl;
            i++;
        } else if (!strcmp(argv[i],"-start_delta")){
            start_delta = atof(argv[i+1]);
            cout << "Start connectios with a random delay of upto " << start_delta << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-ar_sticky_delta")){
            ar_sticky_delta = atof(argv[i+1]);
            cout << "Adaptive routing sticky delta " << ar_sticky_delta << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-ar_granularity")){
            if (!strcmp(argv[i+1], "packet")) {
                ar_granularity = FatTreeSwitch::PER_PACKET;
            } else if (!strcmp(argv[i+1], "flowlet")) {
                ar_granularity = FatTreeSwitch::PER_FLOWLET;
            } else {
                cout << "Unknown AR granularity expecting packet or flowlet" << endl;
                exit(1);
            }
            cout << "Adaptive routing granularity " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-reps_buffer")){
            reps_buffer = atoi(argv[i+1]);
            cout << "reps buffer size " << reps_buffer << endl;
            i++;
        } else if (!strcmp(argv[i],"-conweave_rtt_us")){
            conweave_rtt_us = atof(argv[i+1]);
            cout << "conweave RTT reroute threshold " << conweave_rtt_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-conweave_min_reroute_us")){
            conweave_min_reroute_us = atof(argv[i+1]);
            cout << "conweave minimum reroute gap " << conweave_min_reroute_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-ndp_cwnd")){
            ndp_cwnd = atoi(argv[i+1]);
            if (!ndp_cwnd)
                ndp_cwnd = 1;
            cout << "ndp initial receiver-pull window " << ndp_cwnd << " packets" << endl;
            i++;
        } else if (!strcmp(argv[i],"-ecn_thresh")){
            ecn_thresh = atof(argv[i+1]);
            if (ecn_thresh < 0.0)
                ecn_thresh = 0.0;
            ecn_thresh_user_set = true;
            cout << "Composite ECN threshold fraction " << ecn_thresh << endl;
            i++;
        } else if (!strcmp(argv[i],"-mrc_active_paths")){
            mrc_active_paths = atoi(argv[i+1]);
            cout << "MRC active EV set size " << mrc_active_paths << endl;
            i++;
        } else if (!strcmp(argv[i],"-mrc_backup_paths")){
            mrc_backup_paths = atoi(argv[i+1]);
            cout << "MRC backup EV set size " << mrc_backup_paths << endl;
            i++;
        } else if (!strcmp(argv[i],"-mrc_min_active_paths")){
            mrc_min_active_paths = atoi(argv[i+1]);
            if (!mrc_min_active_paths)
                mrc_min_active_paths = 1;
            cout << "MRC minimum active EVs " << mrc_min_active_paths << endl;
            i++;
        } else if (!strcmp(argv[i],"-mrc_ecn_cooldown_us")){
            mrc_ecn_cooldown_us = atof(argv[i+1]);
            if (mrc_ecn_cooldown_us < 0)
                mrc_ecn_cooldown_us = 0;
            cout << "MRC ECN cooldown " << mrc_ecn_cooldown_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-mrc_failed_retry_us")){
            mrc_failed_retry_us = atof(argv[i+1]);
            if (mrc_failed_retry_us < 0)
                mrc_failed_retry_us = 0;
            cout << "MRC failed-path retry " << mrc_failed_retry_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-mrc_probe_interval_pkts")){
            mrc_probe_interval_pkts = atoi(argv[i+1]);
            cout << "MRC background probe interval " << mrc_probe_interval_pkts << " packets" << endl;
            i++;
        } else if (!strcmp(argv[i],"-roce_rx_mode")){
            if (!strcmp(argv[i+1], "gbn") || !strcmp(argv[i+1], "default")) {
                roce_rx_mode = RoceSrc::RX_GBN;
            } else if (!strcmp(argv[i+1], "sp") || !strcmp(argv[i+1], "sack") ||
                       !strcmp(argv[i+1], "selective")) {
                roce_rx_mode = RoceSrc::RX_SP_RETX_QUEUE;
            } else {
                cout << "Unknown RoCE receive mode " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "RoCE receive mode " << argv[i+1] << endl;
            roce_rx_mode_user_set = true;
            i++;
        } else if (!strcmp(argv[i],"-roce_sack_bitmap_bits")){
            int requested_bits = atoi(argv[i+1]);
            uint32_t normalized_bits = requested_bits > 0 ?
                RoceSrc::normalizeSackBitmapBits((uint32_t)requested_bits) :
                ROCE_SACK_BITMAP_BITS_DEFAULT;
            roce_sack_bitmap_bits = normalized_bits;
            cout << "RoCE SACK bitmap " << roce_sack_bitmap_bits << " bits";
            if ((uint32_t)(requested_bits > 0 ? requested_bits : 0) != roce_sack_bitmap_bits)
                cout << " (supported values are 64 and 128)";
            cout << endl;
            i++;
        } else if (!strcmp(argv[i],"-roce_ooo_us")){
            roce_ooo_us = atof(argv[i+1]);
            if (roce_ooo_us < 0)
                roce_ooo_us = 0;
            roce_ooo_user_set = true;
            cout << "RoCE OOO tolerance " << roce_ooo_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-roce_ooo_window_pkts") ||
                   !strcmp(argv[i],"-roce_loss_trace_window_pkts")){
            roce_ooo_window_pkts = atoi(argv[i+1]);
            if (!roce_ooo_window_pkts)
                roce_ooo_window_pkts = 1;
            roce_ooo_window_user_set = true;
            cout << "RoCE loss trace window " << roce_ooo_window_pkts << " packets" << endl;
            i++;
        } else if (!strcmp(argv[i],"-roce_ooo_window_ratio") ||
                   !strcmp(argv[i],"-roce_loss_trace_window_ratio")){
            roce_ooo_window_ratio = atof(argv[i+1]);
            if (roce_ooo_window_ratio < 0)
                roce_ooo_window_ratio = 0;
            roce_ooo_ratio_user_set = true;
            cout << "RoCE loss trace window ratio " << roce_ooo_window_ratio << " BDP" << endl;
            i++;
        } else if (!strcmp(argv[i],"-roce_bdp_bytes")){
            roce_bdp_bytes = strtoull(argv[i+1], NULL, 10);
            cout << "RoCE BDP override " << roce_bdp_bytes << " bytes" << endl;
            i++;
        } else if (!strcmp(argv[i],"-roce_nack_interval_us")){
            roce_nack_interval_us = atof(argv[i+1]);
            if (roce_nack_interval_us < 0)
                roce_nack_interval_us = 0;
            cout << "RoCE NACK interval " << roce_nack_interval_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-roce_rto_us")){
            roce_rto_us = atoi(argv[i+1]);
            if (!roce_rto_us)
                roce_rto_us = 1;
            roce_rto_user_set = true;
            cout << "RoCE RTO " << roce_rto_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-roce_rto_high_us")){
            roce_rto_high_us = atoi(argv[i+1]);
            roce_rto_high_user_set = true;
            cout << "RoCE high RTO " << roce_rto_high_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-cc_iw_pkts")){
            cc_iw_pkts = atoi(argv[i+1]);
            if (!cc_iw_pkts)
                cc_iw_pkts = 1;
            cout << "CC initial window " << cc_iw_pkts << " packets" << endl;
            i++;
        } else if (!strcmp(argv[i],"-cc_min_cwnd_pkts")){
            cc_min_cwnd_pkts = atoi(argv[i+1]);
            if (!cc_min_cwnd_pkts)
                cc_min_cwnd_pkts = 1;
            cout << "CC minimum window " << cc_min_cwnd_pkts << " packets" << endl;
            i++;
        } else if (!strcmp(argv[i],"-cc_max_cwnd_pkts")){
            cc_max_cwnd_pkts = atoi(argv[i+1]);
            cout << "CC maximum window " << cc_max_cwnd_pkts << " packets" << endl;
            i++;
        } else if (!strcmp(argv[i],"-dcqcn_g")){
            double g = atof(argv[i+1]);
            if (g < 0)
                g = 0;
            RoceSrc::setDcqcnG(g);
            cout << "DCQCN g " << g << endl;
            i++;
        } else if (!strcmp(argv[i],"-dcqcn_initial_alpha")){
            double alpha = atof(argv[i+1]);
            if (alpha < 0)
                alpha = 0;
            if (alpha > 1)
                alpha = 1;
            RoceSrc::setDcqcnInitialAlpha(alpha);
            cout << "DCQCN initial alpha " << alpha << endl;
            i++;
        } else if (!strcmp(argv[i],"-dcqcn_ai_mbps")){
            double ai_mbps = atof(argv[i+1]);
            if (ai_mbps < 0)
                ai_mbps = 0;
            RoceSrc::setDcqcnAiRate(speedFromMbps(ai_mbps));
            cout << "DCQCN additive increase " << ai_mbps << "Mbps" << endl;
            i++;
        } else if (!strcmp(argv[i],"-dcqcn_min_rate_mbps")){
            double min_mbps = atof(argv[i+1]);
            if (min_mbps < 0)
                min_mbps = 0;
            RoceSrc::setDcqcnMinRate(speedFromMbps(min_mbps));
            cout << "DCQCN minimum rate " << min_mbps << "Mbps" << endl;
            i++;
        } else if (!strcmp(argv[i],"-dcqcn_alpha_us")){
            double alpha_us = atof(argv[i+1]);
            if (alpha_us < 0)
                alpha_us = 0;
            RoceSrc::setDcqcnAlphaInterval(timeFromUs(alpha_us));
            cout << "DCQCN alpha interval " << alpha_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-dcqcn_rate_us")){
            double rate_us = atof(argv[i+1]);
            if (rate_us < 0)
                rate_us = 0;
            RoceSrc::setDcqcnRateIncreaseInterval(timeFromUs(rate_us));
            cout << "DCQCN rate increase interval " << rate_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-dcqcn_cnp_us")){
            double cnp_us = atof(argv[i+1]);
            if (cnp_us < 0)
                cnp_us = 0;
            RoceSrc::setDcqcnCnpInterval(timeFromUs(cnp_us));
            cout << "DCQCN CNP interval " << cnp_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-dcqcn_byte_counter")){
            mem_b bytes = atoll(argv[i+1]);
            if (bytes < 0)
                bytes = 0;
            RoceSrc::setDcqcnByteCounter(bytes);
            cout << "DCQCN byte counter " << bytes << " bytes" << endl;
            i++;
        } else if (!strcmp(argv[i],"-dcqcn_fast_recovery_steps")){
            uint32_t steps = atoi(argv[i+1]);
            RoceSrc::setDcqcnFastRecoverySteps(steps);
            cout << "DCQCN fast recovery steps " << steps << endl;
            i++;
        } else if (!strcmp(argv[i],"-dcqcn_nack_reaction")){
            if (!strcmp(argv[i+1], "cnp")) {
                RoceSrc::setDcqcnNackReaction(RoceSrc::DCQCN_NACK_CNP);
            } else if (!strcmp(argv[i+1], "ignore")) {
                RoceSrc::setDcqcnNackReaction(RoceSrc::DCQCN_NACK_IGNORE);
            } else if (!strcmp(argv[i+1], "rate_cut")) {
                RoceSrc::setDcqcnNackReaction(RoceSrc::DCQCN_NACK_RATE_CUT);
            } else {
                cout << "Unknown DCQCN NACK reaction " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "DCQCN NACK reaction " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-slow_core_downlinks")){
            slow_core_downlinks = atoi(argv[i+1]);
            cout << "Slow core-to-agg downlinks " << slow_core_downlinks << endl;
            i++;
        } else if (!strcmp(argv[i],"-slow_core_downlink_divisor")){
            slow_core_downlink_divisor = atoi(argv[i+1]);
            if (!slow_core_downlink_divisor)
                slow_core_downlink_divisor = 1;
            cout << "Slow core-to-agg downlink divisor " << slow_core_downlink_divisor << endl;
            i++;
        } else if (!strcmp(argv[i],"-slow_tor_uplinks")){
            slow_tor_uplinks = atoi(argv[i+1]);
            cout << "Slow ToR-to-agg uplinks " << slow_tor_uplinks << endl;
            i++;
        } else if (!strcmp(argv[i],"-slow_tor_uplink_divisor")){
            slow_tor_uplink_divisor = atoi(argv[i+1]);
            if (!slow_tor_uplink_divisor)
                slow_tor_uplink_divisor = 1;
            cout << "Slow ToR-to-agg uplink divisor " << slow_tor_uplink_divisor << endl;
            i++;
        } else if (!strcmp(argv[i],"-sglb_background")){
            sglb_background = true;
            cout << "SGLB fixed-link background enabled" << endl;
        } else if (!strcmp(argv[i],"-sglb_bg_rate_gbps")){
            sglb_bg_rate_gbps = atof(argv[i+1]);
            if (sglb_bg_rate_gbps < 0.0)
                sglb_bg_rate_gbps = 0.0;
            cout << "SGLB background rate " << sglb_bg_rate_gbps << "Gbps" << endl;
            i++;
        } else if (!strcmp(argv[i],"-sglb_bg_on_us")){
            sglb_bg_on_us = atof(argv[i+1]);
            if (sglb_bg_on_us < 0.0)
                sglb_bg_on_us = 0.0;
            cout << "SGLB background ON " << sglb_bg_on_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-sglb_bg_off_us")){
            sglb_bg_off_us = atof(argv[i+1]);
            if (sglb_bg_off_us < 0.0)
                sglb_bg_off_us = 0.0;
            cout << "SGLB background OFF " << sglb_bg_off_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-sglb_bg_packet_size")){
            sglb_bg_packet_size = atoi(argv[i+1]);
            cout << "SGLB background packet size " << sglb_bg_packet_size << " bytes" << endl;
            i++;
        } else if (!strcmp(argv[i],"-queue_cv_sample_us")){
            queue_cv_sample_us = atof(argv[i+1]);
            if (queue_cv_sample_us < 0.0)
                queue_cv_sample_us = 0.0;
            cout << "Queue CV sample interval " << queue_cv_sample_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-glb_update_us")){
            double glb_update_us = atof(argv[i+1]);
            if (glb_update_us < 0)
                glb_update_us = 0;
            FatTreeSwitch::_glb_update_interval = timeFromUs(glb_update_us);
            FatTreeSwitch::_glb_gcn_update_interval = FatTreeSwitch::_glb_update_interval;
            cout << "glb GCN export update interval " << glb_update_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-glb_gcn_update_us")){
            double glb_update_us = atof(argv[i+1]);
            if (glb_update_us < 0)
                glb_update_us = 0;
            FatTreeSwitch::_glb_gcn_update_interval = timeFromUs(glb_update_us);
            FatTreeSwitch::_glb_update_interval = FatTreeSwitch::_glb_gcn_update_interval;
            cout << "glb GCN export update interval " << glb_update_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-glb_gcn_aging_us")){
            double glb_aging_us = atof(argv[i+1]);
            if (glb_aging_us < 0)
                glb_aging_us = 0;
            FatTreeSwitch::_glb_gcn_aging_interval = timeFromUs(glb_aging_us);
            cout << "glb GCN aging interval " << glb_aging_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-glb_weights")){
            FatTreeSwitch::_glb_queue_weight = atof(argv[i+1]);
            FatTreeSwitch::_glb_util_weight = atof(argv[i+2]);
            FatTreeSwitch::_glb_remote_queue_weight = FatTreeSwitch::_glb_queue_weight;
            FatTreeSwitch::_glb_remote_util_weight = FatTreeSwitch::_glb_util_weight;
            FatTreeSwitch::_glb_remote_busy_weight = atof(argv[i+3]);
            cout << "glb weights queue " << FatTreeSwitch::_glb_queue_weight
                 << " util " << FatTreeSwitch::_glb_util_weight
                 << " remote_busy " << FatTreeSwitch::_glb_remote_busy_weight << endl;
            i += 3;
        } else if (!strcmp(argv[i],"-glb_factors")){
            FatTreeSwitch::_glb_queue_weight = atof(argv[i+1]);
            FatTreeSwitch::_glb_util_weight = atof(argv[i+2]);
            FatTreeSwitch::_glb_remote_queue_weight = atof(argv[i+3]);
            FatTreeSwitch::_glb_remote_util_weight = atof(argv[i+4]);
            FatTreeSwitch::_glb_remote_busy_weight = atof(argv[i+5]);
            cout << "glb factors local_q " << FatTreeSwitch::_glb_queue_weight
                 << " local_util " << FatTreeSwitch::_glb_util_weight
                 << " remote_q " << FatTreeSwitch::_glb_remote_queue_weight
                 << " remote_util " << FatTreeSwitch::_glb_remote_util_weight
                 << " remote_busy " << FatTreeSwitch::_glb_remote_busy_weight << endl;
            i += 5;
        } else if (!strcmp(argv[i],"-glb_normalize")){
            FatTreeSwitch::_glb_normalize_scores = true;
            cout << "glb normalized queue/util factors enabled" << endl;
        } else if (!strcmp(argv[i],"-glb_local_damping")){
            FatTreeSwitch::_glb_local_damping = true;
            cout << "glb local-pressure downstream damping enabled" << endl;
        } else if (!strcmp(argv[i],"-glb_downstream_weight")){
            FatTreeSwitch::_glb_downstream_weight = atof(argv[i+1]);
            cout << "glb downstream weight " << FatTreeSwitch::_glb_downstream_weight << endl;
            i++;
        } else if (!strcmp(argv[i],"-glb_quality_bucket")){
            FatTreeSwitch::_glb_quality_bucket = atof(argv[i+1]);
            if (FatTreeSwitch::_glb_quality_bucket <= 0.0)
                FatTreeSwitch::_glb_quality_bucket = 1.0;
            cout << "glb quality bucket " << FatTreeSwitch::_glb_quality_bucket << endl;
            i++;
        } else if (!strcmp(argv[i],"-glb_quality_levels")){
            FatTreeSwitch::_glb_quality_levels = atoi(argv[i+1]);
            if (!FatTreeSwitch::_glb_quality_levels)
                FatTreeSwitch::_glb_quality_levels = 1;
            FatTreeSwitch::_glb_max_quality = FatTreeSwitch::_glb_quality_levels - 1;
            cout << "glb quality levels " << FatTreeSwitch::_glb_quality_levels << endl;
            i++;
        } else if (!strcmp(argv[i],"-glb_min_choices")){
            FatTreeSwitch::_glb_min_choices = atoi(argv[i+1]);
            if (!FatTreeSwitch::_glb_min_choices)
                FatTreeSwitch::_glb_min_choices = 1;
            cout << "glb minimum sprayed choices " << FatTreeSwitch::_glb_min_choices << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_feedback_pkts")){
            cout << "n-mrc feedback packet threshold is canonical auto(path_count); ignoring deprecated value " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_feedback_hot_path_pkts")){
            cout << "n-mrc hot-path feedback threshold is disabled; ignoring deprecated value " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_feedback_min_us")){
            nmrc_feedback_min_us = atof(argv[i+1]);
            if (nmrc_feedback_min_us < 0)
                nmrc_feedback_min_us = 0;
            cout << "n-mrc minimum feedback interval " << nmrc_feedback_min_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_feedback_max_us")){
            nmrc_feedback_max_us = atof(argv[i+1]);
            if (nmrc_feedback_max_us < 0)
                nmrc_feedback_max_us = 0;
            cout << "n-mrc maximum feedback interval " << nmrc_feedback_max_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_min_good_paths")){
            cout << "n-mrc minimum confirmed good paths is canonical min(16,path_count/2); ignoring deprecated value " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_bad_hold_down_us")){
            nmrc_bad_hold_down_us = atof(argv[i+1]);
            if (nmrc_bad_hold_down_us < 0)
                nmrc_bad_hold_down_us = 0;
            cout << "n-mrc bad path keeping window " << nmrc_bad_hold_down_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_state_mode")){
            if (!strcmp(argv[i+1], "default") ||
                !strcmp(argv[i+1], "2state-bad-cache") ||
                !strcmp(argv[i+1], "bad-cache") ||
                !strcmp(argv[i+1], "bad-cache-a")) {
                nmrc_state_mode = 3;
                nmrc_bad_cache_windows = 1;
            }
            else if (!strcmp(argv[i+1], "4-state") ||
                     !strcmp(argv[i+1], "4state") ||
                     !strcmp(argv[i+1], "legacy") ||
                     !strcmp(argv[i+1], "2bit-ecn01"))
                nmrc_state_mode = 2;
            else if (!strcmp(argv[i+1], "binary"))
                nmrc_state_mode = 0;
            else if (!strcmp(argv[i+1], "2bit-observed"))
                nmrc_state_mode = 1;
            else {
                cout << "Unknown n-mrc state mode " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "n-mrc endpoint state mode " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_bad_cache_windows")){
            nmrc_bad_cache_windows = 1;
            nmrc_state_mode = 3;
            cout << "n-mrc bad-cache uses canonical one-window mode; ignoring deprecated value "
                 << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_weak_sample_pkts")){
            nmrc_weak_sample_pkts = atoi(argv[i+1]);
            cout << "n-mrc weak path sampling interval " << nmrc_weak_sample_pkts << " packets" << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_ecn_degrade")){
            if (!strcmp(argv[i+1], "aggressive"))
                nmrc_ecn_degrade_mode = 0;
            else if (!strcmp(argv[i+1], "graded"))
                nmrc_ecn_degrade_mode = 1;
            else {
                cout << "Unknown n-mrc ECN degrade mode " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "n-mrc ECN degrade mode " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_unknown_reopen")){
            nmrc_unknown_reopen = true;
            cout << "n-mrc unknown low-state reopen enabled" << endl;
        } else if (!strcmp(argv[i],"-nmrc_select")){
            if (strcmp(argv[i+1], "random") && strcmp(argv[i+1], "rr")) {
                cout << "Unknown n-mrc select mode " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "n-mrc select mode is canonical rr; ignoring deprecated value " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_ack_update")){
            cout << "n-mrc per-ACK path update is disabled in canonical mode; ignoring deprecated value " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_path_hash")){
            if (strcmp(argv[i+1], "hash") && strcmp(argv[i+1], "direct") && strcmp(argv[i+1], "tier")) {
                cout << "Unknown n-mrc path hash mode " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "n-mrc path hash is canonical tier EV mapping; ignoring deprecated value " << argv[i+1] << endl;
            i++;
        }
         else if (!strcmp(argv[i],"-pfc_thresholds")){
            low_pfc = atoi(argv[i+1]);
            high_pfc = atoi(argv[i+2]);
            pfc_user_set = true;
            cout << "PFC thresholds high " << high_pfc << " low " << low_pfc << endl;
            i+=2;
        } else if (!strcmp(argv[i],"-ar_method")){
            if (!strcmp(argv[i+1],"pause")){
                cout << "Adaptive routing based on pause state " << endl;
                FatTreeSwitch::fn = &FatTreeSwitch::compare_pause;
            }
            else if (!strcmp(argv[i+1],"queue")){
                cout << "Adaptive routing based on queue size " << endl;
                FatTreeSwitch::fn = &FatTreeSwitch::compare_queuesize;
            }
            else if (!strcmp(argv[i+1],"bandwidth")){
                cout << "Adaptive routing based on bandwidth utilization " << endl;
                FatTreeSwitch::fn = &FatTreeSwitch::compare_bandwidth;
            }
            else if (!strcmp(argv[i+1],"flowcount")){
                cout << "Adaptive routing based on bandwidth utilization " << endl;
                FatTreeSwitch::fn = &FatTreeSwitch::compare_flow_count;
            }
            else if (!strcmp(argv[i+1],"pqb")){
                cout << "Adaptive routing based on pause, queuesize and bandwidth utilization " << endl;
                FatTreeSwitch::fn = &FatTreeSwitch::compare_pqb;
            }
            else if (!strcmp(argv[i+1],"pq")){
                cout << "Adaptive routing based on pause, queuesize" << endl;
                FatTreeSwitch::fn = &FatTreeSwitch::compare_pq;
            }
            else if (!strcmp(argv[i+1],"pb")){
                cout << "Adaptive routing based on pause, bandwidth utilization" << endl;
                FatTreeSwitch::fn = &FatTreeSwitch::compare_pb;
            }
            else if (!strcmp(argv[i+1],"qb")){
                cout << "Adaptive routing based on queuesize, bandwidth utilization" << endl;
                FatTreeSwitch::fn = &FatTreeSwitch::compare_qb; 
            }
            else {
                cout << "Unknown AR method expecting one of pause, queue, bandwidth, pqb, pq, pb, qb" << endl;
                exit(1);
            }
            i++;
        }  else if (!strcmp(argv[i],"-strat")){
            if (!strcmp(argv[i+1], "perm")) {
                route_strategy = SCATTER_PERMUTE;
            } else if (!strcmp(argv[i+1], "rand")) {
                route_strategy = SCATTER_RANDOM;
            } else if (!strcmp(argv[i+1], "ecmp")) {
                route_strategy = SCATTER_ECMP;
            } else if (!strcmp(argv[i+1], "pull")) {
                route_strategy = PULL_BASED;
            } else if (!strcmp(argv[i+1], "single")) {
                route_strategy = SINGLE_PATH;
            } else if (!strcmp(argv[i+1], "ecmp_host")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
            } else if (!strcmp(argv[i+1], "ecmp_ar")) {
                route_strategy = ECMP_FIB;
                path_entropy_size = 1;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ADAPTIVE_ROUTING);
            } else if (!strcmp(argv[i+1], "ecmp_host_ar")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP_ADAPTIVE);
                FatTreeSwitch::set_ar_fraction(atoi(argv[i+2]));
                cout << "AR fraction: " << atoi(argv[i+2]) << endl;
                i++;
            } else if (!strcmp(argv[i+1], "ecmp_rr")) {
                route_strategy = ECMP_FIB;
                path_entropy_size = 1;
                FatTreeSwitch::set_strategy(FatTreeSwitch::RR);
            }
            i++;
        } else {
            exit_error(argv[0]);
        }
                
        i++;
    }

    srand(seed);
    srandom(seed);

    cout << "Parsed args\n";
    if (!sglb_bg_packet_size)
        sglb_bg_packet_size = packet_size;
    Packet::set_packet_size(packet_size);

    if (roce_lb_mode == RoceSrc::LB_MRC) {
        if (!queue_type_user_set) {
            qt = COMPOSITE_ECN_LB;
            cout << "MRC default queue_type composite_ecn_lb (trim + priority headers + ECN)" << endl;
        }
        if (!roce_rx_mode_user_set) {
            roce_rx_mode = RoceSrc::RX_SP_RETX_QUEUE;
            cout << "MRC default receive mode sp (SACK/selective retransmission)" << endl;
        }
        if (!roce_cc_mode_user_set) {
            roce_cc_mode = RoceSrc::CC_NONE;
            cout << "MRC default cc none (ECN drives path avoidance, not DCQCN rate control)" << endl;
        }
        if (!ecn_thresh_user_set) {
            ecn_thresh = 0.8;
            cout << "MRC default composite ECN threshold fraction " << ecn_thresh << endl;
        }
    }

    source_pathid_lb = (roce_lb_mode == RoceSrc::LB_NMRC ||
                        roce_lb_mode == RoceSrc::LB_MRC ||
                        roce_lb_mode == RoceSrc::LB_RR ||
                        roce_lb_mode == RoceSrc::LB_CONWEAVE ||
                        roce_lb_mode == RoceSrc::LB_NDP);
    if (source_pathid_lb && !path_entropy_user_set && path_entropy_size > 10000) {
        path_entropy_size = 1;
        cout << "source-controlled LB will auto-calibrate path count from topology" << endl;
    }

    uint32_t one_way_links = tiers == 3 ? 6 : 4;
    uint32_t one_way_switches = tiers == 3 ? 5 : 3;
    double estimated_rtt_us = 2.0 * (one_way_links * timeAsUs(hop_latency) +
                                     one_way_switches * timeAsUs(switch_latency));
    uint64_t estimated_bdp_bytes = roce_bdp_bytes;
    if (!estimated_bdp_bytes) {
        estimated_bdp_bytes = (uint64_t)(((double)linkspeed * estimated_rtt_us * 1e-6) / 8.0);
    }
    uint32_t estimated_bdp_pkts = (uint32_t)ceil((double)estimated_bdp_bytes /
                                                Packet::data_packet_size());
    if (!estimated_bdp_pkts)
        estimated_bdp_pkts = 1;

    if (!queue_user_set) {
        queuesize = estimated_bdp_pkts;
        cout << "reps default queue size 1BDP = " << queuesize
             << " packets (" << estimated_bdp_bytes << " bytes, estimated RTT "
             << estimated_rtt_us << "us)" << endl;
    }

    if (!pfc_user_set) {
        low_pfc = (uint64_t)ceil((double)queuesize * 0.2);
        high_pfc = (uint64_t)ceil((double)queuesize * 0.8);
        if (!low_pfc)
            low_pfc = 1;
        if (high_pfc <= low_pfc)
            high_pfc = low_pfc + 1;
        cout << "Default PFC thresholds scaled with queue: high "
             << high_pfc << " low " << low_pfc << " packets" << endl;
    }

    if (nmrc_feedback_max_us < nmrc_feedback_min_us)
        nmrc_feedback_max_us = nmrc_feedback_min_us;

    if (roce_rx_mode == RoceSrc::RX_SP_RETX_QUEUE) {
        if (!roce_rto_user_set) {
            roce_rto_us = 70;
        }
        if (!roce_rto_high_user_set)
            roce_rto_high_us = 0;
        if (!roce_ooo_user_set) {
            double kmax_drain_us = ((double)estimated_bdp_bytes * 0.8 * 8.0) /
                                   ((double)linkspeed * 1e-6);
            roce_ooo_us = estimated_rtt_us + kmax_drain_us;
        }
        if (!roce_ooo_window_user_set && !roce_ooo_ratio_user_set) {
            roce_ooo_window_ratio = 1.0;
        }
        cout << "RoCE SP effective RTO " << roce_rto_us << "us";
        if (roce_rto_high_us)
            cout << " (high " << roce_rto_high_us << "us)";
        cout << ", OOO tolerance " << roce_ooo_us
             << "us, NACK interval " << roce_nack_interval_us << "us" << endl;
        cout << "RoCE SP SACK feedback uses aPSN + bitmap_start_psn + valid_len + "
             << roce_sack_bitmap_bits << "-bit data bitmap" << endl;
    }

    if (roce_ooo_window_ratio > 0.0) {
        uint64_t bdp_bytes = roce_bdp_bytes;
        if (!bdp_bytes) {
            bdp_bytes = estimated_bdp_bytes;
            cout << "RoCE estimated RTT " << estimated_rtt_us
                 << "us, BDP " << bdp_bytes << " bytes" << endl;
        }
        roce_ooo_window_pkts = (uint32_t)ceil((roce_ooo_window_ratio * bdp_bytes) /
                                              Packet::data_packet_size());
        if (!roce_ooo_window_pkts)
            roce_ooo_window_pkts = 1;
        cout << "RoCE loss trace window from ratio " << roce_ooo_window_ratio
             << " = " << roce_ooo_window_pkts << " packets" << endl;
        if (roce_ooo_window_pkts > roce_sack_bitmap_bits) {
            cout << "RoCE loss trace window exceeds one SACK bitmap; "
                 << "offsetted SACK blocks report " << roce_sack_bitmap_bits
                 << " packets at a time" << endl;
        }
    } else if (roce_ooo_window_user_set && roce_ooo_window_pkts > roce_sack_bitmap_bits) {
        cout << "RoCE loss trace window " << roce_ooo_window_pkts
             << " packets exceeds one SACK bitmap; offsetted SACK blocks report "
             << roce_sack_bitmap_bits << " packets at a time" << endl;
    }

    FatTreeSwitch::_ar_sticky = ar_granularity;
    FatTreeSwitch::_sticky_delta = timeFromUs(ar_sticky_delta);
    FatTreeSwitch::_ecn_threshold_fraction = ecn_thresh;
    FatTreeSwitch::_nmrc_feedback_min_interval = timeFromUs(nmrc_feedback_min_us);
    FatTreeSwitch::_nmrc_feedback_max_interval = timeFromUs(nmrc_feedback_max_us);
    FatTreeSwitch::_pathid_only_hash = source_pathid_lb;
    if (FatTreeSwitch::_strategy == FatTreeSwitch::GLB) {
        cout << "GLB effective config: GCN update "
             << timeAsUs(FatTreeSwitch::_glb_gcn_update_interval)
             << "us, aging " << timeAsUs(FatTreeSwitch::_glb_gcn_aging_interval)
             << "us, quality levels " << FatTreeSwitch::_glb_quality_levels
             << ", bucket " << FatTreeSwitch::_glb_quality_bucket
             << ", min choices " << FatTreeSwitch::_glb_min_choices
             << ", downstream weight " << FatTreeSwitch::_glb_downstream_weight
             << ", local damping " << (FatTreeSwitch::_glb_local_damping ? "on" : "off")
             << endl;
    }

    RoceSrc::setLoadBalancing(roce_lb_mode);
    RoceSrc::setPathEntropySize(path_entropy_size);
    RoceSrc::setSackBitmapBits(roce_sack_bitmap_bits);
    RoceSrc::setReceiveMode(roce_rx_mode);
    RoceSrc::setOooTolerance(timeFromUs(roce_ooo_us));
    RoceSrc::setOooWindowPkts(roce_ooo_window_pkts);
    RoceSrc::setNackInterval(timeFromUs(roce_nack_interval_us));
    RoceSrc::setMinRTO(roce_rto_us);
    RoceSrc::setHighRTO(roce_rto_high_us);
    RoceSrc::setRepsBufferSize(reps_buffer);
    RoceSrc::setRepsWarmupPkts(roce_lb_mode == RoceSrc::LB_REPS ? estimated_bdp_pkts : 0);
    if (roce_lb_mode == RoceSrc::LB_REPS) {
        cout << "REPS warmup exploration " << estimated_bdp_pkts
             << " packets (1BDP)" << endl;
    }
    RoceSrc::setConweaveRttThreshold(timeFromUs(conweave_rtt_us));
    RoceSrc::setConweaveMinRerouteGap(timeFromUs(conweave_min_reroute_us));
    RoceSrc::setNdpInitialWindow(ndp_cwnd);
    RoceSrc::setCongestionControl(roce_cc_mode);
    if (!cc_iw_pkts) {
        if (roce_cc_mode == RoceSrc::CC_DCQCN ||
            roce_cc_mode == RoceSrc::CC_DCQCN_VARIANT) {
            cc_iw_pkts = (uint32_t)ceil(0.4 * (double)estimated_bdp_pkts);
            if (!cc_iw_pkts)
                cc_iw_pkts = 1;
            cout << "Default RoCE CC flight cap 0.4BDP = "
                 << cc_iw_pkts << " packets" << endl;
        } else {
            cc_iw_pkts = queuesize ? queuesize : 1;
        }
    }
    RoceSrc::setCcInitialWindow(cc_iw_pkts);
    RoceSrc::setCcMinWindow(cc_min_cwnd_pkts);
    RoceSrc::setCcMaxWindow(cc_max_cwnd_pkts);

    double roce_rto_scan_us = roce_rto_us / 10.0;
    if (roce_rto_scan_us < 1.0)
        roce_rto_scan_us = 1.0;
    RoceRtxTimerScanner roceRtxScanner(timeFromUs(roce_rto_scan_us), eventlist);

    LosslessInputQueue::_high_threshold = Packet::data_packet_size()*high_pfc;
    LosslessInputQueue::_low_threshold = Packet::data_packet_size()*low_pfc;

    eventlist.setEndtime(timeFromUs((uint32_t)end_time));
    queuesize = memFromPkt(queuesize);
    if (qt == LOSSY_INPUT_ECN) {
        cout << "Lossy input ECN queue: finite queue, no PFC, dequeue RED ECN Kmin "
             << (queuesize / 5) / Packet::data_packet_size()
             << " pkts, Kmax "
             << (queuesize * 4 / 5) / Packet::data_packet_size()
             << " pkts" << endl;
    }
    
    switch (route_strategy) {
    case ECMP_FIB:
    case SCATTER_ECMP:
        if (path_entropy_size > 10000) {
            fprintf(stderr, "Route Strategy is ecmp.  Must specify path count using -paths\n");
            exit(1);
        }
        break;
    case SINGLE_PATH:
        if (path_entropy_size < 10000 && path_entropy_size > 1) {
            fprintf(stderr, "Route Strategy is SINGLE_PATH, but multiple paths are specifiec using -paths\n");
            exit(1);
        }
        break;
    case NOT_SET:
        fprintf(stderr, "Route Strategy not set.  Use the -strat param.  \nValid values are perm, rand, pull, rg and single\n");
        exit(1);
    default:
        break;
    }

    // prepare the loggers

    cout << "Logging to " << filename.str() << endl;
    //Logfile 
    Logfile logfile(filename.str(), eventlist);

    logfile.setStartTime(timeFromSec(0));

    RoceSinkLoggerSampling sinkLogger = RoceSinkLoggerSampling(timeFromMs(logtime), eventlist);
    if (log_sink) {
        logfile.addLogger(sinkLogger);
    }
    RoceTrafficLogger traffic_logger = RoceTrafficLogger();
    if (log_traffic) {
        logfile.addLogger(traffic_logger);
    }

    RoceSrc* roceSrc;
    RoceSink* roceSnk;

    Route* routeout, *routein;

    QueueLoggerFactory *qlf = 0;
    if (log_tor_downqueue || log_tor_upqueue) {
        qlf = new QueueLoggerFactory(&logfile, QueueLoggerFactory::LOGGER_SAMPLING, eventlist);
        qlf->set_sample_period(timeFromUs(10.0));
    } else if (log_queue_usage) {
        qlf = new QueueLoggerFactory(&logfile, QueueLoggerFactory::LOGGER_EMPTY, eventlist);
        qlf->set_sample_period(timeFromUs(10.0));
    }
#ifdef FAT_TREE
    FatTreeTopology* top;
    if (topo_file) {
        if (slow_core_downlinks) {
            cerr << "-slow_core_downlinks is only supported with generated fat-tree topologies\n";
            exit(1);
        }
        if (slow_tor_uplinks) {
            cerr << "-slow_tor_uplinks is only supported with generated fat-tree topologies\n";
            exit(1);
        }
        top = FatTreeTopology::load(topo_file, qlf, eventlist, queuesize, qt, snd_type);
    } else {
        FatTreeTopology::set_tiers(tiers);
        FatTreeTopology::set_slow_link_divisor(slow_core_downlink_divisor);
        FatTreeTopology::set_slow_tor_uplinks(slow_tor_uplinks);
        FatTreeTopology::set_slow_tor_uplink_divisor(slow_tor_uplink_divisor);
        top = new FatTreeTopology(no_of_nodes, linkspeed, queuesize, qlf, 
                                               &eventlist,NULL,qt,hop_latency,switch_latency,snd_type,slow_core_downlinks);
    }
#endif

#ifdef OV_FAT_TREE
    OversubscribedFatTreeTopology* top = new OversubscribedFatTreeTopology(lf, &eventlist,ff);
#endif

#ifdef MH_FAT_TREE
    MultihomedFatTreeTopology* top = new MultihomedFatTreeTopology(lf, &eventlist,ff);
#endif

#ifdef STAR
    StarTopology* top = new StarTopology(lf, &eventlist,ff);
#endif

#ifdef BCUBE
    BCubeTopology* top = new BCubeTopology(lf, &eventlist,ff);
    cout << "BCUBE " << K << endl;
#endif

#ifdef VL2
    VL2Topology* top = new VL2Topology(lf, &eventlist,ff);
#endif

    if (log_switches) {
        top->add_switch_loggers(logfile, timeFromUs(20.0));
    }

    SglbBackgroundDropSink* sglb_bg_drop = NULL;
    vector<SglbBackgroundSource*> sglb_bg_sources;
    if (sglb_background) {
        sglb_bg_drop = new SglbBackgroundDropSink();
        uint32_t added = install_sglb_background(
            top, eventlist, sglb_bg_sources, sglb_bg_drop,
            sglb_bg_rate_gbps, sglb_bg_packet_size,
            timeFromUs(sglb_bg_on_us), timeFromUs(sglb_bg_off_us));
        cout << "SGLB background installed " << added << " fixed-link sources" << endl;
    }

    SglbQueueCvSampler* queue_cv_sampler = NULL;
    if (queue_cv_sample_us > 0.0) {
        queue_cv_sampler = new SglbQueueCvSampler(eventlist, top, timeFromUs(queue_cv_sample_us));
        queue_cv_sampler->start();
        cout << "Queue CV sampler enabled every " << queue_cv_sample_us << "us" << endl;
    }

    if (source_pathid_lb) {
        uint32_t path_combo = top->radix_up(TOR_TIER);
        if (top->get_tiers() == 3) {
            path_combo *= top->radix_up(AGG_TIER);
            path_combo *= top->bundlesize(CORE_TIER);
            path_combo *= top->bundlesize(AGG_TIER);
        } else {
            path_combo *= top->bundlesize(AGG_TIER);
        }

        if (path_combo > 0 && path_combo != path_entropy_size) {
            cout << "source-controlled tier path count adjusted from " << path_entropy_size
                 << " to " << path_combo
                 << " to match topology path combinations" << endl;
            path_entropy_size = path_combo;
            RoceSrc::setPathEntropySize(path_entropy_size);
        }
    }
    RoceSrc::setNmrcHostsPerTor(top->radix_down(TOR_TIER));

    uint32_t nmrc_path_space = path_entropy_size ? path_entropy_size : 1;
    FatTreeSwitch::_nmrc_path_count = nmrc_path_space;
    uint32_t auto_nmrc_feedback_pkts = nmrc_path_space / 2;
    if (auto_nmrc_feedback_pkts < 32)
        auto_nmrc_feedback_pkts = 32;
    if (auto_nmrc_feedback_pkts > 128)
        auto_nmrc_feedback_pkts = 128;

    FatTreeSwitch::_nmrc_feedback_pkts = auto_nmrc_feedback_pkts;
    FatTreeSwitch::_nmrc_feedback_bad_only = nmrc_state_mode == 3;
    FatTreeSwitch::_nmrc_feedback_observed_values = !FatTreeSwitch::_nmrc_feedback_bad_only &&
                                                    (nmrc_state_mode == 1 || nmrc_state_mode == 2);
    uint32_t effective_nmrc_min_good_paths = nmrc_path_space / 2;
    if (effective_nmrc_min_good_paths < 1)
        effective_nmrc_min_good_paths = 1;
    if (effective_nmrc_min_good_paths > 16)
        effective_nmrc_min_good_paths = 16;
    if (roce_lb_mode == RoceSrc::LB_NMRC) {
        cout << "n-mrc canonical: paths " << nmrc_path_space
             << ", feedback_pkts " << FatTreeSwitch::_nmrc_feedback_pkts
             << ", min_interval_us " << nmrc_feedback_min_us
             << ", max_interval_us " << nmrc_feedback_max_us
             << ", min_good_paths " << effective_nmrc_min_good_paths
             << ", bad_hold_down_us " << nmrc_bad_hold_down_us
             << ", state_mode " << nmrc_state_mode
             << ", bad_cache_windows " << nmrc_bad_cache_windows
             << ", weak_sample_pkts " << nmrc_weak_sample_pkts
             << ", ecn_degrade_mode " << nmrc_ecn_degrade_mode
             << ", unknown_reopen " << nmrc_unknown_reopen << endl;
    }
    if (roce_lb_mode == RoceSrc::LB_MRC) {
        uint32_t effective_mrc_active = mrc_active_paths ? mrc_active_paths : nmrc_path_space;
        uint32_t effective_mrc_backup = mrc_backup_paths ? mrc_backup_paths : effective_mrc_active;
        uint32_t effective_mrc_logical = effective_mrc_active + effective_mrc_backup;
        if (effective_mrc_logical < nmrc_path_space)
            effective_mrc_logical = nmrc_path_space;
        if (!effective_mrc_logical)
            effective_mrc_logical = 1;
        if (effective_mrc_active > effective_mrc_logical)
            effective_mrc_active = effective_mrc_logical;
        if (effective_mrc_backup > effective_mrc_logical - effective_mrc_active)
            effective_mrc_backup = effective_mrc_logical - effective_mrc_active;
        cout << "MRC: paths " << nmrc_path_space
             << ", logical_evs " << effective_mrc_logical
             << ", active_evs " << effective_mrc_active
             << ", backup_evs " << effective_mrc_backup
             << ", min_active_paths " << mrc_min_active_paths
             << ", ecn_cooldown_us " << mrc_ecn_cooldown_us
             << ", failed_retry_us " << mrc_failed_retry_us
             << ", probe_interval_pkts " << mrc_probe_interval_pkts
             << ", composite_ecn_threshold_fraction " << ecn_thresh << endl;
    }
    RoceSrc::setNmrcMinGoodPaths(effective_nmrc_min_good_paths);
    RoceSrc::setNmrcBadHoldDown(timeFromUs(nmrc_bad_hold_down_us));
    RoceSrc::setNmrcStateMode(nmrc_state_mode);
    RoceSrc::setNmrcBadCacheWindows(nmrc_bad_cache_windows);
    RoceSrc::setNmrcWeakSamplePkts(nmrc_weak_sample_pkts);
    RoceSrc::setNmrcEcnDegradeMode(nmrc_ecn_degrade_mode);
    RoceSrc::setNmrcUnknownReopen(nmrc_unknown_reopen);
    RoceSrc::setMrcActivePaths(mrc_active_paths);
    RoceSrc::setMrcBackupPaths(mrc_backup_paths);
    RoceSrc::setMrcMinActivePaths(mrc_min_active_paths);
    RoceSrc::setMrcEcnCooldown(timeFromUs(mrc_ecn_cooldown_us));
    RoceSrc::setMrcFailedRetry(timeFromUs(mrc_failed_retry_us));
    RoceSrc::setMrcProbeIntervalPkts(mrc_probe_interval_pkts);
    RoceSrc::setPathEntropySize(path_entropy_size);

    vector<const Route*>*** net_paths;
    net_paths = new vector<const Route*>**[no_of_nodes];

    int **path_refcounts;
    path_refcounts = new int*[no_of_nodes];

    int* is_dest = new int[no_of_nodes];
    
    for (size_t s = 0; s < no_of_nodes; s++) {
        is_dest[s] = 0;
        net_paths[s] = new vector<const Route*>*[no_of_nodes];
        path_refcounts[s] = new int[no_of_nodes];
        for (size_t d = 0; d < no_of_nodes; d++) {
            net_paths[s][d] = NULL;
            path_refcounts[s][d] = 0;
        }
    }
    
    ConnectionMatrix* conns = new ConnectionMatrix(no_of_nodes);

    if (tm_file){
        cout << "Loading connection matrix from  " << tm_file << endl;

        if (!conns->load(tm_file))
            exit(-1);
    }
    else {
        cout << "Loading connection matrix from  standard input" << endl;        
        conns->load(cin);
    }

    if (conns->N != no_of_nodes){
        cout << "Connection matrix number of nodes is " << conns->N << " while I am using " << no_of_nodes << endl;
        exit(-1);
    }

    // handle link failures specified in the connection matrix.
    for (size_t c = 0; c < conns->failures.size(); c++){
        failure* crt = conns->failures.at(c);

        cout << "Adding link failure switch type" << crt->switch_type << " Switch ID " << crt->switch_id << " link ID "  << crt->link_id << endl;
        top->add_failed_link(crt->switch_type,crt->switch_id,crt->link_id);
    }
    
    vector<connection*>* all_conns;
    
    // used just to print out stats data at the end
    //list <const Route*> routes;

    all_conns = conns->getAllConnections();
    vector <RoceSrc*> roce_srcs;

    for (size_t c = 0; c < all_conns->size(); c++){
        connection* crt = all_conns->at(c);
        int src = crt->src;
        int dest = crt->dst;
        path_refcounts[src][dest]++;
        path_refcounts[dest][src]++;
                        
        if (!net_paths[src][dest]&&route_strategy!=ECMP_FIB) {
            vector<const Route*>* paths = top->get_bidir_paths(src,dest,false);
            net_paths[src][dest] = paths;
            /*
              for (unsigned int i = 0; i < paths->size(); i++) {
              routes.push_back((*paths)[i]);
              }
            */
        }
        if (!net_paths[dest][src]&&route_strategy!=ECMP_FIB) {
            vector<const Route*>* paths = top->get_bidir_paths(dest,src,false);
            net_paths[dest][src] = paths;
        }
    }

    map <flowid_t, TriggerTarget*> flowmap;

    for (size_t c = 0; c < all_conns->size(); c++){
        connection* crt = all_conns->at(c);
        int src = crt->src;
        int dest = crt->dst;
        cout << "Connection " << crt->src << "->" <<crt->dst << " starting at " << timeAsUs(crt->start)
             << " size " << crt->size;
        if (crt->rate_mbps)
            cout << " rate_mbps " << crt->rate_mbps;
        cout << endl;

        linkspeed_bps src_rate = crt->rate_mbps ? speedFromMbps((uint64_t)crt->rate_mbps) : linkspeed;
        roceSrc = new RoceSrc(NULL, NULL, eventlist, src_rate);
        roceRtxScanner.registerRoce(*roceSrc);

        roce_srcs.push_back(roceSrc);
        roceSrc->set_src(src);
        roceSrc->set_dst(dest);
                        
        if (crt->size>0){
            roceSrc->set_flowsize(crt->size);
        }

        if (crt->flowid) {
            roceSrc->set_flowid(crt->flowid);
            assert(flowmap.find(crt->flowid) == flowmap.end()); // don't have dups
            flowmap[crt->flowid] = roceSrc;
        }

        if (crt->trigger) {
            Trigger* trig = conns->getTrigger(crt->trigger, eventlist);
            trig->add_target(*roceSrc);
        }
        if (crt->send_done_trigger) {
            Trigger* trig = conns->getTrigger(crt->send_done_trigger, eventlist);
            roceSrc->set_end_trigger(*trig);
        }

        roceSnk = new RoceSink();
                        
        roceSrc->setName("Roce_" + ntoa(src) + "_" + ntoa(dest));

        logfile.writeName(*roceSrc);

        roceSnk->set_src(src);
                        
        roceSnk->setName("Roce_sink_" + ntoa(src) + "_" + ntoa(dest));
        logfile.writeName(*roceSnk);
                        
        ((HostQueue*)top->queues_ns_nlp[src][top->HOST_POD_SWITCH(src)][0])->addHostSender(roceSrc);

        if (route_strategy!=SINGLE_PATH && route_strategy!=ECMP_FIB){
            abort();
        } else if (route_strategy==ECMP_FIB) {
            Route* srctotor = new Route();
            
            srctotor->push_back(top->queues_ns_nlp[src][top->HOST_POD_SWITCH(src)][0]);
            srctotor->push_back(top->pipes_ns_nlp[src][top->HOST_POD_SWITCH(src)][0]);
            srctotor->push_back(top->queues_ns_nlp[src][top->HOST_POD_SWITCH(src)][0]->getRemoteEndpoint());

            Route* dsttotor = new Route();
            dsttotor->push_back(top->queues_ns_nlp[dest][top->HOST_POD_SWITCH(dest)][0]);
            dsttotor->push_back(top->pipes_ns_nlp[dest][top->HOST_POD_SWITCH(dest)][0]);
            dsttotor->push_back(top->queues_ns_nlp[dest][top->HOST_POD_SWITCH(dest)][0]->getRemoteEndpoint());


            if (crt->start != TRIGGER_START && start_delta > 0){
                crt->start += timeFromUs(drand48()*start_delta);
                cout << "Start is " << timeAsUs(crt->start) << endl;
            }
            roceSrc->connect(srctotor, dsttotor, *roceSnk, crt->start);

            //register src and snk to receive packets from their respective TORs. 
            assert(top->switches_lp[top->HOST_POD_SWITCH(src)]);
            assert(top->switches_lp[top->HOST_POD_SWITCH(src)]);
            top->switches_lp[top->HOST_POD_SWITCH(src)]->addHostPort(src,roceSrc->flow_id(),roceSrc);
            top->switches_lp[top->HOST_POD_SWITCH(dest)]->addHostPort(dest,roceSrc->flow_id(),roceSnk);
        } else {
            int choice = rand()%net_paths[src][dest]->size();
            routeout = new Route(*(net_paths[src][dest]->at(choice)));
            routeout->add_endpoints(roceSrc, roceSnk);
            routeout->push_back(roceSnk);
                                
            routein = new Route(*top->get_bidir_paths(dest,src,false)->at(choice));
            routein->add_endpoints(roceSnk, roceSrc);
            routein->push_back(roceSrc);
            roceSrc->connect(routeout, routein, *roceSnk, timeFromUs((uint32_t)rand()%20));
        }

        path_refcounts[src][dest]--;
        path_refcounts[dest][src]--;

        // free up the routes if no other connection needs them 
        if (path_refcounts[src][dest] == 0 && net_paths[src][dest]) {
            vector<const Route*>::iterator i;
            for (i = net_paths[src][dest]->begin(); i != net_paths[src][dest]->end(); i++) {
                if ((*i)->reverse())
                    delete (*i)->reverse();
                delete *i;
            }
            delete net_paths[src][dest];
        }
        if (path_refcounts[dest][src] == 0 && net_paths[dest][src]) {
            vector<const Route*>::iterator i;
            for (i = net_paths[dest][src]->begin(); i != net_paths[dest][src]->end(); i++) {
                if ((*i)->reverse())
                    delete (*i)->reverse();
                delete *i;
            }
            delete net_paths[dest][src];
        }

        if (log_sink) {
            sinkLogger.monitorSink(roceSnk);
        }
    }

    for (size_t ix = 0; ix < no_of_nodes; ix++) {
        delete path_refcounts[ix];
    }

    Logged::dump_idmap();
    // Record the setup
    int pktsize = Packet::data_packet_size();
    logfile.write("# pktsize=" + ntoa(pktsize) + " bytes");
    logfile.write("# hostnicrate = " + ntoa(linkspeed/1000000) + " Mbps");
    //logfile.write("# corelinkrate = " + ntoa(HOST_NIC*CORE_TO_HOST) + " pkt/sec");
    //logfile.write("# buffer = " + ntoa((double) (queues_na_ni[0][1]->_maxsize) / ((double) pktsize)) + " pkt");
    double rtt = timeAsSec(timeFromUs(RTT));
    logfile.write("# rtt =" + ntoa(rtt));
    
    // GO!
    cout << "Starting simulation" << endl;
    while (eventlist.doNextEvent()) {
    }

    cout << "Done" << endl;
    int new_pkts = 0, rtx_pkts = 0;
    uint64_t ack_pkts = 0, nack_pkts = 0;
    for (size_t ix = 0; ix < roce_srcs.size(); ix++) {
        new_pkts += roce_srcs[ix]->_new_packets_sent;
        rtx_pkts += roce_srcs[ix]->_rtx_packets_sent;
        ack_pkts += roce_srcs[ix]->_acks_received;
        nack_pkts += roce_srcs[ix]->_nacks_received;
    }
    cout << "New: " << new_pkts << " Rtx: " << rtx_pkts << endl;
    cout << "RoceDiag "
         << "acks=" << ack_pkts
         << " nacks=" << nack_pkts
         << " rtos=" << RoceSrc::_global_rto_count
         << endl;
    QueueDiag queue_diag = collect_queue_diag(top);
    cout << "QueueDiag "
         << "lossless_overflows=" << queue_diag.lossless_overflows
         << " lossy_drops=" << queue_diag.lossy_drops
         << " lossy_ecn_marks=" << queue_diag.lossy_ecn_marks
         << " composite_trims=" << queue_diag.composite_trims
         << " composite_drops=" << queue_diag.composite_drops
         << " composite_ecn_marks=" << queue_diag.composite_ecn_marks
         << endl;
    if (queue_cv_sampler) {
        cout << "QueueCvDiag "
             << "spine_queue_cv=" << queue_cv_sampler->cv()
             << " spine_queue_avg=" << queue_cv_sampler->average_queue()
             << " spine_queue_count=" << queue_cv_sampler->count()
             << endl;
    }

    /*list <const Route*>::iterator rt_i;
      int counts[10]; int hop;
      for (int i = 0; i < 10; i++)
      counts[i] = 0;
      for (rt_i = routes.begin(); rt_i != routes.end(); rt_i++) {
      const Route* r = (*rt_i);
      //print_route(*r);
      #ifdef PRINTPATHS
      cout << "Path:" << endl;
      #endif
      hop = 0;
      for (int i = 0; i < r->size(); i++) {
      PacketSink *ps = r->at(i); 
      CompositeQueue *q = dynamic_cast<CompositeQueue*>(ps);
      if (q == 0) {
      #ifdef PRINTPATHS
      cout << ps->nodename() << endl;
      #endif
      } else {
      #ifdef PRINTPATHS
      cout << q->nodename() << " id=" << q->id << " " << q->num_packets() << "pkts " 
                     << q->num_headers() << "hdrs " << q->num_acks() << "acks " << q->num_nacks() << "nacks " << q->num_stripped() << "stripped"
                     << endl;
#endif
                counts[hop] += q->num_stripped();
                hop++;
            }
        } 
#ifdef PRINTPATHS
        cout << endl;
#endif
    }
    for (int i = 0; i < 10; i++)
    cout << "Hop " << i << " Count " << counts[i] << endl;*/
        
}
