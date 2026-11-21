import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from preflight import estimate

class EstimateTests(unittest.TestCase):
    def test_ideal_weight_math(self):
        result = estimate(70_000_000_000, 0.1, 0)
        self.assertAlmostEqual(result["weight_gib_estimate"], 0.815, places=3)
        self.assertFalse(result["runtime_verified"])

    def test_over_budget(self):
        self.assertFalse(estimate(70_000_000_000, 1.0, 2)["fits_assumed_vram_budget"])

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            estimate(70_000_000_000, -1, 0)

if __name__ == "__main__":
    unittest.main()
