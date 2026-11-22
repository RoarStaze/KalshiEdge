# Cached LittleBit CUDA layer streaming (experimental)

**Goal:** Reduce Python overhead after successful RTX 4050 streaming tests by caching Safetensors metadata within one streaming run and eliminating explicit per-layer garbage collection. Benchmark against the existing, verified CPU-round-trip streamer.

This is NOT a full language model, a trained Gemma checkpoint, or a total-VRAM measurement.

## What changed

- Existing CUDALayerStream remains unchanged and available as the verified reference.
- CachedLayerReader parses each checkpoint header once per run. For sharded checkpoints it reuses the manifest and shard metadata for previously opened shards. Tensor bytes still must be read separately for each layer. File size and modification time are checked before metadata reuse; these are best-effort checks, not cryptographic integrity guarantees.
- LeanCUDALayerStream caches metadata and omits forced per-layer gc.collect calls, but still synchronizes CUDA and releases each layer's packed-factor GPU allocations. Outstanding allocations or exceeding private pool limits cause an error.
- The paired benchmark runs both paths using the same deterministic synthetic Safetensors checkpoint, alternating execution order, verifying numeric parity and checking evictions.
- The only change to the existing Safetensors layer reader is a private optional _shard_group argument; its public from_file(path, prefix) usage is unchanged.

## Run with your existing CUDA environment — PowerShell

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"

& $py experiments\littlebit70b\cached_stream.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Then compare the same larger synthetic workload already measured:

~~~powershell
& $py experiments\littlebit70b\cached_stream.py --width 4096 --rank 256 --layers 12 --repeats 3 --pool-limit-mib 32 --max-file-mib 16
~~~

Expected output status: LITTLEBIT_CACHED_STREAM_COMPARISON_OK, cuda_executed true, checkpoint SYNTHETIC_ONLY, trained_model false, whole_llm false, live_pool_bytes_after_run 0, metadata_index_loads 1 on the one-file synthetic checkpoint.

The experiment suite should now contain **84 tests**: 71 previously verified plus 13 new ones, including 3 new GPU-dependent tests. No CUDA tests should skip on the RTX 4050.

## Interpret the report

- baseline_median_ms: median complete wall-clock streaming time (existing CPU-round-trip streamer).
- cached_median_ms: median complete wall-clock time for cached metadata and no forced per-layer garbage collection.
- speedup_baseline_over_cached: above 1 means the experimental path was faster in this paired test; below 1 means slower.
- cached_pool_peak_bytes: private CuPy pool peak only. vram_peak_measured is always false.
- timing_variability_warning: true if either path varies by 2x or more.
- metadata_index_loads: count of checkpoint metadata groups parsed per experimental run, not lazily loaded individual shard headers.

## Limitations

Changing both metadata reuse and per-layer garbage collection at once does not isolate which change caused any measured speedup. Tensor bytes are still read individually from the file. Peak total VRAM (CUDA driver, context, other pools, library allocations, future KV cache) is not measured or bounded. The cache trusts basic file size/time change detection, which is not a tamper-proof integrity check.

This is synthetic packed linear algebra. There is no trained 27B LittleBit checkpoint, tokenizer, attention or Gemma 3 text-generation runtime here. A complete model's memory and latency remain unverified. Only promote the new backend after actual GPU parity, eviction and paired measurements support it.
