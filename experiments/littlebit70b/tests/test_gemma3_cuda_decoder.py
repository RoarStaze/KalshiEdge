"""CPU contract checks and GPU-vs-CPU parity for a tiny packed Gemma3 decoder."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemma3_decoder import DecoderSpec, Gemma3KVCache, make_synthetic_decoder
from cuda_packed import cuda_is_available
from gemma3_cuda_decoder import (CUDAGemma3PackedDecoder, CUDAGemma3KVCache,
                                 _validate_cache_limit, run_demo)


class CPUContracts(unittest.TestCase):
    def test_cache_limits_reject_invalid_types(self):
        for bad in (0, -1, True, 1.2):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _validate_cache_limit(bad)

    def test_backend_math_equations_match_cpu_reference_without_cuda(self):
        # NumPy substitutes for the array operations, but the production CUDA
        # code path's GEMMA math and its packed projection interface still run.
        from gemma3_cuda_decoder import CUDAGemma3PackedDecoder, CUDAGemma3KVCache
        cfg = DecoderSpec(hidden=8, intermediate=12, q_heads=4, kv_heads=2,
                          head_dim=4, sliding_window=3, query_pre_attn_scalar=8.,
                          global_rope_factor=8.)
        cpu = make_synthetic_decoder(cfg, seed=23)
        with patch('gemma3_cuda_decoder._cupy', return_value=np):
            class ArrayProjection:
                def __init__(self, op):
                    self.op = op
                def forward(self, vector):
                    return np.asarray(self.op.forward(vector), dtype=np.float32)
            gpu_equations = CUDAGemma3PackedDecoder(cfg,
                {key: ArrayProjection(op) for key, op in cpu.projections.items()}, cpu.norms)
            for kind in ('sliding_attention', 'full_attention'):
                a = Gemma3KVCache(kind, cfg.sliding_window)
                b = CUDAGemma3KVCache(kind, cfg.sliding_window, max_tokens=12)
                for pos in range(6):
                    x = np.sin(np.arange(cfg.hidden, dtype=np.float32) * .2 + pos * .07)
                    expect = cpu.forward_token(x, pos, a)
                    got = gpu_equations.forward_token(x, pos, b)
                    np.testing.assert_allclose(got, expect, atol=4e-5, rtol=4e-5)
                self.assertEqual(a.positions, b.positions)

    def test_missing_cuda_reports_failure_not_cpu_success(self):
        if not cuda_is_available():
            with self.assertRaises(Exception):
                run_demo()


@unittest.skipUnless(cuda_is_available(), 'CuPy with CUDA device required')
class CUDADecoderTests(unittest.TestCase):
    def setUp(self):
        self.cfg = DecoderSpec(hidden=8, intermediate=12, q_heads=4, kv_heads=2,
                               head_dim=4, sliding_window=3, query_pre_attn_scalar=8.,
                               global_rope_factor=8.)
        self.cpu = make_synthetic_decoder(self.cfg, seed=37)
        self.gpu = CUDAGemma3PackedDecoder.from_cpu(self.cpu)

    def test_global_and_sliding_five_token_decoder_parity(self):
        for kind in ('sliding_attention', 'full_attention'):
            cpu_cache = Gemma3KVCache(kind, self.cfg.sliding_window)
            gpu_cache = CUDAGemma3KVCache(kind, self.cfg.sliding_window, max_tokens=10)
            for position in range(6):
                x = np.sin((np.arange(self.cfg.hidden, dtype=np.float32) + position) * .17)
                expected = self.cpu.forward_token(x, position, cpu_cache)
                actual = self.gpu.forward_token(x, position, gpu_cache).get()
                np.testing.assert_allclose(actual, expected, rtol=5e-4, atol=5e-4)
                self.assertEqual(gpu_cache.positions, cpu_cache.positions)
            self.assertEqual(len(gpu_cache.positions), 3 if kind == 'sliding_attention' else 6)
            self.assertEqual(gpu_cache.keys.shape[-2:], (2, 4))

    def test_two_cuda_decoder_instances_do_not_share_cache(self):
        first = CUDAGemma3KVCache('full_attention', 3, max_tokens=5)
        second = CUDAGemma3KVCache('sliding_attention', 3, max_tokens=5)
        x = np.linspace(-1., 1., self.cfg.hidden, dtype=np.float32)
        a = self.gpu.forward_token(x, 0, first).get()
        b = self.gpu.forward_token(x, 0, second).get()
        np.testing.assert_allclose(a, b, atol=2e-5, rtol=2e-5)
        self.assertEqual(first.positions, [0])
        self.assertEqual(second.positions, [0])

    def test_cache_rejects_repeated_or_skipped_positions(self):
        c = CUDAGemma3KVCache('full_attention', 3, max_tokens=5)
        x = np.ones(self.cfg.hidden, dtype=np.float32)
        self.gpu.forward_token(x, 0, c)
        for p in (0, 2, -1):
            with self.subTest(pos=p), self.assertRaises(ValueError):
                self.gpu.forward_token(x, p, c)
        self.assertEqual(c.positions, [0])

    def test_global_cache_capacity_is_enforced(self):
        c = CUDAGemma3KVCache('full_attention', 3, max_tokens=2)
        x = np.zeros(self.cfg.hidden, dtype=np.float32)
        self.gpu.forward_token(x, 0, c)
        self.gpu.forward_token(x, 1, c)
        with self.assertRaises(MemoryError):
            self.gpu.forward_token(x, 2, c)
        self.assertEqual(c.positions, [0, 1])

    def test_rejects_cache_window_different_from_decoder(self):
        c = CUDAGemma3KVCache('sliding_attention', 2, max_tokens=5)
        with self.assertRaises(ValueError):
            self.gpu.forward_token(np.zeros(self.cfg.hidden, dtype=np.float32), 0, c)
        self.assertEqual(c.positions, [])

    def test_cuda_demo_reports_real_cuda_not_trained_model(self):
        result = run_demo()
        self.assertEqual(result['status'], 'GEMMA3_PACKED_CUDA_DECODER_OK')
        self.assertTrue(result['cuda_executed'])
        self.assertFalse(result['whole_llm'])
        self.assertFalse(result['trained_model'])
        self.assertEqual(result['checkpoint'], 'SYNTHETIC_ONLY')
        self.assertEqual(result['packed_projection_count'], 7)
        self.assertLessEqual(result['max_abs_error'], 1e-3)


if __name__ == '__main__':
    unittest.main()
