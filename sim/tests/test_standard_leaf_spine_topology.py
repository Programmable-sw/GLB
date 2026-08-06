#!/usr/bin/env python3

import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "sim/datacenter/htsim_roce"


def verify(nodes, expected_leaves):
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        traffic = temp / "empty.cm"
        traffic.write_text(
            f"Nodes {nodes}\nConnections 0\n", encoding="utf-8")
        result = subprocess.run(
            [
                str(BINARY), "-o", str(temp / "out.dat"),
                "-tm", str(traffic), "-nodes", str(nodes), "-conns", "0",
                "-tiers", "2", "-lb", "rr", "-paths", "64",
                "-queue_type", "composite_ecn_lb",
                "-host_queue_type", "prio", "-cc", "dcqcn_variant",
                "-end", "1", "-linkspeed", "400000",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout)
        expected = (
            f"Standard 2-tier leaf-spine: nodes {nodes} leaves {expected_leaves} "
            "spines 64 downlinks_per_leaf 64 uplinks_per_leaf 64"
        )
        if expected not in result.stdout:
            raise AssertionError(
                f"missing fixed 64-spine topology diagnostic:\n{result.stdout}"
            )


def main():
    verify(128, 2)
    verify(512, 8)
    verify(2048, 32)


if __name__ == "__main__":
    main()
