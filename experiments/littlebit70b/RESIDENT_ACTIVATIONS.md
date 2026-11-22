# Experimental GPU-resident LittleBit activations

**Goal:** Compare the verified CPU-round-trip LittleBit streaming prototype with a second option that keeps intermediate activation vectors on the RTX 4050 between packed linear layers.

This is still **synthetic packed linear algebra**: no pretrained weights, no language generation, no Gemma inference, no Qwen, no paid cloud service.

## What changes

- The existing CUDALayerStream baseline is preserved and remains available.
- GPUResidentActivationStream uses **two private capped CuPy memory pools**, one for packed layer factors/scratch arrays and one for activation vectors.
- Each layer is loaded from Safetensors, run on GPU, then evicted. An activation is copied GPU-to-GPU into the separate activation pool to survive the next layer. Intermediate activations **do not round-trip to system RAM**.
- Both private pools must have zero live allocations when the run ends.
- The paired benchmark uses the same deterministic synthetic checkpoint and inputs. Each mode is warmed up, numerical output parity is checked, order alternates by repetition, and median whole-chain wall times are compared. **No performance benefit is assumed.**

The two private pool caps do **not** limit the entire CUDA process's VRAM; CUDA contexts, NVRTC, other allocators, and other processes are excluded. See [CuPy MemoryPool](https://docs.cupy.dev/en/stable/reference/generated/cupy.cuda.MemoryPool.html) and [using_allocator](https://docs.cupy.dev/en/stable/reference/generated/cupy.cuda.using_allocator.html).

## Run on your laptop — PowerShell

No new installs; reuse the isolated environment previously tested on RTX 4050.

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"

& $py experiments\littlebit70b\resident_stream.py

& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Expected profile status: LITTLEBIT_GPU_RESIDENT_COMPARISON_OK with cuda_executed: true, checkpoint: SYNTHETIC_ONLY, trained_model: false and whole_llm: false.

The full experiment suite should have **71 tests** (previously verified 65 + 6 new tests, two of them requiring CUDA). On the RTX 4050 none of the CUDA tests should skip.

To compare the same larger synthetic dimensions as your most recent profile:

~~~powershell
& $py experiments\littlebit70b\resident_stream.py --width 4096 --rank 256 --layers 12 --repeats 3 --pool-limit-mib 32 --activation-pool-mib 4 --max-file-mib 16
~~~

## Key result fields

- median_cpu_roundtrip_ms: whole-chain median for the original GPU layer streamer, including disk reads, CPU-GPU activation transfers and cleanup.
- median_gpu_resident_ms: whole-chain median for the new CUDA-resident activation option, including disk reads, GPU copies, kernel calls and cleanup.
- speedup_cpu_roundtrip_over_gpu_resident: ratio >1 means the experimental resident path ran faster for that benchmark; <1 means slower.
- timing_variability_warning: true if any method varied by 2x or more across repeats.
- weight_pool_peak_reserved_bytes, activation_pool_peak_reserved_bytes: pool-specific peaks, not whole-device peak.
- private_weight_and_activation_live_after_run: must be zero.
- intermediate_cpu_activation_copies: zero. The initial input is transferred to GPU and the final output to CPU.
- vram_peak_measured: false.

**Baseline from previous RTX 4050 profiles:** 8×(1024,128) synthetic layers ran in a 67.58ms whole-chain median and 12×(4096,256) ran in 99.43ms. These were different prior runs, not a controlled comparison. Use the paired benchmark above rather than comparing them directly.

## Limitations

The resident implementation performs an extra GPU-to-GPU activation copy per layer, so it might not be faster. The loader still reparses Safetensors metadata and Python garbage collection and synchronization occur each layer. The two memory pools add allocations; a 6GB GPU's whole-process peak VRAM remains unmeasured.

Full Gemma 3 27B inference additionally requires a trained LittleBit checkpoint, attention, embeddings, tokenizer, normalization, KV cache, model-wide memory planning and quality evaluation. This does not provide those or prove 27B fits.
