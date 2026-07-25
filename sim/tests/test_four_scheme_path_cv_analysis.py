#!/usr/bin/env python3

import csv
import importlib.util
import math
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/n-mrc/analyze_four_scheme_path_cv.py"


def load_module():
    spec = importlib.util.spec_from_file_location("path_cv_analysis", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    module = load_module()
    if module.population_cv([10, 10, 10, 10]) != 0:
        raise AssertionError("equal counts must have zero CV")
    expected = math.sqrt(75.0) / 5.0
    observed = module.population_cv([20, 0, 0, 0])
    if not math.isclose(observed, expected, rel_tol=1e-12):
        raise AssertionError((observed, expected))

    with tempfile.TemporaryDirectory() as temp_dir:
        source = Path(temp_dir) / "avail.csv"
        with source.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "time_us", "selected_total", "path_0", "path_1",
                "path_2", "path_3", "cumulative_cv",
            ])
            writer.writerow([1, 40, 10, 10, 10, 10, 0])
            writer.writerow([2, 80, 30, 20, 20, 10, 0])
            writer.writerow([3, 120, 40, 30, 30, 20, 0])
        rows = module.transform_trace("avail", source)

    if len(rows) != 3:
        raise AssertionError(rows)
    if rows[0]["window_cv"] != 0:
        raise AssertionError(rows[0])
    expected_window = module.population_cv([20, 10, 10, 0])
    if not math.isclose(rows[1]["window_cv"], expected_window, rel_tol=1e-12):
        raise AssertionError(rows[1])
    if not math.isclose(
        rows[2]["window_cv"],
        module.population_cv([10, 10, 10, 10]),
        rel_tol=1e-12,
    ):
        raise AssertionError(rows[2])
    print("Four-scheme path CV analysis test passed")


if __name__ == "__main__":
    main()
