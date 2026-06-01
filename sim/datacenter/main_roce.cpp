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
#include "firstfit.h"
#include "topology.h"
#include "connection_matrix.h"

#include "fat_tree_topology.h"
#include "fat_tree_switch.h"

#include <list>

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

void exit_error(char* progr) {
    cout << "Usage " << progr << " [-nodes N]\n\t[-conns C]\n\t[-q queue_size]\n\t[-queue_type composite|random|lossless|lossless_input|lossless_input_ecn]\n\t[-tm traffic_matrix_file]\n\t[-lb ecmp|ecmp_rr|adaptive-routing|glb|drill|reps|n-mrc|spray|ops|conweave|ndp]\n\t[-cc none|dcqcn|dcqcn_variant|mprdma]\n\t[-cc_iw_pkts pkts]\n\t[-cc_min_cwnd_pkts pkts]\n\t[-cc_max_cwnd_pkts pkts]\n\t[-dcqcn_g x]\n\t[-dcqcn_initial_alpha x]\n\t[-dcqcn_ai_mbps x]\n\t[-dcqcn_min_rate_mbps x]\n\t[-dcqcn_alpha_us x]\n\t[-dcqcn_rate_us x]\n\t[-dcqcn_cnp_us x]\n\t[-dcqcn_byte_counter bytes]\n\t[-dcqcn_fast_recovery_steps N]\n\t[-strat route_strategy (single,\n\tecmp_host,ecmp_ar,\n\tecmp_host_ar ar_thresh)]\n\t[-log log_level]\n\t[-seed random_seed]\n\t[-end end_time_in_usec]\n\t[-mtu MTU] default 4096\n\t[-linkspeed Mbps] default 400000\n\t[-hop_latency x] per hop wire latency in us, default 0.5\n\t[-switch_latency x] switching latency in us, default 0.5\n\t[-start_delta] time in us to randomly delay the start of connections\n\t[-slow_core_downlinks N]\n\t[-slow_core_downlink_divisor N]\n\t[-slow_tor_uplinks N]\n\t[-slow_tor_uplink_divisor N]\n\t[-nmrc_bad_hold_down_us x]\n\t[-nmrc_state_mode binary|2bit-observed|2bit-ecn01]\n\t[-nmrc_weak_sample_pkts N]\n\t[-nmrc_ecn_degrade aggressive|graded]\n\t[-glb_update_us x]\n\t[-glb_weights q_weight util_weight remote_busy_weight]\n\t[-glb_factors local_q local_util remote_q remote_util remote_busy]\n\t[-glb_normalize]\n\t[-glb_downstream_weight x]\n\t[-glb_quality_bucket x]\n\t[-conweave_rtt_us x]\n\t[-ndp_cwnd pkts]\n\t[-pfc_thresholds low high]" << endl;
    cout << "\t[-nmrc_unknown_reopen]" << endl;
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
    uint32_t ar_granularity = FatTreeSwitch::PER_FLOWLET;
    RoceSrc::lb_mode_t roce_lb_mode = RoceSrc::LB_ECMP;
    RoceSrc::cc_mode_t roce_cc_mode = RoceSrc::CC_DCQCN_VARIANT;

    queue_type snd_type = FAIR_PRIO;

    uint64_t high_pfc = 80, low_pfc = 20;
    uint32_t reps_buffer = 8;
    double conweave_rtt_us = 16.0;
    double conweave_min_reroute_us = 4.0;
    uint32_t ndp_cwnd = 256;
    uint32_t cc_iw_pkts = 0;
    uint32_t cc_min_cwnd_pkts = 1;
    uint32_t cc_max_cwnd_pkts = 0;
    uint32_t slow_core_downlinks = 0;
    uint32_t slow_core_downlink_divisor = 10;
    uint32_t slow_tor_uplinks = 0;
    uint32_t slow_tor_uplink_divisor = 2;
    double nmrc_bad_hold_down_us = 0.0;
    uint32_t nmrc_state_mode = 0;
    uint32_t nmrc_weak_sample_pkts = 0;
    uint32_t nmrc_ecn_degrade_mode = 0;
    bool nmrc_unknown_reopen = false;
    double nmrc_feedback_min_us = 5.0;
    double nmrc_feedback_max_us = 20.0;

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
            else if (!strcmp(argv[i+1], "lossless")) {
                qt = LOSSLESS;
            }
            else if (!strcmp(argv[i+1], "lossless_input")) {
                qt = LOSSLESS_INPUT;
            }
            else if (!strcmp(argv[i+1], "lossless_input_ecn")) {
                qt = LOSSLESS_INPUT_ECN;
            }
            else {
                cout << "Unknown queue type " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "queue_type "<< qt << endl;
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
            i++;
        } else if (!strcmp(argv[i],"-lb")){
            if (!strcmp(argv[i+1], "ecmp")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_ECMP;
            } else if (!strcmp(argv[i+1], "ecmp_rr")) {
                route_strategy = ECMP_FIB;
                path_entropy_size = 1;
                FatTreeSwitch::set_strategy(FatTreeSwitch::RR);
                roce_lb_mode = RoceSrc::LB_ECMP;
            } else if (!strcmp(argv[i+1], "glb")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::GLB);
                roce_lb_mode = RoceSrc::LB_ECMP;
            } else if (!strcmp(argv[i+1], "reps")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_REPS;
            } else if (!strcmp(argv[i+1], "n-mrc")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_NMRC;
            } else if (!strcmp(argv[i+1], "spray")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_SPRAY;
            } else if (!strcmp(argv[i+1], "ops")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_OPS;
            } else if (!strcmp(argv[i+1], "conweave")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_CONWEAVE;
            } else if (!strcmp(argv[i+1], "ndp")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ECMP);
                roce_lb_mode = RoceSrc::LB_NDP;
            } else if (!strcmp(argv[i+1], "adaptive-routing")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::ADAPTIVE_ROUTING);
                roce_lb_mode = RoceSrc::LB_ECMP;
            } else if (!strcmp(argv[i+1], "drill")) {
                route_strategy = ECMP_FIB;
                FatTreeSwitch::set_strategy(FatTreeSwitch::DRILL);
                roce_lb_mode = RoceSrc::LB_ECMP;
            } else {
                cout << "Unknown lb mode " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "lb mode " << argv[i+1] << endl;
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
            cout << "no of paths " << path_entropy_size << endl;
            i++;
        } else if (!strcmp(argv[i],"-hop_latency")){
            hop_latency = timeFromUs(atof(argv[i+1]));
            cout << "Hop latency set to " << timeAsUs(hop_latency) << endl;
            i++;
        } else if (!strcmp(argv[i],"-switch_latency")){
            switch_latency = timeFromUs(atof(argv[i+1]));
            cout << "Switch latency set to " << timeAsUs(hop_latency) << endl;
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
            cout << "REPS buffer size " << reps_buffer << endl;
            i++;
        } else if (!strcmp(argv[i],"-conweave_rtt_us")){
            conweave_rtt_us = atof(argv[i+1]);
            cout << "ConWeave RTT reroute threshold " << conweave_rtt_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-conweave_min_reroute_us")){
            conweave_min_reroute_us = atof(argv[i+1]);
            cout << "ConWeave minimum reroute gap " << conweave_min_reroute_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-ndp_cwnd")){
            ndp_cwnd = atoi(argv[i+1]);
            if (!ndp_cwnd)
                ndp_cwnd = 1;
            cout << "NDP initial receiver-pull window " << ndp_cwnd << " packets" << endl;
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
        } else if (!strcmp(argv[i],"-glb_update_us")){
            double glb_update_us = atof(argv[i+1]);
            if (glb_update_us < 0)
                glb_update_us = 0;
            FatTreeSwitch::_glb_update_interval = timeFromUs(glb_update_us);
            cout << "GLB remote quality update interval " << glb_update_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-glb_weights")){
            FatTreeSwitch::_glb_queue_weight = atof(argv[i+1]);
            FatTreeSwitch::_glb_util_weight = atof(argv[i+2]);
            FatTreeSwitch::_glb_remote_queue_weight = FatTreeSwitch::_glb_queue_weight;
            FatTreeSwitch::_glb_remote_util_weight = FatTreeSwitch::_glb_util_weight;
            FatTreeSwitch::_glb_remote_busy_weight = atof(argv[i+3]);
            cout << "GLB weights queue " << FatTreeSwitch::_glb_queue_weight
                 << " util " << FatTreeSwitch::_glb_util_weight
                 << " remote_busy " << FatTreeSwitch::_glb_remote_busy_weight << endl;
            i += 3;
        } else if (!strcmp(argv[i],"-glb_factors")){
            FatTreeSwitch::_glb_queue_weight = atof(argv[i+1]);
            FatTreeSwitch::_glb_util_weight = atof(argv[i+2]);
            FatTreeSwitch::_glb_remote_queue_weight = atof(argv[i+3]);
            FatTreeSwitch::_glb_remote_util_weight = atof(argv[i+4]);
            FatTreeSwitch::_glb_remote_busy_weight = atof(argv[i+5]);
            cout << "GLB factors local_q " << FatTreeSwitch::_glb_queue_weight
                 << " local_util " << FatTreeSwitch::_glb_util_weight
                 << " remote_q " << FatTreeSwitch::_glb_remote_queue_weight
                 << " remote_util " << FatTreeSwitch::_glb_remote_util_weight
                 << " remote_busy " << FatTreeSwitch::_glb_remote_busy_weight << endl;
            i += 5;
        } else if (!strcmp(argv[i],"-glb_normalize")){
            FatTreeSwitch::_glb_normalize_scores = true;
            cout << "GLB normalized queue/util factors enabled" << endl;
        } else if (!strcmp(argv[i],"-glb_downstream_weight")){
            FatTreeSwitch::_glb_downstream_weight = atof(argv[i+1]);
            cout << "GLB downstream weight " << FatTreeSwitch::_glb_downstream_weight << endl;
            i++;
        } else if (!strcmp(argv[i],"-glb_quality_bucket")){
            FatTreeSwitch::_glb_quality_bucket = atof(argv[i+1]);
            if (FatTreeSwitch::_glb_quality_bucket <= 0.0)
                FatTreeSwitch::_glb_quality_bucket = 1.0;
            cout << "GLB quality bucket " << FatTreeSwitch::_glb_quality_bucket << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_feedback_pkts")){
            cout << "N-MRC feedback packet threshold is canonical auto(path_count); ignoring deprecated value " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_feedback_hot_path_pkts")){
            cout << "N-MRC hot-path feedback threshold is disabled; ignoring deprecated value " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_feedback_min_us")){
            nmrc_feedback_min_us = atof(argv[i+1]);
            if (nmrc_feedback_min_us < 0)
                nmrc_feedback_min_us = 0;
            cout << "N-MRC minimum feedback interval " << nmrc_feedback_min_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_feedback_max_us")){
            nmrc_feedback_max_us = atof(argv[i+1]);
            if (nmrc_feedback_max_us < 0)
                nmrc_feedback_max_us = 0;
            cout << "N-MRC maximum feedback interval " << nmrc_feedback_max_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_min_good_paths")){
            cout << "N-MRC minimum confirmed good paths is canonical min(16,path_count/2); ignoring deprecated value " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_bad_hold_down_us")){
            nmrc_bad_hold_down_us = atof(argv[i+1]);
            if (nmrc_bad_hold_down_us < 0)
                nmrc_bad_hold_down_us = 0;
            cout << "N-MRC bad path keeping window " << nmrc_bad_hold_down_us << "us" << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_state_mode")){
            if (!strcmp(argv[i+1], "binary"))
                nmrc_state_mode = 0;
            else if (!strcmp(argv[i+1], "2bit-observed"))
                nmrc_state_mode = 1;
            else if (!strcmp(argv[i+1], "2bit-ecn01"))
                nmrc_state_mode = 2;
            else {
                cout << "Unknown N-MRC state mode " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "N-MRC endpoint state mode " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_weak_sample_pkts")){
            nmrc_weak_sample_pkts = atoi(argv[i+1]);
            cout << "N-MRC weak path sampling interval " << nmrc_weak_sample_pkts << " packets" << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_ecn_degrade")){
            if (!strcmp(argv[i+1], "aggressive"))
                nmrc_ecn_degrade_mode = 0;
            else if (!strcmp(argv[i+1], "graded"))
                nmrc_ecn_degrade_mode = 1;
            else {
                cout << "Unknown N-MRC ECN degrade mode " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "N-MRC ECN degrade mode " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_unknown_reopen")){
            nmrc_unknown_reopen = true;
            cout << "N-MRC unknown low-state reopen enabled" << endl;
        } else if (!strcmp(argv[i],"-nmrc_select")){
            if (strcmp(argv[i+1], "random") && strcmp(argv[i+1], "rr")) {
                cout << "Unknown N-MRC select mode " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "N-MRC select mode is canonical rr; ignoring deprecated value " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_ack_update")){
            cout << "N-MRC per-ACK path update is disabled in canonical mode; ignoring deprecated value " << argv[i+1] << endl;
            i++;
        } else if (!strcmp(argv[i],"-nmrc_path_hash")){
            if (strcmp(argv[i+1], "hash") && strcmp(argv[i+1], "direct") && strcmp(argv[i+1], "tier")) {
                cout << "Unknown N-MRC path hash mode " << argv[i+1] << endl;
                exit_error(argv[0]);
            }
            cout << "N-MRC path hash is canonical tier EV mapping; ignoring deprecated value " << argv[i+1] << endl;
            i++;
        }
         else if (!strcmp(argv[i],"-pfc_thresholds")){
            low_pfc = atoi(argv[i+1]);
            high_pfc = atoi(argv[i+2]);
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
    Packet::set_packet_size(packet_size);

    if (nmrc_feedback_max_us < nmrc_feedback_min_us)
        nmrc_feedback_max_us = nmrc_feedback_min_us;

    FatTreeSwitch::_ar_sticky = ar_granularity;
    FatTreeSwitch::_sticky_delta = timeFromUs(ar_sticky_delta);
    FatTreeSwitch::_nmrc_feedback_min_interval = timeFromUs(nmrc_feedback_min_us);
    FatTreeSwitch::_nmrc_feedback_max_interval = timeFromUs(nmrc_feedback_max_us);
    bool source_pathid_lb = (roce_lb_mode == RoceSrc::LB_NMRC ||
                             roce_lb_mode == RoceSrc::LB_SPRAY ||
                             roce_lb_mode == RoceSrc::LB_CONWEAVE ||
                             roce_lb_mode == RoceSrc::LB_NDP);
    FatTreeSwitch::_pathid_only_hash = source_pathid_lb;

    RoceSrc::setLoadBalancing(roce_lb_mode);
    RoceSrc::setPathEntropySize(path_entropy_size);
    RoceSrc::setRepsBufferSize(reps_buffer);
    RoceSrc::setConweaveRttThreshold(timeFromUs(conweave_rtt_us));
    RoceSrc::setConweaveMinRerouteGap(timeFromUs(conweave_min_reroute_us));
    RoceSrc::setNdpInitialWindow(ndp_cwnd);
    if (!cc_iw_pkts)
        cc_iw_pkts = queuesize ? queuesize : 1;
    RoceSrc::setCongestionControl(roce_cc_mode);
    RoceSrc::setCcInitialWindow(cc_iw_pkts);
    RoceSrc::setCcMinWindow(cc_min_cwnd_pkts);
    RoceSrc::setCcMaxWindow(cc_max_cwnd_pkts);

    LosslessInputQueue::_high_threshold = Packet::data_packet_size()*high_pfc;
    LosslessInputQueue::_low_threshold = Packet::data_packet_size()*low_pfc;

    eventlist.setEndtime(timeFromUs((uint32_t)end_time));
    queuesize = memFromPkt(queuesize);
    
    switch (route_strategy) {
    case ECMP_FIB:
    case SCATTER_ECMP:
        if (path_entropy_size > 10000) {
            fprintf(stderr, "Route Strategy is ECMP.  Must specify path count using -paths\n");
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

    RoceSrc::setMinRTO(1000);

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
    FatTreeSwitch::_nmrc_feedback_observed_values = nmrc_state_mode == 1 || nmrc_state_mode == 2;
    uint32_t effective_nmrc_min_good_paths = nmrc_path_space / 2;
    if (effective_nmrc_min_good_paths < 1)
        effective_nmrc_min_good_paths = 1;
    if (effective_nmrc_min_good_paths > 16)
        effective_nmrc_min_good_paths = 16;
    if (roce_lb_mode == RoceSrc::LB_NMRC) {
        cout << "N-MRC canonical: paths " << nmrc_path_space
             << ", feedback_pkts " << FatTreeSwitch::_nmrc_feedback_pkts
             << ", min_interval_us " << nmrc_feedback_min_us
             << ", max_interval_us " << nmrc_feedback_max_us
             << ", min_good_paths " << effective_nmrc_min_good_paths
             << ", bad_hold_down_us " << nmrc_bad_hold_down_us
             << ", state_mode " << nmrc_state_mode
             << ", weak_sample_pkts " << nmrc_weak_sample_pkts
             << ", ecn_degrade_mode " << nmrc_ecn_degrade_mode
             << ", unknown_reopen " << nmrc_unknown_reopen << endl;
    }
    RoceSrc::setNmrcMinGoodPaths(effective_nmrc_min_good_paths);
    RoceSrc::setNmrcBadHoldDown(timeFromUs(nmrc_bad_hold_down_us));
    RoceSrc::setNmrcStateMode(nmrc_state_mode);
    RoceSrc::setNmrcWeakSamplePkts(nmrc_weak_sample_pkts);
    RoceSrc::setNmrcEcnDegradeMode(nmrc_ecn_degrade_mode);
    RoceSrc::setNmrcUnknownReopen(nmrc_unknown_reopen);
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
        cout << "Connection " << crt->src << "->" <<crt->dst << " starting at " << timeAsUs(crt->start) << " size " << crt->size << endl;

        roceSrc = new RoceSrc(NULL, NULL, eventlist,linkspeed);

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
                                
            routein = new Route(*top->get_bidir_paths(dest,src,false)->at(choice));
            routein->add_endpoints(roceSnk, roceSrc);
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
    for (size_t ix = 0; ix < roce_srcs.size(); ix++) {
        new_pkts += roce_srcs[ix]->_new_packets_sent;
        rtx_pkts += roce_srcs[ix]->_rtx_packets_sent;
    }
    cout << "New: " << new_pkts << " Rtx: " << rtx_pkts << endl;

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
