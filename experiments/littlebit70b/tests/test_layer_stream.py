"""Behavioral tests for bounded sequential LittleBit layer execution."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cuda_packed import cuda_is_available
from layer_stream import (
    LayerStep, MemoryBudgetError, run_cpu_sequence,
    CUDALayerStream, write_synthetic_two_layer_checkpoint, demo,
)
from safetensors_layer import SafetensorsLittleBitLayer


class CPUSequenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'layers.safetensors'
        self.steps = write_synthetic_two_layer_checkpoint(self.path)
        self.x = [1., -2., 3., -4., 5.]

    def test_distinct_layer_shapes_and_explicit_cpu_forward_composition(self):
        self.assertEqual(len(self.steps), 2)
        first = SafetensorsLittleBitLayer.from_file(self.path, self.steps[0].prefix)
        second = SafetensorsLittleBitLayer.from_file(self.path, self.steps[1].prefix)
        self.assertEqual((first.v_shape[1], first.u_shape[0]), (5, 2))
        self.assertEqual((second.v_shape[1], second.u_shape[0]), (2, 3))
        expected = second.forward_cpu(first.forward_cpu(self.x))
        result = run_cpu_sequence(self.steps, self.x)
        self.assertEqual(result['output'], expected)
        self.assertEqual(result['layer_count'], 2)
        self.assertFalse(result['cuda_executed'])
        self.assertEqual(result['checkpoint'], 'UNVERIFIED_LOCAL_CHECKPOINT')

    def test_empty_sequence_rejected(self):
        with self.assertRaises(ValueError):
            run_cpu_sequence([], self.x)

    def test_bad_input_dimension_fails_before_second_layer(self):
        with self.assertRaisesRegex(ValueError, 'input dimension'):
            run_cpu_sequence(self.steps, [1., 2., 3.])

    def test_input_is_not_mutated(self):
        x = self.x[:]
        run_cpu_sequence(self.steps, x)
        self.assertEqual(x, self.x)

    def test_per_layer_selected_tensor_budget_honored(self):
        with self.assertRaisesRegex(ValueError, 'budget'):
            run_cpu_sequence(self.steps, self.x, max_layer_bytes=8)

    def test_invalid_gpu_pool_limit_rejected_without_gpu(self):
        for invalid in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                CUDALayerStream(pool_limit_bytes=invalid)

    def test_invalid_cuda_backend_rejected_without_gpu(self):
        with self.assertRaises(ValueError):
            CUDALayerStream(backend='not-real')

    def test_demo_cpu_only_or_gpu_is_explicit(self):
        report = demo()
        self.assertEqual(report['status'], 'LITTLEBIT_TWO_LAYER_STREAM_OK')
        self.assertEqual(report['layer_count'], 2)
        self.assertEqual(report['checkpoint'], 'SYNTHETIC_ONLY')
        self.assertFalse(report['trained_model'])
        self.assertFalse(report['whole_llm'])
        self.assertEqual(report['cuda_executed'], cuda_is_available())


@unittest.skipUnless(cuda_is_available(), 'CuPy CUDA GPU required')
class GPUStreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'layers.safetensors'
        self.steps = write_synthetic_two_layer_checkpoint(self.path)
        self.x = [1., -2., 3., -4., 5.]

    def test_gpu_sequence_matches_cpu_and_evicts_between_steps(self):
        expected = run_cpu_sequence(self.steps, self.x)['output']
        for backend in ('warp', 'scalar'):
            report = CUDALayerStream(pool_limit_bytes=8*1024*1024, backend=backend).run(self.steps, self.x)
            for got, want in zip(report['output'], expected):
                self.assertAlmostEqual(got, want, delta=1e-4*(1+abs(want)))
            self.assertEqual(report['layer_count'], 2)
            self.assertEqual(report['eviction_count'], 2)
            self.assertEqual(report['live_pool_bytes_after_last_evict'], 0)
            self.assertGreater(report['peak_pool_bytes'], 0)
            self.assertLessEqual(report['peak_pool_bytes'], 8*1024*1024)

    def test_pool_too_small_fails_instead_of_oversubscribing(self):
        with self.assertRaises(MemoryBudgetError):
            CUDALayerStream(pool_limit_bytes=1).run(self.steps, self.x)

    def test_repeat_execution_does_not_retain_private_pool_blocks(self):
        runner = CUDALayerStream(pool_limit_bytes=8*1024*1024)
        for _ in range(4):
            out = runner.run(self.steps, self.x)
            self.assertEqual(out['live_pool_bytes_after_last_evict'], 0)
            self.assertEqual(runner.pool.used_bytes(), 0)


if __name__ == '__main__':
    unittest.main()
