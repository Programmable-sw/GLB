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
simtime_picosec RoceSrc::_ooo_tolerance = timeFromUs(15.0);
uint32_t RoceSrc::_ooo_window_pkts = 32;
simtime_picosec RoceSrc::_nack_interval = timeFromUs(4.0);
RoceSrc::lb_mode_t RoceSrc::_lb_mode = RoceSrc::LB_ECMP;
uint32_t RoceSrc::_path_entropy_size = 256;
uint32_t RoceSrc::_reps_buffer_size = 8;
uint32_t RoceSrc::_nmrc_min_good_paths = 16;
uint32_t RoceSrc::_nmrc_hosts_per_tor = 1;
simtime_picosec RoceSrc::_nmrc_bad_hold_down = 0;
uint32_t RoceSrc::_nmrc_state_mode = 3;
uint32_t RoceSrc::_nmrc_weak_sample_pkts = 0;
uint32_t RoceSrc::_nmrc_ecn_degrade_mode = 0;
bool RoceSrc::_nmrc_unknown_reopen = false;
uint32_t RoceSrc::_nmrc_bad_cache_windows = 1;
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
    reset_reps_buffer();
    _nmrc_path_bitmap.assign(_path_entropy_size ? _path_entropy_size : 1, nmrc_initial_state(_nmrc_state_mode));
    _nmrc_hold_until = 0;
    _nmrc_bad_epoch_cursor = 0;
    _ndp_cursor = 0;
    _ndp_pull_credit = 0;
    _ndp_paths_ready = false;
    _conweave_last_reroute = 0;
    _nmrc_cursor.fill(0);
    _nmrc_stride.fill(1);
    _nmrc_cursor_ready.fill(false);
    _rtx_queue.clear();
    reset_congestion_control();

    //cout << _nodename << " path id is " << _pathid << endl;

    // debugging hack
    _log_me = false;
    //if (get_id() == 144212)
    //    _log_me = true;

    _state_send = READY;
    _time_last_sent = 0;
    _send_event_time = 0;
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
        eventlist().cancelPendingSourceByTime(*this, _send_event_time);
    }

    eventlist().sourceIsPending(*this, when);
    _send_event_pending = true;
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
        dcqcn_on_cnp();
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

bool RoceSrc::has_retransmit_work() const {
    return _rx_mode == RX_SP_RETX_QUEUE && !_rtx_queue.empty();
}

void RoceSrc::clean_retransmit_queue() {
    while (!_rtx_queue.empty() && *_rtx_queue.begin() <= _last_acked)
        _rtx_queue.erase(_rtx_queue.begin());
}

void RoceSrc::startflow(){
    cout << "startflow " << _flow._name << " at " << timeAsUs(eventlist().now()) << endl;
    _flow_started = true;
    _highest_sent = 0;
    _last_acked = 0;
    _rtx_queue.clear();
    _rto = _min_rto;
    _rtx_timeout = timeInf;
    
    _acked_packets = 0;
    _packets_sent = 0;
    _done = false;
    reset_congestion_control();
    if (_lb_mode == LB_REPS)
        reset_reps_buffer();
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

        if (first_missing <= _highest_sent &&
            (!_flow_size || first_missing <= _flow_size)) {
            _rtx_queue.insert(first_missing);
        }

        if (nack.has_sack()) {
            uint64_t received_bitmap = nack.sack_bitmap();
            uint32_t sack_offset = nack.sack_offset();
            int highest_sacked_bit = -1;
            for (uint32_t bit = 0; bit < ROCE_SACK_BITMAP_BITS; bit++) {
                if (received_bitmap & (1ULL << bit))
                    highest_sacked_bit = bit;
            }

            for (int bit = 0; bit <= highest_sacked_bit; bit++) {
                RocePacket::seq_t seq = first_missing + ((uint64_t)sack_offset + bit) * _mss;
                if (seq <= _last_acked)
                    continue;
                if (seq > _highest_sent)
                    break;
                if (_flow_size && seq > _flow_size)
                    break;

                if (received_bitmap & (1ULL << bit))
                    _rtx_queue.erase(seq);
                else
                    _rtx_queue.insert(seq);
            }
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
        newly_acked_pkts = ((double)(ackno - old_last_acked)) / _mss;
        reset_rtx_timeout();
    }
    if (_logger) _logger->logRoce(*this, RoceLogger::ROCE_RCV);

    update_congestion_control_on_ack(ack, newly_acked_pkts);
    update_reps(ack);
    update_conweave(ack, m);
    update_nmrc(ack);
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

uint32_t RoceSrc::choose_path(Packet::PktPriority priority) {
    uint32_t path_space = _path_entropy_size;
    if (path_space == 0)
        path_space = 1;

    if (_lb_mode == LB_REPS) {
        ensure_reps_buffer();
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
    while (_rx_mode == RX_SP_RETX_QUEUE && !_rtx_queue.empty()) {
        RocePacket::seq_t candidate = *_rtx_queue.begin();
        _rtx_queue.erase(_rtx_queue.begin());
        if (candidate <= _last_acked)
            continue;
        if (candidate > _highest_sent)
            continue;
        if (_flow_size && candidate > _flow_size)
            continue;
        seqno = candidate;
        retransmitted = true;
        break;
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
    p->set_pathid(choose_path(p->priority()));

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
    : DataReceiver("roce_sink"),_cumulative_ack(0) , _total_received(0) 
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
        bool has_sack = RoceSrc::_rx_mode == RoceSrc::RX_SP_RETX_QUEUE;
        uint64_t bitmap = has_sack ? build_sack_bitmap(_cumulative_ack, sack_offset) : 0;
        send_nack(ts, _cumulative_ack, p->path_id(), bitmap, sack_offset, has_sack);
        pkt.flow().logTraffic(pkt,*this,TrafficLogger::PKT_RCVDESTROY);
        pkt.free();
        return;
    }

    int size = p->size()-RocePacket::ACKSIZE; 
    RocePacket::seq_t packet_end = seqno + size - 1;
    if (packet_end > _highest_seqno)
        _highest_seqno = packet_end;

    if (seqno > _cumulative_ack+1) {
        _ooo_packets[seqno] = size;
        if (RoceSrc::_rx_mode == RoceSrc::RX_SP_RETX_QUEUE) {
            if (_ooo_first_time == 0)
                _ooo_first_time = EventList::now();
            if (should_send_sp_nack()) {
                uint16_t sack_offset = 0;
                uint64_t bitmap = build_sack_bitmap(_cumulative_ack, sack_offset);
                send_nack(ts, _cumulative_ack, p->path_id(), bitmap, sack_offset, true);
                _nack_silent_until = EventList::now() + RoceSrc::_nack_interval;
                pkt.flow().logTraffic(pkt,*this,TrafficLogger::PKT_RCVDESTROY);
                pkt.free();
                return;
            }
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
            if (_ooo_packets.empty())
                _ooo_first_time = 0;
            else
                _ooo_first_time = EventList::now();
        }
    } else if (seqno < _cumulative_ack+1) {
        //must have been a bad retransmit
        if (RoceSrc::_rx_mode == RoceSrc::RX_SP_RETX_QUEUE && should_send_sp_nack()) {
            uint16_t sack_offset = 0;
            uint64_t bitmap = build_sack_bitmap(_cumulative_ack, sack_offset);
            send_nack(ts, _cumulative_ack, p->path_id(), bitmap, sack_offset, true);
            _nack_silent_until = EventList::now() + RoceSrc::_nack_interval;
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

uint64_t RoceSink::build_sack_bitmap(RocePacket::seq_t ackno, uint16_t& sack_offset) const {
    uint64_t bitmap = 0;
    sack_offset = 0;
    RocePacket::seq_t first_traced = ackno + 1;
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
        return bitmap;

    uint64_t block_start = 0;
    if (first_ooo_offset >= ROCE_SACK_BITMAP_BITS)
        block_start = first_ooo_offset - (ROCE_SACK_BITMAP_BITS - 1);
    if (block_start > UINT16_MAX)
        block_start = UINT16_MAX;
    sack_offset = (uint16_t)block_start;

    for (uint32_t bit = 0; bit < ROCE_SACK_BITMAP_BITS; bit++) {
        RocePacket::seq_t seq = first_traced + (block_start + bit) * _src->_mss;
        if (_ooo_packets.find(seq) != _ooo_packets.end())
            bitmap |= 1ULL << bit;
    }
    return bitmap;
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

void RoceSink::send_nack(simtime_picosec ts, RocePacket::seq_t ackno, uint32_t path_id,
                         uint64_t sack_bitmap, uint16_t sack_offset, bool has_sack) {
    RoceNack *nack = NULL;
    nack = RoceNack::newpkt(_src->_flow, *_route, ackno,_srcaddr,sack_bitmap,
                            sack_offset, has_sack);
    if (_log_me)
        cout << "Sink " << get_id() << " sending nack " << ackno
             << " sack_offset " << sack_offset
             << " bitmap " << sack_bitmap << endl;

    nack->set_pathid(path_id);
    assert(nack);
    nack->flow().logTraffic(*nack,*this,TrafficLogger::PKT_CREATE);
    nack->set_ts(ts);
    nack->sendOn();
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
