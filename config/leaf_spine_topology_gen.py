import os

def generate_topology(num_spines, num_leaves, os_ratio, rate="100Gbps", delay="1000ns", error_rate="0"):
    # 在 100G 网络中，OS=1 意味着每个 Leaf 连接的主机数等于其连接的 Spine 数
    hosts_per_leaf = num_spines * os_ratio
    total_hosts = num_leaves * hosts_per_leaf
    total_switches = num_leaves + num_spines
    total_nodes = total_hosts + total_switches
    
    # 链路数：主机到 Leaf 的链路 + Leaf 到 Spine 的链路
    # 注：由于是双向链路，在某些模拟器中计为1条，按原文件格式，我们仅列出一条连接
    total_links = (num_leaves * hosts_per_leaf) + (num_leaves * num_spines)

    # 节点 ID 分配
    # Hosts: 0 到 total_hosts - 1
    # Switches: total_hosts 到 total_nodes - 1
    switch_ids = list(range(total_hosts, total_nodes))
    leaf_ids = switch_ids[:num_leaves]
    spine_ids = switch_ids[num_leaves:]

    current_dir = os.getcwd()
    filename = f"leaf{num_leaves}_spine{num_spines}_100G_OS{os_ratio}.txt"
    
    with open(filename, 'w') as f:
        # 第一行: total node #, switch node #, link #
        f.write(f"{total_nodes} {total_switches} {total_links}\n")
        
        # 第二行: switch node IDs...
        f.write(" ".join(map(str, switch_ids)) + "\n")
        
        # 生成 Host 到 Leaf 的连接
        host_id = 0
        for leaf in leaf_ids:
            for _ in range(hosts_per_leaf):
                f.write(f"{host_id} {leaf} {rate} {delay} {error_rate}\n")
                host_id += 1
                
        # 生成 Leaf 到 Spine 的连接 (全互联)
        for leaf in leaf_ids:
            for spine in spine_ids:
                f.write(f"{leaf} {spine} {rate} {delay} {error_rate}\n")

    print(f"拓扑生成成功: {filename}")
    print(f"总节点数: {total_nodes}")
    print(f"主机数: {total_hosts}")
    print(f"交换机数: {total_switches} (Leaf: {num_leaves}, Spine: {num_spines})")
    print(f"总链路数: {total_links}")

if __name__ == "__main__":
    # 配置参数
    SPINES = 16
    LEAVES = 32
    OVERSUBSCRIPTION = 1 # OS=1
    
    generate_topology(
        num_spines=SPINES, 
        num_leaves=LEAVES, 
        os_ratio=OVERSUBSCRIPTION,
        rate="100Gbps", 
        delay="1000ns", 
        error_rate="0"
    )