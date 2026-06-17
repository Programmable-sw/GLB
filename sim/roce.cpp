// -*- c-basic-offset: 4; indent-tabs-mode: nil -*- 
#include <math.h>
#include <iostream>
#include <algorithm>
#include "roce.h"
#include "queue.h"
#include <stdio.h>
#include "switch.h"
#include "trigger.h"
#include "ecn.h"
using namespace std;

static uint32_t nmrc_mix(uint32_t a, uint32_t b, uint32_t c) {
    a += 0x9e3779b9;
    b += 0x9e3779b9;
    c += 0x85ebca6b;
    a -= b; a -= c; a ^= (c >> 13);
    b -= c; b -= a; b ^= (a << 8);
    c -= a; c -= b; c ^= (b >> 13);
    a -= b; a -= c; a ^= (c >> 12);
    b -= c; b -= a; b ^= (a << 16);
    c -= a; c -= b; c ^= (b >> 5);
    a -= b; a -= c; a ^= (c >> 3);
    b -= c; b -= a; b ^= (a << 10);
    c -= a; c -= b; c ^= (b >> 15);
    return c;
}

static uint32_t nmrc_gcd(uint32_t a, uint32_t b) {
    while (b != 0) {
        uint32_t t = a % b;
        a = b;
        b = t;
    }
    return a;
}

static uint32_t nmrc_priority_index(Packet::PktPriority priority) {
    switch (priority) {
    case Packet::PRIO_LO:
        return 0;
    case Packet::PRIO_MID:
        return 1;
    case Packet::PRIO_HI:
        return 2;
    case Packet::PRIO_NONE:
        return 0;
    }
    return 0;
}

static uint32_t nmrc_bitmap_count(const NmrcBitmap& bitmap) {
    uint32_t count = 0;
    for (uint32_t i = 0; i < bitmap.size(); i++) {
        if (bitmap[i])
            count++;
    }
    return count;
}

static uint32_t nmrc_bitmap_count_at_least(const NmrcBitmap& bitmap, uint8_t min_value) {
    uint32_t count = 0;
    for (uint32_t i = 0; i < bitmap.size(); i++) {
        if (bitmap[i] >= min_value)
            count++;
    }
    return count;
}

static bool nmrc_bitmap_any(const NmrcBitmap& bitmap) {
    for (uint32_t i = 0; i < bitmap.size(); i++) {
        if (bitmap[i])
            return true;
    }
    return false;
}

static bool nmrc_uses_2bit_state(uint32_t mode) {
    return mode == 1 || mode == 2;
}

static bool nmrc_uses_bad_cache_state(uint32_t mode) {
    return mode == 3;
}

static uint8_t nmrc_initial_state(uint32_t mode) {
    return nmrc_uses_2bit_state(mode) ? 3 : 1;
}

////////////////////////////////////////////////////////////////
//  ROCE SOURCE
////////////////////////////////////////////////////////////////

/* When you're debugging, sometimes it's useful to enable debugging on
   a single ROCE receiver, rather than on all of them.  Set this to the
   node ID and recompile if you need this; otherwise leave it
   alone. */
//#define LOGSINK 2332
#define LOGSINK   0 


/* keep track of RTOs.  Generally, we shouldn't see RTOs if
   return-to-sender is enabled.  Otherwise we'll see them with very
   large incasts. */
uint32_t RoceSrc::_global_node_count = 0;
uint32_t RoceSrc::_global_rto_count = 0;

/* _min_rto can be tuned using SetMinRTO. Don't change it here.  */
simtime_picosec RoceSrc::_min_rto = timeFromUs((uint32_t)DEFAULT_RTO_MIN);
simtime_picosec RoceSrc::_rto_high = 0;
RoceSrc::rx_mode_t RoceSrc::_rx_mode = RoceSrc::RX_GBN;
uint32_t RoceSrc::_sack_bitmap_bits = ROCE_SACK_BITMAP_BITS_DEFAULT;
simtime_picosec RoceSrc::_ooo_tolerance = timeFromUs(15.0);
uint32_t RoceSrc::_ooo_window_pkts = 32;
simtime_picosec RoceSrc::_nack_interval = timeFromUs(4.0);
RoceSrc::lb_mode_t RoceSrc::_lb_mode = RoceSrc::LB_ECMP;
uint32_t RoceSrc::_path_entropy_size = 256;
uint32_t RoceSrc::_reps_buffer_size = 8;
uint32_t RoceSrc::_reps_warmup_pkts = 0;
uint32_t RoceSrc::_nmrc_min_good_paths = 16;
uint32_t RoceSrc::_nmrc_hosts_per_tor = 1;
simtime_picosec RoceSrc::_nmrc_bad_hold_down = 0;
uint32_t RoceSrc::_nmrc_state_mode = 3;
uint32_t RoceSrc::_nmrc_weak_sample_pkts = 0;
uint32_t RoceSrc::_nmrc_ecn_degrade_mode = 0;
bool RoceSrc::_nmrc_unknown_reopen = false;
uint32_t RoceSrc::_nmrc_bad_cache_windows = 1;
uint32_t RoceSrc::_mrc_active_path_count = 256;
uint32_t RoceSrc::_mrc_backup_path_count = 256;
uint32_t RoceSrc::_mrc_min_active_paths = 16;
simtime_picosec RoceSrc::_mrc_ecn_cooldown = timeFromUs(20.0);
simtime_picosec RoceSrc::_mrc_failed_retry = timeFromUs(100.0);
uint32_t RoceSrc::_mrc_probe_interval_pkts = 256;
simtime_picosec RoceSrc::_conweave_rtt_threshold = timeFromUs(16.0);
simtime_picosec RoceSrc::_conweave_min_reroute_gap = timeFromUs(4.0);
uint32_t RoceSrc::_ndp_initial_window = 256;
RoceSrc::cc_mode_t RoceSrc::_cc_mode = RoceSrc::CC_DCQCN_VARIANT;
uint32_t RoceSrc::_cc_initial_cwnd_pkts = 100;
uint32_t RoceSrc::_cc_min_cwnd_pkts = 1;
uint32_t RoceSrc::_cc_max_cwnd_pkts = 0;
double RoceSrc::_dcqcn_g = 1.0 / 256.0;
double RoceSrc::_dcqcn_initial_alpha = 0.0625;
linkspeed_bps RoceSrc::_dcqcn_ai_rate = 4000000000ULL;
linkspeed_bps RoceSrc::_dcqcn_min_rate = 1000000000ULL;
mem_b RoceSrc::_dcqcn_byte_counter = 10 * 1024 * 1024;
uint32_t RoceSrc::_dcqcn_fast_recovery_steps = 5;
simtime_picosec RoceSrc::_dcqcn_alpha_interval = timeFromUs(55.0);
simtime_picosec RoceSrc::_dcqcn_rate_increase_interval = timeFromUs(55.0);
simtime_picosec RoceSrc::_dcqcn_cnp_interval = timeFromUs(50.0);
RoceSrc::dcqcn_nack_reaction_t RoceSrc::_dcqcn_nack_reaction = RoceSrc::DCQCN_NACK_AS_CNP;
std::map<std::pair<uint32_t, uint32_t>, NmrcBitmap> RoceSrc::_nmrc_shared_bitmaps;
std::map<std::pair<uint32_t, uint32_t>, simtime_picosec> RoceSrc::_nmrc_shared_hold_until;
std::map<std::pair<uint32_t, uint32_t>, std::vector<NmrcBitmap> > RoceSrc::_nmrc_shared_bad_epochs;
std::map<std::pair<uint32_t, uint32_t>, uint32_t> RoceSrc::_nmrc_shared_bad_epoch_cursor;

RoceSrc::RoceSrc(RoceLogger* logger, TrafficLogger* pktlogger, EventList &eventlist, linkspeed_bps rate)
    : BaseQueue(rate,eventlist,NULL), _flow(pktlogger), _logger(logger)
{
    _mss = Packet::data_packet_size();
    _end_trigger = NULL;

    _stop_time = 0;
    _flow_started = false;
    _base_rtt = timeInf;
    _acked_packets = 0;
    _packets_sent = 0;
    _new_packets_sent = 0;
    _rtx_packets_sent = 0;
    _acks_received = 0;
    _nacks_received = 0;

    _highest_sent = 0;
    _last_acked = 0;
    _srcaddr = UINT32_MAX;
    _dstaddr = UINT32_MAX;

    _sink = 0;
    _done = false;

    _rtt = 0;
    _rto = timeFromMs(20);
    _mdev = 0;
    _drops = 0;
    _flow_size = ((uint64_t)1)<<63;
  
    _node_num = _global_node_count++;
    _nodename = "rocesrc " + to_string(_node_num);

    _pathid = random()%256;
    _reps_head = 0;
    _reps_valid_count = 0;
    _reps_explore_remaining = 0;
    reset_reps_buffer();
    _nmrc_path_bitmap.assign(_path_entropy_size ? _path_entropy_size : 1, nmrc_initial_state(_nmrc_state_mode));
    _nmrc_hold_until = 0;
    _nmrc_bad_epoch_cursor = 0;
    _ndp_cursor = 0;
    _ndp_pull_credit = 0;
    _ndp_paths_ready = false;
    _mrc_active_cursor = 0;
    _mrc_backup_cursor = 0;
    _mrc_path_space = 0;
    _mrc_paths_ready = false;
    _conweave_last_reroute = 0;
    _nmrc_cursor.fill(0);
    _nmrc_stride.fill(1);
    _nmrc_cursor_ready.fill(false);
    _rtx_queue.clear();
    reset_sack_recovery_state();
    reset_congestion_control();

    //cout << _nodename << " path id is " << _pathid << endl;

    // debugging hack
    _log_me = false;
    //if (get_id() == 144212)
    //    _log_me = true;

    _state_send = READY;
    _time_last_sent = 0;
    _send_event_time = 0;
    _send_event_handle = EventList::nullHandle();
    _send_event_pending = false;
    _rtx_timeout = timeInf;
    update_packet_spacing();
}

/*mem_b RoceSrc::queuesize(){
  return 0;
  }

  mem_b RoceSrc::maxsize(){
  return 0;
  }*/

void RoceSrc::set_traffic_logger(TrafficLogger* pktlogger) {
    _flow.set_logger(pktlogger);
}

void RoceSrc::schedule_send(simtime_picosec when) {
    if (_done)
        return;
    assert(when >= eventlist().now());

    if (_send_event_pending) {
        if (when >= _send_event_time)
            return;
        if (_send_event_handle != EventList::nullHandle())
            eventlist().cancelPendingSourceByHandle(*this, _send_event_handle);
        _send_event_pending = false;
        _send_event_handle = EventList::nullHandle();
        _send_event_time = 0;
    }

    _send_event_handle = eventlist().sourceIsPendingGetHandle(*this, when);
    _send_event_pending = _send_event_handle != EventList::nullHandle();
    _send_event_time = when;
}

void RoceSrc::schedule_send_now() {
    schedule_send(eventlist().now());
}

void RoceSrc::update_packet_spacing() {
    double rate = (double)_bitrate;
    if (_cc_mode == CC_DCQCN && _dcqcn_current_rate > 0 && _dcqcn_current_rate < rate)
        rate = _dcqcn_current_rate;
    if (rate < 1.0)
        rate = 1.0;

    double spacing = (Packet::data_packet_size()+RocePacket::ACKSIZE) * (pow(10.0,12.0) * 8) / rate;
    if (spacing < 1.0)
        spacing = 1.0;
    _packet_spacing = (simtime_picosec)spacing;
}

void RoceSrc::reset_congestion_control() {
    _cc_cwnd_pkts = _cc_initial_cwnd_pkts ? _cc_initial_cwnd_pkts : 1;
    _cc_inflate_pkts = 0;
    clamp_congestion_window();

    _dcqcn_alpha = _dcqcn_initial_alpha;
    _dcqcn_current_rate = (double)_bitrate;
    _dcqcn_target_rate = (double)_bitrate;
    _dcqcn_bytes_since_increase = 0;
    _dcqcn_recovery_count = 0;
    _dcqcn_seen_cnp = false;
    _dcqcn_marked_since_alpha = false;
    _dcqcn_last_cnp = 0;
    _dcqcn_next_alpha_update = eventlist().now() + _dcqcn_alpha_interval;
    _dcqcn_next_rate_increase = eventlist().now() + _dcqcn_rate_increase_interval;
    clamp_dcqcn_rate();
    update_packet_spacing();
}

void RoceSrc::reset_rtx_timeout() {
    if (!_flow_started || _done || _highest_sent <= _last_acked) {
        _rtx_timeout = timeInf;
        return;
    }
    _rtx_timeout = eventlist().now() + current_rto_interval();
}

simtime_picosec RoceSrc::current_rto_interval() const {
    if (_rx_mode == RX_SP_RETX_QUEUE && _rto_high) {
        if (_highest_sent > _last_acked && _highest_sent - _last_acked > 3 * _mss)
            return _rto_high;
        return _min_rto;
    }
    return _rto;
}

void RoceSrc::clamp_congestion_window() {
    double min_cwnd = _cc_min_cwnd_pkts ? _cc_min_cwnd_pkts : 1;
    if (_cc_max_cwnd_pkts && _cc_cwnd_pkts > _cc_max_cwnd_pkts)
        _cc_cwnd_pkts = _cc_max_cwnd_pkts;
    if (_cc_cwnd_pkts < min_cwnd)
        _cc_cwnd_pkts = min_cwnd;
}

double RoceSrc::congestion_window_available() const {
    if (_cc_mode == CC_NONE)
        return 1.0;

    double outstanding_pkts = 0;
    if (_highest_sent > _last_acked)
        outstanding_pkts = ((double)(_highest_sent - _last_acked)) / _mss;

    if (_cc_mode == CC_DCQCN)
        return _cc_cwnd_pkts - outstanding_pkts;

    return _cc_cwnd_pkts + _cc_inflate_pkts - outstanding_pkts;
}

bool RoceSrc::congestion_window_allows_send() const {
    if (_cc_mode == CC_NONE)
        return true;
    return congestion_window_available() >= 1.0;
}

void RoceSrc::update_congestion_control_on_ack(const RoceAck& ack, double newly_acked_pkts) {
    if (_cc_mode == CC_NONE)
        return;

    if (_cc_mode == CC_DCQCN) {
        dcqcn_update_alpha_timer();
        if (ack.flags() & ECN_ECHO)
            dcqcn_on_cnp();
        dcqcn_maybe_increase(newly_acked_pkts);
        return;
    }

    if (ack.flags() & ECN_ECHO)
        _cc_cwnd_pkts -= 0.5;
    else
        _cc_cwnd_pkts += 1.0 / _cc_cwnd_pkts;

    clamp_congestion_window();

    _cc_inflate_pkts += 1.0;
    if (newly_acked_pkts > 0) {
        if (newly_acked_pkts >= _cc_inflate_pkts)
            _cc_inflate_pkts = 0;
        else
            _cc_inflate_pkts -= newly_acked_pkts;
    }
}

void RoceSrc::update_congestion_control_on_nack() {
    if (_cc_mode == CC_NONE)
        return;

    if (_cc_mode == CC_DCQCN) {
        if (_dcqcn_nack_reaction == DCQCN_NACK_AS_CNP) {
            dcqcn_on_cnp();
        } else if (_dcqcn_nack_reaction == DCQCN_NACK_RATE_CUT) {
            simtime_picosec now = eventlist().now();
            _dcqcn_target_rate = _dcqcn_current_rate;
            _dcqcn_current_rate *= 0.875;
            _dcqcn_bytes_since_increase = 0;
            _dcqcn_recovery_count = 0;
            _dcqcn_next_rate_increase = now + _dcqcn_rate_increase_interval;
            clamp_dcqcn_rate();
            update_packet_spacing();
        }
        return;
    }

    _cc_cwnd_pkts -= 1.0;
    clamp_congestion_window();
    _cc_inflate_pkts = 0;
}

void RoceSrc::clamp_dcqcn_rate() {
    double line_rate = (double)_bitrate;
    double min_rate = _dcqcn_min_rate ? (double)_dcqcn_min_rate : 1.0;

    if (_dcqcn_current_rate < min_rate)
        _dcqcn_current_rate = min_rate;
    if (_dcqcn_target_rate < min_rate)
        _dcqcn_target_rate = min_rate;
    if (_dcqcn_current_rate > line_rate)
        _dcqcn_current_rate = line_rate;
    if (_dcqcn_target_rate > line_rate)
        _dcqcn_target_rate = line_rate;

    if (_dcqcn_alpha < 0.0)
        _dcqcn_alpha = 0.0;
    if (_dcqcn_alpha > 1.0)
        _dcqcn_alpha = 1.0;
}

void RoceSrc::dcqcn_update_alpha_timer() {
    if (!_dcqcn_alpha_interval)
        return;

    simtime_picosec now = eventlist().now();
    while (now >= _dcqcn_next_alpha_update) {
        if (!_dcqcn_marked_since_alpha)
            _dcqcn_alpha *= (1.0 - _dcqcn_g);
        _dcqcn_marked_since_alpha = false;
        _dcqcn_next_alpha_update += _dcqcn_alpha_interval;
    }
    clamp_dcqcn_rate();
}

void RoceSrc::dcqcn_on_cnp() {
    simtime_picosec now = eventlist().now();
    _dcqcn_marked_since_alpha = true;

    if (_dcqcn_seen_cnp && _dcqcn_cnp_interval && now - _dcqcn_last_cnp < _dcqcn_cnp_interval)
        return;

    _dcqcn_target_rate = _dcqcn_current_rate;
    double cut = 1.0 - _dcqcn_alpha / 2.0;
    if (cut < 0.0)
        cut = 0.0;
    _dcqcn_current_rate *= cut;
    _dcqcn_alpha = (1.0 - _dcqcn_g) * _dcqcn_alpha + _dcqcn_g;

    _dcqcn_bytes_since_increase = 0;
    _dcqcn_recovery_count = 0;
    _dcqcn_seen_cnp = true;
    _dcqcn_last_cnp = now;
    _dcqcn_next_alpha_update = now + _dcqcn_alpha_interval;
    _dcqcn_next_rate_increase = now + _dcqcn_rate_increase_interval;

    clamp_dcqcn_rate();
    update_packet_spacing();
}

void RoceSrc::dcqcn_increase_rate() {
    if (_dcqcn_recovery_count < _dcqcn_fast_recovery_steps) {
        _dcqcn_current_rate = (_dcqcn_target_rate + _dcqcn_current_rate) / 2.0;
        _dcqcn_recovery_count++;
    } else {
        _dcqcn_target_rate += (double)_dcqcn_ai_rate;
        _dcqcn_current_rate = (_dcqcn_target_rate + _dcqcn_current_rate) / 2.0;
    }

    clamp_dcqcn_rate();
}

void RoceSrc::dcqcn_maybe_increase(double newly_acked_pkts) {
    if (newly_acked_pkts > 0.0)
        _dcqcn_bytes_since_increase += (mem_b)(newly_acked_pkts * _mss);

    simtime_picosec now = eventlist().now();
    bool changed = false;
    uint32_t updates = 0;

    while (updates < 64) {
        bool due_timer = _dcqcn_rate_increase_interval && now >= _dcqcn_next_rate_increase;
        bool due_bytes = _dcqcn_byte_counter > 0 && _dcqcn_bytes_since_increase >= _dcqcn_byte_counter;
        if (!due_timer && !due_bytes)
            break;

        dcqcn_increase_rate();
        changed = true;
        updates++;

        if (due_timer)
            _dcqcn_next_rate_increase += _dcqcn_rate_increase_interval;
        if (due_bytes)
            _dcqcn_bytes_since_increase -= _dcqcn_byte_counter;
    }

    if (changed)
        update_packet_spacing();
}

void RoceSrc::log_me() {
    // avoid looping
    if (_log_me == true)
        return;

    cout << "Enabling logging on RoceSrc " << _nodename << endl;
    _log_me = true;
    if (_sink)
        _sink->log_me();
}

void RoceSrc::SpRtxQueue::clear() {
    _seqs.clear();
}

bool RoceSrc::SpRtxQueue::empty() const {
    return _seqs.empty();
}

void RoceSrc::SpRtxQueue::insert(RocePacket::seq_t seq) {
    _seqs.insert(seq);
}

void RoceSrc::SpRtxQueue::erase(RocePacket::seq_t seq) {
    _seqs.erase(seq);
}

void RoceSrc::SpRtxQueue::erase_acked(RocePacket::seq_t last_acked) {
    while (!_seqs.empty() && *_seqs.begin() <= last_acked)
        _seqs.erase(_seqs.begin());
}

bool RoceSrc::SpRtxQueue::pop_next(RocePacket::seq_t last_acked,
                                   RocePacket::seq_t highest_sent,
                                   uint64_t flow_size,
                                   RocePacket::seq_t& seq) {
    while (!_seqs.empty()) {
        RocePacket::seq_t candidate = *_seqs.begin();
        _seqs.erase(_seqs.begin());
        if (candidate <= last_acked)
            continue;
        if (candidate > highest_sent)
            continue;
        if (flow_size && candidate > flow_size)
            continue;
        seq = candidate;
        return true;
    }
    return false;
}

size_t RoceSrc::SpRtxQueue::size() const {
    return _seqs.size();
}

bool RoceSrc::has_retransmit_work() const {
    return _rx_mode == RX_SP_RETX_QUEUE && !_rtx_queue.empty();
}

void RoceSrc::clean_retransmit_queue() {
    _rtx_queue.erase_acked(_last_acked);
}

void RoceSrc::reset_sack_recovery_state() {
    _sack_rxt_psn = 0;
    _sack_rxt_psn_updated = 0;
    _sack_rxt_psn_valid = false;
}

simtime_picosec RoceSrc::sack_rxtpsn_reset_interval() const {
    if (_rtt > 0)
        return _rtt;
    if (_base_rtt != timeInf)
        return _base_rtt;
    if (_ooo_tolerance > 0)
        return _ooo_tolerance;
    return _min_rto;
}

void RoceSrc::maybe_reset_sack_rxtpsn() {
    if (!_sack_rxt_psn_valid)
        return;
    simtime_picosec threshold = sack_rxtpsn_reset_interval();
    if (threshold == 0)
        return;
    if (eventlist().now() - _sack_rxt_psn_updated >= threshold)
        _sack_rxt_psn_valid = false;
}

bool RoceSrc::sack_seq_blocked_by_rxtpsn(RocePacket::seq_t seq) const {
    return _sack_rxt_psn_valid && seq <= _sack_rxt_psn;
}

void RoceSrc::update_sack_rxtpsn(RocePacket::seq_t psn) {
    if (!_sack_rxt_psn_valid || psn > _sack_rxt_psn) {
        _sack_rxt_psn = psn;
        _sack_rxt_psn_valid = true;
        _sack_rxt_psn_updated = eventlist().now();
    }
}

void RoceSrc::startflow(){
    cout << "startflow " << _flow._name << " at " << timeAsUs(eventlist().now()) << endl;
    _flow_started = true;
    _highest_sent = 0;
    _last_acked = 0;
    _rtx_queue.clear();
    reset_sack_recovery_state();
    _rto = _min_rto;
    _rtx_timeout = timeInf;
    
    _acked_packets = 0;
    _packets_sent = 0;
    _done = false;
    reset_congestion_control();
    if (_lb_mode == LB_REPS)
        reset_reps_buffer();
    if (_lb_mode == LB_MRC)
        reset_mrc_paths();
    if (_lb_mode == LB_NDP) {
        _ndp_pull_credit = _ndp_initial_window;
        _ndp_paths_ready = false;
    }
    
    schedule_send_now();
}

void RoceSrc::set_end_trigger(Trigger& end_trigger) {
    _end_trigger = &end_trigger;
}

void RoceSrc::connect(Route* routeout, Route* routeback, RoceSink& sink, simtime_picosec starttime) {
    assert(routeout);
    _route = routeout;
    
    _sink = &sink;
    _flow.set_id(get_id()); // identify the packet flow with the ROCE source that generated it
    _flow._name = _name;
    _sink->connect(*this, routeback);

    if (starttime != TRIGGER_START) {
        if (starttime == 0)
            startflow();
        else
            schedule_send(starttime);
    }
    //else cout << "TRIGGER START " << _nodename << endl; 
}

/* Process a NACK.  Generally this involves queuing the NACKed packet
   for retransmission, but then waiting for a PULL to actually resend
   it.  However, sometimes the NACK has the PULL bit set, and then we
   resend immediately */
void RoceSrc::processNack(const RoceNack& nack){
    RoceNack::seq_t ackno = nack.ackno();
    if (_rx_mode == RX_SP_RETX_QUEUE && ackno < _last_acked)
        return;
    if (ackno > _last_acked)
        _last_acked = ackno;

    if (_rx_mode == RX_SP_RETX_QUEUE) {
        clean_retransmit_queue();

        RocePacket::seq_t first_missing = _last_acked + 1;

        if (nack.has_sack()) {
            maybe_reset_sack_rxtpsn();

            {
                RocePacket::seq_t sack_start = nack.sack_bitmap_start_psn();
                if (sack_start == 0)
                    sack_start = first_missing + (uint64_t)nack.sack_offset() * _mss;

                if (sack_start > first_missing) {
                    for (RocePacket::seq_t seq = first_missing; seq < sack_start; seq += _mss) {
                        if (seq <= _last_acked)
                            continue;
                        if (seq > _highest_sent)
                            break;
                        if (_flow_size && seq > _flow_size)
                            break;
                        if (!sack_seq_blocked_by_rxtpsn(seq))
                            _rtx_queue.insert(seq);
                    }
                }

                uint16_t valid_length = nack.sack_bitmap_valid_length();
                if (valid_length > _sack_bitmap_bits)
                    valid_length = _sack_bitmap_bits;

                if (valid_length == 0) {
                    if (first_missing <= _highest_sent &&
                        (!_flow_size || first_missing <= _flow_size) &&
                        !sack_seq_blocked_by_rxtpsn(first_missing)) {
                        _rtx_queue.insert(first_missing);
                    }
                }

                for (uint32_t bit = 0; bit < valid_length; bit++) {
                    RocePacket::seq_t seq = sack_start + (uint64_t)bit * _mss;
                    if (seq <= _last_acked)
                        continue;
                    if (seq > _highest_sent)
                        break;
                    if (_flow_size && seq > _flow_size)
                        break;

                    if (nack.sack_bit(bit)) {
                        _rtx_queue.erase(seq);
                    } else if (!sack_seq_blocked_by_rxtpsn(seq)) {
                        _rtx_queue.insert(seq);
                    }
                }

                if (valid_length > 0)
                    update_sack_rxtpsn(sack_start + (uint64_t)(valid_length - 1) * _mss);
            }
        } else if (first_missing <= _highest_sent &&
                   (!_flow_size || first_missing <= _flow_size)) {
            _rtx_queue.insert(first_missing);
        }

        if (_log_me)
            cout << "Src " << get_id() << " queued " << _rtx_queue.size()
                 << " selective retransmissions after nack " << _last_acked
                 << " sack_offset " << (nack.has_sack() ? nack.sack_offset() : 0)
                 << " at " << timeAsUs(eventlist().now()) << " us" << endl;
    } else {
        uint64_t old_highest = _highest_sent;
        if (_last_acked < _highest_sent)
            _rtx_packets_sent += (_highest_sent - _last_acked + _mss - 1) / _mss;

        if (_log_me)
            cout << "Src " << get_id() << " go back n from " << old_highest
                 << " to " << _last_acked << " at "
                 << timeAsUs(eventlist().now()) << " us" << endl;

        _highest_sent = _last_acked;
    }

    if (_flow_size && _highest_sent>=_flow_size && _last_acked < _flow_size){
        // restart pacing; it may have stopped once we passed the flow size.
        if (_log_me)
            cout << "Src " << get_id() << " restarting pacing\n";
        schedule_send_now();
    }

    update_congestion_control_on_nack();
    update_mrc_on_nack(nack);
    reset_rtx_timeout();
    if (_lb_mode == LB_NDP) {
        grant_ndp_credit();
        if (_state_send == READY)
            schedule_send_now();
    }
    if (_lb_mode != LB_NDP && _state_send == READY &&
        (has_retransmit_work() || congestion_window_allows_send()))
        schedule_send_now();

    //this packet be sent when it is time to send a new packet!
}

/* Process an ACK.  Mostly just housekeeping*/
void RoceSrc::processAck(const RoceAck& ack) {
    RoceAck::seq_t ackno = ack.ackno();
    simtime_picosec ts = ack.ts();
    uint64_t old_last_acked = _last_acked;

    // Compute rtt.  This comes originally from TCP, and may not be optimal for ROCE */
    uint64_t m = eventlist().now()-ts;

    if (m!=0){
        if (_rtt>0){
            uint64_t abs;
            if (m>_rtt)
                abs = m - _rtt;
            else
                abs = _rtt - m;

            _mdev = 3 * _mdev / 4 + abs/4;
            _rtt = 7*_rtt/8 + m/8;

            _rto = _rtt + 4*_mdev;
        } else {
            _rtt = m;
            _mdev = m/2;
            _rto = _rtt + 4*_mdev;
        }
        if (_base_rtt==timeInf || _base_rtt > m)
            _base_rtt = m;
    }

    if (_rto < _min_rto)
        _rto = _min_rto * ((drand() * 0.5) + 0.75);

    double newly_acked_pkts = 0;
    if (ackno > _last_acked) { // a brand new ack    
        // we should probably cancel the rtx timer for any acked by
        // the cumulative ack, but we'll get an ACK or NACK anyway in
        // due course.
        _last_acked = ackno;
        clean_retransmit_queue();
        _sack_rxt_psn_valid = false;
        newly_acked_pkts = ((double)(ackno - old_last_acked)) / _mss;
        reset_rtx_timeout();
    }
    if (_logger) _logger->logRoce(*this, RoceLogger::ROCE_RCV);

    update_congestion_control_on_ack(ack, newly_acked_pkts);
    update_reps(ack);
    update_conweave(ack, m);
    update_nmrc(ack);
    update_mrc_on_ack(ack);
    if (_lb_mode == LB_NDP && !_done) {
        grant_ndp_credit();
        if (_state_send == READY)
            schedule_send_now();
    }
    if (_lb_mode != LB_NDP && !_done && _state_send == READY &&
        (has_retransmit_work() || congestion_window_allows_send()))
        schedule_send_now();

    if (_log_me)
        cout << "Src " << get_id() << " ackno " << ackno << endl;
    if (ackno >= _flow_size){
        cout << "Flow " << _name << " " << get_id() << " finished at " << timeAsUs(eventlist().now()) << " total bytes " << ackno << endl;
        _done = true;
        _rtx_timeout = timeInf;
        if (_end_trigger) {
            _end_trigger->activate();
        }

        return;
    }
}

void RoceSrc::init_nmrc_priority(Packet::PktPriority priority, uint32_t path_space) {
    uint32_t prio = nmrc_priority_index(priority);
    if (_nmrc_cursor_ready[prio])
        return;

    uint32_t src = _srcaddr == UINT32_MAX ? _node_num : _srcaddr;
    uint32_t dst = _dstaddr == UINT32_MAX ? (_node_num ^ 0x5bd1e995) : _dstaddr;
    uint32_t flow = _flow.flow_id();
    uint32_t seed = nmrc_mix(src, dst, flow ^ (prio * 0x9e3779b9));

    _nmrc_cursor[prio] = seed % path_space;

    uint32_t stride = ((seed >> 8) % path_space) | 1;
    if (stride == 0)
        stride = 1;
    while (nmrc_gcd(stride, path_space) != 1)
        stride = (stride + 2) % path_space;
    if (stride == 0)
        stride = 1;
    _nmrc_stride[prio] = stride;
    _nmrc_cursor_ready[prio] = true;
}

void RoceSrc::ensure_nmrc_bitmap(uint32_t path_space) {
    if (path_space == 0)
        path_space = 1;
    if (_nmrc_path_bitmap.size() != path_space)
        _nmrc_path_bitmap.assign(path_space, nmrc_initial_state(_nmrc_state_mode));
    if (nmrc_uses_bad_cache_state(_nmrc_state_mode))
        ensure_nmrc_bad_cache(path_space);
}

void RoceSrc::ensure_nmrc_bad_cache(uint32_t path_space) {
    if (path_space == 0)
        path_space = 1;
    uint32_t windows = _nmrc_bad_cache_windows ? _nmrc_bad_cache_windows : 1;

    bool reset = _nmrc_bad_epochs.size() != windows;
    if (!reset) {
        for (uint32_t i = 0; i < _nmrc_bad_epochs.size(); i++) {
            if (_nmrc_bad_epochs[i].size() != path_space) {
                reset = true;
                break;
            }
        }
    }

    if (reset) {
        _nmrc_bad_epochs.assign(windows, NmrcBitmap(path_space, 0));
        _nmrc_bad_epoch_cursor = 0;
        _nmrc_path_bitmap.assign(path_space, 1);
    }
}

void RoceSrc::rebuild_nmrc_bad_cache_bitmap(uint32_t path_space) {
    if (path_space == 0)
        path_space = 1;
    _nmrc_path_bitmap.assign(path_space, 1);
    for (uint32_t epoch = 0; epoch < _nmrc_bad_epochs.size(); epoch++) {
        for (uint32_t i = 0; i < path_space && i < _nmrc_bad_epochs[epoch].size(); i++) {
            if (_nmrc_bad_epochs[epoch][i])
                _nmrc_path_bitmap[i] = 0;
        }
    }
}

void RoceSrc::apply_nmrc_bad_cache_feedback(const NmrcBitmap& feedback, uint32_t path_space) {
    ensure_nmrc_bad_cache(path_space);
    if (_nmrc_bad_epochs.empty())
        return;

    NmrcBitmap bad(path_space, 0);
    for (uint32_t i = 0; i < path_space; i++) {
        if (i < feedback.size() && !feedback[i])
            bad[i] = 1;
    }

    _nmrc_bad_epochs[_nmrc_bad_epoch_cursor] = bad;
    _nmrc_bad_epoch_cursor = (_nmrc_bad_epoch_cursor + 1) % _nmrc_bad_epochs.size();
    rebuild_nmrc_bad_cache_bitmap(path_space);
}

std::pair<uint32_t, uint32_t> RoceSrc::nmrc_cache_key() const {
    uint32_t hosts_per_tor = _nmrc_hosts_per_tor ? _nmrc_hosts_per_tor : 1;
    uint32_t src = _srcaddr == UINT32_MAX ? _node_num : _srcaddr;
    uint32_t dst = _dstaddr == UINT32_MAX ? 0 : _dstaddr;
    return std::make_pair(src / hosts_per_tor, dst / hosts_per_tor);
}

void RoceSrc::load_nmrc_shared_bitmap(uint32_t path_space) {
    std::pair<uint32_t, uint32_t> key = nmrc_cache_key();
    auto it = _nmrc_shared_bitmaps.find(key);
    if (it != _nmrc_shared_bitmaps.end()) {
        if (it->second.size() != path_space)
            return;
        _nmrc_path_bitmap = it->second;
    }

    auto hold_it = _nmrc_shared_hold_until.find(key);
    if (hold_it != _nmrc_shared_hold_until.end())
        _nmrc_hold_until = hold_it->second;

    if (nmrc_uses_bad_cache_state(_nmrc_state_mode)) {
        ensure_nmrc_bad_cache(path_space);
        auto epochs_it = _nmrc_shared_bad_epochs.find(key);
        if (epochs_it != _nmrc_shared_bad_epochs.end()) {
            bool compatible = epochs_it->second.size() == _nmrc_bad_epochs.size();
            for (uint32_t i = 0; compatible && i < epochs_it->second.size(); i++)
                compatible = epochs_it->second[i].size() == path_space;
            if (compatible) {
                _nmrc_bad_epochs = epochs_it->second;
                auto cursor_it = _nmrc_shared_bad_epoch_cursor.find(key);
                if (cursor_it != _nmrc_shared_bad_epoch_cursor.end() && !_nmrc_bad_epochs.empty())
                    _nmrc_bad_epoch_cursor = cursor_it->second % _nmrc_bad_epochs.size();
                rebuild_nmrc_bad_cache_bitmap(path_space);
            }
        }
    }
}

void RoceSrc::store_nmrc_shared_bitmap() {
    std::pair<uint32_t, uint32_t> key = nmrc_cache_key();
    _nmrc_shared_bitmaps[key] = _nmrc_path_bitmap;
    _nmrc_shared_hold_until[key] = _nmrc_hold_until;
    if (nmrc_uses_bad_cache_state(_nmrc_state_mode)) {
        _nmrc_shared_bad_epochs[key] = _nmrc_bad_epochs;
        _nmrc_shared_bad_epoch_cursor[key] = _nmrc_bad_epoch_cursor;
    }
}

void RoceSrc::release_nmrc_hold_if_expired(uint32_t path_space) {
    if (_nmrc_bad_hold_down == 0 || _nmrc_hold_until == 0)
        return;
    if (eventlist().now() < _nmrc_hold_until)
        return;
    _nmrc_hold_until = 0;
    _nmrc_path_bitmap.assign(path_space, nmrc_initial_state(_nmrc_state_mode));
    store_nmrc_shared_bitmap();
}

void RoceSrc::reset_mrc_paths() {
    _mrc_evs.clear();
    _mrc_active.clear();
    _mrc_backup.clear();
    _mrc_active_cursor = 0;
    _mrc_backup_cursor = 0;
    _mrc_path_space = 0;
    _mrc_paths_ready = false;
    _mrc_seq_ev.clear();
}

uint32_t RoceSrc::mrc_logical_ev_count(uint32_t path_space) const {
    if (path_space == 0)
        path_space = 1;

    uint32_t active = _mrc_active_path_count ? _mrc_active_path_count : path_space;
    uint32_t backup = _mrc_backup_path_count ? _mrc_backup_path_count : active;
    uint32_t logical = active + backup;
    if (logical < path_space)
        logical = path_space;
    if (!logical)
        logical = 1;
    return logical;
}

uint32_t RoceSrc::mrc_desired_active_paths(uint32_t path_space) const {
    if (path_space == 0)
        path_space = 1;

    uint32_t desired = _mrc_active_path_count ? _mrc_active_path_count : path_space;
    uint32_t logical = mrc_logical_ev_count(path_space);
    if (desired > logical)
        desired = logical;
    if (!desired)
        desired = 1;
    return desired;
}

bool RoceSrc::mrc_ev_in_active(uint32_t logical_ev) const {
    for (uint32_t i = 0; i < _mrc_active.size(); i++) {
        if (_mrc_active[i] == logical_ev)
            return true;
    }
    return false;
}

void RoceSrc::init_mrc_paths(uint32_t path_space) {
    if (path_space == 0)
        path_space = 1;
    uint32_t logical_count = mrc_logical_ev_count(path_space);
    if (_mrc_paths_ready && _mrc_evs.size() == logical_count &&
        _mrc_path_space == path_space)
        return;

    _mrc_evs.assign(logical_count, MrcEv());
    _mrc_active.clear();
    _mrc_backup.clear();
    _mrc_active_cursor = 0;
    _mrc_backup_cursor = 0;
    _mrc_seq_ev.clear();

    vector<uint32_t> ids(logical_count);
    vector<uint32_t> physical(path_space);
    for (uint32_t i = 0; i < logical_count; i++)
        ids[i] = i;
    for (uint32_t i = 0; i < path_space; i++)
        physical[i] = i;

    uint32_t src = _srcaddr == UINT32_MAX ? _node_num : _srcaddr;
    uint32_t dst = _dstaddr == UINT32_MAX ? (_node_num ^ 0x5bd1e995) : _dstaddr;
    uint32_t seed = nmrc_mix(src, dst, _flow.flow_id() ^ 0x4d524300);
    for (uint32_t i = 0; i < logical_count; i++) {
        uint32_t remaining = logical_count - i;
        uint32_t ix = i + (nmrc_mix(seed, i, _flow.flow_id()) % remaining);
        uint32_t tmp = ids[i];
        ids[i] = ids[ix];
        ids[ix] = tmp;
    }
    for (uint32_t i = 0; i < path_space; i++) {
        uint32_t remaining = path_space - i;
        uint32_t ix = i + (nmrc_mix(seed, i, _flow.flow_id() ^ 0x70687973) % remaining);
        uint32_t tmp = physical[i];
        physical[i] = physical[ix];
        physical[ix] = tmp;
    }

    uint32_t desired_active = mrc_desired_active_paths(path_space);
    uint32_t desired_backup = _mrc_backup_path_count ? _mrc_backup_path_count : desired_active;
    if (desired_backup > logical_count - desired_active)
        desired_backup = logical_count - desired_active;

    for (uint32_t logical = 0; logical < logical_count; logical++) {
        _mrc_evs[logical].logical_ev = logical;
        _mrc_evs[logical].physical_path = physical[logical % path_space];
        _mrc_evs[logical].state = MRC_PATH_UNUSED;
        _mrc_evs[logical].probe_successes = 0;
        _mrc_evs[logical].retry_after = 0;
    }

    for (uint32_t i = 0; i < desired_active && i < ids.size(); i++) {
        _mrc_evs[ids[i]].state = MRC_PATH_ACTIVE;
        _mrc_active.push_back(ids[i]);
    }
    for (uint32_t i = desired_active; i < desired_active + desired_backup; i++)
        _mrc_backup.push_back(ids[i]);

    _mrc_paths_ready = true;
    _mrc_path_space = path_space;
}

bool RoceSrc::mrc_ev_selectable(uint32_t logical_ev) {
    if (logical_ev >= _mrc_evs.size())
        return false;

    MrcEv& ev = _mrc_evs[logical_ev];
    if (ev.state == MRC_PATH_ACTIVE)
        return true;

    if (ev.state == MRC_PATH_COOLING && eventlist().now() >= ev.retry_after) {
        mrc_activate_ev(logical_ev);
        return ev.state == MRC_PATH_ACTIVE;
    }

    return false;
}

void RoceSrc::mrc_activate_ev(uint32_t logical_ev) {
    if (logical_ev >= _mrc_evs.size())
        return;

    if (!mrc_ev_in_active(logical_ev)) {
        uint32_t path_space = _path_entropy_size ? _path_entropy_size : 1;
        uint32_t desired = mrc_desired_active_paths(path_space);
        if (_mrc_active.size() >= desired) {
            _mrc_evs[logical_ev].state = MRC_PATH_UNUSED;
            _mrc_evs[logical_ev].retry_after = 0;
            _mrc_evs[logical_ev].probe_successes = 0;
            return;
        }
        _mrc_active.push_back(logical_ev);
    }

    _mrc_evs[logical_ev].state = MRC_PATH_ACTIVE;
    _mrc_evs[logical_ev].retry_after = 0;
    _mrc_evs[logical_ev].probe_successes = 0;
}

void RoceSrc::mrc_remove_active_ev(uint32_t logical_ev) {
    for (vector<uint32_t>::iterator it = _mrc_active.begin();
         it != _mrc_active.end();) {
        if (*it == logical_ev)
            it = _mrc_active.erase(it);
        else
            it++;
    }
    if (!_mrc_active.empty())
        _mrc_active_cursor %= _mrc_active.size();
    else
        _mrc_active_cursor = 0;
}

void RoceSrc::mrc_promote_backup(uint32_t path_space) {
    uint32_t desired = mrc_desired_active_paths(path_space);
    simtime_picosec now = eventlist().now();

    while (_mrc_active.size() < desired &&
           _mrc_backup_cursor < _mrc_backup.size()) {
        uint32_t logical_ev = _mrc_backup[_mrc_backup_cursor++];
        if (logical_ev >= _mrc_evs.size())
            continue;
        if (mrc_ev_in_active(logical_ev))
            continue;
        MrcEv& ev = _mrc_evs[logical_ev];
        if ((ev.state == MRC_PATH_FAILED || ev.state == MRC_PATH_COOLING) &&
            now < ev.retry_after)
            continue;
        ev.state = MRC_PATH_ACTIVE;
        ev.retry_after = 0;
        ev.probe_successes = 0;
        _mrc_active.push_back(logical_ev);
    }

    for (uint32_t logical_ev = 0;
         _mrc_active.size() < desired && logical_ev < _mrc_evs.size();
         logical_ev++) {
        if (mrc_ev_in_active(logical_ev))
            continue;
        MrcEv& ev = _mrc_evs[logical_ev];
        if ((ev.state == MRC_PATH_FAILED || ev.state == MRC_PATH_COOLING) &&
            now < ev.retry_after)
            continue;
        ev.state = MRC_PATH_ACTIVE;
        ev.retry_after = 0;
        ev.probe_successes = 0;
        _mrc_active.push_back(logical_ev);
    }
}

void RoceSrc::mrc_mark_congested(uint32_t logical_ev) {
    if (logical_ev >= _mrc_evs.size())
        return;
    MrcEv& ev = _mrc_evs[logical_ev];
    if (ev.state == MRC_PATH_FAILED)
        return;

    if (_mrc_ecn_cooldown == 0) {
        if (mrc_ev_in_active(logical_ev))
            ev.state = MRC_PATH_ACTIVE;
        return;
    }

    mrc_remove_active_ev(logical_ev);
    ev.state = MRC_PATH_COOLING;
    ev.probe_successes = 0;
    ev.retry_after = eventlist().now() + _mrc_ecn_cooldown;
    mrc_promote_backup(_path_entropy_size ? _path_entropy_size : 1);
}

void RoceSrc::mrc_mark_physical_congested(uint32_t physical_path) {
    if (_mrc_evs.empty())
        return;
    uint32_t path_space = _path_entropy_size ? _path_entropy_size : 1;
    uint32_t physical = physical_path % path_space;
    for (uint32_t i = 0; i < _mrc_evs.size(); i++) {
        if (_mrc_evs[i].physical_path == physical &&
            _mrc_evs[i].state != MRC_PATH_UNUSED)
            mrc_mark_congested(i);
    }
}

void RoceSrc::mrc_mark_failed(uint32_t logical_ev) {
    if (logical_ev >= _mrc_evs.size())
        return;

    mrc_remove_active_ev(logical_ev);
    _mrc_evs[logical_ev].state = MRC_PATH_FAILED;
    _mrc_evs[logical_ev].probe_successes = 0;
    _mrc_evs[logical_ev].retry_after = eventlist().now() + _mrc_failed_retry;
    mrc_promote_backup(_path_entropy_size ? _path_entropy_size : 1);
}

void RoceSrc::mrc_mark_physical_failed(uint32_t physical_path) {
    if (_mrc_evs.empty())
        return;
    uint32_t path_space = _path_entropy_size ? _path_entropy_size : 1;
    uint32_t physical = physical_path % path_space;
    for (uint32_t i = 0; i < _mrc_evs.size(); i++) {
        if (_mrc_evs[i].physical_path == physical &&
            _mrc_evs[i].state != MRC_PATH_UNUSED &&
            _mrc_evs[i].state != MRC_PATH_FAILED)
            mrc_mark_failed(i);
    }
}

uint32_t RoceSrc::mrc_choose_probe_ev(uint32_t path_space) {
    if (_mrc_probe_interval_pkts == 0 || _packets_sent == 0 ||
        _packets_sent % _mrc_probe_interval_pkts != 0)
        return UINT32_MAX;

    simtime_picosec now = eventlist().now();
    for (uint32_t i = 0; i < _mrc_evs.size(); i++) {
        if (_mrc_evs[i].state == MRC_PATH_FAILED && now >= _mrc_evs[i].retry_after) {
            _mrc_evs[i].state = MRC_PATH_PROBING;
            _mrc_evs[i].probe_successes = 0;
            return i;
        }
    }

    while (_mrc_backup_cursor < _mrc_backup.size()) {
        uint32_t logical_ev = _mrc_backup[_mrc_backup_cursor++];
        if (logical_ev >= _mrc_evs.size())
            continue;
        if (mrc_ev_in_active(logical_ev))
            continue;
        if (_mrc_evs[logical_ev].state == MRC_PATH_UNUSED) {
            _mrc_evs[logical_ev].state = MRC_PATH_PROBING;
            _mrc_evs[logical_ev].probe_successes = 0;
            return logical_ev;
        }
    }

    for (uint32_t i = 0; i < _mrc_evs.size(); i++) {
        if (_mrc_evs[i].state == MRC_PATH_UNUSED) {
            _mrc_evs[i].state = MRC_PATH_PROBING;
            _mrc_evs[i].probe_successes = 0;
            return i;
        }
    }

    return UINT32_MAX;
}

uint32_t RoceSrc::choose_mrc_path(uint32_t path_space) {
    return choose_mrc_ev(path_space).physical_path;
}

RoceSrc::MrcChoice RoceSrc::choose_mrc_ev(uint32_t path_space) {
    init_mrc_paths(path_space);

    uint32_t probe = mrc_choose_probe_ev(path_space);
    if (probe != UINT32_MAX)
        return MrcChoice(probe, _mrc_evs[probe].physical_path % path_space);

    uint32_t min_active = _mrc_min_active_paths;
    uint32_t desired = mrc_desired_active_paths(path_space);
    if (min_active > desired)
        min_active = desired;
    if (_mrc_active.size() < min_active)
        mrc_promote_backup(path_space);

    if (!_mrc_active.empty()) {
        uint32_t tries = (uint32_t)_mrc_active.size();
        for (uint32_t i = 0; i < tries; i++) {
            uint32_t ix = _mrc_active_cursor % _mrc_active.size();
            uint32_t logical_ev = _mrc_active[ix];
            _mrc_active_cursor = (ix + 1) % _mrc_active.size();
            if (mrc_ev_selectable(logical_ev))
                return MrcChoice(logical_ev, _mrc_evs[logical_ev].physical_path % path_space);
        }
    }

    for (uint32_t i = 0; i < _mrc_evs.size(); i++) {
        if (mrc_ev_selectable(i))
            return MrcChoice(i, _mrc_evs[i].physical_path % path_space);
    }

    simtime_picosec now = eventlist().now();
    for (uint32_t i = 0; i < _mrc_evs.size(); i++) {
        if (_mrc_evs[i].state == MRC_PATH_FAILED && now >= _mrc_evs[i].retry_after) {
            _mrc_evs[i].state = MRC_PATH_PROBING;
            _mrc_evs[i].probe_successes = 0;
            return MrcChoice(i, _mrc_evs[i].physical_path % path_space);
        }
    }

    if (!_mrc_evs.empty()) {
        uint32_t logical = random() % _mrc_evs.size();
        return MrcChoice(logical, _mrc_evs[logical].physical_path % path_space);
    }

    return MrcChoice(UINT32_MAX, random() % path_space);
}

void RoceSrc::note_mrc_packet_ev(RocePacket::seq_t seqno, uint32_t logical_ev) {
    if (_lb_mode != LB_MRC)
        return;
    if (logical_ev == UINT32_MAX)
        return;
    _mrc_seq_ev[seqno] = logical_ev;
}

void RoceSrc::clean_mrc_seq_evs() {
    if (_lb_mode != LB_MRC)
        return;
    while (!_mrc_seq_ev.empty()) {
        map<RocePacket::seq_t, uint32_t>::iterator it = _mrc_seq_ev.begin();
        if (it->first > _last_acked)
            break;
        _mrc_seq_ev.erase(it);
    }
}

void RoceSrc::fail_mrc_sequence(RocePacket::seq_t seqno) {
    map<RocePacket::seq_t, uint32_t>::iterator it = _mrc_seq_ev.find(seqno);
    if (it == _mrc_seq_ev.end())
        return;
    mrc_mark_failed(it->second);
}

void RoceSrc::mrc_note_clean_ack(RocePacket::seq_t ackno) {
    for (map<RocePacket::seq_t, uint32_t>::iterator it = _mrc_seq_ev.begin();
         it != _mrc_seq_ev.end() && it->first <= ackno; it++) {
        uint32_t logical_ev = it->second;
        if (logical_ev >= _mrc_evs.size())
            continue;
        MrcEv& ev = _mrc_evs[logical_ev];
        if (ev.state == MRC_PATH_PROBING) {
            ev.probe_successes++;
            mrc_activate_ev(logical_ev);
        } else if (ev.state == MRC_PATH_COOLING &&
                   eventlist().now() >= ev.retry_after) {
            mrc_activate_ev(logical_ev);
        }
    }
}

void RoceSrc::mrc_note_ecn_ack(RocePacket::seq_t ackno, uint32_t physical_path) {
    map<RocePacket::seq_t, uint32_t>::iterator it = _mrc_seq_ev.upper_bound(ackno);
    if (it != _mrc_seq_ev.begin()) {
        --it;
        if (it->first <= ackno) {
            mrc_mark_congested(it->second);
            return;
        }
    }
    mrc_mark_physical_congested(physical_path);
}

void RoceSrc::update_mrc_on_ack(const RoceAck& ack) {
    if (_lb_mode != LB_MRC)
        return;

    uint32_t path_space = _path_entropy_size ? _path_entropy_size : 1;
    init_mrc_paths(path_space);

    if (ack.flags() & ECN_ECHO)
        mrc_note_ecn_ack(ack.ackno(), ack.pathid());
    else
        mrc_note_clean_ack(ack.ackno());

    clean_mrc_seq_evs();
}

void RoceSrc::update_mrc_on_nack(const RoceNack& nack) {
    if (_lb_mode != LB_MRC)
        return;

    uint32_t path_space = _path_entropy_size ? _path_entropy_size : 1;
    init_mrc_paths(path_space);
    RocePacket::seq_t first_missing = _last_acked + 1;
    bool marked = false;

    if (nack.reason() == RoceNack::TRIM) {
        map<RocePacket::seq_t, uint32_t>::iterator it = _mrc_seq_ev.find(first_missing);
        if (it != _mrc_seq_ev.end())
            mrc_mark_congested(it->second);
        else
            mrc_mark_physical_congested(nack.pathid());
        return;
    }

    if (first_missing <= _highest_sent) {
        map<RocePacket::seq_t, uint32_t>::iterator it = _mrc_seq_ev.find(first_missing);
        if (it != _mrc_seq_ev.end()) {
            mrc_mark_failed(it->second);
            marked = true;
        }
    }

    if (nack.has_sack()) {
        RocePacket::seq_t sack_start = nack.sack_bitmap_start_psn();
        if (sack_start == 0)
            sack_start = first_missing + (uint64_t)nack.sack_offset() * _mss;

        if (sack_start > first_missing) {
            for (RocePacket::seq_t seq = first_missing; seq < sack_start; seq += _mss) {
                map<RocePacket::seq_t, uint32_t>::iterator it = _mrc_seq_ev.find(seq);
                if (it != _mrc_seq_ev.end()) {
                    mrc_mark_failed(it->second);
                    marked = true;
                }
            }
        }

        uint16_t valid_length = nack.sack_bitmap_valid_length();
        if (valid_length > _sack_bitmap_bits)
            valid_length = _sack_bitmap_bits;

        for (uint32_t bit = 0; bit < valid_length; bit++) {
            if (nack.sack_bit(bit))
                continue;
            RocePacket::seq_t seq = sack_start + (uint64_t)bit * _mss;
            map<RocePacket::seq_t, uint32_t>::iterator it = _mrc_seq_ev.find(seq);
            if (it != _mrc_seq_ev.end()) {
                mrc_mark_failed(it->second);
                marked = true;
            }
        }
    }

    if (!marked)
        mrc_mark_physical_failed(nack.pathid());
}

void RoceSrc::update_mrc_on_rto() {
    if (_lb_mode != LB_MRC)
        return;

    uint32_t path_space = _path_entropy_size ? _path_entropy_size : 1;
    init_mrc_paths(path_space);
    RocePacket::seq_t first_missing = _last_acked + 1;
    if (first_missing <= _highest_sent)
        fail_mrc_sequence(first_missing);
}

void RoceSrc::init_ndp_paths(uint32_t path_space) {
    if (path_space == 0)
        path_space = 1;
    if (_ndp_paths_ready && _ndp_path_ids.size() == path_space)
        return;

    _ndp_path_ids.resize(path_space);
    for (uint32_t i = 0; i < path_space; i++)
        _ndp_path_ids[i] = i;
    for (uint32_t i = 0; i < path_space; i++) {
        uint32_t ix = i + (random() % (path_space - i));
        uint32_t tmp = _ndp_path_ids[i];
        _ndp_path_ids[i] = _ndp_path_ids[ix];
        _ndp_path_ids[ix] = tmp;
    }
    _ndp_cursor = 0;
    _ndp_paths_ready = true;
}

uint32_t RoceSrc::choose_ndp_path(uint32_t path_space) {
    init_ndp_paths(path_space);
    if (_ndp_cursor >= _ndp_path_ids.size()) {
        _ndp_paths_ready = false;
        init_ndp_paths(path_space);
    }
    return _ndp_path_ids[_ndp_cursor++] % path_space;
}

void RoceSrc::grant_ndp_credit(uint32_t credits) {
    if (UINT32_MAX - _ndp_pull_credit < credits)
        _ndp_pull_credit = UINT32_MAX;
    else
        _ndp_pull_credit += credits;
}

uint32_t RoceSrc::choose_path(Packet::PktPriority priority, bool retransmitted) {
    uint32_t path_space = _path_entropy_size;
    if (path_space == 0)
        path_space = 1;

    if (_lb_mode == LB_REPS) {
        ensure_reps_buffer();
        if (!retransmitted && _reps_explore_remaining > 0) {
            _reps_explore_remaining--;
            return random() % path_space;
        }
        if (_reps_valid_count > 0) {
            uint32_t offset = (_reps_head + _reps_buffer.size() - _reps_valid_count) % _reps_buffer.size();
            assert(_reps_buffer[offset].valid);
            uint32_t path = _reps_buffer[offset].cached_ev;
            _reps_buffer[offset].valid = false;
            _reps_valid_count--;
            return path % path_space;
        }
        return random() % path_space;
    }

    if (_lb_mode == LB_NMRC) {
        uint32_t prio = nmrc_priority_index(priority);
        init_nmrc_priority(priority, path_space);
        ensure_nmrc_bitmap(path_space);
        load_nmrc_shared_bitmap(path_space);
        if (!nmrc_uses_bad_cache_state(_nmrc_state_mode))
            release_nmrc_hold_if_expired(path_space);

        if (nmrc_uses_bad_cache_state(_nmrc_state_mode)) {
            if (nmrc_bitmap_any(_nmrc_path_bitmap)) {
                for (uint32_t offset = 1; offset <= path_space; offset++) {
                    uint32_t candidate = (_nmrc_cursor[prio] + offset * _nmrc_stride[prio]) % path_space;
                    if (_nmrc_path_bitmap[candidate]) {
                        _nmrc_cursor[prio] = candidate;
                        return candidate;
                    }
                }
            }
            return random() % path_space;
        }

        if (nmrc_uses_2bit_state(_nmrc_state_mode)) {
            uint32_t min_good_paths = _nmrc_min_good_paths;
            if (min_good_paths > path_space)
                min_good_paths = path_space;

            if (_nmrc_weak_sample_pkts > 0 &&
                _packets_sent > 0 &&
                _packets_sent % _nmrc_weak_sample_pkts == 0) {
                for (uint32_t offset = 1; offset <= path_space; offset++) {
                    uint32_t candidate = (_nmrc_cursor[prio] + offset * _nmrc_stride[prio]) % path_space;
                    if (_nmrc_path_bitmap[candidate] == 1) {
                        _nmrc_cursor[prio] = candidate;
                        return candidate;
                    }
                }
            }

            uint8_t min_state = 3;
            if (nmrc_bitmap_count_at_least(_nmrc_path_bitmap, 3) < min_good_paths) {
                if (nmrc_bitmap_count_at_least(_nmrc_path_bitmap, 2) >= min_good_paths)
                    min_state = 2;
                else if (nmrc_bitmap_count_at_least(_nmrc_path_bitmap, 1) > 0)
                    min_state = 1;
            }

            for (uint32_t offset = 1; offset <= path_space; offset++) {
                uint32_t candidate = (_nmrc_cursor[prio] + offset * _nmrc_stride[prio]) % path_space;
                if (_nmrc_path_bitmap[candidate] >= min_state) {
                    _nmrc_cursor[prio] = candidate;
                    return candidate;
                }
            }
            return random() % path_space;
        }

        if (nmrc_bitmap_any(_nmrc_path_bitmap)) {
            for (uint32_t offset = 1; offset <= path_space; offset++) {
                uint32_t candidate = (_nmrc_cursor[prio] + offset * _nmrc_stride[prio]) % path_space;
                if (_nmrc_path_bitmap[candidate]) {
                    _nmrc_cursor[prio] = candidate;
                    return candidate;
                }
            }
        }
        return random() % path_space;
    }

    if (_lb_mode == LB_MRC)
        return choose_mrc_path(path_space);

    if (_lb_mode == LB_RR) {
        uint32_t prio = nmrc_priority_index(priority);
        init_nmrc_priority(priority, path_space);
        uint32_t candidate = (_nmrc_cursor[prio] + _nmrc_stride[prio]) % path_space;
        _nmrc_cursor[prio] = candidate;
        return candidate;
    }

    if (_lb_mode == LB_OPS)
        return random() % path_space;

    if (_lb_mode == LB_NDP)
        return choose_ndp_path(path_space);

    return _pathid % path_space;
}

void RoceSrc::ensure_reps_buffer() {
    uint32_t size = _reps_buffer_size ? _reps_buffer_size : 1;
    if (_reps_buffer.size() == size)
        return;
    _reps_buffer.assign(size, RepsBufferEntry());
    _reps_head = 0;
    _reps_valid_count = 0;
}

void RoceSrc::reset_reps_buffer() {
    uint32_t size = _reps_buffer_size ? _reps_buffer_size : 1;
    _reps_buffer.assign(size, RepsBufferEntry());
    _reps_head = 0;
    _reps_valid_count = 0;
    _reps_explore_remaining = _reps_warmup_pkts;
}

void RoceSrc::update_reps(const RoceAck& ack) {
    if (_lb_mode != LB_REPS)
        return;
    if (ack.flags() & ECN_ECHO)
        return;

    ensure_reps_buffer();
    uint32_t path_space = _path_entropy_size ? _path_entropy_size : 1;
    if (!_reps_buffer[_reps_head].valid)
        _reps_valid_count++;
    _reps_buffer[_reps_head].cached_ev = ack.pathid() % path_space;
    _reps_buffer[_reps_head].valid = true;
    _reps_head = (_reps_head + 1) % _reps_buffer.size();
}

void RoceSrc::update_conweave(const RoceAck& ack, simtime_picosec rtt) {
    if (_lb_mode != LB_CONWEAVE)
        return;

    uint32_t path_space = _path_entropy_size ? _path_entropy_size : 1;
    simtime_picosec now = eventlist().now();
    if (rtt <= _conweave_rtt_threshold)
        return;
    if (now - _conweave_last_reroute < _conweave_min_reroute_gap)
        return;

    uint32_t current = ack.pathid() % path_space;
    uint32_t next = random() % path_space;
    if (path_space > 1) {
        while (next == current)
            next = random() % path_space;
    }
    _pathid = next;
    _conweave_last_reroute = now;
}

void RoceSrc::update_nmrc(const RoceAck& ack) {
    if (_lb_mode != LB_NMRC)
        return;

    uint32_t path_space = _path_entropy_size ? _path_entropy_size : 1;
    ensure_nmrc_bitmap(path_space);

    if (!ack.has_nmrc_feedback())
        return;

    NmrcBitmap fresh_bitmap = ack.nmrc_bitmap();
    if (fresh_bitmap.size() != path_space)
        fresh_bitmap.resize(path_space, 1);

    if (nmrc_uses_bad_cache_state(_nmrc_state_mode)) {
        apply_nmrc_bad_cache_feedback(fresh_bitmap, path_space);
        store_nmrc_shared_bitmap();
        return;
    }

    uint32_t min_good_paths = _nmrc_min_good_paths;
    if (min_good_paths > path_space)
        min_good_paths = path_space;

    if (nmrc_uses_2bit_state(_nmrc_state_mode)) {
        for (uint32_t i = 0; i < path_space; i++) {
            if (!fresh_bitmap[i]) {
                if (_nmrc_state_mode == 2) {
                    if (_nmrc_ecn_degrade_mode == 1 && _nmrc_path_bitmap[i] > 1)
                        _nmrc_path_bitmap[i]--;
                    else
                        _nmrc_path_bitmap[i] = 1;
                } else {
                    _nmrc_path_bitmap[i] = 0;
                }
            } else if (fresh_bitmap[i] > 1 && _nmrc_path_bitmap[i] < 3) {
                _nmrc_path_bitmap[i]++;
            } else if (_nmrc_unknown_reopen && fresh_bitmap[i] == 1 && _nmrc_path_bitmap[i] < 2) {
                _nmrc_path_bitmap[i]++;
            }
        }
        store_nmrc_shared_bitmap();
        return;
    }

    bool hold_bad = false;
    for (uint32_t i = 0; i < path_space; i++) {
        if (!fresh_bitmap[i]) {
            hold_bad = true;
            break;
        }
    }

    if (_nmrc_bad_hold_down > 0) {
        release_nmrc_hold_if_expired(path_space);
        if (hold_bad)
            _nmrc_hold_until = eventlist().now() + _nmrc_bad_hold_down;
        if (_nmrc_hold_until && eventlist().now() < _nmrc_hold_until) {
            NmrcBitmap held = _nmrc_path_bitmap;
            for (uint32_t i = 0; i < path_space; i++) {
                if (!fresh_bitmap[i])
                    held[i] = 0;
            }
            if (nmrc_bitmap_count(held) < min_good_paths) {
                for (uint32_t i = 0; i < path_space; i++) {
                    if (fresh_bitmap[i])
                        held[i] = 1;
                }
            }
            if (!nmrc_bitmap_any(held))
                held.assign(path_space, 1);
            _nmrc_path_bitmap = held;
            store_nmrc_shared_bitmap();
            return;
        }
    }

    if (nmrc_bitmap_count(fresh_bitmap) >= min_good_paths) {
        _nmrc_path_bitmap = fresh_bitmap;
        store_nmrc_shared_bitmap();
        return;
    }

    NmrcBitmap merged = _nmrc_path_bitmap;
    for (uint32_t i = 0; i < path_space; i++) {
        if (fresh_bitmap[i])
            merged[i] = 1;
    }
    if (!nmrc_bitmap_any(merged))
        merged.assign(path_space, 1);
    _nmrc_path_bitmap = merged;
    store_nmrc_shared_bitmap();
}

void RoceSrc::processPause(const EthPausePacket& p) {
    if (p.sleepTime()>0){
        //remote end is telling us to shut up.
        //cout << "Source " << str() << " PAUSE " << timeAsUs(eventlist().now()) << endl;
        //assert(_state_send != PAUSED);
        _state_send = PAUSED;
    } else {
        //we are allowed to send!
        //assert(_state_send != READY);
        _state_send = READY;
        //cout << "Source " << str() << " RESUME " << timeAsUs(eventlist().now()) << endl;
        schedule_send_now();
    }
}

void RoceSrc::receivePacket(Packet& pkt) 
{
    if (!_flow_started){
        assert(pkt.type()==ETH_PAUSE);
        return; 
    }

    if (_stop_time && eventlist().now() >= _stop_time) {
        // stop sending new data, but allow us to finish any retransmissions
        _flow_size = _highest_sent+_mss;
        _stop_time = 0;
    }

    if (_done)
        return;

    switch (pkt.type()) {
    case ETH_PAUSE:
        processPause((const EthPausePacket&)pkt);
        pkt.free();
        return;
    case ROCENACK: 
        _nacks_received++;
        processNack((const RoceNack&)pkt);
        pkt.free();
        return;
    case ROCEACK:
        _acks_received++;
        processAck((const RoceAck&)pkt);
        pkt.free();
        return;
    default:
        abort();
    }
}

// Note: the data sequence number is the number of Byte1 of the packet, not the last byte.
bool RoceSrc::send_packet() {
    RocePacket* p = NULL;
    bool last_packet = false;
    bool retransmitted = false;
    RocePacket::seq_t seqno = 0;
    if (_log_me)
        cout << "Src " << get_id() << " send_packet\n";
    assert(_flow_started);

    clean_retransmit_queue();
    if (_rx_mode == RX_SP_RETX_QUEUE) {
        RocePacket::seq_t candidate = 0;
        if (_rtx_queue.pop_next(_last_acked, _highest_sent, _flow_size, candidate)) {
            seqno = candidate;
            retransmitted = true;
        }
    }

    if (_flow_size && _last_acked >= _flow_size) {
        //flow is finished
        if (_log_me)
            cout << "Src " << get_id() << " flow is finished, not sending\n";
        return false;
    }

    if (!retransmitted) {
        if (!congestion_window_allows_send())
            return false;
        if (_flow_size && _highest_sent >= _flow_size) {
            if (_log_me)
                cout << "Src " << get_id() << " no new packets to send\n";
            return false;
        }
        seqno = _highest_sent + 1;
    }

    if (_flow_size && seqno + _mss - 1 >= _flow_size) {
        last_packet = true;
        if (_log_me) {
            cout << _name << " " << get_id() << " sending last packet with SEQNO " << seqno << " at " << timeAsUs(eventlist().now()) << endl;
        }
    }

    p = RocePacket::newpkt(_flow, *_route, seqno, _mss, retransmitted, last_packet,_dstaddr);
    
    assert(p);
    p->set_src(_srcaddr);
    uint32_t path = 0;
    uint32_t logical_ev = UINT32_MAX;
    if (_lb_mode == LB_MRC) {
        uint32_t path_space = _path_entropy_size ? _path_entropy_size : 1;
        MrcChoice choice = choose_mrc_ev(path_space);
        path = choice.physical_path;
        logical_ev = choice.logical_ev;
    } else {
        path = choose_path(p->priority(), retransmitted);
    }
    p->set_pathid(path);
    note_mrc_packet_ev(seqno, logical_ev);

    p->flow().logTraffic(*p,*this,TrafficLogger::PKT_CREATESEND);
    p->set_ts(eventlist().now());
    
    if (_log_me) {
        cout << "Src " << get_id() << " sent " << seqno << " Flow Size: " << _flow_size
             << (retransmitted ? " retransmit" : "") << endl;
    }
    if (retransmitted) {
        _rtx_packets_sent++;
    } else {
        _highest_sent += _mss;
        _new_packets_sent++;
    }
    _packets_sent++;
    if (_highest_sent > _last_acked && _rtx_timeout == timeInf)
        reset_rtx_timeout();

    //cout << "Sent " << _highest_sent+1 << " Flow Size: " << _flow_size << " Flow " << _name << " time " << timeAsUs(eventlist().now()) << endl;

    p->sendOn();
    return true;
}

void RoceSrc::rtx_timer_hook(simtime_picosec now, simtime_picosec period) {
    if (!_flow_started || _done || _highest_sent == 0)
        return;
    if (_highest_sent <= _last_acked) {
        _rtx_timeout = timeInf;
        return;
    }
    if (_rtx_timeout == timeInf) {
        reset_rtx_timeout();
        return;
    }
    if (now < _rtx_timeout)
        return;

    if (_log_me) {
        cout << "Src " << get_id() << " RTO at " << timeAsUs(now)
             << " us, rto " << timeAsUs(_rto)
             << " us, ack " << _last_acked
             << " highest " << _highest_sent << endl;
    }

    _global_rto_count++;
    if (_rx_mode == RX_SP_RETX_QUEUE) {
        RocePacket::seq_t first_missing = _last_acked + 1;
        if (first_missing <= _highest_sent &&
            (!_flow_size || first_missing <= _flow_size)) {
            _rtx_queue.insert(first_missing);
        }
    } else {
        if (_last_acked < _highest_sent)
            _rtx_packets_sent += (_highest_sent - _last_acked + _mss - 1) / _mss;
        _highest_sent = _last_acked;
    }

    update_mrc_on_rto();
    update_congestion_control_on_nack();
    if (!(_rx_mode == RX_SP_RETX_QUEUE && _rto_high)) {
        if (_rto < _min_rto)
            _rto = _min_rto;
        else
            _rto *= 2;
    }
    reset_rtx_timeout();

    if (_lb_mode == LB_NDP)
        grant_ndp_credit();
    if (_state_send == READY && (has_retransmit_work() || congestion_window_allows_send()))
        schedule_send_now();
}

void RoceSrc::doNextEvent() {
    _send_event_pending = false;
    _send_event_time = 0;
    _send_event_handle = EventList::nullHandle();

    if (!_flow_started){
      startflow();
      return;
    }

    assert(_flow_started);
    if (_log_me) 
        cout << "Src " << get_id() << " do next event\n";
        

    if (_state_send==PAUSED) {
        if (_log_me) 
            cout << "Src " << get_id() << " paused\n";
        return;
    }

    if (_lb_mode == LB_NDP && _ndp_pull_credit == 0)
        return;

    clean_retransmit_queue();
    if (!has_retransmit_work() && !congestion_window_allows_send())
        return;

    if (_flow_size && _highest_sent >= _flow_size && !has_retransmit_work()) {
        if (_log_me) 
            cout << "Src " << get_id()  << " stopping send coz highest_sent is " << _highest_sent << endl;
        return;
    }

    if (_time_last_sent==0 || eventlist().now() - _time_last_sent >= _packet_spacing){
        if (_lb_mode == LB_NDP && _ndp_pull_credit > 0)
            _ndp_pull_credit--;
        if (send_packet())
            _time_last_sent = eventlist().now();
    }

    simtime_picosec next_send = _time_last_sent + _packet_spacing;
    if (next_send < eventlist().now())
        next_send = eventlist().now();

    if (has_retransmit_work() ||
        (congestion_window_allows_send() &&
         (!_flow_size || _highest_sent < _flow_size))) {
        schedule_send(next_send);
    }
}

////////////////////////////////////////////////////////////////
//  ROCE SINK
////////////////////////////////////////////////////////////////

/* Only use this constructor when there is only one for to this receiver */
RoceSink::RoceSink()
    : DataReceiver("roce_sink"), _cumulative_ack(0) , _total_received(0),
      _ooo_nack_timer(*this)
{
    _src = 0;
    
    _nodename = "rocesink";
    _highest_seqno = 0;
    _log_me = false;
    //if (get_id() == 144214)
    //    _log_me = true;
    _total_received = 0;
    _ooo_first_time = 0;
    _nack_silent_until = 0;
    _ooo_nack_event_pending = false;
    _ooo_nack_event_time = 0;
    _ooo_nack_path_id = 0;
}

void RoceSink::log_me() {
    // avoid looping
    if (_log_me == true)
        return;

    _log_me = true;

    if (_src)
        _src->log_me();  
}

/* Connect a src to this sink. */ 
void RoceSink::connect(RoceSrc& src, Route* route)
{
    _src = &src;
    _route = route;
    _cumulative_ack = 0;
    _drops = 0;
    _ooo_packets.clear();
    _highest_seqno = 0;
    _ooo_first_time = 0;
    _nack_silent_until = 0;
    cancel_ooo_nack_timer();
    _ooo_nack_path_id = 0;
}

void RoceSink::arm_ooo_nack_timer(simtime_picosec when) {
    if (RoceSrc::_rx_mode != RoceSrc::RX_SP_RETX_QUEUE || _ooo_packets.empty())
        return;
    if (when < EventList::now())
        when = EventList::now();

    if (_ooo_nack_event_pending) {
        if (when >= _ooo_nack_event_time)
            return;
        EventList::cancelPendingSourceByTime(_ooo_nack_timer, _ooo_nack_event_time);
    }

    EventList::sourceIsPending(_ooo_nack_timer, when);
    _ooo_nack_event_pending = true;
    _ooo_nack_event_time = when;
}

void RoceSink::cancel_ooo_nack_timer() {
    if (!_ooo_nack_event_pending)
        return;
    EventList::cancelPendingSourceByTime(_ooo_nack_timer, _ooo_nack_event_time);
    _ooo_nack_event_pending = false;
    _ooo_nack_event_time = 0;
}

void RoceSink::ooo_nack_timer_hook() {
    _ooo_nack_event_pending = false;
    _ooo_nack_event_time = 0;

    if (RoceSrc::_rx_mode != RoceSrc::RX_SP_RETX_QUEUE || _ooo_packets.empty())
        return;

    simtime_picosec now = EventList::now();
    simtime_picosec ooo_ready = _ooo_first_time + RoceSrc::_ooo_tolerance;
    if (_ooo_first_time != 0 && now < ooo_ready) {
        arm_ooo_nack_timer(ooo_ready);
        return;
    }
    if (now < _nack_silent_until) {
        arm_ooo_nack_timer(_nack_silent_until);
        return;
    }

    uint16_t sack_offset = 0;
    uint16_t sack_valid_length = 0;
    uint64_t bitmap_low = 0;
    uint64_t bitmap_high = 0;
    RocePacket::seq_t sack_start_psn = 0;
    build_sack_bitmap(_cumulative_ack, bitmap_low, bitmap_high,
                      sack_start_psn, sack_valid_length, sack_offset);
    send_nack(now, _cumulative_ack, _ooo_nack_path_id, bitmap_low, sack_offset,
              true, RoceNack::OOO, bitmap_high, sack_start_psn,
              sack_valid_length);
    _nack_silent_until = now + RoceSrc::_nack_interval;

    if (!_ooo_packets.empty())
        arm_ooo_nack_timer(_nack_silent_until);
}


// Receive a packet.
// Note: _cumulative_ack is the last byte we've ACKed.
// seqno is the first byte of the new packet.
void RoceSink::receivePacket(Packet& pkt) {
    /*
      if (random()%10==0){
      pkt.free();
      return;
      }*/

    assert(pkt.dst () == _src->_dstaddr);

    switch (pkt.type()) {
    case ROCE:
        break;
    default:
        abort();
    }

    RocePacket *p = (RocePacket*)(&pkt);
    RocePacket::seq_t seqno = p->seqno();
    if (_log_me) {
        cout << "Sink " << get_id() << " recv'd " << seqno << endl;
    }
    simtime_picosec ts = p->ts();
    //bool last_packet = ((RocePacket*)&pkt)->last_packet();

    if (pkt.header_only()) {
        uint16_t sack_offset = 0;
        uint16_t sack_valid_length = 0;
        bool has_sack = RoceSrc::_rx_mode == RoceSrc::RX_SP_RETX_QUEUE;
        uint64_t bitmap_low = 0;
        uint64_t bitmap_high = 0;
        RocePacket::seq_t sack_start_psn = 0;
        if (has_sack) {
            build_sack_bitmap(_cumulative_ack, bitmap_low, bitmap_high,
                              sack_start_psn, sack_valid_length, sack_offset);
        }
        send_nack(ts, _cumulative_ack, p->path_id(), bitmap_low, sack_offset,
                  has_sack, RoceNack::TRIM, bitmap_high, sack_start_psn,
                  sack_valid_length);
        pkt.flow().logTraffic(pkt,*this,TrafficLogger::PKT_RCVDESTROY);
        pkt.free();
        return;
    }

    int size = p->size()-RocePacket::ACKSIZE; 
    RocePacket::seq_t packet_end = seqno + size - 1;
    if (packet_end > _highest_seqno)
        _highest_seqno = packet_end;

    if (seqno > _cumulative_ack+1) {
        if (RoceSrc::_rx_mode != RoceSrc::RX_SP_RETX_QUEUE) {
            send_nack(ts, _cumulative_ack, p->path_id());
            pkt.flow().logTraffic(pkt,*this,TrafficLogger::PKT_RCVDESTROY);
            pkt.free();
            return;
        }

        _ooo_packets[seqno] = size;
        if (_ooo_first_time == 0) {
            _ooo_first_time = EventList::now();
            _ooo_nack_path_id = p->path_id();
            arm_ooo_nack_timer(_ooo_first_time + RoceSrc::_ooo_tolerance);
        }
        if (should_send_sp_nack()) {
            uint16_t sack_offset = 0;
            uint16_t sack_valid_length = 0;
            uint64_t bitmap_low = 0;
            uint64_t bitmap_high = 0;
            RocePacket::seq_t sack_start_psn = 0;
            build_sack_bitmap(_cumulative_ack, bitmap_low, bitmap_high,
                              sack_start_psn, sack_valid_length, sack_offset);
            send_nack(ts, _cumulative_ack, p->path_id(), bitmap_low, sack_offset,
                      true, RoceNack::OOO, bitmap_high, sack_start_psn,
                      sack_valid_length);
            _nack_silent_until = EventList::now() + RoceSrc::_nack_interval;
            arm_ooo_nack_timer(_nack_silent_until);
            pkt.flow().logTraffic(pkt,*this,TrafficLogger::PKT_RCVDESTROY);
            pkt.free();
            return;
        }
    } else if (seqno == _cumulative_ack+1) { // it's the next expected seq no
        _cumulative_ack = seqno + size - 1;
        while (_ooo_packets.find(_cumulative_ack + 1) != _ooo_packets.end()) {
            RocePacket::seq_t next_seq = _cumulative_ack + 1;
            int next_size = _ooo_packets[next_seq];
            _ooo_packets.erase(next_seq);
            _cumulative_ack = next_seq + next_size - 1;
        }
        if (RoceSrc::_rx_mode == RoceSrc::RX_SP_RETX_QUEUE) {
            if (_ooo_packets.empty()) {
                _ooo_first_time = 0;
                cancel_ooo_nack_timer();
            }
            else {
                _ooo_first_time = EventList::now();
                arm_ooo_nack_timer(_ooo_first_time + RoceSrc::_ooo_tolerance);
            }
        }
    } else if (seqno < _cumulative_ack+1) {
        //must have been a bad retransmit
        if (RoceSrc::_rx_mode == RoceSrc::RX_SP_RETX_QUEUE && should_send_sp_nack()) {
            uint16_t sack_offset = 0;
            uint16_t sack_valid_length = 0;
            uint64_t bitmap_low = 0;
            uint64_t bitmap_high = 0;
            RocePacket::seq_t sack_start_psn = 0;
            build_sack_bitmap(_cumulative_ack, bitmap_low, bitmap_high,
                              sack_start_psn, sack_valid_length, sack_offset);
            send_nack(ts, _cumulative_ack, p->path_id(), bitmap_low, sack_offset,
                      true, RoceNack::OOO, bitmap_high, sack_start_psn,
                      sack_valid_length);
            _nack_silent_until = EventList::now() + RoceSrc::_nack_interval;
            arm_ooo_nack_timer(_nack_silent_until);
            pkt.flow().logTraffic(pkt,*this,TrafficLogger::PKT_RCVDESTROY);
            pkt.free();
            return;
        }
    }
    send_ack(*p, ts);
    // have we seen everything yet?
    pkt.flow().logTraffic(pkt,*this,TrafficLogger::PKT_RCVDESTROY);
    pkt.free();
}

void RoceSink::send_ack(const RocePacket& pkt, simtime_picosec ts) {
    RoceAck *ack = 0;
    ack = RoceAck::newpkt(_src->_flow, *_route, _cumulative_ack,_srcaddr);
    if (_log_me)
        cout << "Sink " << get_id() << " sending ack " << _cumulative_ack << endl;
    ack->set_pathid(pkt.path_id());
    ack->set_ts(ts);
    if (pkt.flags() & ECN_CE)
        ack->set_flags(ack->flags() | ECN_ECHO);
    if (pkt.has_nmrc_feedback())
        ack->set_nmrc_feedback(pkt.nmrc_bitmap());
    ack->sendOn();
}

void RoceSink::build_sack_bitmap(RocePacket::seq_t ackno, uint64_t& sack_bitmap_low,
                                 uint64_t& sack_bitmap_high,
                                 RocePacket::seq_t& sack_bitmap_start_psn,
                                 uint16_t& sack_bitmap_valid_length,
                                 uint16_t& sack_offset) const {
    sack_bitmap_low = 0;
    sack_bitmap_high = 0;
    sack_bitmap_valid_length = 0;
    sack_offset = 0;
    RocePacket::seq_t first_traced = ackno + 1;
    sack_bitmap_start_psn = first_traced;
    uint64_t first_ooo_offset = UINT64_MAX;

    for (map<RocePacket::seq_t, int>::const_iterator it = _ooo_packets.begin();
         it != _ooo_packets.end(); it++) {
        if (it->first < first_traced)
            continue;
        uint64_t offset = (it->first - first_traced) / _src->_mss;
        if (offset < first_ooo_offset)
            first_ooo_offset = offset;
    }

    if (first_ooo_offset == UINT64_MAX)
        return;

    uint64_t block_start = 0;
    uint32_t sack_bitmap_bits = RoceSrc::_sack_bitmap_bits;
    if (first_ooo_offset >= sack_bitmap_bits)
        block_start = first_ooo_offset - (sack_bitmap_bits - 1);
    sack_offset = block_start > UINT16_MAX ? UINT16_MAX : (uint16_t)block_start;
    sack_bitmap_start_psn = first_traced + block_start * _src->_mss;

    for (uint32_t bit = 0; bit < sack_bitmap_bits; bit++) {
        RocePacket::seq_t seq = first_traced + (block_start + bit) * _src->_mss;
        if (_ooo_packets.find(seq) != _ooo_packets.end()) {
            if (bit < 64)
                sack_bitmap_low |= 1ULL << bit;
            else
                sack_bitmap_high |= 1ULL << (bit - 64);
            sack_bitmap_valid_length = bit + 1;
        }
    }
}

bool RoceSink::should_send_sp_nack() const {
    if (_ooo_packets.empty())
        return false;
    if (EventList::now() < _nack_silent_until)
        return false;

    bool ooo_timeout = _ooo_first_time != 0 &&
        EventList::now() >= _ooo_first_time + RoceSrc::_ooo_tolerance;
    uint64_t window_bytes = (uint64_t)RoceSrc::_ooo_window_pkts * _src->_mss;
    bool ooo_window = _highest_seqno > _cumulative_ack &&
        _highest_seqno - _cumulative_ack > window_bytes;

    return ooo_timeout || ooo_window;
}

RoceNack* RoceSink::send_nack(simtime_picosec ts, RocePacket::seq_t ackno, uint32_t path_id,
                              uint64_t sack_bitmap, uint16_t sack_offset, bool has_sack,
                              RoceNack::nack_reason_t reason,
                              uint64_t sack_bitmap_high,
                              RocePacket::seq_t sack_bitmap_start_psn,
                              uint16_t sack_bitmap_valid_length) {
    RoceNack *nack = NULL;
    nack = RoceNack::newpkt(_src->_flow, *_route, ackno,_srcaddr,sack_bitmap,
                            sack_offset, has_sack, sack_bitmap_high,
                            sack_bitmap_start_psn, sack_bitmap_valid_length,
                            RoceSrc::_sack_bitmap_bits);
    if (_log_me)
        cout << "Sink " << get_id() << " sending nack " << ackno
             << " sack_offset " << sack_offset
             << " sack_start " << sack_bitmap_start_psn
             << " sack_valid " << sack_bitmap_valid_length
             << " bitmap_low " << sack_bitmap
             << " bitmap_high " << sack_bitmap_high << endl;

    nack->set_pathid(path_id);
    nack->set_reason(reason);
    assert(nack);
    nack->flow().logTraffic(*nack,*this,TrafficLogger::PKT_CREATE);
    nack->set_ts(ts);
    nack->sendOn();
    return nack;
}

////////////////////////////////////////////////////////////////
//  ROCE RETRANSMISSION TIMER
////////////////////////////////////////////////////////////////

RoceRtxTimerScanner::RoceRtxTimerScanner(simtime_picosec scanPeriod, EventList& eventlist)
    : EventSource(eventlist, "RoceRtxScanner"), _scanPeriod(scanPeriod)
{
    eventlist.sourceIsPendingRel(*this, _scanPeriod);
}

void RoceRtxTimerScanner::registerRoce(RoceSrc& src)
{
    _roces.push_back(&src);
}

void RoceRtxTimerScanner::doNextEvent()
{
    simtime_picosec now = eventlist().now();
    roces_t::iterator i;
    for (i = _roces.begin(); i != _roces.end(); i++) {
        (*i)->rtx_timer_hook(now, _scanPeriod);
    }
    eventlist().sourceIsPendingRel(*this, _scanPeriod);
}
