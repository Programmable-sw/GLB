import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("plot_netaware_a2a_topk_vs_weighted_micro.py")
SPEC = importlib.util.spec_from_file_location("a2a_plot", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ChainedFlowPerformanceTest(unittest.TestCase):
    def test_uses_each_actual_start_time(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "stdout.log"
            log.write_text(
                "startflow Roce_1_2 at 0\n"
                "startflow Roce_2_3 at 7.5\n"
                "Flow Roce_1_2 1078 finished at 10 total bytes 2097152\n"
                "Flow Roce_2_3 1082 finished at 20 total bytes 2097152\n"
                ".Done\n"
            )
            result = MODULE.parse_chained_flow_performance(log, expected_flows=2)
            self.assertEqual(result["completed_flows"], 2)
            self.assertEqual(result["mean_fct_us"], 11.25)
            self.assertEqual(result["p50_fct_us"], 11.25)
            self.assertEqual(result["max_fct_us"], 12.5)


if __name__ == "__main__":
    unittest.main()
