import matplotlib.pyplot as plt
import numpy as np

def parse_slowdown(filename):
    """解析文件，提取小流和大流的平均归一化FCT"""
    small_avg, large_avg = 0.0, 0.0
    with open(filename, 'r') as f:
        for line in f:
            if line.startswith('<1BDP'):
                small_avg = float(line.strip().split(',')[1])
            elif line.startswith('>1BDP'):
                large_avg = float(line.strip().split(',')[1])
            elif line.startswith('ABSOLUTE'):
                break
    return small_avg, large_avg

# ================= 配置区 =================
# 填入不同方案的 summary txt 文件路径和方案名称
schemes = {
    "Ours": "./mix/output/glblatest_fat4_netload50/602852476_out_fct_summary.txt",
    "Ecmp": "./mix/output/ecmp_fat4_irn2_pfc0/510364948_out_fct_summary.txt",    
    "Conga": "./mix/output/conga_fat4_irn2_pfc0/945688000_out_fct_summary.txt",
    "Conweave": "./mix/output/conweave_fat4_irn2_pfc0/339866944_out_fct_summary.txt",
    # "Drill": "./mix/output/drill_fat4_irn2_pfc0/512680801_out_fct_summary.txt",
    "Drill": "./mix/output/drill_fat4_irn1_pfc1/760049578_out_fct_summary.txt",
}
# ==========================================

labels = list(schemes.keys())
small_fcts = []
large_fcts = []

for name, path in schemes.items():
    s_avg, l_avg = parse_slowdown(path)
    small_fcts.append(s_avg)
    large_fcts.append(l_avg)

x = np.arange(len(labels))
width = 0.35  # 柱子宽度

fig, ax = plt.subplots(figsize=(8, 6))
rects1 = ax.bar(x - width/2, small_fcts, width, label='Small Flows (< 1 BDP)', color='skyblue', edgecolor='black')
rects2 = ax.bar(x + width/2, large_fcts, width, label='Large Flows (> 1 BDP)', color='salmon', edgecolor='black')

# 添加标签、标题和图例
ax.set_ylabel('Normalized FCT (Avg Slowdown)', fontsize=12)
ax.set_title('Normalized FCT Comparison (fatk4-netload50)', fontsize=14)
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=12)
ax.legend(fontsize=11)
ax.grid(axis='y', linestyle='--', alpha=0.7)

# 在柱子上显示具体数值
def autolabel(rects):
    for rect in rects:
        height = rect.get_height()
        ax.annotate(f'{height:.2f}',
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 3),  # 3 points vertical offset
                    textcoords="offset points",
                    ha='center', va='bottom', fontsize=10)

autolabel(rects1)
autolabel(rects2)

fig.tight_layout()
plt.savefig('fct_bar_chart.png', dpi=300)
plt.show()