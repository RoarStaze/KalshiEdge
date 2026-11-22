import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repeat_benchmark import summarize_runs


class BenchmarkSummaryTests(unittest.TestCase):
    def test_medians_and_ratio_are_computed_from_raw_timings(self):
        runs = [
            {'scalar_ms_per_call':2.0, 'warp_ms_per_call':1.0, 'max_abs_error':0.0001},
            {'scalar_ms_per_call':3.0, 'warp_ms_per_call':1.5, 'max_abs_error':0.0002},
            {'scalar_ms_per_call':4.0, 'warp_ms_per_call':2.0, 'max_abs_error':0.0003},
        ]
        out = summarize_runs(runs)
        self.assertEqual(out['scalar_median_ms'], 3.0)
        self.assertEqual(out['warp_median_ms'], 1.5)
        self.assertEqual(out['speedup'], 2.0)
        self.assertEqual(out['max_abs_error'], 0.0003)
        self.assertTrue(out['timing_variability_warning'])  # both span 2x

    def test_stable_runs_have_no_warning(self):
        runs = [
            {'scalar_ms_per_call':2.0, 'warp_ms_per_call':0.9, 'max_abs_error':0.},
            {'scalar_ms_per_call':2.05, 'warp_ms_per_call':1.0, 'max_abs_error':0.},
            {'scalar_ms_per_call':1.95, 'warp_ms_per_call':1.1, 'max_abs_error':0.},
        ]
        out = summarize_runs(runs)
        self.assertFalse(out['timing_variability_warning'])

    def test_bad_timings_rejected(self):
        for bad in ([], [{'scalar_ms_per_call':0., 'warp_ms_per_call':2., 'max_abs_error':0.}],
                    [{'scalar_ms_per_call':float('nan'), 'warp_ms_per_call':2., 'max_abs_error':0.}]):
            with self.assertRaises(ValueError):
                summarize_runs(bad)


if __name__ == '__main__':
    unittest.main()
