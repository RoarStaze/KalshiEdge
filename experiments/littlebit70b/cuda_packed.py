"""Optional CuPy/CUDA implementation of LittleBit packed binary factors.

Runs each signed binary matrix-vector product directly from int32 packed words.
Not a trained model, an accelerated complete LLM, or a Gemma checkpoint reader.
The simple per-output-row CUDA kernel prioritizes correctness over throughput.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
import math

from packed_linear import PackedBinaryMatrix, LittleBitPackedLinear


class GPUUnavailableError(RuntimeError):
    """CuPy, a compatible CUDA driver, or the GPU is unavailable."""


def cuda_is_available():
    try:
        import cupy as cp
        return cp.cuda.runtime.getDeviceCount() > 0
    except Exception:
        return False


def _cupy():
    try:
        import cupy as cp
        if cp.cuda.runtime.getDeviceCount() < 1:
            raise GPUUnavailableError('No CUDA device detected by CuPy.')
        return cp
    except GPUUnavailableError:
        raise
    except (ImportError, RuntimeError, OSError) as exc:
        raise GPUUnavailableError(
            'CuPy/CUDA unavailable. Use an isolated Python environment with a compatible '
            'cupy-cuda12x installation and an NVIDIA CUDA driver. '
            'Do not modify the KalshiEdge trading environment.'
        ) from exc


CUDA_SOURCE = r'''
extern "C" __global__
void littlebit_packed_matvec(
        const unsigned int* __restrict__ packed,
        const float* __restrict__ x,
        float* __restrict__ y,
        const int nrows,
        const int ncols,
        const int stride_words) {
    const int row = blockIdx.x * blockDim.x + threadIdx.x;
    if (row >= nrows) return;
    const unsigned int* p = packed + ((unsigned long long)row * (unsigned long long)stride_words);
    float sum = 0.f;
    // 0-bit means +1; 1-bit means -1, LSB-first in signed int32 words.
    // Loop only over ncols, never over padded trailing bits.
    for (int col = 0; col < ncols; ++col) {
        const unsigned int word = p[col >> 5];
        const float sign = ((word >> (col & 31)) & 1u) ? -1.f : 1.f;
        sum += sign * x[col];
    }
    y[row] = sum;
}
'''


@lru_cache(maxsize=1)
def _get_kernel():
    cp = _cupy()
    return cp.RawKernel(CUDA_SOURCE, 'littlebit_packed_matvec', backend='nvrtc')


@dataclass(frozen=True)
class CUDAPackedMatrix:
    """CUDA-resident packed uint32 words, re-used across repeated matvec calls."""
    words: object
    columns: int
    rows: int
    storage_bytes: int

    @classmethod
    def from_cpu(cls, packed):
        if not isinstance(packed, PackedBinaryMatrix):
            raise TypeError('Expected a PackedBinaryMatrix from packed_linear.py')
        cp = _cupy()
        # Mask signed int32 words before conversion; NumPy 2 may reject overflow.
        unsigned = [[word & 0xffffffff for word in row] for row in packed.rows]
        words = cp.asarray(unsigned, dtype=cp.uint32)
        return cls(words, packed.columns, packed.shape[0], packed.storage_bytes)

    def matvec(self, x):
        cp = _cupy()
        vec = cp.asarray(x, dtype=cp.float32)
        if vec.ndim != 1 or vec.shape[0] != self.columns:
            raise ValueError(f'Expected a vector of length {self.columns}')
        output = cp.empty(self.rows, dtype=cp.float32)
        threads = 128
        _get_kernel()(((self.rows + threads - 1) // threads,), (threads,),
                      (self.words, vec, output, cp.int32(self.rows),
                       cp.int32(self.columns), cp.int32((self.columns + 31) // 32)))
        return output


@dataclass(frozen=True)
class _CudaPath:
    v: CUDAPackedMatrix
    u: CUDAPackedMatrix
    v1: object
    v2: object
    u1: object
    u2: object

    @classmethod
    def from_cpu(cls, v, u, v1, v2, u1, u2):
        cp = _cupy()
        return cls(CUDAPackedMatrix.from_cpu(v), CUDAPackedMatrix.from_cpu(u),
                   cp.asarray(v1, dtype=cp.float32), cp.asarray(v2, dtype=cp.float32),
                   cp.asarray(u1, dtype=cp.float32), cp.asarray(u2, dtype=cp.float32))

    def forward(self, x):
        h = self.v.matvec(x * self.v2)
        return self.u.matvec(h * self.v1 * self.u2) * self.u1


@dataclass(frozen=True)
class CUDALittleBitLinear:
    primary: _CudaPath
    residual: _CudaPath | None = None
    bias: object = None

    @classmethod
    def from_cpu(cls, layer):
        if not isinstance(layer, LittleBitPackedLinear):
            raise TypeError('Expected LittleBitPackedLinear')
        cp = _cupy()
        primary = _CudaPath.from_cpu(layer.v, layer.u, layer.v1, layer.v2, layer.u1, layer.u2)
        residual = _CudaPath.from_cpu(*layer.residual) if layer.residual is not None else None
        bias = cp.asarray(layer.bias, dtype=cp.float32) if layer.bias is not None else None
        return cls(primary, residual, bias)

    def forward(self, x):
        cp = _cupy()
        vector = cp.asarray(x, dtype=cp.float32)
        if vector.ndim != 1 or vector.shape[0] != self.primary.v.columns:
            raise ValueError(f'Expected a vector of length {self.primary.v.columns}')
        out = self.primary.forward(vector)
        if self.residual is not None:
            out = out + self.residual.forward(vector)
        if self.bias is not None:
            out = out + self.bias
        return out


def demo():
    if not cuda_is_available():
        raise GPUUnavailableError('CuPy GPU runtime not available; no fallback CPU success is reported.')
    cp = _cupy()
    v = PackedBinaryMatrix.from_signs([[1 if (r + c) % 3 else -1 for c in range(96)] for r in range(24)])
    u = PackedBinaryMatrix.from_signs([[1 if (r + c) % 4 else -1 for c in range(24)] for r in range(48)])
    cpu = LittleBitPackedLinear(v=v, u=u, v1=[0.5]*24, v2=[1.0]*96,
                                u1=[0.1]*48, u2=[0.25]*24)
    x = [math.sin(i) for i in range(96)]
    gpu = CUDALittleBitLinear.from_cpu(cpu)
    actual = gpu.forward(x).get().tolist()
    expected = cpu.forward(x)
    max_abs_error = max(abs(a - b) for a, b in zip(actual, expected))
    tolerance = 1e-4 * (1 + max(abs(b) for b in expected))
    if max_abs_error > tolerance:
        raise AssertionError(f'CUDA parity check failed: max abs error {max_abs_error} > {tolerance}')
    name = cp.cuda.runtime.getDeviceProperties(cp.cuda.runtime.getDevice())['name']
    if isinstance(name, bytes):
        name = name.decode('utf-8', errors='replace')
    return {
        'status': 'CUDA_PACKED_FACTOR_DEMO_OK',
        'checkpoint': 'SYNTHETIC_ONLY',
        'trained_model': False,
        'cuda_kernel_executed': True,
        'device': str(name),
        'binary_factor_bytes': v.storage_bytes + u.storage_bytes,
        'output_length': len(actual),
        'max_abs_error': max_abs_error,
    }


if __name__ == '__main__':
    try:
        print(json.dumps(demo(), indent=2))
    except GPUUnavailableError as exc:
        print('CUDA_NOT_AVAILABLE: ' + str(exc))
        raise SystemExit(2)
