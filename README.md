# N-MRC on htsim

This repository implements and evaluates N-MRC in Broadcom `csg-htsim`, using the RoCE datacenter simulator on generated fat-tree topologies.

N-MRC is a source-controlled packet-level load balancing scheme. The source host chooses an EV/pathid for each packet; switches interpret that EV at each ECMP stage; the destination ToR aggregates path observations and returns compact path-state feedback to the source. The source then uses a shared `(src ToR, dst ToR)` path-health bitmap to steer later packets, without keeping a large per-flow/per-QP path table.

The standard experiments in this branch use two scenarios:

- Scenario 1, healthy network: 2048 nodes / 2-tier leaf-spine.
- Scenario 2, asymmetric bandwidth: 1024 nodes / 3-tier fat-tree, with 3% ToR uplinks set to half bandwidth.

Both scenarios mainly compare `ECMP`, `OPS`, `REPS`, and `N-MRC`.

## Repository Layout

- `sim/`: htsim C++ discrete-event simulator.
- `sim/datacenter/htsim_roce`: compiled RoCE simulator executable.
- `experiments/n-mrc/run_literature_metric_compare.py`: standard N-MRC comparison script.
- `experiments/n-mrc/run_glb_factor_compare.py`: auxiliary GLB parameter sweep script.
- `experiments/n-mrc/README.md`: detailed experiment notes.
- `experiments/n-mrc/output/`: local experiment outputs, ignored by git.

## N-MRC Mechanism

N-MRC treats an EV as an end-to-end path combination. In a 2-tier topology, the EV identifies the ToR uplink choice. In a 3-tier topology, the EV is segmented across ToR uplink, aggregation uplink, core-down bundle, and aggregation-down bundle choices.

At the source, N-MRC keeps one shared path-state bitmap per `(src ToR, dst ToR)` pair. Each EV uses a 2-bit state:

| State | Meaning |
| --- | --- |
| `11` | strong-good path |
| `10` | usable path |
| `01` | suspect path |
| `00` | reserved for loss/unavailable semantics |

The source prefers `11` paths. If there are not enough strong-good paths, it gradually widens selection to `10` and then `01`. The destination ToR records whether packets on each EV arrive cleanly or with ECN CE marks, emits bitmap feedback, and the source updates the shared path-state bitmap from ACK feedback.

## Load Balancing Schemes

| Scheme | Mechanism |
| --- | --- |
| ECMP | Baseline hashing over ECMP next hops. |
| OPS | Source randomly selects an EV per packet. |
| REPS | Receiver feedback lets the source reuse recently clean EVs. |
| N-MRC | Source selects packets using ToR-pair shared 2-bit path health. |
| ConWeave-like | RTT-threshold based reroute logic implemented in this simulator. |
| Adaptive Routing | Switch-local congestion-aware next-hop selection. |
| DRILL | Switch-local random candidates plus remembered candidate selection. |
| GLB | Switch-local scoring using local and remote queue/utilization signals. |

## Standard Scenarios

| Scenario | Topology | Link condition | Flow size | Compared schemes |
| --- | --- | --- | --- | --- |
| Healthy network | 2048 nodes / 2-tier | all links 400Gbps | 4/8/16/32MiB | ECMP / OPS / REPS / N-MRC |
| Asymmetric bandwidth | 1024 nodes / 3-tier | 3% ToR uplinks at 200Gbps | 4/8/16/32MiB | ECMP / OPS / REPS / N-MRC |

Common settings:

- Link speed: 400Gbps.
- Switch queue model: `lossless_input_ecn`.
- Switch queue length: `-q 100` packets.
- ECN: Kmin/Kmax = 20/80 packets.
- PFC: low/high threshold = 20/80 packets.
- Host queue: `prio`.
- MTU: 4096 bytes.
- Congestion control: `DCQCN_variant`.
- Traffic: `tornado`, one foreground flow per host, destination `(src + nodes/2) % nodes`.
- Metrics: avg FCT, p99 FCT, p99.9 FCT, max FCT, CCT.

`lossless_input_ecn` is the switch-side lossless input queue with ECN marking and PFC pause behavior. It models a lossless RoCE-style fabric: congestion is signaled by ECN/PFC instead of intentional packet drops in the standard scenarios.

`prio` is the host-side priority queue. It separates control/high-priority packets such as ACK/NACK/CNP-like traffic from ordinary data packets, so feedback is not queued behind data in the same way.

The simulator also contains queue types that can drop packets and RoCE NACK/retransmission code paths, but the standard N-MRC experiments in this repository use the lossless ECN/PFC setting above. Lossy-network experiments are not part of the current default comparison.

In the asymmetric bandwidth scenario, slow links are selected from all ToR-to-aggregation uplinks. A generated 1024-node / 3-tier fat-tree has 1024 such uplinks, so the default slow-link count is `ceil(1024 * 0.03) = 31`.

## Setup

Clone this branch:

```bash
git clone -b main-htsim https://github.com/Programmable-sw/GLB.git csg-htsim
cd csg-htsim
git remote add upstream https://github.com/Broadcom/csg-htsim.git || true
git fetch --all --prune
```

Install dependencies on Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y build-essential make g++ git python3 python3-pip
python3 -m pip install --user matplotlib
```

Build the simulator:

```bash
make -C sim -j"$(nproc)"
```

Check the RoCE executable and Python scripts:

```bash
./sim/datacenter/htsim_roce -h | head -n 40
python3 -m py_compile \
  experiments/n-mrc/run_literature_metric_compare.py \
  experiments/n-mrc/run_glb_factor_compare.py
```

## Run Experiments

Run the standard comparison:

```bash
python3 experiments/n-mrc/run_literature_metric_compare.py
```

This runs 8 scenarios and 4 schemes per scenario:

- `healthy_2048n_2tier_{4,8,16,32}m`
- `asym_tor3pct_1024n_3tier_{4,8,16,32}m`

Default output directory:

```text
experiments/n-mrc/output/topo-healthy2048n2t-asym1024n3t_traffic-tornado_flow-4-32m_scene-healthy-asym3pct_schemes-ecmp-ops-reps-n-mrc/
```

Run a quick 8MiB check:

```bash
SCENARIO_FLOW_SIZE_MIBS=8 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

Keep raw per-run files:

```bash
KEEP_RAW_OUTPUT=1 \
python3 experiments/n-mrc/run_literature_metric_compare.py
```

Useful environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SCENARIO_HEALTHY_TOPOLOGIES` | `2048:2` | Healthy-network topology list, in `nodes:tiers` format. |
| `SCENARIO_ASYM_TOPOLOGIES` | `1024:3` | Asymmetric-bandwidth topology list. |
| `SCENARIO_FLOW_SIZE_MIBS` | `4,8,16,32` | Flow size list. |
| `SCENARIO_TRAFFIC` | `tornado` | `tornado` or `permutation`. |
| `SCENARIO_CC` | `dcqcn_variant` | Can be changed to `dcqcn`. |
| `SCENARIO_OUT` | auto-generated | Output directory. |
| `KEEP_RAW_OUTPUT` | unset | Set to `1` to keep `.cmd`, `.stdout`, `.dat`, and `.cm` files. |

## Outputs

- `summary.csv`: metrics per scenario and scheme.
- `normalized.csv`: scenario-local normalized metrics.
- `per_flow.csv`: per-flow FCT details.
- `comparison_report.md`: generated Markdown report.
- `scenario_plan.md`: scenario list generated by the script.

## Auxiliary GLB Sweep

```bash
python3 experiments/n-mrc/run_glb_factor_compare.py
```

Example:

```bash
GLB_FACTOR_NODES=1024 GLB_FACTOR_TIERS=3 GLB_FACTOR_FLOW_SIZE=$((32 * 1024 * 1024)) \
python3 experiments/n-mrc/run_glb_factor_compare.py
```

## Core Code

- `experiments/n-mrc/run_literature_metric_compare.py`: scenario generation, command construction, metric parsing, and report generation.
- `experiments/n-mrc/run_glb_factor_compare.py`: GLB factor sweep.
- `sim/datacenter/main_roce.cpp`: RoCE CLI, LB mode selection, and N-MRC default parameters.
- `sim/roce.cpp` / `sim/roce.h`: source-side ECMP/OPS/REPS/N-MRC selection and feedback updates.
- `sim/rocepacket.h`: pathid and N-MRC feedback fields on RoCE packets/ACKs.
- `sim/datacenter/fat_tree_switch.cpp` / `sim/datacenter/fat_tree_switch.h`: switch forwarding, EV segmentation, and N-MRC feedback generation.
- `sim/datacenter/fat_tree_topology.cpp` / `sim/datacenter/fat_tree_topology.h`: generated fat-tree topology and slow-link injection.

## Cleanup

```bash
rm -rf experiments/n-mrc/output experiments/n-mrc/output_*
make -C sim clean
```

## htsim Background

htsim is a high performance discrete event simulator, inspired by ns2, but much faster, primarily intended to examine congestion control algorithm behaviour. This fork keeps the htsim/RoCE simulator structure and adds N-MRC-oriented experiments on top.
