// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-        


#ifndef ROCE_H
#define ROCE_H

/*
 * A ROCEv2 source and sink
 */

#include <list>
#include <map>
#include <set>
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
#define ROCE_SACK_BITMAP_BITS_DEFAULT 64
#define ROCE_SACK_BITMAP_BITS_MAX 128

class RoceSink;
class Switch;

class RoceSrc : public BaseQueue, public TriggerTarget {
    friend class RoceSink;
public:
    typedef enum {LB_ECMP = 0, LB_REPS = 1, LB_NMRC = 2, LB_CONWEAVE = 3, LB_NDP = 4, LB_RR = 5, LB_OPS = 6, LB_MRC = 7} lb_mode_t;
    typedef enum {CC_NONE = 0, CC_DCQCN_VARIANT = 1, CC_MPRDMA = 1, CC_DCQCN = 2} cc_mode_t;
    typedef enum {RX_GBN = 0, RX_SP_RETX_QUEUE = 1} rx_mode_t;
    typedef enum {
        DCQCN_NACK_AS_CNP = 0,
        DCQCN_NACK_CNP = DCQCN_NACK_AS_CNP,
        DCQCN_NACK_IGNORE = 1,
        DCQCN_NACK_RATE_CUT = 2
    } dcqcn_nack_reaction_t;

    RoceSrc(RoceLogger* logger, TrafficLogger* pktlogger, EventList &eventlist, linkspeed_bps rate);

    virtual void connect(Route* routeout, Route* routeback, RoceSink& sink, simtime_picosec startTime);
    void set_src(uint32_t src) {_srcaddr = src;}
    void set_dst(uint32_t dst) {_dstaddr = dst;}
    void set_traffic_logger(TrafficLogger* pktlogger);

    void startflow();
    void setRate(linkspeed_bps r) {_bitrate = r; update_packet_spacing(); doNextEvent();}

    inline void set_flowid(flowid_t flow_id) { _flow.set_flowid(flow_id);}


    static void setMinRTO(uint32_t min_rto_in_us) {_min_rto = timeFromUs((uint32_t)min_rto_in_us);}
    static void setHighRTO(uint32_t high_rto_in_us) {_rto_high = high_rto_in_us ? timeFromUs((uint32_t)high_rto_in_us) : 0;}
    static void setReceiveMode(rx_mode_t mode) {_rx_mode = mode;}
    static uint32_t normalizeSackBitmapBits(uint32_t bits) {
        return bits <= ROCE_SACK_BITMAP_BITS_DEFAULT ?
            ROCE_SACK_BITMAP_BITS_DEFAULT : ROCE_SACK_BITMAP_BITS_MAX;
    }
    static void setSackBitmapBits(uint32_t bits) {_sack_bitmap_bits = normalizeSackBitmapBits(bits);}
    static uint32_t sackBitmapBits() {return _sack_bitmap_bits;}
    static void setOooTolerance(simtime_picosec interval) {_ooo_tolerance = interval;}
    static void setOooWindowPkts(uint32_t pkts) {_ooo_window_pkts = pkts ? pkts : 1;}
    static void setNackInterval(simtime_picosec interval) {_nack_interval = interval;}
    static void setLoadBalancing(lb_mode_t mode) {_lb_mode = mode;}
    static void setPathEntropySize(uint32_t paths) {_path_entropy_size = paths ? paths : 1;}
    static void setRepsBufferSize(uint32_t size) {_reps_buffer_size = size ? size : 1;}
    static void setRepsWarmupPkts(uint32_t pkts) {_reps_warmup_pkts = pkts;}
    static void setNmrcMinGoodPaths(uint32_t paths) {_nmrc_min_good_paths = paths ? paths : 1;}
    static void setNmrcHostsPerTor(uint32_t hosts) {_nmrc_hosts_per_tor = hosts ? hosts : 1;}
    static void setNmrcBadHoldDown(simtime_picosec hold_down) {_nmrc_bad_hold_down = hold_down;}
    static void setNmrcStateMode(uint32_t mode) {_nmrc_state_mode = mode;}
    static void setNmrcWeakSamplePkts(uint32_t pkts) {_nmrc_weak_sample_pkts = pkts;}
    static void setNmrcEcnDegradeMode(uint32_t mode) {_nmrc_ecn_degrade_mode = mode;}
    static void setNmrcUnknownReopen(bool enable) {_nmrc_unknown_reopen = enable;}
    static void setNmrcBadCacheWindows(uint32_t windows) {_nmrc_bad_cache_windows = windows ? windows : 1;}
    static void setMrcActivePaths(uint32_t paths) {_mrc_active_path_count = paths;}
    static void setMrcBackupPaths(uint32_t paths) {_mrc_backup_path_count = paths;}
    static void setMrcMinActivePaths(uint32_t paths) {_mrc_min_active_paths = paths ? paths : 1;}
    static void setMrcEcnCooldown(simtime_picosec cooldown) {_mrc_ecn_cooldown = cooldown;}
    static void setMrcFailedRetry(simtime_picosec retry) {_mrc_failed_retry = retry;}
    static void setMrcProbeIntervalPkts(uint32_t pkts) {_mrc_probe_interval_pkts = pkts;}
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
    static void setDcqcnNackReaction(dcqcn_nack_reaction_t reaction) {_dcqcn_nack_reaction = reaction;}
    static void setDcqcnNackReaction(uint32_t reaction) {_dcqcn_nack_reaction = (dcqcn_nack_reaction_t)reaction;}

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
    virtual void rtx_timer_hook(simtime_picosec now, simtime_picosec period);

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

    bool send_packet();

    virtual const string& nodename() { return _nodename; }
    inline uint32_t flow_id() const { return _flow.flow_id();}
 
    //debugging hack
    void log_me();
    bool _log_me;

    static uint32_t _global_node_count; 
    static uint32_t _global_rto_count;  // keep track of the total number of timeouts across all srcs
    static simtime_picosec _min_rto;
    static simtime_picosec _rto_high;
    static rx_mode_t _rx_mode;
    static uint32_t _sack_bitmap_bits;
    static simtime_picosec _ooo_tolerance;
    static uint32_t _ooo_window_pkts;
    static simtime_picosec _nack_interval;
    static lb_mode_t _lb_mode;
    static uint32_t _path_entropy_size;
    static uint32_t _reps_buffer_size;
    static uint32_t _reps_warmup_pkts;
    static uint32_t _nmrc_min_good_paths;
    static uint32_t _nmrc_hosts_per_tor;
    static simtime_picosec _nmrc_bad_hold_down;
    static uint32_t _nmrc_state_mode;
    static uint32_t _nmrc_weak_sample_pkts;
    static uint32_t _nmrc_ecn_degrade_mode;
    static bool _nmrc_unknown_reopen;
    static uint32_t _nmrc_bad_cache_windows;
    static uint32_t _mrc_active_path_count;
    static uint32_t _mrc_backup_path_count;
    static uint32_t _mrc_min_active_paths;
    static simtime_picosec _mrc_ecn_cooldown;
    static simtime_picosec _mrc_failed_retry;
    static uint32_t _mrc_probe_interval_pkts;
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
    static dcqcn_nack_reaction_t _dcqcn_nack_reaction;

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
    simtime_picosec _send_event_time;
    EventList::Handle _send_event_handle;
    simtime_picosec _rtx_timeout;
    bool _send_event_pending;
    bool _done;

    void schedule_send(simtime_picosec when);
    void schedule_send_now();
    void update_packet_spacing();
    void reset_congestion_control();
    void reset_rtx_timeout();
    simtime_picosec current_rto_interval() const;
    void update_congestion_control_on_ack(const RoceAck& ack, double newly_acked_pkts);
    void update_congestion_control_on_nack();
    bool has_retransmit_work() const;
    void clean_retransmit_queue();
    void reset_sack_recovery_state();
    simtime_picosec sack_rxtpsn_reset_interval() const;
    void maybe_reset_sack_rxtpsn();
    bool sack_seq_blocked_by_rxtpsn(RocePacket::seq_t seq) const;
    void update_sack_rxtpsn(RocePacket::seq_t psn);
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

    uint32_t choose_path(Packet::PktPriority priority, bool retransmitted);
    void update_reps(const RoceAck& ack);
    void update_conweave(const RoceAck& ack, simtime_picosec rtt);
    void update_nmrc(const RoceAck& ack);
    void reset_mrc_paths();
    void init_mrc_paths(uint32_t path_space);
    uint32_t choose_mrc_path(uint32_t path_space);
    struct MrcChoice {
        uint32_t logical_ev;
        uint32_t physical_path;
        MrcChoice() : logical_ev(UINT32_MAX), physical_path(0) {}
        MrcChoice(uint32_t logical, uint32_t physical)
            : logical_ev(logical), physical_path(physical) {}
    };
    MrcChoice choose_mrc_ev(uint32_t path_space);
    void update_mrc_on_ack(const RoceAck& ack);
    void update_mrc_on_nack(const RoceNack& nack);
    void update_mrc_on_rto();
    void note_mrc_packet_ev(RocePacket::seq_t seqno, uint32_t logical_ev);
    void clean_mrc_seq_evs();
    void fail_mrc_sequence(RocePacket::seq_t seqno);
    uint32_t mrc_logical_ev_count(uint32_t path_space) const;
    uint32_t mrc_desired_active_paths(uint32_t path_space) const;
    bool mrc_ev_in_active(uint32_t logical_ev) const;
    bool mrc_ev_selectable(uint32_t logical_ev);
    void mrc_activate_ev(uint32_t logical_ev);
    void mrc_remove_active_ev(uint32_t logical_ev);
    void mrc_mark_congested(uint32_t logical_ev);
    void mrc_mark_physical_congested(uint32_t physical_path);
    void mrc_mark_failed(uint32_t logical_ev);
    void mrc_mark_physical_failed(uint32_t physical_path);
    void mrc_promote_backup(uint32_t path_space);
    uint32_t mrc_choose_probe_ev(uint32_t path_space);
    void mrc_note_clean_ack(RocePacket::seq_t ackno);
    void mrc_note_ecn_ack(RocePacket::seq_t ackno, uint32_t physical_path);
    void init_nmrc_priority(Packet::PktPriority priority, uint32_t path_space);
    void ensure_nmrc_bitmap(uint32_t path_space);
    void load_nmrc_shared_bitmap(uint32_t path_space);
    void store_nmrc_shared_bitmap();
    std::pair<uint32_t, uint32_t> nmrc_cache_key() const;
    void release_nmrc_hold_if_expired(uint32_t path_space);
    void ensure_nmrc_bad_cache(uint32_t path_space);
    void rebuild_nmrc_bad_cache_bitmap(uint32_t path_space);
    void apply_nmrc_bad_cache_feedback(const NmrcBitmap& feedback, uint32_t path_space);
    void init_ndp_paths(uint32_t path_space);
    uint32_t choose_ndp_path(uint32_t path_space);
    void grant_ndp_credit(uint32_t credits = 1);

    struct RepsBufferEntry {
        uint32_t cached_ev;
        bool valid;
        RepsBufferEntry() : cached_ev(0), valid(false) {}
    };
    enum MrcPathState {
        MRC_PATH_UNUSED = 0,
        MRC_PATH_ACTIVE = 1,
        MRC_PATH_COOLING = 2,
        MRC_PATH_FAILED = 3,
        MRC_PATH_PROBING = 4
    };
    struct MrcEv {
        uint32_t logical_ev;
        uint32_t physical_path;
        uint8_t state;
        uint8_t probe_successes;
        simtime_picosec retry_after;
        MrcEv()
            : logical_ev(0), physical_path(0), state(MRC_PATH_UNUSED),
              probe_successes(0), retry_after(0) {}
    };
    class SpRtxQueue {
    public:
        void clear();
        bool empty() const;
        void insert(RocePacket::seq_t seq);
        void erase(RocePacket::seq_t seq);
        void erase_acked(RocePacket::seq_t last_acked);
        bool pop_next(RocePacket::seq_t last_acked,
                      RocePacket::seq_t highest_sent,
                      uint64_t flow_size,
                      RocePacket::seq_t& seq);
        size_t size() const;
    private:
        std::set<RocePacket::seq_t> _seqs;
    };
    void ensure_reps_buffer();
    void reset_reps_buffer();

    std::vector<RepsBufferEntry> _reps_buffer;
    uint32_t _reps_head;
    uint32_t _reps_valid_count;
    uint32_t _reps_explore_remaining;
    static std::map<std::pair<uint32_t, uint32_t>, NmrcBitmap> _nmrc_shared_bitmaps;
    static std::map<std::pair<uint32_t, uint32_t>, simtime_picosec> _nmrc_shared_hold_until;
    static std::map<std::pair<uint32_t, uint32_t>, std::vector<NmrcBitmap> > _nmrc_shared_bad_epochs;
    static std::map<std::pair<uint32_t, uint32_t>, uint32_t> _nmrc_shared_bad_epoch_cursor;
    NmrcBitmap _nmrc_path_bitmap;
    simtime_picosec _nmrc_hold_until;
    std::vector<NmrcBitmap> _nmrc_bad_epochs;
    uint32_t _nmrc_bad_epoch_cursor;
    std::vector<uint32_t> _ndp_path_ids;
    uint32_t _ndp_cursor;
    uint32_t _ndp_pull_credit;
    bool _ndp_paths_ready;
    std::vector<MrcEv> _mrc_evs;
    std::vector<uint32_t> _mrc_active;
    std::vector<uint32_t> _mrc_backup;
    uint32_t _mrc_active_cursor;
    uint32_t _mrc_backup_cursor;
    uint32_t _mrc_path_space;
    bool _mrc_paths_ready;
    std::map<RocePacket::seq_t, uint32_t> _mrc_seq_ev;
    simtime_picosec _conweave_last_reroute;
    std::array<uint32_t, 3> _nmrc_cursor;
    std::array<uint32_t, 3> _nmrc_stride;
    std::array<bool, 3> _nmrc_cursor_ready;
    SpRtxQueue _rtx_queue;
    RocePacket::seq_t _sack_rxt_psn;
    simtime_picosec _sack_rxt_psn_updated;
    bool _sack_rxt_psn_valid;
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
    class OooNackTimer : public EventSource {
    public:
        OooNackTimer(RoceSink& sink)
            : EventSource("roce_sink_ooo_timer"), _sink(sink) {}
        virtual void doNextEvent() {_sink.ooo_nack_timer_hook();}
    private:
        RoceSink& _sink;
    };

 
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
    simtime_picosec _ooo_first_time;
    simtime_picosec _nack_silent_until;
    bool _ooo_nack_event_pending;
    simtime_picosec _ooo_nack_event_time;
    uint32_t _ooo_nack_path_id;
    OooNackTimer _ooo_nack_timer;
 
    // Mechanism
    void send_ack(const RocePacket& pkt, simtime_picosec ts);
    RoceNack* send_nack(simtime_picosec ts, RocePacket::seq_t ackno, uint32_t path_id = 0,
                        uint64_t sack_bitmap = 0, uint16_t sack_offset = 0,
                        bool has_sack = false,
                        RoceNack::nack_reason_t reason = RoceNack::LOSS,
                        uint64_t sack_bitmap_high = 0,
                        RocePacket::seq_t sack_bitmap_start_psn = 0,
                        uint16_t sack_bitmap_valid_length = 0);
    void build_sack_bitmap(RocePacket::seq_t ackno, uint64_t& sack_bitmap_low,
                           uint64_t& sack_bitmap_high,
                           RocePacket::seq_t& sack_bitmap_start_psn,
                           uint16_t& sack_bitmap_valid_length,
                           uint16_t& sack_offset) const;
    bool should_send_sp_nack() const;
    void arm_ooo_nack_timer(simtime_picosec when);
    void cancel_ooo_nack_timer();
    void ooo_nack_timer_hook();
};

class RoceRtxTimerScanner : public EventSource {
public:
    RoceRtxTimerScanner(simtime_picosec scanPeriod, EventList& eventlist);
    void doNextEvent();
    void registerRoce(RoceSrc& src);
private:
    simtime_picosec _scanPeriod;
    typedef list<RoceSrc*> roces_t;
    roces_t _roces;
};

#endif
