# LittleBit CUDA streaming profiler — bounded synthetic layers

**Goal:** Measure end-to-end wall-clock latency for reading multiple Samsung-compatible **packed synthetic linear layers** from a temporary Safetensors checkpoint, running them through the existing warp-parallel CUDA backend, and evicting each layer before the next.

This does **not** generate model text, train Gemma 3, or demonstrate that 27B will fit in 6GB VRAM. The weights are random, untrained binary factors, never downloaded from a model hub.

## Run on the RTX 4050 using the existing environment

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"

& $py experiments\littlebit70b\stream_profile.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Default profile: 8 synthetic linear layers, each width 1,024, latent rank 128, 3 timed repetitions, a 32 MiB private CuPy pool cap, and a 16 MiB checkpoint-file cap. One CUDA warmup run excludes compilation overhead from measurements. The GPU must actually execute; no CPU fallback claims success.

For a larger but bounded profile:

~~~powershell
& $py experiments\littlebit70b\stream_profile.py --width 4096 --rank 256 --layers 12 --repeats 3 --pool-limit-mib 32 --max-file-mib 16
~~~

The profiler refuses excessive width/rank/layer/repeat counts or estimated checkpoint size before creating weights. The Safetensors file is temporary and deleted automatically.

## Report meanings

- status LITTLEBIT_CUDA_STREAM_PROFILE_OK indicates actual CUDA execution.
- checkpoint SYNTHETIC_ONLY; trained_model false; whole_llm false.
- checkpoint_file_bytes is the synthetic file size; packed_binary_payload_bytes excludes scales, metadata, and unquantized tensors.
- total_wall_median_ms is the median wall-clock duration of a **full multi-layer streamed pass** (disk reads, CPU-GPU transfers, kernels, CPU output transfer, synchronizations and memory cleanup). Checkpoint creation and the warmup pass are excluded.
- layers_per_second counts synthetic **linear layers**, NOT generated tokens.
- private_pool_peak_reserved_bytes is the maximum allocated/reserved amount measured by CUDALayerStream within its *private* CuPy memory pool.
- live_pool_bytes_after_each_run must be all zeros; this proves no live allocations remain in **that pool**, not all GPU memory.
- device_free_before_bytes_snapshot and device_free_after_bytes_snapshot are NVML/CUDA device free-memory snapshots. They are NOT instantaneous or peak whole-process VRAM measurements. The field vram_peak_measured is always false.
- wall_time_variability_warning indicates a 2x or larger observed timing spread.

Because the implementation transfers each layer's output back to CPU and calls Python garbage collection for every layer, speed is **not** a prediction for a production transformer. End-to-end streaming is likely slower than keeping all layers resident on GPU.

## Status and next milestone

The last RTX 4050 run validated two tiny packed layers, 2 evictions, and all **56** existing experiment tests with no skipped tests. This new profiler adds 9 more tests (one GPU-only). **65 total tests** are expected once this change is pulled.

Only local CPU/unit validation has been performed on the new profiler; RTX 4050 performance and actual GPU memory behavior require the commands above.

After a successful profile, use measured latency/memory to evaluate layer caching and prefetch before implementing additional attention, embeddings and tokenization components. A quality-preserving trained 27B LittleBit checkpoint remains unavailable under the user's free-only constraints.

Samsung method: https://github.com/SamsungLabs/LittleBit
