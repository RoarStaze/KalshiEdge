"""Experimental warp-per-row CUDA backend for Samsung LittleBit packed factors.

Preserves the proven scalar CUDA backend in cuda_packed.py. Uses a CUDA
warp (32 threads) per output row and keeps signs packed as uint32 words.
Benchmark numbers apply only to synthetic matrix-vector kernels, not LLMs.
"""
from dataclasses import dataclass
from functools import lru_cache
import json

from cuda_packed import (
    CUDAPackedMatrix, CUDALittleBitLinear, _CudaPath,
    _cupy, _get_kernel, GPUUnavailableError,
)
from packed_linear import PackedBinaryMatrix, LittleBitPackedLinear


WARP_SOURCE = r'''
extern "C" __global__
void littlebit_warp_matvec(
        const unsigned int* __restrict__ packed,
        const float* __restrict__ x,
        float* __restrict__ y,
        const int nrows,
        const int ncols,
        const int stride_words) {
    const int tid = blockIdx.x * blockDim.x + threadIdx.x;
    const int row = tid >> 5;              // One 32-thread warp per row.
    const int lane = threadIdx.x & 31;
    if (row >= nrows) return;              // Entire warp exits together.
    const unsigned int* row_words = packed +
        ((unsigned long long)row * (unsigned long long)stride_words);
    float subtotal = 0.0f;
    for (int col = lane; col < ncols; col += 32) {
        const unsigned int bits = row_words[col >> 5];
        const unsigned int negative = (bits >> (col & 31)) & 1u;
        subtotal += negative ? -x[col] : x[col];
    }
    // All 32 lanes of every active warp participate in the reduction.
    for (int offset = 16; offset > 0; offset >>= 1) {
        subtotal += __shfl_down_sync(0xffffffffu, subtotal, offset);
    }
    if (lane == 0) y[row] = subtotal;
}
'''


@lru_cache(maxsize=1)
def _get_warp_kernel():
    cp = _cupy()
    return cp.RawKernel(WARP_SOURCE, 'littlebit_warp_matvec', backend='nvrtc')


class WarpCUDAPackedMatrix(CUDAPackedMatrix):
    """Drop-in packed matrix using the experimental warp-parallel kernel."""

    def matvec(self, x):
        cp = _cupy()
        vec = cp.asarray(x, dtype=cp.float32)
        if vec.ndim != 1 or vec.shape[0] != self.columns:
            raise ValueError(f'Expected a vector of length {self.columns}')
        output = cp.empty(self.rows, dtype=cp.float32)
        threads = 128  # Four warps per block -> four rows per block.
        blocks = (self.rows + (threads // 32) - 1) // (threads // 32)
        _get_warp_kernel()((blocks,), (threads,),
                           (self.words, vec, output, cp.int32(self.rows),
                            cp.int32(self.columns), cp.int32((self.columns + 31) // 32)))
        return output


@dataclass(frozen=True)
class _WarpPath(_CudaPath):
    @classmethod
    def from_cpu(cls, v, u, v1, v2, u1, u2):
        cp = _cupy()
        return cls(WarpCUDAPackedMatrix.from_cpu(v), WarpCUDAPackedMatrix.from_cpu(u),
                   cp.asarray(v1, dtype=cp.float32), cp.asarray(v2, dtype=cp.float32),
                   cp.asarray(u1, dtype=cp.float32), cp.asarray(u2, dtype=cp.float32))


class WarpCUDALittleBitLinear(CUDALittleBitLinear):
    @classmethod
    def from_cpu(cls, layer):
        if not isinstance(layer, LittleBitPackedLinear):
            raise TypeError('Expected LittleBitPackedLinear')
        cp = _cupy()
        primary = _WarpPath.from_cpu(layer.v, layer.u, layer.v1, layer.v2, layer.u1, layer.u2)
        residual = _WarpPath.from_cpu(*layer.residual) if layer.residual is not None else None
        bias = cp.asarray(layer.bias, dtype=cp.float32) if layer.bias is not None else None
        return cls(primary, residual, bias)


def benchmark_shapes(rows, columns, repetitions):
    """Validate small synthetic kernel benchmark fits a <=32 MiB factor budget."""
    if any(type(v) is not int or v < 1 for v in (rows, columns, repetitions)):
        raise ValueError('Rows, columns, and repetitions must be positive integers')
    payload = rows * ((columns + 31) // 32) * 4
    if payload > 32 * 1024 * 1024:
        raise ValueError('Packed benchmark payload exceeds the 32 MiB limit')
    if repetitions > 1000:
        raise ValueError('Repetitions must not exceed 1000')
    return payload


def _kernel_latency_ms(kernel, grid, block, arguments, repetitions):
    cp = _cupy()
    for _ in range(5):
        kernel(grid, block, arguments)
    cp.cuda.runtime.deviceSynchronize()
    start, stop = cp.cuda.Event(), cp.cuda.Event()
    start.record()
    for _ in range(repetitions):
        kernel(grid, block, arguments)
    stop.record()
    stop.synchronize()
    return float(cp.cuda.get_elapsed_time(start, stop)) / repetitions


def benchmark(rows=1024, columns=2048, repetitions=50):
    """Compare original and warp CUDA kernel latency using GPU events.

    Inputs, packed words and output buffers are created BEFORE measurement.
    Timings exclude installation/CPU packing/transfers, but host launch gaps
    can affect GPU-event timings for these fast kernels. Do not extrapolate
    these microbenchmarks into Gemma 27B tokens per second.
    """
    payload = benchmark_shapes(rows, columns, repetitions)
    cp = _cupy()
    word_stride = (columns + 31) // 32
    total_words = rows * word_stride
    words = (cp.arange(total_words, dtype=cp.uint32) * cp.uint32(2654435761)
             + cp.uint32(2246822519)).reshape(rows, word_stride)
    x = cp.sin(cp.arange(columns, dtype=cp.float32) * cp.float32(0.013))
    scalar_out = cp.empty(rows, dtype=cp.float32)
    warp_out = cp.empty(rows, dtype=cp.float32)
    args_scalar = (words, x, scalar_out, cp.int32(rows), cp.int32(columns), cp.int32(word_stride))
    args_warp = (words, x, warp_out, cp.int32(rows), cp.int32(columns), cp.int32(word_stride))
    scalar_kernel = _get_kernel()
    warp_kernel = _get_warp_kernel()
    scalar_blocks = (rows + 127) // 128
    warp_blocks = (rows + 3) // 4
    scalar_kernel((scalar_blocks,), (128,), args_scalar)
    warp_kernel((warp_blocks,), (128,), args_warp)
    cp.cuda.runtime.deviceSynchronize()
    error_vector = cp.abs(scalar_out - warp_out)
    max_abs_error = float(cp.max(error_vector).get())
    acceptable = cp.float32(0.002) + cp.float32(0.0002) * cp.abs(scalar_out)
    if not bool(cp.all(error_vector <= acceptable).item()):
        raise AssertionError(f'Scalar/warp output mismatch: max absolute error {max_abs_error}')
    scalar_ms = _kernel_latency_ms(scalar_kernel, (scalar_blocks,), (128,), args_scalar, repetitions)
    warp_ms = _kernel_latency_ms(warp_kernel, (warp_blocks,), (128,), args_warp, repetitions)
    device_name = cp.cuda.runtime.getDeviceProperties(cp.cuda.runtime.getDevice())['name']
    if isinstance(device_name, bytes):
        device_name = device_name.decode('utf-8', errors='replace')
    return {
        'status': 'CUDA_WARP_BENCHMARK_OK',
        'checkpoint': 'SYNTHETIC_ONLY',
        'trained_model': False,
        'gpu_kernel_executed': True,
        'device': str(device_name),
        'rows': rows,
        'columns': columns,
        'packed_binary_bytes': payload,
        'repetitions': repetitions,
        'max_abs_error': max_abs_error,
        'scalar_ms_per_call': round(scalar_ms, 6),
        'warp_ms_per_call': round(warp_ms, 6),
        'speedup_scalar_over_warp': round(scalar_ms / warp_ms, 3) if warp_ms else None,
    }


if __name__ == '__main__':
    try:
        print(json.dumps([benchmark(128, 256), benchmark(1024, 2048),
                          benchmark(2048, 4096, 30)], indent=2))
    except GPUUnavailableError as exc:
        print('CUDA_NOT_AVAILABLE: ' + str(exc))
        raise SystemExit(2)
