#!/usr/bin/env python3

import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "sim/datacenter/htsim_roce"


def run(traffic, output, extra_args):
    command = [
        str(BINARY), "-o", str(output), "-tm", str(traffic),
        "-nodes", "256", "-conns", "0", "-tiers", "2", "-lb", "mrc",
        "-roce_rx_mode", "sp", "-queue_type", "composite_ecn_lb",
        "-host_queue_type", "prio", "-cc", "dcqcn_variant", "-end", "1",
        "-linkspeed", "400000", "-paths", "64", *extra_args,
    ]
    return subprocess.run(
        command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False)


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        traffic = temp / "empty.cm"
        traffic.write_text("Nodes 256\nConnections 0\n", encoding="utf-8")
        final = run(traffic, temp / "final.dat", [])
        if final.returncode != 0:
            raise AssertionError(final.stdout)
        expected = (
            "FinalCcMrcConfig dcqcn_variant_inflate=disabled "
            "mrc_ecn_trim_penalty=mode_uniform")
        if expected not in final.stdout:
            raise AssertionError(f"missing final semantics: {expected!r}")
        if "roce_trim_recovery=exact" not in final.stdout:
            raise AssertionError("default transport did not select exact Trim recovery")
        bounded = (
            "RoceTransportConfig semantics=mrc_exact_bounded "
            "awnd=cwnd_minus_inflight exact_trim_attempt_id=on ")
        if bounded not in final.stdout:
            raise AssertionError(f"missing bounded transport config: {bounded!r}")

        legacy = run(
            traffic, temp / "legacy.dat",
            ["-roce_transport_semantics", "legacy",
             "-roce_trim_recovery", "cumulative"])
        if legacy.returncode != 0:
            raise AssertionError(legacy.stdout)
        legacy_diag = (
            "RoceTransportConfig semantics=legacy awnd=legacy "
            "exact_trim_attempt_id=off recovery_reserve_bytes=0")
        if legacy_diag not in legacy.stdout:
            raise AssertionError(
                f"missing explicit legacy transport config: {legacy_diag!r}")
        legacy_cc = (
            "FinalCcMrcConfig dcqcn_variant_inflate=natural "
            "mrc_ecn_trim_penalty=mode_uniform")
        if legacy_cc not in legacy.stdout:
            raise AssertionError(
                f"missing legacy Natural inflate config: {legacy_cc!r}")

        inconsistent = run(
            traffic, temp / "inconsistent.dat",
            ["-roce_transport_semantics", "mrc_exact_bounded",
             "-roce_trim_recovery", "cumulative"])
        if inconsistent.returncode == 0:
            raise AssertionError(
                "bounded transport accepted cumulative Trim recovery")
        default_diag = (
            "MrcPolicyDiag policy=skip_token "
            "all_skip_resolution=natural_rotation")
        if default_diag not in final.stdout:
            raise AssertionError(
                f"missing default skip-token config: {default_diag!r}")

        scaled = run(
            traffic, temp / "scaled.dat",
            ["-mrc_cooldown_mode", "cwnd_scaled"])
        if scaled.returncode != 0:
            raise AssertionError(scaled.stdout)
        scaled_diag = (
            "MrcCooldownDiag mrc_cooldown_mode=cwnd_scaled "
            "mrc_cooldown_reference=topology_bdp "
            "mrc_cooldown_reference_pkts=86 "
            "mrc_cwnd_scaled_rotations=3 "
            "mrc_cwnd_scaled_skip_selections=96")
        if scaled_diag not in scaled.stdout:
            raise AssertionError(
                f"missing explicit cwnd-scaled config: {scaled_diag!r}")

        explicit = run(
            traffic, temp / "explicit.dat",
            ["-mrc_cooldown_mode", "cwnd_scaled",
             "-mrc_cooldown_reference_pkts", "100"])
        if explicit.returncode != 0:
            raise AssertionError(explicit.stdout)
        explicit_diag = (
            "MrcCooldownDiag mrc_cooldown_mode=cwnd_scaled "
            "mrc_cooldown_reference=explicit "
            "mrc_cooldown_reference_pkts=100 "
            "mrc_cwnd_scaled_rotations=4 "
            "mrc_cwnd_scaled_skip_selections=128")
        if explicit_diag not in explicit.stdout:
            raise AssertionError(
                f"missing explicit cooldown reference: {explicit_diag!r}")

        round_robin = run(
            traffic, temp / "round_robin.dat",
            ["-mrc_congestion_policy", "one_cycle",
             "-mrc_all_cooling_fallback", "round_robin"])
        if round_robin.returncode != 0:
            raise AssertionError(round_robin.stdout)
        if "MrcFallbackDiag mrc_all_cooling_fallback=round_robin" not in (
                round_robin.stdout):
            raise AssertionError("missing explicit round-robin fallback config")

        removed = [
            ["-mrc_trim_cool", "off"],
            ["-dcqcn_variant_sack_inflate", "clear"],
            ["-dcqcn_variant_inflate_mode", "legacy_clear_all"],
            ["-dcqcn_variant_nack_recovery_gate", "on"],
            ["-mrc_cooldown_mode", "fixed"],
            ["-mrc_cooldown_mode", "adaptive"],
            ["-mrc_cooldown_mode", "window_scaled"],
            ["-mrc_cooldown_cycles", "1"],
            ["-mrc_adaptive_max_cycles", "8"],
        ]
        for index, args in enumerate(removed):
            result = run(traffic, temp / f"removed_{index}.dat", args)
            if result.returncode == 0:
                raise AssertionError(f"removed ablation flag was accepted: {args}")


if __name__ == "__main__":
    main()
