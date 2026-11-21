import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from discover import inspect_model

class DiscoveryTests(unittest.TestCase):
    def test_name_does_not_prove_runnable(self):
        candidate = inspect_model({"id": "someone/Llama-70B-LittleBit", "siblings": [
            {"rfilename": "config.json"}, {"rfilename": "model.safetensors"}]})
        self.assertTrue(candidate["possible_70b_by_name"])
        self.assertTrue(candidate["has_weights"])
        self.assertFalse(candidate["verified_70b"])
        self.assertFalse(candidate["verified_runnable"])

    def test_missing_weights(self):
        candidate = inspect_model({"id": "someone/littlebit", "siblings": []})
        self.assertFalse(candidate["has_weights"])

if __name__ == "__main__":
    unittest.main()
