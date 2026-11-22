"""Repeated CUDA packed-factor timing summary (synthetic workloads only).

Collects medians across several GPU-event microbenchmark passes to expose
instability. Never estimates Gemma tokens/sec or selects a production backend.
"""
import json
import math
import statistics

from cuda_warp import benchmark


def summarize_runs(runs):
    if not runs:
        raise ValueError('At least one benchmark run is required')
    scalar, warp, errors = [], [], []
    for run in runs:
        try:
            a = float(run['scalar_ms_per_call'])
            b = float(run['warp_ms_per_call'])
            error = float(run['max_abs_error'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Missing or invalid benchmark result') from exc
        if not all(math.isfinite(value) for value in (a, b, error)) or a <= 0 or b <= 0 or error < 0:
            raise ValueError('Benchmark timings must be positive finite numbers')
        scalar.append(a)
        warp.append(b)
        errors.append(error)
    smed, wmed = statistics.median(scalar), statistics.median(warp)
    warning = (max(scalar) >= 2.0 * min(scalar) or max(warp) >= 2.0 * min(warp))
    return {
        'sample_count': len(runs),
        'scalar_median_ms': round(smed, 6),
        'warp_median_ms': round(wmed, 6),
        'speedup': round(smed / wmed, 3),
        'scalar_range_ms': [min(scalar), max(scalar)],
        'warp_range_ms': [min(warp), max(warp)],
        'max_abs_error': max(errors),
        'timing_variability_warning': warning,
    }


def main():
    shapes = [(128, 256), (1024, 2048), (2048, 4096)]
    runs_by_size = {shape: [] for shape in shapes}
    # Reverse test order every other round to reduce persistent shape-order bias.
    for round_no in range(5):
        ordered = shapes if round_no % 2 == 0 else list(reversed(shapes))
        for rows, cols in ordered:
            runs_by_size[(rows, cols)].append(benchmark(rows, cols, 30))
    results = []
    for (rows, cols), runs in runs_by_size.items():
        results.append({'rows': rows, 'columns': cols,
                        'device': runs[0]['device'],
                        'status': 'CUDA_REPEATED_BENCHMARK_OK',
                        'checkpoint': 'SYNTHETIC_ONLY',
                        'trained_model': False,
                        **summarize_runs(runs)})
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
