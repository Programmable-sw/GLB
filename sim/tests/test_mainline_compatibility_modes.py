#!/usr/bin/env python3

import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "sim/datacenter/htsim_roce"


def run(traffic, output, scheme, extra_args, before_lb_args=()):
    command = [
        str(BINARY), "-o", str(output), "-tm", str(traffic),
        "-nodes", "2", "-conns", "0", "-tiers", "2",
        *before_lb_args,
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


def reject(result, text, context):
    if result.returncode == 0:
        raise AssertionError(f"{context} was unexpectedly accepted")
    if text not in result.stdout:
        raise AssertionError(
            f"{context} did not report {text!r}:\n{result.stdout}"
        )


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
            "Usage ",
            "legacy NetAware feedback cadence",
        )

        hybrid_nmrc = run(traffic, temp / "hybrid_nmrc.dat", "n-mrc", [])
        require(
            hybrid_nmrc,
            "HybridNmrcConfig ev_mode=encoded "
            "reroute_policy=better_ge3 fastcnp=on",
        )
        require(
            hybrid_nmrc,
            "preset=n-mrc endpoint_policy=rr_cooldown "
            "all_cooling_policy=earliest network_decision=graded "
            "binary_threshold=0.5",
        )

        presets = {
            "n-mrc": (
                "rr_cooldown", "earliest", "graded", "on"
            ),
            "n-mrc-allcool-rr-reset": (
                "rr_cooldown", "rr_reset", "graded", "on"
            ),
            "n-mrc1": (
                "random_stateless", "earliest", "graded", "off"
            ),
            "n-mrc2": (
                "rr_cooldown", "earliest", "binary_score", "on"
            ),
        }
        for preset, (endpoint, all_cooling, network, fastcnp) in presets.items():
            default_resolved = run(
                traffic, temp / f"{preset}_default.dat", preset, [],
            )
            require(
                default_resolved,
                "HybridNmrcConfig ev_mode=encoded "
                f"reroute_policy=better_ge3 fastcnp={fastcnp}",
            )
            require(
                default_resolved,
                f"preset={preset} endpoint_policy={endpoint} "
                f"all_cooling_policy={all_cooling} "
                f"network_decision={network} binary_threshold=0.5",
            )

            matching_options = [
                "-nmrc_ev_mode", "encoded",
                "-nmrc_reroute_policy", "better_ge3",
                "-nmrc_fastcnp", fastcnp,
                "-nmrc_endpoint_policy", endpoint,
                "-nmrc_all_cooling_policy", all_cooling,
                "-nmrc_network_decision", network,
                "-nmrc_binary_threshold", "0.5",
            ]
            resolved = run(
                traffic, temp / f"{preset}.dat", preset, matching_options,
            )
            require(
                resolved,
                f"preset={preset} endpoint_policy={endpoint} "
                f"all_cooling_policy={all_cooling} "
                f"network_decision={network} binary_threshold=0.5",
            )

            before_lb = run(
                traffic, temp / f"{preset}_before_lb.dat", preset, [],
                matching_options,
            )
            require(
                before_lb,
                f"preset={preset} endpoint_policy={endpoint} "
                f"all_cooling_policy={all_cooling} "
                f"network_decision={network} binary_threshold=0.5",
            )

        nmrc4 = run(traffic, temp / "nmrc4_default.dat", "n-mrc4", [])
        require(
            nmrc4,
            "HybridNmrcConfig ev_mode=encoded preset=n-mrc4 "
            "endpoint_policy=rr_cooldown "
            "all_cooling_policy=earliest network_decision=relative_delta "
            "relative_delta=0.3 fastcnp=on reroute_policy=n/a",
        )
        for value in ("0.20", "0.30", "0.40"):
            for before_lb_args, extra_args in (
                (("-nmrc_relative_delta", value), ()),
                ((), ("-nmrc_relative_delta", value)),
            ):
                resolved = run(
                    traffic, temp / f"nmrc4_delta_{value}.dat", "n-mrc4",
                    extra_args, before_lb_args,
                )
                require(
                    resolved,
                    "preset=n-mrc4 endpoint_policy=rr_cooldown "
                    "all_cooling_policy=earliest "
                    "network_decision=relative_delta "
                    f"relative_delta={float(value):g}",
                )

        reject(
            run(traffic, temp / "nmrc4_missing_delta.dat", "n-mrc4",
                ["-nmrc_relative_delta"]),
            "missing value for -nmrc_relative_delta",
            "missing n-MRC4 relative delta",
        )
        reject(
            run(traffic, temp / "nmrc4_duplicate_delta.dat", "n-mrc4",
                ["-nmrc_relative_delta", "0.3",
                 "-nmrc_relative_delta", "0.4"]),
            "-nmrc_relative_delta may only be specified once",
            "duplicate n-MRC4 relative delta",
        )
        for value in ("nan", "inf", "0", "-0.1", "1.1", "0.3junk"):
            reject(
                run(traffic, temp / f"nmrc4_bad_delta_{value}.dat", "n-mrc4",
                    ["-nmrc_relative_delta", value]),
                f"invalid n-MRC relative delta {value}",
                f"invalid n-MRC4 relative delta {value}",
            )
        for preset in ("n-mrc", "n-mrc2", "ecmp"):
            reject(
                run(traffic, temp / f"{preset}_delta_scope.dat", preset,
                    ["-nmrc_relative_delta", "0.3"]),
                "-nmrc_relative_delta requires -lb n-mrc4",
                f"relative delta scope for {preset}",
            )
        for option, value, required in (
            ("-nmrc_fastcnp", "off", "on"),
            ("-nmrc_ev_mode", "random32", "encoded"),
            ("-nmrc_endpoint_policy", "random_stateless", "rr_cooldown"),
            ("-nmrc_all_cooling_policy", "rr_reset", "earliest"),
            ("-nmrc_network_decision", "graded", "relative_delta"),
            ("-nmrc_network_decision", "binary_score", "relative_delta"),
        ):
            reject(
                run(traffic, temp / f"nmrc4_conflict_{option[6:]}.dat",
                    "n-mrc4", [option, value]),
                f"n-MRC preset n-mrc4 conflicts with {option} {value}; "
                f"requires {required}",
                f"n-MRC4 {option} conflict",
            )
        for option, value in (
            ("-nmrc_reroute_policy", "better_ge3"),
            ("-nmrc_binary_threshold", "0.5"),
        ):
            reject(
                run(traffic, temp / f"nmrc4_unsupported_{option[6:]}.dat",
                    "n-mrc4", [option, value]),
                f"n-MRC preset n-mrc4 does not allow {option}",
                f"n-MRC4 unsupported {option}",
            )

        reject(
            run(
                traffic, temp / "allcool_conflict.dat",
                "n-mrc-allcool-rr-reset",
                ["-nmrc_endpoint_policy", "random_stateless"],
            ),
            "n-MRC preset n-mrc-allcool-rr-reset conflicts with "
            "-nmrc_endpoint_policy random_stateless; requires rr_cooldown",
            "all-cooling preset conflict",
        )
        reject(
            run(
                traffic, temp / "nmrc1_conflict.dat", "n-mrc1",
                ["-nmrc_fastcnp", "on"],
            ),
            "n-MRC preset n-mrc1 conflicts with -nmrc_fastcnp on; "
            "requires off",
            "n-mrc1 preset conflict",
        )
        reject(
            run(
                traffic, temp / "nmrc2_conflict.dat", "n-mrc2",
                ["-nmrc_network_decision", "graded"],
            ),
            "n-MRC preset n-mrc2 conflicts with -nmrc_network_decision "
            "graded; requires binary_score",
            "n-mrc2 preset conflict",
        )
        reject(
            run(
                traffic, temp / "non_nmrc_override.dat", "ecmp",
                ["-nmrc_endpoint_policy", "rr_cooldown"],
            ),
            "n-MRC options require an N-MRC load-balancing preset",
            "N-MRC option scope",
        )
        for option in (
            "-nmrc_ev_mode",
            "-nmrc_reroute_policy",
            "-nmrc_fastcnp",
            "-nmrc_endpoint_policy",
            "-nmrc_all_cooling_policy",
            "-nmrc_network_decision",
            "-nmrc_binary_threshold",
        ):
            reject(
                run(
                    traffic, temp / f"missing_{option[1:]}.dat", "n-mrc",
                    [option],
                ),
                f"missing value for {option}",
                f"missing {option} value",
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
