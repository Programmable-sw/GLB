import matplotlib.pyplot as plt
import numpy as np

def plot_flow_size_cdf(flow_file):
    sizes = []
    with open(flow_file, 'r') as f:
        # 第一行通常是总流数，先读掉
        first_line = f.readline()
        
        for line in f:
            parts = line.strip().split()
            # 确保是有效的数据行 (src dst 3 size time)
            if len(parts) >= 4:
                sizes.append(float(parts[3]))
                
    # 计算 CDF
    sizes_sorted = np.sort(sizes)
    cdf = np.arange(1, len(sizes_sorted) + 1) / len(sizes_sorted)
    
    # 绘图
    plt.figure(figsize=(8, 6))
    plt.plot(sizes_sorted, cdf, linewidth=2.5, color='darkblue')
    
    # X 轴通常使用对数坐标，因为流大小跨度很大（从几十字节到几百兆）
    plt.xscale('log')
    plt.grid(True, which="both", ls="--", alpha=0.5)
    
    plt.xlabel('Flow Size (Bytes)', fontsize=12)
    plt.ylabel('CDF', fontsize=12)
    plt.title('CDF of Generated Flow Sizes', fontsize=14)
    
    plt.tight_layout()
    plt.savefig('flow_size_cdf.png', dpi=300)
    plt.show()

# 填入你的流生成文件
flow_txt_path = "./config/L_25.00_CDF_AliStorage2019_N_32_T_100ms_B_100_flow.txt"
plot_flow_size_cdf(flow_txt_path)