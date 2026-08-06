#!/usr/bin/env python3

import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "sim/datacenter/htsim_roce"


def run(traffic: Path, topology: Path, output: Path, extra_args,
        nodes="256"):
    topology_args = ["-topo", str(topology)] if topology else []
    command = [
        str(BINARY), "-o", str(output), "-tm", str(traffic),
        "-nodes", nodes, "-conns", "0", "-tiers", "2", "-lb", "mrc",
        *topology_args,
        "-roce_rx_mode", "sp", "-queue_type", "composite_ecn_lb",
        "-host_queue_type", "prio", "-cc", "dcqcn_variant", "-end", "1",
        "-linkspeed", "400000", "-paths", "64", *extra_args,
    ]
    return subprocess.run(
        command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False)


def expect_success(result, expected):
    if result.returncode != 0:
        raise AssertionError(result.stdout)
    if expected not in result.stdout:
        raise AssertionError(
            f"missing diagnostic {expected!r}\noutput:\n{result.stdout}")


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        traffic = temp / "empty.cm"
        traffic.write_text("Nodes 256\nConnections 0\n", encoding="utf-8")
        topology = temp / "leaf4-spine64.top"
        topology.write_text(
            "nodes 256\n"
            "tiers 2\n"
            "podsize 256\n"
            "tier 0\n"
            "downlink_speed_gbps 400\n"
            "radix_up 64\n"
            "radix_down 64\n"
            "downlink_latency_ns 500\n"
            "tier 1\n"
            "downlink_speed_gbps 400\n"
            "radix_down 4\n"
            "downlink_latency_ns 500\n",
            encoding="utf-8",
        )

        default = run(traffic, topology, temp / "default.dat", [])
        expect_success(
            default,
            "MrcPolicyDiag policy=skip_token "
            "all_skip_resolution=natural_rotation")
        expect_success(
            default,
            "MrcFailureRecoveryDiag enabled=0 probe_success_threshold=3 "
            "assumed_bad=0 probe_packets=0")

        noncanonical_topology = temp / "leaf4-spine32.top"
        noncanonical_topology.write_text(
            "nodes 256\n"
            "tiers 2\n"
            "podsize 256\n"
            "tier 0\n"
            "downlink_speed_gbps 400\n"
            "radix_up 32\n"
            "radix_down 64\n"
            "oversubscribed 2\n"
            "downlink_latency_ns 500\n"
            "tier 1\n"
            "downlink_speed_gbps 400\n"
            "radix_down 4\n"
            "downlink_latency_ns 500\n",
            encoding="utf-8",
        )
        noncanonical = run(
            traffic, noncanonical_topology, temp / "noncanonical.dat", [])
        if noncanonical.returncode == 0:
            raise AssertionError("skip-token accepted a non-64-path topology")
        if "requires exactly 64 physical paths" not in noncanonical.stdout:
            raise AssertionError(noncanonical.stdout)

        for policy in ("skip_token", "skip_rotation", "one_cycle",
                       "cwnd_scaled"):
            result = run(
                traffic, topology, temp / f"{policy}.dat",
                ["-mrc_congestion_policy", policy,
                 "-mrc_active_evs", "64"])
            expect_success(result, f"MrcPolicyDiag policy={policy} ")
            expect_success(
                result, "MrcEvModelDiag mrc_ev_model=encoded "
                "mrc_active_evs=64 mrc_backup_evs=0 ")

        invalid_profile = run(
            traffic, topology, temp / "invalid_profile.dat",
            ["-mrc_congestion_policy", "skip_token",
             "-mrc_active_evs", "63"])
        if invalid_profile.returncode == 0:
            raise AssertionError("canonical policy accepted a partial EV profile")

        invalid = run(
            traffic, topology, temp / "invalid.dat",
            ["-mrc_congestion_policy", "invalid"])
        if invalid.returncode == 0:
            raise AssertionError("invalid MRC policy was accepted")

        mixed = run(
            traffic, topology, temp / "mixed.dat",
            ["-mrc_congestion_policy", "skip_token",
             "-mrc_cooldown_mode", "one_cycle"])
        if mixed.returncode == 0:
            raise AssertionError("mixed new and legacy policy flags were accepted")

        legacy = run(
            traffic, topology, temp / "legacy.dat",
            ["-mrc_cooldown_mode", "cwnd_scaled"])
        expect_success(legacy, "MrcPolicyDiag policy=cwnd_scaled ")
        expect_success(
            legacy, "MrcEvModelDiag mrc_ev_model=encoded "
            "mrc_active_evs=32 mrc_backup_evs=32 ")

        enabled = run(
            traffic, topology, temp / "failure_enabled.dat",
            ["-mrc_failure_recovery", "on",
             "-mrc_probe_success_threshold", "5"])
        expect_success(
            enabled,
            "MrcFailureRecoveryDiag enabled=1 probe_success_threshold=5 ")


if __name__ == "__main__":
    main()
