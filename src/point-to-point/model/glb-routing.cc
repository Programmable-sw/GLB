#include "ns3/glb-routing.h"
#include "ns3/log.h"
#include "ns3/simulator.h"
#include "ns3/ipv4-header.h"
#include "ns3/ppp-header.h"
#include "ns3/qbb-header.h"
#include "ns3/switch-mmu.h" // 必须包含以便读取真实队列

#include <algorithm> 
#include <cstring>

namespace ns3 {

NS_LOG_COMPONENT_DEFINE("GlbRouting");
NS_OBJECT_ENSURE_REGISTERED(GlbRouting);

/* =========================================================
 * GCN Tag Implementation
 * ========================================================= */
TypeId GcnTag::GetTypeId(void) {
    static TypeId tid = TypeId("ns3::GcnTag").SetParent<Tag>().AddConstructor<GcnTag>();
    return tid;
}
TypeId GcnTag::GetInstanceTypeId(void) const { return GetTypeId(); }
uint32_t GcnTag::GetSerializedSize(void) const { 
    return sizeof(uint32_t) + sizeof(uint8_t) + 128 * sizeof(uint16_t); // 【修改】
}
void GcnTag::Serialize(TagBuffer i) const {
    i.WriteU32(originSwitchId);
    i.WriteU8(b_global); // 【修改】
    for (int p = 0; p < 128; ++p) i.WriteU16(portScores[p]); // 【修改】
}
void GcnTag::Deserialize(TagBuffer i) {
    originSwitchId = i.ReadU32();
    b_global = i.ReadU8(); // 【修改】
    for (int p = 0; p < 128; ++p) portScores[p] = i.ReadU16(); // 【修改】
}
void GcnTag::Print(std::ostream& os) const {
    os << "originSwitchId=" << originSwitchId << " b_global=" << (uint32_t)b_global; // 【修改】
}

/* =========================================================
 * LSN Tag Implementation
 * ========================================================= */
TypeId LsnTag::GetTypeId(void) {
    static TypeId tid = TypeId("ns3::LsnTag").SetParent<Tag>().AddConstructor<LsnTag>();
    return tid;
}
TypeId LsnTag::GetInstanceTypeId(void) const { return GetTypeId(); }
uint32_t LsnTag::GetSerializedSize(void) const { 
    return sizeof(uint32_t) + sizeof(uint8_t); 
}
void LsnTag::Serialize(TagBuffer i) const {
    i.WriteU32(linkId);
    i.WriteU8(isDown);
}
void LsnTag::Deserialize(TagBuffer i) {
    linkId = i.ReadU32();
    isDown = i.ReadU8();
}
void LsnTag::Print(std::ostream& os) const {
    os << "linkId=" << linkId << " isDown=" << (uint32_t)isDown;
}

/* =========================================================
 * GlbRouting Implementation
 * ========================================================= */
TypeId GlbRouting::GetTypeId(void) {
    static TypeId tid = TypeId("ns3::GlbRouting").SetParent<Object>().AddConstructor<GlbRouting>();
    return tid;
}

GlbRouting::GlbRouting() : m_isToR(false), m_switch_id(0), m_num_ports(0) {}

GlbRouting::~GlbRouting() {
    m_gcnTimer.Cancel();
}

void GlbRouting::SetSwitchInfo(bool isToR, uint32_t switch_id, uint32_t num_ports) {
    m_isToR = isToR;
    m_switch_id = switch_id;
    m_num_ports = num_ports;
    
    // 初始化时间戳
    m_lastGcnTime = Simulator::Now();
    
    // 使用自定义的个位数 us 周期启动定时器
    m_gcnTimer = Simulator::Schedule(MicroSeconds(m_gcnIntervalUs), &GlbRouting::SendGcn, this);
}

void GlbRouting::SetPortGroups(const std::vector<uint32_t>& uplinks, const std::vector<uint32_t>& downlinks) {
    m_uplinks = uplinks;
    m_downlinks = downlinks;
}

uint32_t GlbRouting::RouteInput(Ptr<Packet> p, CustomHeader& ch, const std::vector<int>& nexthops) {
    // ---------------- 1. 控制包处理 ----------------
    GcnTag gcnTag;
    if (p->PeekPacketTag(gcnTag)) {
        uint32_t originId = gcnTag.originSwitchId; 
        
        for (auto& pair : m_pathTable) {
            if (pair.second.nhId == originId) { 
                uint8_t local_port = pair.second.outPort;
                uint8_t neighbor_port = pair.second.neighborOutPort;
                
                double V = 0.0;
                
                // 获取本地真实积压和真实利用率
                uint32_t q1 = m_mmu ? (m_mmu->m_usedEgressPortBytes[local_port] / 1000) : 0; 
                uint32_t l1 = m_portUtilization[local_port];
                uint32_t score_R = gcnTag.portScores[neighbor_port];

                // ====== PFC 状态 ======
                bool isLocalPaused = !m_getPortPausedCallback.IsNull() ? m_getPortPausedCallback(local_port) : false;
                
                // // ====== pfc2path ======
                // if (isLocalPaused) {
                //     pair.second.quality = 7; // 本地被 PFC 暂停，直接赐死为最差等级 7
                // } else {
                //     double w_q1 = 0.3;
                //     double w_l1 = 0.1;
                //     double V = w_q1 * q1 + w_l1 * l1 + score_R;
                    
                //     uint32_t step_size = 30; 
                //     uint32_t congestion_level = (uint32_t)(V) / step_size;
                //     pair.second.quality = (uint8_t)std::min(congestion_level, (uint32_t)7);
                // }

                // ====== pfc2link ======
                if (isLocalPaused) {
                    l1 = 100; // 核心修复：PFC 暂停时，视为本地链路 100% 满载
                }

                // 浮点运算
                double w_q1 = 0.3;
                double w_l1 = 0.1;
                V = w_q1 * q1 + w_l1 * l1 + score_R; 

                // stepsize 32
                uint32_t total_score = (uint32_t)(V);
                pair.second.quality = (uint8_t)std::min(total_score >> 5, (uint32_t)7);
            }
        }
        return 0xFFFFFFFF; 
    }

    LsnTag lsnTag;
    if (p->PeekPacketTag(lsnTag)) {
        // [由于 LSN 需要定位到具体的 Path ID 才能标 down，目前原设计只有 linkId，
        // 之后可以把 LSN 也改为携带故障的 Node_ID 或 (nhId, nnhId) 来阻断]
        return 0xFFFFFFFF; 
    }

    // ---------------- 2. 数据包转发逻辑 ----------------
    uint32_t dstIP = ch.dip;
    uint32_t dstToRId = Settings::hostIp2SwitchId[dstIP]; 

    auto it_group = m_dst2PathIds.find(dstToRId);
    if (it_group != m_dst2PathIds.end() && !it_group->second.empty()) {
        
        uint8_t bestQuality = 255; 
        std::vector<uint8_t> bestPorts; // 用于收集所有并列第一的物理出端口

        // 遍历寻找最优质量分数
        for (uint32_t pathId : it_group->second) {
            auto it_path = m_pathTable.find(pathId);
            if (it_path != m_pathTable.end() && it_path->second.linkAvail) {
                
                // 如果发现更小的分数，清空之前的集合，重新收集
                if (it_path->second.quality < bestQuality) {
                    bestQuality = it_path->second.quality;
                    bestPorts.clear();
                    bestPorts.push_back(it_path->second.outPort);
                } 
                // 如果发现相等的分数（并列第一），加入喷洒池
                else if (it_path->second.quality == bestQuality) {
                    bestPorts.push_back(it_path->second.outPort);
                }
            }
        }

        // 逐包随机喷洒 (Random Packet Spraying)
        if (!bestPorts.empty()) {
            // 在 ns-3 中使用 rand() 进行快速随机选择（也可以用轮询 RR 变量代替）
            uint32_t spray_idx = rand() % bestPorts.size();
            return bestPorts[spray_idx]; 
        }
    }
    
    // 本节点如果未初始化 GLB 两跳表 (如 Spine/Leaf)，退化为 Flow ECMP 哈希转发
    if (nexthops.size() > 0) {
        // 异或哈希
        uint32_t hash = ch.sip ^ ch.dip ^ ch.udp.sport ^ ch.udp.dport ^ ch.l3Prot;
        return nexthops[hash % nexthops.size()];
    }
    
    return 0;
}

void GlbRouting::PrintRoutingTable() const {
    std::cout << "\n========================================================================\n";
    std::cout << "[GLB Table Dump] Switch ID: " << m_switch_id << " (IsToR: " << m_isToR << ")\n";
    std::cout << "------------------------------------------------------------------------\n";
    std::cout << "1. Group Table (m_dst2PathIds): DstToR_ID -> Candidate Path_IDs\n";
    for (auto const& pair : m_dst2PathIds) {
        std::cout << "   DstToR " << pair.first << " : ";
        for (uint32_t pathId : pair.second) {
            std::cout << std::hex << "0x" << pathId << std::dec << " ";
        }
        std::cout << "\n";
    }
    std::cout << "------------------------------------------------------------------------\n";
    std::cout << "2. Path Quality Table (m_pathTable): Path_ID -> [nhId, nnhId, outPort, Quality]\n";
    for (auto const& pair : m_pathTable) {
        std::cout << "   Path 0x" << std::hex << pair.first << std::dec 
                  << " | nhId:" << pair.second.nhId 
                  << " nnhId:" << pair.second.nnhId 
                  << " outPort:" << (uint32_t)pair.second.outPort 
                  << " Quality:" << (uint32_t)pair.second.quality 
                  << " Avail:" << pair.second.linkAvail << "\n";
    }
    std::cout << "========================================================================\n";
}

/* GCN: 周期性向所有上下游端口广播真实状态 */
void GlbRouting::SendGcn() {
    if (m_switchSendCallback.IsNull() || !m_mmu) return;
    if (m_isToR) return;

    GcnTag tag;
    tag.originSwitchId = m_switch_id;
    memset(tag.portScores, 0, sizeof(tag.portScores));

    // 1. 获取全交换机共享缓存水位 (b_global)
    uint32_t total_used_bytes = 0;
    for (uint32_t port = 1; port <= m_num_ports; ++port) {
        total_used_bytes += (m_mmu->m_usedEgressPortBytes[port] / 1000); 
    }
    tag.b_global = (m_num_ports == 0) ? 0 : (total_used_bytes / m_num_ports);

    // 2. 计算周期内的瞬时链路利用率
    ns3::Time now = Simulator::Now();
    double interval_s = (now - m_lastGcnTime).GetSeconds();
    m_lastGcnTime = now;

    // 避免首次运行或定时器极小导致的除 0 异常
    if (interval_s > 0) {
        // 基于真实的时间差计算这段时间内链路能发送的最大理论字节数
        uint64_t maxBytesPerInterval = (m_linkBandwidthBps * interval_s) / 8.0; 

        for (uint32_t i = 1; i <= m_num_ports; ++i) {
            if (!m_getTxBytesCallback.IsNull()) {
                uint64_t currentTx = m_getTxBytesCallback(i);
                uint64_t deltaTx = currentTx - m_lastTxBytes[i];
                m_lastTxBytes[i] = currentTx;
                
                // 算出极其敏锐的瞬时利用率
                double util = (double)deltaTx / maxBytesPerInterval * 100.0;
                m_portUtilization[i] = (uint8_t)std::min(util, 100.0);
            }
        }
    }

    // 3. 预计算每个端口的远端 3 因子得分 (全范围保留) && PFC 状态通告
    double w_q2 = 0.3; 
    double w_l2 = 0.1; 
    double w_b = 0.2; 
    
    // 【注意：就是这里！之前可能不小心把这个 for 循环头以及 q2, l2 的获取给删了】
    for (uint32_t i = 1; i <= m_num_ports; ++i) {
        // 获取远端的队列积压和利用率
        uint32_t q2 = m_mmu ? (m_mmu->m_usedEgressPortBytes[i] / 1000) : 0; 
        uint32_t l2 = m_portUtilization[i]; 
        
        // ====== pfc状态 ======
        bool isPaused = !m_getPortPausedCallback.IsNull() ? m_getPortPausedCallback(i) : false;
        
        // ====== pfc2path ======
        // // if (isPaused) {
        // //     // 被暂停时：直接分配远端的满分极限值，不做后续浮点运算
        // //     tag.portScores[i] = 65535; 
        // // } else {
        // //     // 正常时：使用降低利用率权重后的新公式计算 3 因子得分
        // //     double score_R = w_q2 * q2 + w_l2 * l2 + w_b * tag.b_global;
        // //     tag.portScores[i] = (uint16_t)std::min(score_R, 65535.0); 
        // // }

        // ====== pfc2link ======
        if (isPaused) {
            l2 = 100; // 核心修复：远端被暂停时，视为远端链路 100% 满载
        }

        // 恢复正常的 3 因子计算，不再一刀切通告 65535
        double score_R = w_q2 * q2 + w_l2 * l2 + w_b * tag.b_global;
        tag.portScores[i] = (uint16_t)std::min(score_R, 65535.0);
    }

    // --- 构造并发包的逻辑保持不变 ---
    Ptr<Packet> gcnPkt = Create<Packet>(64);
    gcnPkt->AddPacketTag(tag);

    Ipv4Header ipv4h;
    ipv4h.SetProtocol(0xFD); 
    ipv4h.SetTtl(1);         
    ipv4h.SetPayloadSize(gcnPkt->GetSize());
    gcnPkt->AddHeader(ipv4h);

    PppHeader ppp;
    ppp.SetProtocol(0x0021);
    gcnPkt->AddHeader(ppp);

    CustomHeader gcnCh(CustomHeader::L3_Header); 
    gcnCh.l3Prot = 0xFD;
    gcnCh.sip = Settings::node_id_to_ip(m_switch_id).Get();
    gcnCh.dip = 0xFFFFFFFF; 

    for (uint32_t p = 1; p <= m_num_ports; ++p) {
        m_switchSendCallback(gcnPkt->Copy(), gcnCh, p, 0); 
    }
    
    m_gcnTimer = Simulator::Schedule(MicroSeconds(m_gcnIntervalUs), &GlbRouting::SendGcn, this);
}

/* LSN: 严格基于上下游关系进行组播 */
void GlbRouting::SendLsn(uint32_t linkId, bool isDown) {
    if (m_isToR) return; // ToR 不发布状态信息

    // 确定当前交换机的角色并筛选组播目标端口
    std::vector<uint32_t> targetPorts;
    if (m_uplinks.empty() && !m_downlinks.empty()) {
        // 我是 Spine：向下游组（Leaf）通告
        targetPorts = m_downlinks;
    } else if (!m_uplinks.empty() && !m_downlinks.empty()) {
        // 我是 Leaf：向上游（Spine）和下游（ToR）通告
        targetPorts = m_uplinks;
        targetPorts.insert(targetPorts.end(), m_downlinks.begin(), m_downlinks.end());
    }

    for (uint32_t port : targetPorts) {
        LsnTag tag;
        tag.linkId = linkId;
        tag.isDown = isDown ? 1 : 0;

        Ptr<Packet> lsnPkt = Create<Packet>(64);
        lsnPkt->AddPacketTag(tag);

        Ipv4Header ipv4h;
        ipv4h.SetProtocol(0xFD); 
        ipv4h.SetTtl(2); // 一跳后终止
        ipv4h.SetPayloadSize(lsnPkt->GetSize());
        lsnPkt->AddHeader(ipv4h);

        PppHeader ppp;
        ppp.SetProtocol(0x0021);
        lsnPkt->AddHeader(ppp);

        CustomHeader lsnCh(CustomHeader::L3_Header);
        lsnCh.l3Prot = 0xFD;
        lsnCh.sip = Settings::node_id_to_ip(m_switch_id).Get();
        lsnCh.dip = 0xFFFFFFFF;

        m_switchSendCallback(lsnPkt->Copy(), lsnCh, port, 0);
    }
}

} // namespace ns3