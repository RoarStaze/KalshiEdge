"""TDD contract for a truthful LittleBit-only large-checkpoint scout."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from large_check import classify_model, parameter_billions, scan_models


class ParameterTests(unittest.TestCase):
    def test_parameter_count_from_name(self):
        self.assertEqual(parameter_billions('owner/Llama-70B-LittleBit-2'), 70)
        self.assertEqual(parameter_billions('Haverbex/Qwen3.8-27B-LittleBit-0.1bit'), 27)
        self.assertIsNone(parameter_billions('owner/LittleBit-research'))

    def test_does_not_accept_non_littlebit_brand(self):
        self.assertIsNone(classify_model({"id": "someone/Little-Bitch-70B", "tags": []}))

    def test_filter_small_models(self):
        self.assertIsNone(classify_model({"id": "LittleBitLLM/littlebit-qwen3-8b-0.55bpw", "tags": ["littlebit"]}))

    def test_27b_is_explicitly_ineligible(self):
        candidate = classify_model({
            "id": "Haverbex/Qwen3.8-27B-LittleBit-0.1bit",
            "tags": ["littlebit"],
            "siblings": [{"rfilename": "model-00001-of-00231.safetensors", "size": 1234567}],
        })
        self.assertEqual(candidate["model_size_billions_inferred_from_name"], 27)
        self.assertEqual(candidate["status"], "REJECTED_PUBLISHED_QUALITY")
        self.assertFalse(candidate["ready_to_run"])
        self.assertFalse(candidate["parameters_verified"])

    def test_unknown_70b_not_treated_as_runnable(self):
        candidate = classify_model({
            "id": "somebody/Llama-70B-LittleBit-2",
            "tags": ["littlebit"],
            "siblings": [{"rfilename": "config.json"},
                         {"rfilename": "littlebit_config.json"},
                         {"rfilename": "weights.safetensors", "size": 1_000_000_000}],
        })
        self.assertEqual(candidate["status"], "UNVERIFIED")
        self.assertTrue(candidate["checkpoint_files_present"])
        self.assertFalse(candidate["ready_to_run"])
        self.assertFalse(candidate["training_quality_verified"])
        self.assertFalse(candidate["packed_runtime_verified"])

    def test_unknown_sizes_are_not_zero(self):
        candidate = classify_model({
            "id": "someone/LittleBit-70B", "tags": ["littlebit"],
            "siblings": [{"rfilename": "weights.safetensors"}],
        })
        self.assertIsNone(candidate["stored_weight_bytes"])

    def test_only_report_confirmed_sources(self):
        raw = [
            {"id": "someone/LittleBit-70B", "tags": ["littlebit"], "siblings": []},
            {"id": "Aculi/Little-Bitch-3B", "tags": []},
        ]
        report = scan_models(raw)
        self.assertEqual(len(report["candidates"]), 1)
        self.assertFalse(report["ready_to_run_large_model"])


if __name__ == "__main__":
    unittest.main()
