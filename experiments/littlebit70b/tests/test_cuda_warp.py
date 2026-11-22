"""Correctness contract for warp-per-row packed CUDA inference."""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packed_linear import PackedBinaryMatrix, LittleBitPackedLinear
from cuda_packed import CUDAPackedMatrix, cuda_is_available
from cuda_warp import WarpCUDAPackedMatrix, WarpCUDALittleBitLinear, benchmark_shapes


class CPUValidations(unittest.TestCase):
    def test_invalid_matrix_input_rejected_without_cupy(self):
        with self.assertRaises(TypeError):
            WarpCUDAPackedMatrix.from_cpu('not-packed')

    def test_benchmark_shape_limits_reject_oversized_payload(self):
        with self.assertRaises(ValueError):
            benchmark_shapes(100000, 65536, 10)
        with self.assertRaises(ValueError):
            benchmark_shapes(0, 128, 10)
        with self.assertRaises(ValueError):
            benchmark_shapes(128, 128, 0)


@unittest.skipUnless(cuda_is_available(), 'CUDA GPU and CuPy required')
class GPUParity(unittest.TestCase):
    def test_boundary_widths_and_incomplete_warps(self):
        for cols in (1, 7, 31, 32, 33, 63, 64, 65, 127, 257):
            for rows in (1, 3, 5, 9):
                rng = random.Random(cols * 113 + rows)
                signs = [[rng.choice((-1, 1)) for _ in range(cols)] for _ in range(rows)]
                vector = [rng.uniform(-1, 1) for _ in range(cols)]
                packed = PackedBinaryMatrix.from_signs(signs)
                result = WarpCUDAPackedMatrix.from_cpu(packed).matvec(vector).get().tolist()
                want = packed.matvec(vector)
                for a, b in zip(result, want):
                    self.assertAlmostEqual(a, b, delta=0.0001 * (1 + abs(b)))

    def test_high_sign_bit_and_padded_bits_are_ignored(self):
        # The high bit of the first int32 word must not be treated as a signed shift.
        a = PackedBinaryMatrix.from_words([[-2147483648, -1]], columns=33)
        b = PackedBinaryMatrix.from_words([[-2147483648, 1]], columns=33)
        x = [0.0] * 31 + [2.5, 3.0]
        wa = float(WarpCUDAPackedMatrix.from_cpu(a).matvec(x).get()[0])
        wb = float(WarpCUDAPackedMatrix.from_cpu(b).matvec(x).get()[0])
        self.assertAlmostEqual(wa, -5.5)
        self.assertAlmostEqual(wb, -5.5)

    def test_layer_with_residual_and_bias_matches_cpu(self):
        rng = random.Random(17)
        v = PackedBinaryMatrix.from_signs([[rng.choice((-1, 1)) for _ in range(65)] for _ in range(19)])
        u = PackedBinaryMatrix.from_signs([[rng.choice((-1, 1)) for _ in range(19)] for _ in range(25)])
        v1 = [rng.uniform(-1, 1) for _ in range(19)]
        v2 = [rng.uniform(-1, 1) for _ in range(65)]
        u1 = [rng.uniform(-1, 1) for _ in range(25)]
        u2 = [rng.uniform(-1, 1) for _ in range(19)]
        bias = [rng.uniform(-1, 1) for _ in range(25)]
        cpu = LittleBitPackedLinear(v, u, v1, v2, u1, u2, bias, (v, u, v1, v2, u1, u2))
        x = [rng.uniform(-1, 1) for _ in range(65)]
        gpu = WarpCUDALittleBitLinear.from_cpu(cpu)
        a = gpu.forward(x).get().tolist()
        b = cpu.forward(x)
        for got, want in zip(a, b):
            self.assertAlmostEqual(got, want, delta=0.0001 * (1 + abs(want)))

    def test_input_length_checked(self):
        p = PackedBinaryMatrix.from_signs([[1, -1]])
        with self.assertRaises(ValueError):
            WarpCUDAPackedMatrix.from_cpu(p).matvec([1.0])


if __name__ == '__main__':
    unittest.main()
