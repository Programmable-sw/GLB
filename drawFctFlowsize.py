import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import ScalarFormatter
import os

# 1. 定义多方案的配置 (方案名 -> 文件路径及样式)
# 请将 'file' 对应的值替换为你本地真实的 ns-3 输出文件路径
# 如果某个文件不存在，脚本会自动跳过它，不会报错
schemes = {
    'Ours':     {'file': './mix/output/glblatest_fat4_netload50/602852476_out_fct.txt',   'color': "#3e79fa", 'linestyle': ':',  'marker': ''},
    'Ecmp':     {'file': './mix/output/ecmp_fat4_irn2_pfc0/510364948_out_fct.txt',                            'color': '#ff7f0e', 'linestyle': ':',  'marker': ''},
    'Conga':    {'file': './mix/output/conga_fat4_irn2_pfc0/945688000_out_fct.txt',                   'color': '#d62728', 'linestyle': '-.', 'marker': ''},
    'ConWeave': {'file': './mix/output/conweave_fat4_irn2_pfc0/339866944_out_fct.txt',                'color': '#8c564b', 'linestyle': '-',  'marker': ''}, # 假设你传的文件是 ConWeave
    'Drill':    {'file': './mix/output/drill_fat4_irn2_pfc0/512680801_out_fct.txt',                   'color': '#2ca02c', 'linestyle': '--', 'marker': ''},
    'Drill(*)':    {'file': './mix/output/drill_fat4_irn1_pfc1/760049578_out_fct.txt',                   'color': "#ce6c95", 'linestyle': '--', 'marker': ''},
}

# 2. X轴标签与刻度线物理位置 (死死钉在 0, 1, 2... 10)
labels = ['0', '1.8K', '3.5K', '4.6K', '5.5K', '6.3K', '7.2K', '8.6K', '16K', '31K', '2.0M']
x_ticks_pos = np.arange(len(labels))

# 3. 精准分段控制数据区间与 X 轴绘制位置
bins = [0]
x_data_pos = []

# --- 第一段: 0 ~ 1.8K ---
# 均分为两份，0~900 画在 x=0.5(隐形起点)，900~1800 画在 x=1.0(对齐 1.8K 刻度)
bins.extend([900, 1800])
x_data_pos.extend([0.5, 1.0])

# --- 第二段: 1.8K ~ 31K (仅对此范围做 2 倍插值) ---
major_middle = [1800, 3500, 4600, 5500, 6300, 7200, 8600, 16000, 31000]
for i in range(len(major_middle) - 1):
    start_b = major_middle[i]
    end_b = major_middle[i+1]
    base_x = i + 1  # 对应 1.8K 的 x 坐标基准是 1
    
    # 取中间值作为 2 倍插值的边界
    mid_b = start_b + (end_b - start_b) / 2.0
    bins.extend([mid_b, end_b])
    
    # 对应的绘制点也会落在两刻度正中间(x.5) 和 刻度线上(x.0)
    x_data_pos.extend([base_x + 0.5, base_x + 1.0])

# --- 第三段: 31K ~ 无穷大 (不插值，单独统计长尾流) ---
# 包含 31K 以上的所有极大流，绘制在 x=10 的位置(完美对齐 2.0M 刻度)
bins.append(float('inf'))
x_data_pos.append(10.0)


plt.rcParams['font.family'] = 'serif'
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
columns = ['src', 'dst', 'sport', 'dport', 'size_bytes', 'time', 'actual_fct', 'ideal_fct']

max_avg_y, max_p99_y = 1, 1

# 4. 读取与绘图
for name, config in schemes.items():
    file_path = config['file']
    if not os.path.exists(file_path):
        continue
        
    df = pd.read_csv(file_path, sep='\s+', names=columns)
    df['slowdown'] = (df['actual_fct'] / df['ideal_fct']).clip(lower=1.0)
    
    # 根据我们刚才精确配置的 bins 去切分源数据，算出最真实的区间统计值
    df['size_bin'] = pd.cut(df['size_bytes'], bins=bins, right=False)
    grouped = df.groupby('size_bin', observed=False)['slowdown']
    
    avg_slowdown, p99_slowdown = grouped.mean().values, grouped.quantile(0.99).values
    max_avg_y = max(max_avg_y, np.nanmax(avg_slowdown))
    max_p99_y = max(max_p99_y, np.nanmax(p99_slowdown))
    
    # marker 设置为空或很小，因为点变多了，只显示平滑折线更美观
    ax1.plot(x_data_pos, avg_slowdown, marker=config['marker'], markersize=2, 
             linestyle=config['linestyle'], linewidth=2.5, color=config['color'], label=name)
    ax2.plot(x_data_pos, p99_slowdown, marker=config['marker'], markersize=2, 
             linestyle=config['linestyle'], linewidth=2.5, color=config['color'], label=name)

# 5. 坐标轴格式化 (保持不变)
def format_axis(ax, title, max_y, y_ticks):
    ax.set_xlim(0, len(labels) - 1)
    ax.set_xticks(x_ticks_pos)
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=12)
    ax.set_xlabel('Flow Size (Bytes)', fontsize=14)
    ax.set_yscale('log')
    ax.set_ylim(1, max_y)
    formatter = ScalarFormatter()
    formatter.set_scientific(False)
    ax.yaxis.set_major_formatter(formatter)
    ax.set_yticks(y_ticks)
    ax.set_yticklabels([str(y) for y in y_ticks])
    ax.grid(True, linestyle='-', alpha=0.3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.15), ncol=3, frameon=False, fontsize=12)
    ax.set_title(title, y=-0.55, fontsize=14, fontweight='bold')

ax1.set_ylabel('Avg FCT Slowdown', fontsize=14)
avg_yticks = [1, 2, 3, 5, 10]
format_axis(ax1, '(a) 50% Avg.Load (avg)', max(10, int(np.ceil(max_avg_y)) * 1.5), avg_yticks)

ax2.set_ylabel('p99 FCT Slowdown', fontsize=14)
p99_yticks = [1, 5, 10, 20, 50, 100]
format_axis(ax2, '(b) 50% Avg.Load (99%-ile)', max(100, int(np.ceil(max_p99_y)) * 2), p99_yticks)

# 渲染与保存
plt.tight_layout()
plt.subplots_adjust(top=0.82, bottom=0.35) 
plt.savefig('fct_slowdown_compare_plot.png', dpi=300, bbox_inches='tight')
print(f"✅ 图表生成完毕")