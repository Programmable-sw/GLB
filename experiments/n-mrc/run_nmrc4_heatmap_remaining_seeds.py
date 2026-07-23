#!/usr/bin/env python3
"""Resume the N-MRC4 heatmap matrix for seeds 29 and 47."""

import argparse
import concurrent.futures
import gzip
import re
import shlex
import subprocess
from pathlib import Path


DEFAULT_TEMPLATES = Path(
    "/home/user/workspace/csg-htsim/experiments/n-mrc/output/"
    "nmrc4_heatmap_seed13_20260722"
)
DEFAULT_OUTPUT = Path(
    "/home/user/workspace/csg-htsim/experiments/n-mrc/output/"
    "nmrc4_heatmap_three_seed_20260722"
)
FINISH = re.compile(r"^Flow Roce_", re.MULTILINE)


def option(command, name):
    index = command.index(name)
    return command[index + 1]


def replace_option(command, name, value):
    index = command.index(name)
    command[index + 1] = str(value)


def declared_flows(traffic):
    with traffic.open(encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("Connections "):
                return int(line.split()[1])
    raise ValueError("missing Connections line in %s" % traffic)


def completed_flows(log):
    data = log.read_bytes()
    if data.startswith(b"\x1f\x8b"):
        text = gzip.decompress(data).decode("utf-8", "replace")
    else:
        # Older runner versions let the child overwrite the buffered gzip
        # header, leaving simulator text before a trailing empty gzip member.
        text = data.split(b"\x1f\x8b", 1)[0].decode("utf-8", "replace")
    return sum(1 for line in text.splitlines() if line.startswith("Flow Roce_"))


def build_command(template, seed, case_dir):
    command = shlex.split(template.read_text(encoding="utf-8").strip())
    if option(command, "-lb") != "n-mrc4":
        raise ValueError("not an N-MRC4 command: %s" % template)
    if option(command, "-nmrc_absolute_threshold") != "0.5":
        raise ValueError("unexpected absolute threshold: %s" % template)
    if option(command, "-nmrc_relative_delta") != "0.25":
        raise ValueError("unexpected relative delta: %s" % template)
    traffic = Path(option(command, "-tm").replace("seed13.cm", "seed%d.cm" % seed))
    if not traffic.exists():
        raise FileNotFoundError(traffic)
    replace_option(command, "-tm", traffic)
    replace_option(command, "-seed", seed)
    replace_option(command, "-o", case_dir / "logout.dat")
    return command, traffic


def run_case(item):
    seed, scenario, template, output = item
    case_dir = output / ("seed%d" % seed) / scenario
    case_dir.mkdir(parents=True, exist_ok=True)
    command, traffic = build_command(template, seed, case_dir)
    expected = declared_flows(traffic)
    log = case_dir / "stdout.log.gz"
    marker = case_dir / "COMPLETE"
    if log.exists() and completed_flows(log) == expected:
        marker.write_text("completed_flows=%d\n" % expected, encoding="utf-8")
        return seed, scenario, "reused", expected
    marker.unlink(missing_ok=True)
    (case_dir / "command.txt").write_text(
        shlex.join(command) + "\n", encoding="utf-8"
    )
    plain_log = case_dir / "stdout.log"
    with plain_log.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            command, cwd=str(case_dir), stdout=handle,
            stderr=subprocess.STDOUT, check=False,
        )
    with plain_log.open("rb") as source, gzip.open(
            str(log), "wb", compresslevel=6) as target:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            target.write(block)
    plain_log.unlink()
    actual = completed_flows(log)
    if result.returncode != 0 or actual != expected:
        raise RuntimeError(
            "seed=%d scenario=%s rc=%d completed=%d expected=%d" %
            (seed, scenario, result.returncode, actual, expected)
        )
    marker.write_text("completed_flows=%d\n" % actual, encoding="utf-8")
    return seed, scenario, "ran", actual


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--templates", type=Path, default=DEFAULT_TEMPLATES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seeds", type=int, nargs="+", default=[29, 47])
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()

    templates = sorted(args.templates.glob("*/command.txt"))
    if len(templates) != 44:
        raise SystemExit("expected 44 templates, found %d" % len(templates))
    work = [
        (seed, template.parent.name, template, args.output)
        for seed in args.seeds for template in templates
    ]
    args.output.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_case, item) for item in work]
        for future in concurrent.futures.as_completed(futures):
            seed, scenario, state, count = future.result()
            print("seed=%d %-42s %-6s flows=%d" %
                  (seed, scenario, state, count), flush=True)


if __name__ == "__main__":
    main()
