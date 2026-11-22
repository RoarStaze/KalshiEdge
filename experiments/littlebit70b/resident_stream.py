"""Experimental LittleBit packed-layer streaming with GPU-resident activations.

GPU weights are loaded into one capped CuPy pool and evicted after each
layer. A second capped pool retains the activation vector between layers.
The private pool limits do not cap CUDA context, NVRTC, or whole-process VRAM.
No trained checkpoint, transformer, tokenizer, or online model fetch.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
from pathlib import Path
import statistics
from tempfile import TemporaryDirectory
from time import perf_counter


def validate_caps(weight_pool_bytes: int, activation_pool_bytes: int):
    for name, value in (('weight_pool_bytes', weight_pool_bytes),
                        ('activation_pool_bytes', activation_pool_bytes)):
        if type(value) is not int or value <= 0:
            raise ValueError(f'{name} must be a positive integer')


def summarize_pairs(pairs):
    """Median end-to-end times (ms); never interprets layers as generated tokens."""
    if not pairs:
        raise ValueError('At least one paired sample required')
    baseline, resident = [], []
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError('Each sample must contain exactly two timings')
        b, r = pair
        if (type(b) not in (int, float) or type(r) not in (int, float)
                or not math.isfinite(b) or not math.isfinite(r) or b <= 0 or r <= 0):
            raise ValueError('Timings must be positive, finite numbers')
        baseline.append(float(b))
        resident.append(float(r))
    med_b, med_r = statistics.median(baseline), statistics.median(resident)
    return {
        'sample_count': len(pairs),
        'median_cpu_roundtrip_ms': round(med_b, 3),
        'median_gpu_resident_ms': round(med_r, 3),
        'speedup_cpu_roundtrip_over_gpu_resident': round(med_b / med_r, 3),
        'baseline_range_ms': [round(min(baseline), 3), round(max(baseline), 3)],
        'gpu_resident_range_ms': [round(min(resident), 3), round(max(resident), 3)],
        'timing_variability_warning': (max(baseline) >= 2 * min(baseline)
                                       or max(resident) >= 2 * min(resident)),
    }


class GPUResidentActivationStream:
    """Correctness-first CUDA resident-activation implementation.

    Keeps only a float32 activation between layers. Each layer's packed
    factors and scratch arrays live in a separate bounded pool and must
    be evicted before the next. One GPU-to-GPU copy per layer currently
    isolates activation lifetime from packed weight allocations.
    """

    def __init__(self, *, weight_pool_bytes=32 * 1024 * 1024,
                 activation_pool_bytes=4 * 1024 * 1024,
                 max_layer_bytes=64 * 1024 * 1024, backend='warp'):
        validate_caps(weight_pool_bytes, activation_pool_bytes)
        if type(max_layer_bytes) is not int or max_layer_bytes <= 0:
            raise ValueError('max_layer_bytes must be a positive integer')
        if backend not in ('warp', 'scalar'):
            raise ValueError('backend must be warp or scalar')
        self.weight_pool_bytes = weight_pool_bytes
        self.activation_pool_bytes = activation_pool_bytes
        self.max_layer_bytes = max_layer_bytes
        self.backend = backend

    def run(self, steps, vector):
        from cuda_packed import _cupy
        from layer_stream import LayerStep
        from safetensors_layer import SafetensorsLittleBitLayer
        if not steps or any(not isinstance(step, LayerStep) for step in steps):
            raise ValueError('Expected nonempty LayerStep sequence')
        cp = _cupy()
        weight_pool = cp.cuda.MemoryPool()
        activation_pool = cp.cuda.MemoryPool()
        weight_pool.set_limit(size=self.weight_pool_bytes)
        activation_pool.set_limit(size=self.activation_pool_bytes)
        weight_peak = 0
        activation_peak = 0
        evictions = 0
        gpu_input = None
        next_input = None
        gpu_result = None
        gpu_layer = None
        loaded = None
        current = list(vector)
        try:
            with cp.cuda.using_allocator(activation_pool.malloc):
                gpu_input = cp.asarray(current, dtype=cp.float32)
            if gpu_input.ndim != 1:
                raise ValueError('Expected one-dimensional input')
            for index, step in enumerate(steps):
                loaded = SafetensorsLittleBitLayer.from_file(
                    step.checkpoint, step.prefix, max_layer_bytes=self.max_layer_bytes)
                if gpu_input.shape[0] != loaded.v_shape[1]:
                    raise ValueError(f'Layer {index} input dimension mismatch')
                try:
                    with cp.cuda.using_allocator(weight_pool.malloc):
                        gpu_layer = loaded.to_cuda_layer(backend=self.backend)
                        gpu_result = gpu_layer.forward(gpu_input)
                        weight_peak = max(weight_peak, int(weight_pool.total_bytes()))
                    # Separate the next activation from this layer's weight pool.
                    with cp.cuda.using_allocator(activation_pool.malloc):
                        next_input = cp.array(gpu_result, dtype=cp.float32, copy=True)
                        activation_peak = max(activation_peak, int(activation_pool.total_bytes()))
                    cp.cuda.get_current_stream().synchronize()
                    gpu_input = None
                    gpu_result = None
                    gpu_layer = None
                    loaded = None
                    gc.collect()
                    weight_pool.free_all_blocks()
                    if weight_pool.used_bytes() != 0:
                        raise RuntimeError('Weight allocations remain live after eviction')
                    gpu_input = next_input
                    next_input = None
                    activation_pool.free_all_blocks()
                    evictions += 1
                except cp.cuda.memory.OutOfMemoryError as exc:
                    raise MemoryError('Private CUDA pool allocation limit exceeded at layer '
                                      + str(index)) from exc
            result = gpu_input.get().tolist()  # Exactly one final GPU-to-CPU copy.
        finally:
            cp.cuda.get_current_stream().synchronize()
            next_input = None
            gpu_result = None
            gpu_layer = None
            gpu_input = None
            loaded = None
            gc.collect()
            weight_pool.free_all_blocks()
            activation_pool.free_all_blocks()
        if weight_pool.used_bytes() or activation_pool.used_bytes():
            raise RuntimeError('CUDA private-pool allocations still live at end')
        return {
            'status': 'LITTLEBIT_GPU_ACTIVATION_STREAM_OK',
            'output': result, 'layer_count': len(steps), 'eviction_count': evictions,
            'weight_pool_limit_bytes': self.weight_pool_bytes,
            'activation_pool_limit_bytes': self.activation_pool_bytes,
            'weight_pool_peak_reserved_bytes': weight_peak,
            'activation_pool_peak_reserved_bytes': activation_peak,
            'weight_pool_live_bytes_after_run': int(weight_pool.used_bytes()),
            'activation_pool_live_bytes_after_run': int(activation_pool.used_bytes()),
            'checkpoint': 'UNVERIFIED_LOCAL_CHECKPOINT',
            'trained_model': None, 'whole_llm': False,
            'cuda_executed': True,
            'intermediate_cpu_activation_copies': 0,
            'intermediate_gpu_activation_copies': len(steps),
            'backend': self.backend,
        }


def paired_profile(config, *, activation_pool_mib=4):
    """End-to-end comparison on the SAME synthetic file and input vector.

    Warmups excluded. Each pair alternates run order to reduce timing bias.
    Paired median is informative, not a demonstrated improvement until GPU run.
    """
    from cuda_packed import _cupy
    from layer_stream import LayerStep, CUDALayerStream
    from stream_profile import build_checkpoint
    import numpy as np
    if type(activation_pool_mib) is not int or not 1 <= activation_pool_mib <= 64:
        raise ValueError('activation_pool_mib must be 1–64')
    cp = _cupy()
    with TemporaryDirectory(prefix='littlebit-gpu-resident-') as tmp:
        file = Path(tmp) / 'SYNTHETIC_ONLY.safetensors'
        source_steps, file_bytes = build_checkpoint(file, config)
        steps = [LayerStep(s.checkpoint, s.prefix) for s in source_steps]
        x = np.sin(np.arange(config.width, dtype=np.float32) * np.float32(.013)).tolist()
        baseline_kwargs = dict(pool_limit_bytes=config.pool_limit_mib * 1024 * 1024, backend='warp')
        resident_kwargs = dict(weight_pool_bytes=config.pool_limit_mib * 1024 * 1024,
                               activation_pool_bytes=activation_pool_mib * 1024 * 1024,
                               backend='warp')
        # Compile and load both paths once before timing.
        baseline_expected = CUDALayerStream(**baseline_kwargs).run(steps, x)['output']
        resident_expected = GPUResidentActivationStream(**resident_kwargs).run(steps, x)['output']
        if len(baseline_expected) != len(resident_expected) or any(
                abs(a - b) > 1e-4 * (1 + abs(a))
                for a, b in zip(baseline_expected, resident_expected)):
            raise AssertionError('GPU-resident activation output differs from baseline')
        pairs = []
        final_resident = None
        for repetition in range(config.repeats):
            order = ('baseline', 'resident') if repetition % 2 == 0 else ('resident', 'baseline')
            times = {}
            for mode in order:
                runner = (CUDALayerStream(**baseline_kwargs) if mode == 'baseline'
                          else GPUResidentActivationStream(**resident_kwargs))
                start = perf_counter()
                report = runner.run(steps, x)
                times[mode] = (perf_counter() - start) * 1000
                expected = baseline_expected
                if len(report['output']) != len(expected) or any(
                        abs(a-b) > 1e-4 * (1 + abs(b))
                        for a, b in zip(report['output'], expected)):
                    raise AssertionError(mode + ' CUDA output mismatch')
                if report['eviction_count'] != config.layers:
                    raise AssertionError('Not all GPU layers were evicted')
                if mode == 'resident':
                    final_resident = report
            pairs.append((times['baseline'], times['resident']))
        name = cp.cuda.runtime.getDeviceProperties(cp.cuda.runtime.getDevice())['name']
        if isinstance(name, bytes):
            name = name.decode('utf-8', errors='replace')
        return {
            'status': 'LITTLEBIT_GPU_RESIDENT_COMPARISON_OK',
            'checkpoint': 'SYNTHETIC_ONLY', 'trained_model': False,
            'whole_llm': False, 'cuda_executed': True, 'device': str(name),
            'width': config.width, 'rank': config.rank, 'layers': config.layers,
            'checkpoint_file_bytes': file_bytes,
            'weight_pool_limit_bytes': resident_kwargs['weight_pool_bytes'],
            'activation_pool_limit_bytes': resident_kwargs['activation_pool_bytes'],
            'weight_pool_peak_reserved_bytes': final_resident['weight_pool_peak_reserved_bytes'],
            'activation_pool_peak_reserved_bytes': final_resident['activation_pool_peak_reserved_bytes'],
            'private_weight_and_activation_live_after_run':
                final_resident['weight_pool_live_bytes_after_run'] +
                final_resident['activation_pool_live_bytes_after_run'],
            'intermediate_cpu_activation_copies': 0,
            'vram_peak_measured': False,
            'timing_scope': 'File reads + copies + kernels + Python synchronization/cleanup',
            **summarize_pairs(pairs),
        }


def main():
    from stream_profile import ProfileConfig
    parser = argparse.ArgumentParser(description='Compare LittleBit synthetic layer streaming approaches')
    parser.add_argument('--width', type=int, default=1024)
    parser.add_argument('--rank', type=int, default=128)
    parser.add_argument('--layers', type=int, default=8)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--pool-limit-mib', type=int, default=32)
    parser.add_argument('--activation-pool-mib', type=int, default=4)
    parser.add_argument('--max-file-mib', type=int, default=16)
    args = parser.parse_args()
    cfg = ProfileConfig(width=args.width, rank=args.rank, layers=args.layers,
                        repeats=args.repeats, pool_limit_mib=args.pool_limit_mib,
                        max_file_mib=args.max_file_mib)
    report = paired_profile(cfg, activation_pool_mib=args.activation_pool_mib)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
