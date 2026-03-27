运行本仓库：

wget https://www.nsnam.org/releases/ns-allinone-3.19.tar.bz2
tar -xvf ns-allinone-3.19.tar.bz2
cd ns-allinone-3.19
rm -rf ns-3.19
git clone https://github.com/Programmable-sw/GLB.git ns-3.19

cd ns-3.19
./waf configure --build-profile=optimized
./waf

直接运行glb的仿真，可使用默认参数：

python3 run.py --lb glb --irn 1 --pfc 1 --netload 50 --topo fat_k4_100G_OS2