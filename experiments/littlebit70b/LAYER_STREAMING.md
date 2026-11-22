# Bounded two-layer LittleBit CUDA streaming

**Purpose:** Validate a realistic *memory-management mechanism* for LittleBit/LittleBit-2 linear layers: load packed Safetensors factors for one layer, execute them on the GPU, transfer the output to CPU, evict GPU allocations, and then load a second dimension-compatible layer.

**This is NOT a Gemma transformer or a conversational model.** Both demo layers have synthetic/untrained weights. They have dimensions 5 -> 2 and 2 -> 3. No real checkpoint is downloaded.

## Windows PowerShell: exact commands

Use the existing CuPy CUDA 12 environment, not KalshiEdge's trading Python:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"

& $py experiments\littlebit70b\layer_stream.py

& $py -m unittest discover -s experiments\littlebit70b\tests -v
```

Expected demonstration fields on the RTX 4050:

- `status: LITTLEBIT_TWO_LAYER_STREAM_OK`
- `checkpoint: SYNTHETIC_ONLY`
- `cuda_executed: true`
- `layer_count: 2`, `eviction_count: 2`
- `gpu_pool_limit_bytes: 33554432` (32 MiB)
- `peak_pool_bytes` is nonzero and no greater than the private pool allocation limit
- `live_pool_bytes_after_last_evict: 0`
- `max_abs_error` within the reference tolerance

**Expected experiment suite:** 56 tests total (45 previously verified, plus 11 new streaming tests), including 3 new CUDA-only tests. A skipped GPU test on your RTX 4050 is **not** a successful GPU streaming validation.

## Memory accounting: exact scope

This implementation uses `cupy.cuda.MemoryPool()` and its `set_limit(size=...)` method. Each CUDA load+forward runs inside `cupy.cuda.using_allocator(private_pool.malloc)`, followed by GPU synchronization, release of the GPU layer/output references and `private_pool.free_all_blocks()`. The output is deliberately moved to CPU between steps.

The pool cap covers **only allocations through this private CuPy memory pool**. It is not a whole-process 32 MiB VRAM limit. It excludes NVRTC/CUDA context and driver memory, kernel cache allocations, other GPU pools, any future attention/KV cache, and potentially other library allocations. CPU memory usage for file reads, Python and NumPy arrays is separately bounded by the existing 64 MiB per-layer selected-tensor cap; the cap is not a process RSS maximum.

- `peak_pool_bytes`: maximum pool-reserved bytes observed while each layer's GPU result is still live.
- `live_pool_bytes_after_last_evict`: live CuPy allocations in that pool after the layer reference is released; must equal zero.
- `retained_pool_bytes_after_last_evict`: cached/split free blocks the pool might retain even after `free_all_blocks`. This is *not* the same as live allocation bytes.
- A deliberately tiny GPU pool allocation limit must raise `MemoryBudgetError`, not bypass the cap.

Sources: [CuPy MemoryPool](https://docs.cupy.dev/en/stable/reference/generated/cupy.cuda.MemoryPool.html) and [thread-local using_allocator](https://docs.cupy.dev/en/stable/reference/generated/cupy.cuda.using_allocator.html).

## Explicit limitations

The two modules in the demo are merely a linear-layer sequence. Actual Gemma 3 requires embeddings, normalization, attention, positional encodings, nonlinear MLP operations, a tokenizer, KV cache, scheduling, and validated pretrained quantized weights. Keeping only one layer on GPU saves resident layer weights but introduces disk/CPU transfer overhead. No 27B model was trained, loaded or benchmarked, and feasibility of a 27B trained LittleBit model on this laptop remains **unverified**.

Next engineering priority after RTX 4050 verification: an explicit memory/latency profile for repeated streaming across representative synthetic layer sizes, before making any claims about full-model inference.