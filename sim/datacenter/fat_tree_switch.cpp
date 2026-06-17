// -*- c-basic-offset: 4; indent-tabs-mode: nil -*-
#include "fat_tree_switch.h"
#include "routetable.h"
#include "fat_tree_topology.h"
#include "callback_pipe.h"
#include "queue_lossless.h"
#include "queue_lossless_output.h"
#include "rocepacket.h"
#include "ecn.h"
#include <limits>

unordered_map<BaseQueue*,uint32_t> FatTreeSwitch::_port_flow_counts;

class GlbGcnTimer : public EventSource {
public:
    GlbGcnTimer(EventList& eventlist, FatTreeSwitch* sw)
        : EventSource(eventlist, "glb_gcn_timer"), _sw(sw) {}

    void doNextEvent() {
        if (!_sw)
            return;
        _sw->_glb_gcn_timer_pending = false;
        _sw->glb_periodic_refresh_exports();
    }

private:
    FatTreeSwitch* _sw;
};

FatTreeSwitch::FatTreeSwitch(EventList& eventlist, string s, switch_type t, uint32_t id,simtime_picosec delay, FatTreeTopology* ft): Switch(eventlist, s) {
    _id = id;
    _type = t;
    _pipe = new CallbackPipe(delay,eventlist, this);
    _uproutes = NULL;
    _ft = ft;
    _crt_route = 0;
    _hash_salt = random();
    _last_choice = eventlist.now();
    _glb_gcn_timer = NULL;
    _glb_gcn_timer_pending = false;
    _fib = new RouteTable();
}

void FatTreeSwitch::receivePacket(Packet& pkt){
    if (pkt.type()==ETH_PAUSE){
        EthPausePacket* p = (EthPausePacket*)&pkt;
        //I must be in lossless mode!
        //find the egress queue that should process this, and pass it over for processing. 
        for (size_t i = 0;i < _ports.size();i++){
            LosslessQueue* q = (LosslessQueue*)_ports.at(i);
            if (q->getRemoteEndpoint() && ((Switch*)q->getRemoteEndpoint())->getID() == p->senderID()){
                q->receivePacket(pkt);
                break;
            }
        }
        
        return;
    }

    if (_packets.find(&pkt)==_packets.end()){
        //ingress pipeline processing.

        _packets[&pkt] = true;

        const Route * nh = getNextHop(pkt,NULL);
        if (!nh) {
            _packets.erase(&pkt);
            pkt.free();
            return;
        }
        //set next hop which is peer switch.
        pkt.set_route(*nh);

        //emulate the switching latency between ingress and packet arriving at the egress queue.
        _pipe->receivePacket(pkt); 
    }
    else {
        _packets.erase(&pkt);
        
        //egress queue processing.
        //cout << "Switch type " << _type <<  " id " << _id << " pkt dst " << pkt.dst() << " dir " << pkt.get_direction() << endl;
        pkt.sendOn();
    }
};

void FatTreeSwitch::addHostPort(int addr, int flowid, PacketSink* transport){
    Route* rt = new Route();
    rt->push_back(_ft->queues_nlp_ns[_ft->HOST_POD_SWITCH(addr)][addr][0]);
    rt->push_back(_ft->pipes_nlp_ns[_ft->HOST_POD_SWITCH(addr)][addr][0]);
    rt->push_back(transport);
    _fib->addHostRoute(addr,rt,flowid);
}

uint32_t mhash(uint32_t x) {
    x = ((x >> 16) ^ x) * 0x45d9f3b;
    x = ((x >> 16) ^ x) * 0x45d9f3b;
    x = (x >> 16) ^ x;
    return x;
}

uint32_t FatTreeSwitch::adaptive_route_p2c(vector<FibEntry*>* ecmp_set, int8_t (*cmp)(FibEntry*,FibEntry*)){
    uint32_t choice = 0, min = UINT32_MAX;
    uint32_t start, i = 0;
    static const uint16_t nr_choices = 2;
    
    do {
        start = random()%ecmp_set->size();

        Route * r= (*ecmp_set)[start]->getEgressPort();
        assert(r && r->size()>1);
        BaseQueue* q = (BaseQueue*)(r->at(0));
        assert(q);
        if (q->queuesize()<min){
            choice = start;
            min = q->queuesize();
        }
        i++;
    } while (i<nr_choices);
    return choice;
}

uint32_t FatTreeSwitch::adaptive_route(vector<FibEntry*>* ecmp_set, int8_t (*cmp)(FibEntry*,FibEntry*)){
    //cout << "adaptive_route" << endl;
    uint32_t choice = 0;

    uint32_t best_choices[256];
    uint32_t best_choices_count = 0;
  
    FibEntry* min = (*ecmp_set)[choice];
    best_choices[best_choices_count++] = choice;

    for (uint32_t i = 1; i< ecmp_set->size(); i++){
        int8_t c = cmp(min,(*ecmp_set)[i]);

        if (c < 0){
            choice = i;
            min = (*ecmp_set)[choice];
            best_choices_count = 0;
            best_choices[best_choices_count++] = choice;
        }
        else if (c==0){
            assert(best_choices_count<255);
            best_choices[best_choices_count++] = i;
        }        
    }

    assert (best_choices_count>=1);
    uint32_t choiceindex = random()%best_choices_count;
    choice = best_choices[choiceindex];
    //cout << "ECMP set choices " << ecmp_set->size() << " Choice count " << best_choices_count << " chosen entry " << choiceindex << " chosen path " << choice << " ";

    if (cmp==compare_flow_count){
        //for (uint32_t i = 0; i<best_choices_count;i++)
          //  cout << "pathcnt " << best_choices[i] << "="<< _port_flow_counts[(BaseQueue*)( (*ecmp_set)[best_choices[i]]->getEgressPort()->at(0))]<< " ";
        
        _port_flow_counts[(BaseQueue*)((*ecmp_set)[choice]->getEgressPort()->at(0))]++;
    }

    return choice;
}

uint32_t FatTreeSwitch::replace_worst_choice(vector<FibEntry*>* ecmp_set, int8_t (*cmp)(FibEntry*,FibEntry*),uint32_t my_choice){
    uint32_t best_choice = 0;
    uint32_t worst_choice = 0;

    uint32_t best_choices[256];
    uint32_t best_choices_count = 0;

    FibEntry* min = (*ecmp_set)[best_choice];
    FibEntry* max = (*ecmp_set)[worst_choice];
    best_choices[best_choices_count++] = best_choice;

    for (uint32_t i = 1; i< ecmp_set->size(); i++){
        int8_t c = cmp(min,(*ecmp_set)[i]);

        if (c < 0){
            best_choice = i;
            min = (*ecmp_set)[best_choice];
            best_choices_count = 0;
            best_choices[best_choices_count++] = best_choice;
        }
        else if (c==0){
            assert(best_choices_count<256);
            best_choices[best_choices_count++] = i;
        }        

        if (cmp(max,(*ecmp_set)[i])>0){
            worst_choice = i;
            max = (*ecmp_set)[worst_choice];
        }
    }

    //might need to play with different alternatives here, compare to worst rather than just to worst index.
    int8_t r = cmp((*ecmp_set)[my_choice],(*ecmp_set)[worst_choice]);
    assert(r>=0);

    if (r==0){
        assert (best_choices_count>=1);
        return best_choices[random()%best_choices_count];
    }
    else return my_choice;
}


int8_t FatTreeSwitch::compare_pause(FibEntry* left, FibEntry* right){
    Route * r1= left->getEgressPort();
    assert(r1 && r1->size()>1);
    LosslessOutputQueue* q1 = dynamic_cast<LosslessOutputQueue*>(r1->at(0));
    Route * r2= right->getEgressPort();
    assert(r2 && r2->size()>1);
    LosslessOutputQueue* q2 = dynamic_cast<LosslessOutputQueue*>(r2->at(0));

    if (!q1->is_paused()&&q2->is_paused())
        return 1;
    else if (q1->is_paused()&&!q2->is_paused())
        return -1;
    else 
        return 0;
}

int8_t FatTreeSwitch::compare_flow_count(FibEntry* left, FibEntry* right){
    Route * r1= left->getEgressPort();
    assert(r1 && r1->size()>1);
    BaseQueue* q1 = (BaseQueue*)(r1->at(0));
    Route * r2= right->getEgressPort();
    assert(r2 && r2->size()>1);
    BaseQueue* q2 = (BaseQueue*)(r2->at(0));

    if (_port_flow_counts.find(q1)==_port_flow_counts.end())
        _port_flow_counts[q1] = 0;

    if (_port_flow_counts.find(q2)==_port_flow_counts.end())
        _port_flow_counts[q2] = 0;

    //cout << "CMP q1 " << q1 << "=" << _port_flow_counts[q1] << " q2 " << q2 << "=" << _port_flow_counts[q2] << endl; 

    if (_port_flow_counts[q1] < _port_flow_counts[q2])
        return 1;
    else if (_port_flow_counts[q1] > _port_flow_counts[q2] )
        return -1;
    else 
        return 0;
}

int8_t FatTreeSwitch::compare_queuesize(FibEntry* left, FibEntry* right){
    Route * r1= left->getEgressPort();
    assert(r1 && r1->size()>1);
    BaseQueue* q1 = dynamic_cast<BaseQueue*>(r1->at(0));
    Route * r2= right->getEgressPort();
    assert(r2 && r2->size()>1);
    BaseQueue* q2 = dynamic_cast<BaseQueue*>(r2->at(0));

    if (q1->quantized_queuesize() < q2->quantized_queuesize())
        return 1;
    else if (q1->quantized_queuesize() > q2->quantized_queuesize())
        return -1;
    else 
        return 0;
}

int8_t FatTreeSwitch::compare_bandwidth(FibEntry* left, FibEntry* right){
    Route * r1= left->getEgressPort();
    assert(r1 && r1->size()>1);
    BaseQueue* q1 = dynamic_cast<BaseQueue*>(r1->at(0));
    Route * r2= right->getEgressPort();
    assert(r2 && r2->size()>1);
    BaseQueue* q2 = dynamic_cast<BaseQueue*>(r2->at(0));

    if (q1->quantized_utilization() < q2->quantized_utilization())
        return 1;
    else if (q1->quantized_utilization() > q2->quantized_utilization())
        return -1;
    else 
        return 0;

    /*if (q1->average_utilization() < q2->average_utilization())
        return 1;
    else if (q1->average_utilization() > q2->average_utilization())
        return -1;
    else 
        return 0;        */
}

int8_t FatTreeSwitch::compare_pqb(FibEntry* left, FibEntry* right){
    //compare pause, queuesize, bandwidth.
    int8_t p = compare_pause(left, right);

    if (p!=0)
        return p;
    
    p = compare_queuesize(left,right);

    if (p!=0)
        return p;

    return compare_bandwidth(left,right);
}

int8_t FatTreeSwitch::compare_pq(FibEntry* left, FibEntry* right){
    //compare pause, queuesize, bandwidth.
    int8_t p = compare_pause(left, right);

    if (p!=0)
        return p;
    
    return compare_queuesize(left,right);
}

int8_t FatTreeSwitch::compare_qb(FibEntry* left, FibEntry* right){
    //compare pause, queuesize, bandwidth.
    int8_t p = compare_queuesize(left, right);

    if (p!=0)
        return p;
    
    return compare_bandwidth(left,right);
}

int8_t FatTreeSwitch::compare_pb(FibEntry* left, FibEntry* right){
    //compare pause, queuesize, bandwidth.
    int8_t p = compare_pause(left, right);

    if (p!=0)
        return p;
    
    return compare_bandwidth(left,right);
}

void FatTreeSwitch::permute_paths(vector<FibEntry *>* uproutes) {
    if (!uproutes)
        return;
    int len = uproutes->size();
    for (int i = 0; i < len; i++) {
        int ix = random() % (len - i);
        FibEntry* tmppath = (*uproutes)[ix];
        (*uproutes)[ix] = (*uproutes)[len-1-i];
        (*uproutes)[len-1-i] = tmppath;
    }
}

FatTreeSwitch::routing_strategy FatTreeSwitch::_strategy = FatTreeSwitch::NIX;
uint16_t FatTreeSwitch::_ar_fraction = 0;
uint16_t FatTreeSwitch::_ar_sticky = FatTreeSwitch::PER_PACKET;
simtime_picosec FatTreeSwitch::_sticky_delta = timeFromUs((uint32_t)10);
double FatTreeSwitch::_ecn_threshold_fraction = 1.0;
double FatTreeSwitch::_speculative_threshold_fraction = 0.2;
double FatTreeSwitch::_glb_downstream_weight = 0.25;
double FatTreeSwitch::_glb_queue_weight = 0.5;
double FatTreeSwitch::_glb_util_weight = 0.05;
double FatTreeSwitch::_glb_remote_queue_weight = 0.5;
double FatTreeSwitch::_glb_remote_util_weight = 0.05;
double FatTreeSwitch::_glb_remote_busy_weight = 0.2;
double FatTreeSwitch::_glb_quality_bucket = 20.0;
uint32_t FatTreeSwitch::_glb_max_quality = 7;
simtime_picosec FatTreeSwitch::_glb_update_interval = timeFromUs(1.0);
uint32_t FatTreeSwitch::_glb_quality_levels = 8;
uint32_t FatTreeSwitch::_glb_min_choices = 3;
simtime_picosec FatTreeSwitch::_glb_gcn_update_interval = timeFromUs(1.0);
simtime_picosec FatTreeSwitch::_glb_gcn_aging_interval = timeFromUs(20.0);
bool FatTreeSwitch::_glb_normalize_scores = false;
bool FatTreeSwitch::_glb_local_damping = false;
uint32_t FatTreeSwitch::_nmrc_feedback_pkts = 100;
simtime_picosec FatTreeSwitch::_nmrc_feedback_min_interval = timeFromUs(5.0);
simtime_picosec FatTreeSwitch::_nmrc_feedback_max_interval = timeFromUs(20.0);
uint32_t FatTreeSwitch::_nmrc_path_count = 1;
bool FatTreeSwitch::_nmrc_feedback_observed_values = false;
bool FatTreeSwitch::_nmrc_feedback_bad_only = false;
bool FatTreeSwitch::_pathid_only_hash = false;
int8_t (*FatTreeSwitch::fn)(FibEntry*,FibEntry*)= &FatTreeSwitch::compare_queuesize;

uint32_t FatTreeSwitch::glb_queue_kbytes(BaseQueue* q) {
    if (!q)
        return 0;

    uint64_t q_kbytes = q->queuesize() / 1000;
    if (q_kbytes > 65535)
        q_kbytes = 65535;

    return (uint32_t)q_kbytes;
}

double FatTreeSwitch::glb_queue_fraction(BaseQueue* q) {
    if (!q)
        return 0.0;

    mem_b maxsize = q->maxsize();
    if (maxsize <= 0)
        return 0.0;

    double fraction = (double)q->queuesize() / (double)maxsize;
    if (fraction < 0.0)
        fraction = 0.0;
    if (fraction > 1.0)
        fraction = 1.0;
    return fraction;
}

uint32_t FatTreeSwitch::glb_utilization_percent(BaseQueue* q) {
    if (!q)
        return 0;

    uint32_t utilization = q->average_utilization();
    if (utilization > 100)
        utilization = 100;

    LosslessOutputQueue* lq = dynamic_cast<LosslessOutputQueue*>(q);
    if (lq && lq->is_paused())
        utilization = 100;

    return utilization;
}

double FatTreeSwitch::glb_port_score(BaseQueue* q, double queue_weight, double util_weight) {
    if (!q)
        return 0.0;

    double queue = _glb_normalize_scores ? glb_queue_fraction(q) : (double)glb_queue_kbytes(q);
    double utilization = _glb_normalize_scores ?
        (double)glb_utilization_percent(q) / 100.0 :
        (double)glb_utilization_percent(q);

    return queue_weight * queue + util_weight * utilization;
}

void FatTreeSwitch::glb_candidate_queues(uint32_t dst, vector<BaseQueue*>& queues) {
    if (_type == TOR) {
        if (_ft->HOST_POD_SWITCH(dst) == _id) {
            for (uint32_t b = 0; b < _ft->bundlesize(TOR_TIER); b++) {
                BaseQueue* q = _ft->queues_nlp_ns[_id][dst][b];
                if (q)
                    queues.push_back(q);
            }
            return;
        }

        uint32_t agg_min, agg_max;
        if (_ft->get_tiers() == 3) {
            uint32_t podid = _id / _ft->tor_switches_per_pod();
            agg_min = _ft->MIN_POD_AGG_SWITCH(podid);
            agg_max = _ft->MAX_POD_AGG_SWITCH(podid);
        } else {
            agg_min = 0;
            agg_max = _ft->getNAGG() - 1;
        }

        for (uint32_t agg = agg_min; agg <= agg_max; agg++) {
            for (uint32_t b = 0; b < _ft->bundlesize(AGG_TIER); b++) {
                BaseQueue* q = _ft->queues_nlp_nup[_id][agg][b];
                if (q)
                    queues.push_back(q);
            }
        }
        return;
    }

    if (_type == AGG) {
        if (_ft->get_tiers() == 2 || _ft->HOST_POD(dst) == _ft->AGG_SWITCH_POD_ID(_id)) {
            uint32_t target_tor = _ft->HOST_POD_SWITCH(dst);
            for (uint32_t b = 0; b < _ft->bundlesize(AGG_TIER); b++) {
                BaseQueue* q = _ft->queues_nup_nlp[_id][target_tor][b];
                if (q)
                    queues.push_back(q);
            }
            return;
        }

        uint32_t podpos = _id % _ft->agg_switches_per_pod();
        uint32_t uplink_bundles = _ft->radix_up(AGG_TIER) / _ft->bundlesize(CORE_TIER);
        for (uint32_t l = 0; l < uplink_bundles; l++) {
            uint32_t core = l * _ft->agg_switches_per_pod() + podpos;
            for (uint32_t b = 0; b < _ft->bundlesize(CORE_TIER); b++) {
                BaseQueue* q = _ft->queues_nup_nc[_id][core][b];
                if (q)
                    queues.push_back(q);
            }
        }
        return;
    }

    if (_type == CORE) {
        uint32_t target_agg = _ft->MIN_POD_AGG_SWITCH(_ft->HOST_POD(dst)) +
                              (_id % _ft->agg_switches_per_pod());
        for (uint32_t b = 0; b < _ft->bundlesize(CORE_TIER); b++) {
            BaseQueue* q = _ft->queues_nc_nup[_id][target_agg][b];
            if (q)
                queues.push_back(q);
        }
    }
}

FatTreeSwitch::GlbPathState FatTreeSwitch::glb_compute_export_state(uint32_t dst) {
    GlbPathState state;
    vector<BaseQueue*> queues;
    glb_candidate_queues(dst, queues);
    if (queues.empty())
        return state;

    double best = std::numeric_limits<double>::max();
    double busy = 0.0;
    for (uint32_t i = 0; i < queues.size(); i++) {
        double score = glb_port_score(queues[i], _glb_remote_queue_weight, _glb_remote_util_weight);
        if (score < best)
            best = score;
        busy += _glb_normalize_scores ? glb_queue_fraction(queues[i]) : (double)glb_queue_kbytes(queues[i]);
    }

    double avg_busy = busy / queues.size();
    state.best_score = best == std::numeric_limits<double>::max() ? 0.0 : best;
    state.avg_busy = avg_busy;
    state.score = state.best_score + _glb_remote_busy_weight * state.avg_busy;
    state.candidate_count = queues.size();
    state.quality = glb_quality(state.score);
    state.link_available = true;
    state.valid = true;
    return state;
}

void FatTreeSwitch::glb_maybe_refresh_export(uint32_t dst) {
    simtime_picosec now = eventlist().now();
    GlbPathState& cached = _glb_exported_state[dst];
    if (cached.valid && _glb_gcn_update_interval > 0 &&
        now >= cached.last_update &&
        now - cached.last_update < _glb_gcn_update_interval) {
        glb_schedule_periodic_gcn();
        return;
    }

    GlbPathState state = glb_compute_export_state(dst);
    state.last_update = now;
    cached = state;
    glb_schedule_periodic_gcn();
}

void FatTreeSwitch::glb_schedule_periodic_gcn() {
    if (_strategy != GLB || _glb_gcn_update_interval == 0 ||
        _glb_gcn_timer_pending || _glb_exported_state.empty()) {
        return;
    }

    if (!_glb_gcn_timer)
        _glb_gcn_timer = new GlbGcnTimer(eventlist(), this);
    eventlist().sourceIsPendingRel(*_glb_gcn_timer, _glb_gcn_update_interval);
    _glb_gcn_timer_pending = true;
}

void FatTreeSwitch::glb_periodic_refresh_exports() {
    if (_strategy != GLB || _glb_gcn_update_interval == 0 ||
        _glb_exported_state.empty()) {
        return;
    }

    simtime_picosec now = eventlist().now();
    vector<uint32_t> dsts;
    dsts.reserve(_glb_exported_state.size());
    for (unordered_map<uint32_t,GlbPathState>::const_iterator it = _glb_exported_state.begin();
         it != _glb_exported_state.end(); ++it) {
        dsts.push_back(it->first);
    }

    for (size_t i = 0; i < dsts.size(); i++) {
        GlbPathState state = glb_compute_export_state(dsts[i]);
        state.last_update = now;
        _glb_exported_state[dsts[i]] = state;
    }
    glb_schedule_periodic_gcn();
}

uint32_t FatTreeSwitch::glb_next_hop_id(FibEntry* entry) const {
    if (!entry)
        return UINT32_MAX;

    Route *r = entry->getEgressPort();
    if (!r || r->size() <= 2)
        return UINT32_MAX;

    FatTreeSwitch* next = dynamic_cast<FatTreeSwitch*>(r->at(2));
    if (!next)
        return UINT32_MAX;

    return next->getID();
}

bool FatTreeSwitch::glb_entry_available(FibEntry* entry) const {
    uint32_t next_id = glb_next_hop_id(entry);
    if (next_id == UINT32_MAX)
        return true;

    unordered_map<uint32_t,bool>::const_iterator it = _glb_neighbor_available.find(next_id);
    return it == _glb_neighbor_available.end() || it->second;
}

const FatTreeSwitch::GlbPathState* FatTreeSwitch::glb_neighbor_snapshot(FibEntry* entry,
                                                                        uint32_t dst) const {
    if (!glb_entry_available(entry))
        return NULL;

    Route *r = entry->getEgressPort();
    if (!r || r->size() <= 2)
        return NULL;

    FatTreeSwitch* next = dynamic_cast<FatTreeSwitch*>(r->at(2));
    if (!next)
        return NULL;

    unordered_map<uint32_t,GlbPathState>::const_iterator it = next->_glb_exported_state.find(dst);
    if (it == next->_glb_exported_state.end())
        return NULL;

    simtime_picosec now = eventlist().now();
    if (!glb_snapshot_usable(it->second, now, _glb_gcn_aging_interval))
        return NULL;

    return &it->second;
}

void FatTreeSwitch::glb_mark_neighbor_link(uint32_t neighbor_id, bool available) {
    _glb_neighbor_available[neighbor_id] = available;
}

double FatTreeSwitch::glb_score(FibEntry* entry, uint32_t dst, uint32_t depth) {
    if (!glb_entry_available(entry))
        return std::numeric_limits<double>::max();

    Route *r = entry->getEgressPort();
    assert(r && r->size() > 0);

    BaseQueue* q = dynamic_cast<BaseQueue*>(r->at(0));
    if (!q)
        return std::numeric_limits<double>::max();

    double score = glb_port_score(q, _glb_queue_weight, _glb_util_weight);
    double local_score = score;

    if (depth > 0) {
        const GlbPathState* remote = glb_neighbor_snapshot(entry, dst);
        if (remote) {
            double downstream_scale = _glb_local_damping ?
                glb_downstream_scale_for_score(local_score,
                                               _glb_quality_bucket,
                                               _glb_normalize_scores) :
                1.0;
            score += _glb_downstream_weight * downstream_scale * remote->score;
        }
    }
    return score;
}

uint8_t FatTreeSwitch::glb_quality(double score) {
    uint32_t levels = _glb_quality_levels ? _glb_quality_levels : (_glb_max_quality + 1);
    return glb_quality_from_score(score, _glb_quality_bucket, levels);
}

uint32_t FatTreeSwitch::glb_best_score(uint32_t dst, uint32_t depth) {
    vector<FibEntry*> *available_hops = _fib->getRoutes(dst);
    if (!available_hops || available_hops->empty())
        return 0;

    double best = std::numeric_limits<double>::max();
    for (uint32_t i = 0; i < available_hops->size(); i++) {
        double score = glb_score((*available_hops)[i], dst, depth);
        if (score < best)
            best = score;
    }
    return best == std::numeric_limits<double>::max() ? 0 : (uint32_t)best;
}

uint32_t FatTreeSwitch::glb_route(vector<FibEntry*>* ecmp_set, uint32_t dst) {
    vector<uint8_t> qualities(ecmp_set->size(), 255);
    vector<bool> available(ecmp_set->size(), false);
    uint8_t best_quality = 255;

    for (uint32_t i = 0; i < ecmp_set->size(); i++) {
        available[i] = glb_entry_available((*ecmp_set)[i]);
        if (!available[i])
            continue;
        qualities[i] = glb_quality(glb_score((*ecmp_set)[i], dst, 1));
        if (qualities[i] < best_quality)
            best_quality = qualities[i];
    }

    vector<uint32_t> best_choices;
    uint32_t min_choices = _glb_min_choices ? _glb_min_choices : 1;
    uint32_t levels = _glb_quality_levels ? _glb_quality_levels : (_glb_max_quality + 1);
    for (uint32_t q = best_quality; q < levels && best_choices.size() < min_choices; q++) {
        for (uint32_t i = 0; i < ecmp_set->size(); i++) {
            if (available[i] && qualities[i] == q)
                best_choices.push_back(i);
        }
    }

    if (best_choices.empty())
        return random() % ecmp_set->size();
    return best_choices[random() % best_choices.size()];
}

uint32_t FatTreeSwitch::drill_route(vector<FibEntry*>* ecmp_set, uint32_t dst) {
    uint32_t candidates[3];
    uint32_t candidate_count = 0;
    uint32_t hop_count = ecmp_set->size();

    candidates[candidate_count++] = random() % hop_count;
    candidates[candidate_count++] = random() % hop_count;

    if (_drill_memory.find(dst) != _drill_memory.end())
        candidates[candidate_count++] = _drill_memory[dst] % hop_count;

    uint32_t best = candidates[0];
    for (uint32_t i = 1; i < candidate_count; i++) {
        int8_t c = fn((*ecmp_set)[best], (*ecmp_set)[candidates[i]]);
        if (c < 0)
            best = candidates[i];
    }

    _drill_memory[dst] = best;
    return best;
}

uint32_t FatTreeSwitch::pathid_ecmp_choice(Packet& pkt, uint32_t hop_count, packet_direction direction) {
    if (!_pathid_only_hash)
        return freeBSDHash(pkt.flow_id(), pkt.pathid(), _hash_salt) % hop_count;

    uint32_t pathid = pkt.pathid();
    if (_type == TOR)
        return pathid % hop_count;

    uint32_t tor_choices = _ft->radix_up(TOR_TIER);
    if (tor_choices == 0)
        tor_choices = 1;

    if (_type == AGG) {
        if (direction == UP)
            return (pathid / tor_choices) % hop_count;

        uint32_t divisor = tor_choices;
        if (_ft->get_tiers() == 3) {
            uint32_t agg_up_choices = _ft->radix_up(AGG_TIER);
            if (agg_up_choices == 0)
                agg_up_choices = 1;
            uint32_t core_down_choices = _ft->bundlesize(CORE_TIER);
            if (core_down_choices == 0)
                core_down_choices = 1;
            divisor *= agg_up_choices;
            divisor *= core_down_choices;
        }
        return (pathid / divisor) % hop_count;
    }
    if (_type == CORE) {
        uint32_t agg_up_choices = _ft->radix_up(AGG_TIER);
        if (agg_up_choices == 0)
            agg_up_choices = 1;
        return (pathid / (tor_choices * agg_up_choices)) % hop_count;
    }
    return pathid % hop_count;
}

void FatTreeSwitch::maybe_update_nmrc_feedback(Packet& pkt) {
    if (_type != TOR || pkt.type() != ROCE)
        return;

    RocePacket* roce = dynamic_cast<RocePacket*>(&pkt);
    if (!roce || roce->src() == UINT32_MAX)
        return;

    uint32_t src_tor = _ft->HOST_POD_SWITCH(roce->src());
    if (src_tor == _id)
        return;

    NmrcState& state = _nmrc_states[src_tor];
    uint32_t path_count = _nmrc_path_count ? _nmrc_path_count : 1;
    if (state.bitmap.size() != path_count) {
        state.bitmap.assign(path_count, 1);
        state.packets = 0;
    }

    uint32_t path = pkt.pathid() % path_count;
    bool ecn = (pkt.flags() & ECN_CE) != 0;
    state.packets++;
    if (ecn)
        state.bitmap[path] = 0;
    else if (!_nmrc_feedback_bad_only)
        state.bitmap[path] = _nmrc_feedback_observed_values ? 2 : 1;

    simtime_picosec now = eventlist().now();
    bool packet_trigger = state.packets >= _nmrc_feedback_pkts;
    bool time_trigger = state.packets > 0 &&
                        now - state.last_feedback >= _nmrc_feedback_max_interval;

    bool min_elapsed = now - state.last_feedback >= _nmrc_feedback_min_interval;
    if ((packet_trigger && min_elapsed) || time_trigger) {
        roce->set_nmrc_feedback(state.bitmap);
        state.bitmap.assign(path_count, 1);
        state.packets = 0;
        state.last_feedback = now;
    }
}

Route* FatTreeSwitch::getNextHop(Packet& pkt, BaseQueue* ingress_port){
    if (_strategy == GLB)
        glb_maybe_refresh_export(pkt.dst());

    vector<FibEntry*> * available_hops = _fib->getRoutes(pkt.dst());

    if (available_hops){
        //implement a form of ECMP hashing; might need to revisit based on measured performance.
        uint32_t ecmp_choice = 0;
        if (available_hops->size()>1)
            switch(_strategy){
            case NIX:
                abort();
            case ECMP:
                ecmp_choice = pathid_ecmp_choice(pkt, available_hops->size(), (*available_hops)[0]->getDirection());
                break;
            case ADAPTIVE_ROUTING:
                if (_ar_sticky==FatTreeSwitch::PER_PACKET){
                    ecmp_choice = adaptive_route(available_hops,fn); 
                } 
                else if (_ar_sticky==FatTreeSwitch::PER_FLOWLET){     
                    if (_flowlet_maps.find(pkt.flow_id())!=_flowlet_maps.end()){
                        FlowletInfo* f = _flowlet_maps[pkt.flow_id()];
                        
                        // only reroute an existing flow if its inter packet time is larger than _sticky_delta and
                        // and
                        // 50% chance happens. 
                        // and (commented out) if the switch has not taken any other placement decision that we've not seen the effects of.
                        if (eventlist().now() - f->_last > _sticky_delta && /*eventlist().now() - _last_choice > _pipe->delay() + BaseQueue::_update_period  &&*/ random()%2==0){ 
                            //cout << "AR 1 " << timeAsUs(eventlist().now()) << endl;
                            uint32_t new_route = adaptive_route(available_hops,fn); 
                            if (fn(available_hops->at(f->_egress),available_hops->at(new_route)) < 0){
                                f->_egress = new_route;
                                _last_choice = eventlist().now();
                                //cout << "Switch " << _type << ":" << _id << " choosing new path "<<  f->_egress << " for " << pkt.flow_id() << " at " << timeAsUs(eventlist().now()) << " last is " << timeAsUs(f->_last) << endl;
                            }
                        }
                        ecmp_choice = f->_egress;

                        f->_last = eventlist().now();
                    }
                    else {
                        //cout << "AR 2 " << timeAsUs(eventlist().now()) << endl;
                        ecmp_choice = adaptive_route(available_hops,fn); 
                        _last_choice = eventlist().now();

                        _flowlet_maps[pkt.flow_id()] = new FlowletInfo(ecmp_choice,eventlist().now());
                    }
                }

                break;
            case ECMP_ADAPTIVE:
                ecmp_choice = freeBSDHash(pkt.flow_id(),pkt.pathid(),_hash_salt) % available_hops->size();
                if (random()%100 < 50)
                    ecmp_choice = replace_worst_choice(available_hops,fn, ecmp_choice);
                break;
            case RR:
                if (_crt_route>=5 * available_hops->size()){
                    _crt_route = 0;
                    permute_paths(available_hops);
                }
                ecmp_choice = _crt_route % available_hops->size();
                _crt_route ++;
                break;
            case RR_ECMP:
                if (_type == TOR){
                    if (_crt_route>=5 * available_hops->size()){
                        _crt_route = 0;
                        permute_paths(available_hops);
                    }
                    ecmp_choice = _crt_route % available_hops->size();
                    _crt_route ++;
                }
                else ecmp_choice = freeBSDHash(pkt.flow_id(),pkt.pathid(),_hash_salt) % available_hops->size();
                
                break;
            case GLB:
                ecmp_choice = glb_route(available_hops, pkt.dst());
                break;
            case DRILL:
                ecmp_choice = drill_route(available_hops, pkt.dst());
                break;
            }
        
        FibEntry* e = (*available_hops)[ecmp_choice];
        pkt.set_direction(e->getDirection());
        
        return e->getEgressPort();
    }

    //no route table entries for this destination. Add them to FIB or fail. 
    if (_type == TOR){
        if ( _ft->HOST_POD_SWITCH(pkt.dst()) == _id) { 
            //this host is directly connected!
            HostFibEntry* fe = _fib->getHostRoute(pkt.dst(),pkt.flow_id());
            assert(fe);
            pkt.set_direction(DOWN);
            maybe_update_nmrc_feedback(pkt);
            return fe->getEgressPort();
        } else {
            //route packet up!
            if (_uproutes)
                _fib->setRoutes(pkt.dst(),_uproutes);
            else {
                uint32_t podid,agg_min,agg_max;

                if (_ft->get_tiers()==3) {
                    podid = _id / _ft->tor_switches_per_pod();
                    agg_min = _ft->MIN_POD_AGG_SWITCH(podid);
                    agg_max = _ft->MAX_POD_AGG_SWITCH(podid);
                }
                else {
                    agg_min = 0;
                    agg_max = _ft->getNAGG()-1;
                }

                for (uint32_t k=agg_min; k<=agg_max;k++){
                    for (uint32_t b = 0; b < _ft->bundlesize(AGG_TIER); b++) {
                        Route * r = new Route();
                        r->push_back(_ft->queues_nlp_nup[_id][k][b]);
                        assert(((BaseQueue*)r->at(0))->getSwitch() == this);

                        r->push_back(_ft->pipes_nlp_nup[_id][k][b]);
                        r->push_back(_ft->queues_nlp_nup[_id][k][b]->getRemoteEndpoint());
                        _fib->addRoute(pkt.dst(),r,1,UP);
                    }

                    /*
                      FatTreeSwitch* next = (FatTreeSwitch*)_ft->queues_nlp_nup[_id][k]->getRemoteEndpoint();
                      assert (next->getType()==AGG && next->getID() == k);
                    */
                }
                _uproutes = _fib->getRoutes(pkt.dst());
                permute_paths(_uproutes);
            }
        }
    } else if (_type == AGG) {
        if ( _ft->get_tiers()==2 || _ft->HOST_POD(pkt.dst()) == _ft->AGG_SWITCH_POD_ID(_id)) {
            //must go down!
            //target NLP id is 2 * pkt.dst()/K
            uint32_t target_tor = _ft->HOST_POD_SWITCH(pkt.dst());
            for (uint32_t b = 0; b < _ft->bundlesize(AGG_TIER); b++) {
                Route * r = new Route();
                r->push_back(_ft->queues_nup_nlp[_id][target_tor][b]);
                assert(((BaseQueue*)r->at(0))->getSwitch() == this);

                r->push_back(_ft->pipes_nup_nlp[_id][target_tor][b]);          
                r->push_back(_ft->queues_nup_nlp[_id][target_tor][b]->getRemoteEndpoint());

                _fib->addRoute(pkt.dst(),r,1, DOWN);
            }
        } else {
            //go up!
            if (_uproutes)
                _fib->setRoutes(pkt.dst(),_uproutes);
            else {
                uint32_t podpos = _id % _ft->agg_switches_per_pod();
                uint32_t uplink_bundles = _ft->radix_up(AGG_TIER) / _ft->bundlesize(CORE_TIER);
                for (uint32_t l = 0; l <  uplink_bundles ; l++) {
                    uint32_t core = l * _ft->agg_switches_per_pod() + podpos;
                    for (uint32_t b = 0; b < _ft->bundlesize(CORE_TIER); b++) {
                        BaseQueue* q = _ft->queues_nup_nc[_id][core][b];
                        Pipe* pipe = _ft->pipes_nup_nc[_id][core][b];
                        if (!q || !pipe)
                            continue;
                        Route *r = new Route();
                        r->push_back(q);
                        assert(((BaseQueue*)r->at(0))->getSwitch() == this);

                        r->push_back(pipe);
                        r->push_back(q->getRemoteEndpoint());

                        /*
                          FatTreeSwitch* next = (FatTreeSwitch*)_ft->queues_nup_nc[_id][k]->getRemoteEndpoint();
                          assert (next->getType()==CORE && next->getID() == k);
                        */
                    
                        _fib->addRoute(pkt.dst(),r,1,UP);

                        //cout << "AGG switch " << _id << " adding route to " << pkt.dst() << " via CORE " << k << " bundle_id " << b << endl;
                    }
                }
                //_uproutes = _fib->getRoutes(pkt.dst());
                permute_paths(_fib->getRoutes(pkt.dst()));
            }
        }
    } else if (_type == CORE) {
        uint32_t nup = _ft->MIN_POD_AGG_SWITCH(_ft->HOST_POD(pkt.dst())) + (_id % _ft->agg_switches_per_pod());
        for (uint32_t b = 0; b < _ft->bundlesize(CORE_TIER); b++) {
            BaseQueue* q = _ft->queues_nc_nup[_id][nup][b];
            Pipe* pipe = _ft->pipes_nc_nup[_id][nup][b];
            if (!q || !pipe)
                continue;
            Route *r = new Route();
            //cout << "CORE switch " << _id << " adding route to " << pkt.dst() << " via AGG " << nup << endl;

            r->push_back(q);
            assert(((BaseQueue*)r->at(0))->getSwitch() == this);

            r->push_back(pipe);

            r->push_back(q->getRemoteEndpoint());
            _fib->addRoute(pkt.dst(),r,1,DOWN);
        }
    }
    else {
        cerr << "Route lookup on switch with no proper type: " << _type << endl;
        abort();
    }
    if (!_fib->getRoutes(pkt.dst()))
        return NULL;

    //FIB has been filled in; return choice. 
    return getNextHop(pkt, ingress_port);
};
