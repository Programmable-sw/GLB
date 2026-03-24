#ifndef __GLB_ROUTING_H__
#define __GLB_ROUTING_H__

#include <iostream>
#include <map>
#include <vector>
#include "ns3/object.h"
#include "ns3/packet.h"
#include "ns3/tag.h"
#include "ns3/simulator.h"
#include "ns3/nstime.h"
#include "ns3/event-id.h"
#include "ns3/settings.h"
#include "ns3/callback.h" // 支持 Callback

namespace ns3 {

class CustomHeader; 
class SwitchMmu;

// GCN 报文 Tag：高优先级 UDP 广播，周期发送
class GcnTag : public Tag {
public:
    static TypeId GetTypeId(void);
    virtual TypeId GetInstanceTypeId(void) const;
    virtual uint32_t GetSerializedSize(void) const;
    virtual void Serialize(TagBuffer i) const;
    virtual void Deserialize(TagBuffer i);
    virtual void Print(std::ostream& os) const;

    uint32_t originSwitchId;
    uint8_t b_global;           // 【修改】：使用全局共享缓存水位
    uint16_t portScores[128];   // 【修改】：放大为 uint16_t，防止截断
};

// LSN 报文 Tag：最高优先级 UDP 组播，链路变动触发
class LsnTag : public Tag {
public:
    static TypeId GetTypeId(void);
    virtual TypeId GetInstanceTypeId(void) const;
    virtual uint32_t GetSerializedSize(void) const;
    virtual void Serialize(TagBuffer i) const;
    virtual void Deserialize(TagBuffer i);
    virtual void Print(std::ostream& os) const;

    uint32_t linkId;
    uint8_t isDown; // 1 for down, 0 for up
};

// 交换机维护的 2-hop 路径表项
struct GlbPathEntry {
    uint32_t nhId;           // 下一跳设备 ID
    uint32_t nnhId;          // 下下一跳设备 ID
    uint8_t outPort;         // 本地出端口
    uint8_t neighborOutPort; // 下一跳的出端口
    uint8_t quality;         // 质量等级
    bool linkAvail;          // 链路可用性
    
    // 冗余状态（仅用于日志或调试）
    uint8_t qLocal;
    uint8_t qNext;
};

class GlbRouting : public Object {
public:
    // ====== 核心表结构 ======
    std::map<uint32_t, std::vector<uint32_t>> m_dst2PathIds; 
    std::map<uint32_t, GlbPathEntry> m_pathTable;

    static TypeId GetTypeId(void);
    GlbRouting();
    virtual ~GlbRouting();

    void SetSwitchInfo(bool isToR, uint32_t switch_id, uint32_t num_ports);
    void SetPortGroups(const std::vector<uint32_t>& uplinks, const std::vector<uint32_t>& downlinks);
    void SetMmu(Ptr<SwitchMmu> mmu) { m_mmu = mmu; }

    uint32_t RouteInput(Ptr<Packet> p, CustomHeader& ch, const std::vector<int>& nexthops);
    void SendGcn();
    void SendLsn(uint32_t linkId, bool isDown);
    void PrintRoutingTable() const;

    typedef Callback<void, Ptr<Packet>, CustomHeader&, uint32_t, uint32_t> SwitchSendCallback;
    void SetSwitchSendCallback(SwitchSendCallback cb) { m_switchSendCallback = cb; }

    // ====== 发送字节数的回调 ======
    typedef Callback<uint64_t, uint32_t> GetTxBytesCallback;
    void SetGetTxBytesCallback(GetTxBytesCallback cb) { m_getTxBytesCallback = cb; }

    // ====== 是否被 PFC 暂停的回调 ======
    typedef Callback<bool, uint32_t> GetPortPausedCallback;
    void SetGetPortPausedCallback(GetPortPausedCallback cb) { m_getPortPausedCallback = cb; }

private:
    bool m_isToR;
    uint32_t m_switch_id;
    uint32_t m_num_ports; 
    EventId m_gcnTimer;
    
    std::vector<uint32_t> m_uplinks;
    std::vector<uint32_t> m_downlinks;

    // 回调函数变量
    SwitchSendCallback m_switchSendCallback; 
    GetTxBytesCallback m_getTxBytesCallback; 
    GetPortPausedCallback m_getPortPausedCallback; // 新增 PFC 变量
    Ptr<SwitchMmu> m_mmu; 

    // ====== 高频探测的变量 ======
    ns3::Time m_lastGcnTime;
    uint32_t m_gcnIntervalUs = 5; // 【按需修改】控制 GCN 发送频率 (微秒)
    uint64_t m_lastTxBytes[128] = {0};       
    uint8_t m_portUtilization[128] = {0};    
    uint64_t m_linkBandwidthBps = 100000000000ULL; // 100Gbps
};

} // namespace ns3
#endif