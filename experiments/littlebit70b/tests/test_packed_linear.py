"""TDD contract for a truthful LittleBit-only packed-factor CPU prototype."""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packed_linear import PackedBinaryMatrix, LittleBitPackedLinear


def reference_matvec(rows, vector):
    return [sum(a * b for a, b in zip(row, vector)) for row in rows]


class PackedBinaryMatrixTests(unittest.TestCase):
    def test_exact_round_trip_rows_across_32_bit_boundaries(self):
        for width in (1, 7, 31, 32, 33, 63, 64, 65):
            rng = random.Random(width)
            signs = [[rng.choice((-1, 1)) for _ in range(width)] for _ in range(4)]
            packed = PackedBinaryMatrix.from_signs(signs)
            self.assertEqual(packed.shape, (4, width))
            self.assertEqual(packed.storage_bytes, 4 * ((width + 31) // 32) * 4)
            vector = [rng.uniform(-1, 1) for _ in range(width)]
            expected = reference_matvec(signs, vector)
            actual = packed.matvec(vector)
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b, places=10)

    def test_signed_int32_upstream_compatibility(self):
        # Samsung's packer treats -1 as a set bit; bit 31 sets int32 sign.
        packed = PackedBinaryMatrix.from_words([[-2147483648]], columns=32)
        vector = [0.0] * 31 + [2.5]
        self.assertEqual(packed.matvec(vector), [-2.5])

    def test_bad_shapes_are_rejected(self):
        with self.assertRaises(ValueError):
            PackedBinaryMatrix.from_signs([[1, -1], [-1]])
        with self.assertRaises(ValueError):
            PackedBinaryMatrix.from_signs([[0, 1]])
        with self.assertRaises(ValueError):
            PackedBinaryMatrix.from_signs([])
        with self.assertRaises(ValueError):
            PackedBinaryMatrix.from_words([[1]], columns=33)
        with self.assertRaises(ValueError):
            PackedBinaryMatrix.from_words([[1]], columns=0)
        with self.assertRaises(ValueError):
            PackedBinaryMatrix.from_words([[2**32]], columns=1)
        with self.assertRaises(ValueError):
            PackedBinaryMatrix.from_signs([[1, 1]]).matvec([1.0])


class LittleBitPackedLinearTests(unittest.TestCase):
    def test_exact_littlebit_reference_equation_with_scales_and_bias(self):
        # Upstream: (((((x * v2) @ V.T) * v1*u2) @ U.T) * u1) + bias.
        v = [[-1, 1, -1, 1, 1], [1, 1, -1, -1, -1], [-1, -1, 1, 1, -1]]
        u = [[1, 1, -1], [1, -1, -1]]
        x = [1.3, -0.4, 0.2, 0.5, -1.1]
        v2 = [1.1, 0.6, -1.5, 0.1, 2.0]
        v1 = [0.5, -2.0, 0.3]
        u2 = [1.5, 0.4, -0.7]
        u1 = [3.0, -2.0]
        bias = [0.125, -0.25]
        h = reference_matvec(v, [a * b for a, b in zip(x, v2)])
        h = [a * b * c for a, b, c in zip(h, v1, u2)]
        out = reference_matvec(u, h)
        expected = [a * b + c for a, b, c in zip(out, u1, bias)]
        layer = LittleBitPackedLinear(
            v=PackedBinaryMatrix.from_signs(v),
            u=PackedBinaryMatrix.from_signs(u),
            v1=v1, v2=v2, u1=u1, u2=u2, bias=bias,
        )
        for actual, want in zip(layer.forward(x), expected):
            self.assertAlmostEqual(actual, want, places=10)

    def test_residual_path_uses_same_packed_equation(self):
        signs = [[1, -1], [-1, 1]]
        layer = LittleBitPackedLinear(
            v=PackedBinaryMatrix.from_signs(signs),
            u=PackedBinaryMatrix.from_signs(signs),
            v1=[1, 1], v2=[1, 1], u1=[1, 1], u2=[1, 1],
            residual=(
                PackedBinaryMatrix.from_signs(signs), PackedBinaryMatrix.from_signs(signs),
                [1, 1], [1, 1], [1, 1], [1, 1],
            ),
        )
        base = LittleBitPackedLinear(
            v=PackedBinaryMatrix.from_signs(signs),
            u=PackedBinaryMatrix.from_signs(signs),
            v1=[1, 1], v2=[1, 1], u1=[1, 1], u2=[1, 1],
        ).forward([2, 1])
        self.assertEqual(layer.forward([2, 1]), [2 * v for v in base])

    def test_invalid_scales_rejected(self):
        v = PackedBinaryMatrix.from_signs([[1, -1]])
        u = PackedBinaryMatrix.from_signs([[1]])
        with self.assertRaises(ValueError):
            LittleBitPackedLinear(v=v, u=u, v1=[1], v2=[1], u1=[1], u2=[1])


if __name__ == "__main__":
    unittest.main()
