import math
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from resident_stream import validate_caps, summarize_pairs


class CPUContracts(unittest.TestCase):
    def test_invalid_allocations_fail_early(self):
        for value in [0, -1, 1.5, True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_caps(value, 4096)
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_caps(4096, value)

    def test_summary_reports_median_speedup_without_model_claims(self):
        pairs = [(20.0, 12.0), (24.0, 11.0), (22.0, 10.0)]
        out = summarize_pairs(pairs)
        self.assertEqual(out['median_cpu_roundtrip_ms'], 22.0)
        self.assertEqual(out['median_gpu_resident_ms'], 11.0)
        self.assertEqual(out['speedup_cpu_roundtrip_over_gpu_resident'], 2.0)
        self.assertEqual(out['sample_count'], 3)
        self.assertFalse(out['timing_variability_warning'])

    def test_summary_flags_outliers(self):
        out = summarize_pairs([(20, 10), (80, 11), (20, 10)])
        self.assertTrue(out['timing_variability_warning'])

    def test_summary_rejects_invalid_samples(self):
        for entries in ([], [(1, 0)], [(float('nan'), 1)], [(1, -1)], [(1, float('inf'))]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                summarize_pairs(entries)


try:
    from cuda_packed import cuda_is_available
except ImportError:
    def cuda_is_available():
        return False


@unittest.skipUnless(cuda_is_available(), 'CuPy CUDA GPU required')
class GPUIntegration(unittest.TestCase):
    def test_resident_matches_reference_and_evicts_weights(self):
        from stream_profile import ProfileConfig, build_checkpoint
        from layer_stream import LayerStep, CUDALayerStream
        from resident_stream import GPUResidentActivationStream
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.safetensors'
            steps, _ = build_checkpoint(path, ProfileConfig(width=65, rank=17, layers=3, repeats=1))
            steps = [LayerStep(s.checkpoint, s.prefix) for s in steps]
            vector = [math.sin(i * .03) for i in range(65)]
            baseline = CUDALayerStream().run(steps, vector)
            resident = GPUResidentActivationStream().run(steps, vector)
            self.assertEqual(resident['eviction_count'], 3)
            self.assertEqual(resident['weight_pool_live_bytes_after_run'], 0)
            self.assertEqual(resident['activation_pool_live_bytes_after_run'], 0)
            self.assertGreater(resident['weight_pool_peak_reserved_bytes'], 0)
            self.assertGreater(resident['activation_pool_peak_reserved_bytes'], 0)
            self.assertEqual(resident['intermediate_cpu_activation_copies'], 0)
            for got, want in zip(resident['output'], baseline['output']):
                self.assertAlmostEqual(got, want, delta=1e-4 * (1 + abs(want)))

    def test_paired_benchmark_completes_with_real_cuda(self):
        from stream_profile import ProfileConfig
        from resident_stream import paired_profile
        result = paired_profile(ProfileConfig(width=65, rank=17, layers=3, repeats=2))
        self.assertEqual(result['status'], 'LITTLEBIT_GPU_RESIDENT_COMPARISON_OK')
        self.assertEqual(result['sample_count'], 2)
        self.assertEqual(result['private_weight_and_activation_live_after_run'], 0)
        self.assertFalse(result['vram_peak_measured'])


if __name__ == '__main__':
    unittest.main()
