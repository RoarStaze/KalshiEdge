"""CPU proof-of-concept for Samsung LittleBit's packed factor inference.

Uses upstream-compatible, row-major int32 words with LSB-first bits:
+1 => 0, -1 => 1. Computes (x*v2) V.T (v1*u2) U.T u1,
with optional residual factor path and bias. Never expands weight matrices.

NOT an optimized CUDA kernel, checkpoint loader, tokenizer or LLM.
No pretrained model is supplied, trained or quantized by this module.
"""
from dataclasses import dataclass
import json
import math

BITS = 32
WORD_MASK = (1 << BITS) - 1


@dataclass(frozen=True)
class PackedBinaryMatrix:
    """Binarized {-1,+1} rows in Samsung's lsb_first 32-bit packing."""
    rows: tuple[tuple[int, ...], ...]
    columns: int

    @classmethod
    def from_signs(cls, rows):
        if not rows or not rows[0]:
            raise ValueError("A nonempty 2D sign matrix is required")
        width = len(rows[0])
        encoded = []
        for row in rows:
            if len(row) != width:
                raise ValueError("Jagged sign matrix")
            if any(value not in (-1, 1) for value in row):
                raise ValueError("All values must be -1 or +1")
            words = []
            for start in range(0, width, BITS):
                word = sum(1 << offset for offset, value in enumerate(row[start:start + BITS]) if value == -1)
                # Keep int32 signed representation, as in upstream torch.int32.
                words.append(word if word < (1 << 31) else word - (1 << 32))
            encoded.append(tuple(words))
        return cls.from_words(encoded, columns=width)

    @classmethod
    def from_words(cls, rows, columns):
        if not isinstance(columns, int) or columns <= 0 or not rows:
            raise ValueError("Positive columns and nonempty word rows required")
        expected = (columns + BITS - 1) // BITS
        packed = []
        for row in rows:
            if len(row) != expected:
                raise ValueError("Incompatible packed row width")
            if any(type(w) is not int or not -(1 << 31) <= w <= WORD_MASK for w in row):
                raise ValueError("Packed words must fit int32/uint32")
            packed.append(tuple(row))
        return cls(rows=tuple(packed), columns=columns)

    @property
    def shape(self):
        return (len(self.rows), self.columns)

    @property
    def storage_bytes(self):
        """Exact packed payload, excluding Python object overhead or scaling vectors."""
        return len(self.rows) * ((self.columns + BITS - 1) // BITS) * 4

    def matvec(self, vector):
        """Multiply by packed signs directly, without materializing full matrices.

        A sign row equals +1 minus twice the subset whose bit is set.
        This plain-Python CPU path prioritizes reference correctness, not speed.
        """
        if len(vector) != self.columns:
            raise ValueError(f"Expected {self.columns} values, got {len(vector)}")
        total = sum(vector)
        result = []
        for row in self.rows:
            neg_sum = 0.0
            for index, stored in enumerate(row):
                mask = stored & WORD_MASK
                base = index * BITS
                available = min(BITS, self.columns - base)
                mask &= (1 << available) - 1
                while mask:
                    bit = mask & -mask
                    neg_sum += vector[base + bit.bit_length() - 1]
                    mask ^= bit
            result.append(total - 2 * neg_sum)
        return result


@dataclass
class LittleBitPackedLinear:
    """Low-rank binary factor layer with floating scales; inference only."""
    v: PackedBinaryMatrix  # (rank, input)
    u: PackedBinaryMatrix  # (output, rank)
    v1: list[float]         # rank
    v2: list[float]         # input
    u1: list[float]         # output
    u2: list[float]         # rank
    bias: list[float] | None = None
    residual: tuple | None = None  # optional (v,u,v1,v2,u1,u2)

    def __post_init__(self):
        self._validate(self.v, self.u, self.v1, self.v2, self.u1, self.u2)
        if self.bias is not None and len(self.bias) != self.u.shape[0]:
            raise ValueError("Incorrect bias length")
        if self.residual is not None:
            if len(self.residual) != 6:
                raise ValueError("Residual must have six factors/scales")
            self._validate(*self.residual)
            if self.residual[0].shape != self.v.shape or self.residual[1].shape != self.u.shape:
                raise ValueError("Residual must have identical matrix dimensions")

    @staticmethod
    def _validate(v, u, v1, v2, u1, u2):
        if v.shape[0] != u.shape[1]:
            raise ValueError("V rows must equal U columns (rank)")
        if (len(v1), len(v2), len(u1), len(u2)) != (v.shape[0], v.shape[1], u.shape[0], v.shape[0]):
            raise ValueError("Incompatible scale lengths")

    @staticmethod
    def _path(x, v, u, v1, v2, u1, u2):
        first = v.matvec([left * scale for left, scale in zip(x, v2)])
        hidden = [value * s1 * s2 for value, s1, s2 in zip(first, v1, u2)]
        return [value * scale for value, scale in zip(u.matvec(hidden), u1)]

    def forward(self, x):
        if len(x) != self.v.columns:
            raise ValueError("Incorrect input dimension")
        output = self._path(x, self.v, self.u, self.v1, self.v2, self.u1, self.u2)
        if self.residual is not None:
            extra = self._path(x, *self.residual)
            output = [a + b for a, b in zip(output, extra)]
        if self.bias is not None:
            output = [a + b for a, b in zip(output, self.bias)]
        return output


def demo():
    # Synthetic signs, not pretrained weights; small enough for any laptop.
    v = PackedBinaryMatrix.from_signs([[1 if (r + c) % 3 else -1 for c in range(96)] for r in range(24)])
    u = PackedBinaryMatrix.from_signs([[1 if (r + c) % 4 else -1 for c in range(24)] for r in range(48)])
    layer = LittleBitPackedLinear(v=v, u=u, v1=[0.5] * 24, v2=[1.0] * 96,
                                  u1=[0.1] * 48, u2=[0.25] * 24)
    result = layer.forward([math.sin(i) for i in range(96)])
    return {
        "status": "PACKED_FACTOR_CPU_DEMO_OK",
        "checkpoint": "SYNTHETIC_ONLY",
        "trained_model": False,
        "gpu_kernel": False,
        "v_shape": list(v.shape),
        "u_shape": list(u.shape),
        "binary_factor_bytes": v.storage_bytes + u.storage_bytes,
        "output_length": len(result),
        "output_first_3": [round(x, 6) for x in result[:3]],
    }


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
