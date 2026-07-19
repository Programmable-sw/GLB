#!/usr/bin/env python3

import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "sim/datacenter/htsim_roce"


def run(traffic, output, scheme, extra_args):
    command = [
        str(BINARY), "-o", str(output), "-tm", str(traffic),
        "-nodes", "2", "-conns", "0", "-tiers", "2",
        "-lb", scheme, "-queue_type", "composite_ecn_lb",
        "-host_queue_type", "prio", "-roce_rx_mode", "sp",
        "-cc", "dcqcn_variant", "-end", "1",
        "-linkspeed", "400000", "-paths", "1", *extra_args,
    ]
    return subprocess.run(
        command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
    )


def require(result, text):
    if result.returncode != 0:
        raise AssertionError(result.stdout)
    if text not in result.stdout:
        raise AssertionError(f"missing {text!r} in:\n{result.stdout}")


def reject(result, context):
    if result.returncode == 0:
        raise AssertionError(f"{context} was unexpectedly accepted")


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        traffic = temp / "empty.cm"
        traffic.write_text("Nodes 2\nConnections 0\n", encoding="utf-8")

        netaware = run(traffic, temp / "netaware.dat", "netaware", [])
        require(
            netaware,
            "netaware canonical: paths 1, feedback_pkts 1, "
            "min_interval_us 5, max_interval_us 5",
        )
        reject(
            run(
                traffic, temp / "netaware_legacy_cadence.dat", "netaware",
                ["-netaware_feedback_pkts", "32",
                 "-netaware_feedback_min_us", "5",
                 "-netaware_feedback_max_us", "20"],
            ),
            "legacy NetAware feedback cadence",
        )

        hybrid_nmrc = run(traffic, temp / "hybrid_nmrc.dat", "n-mrc", [])
        require(
            hybrid_nmrc,
            "HybridNmrcConfig ev_mode=encoded "
            "reroute_policy=better_ge3 fastcnp=on",
        )

        for scheme in ("avail", "grade"):
            compatible = run(
                traffic, temp / f"{scheme}_compatible.dat", scheme,
                ["-stor_feedback_pkts", "32",
                 "-stor_feedback_min_us", "5",
                 "-stor_feedback_max_us", "20"],
            )
            require(
                compatible,
                f"{scheme} canonical: paths 1, feedback_pkts 32, "
                "min_interval_us 5, max_interval_us 20",
            )

        sglb_legacy = run(
            traffic, temp / "sglb_legacy.dat", "sglb",
            ["-sglb_score_mode", "legacy"],
        )
        require(sglb_legacy, "SGLB effective config: score mode legacy")

        natural_cumulative = run(
            traffic, temp / "natural_cumulative.dat", "mrc",
            ["-roce_transport_semantics", "legacy",
             "-roce_trim_recovery", "cumulative"],
        )
        require(
            natural_cumulative,
            "FinalCcMrcConfig dcqcn_variant_inflate=natural "
            "mrc_ecn_trim_penalty=mode_uniform "
            "roce_trim_recovery=cumulative",
        )


if __name__ == "__main__":
    main()
