"""GPU tests are conditional; import and shape validation always run on CPU."""
import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from cuda_packed import CUDAPackedMatrix, CUDALittleBitLinear, cuda_is_available, GPUUnavailableError
from packed_linear import PackedBinaryMatrix, LittleBitPackedLinear


class CUDAPackedTests(unittest.TestCase):
    def test_module_can_import_without_cupy(self):
        self.assertTrue(callable(cuda_is_available))

    def test_invalid_input_rejected_before_gpu_access(self):
        with self.assertRaises(TypeError):
            CUDAPackedMatrix.from_cpu(object())

    @unittest.skipUnless(cuda_is_available(), 'CuPy and CUDA GPU required')
    def test_packed_matvec_matches_cpu_for_boundary_widths(self):
        for cols in (1, 7, 31, 32, 33, 63, 64, 65, 127):
            rng = random.Random(cols)
            signs = [[rng.choice((-1, 1)) for _ in range(cols)] for _ in range(13)]
            vector = [rng.uniform(-1, 1) for _ in range(cols)]
            cpu = PackedBinaryMatrix.from_signs(signs)
            gpu = CUDAPackedMatrix.from_cpu(cpu)
            self.assertEqual(gpu.storage_bytes, cpu.storage_bytes)
            actual = gpu.matvec(vector).get().tolist()
            for a, b in zip(actual, cpu.matvec(vector)):
                self.assertAlmostEqual(a, b, delta=1e-4 * (1 + abs(b)))

    @unittest.skipUnless(cuda_is_available(), 'CuPy and CUDA GPU required')
    def test_high_bit_signed_int32_compatibility(self):
        packed = PackedBinaryMatrix.from_words([[-2147483648]], columns=32)
        vector = [0.] * 31 + [2.5]
        self.assertAlmostEqual(float(CUDAPackedMatrix.from_cpu(packed).matvec(vector).get()[0]), -2.5)

    @unittest.skipUnless(cuda_is_available(), 'CuPy and CUDA GPU required')
    def test_layer_with_scaling_bias_residual_matches_cpu(self):
        rng = random.Random(37)
        v = PackedBinaryMatrix.from_signs([[rng.choice((-1, 1)) for _ in range(35)] for _ in range(8)])
        u = PackedBinaryMatrix.from_signs([[rng.choice((-1, 1)) for _ in range(8)] for _ in range(13)])
        v1 = [rng.uniform(-1, 1) for _ in range(8)]
        v2 = [rng.uniform(-1, 1) for _ in range(35)]
        u1 = [rng.uniform(-1, 1) for _ in range(13)]
        u2 = [rng.uniform(-1, 1) for _ in range(8)]
        bias = [rng.uniform(-1, 1) for _ in range(13)]
        layer = LittleBitPackedLinear(v, u, v1, v2, u1, u2, bias, (v, u, v1, v2, u1, u2))
        x = [rng.uniform(-1, 1) for _ in range(35)]
        actual = CUDALittleBitLinear.from_cpu(layer).forward(x).get().tolist()
        for a, b in zip(actual, layer.forward(x)):
            self.assertAlmostEqual(a, b, delta=1e-4 * (1 + abs(b)))

    @unittest.skipUnless(cuda_is_available(), 'CuPy and CUDA GPU required')
    def test_input_shape_rejected(self):
        mat = CUDAPackedMatrix.from_cpu(PackedBinaryMatrix.from_signs([[1, -1]]))
        with self.assertRaises(ValueError):
            mat.matvec([1.0])


if __name__ == '__main__':
    unittest.main()
