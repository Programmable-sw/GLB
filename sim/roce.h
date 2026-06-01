// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-        


#ifndef ROCE_H
#define ROCE_H

/*
 * A ROCEv2 source and sink
 */

#include <list>
#include <map>
#include <vector>
#include <array>
//#include "util.h"
#include "math.h"
#include "config.h"
#include "network.h"
#include "rocepacket.h"
#include "queue.h"
#include "eventlist.h"
#include "eth_pause_packet.h"
#include "trigger.h"

#define timeInf 0

//min RTO bound in us
// *** don't change this default - override it by calling RoceSrc::setMinRTO()
#define DEFAULT_RTO_MIN 5000

class RoceSink;
class Switch;

class RoceSrc : public BaseQueue, public TriggerTarget {
    friend class RoceSink;
public:
    typedef enum {LB_ECMP = 0, LB_REPS = 1, LB_DTOR = 2, LB_CONWEAVE = 3, LB_NDP = 4, LB_SPRAY = 5, LB_OPS = 6} lb_mode_t;
    typedef enum {CC_NONE = 0, CC_DCQCN_VARIANT = 1, CC_MPRDMA = 1, CC_DCQCN = 2} cc_mode_t;

    RoceSrc(RoceLogger* logger, TrafficLogger* pktlogger, EventList &eventlist, linkspeed_bps rate);

    virtual void connect(Route* routeout, Route* routeback, RoceSink& sink, simtime_picosec startTime);
    void set_src(uint32_t src) {_srcaddr = src;}
    void set_dst(uint32_t dst) {_dstaddr = dst;}
    void set_traffic_logger(TrafficLogger* pktlogger);

    void startflow();
    void setRate(linkspeed_bps r) {_bitrate = r; update_packet_spacing(); doNextEvent();}

    inline void set_flowid(flowid_t flow_id) { _flow.set_flowid(flow_id);}


    static void setMinRTO(uint32_t min_rto_in_us) {_min_rto = timeFromUs((uint32_t)min_rto_in_us);}
    static void setLoadBalancing(lb_mode_t mode) {_lb_mode = mode;}
    static void setPathEntropySize(uint32_t paths) {_path_entropy_size = paths ? paths : 1;}
    static void setRepsBufferSize(uint32_t size) {_reps_buffer_size = size ? size : 1;}
    static void setDtorMinGoodPaths(uint32_t paths) {_dtor_min_good_paths = paths ? paths : 1;}
    static void setDtorHostsPerTor(uint32_t hosts) {_dtor_hosts_per_tor = hosts ? hosts : 1;}
    static void setDtorBadHoldDown(simtime_picosec hold_down) {_dtor_bad_hold_down = hold_down;}
    static void setDtorStateMode(uint32_t mode) {_dtor_state_mode = mode;}
    static void setDtorWeakSamplePkts(uint32_t pkts) {_dtor_weak_sample_pkts = pkts;}
    static void setDtorEcnDegradeMode(uint32_t mode) {_dtor_ecn_degrade_mode = mode;}
    static void setDtorUnknownReopen(bool enable) {_dtor_unknown_reopen = enable;}
    static void setConweaveRttThreshold(simtime_picosec threshold) {_conweave_rtt_threshold = threshold;}
    static void setConweaveMinRerouteGap(simtime_picosec gap) {_conweave_min_reroute_gap = gap;}
    static void setNdpInitialWindow(uint32_t pkts) {_ndp_initial_window = pkts ? pkts : 1;}
    static void setCongestionControl(cc_mode_t mode) {_cc_mode = mode;}
    static void setCcInitialWindow(uint32_t pkts) {_cc_initial_cwnd_pkts = pkts ? pkts : 1;}
    static void setCcMinWindow(uint32_t pkts) {_cc_min_cwnd_pkts = pkts ? pkts : 1;}
    static void setCcMaxWindow(uint32_t pkts) {_cc_max_cwnd_pkts = pkts;}
    static void setDcqcnG(double g) {_dcqcn_g = g;}
    static void setDcqcnInitialAlpha(double alpha) {_dcqcn_initial_alpha = alpha;}
    static void setDcqcnAiRate(linkspeed_bps rate) {_dcqcn_ai_rate = rate;}
    static void setDcqcnMinRate(linkspeed_bps rate) {_dcqcn_min_rate = rate;}
    static void setDcqcnByteCounter(mem_b bytes) {_dcqcn_byte_counter = bytes;}
    static void setDcqcnFastRecoverySteps(uint32_t steps) {_dcqcn_fast_recovery_steps = steps;}
    static void setDcqcnAlphaInterval(simtime_picosec interval) {_dcqcn_alpha_interval = interval;}
    static void setDcqcnRateIncreaseInterval(simtime_picosec interval) {_dcqcn_rate_increase_interval = interval;}
    static void setDcqcnCnpInterval(simtime_picosec interval) {_dcqcn_cnp_interval = interval;}

    void set_flowsize(uint64_t flow_size_in_bytes) {
        _flow_size = flow_size_in_bytes;
    }

    void set_stoptime(simtime_picosec stop_time) {
        _stop_time = stop_time;
        cout << "Setting stop time to " << timeAsSec(_stop_time) << endl;
    }

    // called from a trigger to start the flow.
    virtual void activate() {
        cout << "Activate called " << _flow._name << endl;
        startflow();
    }

    void set_end_trigger(Trigger& trigger);

    virtual void doNextEvent();
    virtual void receivePacket(Packet& pkt);

    virtual void setPath(uint32_t p) {_pathid = p;}

    virtual void processPause(const EthPausePacket& pkt);
    virtual void processAck(const RoceAck& ack);
    virtual void processNack(const RoceNack& nack);

    virtual mem_b queuesize() const { return 0;};
    virtual mem_b maxsize() const { return 0;}; 

    // should really be private, but loggers want to see:
    uint64_t _highest_sent;  //seqno is in bytes
    uint64_t _packets_sent;
    uint64_t _last_acked;
    uint32_t _new_packets_sent;  // all the below reduced to 32 bits to save RAM
    uint32_t _rtx_packets_sent;
    uint32_t _acks_received;
    uint32_t _nacks_received;

    uint32_t _acked_packets;
    uint32_t _pathid;

    enum {PAUSED,READY};

    uint32_t _dstaddr;

    void print_stats();

    //round trip time estimate, needed for RTO calculation
    simtime_picosec _rtt, _rto, _mdev,_base_rtt;

    uint16_t _mss;
    uint32_t _drops;

    RoceSink* _sink;
 
    const Route* _route;
    bool _flow_started;
    uint16_t _state_send;

    void send_packet();

    virtual const string& nodename() { return _nodename; }
    inline uint32_t flow_id() const { return _flow.flow_id();}
 
    //debugging hack
    void log_me();
    bool _log_me;

    static uint32_t _global_node_count; 
    static uint32_t _global_rto_count;  // keep track of the total number of timeouts across all srcs
    static simtime_picosec _min_rto;
    static lb_mode_t _lb_mode;
    static uint32_t _path_entropy_size;
    static uint32_t _reps_buffer_size;
    static uint32_t _dtor_min_good_paths;
    static uint32_t _dtor_hosts_per_tor;
    static simtime_picosec _dtor_bad_hold_down;
    static uint32_t _dtor_state_mode;
    static uint32_t _dtor_weak_sample_pkts;
    static uint32_t _dtor_ecn_degrade_mode;
    static bool _dtor_unknown_reopen;
    static simtime_picosec _conweave_rtt_threshold;
    static simtime_picosec _conweave_min_reroute_gap;
    static uint32_t _ndp_initial_window;
    static cc_mode_t _cc_mode;
    static uint32_t _cc_initial_cwnd_pkts;
    static uint32_t _cc_min_cwnd_pkts;
    static uint32_t _cc_max_cwnd_pkts;
    static double _dcqcn_g;
    static double _dcqcn_initial_alpha;
    static linkspeed_bps _dcqcn_ai_rate;
    static linkspeed_bps _dcqcn_min_rate;
    static mem_b _dcqcn_byte_counter;
    static uint32_t _dcqcn_fast_recovery_steps;
    static simtime_picosec _dcqcn_alpha_interval;
    static simtime_picosec _dcqcn_rate_increase_interval;
    static simtime_picosec _dcqcn_cnp_interval;

    PacketFlow _flow;

private:
    // Housekeeping
    RoceLogger* _logger;
    Trigger* _end_trigger;

    TrafficLogger* _pktlogger;

    // Connectivity
    string _nodename;
    uint32_t _node_num;
    uint32_t _srcaddr;

    // Mechanism
    void clear_timer(uint64_t start,uint64_t end);

    uint64_t _flow_size;  //The flow size in bytes.  Stop sending after this amount.
    simtime_picosec _stop_time;
    simtime_picosec _packet_spacing;
    simtime_picosec _time_last_sent;
    bool _done;

    void update_packet_spacing();
    void reset_congestion_control();
    void update_congestion_control_on_ack(const RoceAck& ack, double newly_acked_pkts);
    void update_congestion_control_on_nack();
    bool congestion_window_allows_send() const;
    double congestion_window_available() const;
    void clamp_congestion_window();
    void dcqcn_update_alpha_timer();
    void dcqcn_on_cnp();
    void dcqcn_maybe_increase(double newly_acked_pkts);
    void dcqcn_increase_rate();
    void clamp_dcqcn_rate();
    double _cc_cwnd_pkts;
    double _cc_inflate_pkts;
    double _dcqcn_alpha;
    double _dcqcn_current_rate;
    double _dcqcn_target_rate;
    mem_b _dcqcn_bytes_since_increase;
    uint32_t _dcqcn_recovery_count;
    bool _dcqcn_seen_cnp;
    bool _dcqcn_marked_since_alpha;
    simtime_picosec _dcqcn_last_cnp;
    simtime_picosec _dcqcn_next_alpha_update;
    simtime_picosec _dcqcn_next_rate_increase;

    uint32_t choose_path(Packet::PktPriority priority);
    void update_reps(const RoceAck& ack);
    void update_conweave(const RoceAck& ack, simtime_picosec rtt);
    void update_dtor(const RoceAck& ack);
    void init_dtor_priority(Packet::PktPriority priority, uint32_t path_space);
    void ensure_dtor_bitmap(uint32_t path_space);
    void load_dtor_shared_bitmap(uint32_t path_space);
    void store_dtor_shared_bitmap();
    std::pair<uint32_t, uint32_t> dtor_cache_key() const;
    void release_dtor_hold_if_expired(uint32_t path_space);
    void init_ndp_paths(uint32_t path_space);
    uint32_t choose_ndp_path(uint32_t path_space);
    void grant_ndp_credit(uint32_t credits = 1);

    struct RepsBufferEntry {
        uint32_t cached_ev;
        bool valid;
        RepsBufferEntry() : cached_ev(0), valid(false) {}
    };
    void ensure_reps_buffer();
    void reset_reps_buffer();

    std::vector<RepsBufferEntry> _reps_buffer;
    uint32_t _reps_head;
    uint32_t _reps_valid_count;
    static std::map<std::pair<uint32_t, uint32_t>, DtorBitmap> _dtor_shared_bitmaps;
    static std::map<std::pair<uint32_t, uint32_t>, simtime_picosec> _dtor_shared_hold_until;
    DtorBitmap _dtor_path_bitmap;
    simtime_picosec _dtor_hold_until;
    std::vector<uint32_t> _ndp_path_ids;
    uint32_t _ndp_cursor;
    uint32_t _ndp_pull_credit;
    bool _ndp_paths_ready;
    simtime_picosec _conweave_last_reroute;
    std::array<uint32_t, 3> _dtor_cursor;
    std::array<uint32_t, 3> _dtor_stride;
    std::array<bool, 3> _dtor_cursor_ready;
};

class RoceSink : public PacketSink, public DataReceiver {
    friend class RoceSrc;
public:
    RoceSink();

    enum {PAUSED,READY};

    virtual void receivePacket(Packet& pkt);
    
    RoceAck::seq_t _cumulative_ack; // the packet we have cumulatively acked
    uint32_t _drops;
    uint64_t cumulative_ack() { return _cumulative_ack;}
    uint64_t total_received() const { return _cumulative_ack;}
    uint32_t drops(){ return _src->_drops;}
    virtual const string& nodename() { return _nodename; }

    void set_src(uint32_t s) {_srcaddr = s;}
 
    RoceSrc* _src;

    //debugging hack
    void log_me();
    bool _log_me;

    uint32_t _srcaddr;
    
private:
 
    // Connectivity
    void connect(RoceSrc& src, Route* route);

    inline uint32_t flow_id() const {
        return _src->flow_id();
    };

    const Route* _route;

    string _nodename;
 
    RocePacket::seq_t _last_packet_seqno; //sequence number of the last
    //packet in the connection (or 0 if not known)
    uint64_t _total_received;
    RocePacket::seq_t _highest_seqno;
    map<RocePacket::seq_t, int> _ooo_packets;
 
    // Mechanism
    void send_ack(const RocePacket& pkt, simtime_picosec ts);
    void send_nack(simtime_picosec ts, RocePacket::seq_t ackno, uint32_t path_id = 0);
};


#endif
