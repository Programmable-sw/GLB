#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main():
    source = (ROOT / "sim/datacenter/main_roce.cpp").read_text(
        encoding="utf-8")
    required = (
        "nmrc_quantized_topk",
        "-sglb_nmrc_levels 4|8",
        'else if (!strcmp(argv[i],"-sglb_nmrc_levels"))',
        "sglb n-mrc levels ",
        "nmrc_levels ",
        "SGLB_SCORE_NMRC_QUANTIZED_TOPK",
    )
    for marker in required:
        assert marker in source, marker


if __name__ == "__main__":
    main()
