#!/usr/bin/python3
import os
import subprocess
import random

# 笛卡尔积参数空间
ratios = [0.5]     # 空间容忍度 
intervals = [15]      # 时间容忍度 
modes = [(1, 1)]            # (IRN, PFC)

# 固定参数
lb_mode = "glb"
netload = "50"
topo = "fat_k4_100G_OS2"

for r in ratios:
    for t in intervals:
        for irn, pfc in modes:
            numeric_id = str(random.randrange(100000000, 999999999))
            
            # 描述性的后缀名称
            desc_name = f"OOO_{r}BDP_TMR_{t}us_IRN{irn}_PFC{pfc}"
            
            print(f"\n=====================================================================")
            print(f"🚀 Starting Experiment: {desc_name} (Numeric ID: {numeric_id})")
            print(f"=====================================================================")
            
            cmd = [
                "python3", "run.py",
                "--irn", str(irn),
                "--pfc", str(pfc),
                "--lb", lb_mode,
                "--netload", netload,
                "--topo", topo,
                "--ooo_ratio", str(r),
                "--ooo_interval", str(t),
                "--id", numeric_id  # ID 传给 run.py
            ]
            
            # 执行单次实验（阻塞等待直到 fctAnalysis.py 也跑完）
            subprocess.run(cmd)

            old_folder = f"mix/output/{numeric_id}"
            new_folder = f"mix/output/{numeric_id}_{desc_name}"
            
            if os.path.exists(old_folder):
                os.rename(old_folder, new_folder)
                print(f"✅ Analysis complete! Folder safely renamed to: {new_folder}")
            else:
                print(f"❌ Error: Folder {old_folder} not found!")

print("\n🎉 All Cartesian Product Experiments Finished Successfully!")