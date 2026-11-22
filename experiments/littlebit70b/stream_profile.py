"""Bounded synthetic LittleBit-2 layer-streaming latency and memory profile.

No trained weights, transformer, or network access. A checkpoint is generated
from random packed uint32 words (never a dense floating-point sign matrix).
Only the private CuPy pool's peak is tracked; memGetInfo() before/after are
snapshots, NOT a measurement of peak device memory consumption.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import statistics
import struct
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np


@dataclass(frozen=True)
class ProfileConfig:
    width: int = 1024
    rank: int = 128
    layers: int = 8
    repeats: int = 3
    pool_limit_mib: int = 32
    max_file_mib: int = 16

    def __post_init__(self):
        limits = {
            'width': (self.width, 1, 8192),
            'rank': (self.rank, 1, 512),
            'layers': (self.layers, 1, 32),
            'repeats': (self.repeats, 1, 10),
            'pool_limit_mib': (self.pool_limit_mib, 1, 256),
            'max_file_mib': (self.max_file_mib, 1, 64),
        }
        for name, (value, minimum, maximum) in limits.items():
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f'{name} must be an integer from {minimum} to {maximum}')
        # Conservative metadata estimate: checks budget before any checkpoint is generated.
        extras_per_layer = 24 * self.width + 16 * self.rank + 4096
        if self.packed_bytes + self.layers * extras_per_layer > self.max_file_mib * 1024 * 1024:
            raise ValueError('The synthetic checkpoint exceeds max_file_mib budget')

    @property
    def packed_bytes(self):
        words_u = (self.rank + 31) // 32
        words_v = (self.width + 31) // 32
        return self.layers * (self.width * words_u + self.rank * words_v) * 4


def _packed_signs(rng, rows, cols):
    """Generate random packed bits directly, without allocating dense weight matrices."""
    words = (cols + 31) // 32
    result = rng.integers(0, 2**32, size=(rows, words), dtype=np.uint32)
    valid_last_bits = cols % 32
    if valid_last_bits:
        result[:, -1] &= np.uint32((1 << valid_last_bits) - 1)
    return result.view('<i4')


@dataclass(frozen=True)
class _Step:
    checkpoint: Path
    prefix: str


def build_checkpoint(path: Path, config: ProfileConfig):
    """Create a *synthetic* Samsung-keyed Safetensors file with bounded bytes.

    Caller owns cleanup. Returns steps and actual output-file byte count.
    """
    path = Path(path)
    header = {}
    payload = bytearray()
    steps = []
    file_limit = config.max_file_mib * 1024 * 1024
    for i in range(config.layers):
        rng = np.random.default_rng(0x1B1700 + i)
        width, rank = config.width, config.rank
        prefix = f'synthetic.layers.{i}.linear'
        steps.append(_Step(path, prefix))
        arrays = {
            'U_packed': ('I32', _packed_signs(rng, width, rank)),
            'V_packed': ('I32', _packed_signs(rng, rank, width)),
            'U_shape': ('I64', np.array([width, rank], dtype='<i8')),
            'V_shape': ('I64', np.array([rank, width], dtype='<i8')),
            'u1': ('F32', np.full((1, width), 1.0 / math.sqrt(rank), dtype='<f4')),
            'u2': ('F32', np.ones((1, rank), dtype='<f4')),
            'v1': ('F32', np.ones((1, rank), dtype='<f4')),
            'v2': ('F32', np.full((1, width), 1.0 / math.sqrt(width), dtype='<f4')),
            'bias': ('F32', np.zeros((width,), dtype='<f4')),
        }
        for key, (dtype, array) in arrays.items():
            start = len(payload)
            payload.extend(array.tobytes(order='C'))
            if len(payload) > file_limit:
                raise ValueError('Checkpoint payload exceeds max_file_mib budget')
            header[prefix + '.' + key] = {
                'dtype': dtype, 'shape': list(array.shape),
                'data_offsets': [start, len(payload)],
            }
    encoded = json.dumps(header, separators=(',', ':')).encode('utf-8')
    encoded += b' ' * (-len(encoded) % 8)
    final_size = 8 + len(encoded) + len(payload)
    if final_size > file_limit:
        raise ValueError('Checkpoint file including header exceeds max_file_mib budget')
    path.write_bytes(struct.pack('<Q', len(encoded)) + encoded + payload)
    return steps, final_size


def summarize_samples(samples, *, expected_layers):
    if type(expected_layers) is not int or expected_layers < 1 or not samples:
        raise ValueError('Nonempty samples and positive expected_layers required')
    timings = []
    peaks = []
    live = []
    for elapsed_ms, result in samples:
        if not isinstance(elapsed_ms, (float, int)) or not math.isfinite(elapsed_ms) or elapsed_ms <= 0:
            raise ValueError('Invalid wall-clock measurement')
        required = ('layer_count', 'eviction_count', 'gpu_pool_limit_bytes',
                    'peak_pool_bytes', 'live_pool_bytes_after_last_evict',
                    'cuda_executed', 'output')
        if not all(k in result for k in required):
            raise ValueError('Incomplete streaming result')
        if (not result['cuda_executed'] or
                result['layer_count'] != expected_layers or
                result['eviction_count'] != expected_layers or
                result['live_pool_bytes_after_last_evict'] != 0 or
                result['peak_pool_bytes'] > result['gpu_pool_limit_bytes']):
            raise ValueError('Streaming CUDA validation failed')
        timings.append(float(elapsed_ms))
        peaks.append(int(result['peak_pool_bytes']))
        live.append(int(result['live_pool_bytes_after_last_evict']))
    median = statistics.median(timings)
    return {
        'sample_count': len(samples),
        'total_wall_median_ms': round(median, 3),
        'total_wall_min_ms': round(min(timings), 3),
        'total_wall_max_ms': round(max(timings), 3),
        'layers_per_second': round(expected_layers * 1000.0 / median, 3),
        'private_pool_peak_reserved_bytes': max(peaks),
        'live_pool_bytes_after_each_run': live,
        'wall_time_variability_warning': max(timings) >= 2 * min(timings),
    }


def run_profile(config: ProfileConfig):
    # A runtime error here is a genuine GPU failure, never silently a CPU success.
    from cuda_packed import _cupy
    from layer_stream import CUDALayerStream, LayerStep
    cp = _cupy()
    with TemporaryDirectory(prefix='littlebit-stream-profile-') as temp:
        filename = Path(temp) / 'SYNTHETIC_STREAM_PROFILE.safetensors'
        t_build = perf_counter()
        steps, checkpoint_file_bytes = build_checkpoint(filename, config)
        build_ms = (perf_counter() - t_build) * 1000
        stream_steps = [LayerStep(s.checkpoint, s.prefix) for s in steps]
        x = np.sin(np.arange(config.width, dtype='<f4') * np.float32(.013)).tolist()
        runner_args = {'pool_limit_bytes': config.pool_limit_mib * 1024 * 1024, 'backend': 'warp'}
        free_before, total_before = cp.cuda.runtime.memGetInfo()
        # Warm up CUDA kernel compilation/context. Warmup is excluded from timings.
        warm = CUDALayerStream(**runner_args).run(stream_steps, x)
        if warm['eviction_count'] != config.layers or warm['live_pool_bytes_after_last_evict'] != 0:
            raise ValueError('CUDA warmup did not evict every layer')
        samples = []
        previous_output = None
        for _ in range(config.repeats):
            runner = CUDALayerStream(**runner_args)
            t0 = perf_counter()
            result = runner.run(stream_steps, x)
            elapsed = (perf_counter() - t0) * 1000
            output = result['output']
            if len(output) != config.width or any(not math.isfinite(v) for v in output):
                raise ValueError('CUDA output shape/finite check failed')
            if previous_output is not None:
                if any(abs(v - w) > 1e-4 * (1 + abs(w)) for v, w in zip(output, previous_output)):
                    raise ValueError('CUDA results differ between identical runs')
            previous_output = output
            samples.append((elapsed, result))
        free_after, total_after = cp.cuda.runtime.memGetInfo()
        if total_before != total_after:
            raise ValueError('CUDA device changed during profiling')
        name = cp.cuda.runtime.getDeviceProperties(cp.cuda.runtime.getDevice())['name']
        if isinstance(name, bytes):
            name = name.decode('utf-8', errors='replace')
        return {
            'status': 'LITTLEBIT_CUDA_STREAM_PROFILE_OK',
            'checkpoint': 'SYNTHETIC_ONLY',
            'trained_model': False,
            'whole_llm': False,
            'cuda_executed': True,
            'device': str(name),
            'width': config.width,
            'rank': config.rank,
            'layers': config.layers,
            'backend': 'warp',
            'checkpoint_file_bytes': checkpoint_file_bytes,
            'packed_binary_payload_bytes': config.packed_bytes,
            'checkpoint_build_ms_excluded_from_timing': round(build_ms, 3),
            'private_pool_limit_bytes': config.pool_limit_mib * 1024 * 1024,
            'device_free_before_bytes_snapshot': int(free_before),
            'device_free_after_bytes_snapshot': int(free_after),
            'vram_peak_measured': False,
            'timing_scope': 'Disk layer reads + CPU-to-GPU copies + CUDA calls + GPU-to-CPU copies + eviction',
            **summarize_samples(samples, expected_layers=config.layers),
        }


def main():
    parser = argparse.ArgumentParser(description='Bounded synthetic LittleBit CUDA layer-stream profile')
    parser.add_argument('--width', type=int, default=1024)
    parser.add_argument('--rank', type=int, default=128)
    parser.add_argument('--layers', type=int, default=8)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--pool-limit-mib', type=int, default=32)
    parser.add_argument('--max-file-mib', type=int, default=16)
    args = parser.parse_args()
    try:
        config = ProfileConfig(width=args.width, rank=args.rank, layers=args.layers,
                               repeats=args.repeats, pool_limit_mib=args.pool_limit_mib,
                               max_file_mib=args.max_file_mib)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(run_profile(config), indent=2))


if __name__ == '__main__':
    main()
