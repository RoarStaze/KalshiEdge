"""Behavior and safety contracts for synthetic LittleBit streaming profiles."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stream_profile import ProfileConfig, build_checkpoint, summarize_samples, run_profile

try:
    from cuda_packed import cuda_is_available
except ImportError:
    def cuda_is_available():
        return False


class ProfileConfigTests(unittest.TestCase):
    def test_reasonable_default_is_bounded(self):
        cfg = ProfileConfig()
        self.assertEqual(cfg.width, 1024)
        self.assertEqual(cfg.rank, 128)
        self.assertEqual(cfg.layers, 8)
        self.assertLess(cfg.packed_bytes, 16 * 1024 * 1024)

    def test_bad_shapes_and_limits_rejected(self):
        overrides = [dict(width=0), dict(width=8193), dict(rank=0),
                     dict(rank=513), dict(layers=33), dict(repeats=0),
                     dict(repeats=11), dict(pool_limit_mib=0),
                     dict(pool_limit_mib=257), dict(max_file_mib=0),
                     dict(width=4096, rank=512, layers=32, max_file_mib=1)]
        for option in overrides:
            with self.subTest(option=option), self.assertRaises(ValueError):
                ProfileConfig(**option)

    def test_boolean_is_not_valid_integer(self):
        with self.assertRaises(ValueError):
            ProfileConfig(width=True)


class CheckpointTests(unittest.TestCase):
    def test_writes_real_safetensors_multiple_layers_without_dense_signs(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'synthetic.safetensors'
            cfg = ProfileConfig(width=33, rank=7, layers=3, repeats=1)
            steps, bytes_written = build_checkpoint(dest, cfg)
            self.assertEqual(len(steps), 3)
            self.assertEqual(bytes_written, dest.stat().st_size)
            self.assertTrue(all(s.checkpoint == dest for s in steps))
            self.assertTrue(all(s.prefix == f'synthetic.layers.{i}.linear' for i, s in enumerate(steps)))
            import struct
            import numpy as np
            raw = dest.read_bytes()
            headlen = struct.unpack('<Q', raw[:8])[0]
            header = json.loads(raw[8:8 + headlen])
            self.assertEqual(len(header), 3 * 9)
            for i in range(3):
                key = f'synthetic.layers.{i}.linear.'
                self.assertEqual(header[key + 'U_packed']['shape'], [33, 1])
                self.assertEqual(header[key + 'V_packed']['shape'], [7, 2])
                self.assertEqual(header[key + 'U_shape']['shape'], [2])
                self.assertEqual(header[key + 'V_shape']['shape'], [2])
                self.assertEqual(header[key + 'u1']['shape'], [1, 33])
                self.assertEqual(header[key + 'v2']['shape'], [1, 33])
                entry = header[key + 'V_packed']
                start, end = entry['data_offsets']
                words = np.frombuffer(raw[8 + headlen + start:8 + headlen + end], dtype='<u4').reshape(7, 2)
                # Last packed word contains only column 33; padding bits are clear.
                self.assertTrue((words[:, -1] & np.uint32(0xfffffffe) == 0).all())

    def test_deterministic_payload_for_same_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a.safetensors', Path(tmp) / 'b.safetensors'
            config = ProfileConfig(width=64, rank=8, layers=2)
            build_checkpoint(a, config)
            build_checkpoint(b, config)
            self.assertEqual(a.read_bytes(), b.read_bytes())


class SummaryTests(unittest.TestCase):
    def test_reports_medians_and_pool_peak_without_pretending_total_vram_peak(self):
        samples = [
            (100, {'layer_count': 3, 'eviction_count': 3,
                   'gpu_pool_limit_bytes': 1024 * 1024, 'peak_pool_bytes': 8192,
                   'live_pool_bytes_after_last_evict': 0, 'retained_pool_bytes_after_last_evict': 0,
                   'cuda_executed': True, 'output': [0.25]}),
            (120, {'layer_count': 3, 'eviction_count': 3,
                   'gpu_pool_limit_bytes': 1024 * 1024, 'peak_pool_bytes': 16384,
                   'live_pool_bytes_after_last_evict': 0, 'retained_pool_bytes_after_last_evict': 0,
                   'cuda_executed': True, 'output': [0.25]}),
            (80, {'layer_count': 3, 'eviction_count': 3,
                   'gpu_pool_limit_bytes': 1024 * 1024, 'peak_pool_bytes': 4096,
                   'live_pool_bytes_after_last_evict': 0, 'retained_pool_bytes_after_last_evict': 0,
                   'cuda_executed': True, 'output': [0.25]}),
        ]
        result = summarize_samples(samples, expected_layers=3)
        self.assertEqual(result['total_wall_median_ms'], 100.)
        self.assertEqual(result['private_pool_peak_reserved_bytes'], 16384)
        self.assertEqual(result['live_pool_bytes_after_each_run'], [0, 0, 0])
        self.assertEqual(result['layers_per_second'], 30.0)
        self.assertNotIn('gpu_peak_vram_bytes', result)

    def test_rejects_skipped_layers_and_unreleased_allocations(self):
        base = dict(layer_count=2, eviction_count=2, gpu_pool_limit_bytes=1_000_000,
                    peak_pool_bytes=512, live_pool_bytes_after_last_evict=0,
                    retained_pool_bytes_after_last_evict=0,
                    cuda_executed=True, output=[0.2])
        for change in (dict(eviction_count=1), dict(live_pool_bytes_after_last_evict=10),
                       dict(cuda_executed=False), dict(peak_pool_bytes=1_000_001)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                summarize_samples([(100, base | change)], expected_layers=2)

    def test_rejects_empty_samples_and_invalid_time(self):
        with self.assertRaises(ValueError):
            summarize_samples([], expected_layers=2)
        with self.assertRaises(ValueError):
            summarize_samples([(float('nan'), {})], expected_layers=2)


@unittest.skipUnless(cuda_is_available(), 'CuPy CUDA GPU required')
class CUDAIntegrationTests(unittest.TestCase):
    def test_multilayer_stream_profile_executes_and_evicts_on_gpu(self):
        cfg = ProfileConfig(width=65, rank=17, layers=3, repeats=2)
        out = run_profile(cfg)
        self.assertEqual(out['status'], 'LITTLEBIT_CUDA_STREAM_PROFILE_OK')
        self.assertTrue(out['cuda_executed'])
        self.assertFalse(out['trained_model'])
        self.assertEqual(out['sample_count'], 2)
        self.assertEqual(out['layers'], 3)
        self.assertEqual(out['live_pool_bytes_after_each_run'], [0, 0])
        self.assertFalse(out['vram_peak_measured'])
        self.assertLessEqual(out['private_pool_peak_reserved_bytes'], out['private_pool_limit_bytes'])


if __name__ == '__main__':
    unittest.main()
