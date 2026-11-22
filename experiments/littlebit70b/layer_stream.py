"""Streaming two or more synthetic/compatible LittleBit linear layers on CUDA.

One layer is loaded into a private, capped CuPy pool at a time; outputs
move back to CPU before eviction. The pool cap does NOT include CUDA context,
CUDA kernel compiler caches, other memory pools, or an LLM's attention/KV.

This is a proof-of-concept sequential chain of compatible linear layers,
not a Gemma transformer, a model conversion, or a trained checkpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
import gc
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory

import numpy as np

from cuda_packed import _cupy, cuda_is_available
from packed_linear import PackedBinaryMatrix
from safetensors_layer import SafetensorsLittleBitLayer


class MemoryBudgetError(RuntimeError):
    """Private GPU allocation budget or eviction limit exceeded."""


@dataclass(frozen=True)
class LayerStep:
    checkpoint: str | Path
    prefix: str


def _validate_steps(steps, vector):
    if not steps:
        raise ValueError('At least one layer step is required')
    if any(not isinstance(step, LayerStep) for step in steps):
        raise TypeError('Expected LayerStep(checkpoint, prefix) objects')
    return list(vector)


def run_cpu_sequence(steps, vector, *, max_layer_bytes=64 * 1024 * 1024):
    """Sequential CPU reference. Does not imply transformer compatibility."""
    current = _validate_steps(steps, vector)
    for index, step in enumerate(steps):
        layer = SafetensorsLittleBitLayer.from_file(
            step.checkpoint, step.prefix, max_layer_bytes=max_layer_bytes)
        if len(current) != layer.v_shape[1]:
            raise ValueError(f'Layer {index}: input dimension does not match {layer.v_shape[1]}')
        current = layer.forward_cpu(current)
    return {'output': current, 'layer_count': len(steps), 'cuda_executed': False,
            'checkpoint': 'UNVERIFIED_LOCAL_CHECKPOINT', 'trained_model': None, 'whole_llm': False}


class CUDALayerStream:
    """Runs one compatible packed LittleBit linear layer at a time on GPU.

    The private CuPy pool enforces a cap on allocations *through that pool*;
    CUDA context, NVRTC kernels, other pools, and CPU copies are excluded.
    Other concurrent GPU work is not accounted for by this limit.
    """

    def __init__(self, *, pool_limit_bytes=32 * 1024 * 1024,
                 max_layer_bytes=64 * 1024 * 1024, backend='warp'):
        if type(pool_limit_bytes) is not int or pool_limit_bytes <= 0:
            raise ValueError('pool_limit_bytes must be a positive integer')
        if type(max_layer_bytes) is not int or max_layer_bytes <= 0:
            raise ValueError('max_layer_bytes must be a positive integer')
        if backend not in ('warp', 'scalar'):
            raise ValueError("backend must be 'warp' or 'scalar'")
        self.pool_limit_bytes = pool_limit_bytes
        self.max_layer_bytes = max_layer_bytes
        self.backend = backend
        self.pool = None

    def run(self, steps, vector):
        current = _validate_steps(steps, vector)
        cp = _cupy()   # Explicit failure if CUDA is not installed/usable.
        # Per-run pool avoids global allocator changes and stale allocations.
        self.pool = cp.cuda.MemoryPool()
        self.pool.set_limit(size=self.pool_limit_bytes)
        peak = 0
        retained = 0
        live_after_evict = 0
        evicted = 0
        for index, step in enumerate(steps):
            layer = SafetensorsLittleBitLayer.from_file(
                step.checkpoint, step.prefix, max_layer_bytes=self.max_layer_bytes)
            if len(current) != layer.v_shape[1]:
                raise ValueError(f'Layer {index}: input dimension does not match {layer.v_shape[1]}')

            gpu_layer = None
            gpu_output = None
            try:
                with cp.cuda.using_allocator(self.pool.malloc):
                    gpu_layer = layer.to_cuda_layer(backend=self.backend)
                    # GPU input and output buffers are private-pool allocations.
                    gpu_output = gpu_layer.forward(current)
                    current = gpu_output.get().tolist()  # Transfers output to CPU.
                    peak = max(peak, int(self.pool.total_bytes()))
            except cp.cuda.memory.OutOfMemoryError as exc:
                raise MemoryBudgetError(
                    f'Layer {index}: private GPU allocation budget of '
                    f'{self.pool_limit_bytes} bytes exhausted') from exc
            finally:
                # A GPU result must not outlive its active layer in the pool.
                # Synchronize before dropping references, including on failure.
                cp.cuda.get_current_stream().synchronize()
                gpu_output = None
                gpu_layer = None
                layer = None
                gc.collect()
                self.pool.free_all_blocks()
                retained = int(self.pool.total_bytes())
                live_after_evict = int(self.pool.used_bytes())
                evicted += 1
                if live_after_evict != 0:
                    raise MemoryBudgetError('A device allocation remains live after eviction')

        return {
            'status': 'LITTLEBIT_TWO_LAYER_STREAM_OK' if len(steps) == 2 else 'LITTLEBIT_LAYER_STREAM_OK',
            'output': current, 'layer_count': len(steps),
            'eviction_count': evicted, 'gpu_pool_limit_bytes': self.pool_limit_bytes,
            'peak_pool_bytes': peak,
            'retained_pool_bytes_after_last_evict': retained,
            'live_pool_bytes_after_last_evict': live_after_evict,
            'checkpoint': 'UNVERIFIED_LOCAL_CHECKPOINT', 'trained_model': None,
            'whole_llm': False, 'cuda_executed': True,
            'backend': self.backend,
        }


def _synthetic_params(in_features, out_features, rank, seed):
    """Return an independent layer's official-layout raw arrays."""
    v = PackedBinaryMatrix.from_signs([
        [(-1 if (r * 7 + c * 3 + seed) % 5 in (0, 2) else 1)
         for c in range(in_features)] for r in range(rank)
    ])
    u = PackedBinaryMatrix.from_signs([
        [(-1 if (r * 11 + c * 2 + seed) % 4 == 0 else 1)
         for c in range(rank)] for r in range(out_features)
    ])
    return {
        'U_packed': ('I32', np.asarray(u.rows, dtype='<i4')),
        'V_packed': ('I32', np.asarray(v.rows, dtype='<i4')),
        'U_shape': ('I64', np.asarray([out_features, rank], dtype='<i8')),
        'V_shape': ('I64', np.asarray([rank, in_features], dtype='<i8')),
        'u1': ('F32', np.full((1, out_features), 0.25 + 0.1 * seed, dtype='<f4')),
        'u2': ('F32', np.full((1, rank), 0.5, dtype='<f4')),
        'v1': ('F32', np.full((1, rank), 0.25, dtype='<f4')),
        'v2': ('F32', np.full((1, in_features), 1.0, dtype='<f4')),
        'bias': ('F32', np.full((out_features,), 0.01 * seed, dtype='<f4')),
    }


def write_synthetic_two_layer_checkpoint(path):
    """Write two genuine-layout but UNTRAINED LittleBit packed layers."""
    path = Path(path)
    prefix0 = 'synthetic.layers.0.linear'
    prefix1 = 'synthetic.layers.1.linear'
    header = {}
    payload = bytearray()
    for prefix, config in ((prefix0, (5, 2, 3, 1)),
                           (prefix1, (2, 3, 2, 2))):
        for name, (dtype, array) in _synthetic_params(*config).items():
            start = len(payload)
            payload.extend(array.tobytes())
            header[prefix + '.' + name] = {
                'dtype': dtype, 'shape': list(array.shape),
                'data_offsets': [start, len(payload)],
            }
    encoded = json.dumps(header, separators=(',', ':')).encode('utf8')
    encoded += b' ' * (-len(encoded) % 8)  # Standard Safetensors padding.
    path.write_bytes(struct.pack('<Q', len(encoded)) + encoded + payload)
    return [LayerStep(path, prefix0), LayerStep(path, prefix1)]


def demo():
    with TemporaryDirectory(prefix='littlebit-layer-stream-') as temp:
        steps = write_synthetic_two_layer_checkpoint(
            Path(temp) / 'SYNTHETIC_TWO_LAYERS.safetensors')
        x = [1., -2., 3., -4., 5.]
        expected = run_cpu_sequence(steps, x)['output']
        if not cuda_is_available():
            return {
                'status': 'LITTLEBIT_TWO_LAYER_STREAM_OK',
                'cuda_executed': False, 'checkpoint': 'SYNTHETIC_ONLY',
                'trained_model': False, 'whole_llm': False,
                'layer_count': len(steps), 'cpu_result': expected,
            }
        result = CUDALayerStream().run(steps, x)
        max_abs_error = max(abs(a - b) for a, b in zip(result['output'], expected))
        if any(abs(a - b) > 1e-4 * (1 + abs(b)) for a, b in zip(result['output'], expected)):
            raise AssertionError('GPU streaming output does not match CPU reference')
        result['max_abs_error'] = max_abs_error
        result['checkpoint'] = 'SYNTHETIC_ONLY'
        result['trained_model'] = False
        result.pop('output')
        return result


if __name__ == '__main__':
    print(json.dumps(demo(), indent=2))
