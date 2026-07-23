import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("plot_top2_weighted_a2a_p4_imbalance.py")
SPEC = importlib.util.spec_from_file_location("top2_plot", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CompletionMetricsTest(unittest.TestCase):
    def test_cct_is_last_absolute_finish_not_maximum_individual_fct(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "stdout.log"
            log.write_text(
                "startflow Roce_1_2 at 0\n"
                "startflow Roce_2_3 at 7.5\n"
                "Flow Roce_1_2 1078 finished at 10 total bytes 2097152\n"
                "Flow Roce_2_3 1082 finished at 20 total bytes 2097152\n"
            )
            result = MODULE.completion_metrics(log, expected=2)
            self.assertEqual(result["max_fct_us"], 12.5)
            self.assertEqual(result["cct_us"], 20.0)


if __name__ == "__main__":
    unittest.main()
