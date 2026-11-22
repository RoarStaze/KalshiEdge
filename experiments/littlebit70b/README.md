# LittleBit 70B feasibility lab

Isolated research prototype. **Not a working 70B inference engine.** No trading code or credentials are used.

Hardware target: Windows 11, RTX 4050 Laptop GPU (6 GB VRAM), 16 GB system RAM.

## First executable engineering milestone: packed LittleBit factor inference

A **dependency-free CPU reference layer** now computes directly from Samsung-compatible row-major packed binary factors, without expanding dense binary matrices. This uses **synthetic weights only** and is **not** a trained Gemma 3 27B model or an optimized inference engine. See [PACKED_ENGINE.md](PACKED_ENGINE.md) for the math, tests, and limitations.

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
python experiments\littlebit70b\packed_linear.py
python -m unittest discover -s experiments\littlebit70b\tests -v
```

The model target remains **Gemma 3 27B + LittleBit-2, free-only, no Qwen**. These commands do not install dependencies or download weights.

## Gate before downloading weights

A checkpoint must be a *trained, packed* 70B LittleBit-compatible model, not an untrained projection or a filename claiming 0.1 BPW. Its model card must document architecture, quantized tensor coverage, effective bits per parameter, evaluation results, and the exact runtime/kernel.

Use the zero-dependency preflight:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
python experiments/littlebit70b/preflight.py --parameters 70000000000 --effective-bpw 0.23 --overhead-gib 2
python -m unittest discover -s experiments/littlebit70b/tests -v
```

The default 0.23 BPW is an **illustrative scenario**, not a verified LittleBit 70B compression ratio. Change it only with checkpoint evidence.

## Acceptance criteria

1. A legitimate pretrained 70B compressed checkpoint is publicly accessible and legally redistributable.
2. A loader and kernel actually run its compressed representation without materializing the full BF16 model.
3. Measured peak VRAM <= 6 GB and RAM <= 16 GB on the target laptop.
4. Generated text passes basic coherence and repeatability checks.
5. Coding benchmark quality is compared with a smaller local baseline.

No automatic downloads or model execution occur here. A failed memory estimate must never be represented as a working deployment.

## Verified LittleBit-only checkpoint search (2026-11-22)

**Scope restriction:** This project is **Samsung LittleBit/LittleBit-2 only**. Do not substitute GGUF, GPTQ, AWQ, or unrelated quantizers and describe them as LittleBit.

- **70B:** No trained, publicly verifiable checkpoint found in the official Samsung repo, Hugging Face's `littlebit` tagged listings, or searches. Absence from search is not proof that no private model exists.
- **32B:** Supported in research, but no ready-to-run public LittleBit checkpoint verified.
- **27B:** [Haverbex/Qwen3.8-27B-LittleBit-0.1bit](https://huggingface.co/Haverbex/Qwen3.8-27B-LittleBit-0.1bit) is an independent experimental conversion, **not a trained, quality-preserving 0.1-BPW whole-model release**. Model card: 21.57 GB stored weights, 192 MLP projections at target 0.1 BPW, other weights BF16, 0 QAT/recovery steps, severe benchmark degradation, and no packed inference kernel. Its bundled loader expands factors. **Reject for 6GB VRAM / 16GB RAM without additional verified streaming implementation and quality recovery**. Do not auto-download.
- **8B:** [LittleBitLLM/littlebit-qwen3-8b-0.55bpw](https://huggingface.co/LittleBitLLM/littlebit-qwen3-8b-0.55bpw) is an early checkpoint and below the user's target model size.

Official source: https://github.com/SamsungLabs/LittleBit . The official README documents quantization-aware training and evaluation, not a prebuilt 70B local deployment package.

**Release gate:** Never advertise this experiment as a local 27B/70B inference application until real hardware inference, speed, memory, quality, and checkpoint provenance have been demonstrated. Running `discover.py` alone is insufficient.

## Check for a usable 27B–70B LittleBit release

**LittleBit-only**: See [LARGE_MODELS.md](LARGE_MODELS.md) for source-grounded eligibility criteria and the latest documented limitations. Run the metadata scout (no weights downloaded):

```powershell
python experiments/littlebit70b/large_check.py
```

This search does not install, compress, or execute a model. No large model is currently verified runnable on the target hardware.

## Optional CUDA packed-factor experiment

See [CUDA_ENGINE.md](CUDA_ENGINE.md) for the isolated Windows environment, CuPy CUDA 12 installation, and CPU-vs-GPU validation instructions. The GPU demonstration uses only tiny **synthetic** packed binary factors, not a trained Gemma or Llama checkpoint.

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\cuda_packed.py
~~~

Install CuPy into the isolated environment first using the instructions above. The GPU code is not yet validated on the target RTX 4050, and this milestone does not overcome the separate 27B checkpoint and QAT compute blockers.

## Experimental warp kernel benchmark

After the verified RTX 4050 packed-factor CUDA demo, an independent **warp-per-row** CUDA candidate and bounded GPU-event microbenchmark are provided in [WARP_BENCHMARK.md](WARP_BENCHMARK.md). The existing backend remains the default until hardware parity and benchmark measurements justify promotion.

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\cuda_warp.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
```

These only execute synthetic packed-matrix operations, not a trained Gemma model.


## Samsung-packed Safetensors layer loading (experimental)

The [CHECKPOINT_STREAMING.md](CHECKPOINT_STREAMING.md) milestone adds a bounded, per-layer reader for genuine Samsung LittleBit packed factor keys, including BF16 scaling vectors and sharded model.safetensors.index.json manifests. It **does not** download or run a full Gemma 3 27B model.

Use the already-installed isolated CUDA environment:

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\safetensors_layer.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Optional repeated GPU timings:

~~~powershell
& $py experiments\littlebit70b\repeat_benchmark.py
~~~

The checkpoint demo is synthetic only; the new checkpoint-to-GPU path needs RTX 4050 parity validation before any real checkpoint integration.

## Bounded multi-layer CUDA streaming (experimental)

The [LAYER_STREAMING.md](LAYER_STREAMING.md) milestone loads a **two-layer synthetic LittleBit checkpoint**, runs the first packed layer on the GPU, evicts its allocations, then runs the next layer with the CPU-transferred output. A dedicated CuPy memory pool enforces a configurable **pool allocation limit**. This is not a trained 27B LLM and does not bound total VRAM used by the CUDA driver or other libraries.

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\layer_stream.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
```

The RTX 4050 GPU validation is pending; 11 new tests are added, and the expected complete experiment suite has 56 tests. The demo uses synthetic weights and downloads nothing.


## Bounded multi-layer latency and memory profile

The [STREAM_PROFILE.md](STREAM_PROFILE.md) milestone profiles 8 or more **synthetic**, Samsung-keyed packed linear layers using the RTX 4050 CUDA warp backend. It measures median whole-chain latency and private-pool high-water allocation. Device memory readings before and after are snapshots **not VRAM peak**.

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\stream_profile.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Expected suite size is 65 tests (56 previous + 9 profiler tests). No model files are downloaded. This is not end-to-end Gemma generation or evidence of 27B feasibility.

## GPU-resident intermediate activations (experimental)

The [RESIDENT_ACTIVATIONS.md](RESIDENT_ACTIVATIONS.md) milestone adds a **separate** bounded CUDA path that retains intermediate activation vectors on GPU while evicting each layer's packed weights. Its paired benchmark compares against the existing CPU-round-trip streamer using identical synthetic checkpoints and alternating execution order. The old path remains unchanged. Neither path executes a complete Gemma 3 model.

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\resident_stream.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
```

The experiment suite is expected to contain **71 tests**; the new CUDA tests must run (not skip) on your RTX 4050. The GPU resident path is experimental and is not automatically promoted based on earlier benchmarks.


## Cached Safetensors metadata streaming (experimental)

See [CACHED_STREAM.md](CACHED_STREAM.md). A separate CUDA streaming path reuses Safetensors metadata and skips forced garbage collection per layer. Its paired benchmark compares against the unchanged CPU-round-trip streamer on the same synthetic checkpoint, checking numerical parity and eviction.

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\cached_stream.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Expected total: 84 tests, including three new GPU tests. This new path is not GPU-verified until you run it on your RTX 4050. The demo downloads no pretrained weights.


## Gemma 3 27B architecture and checkpoint-key audit

See [GEMMA3_COMPATIBILITY.md](GEMMA3_COMPATIBILITY.md). A new dependency-free static inspector uses the actual Gemma 3 27B model dimensions, seven projection paths per decoder layer and the real Hugging Face Safetensors key prefix. It computes idealized LittleBit text-only memory scenarios and safely differentiates ordinary BF16 checkpoints from checkpoints with Samsung LittleBit packed factor keys. This is not a model runner or a model-quality claim.

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\gemma3_bridge.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

11 new CPU-only audit tests were added, bringing the expected complete experiment suite to **95 tests**. No large weights or datasets are downloaded.


## Tiny Gemma 3 decoder with packed LittleBit projections

[GEMMA3_DECODER_REFERENCE.md](GEMMA3_DECODER_REFERENCE.md) describes the new NumPy CPU numerical reference for one full tiny Gemma 3 text decoder block. It includes four RMSNorm stages, Q/K normalization, local/global split-half RoPE, causal grouped-query attention, bounded sliding/global KV cache behavior, and the seven existing packed LittleBit linear operators for Q/K/V/O and the gated MLP.

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\gemma3_decoder.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

**The decoder demo is CPU-only and uses untrained synthetic packed factors.** The expected LittleBit experiment suite contains **106 tests** (95 prior plus 11 new decoder tests). No PyTorch installation is needed to run these. The demo is not a Gemma 3 27B model or a text generator, and no new weights are downloaded.


## Tiny Gemma 3 decoder executed on packed CUDA (experimental)

See [GEMMA3_CUDA_DECODER.md](GEMMA3_CUDA_DECODER.md). This connects the existing **seven packed LittleBit CUDA warp projections** with CuPy RMSNorm, RoPE, grouped-query attention, causal sliding/global KV caching, gated MLP and residuals. Unlike the previous CPU-only decoder reference, this implementation aims to execute an entire **tiny** block on CUDA.

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\gemma3_cuda_decoder.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

**Expected test count: 115** (106 previously passed + 9 new; six new CUDA-dependent). The hardware GPU path requires the RTX 4050 run; local CPU-side arithmetic checks passed. All demo factors are synthetic, with no real Gemma checkpoint or text generation.


## Six-block synthetic Gemma 3 token generation pipeline

See [GEMMA3_TINY_TOKEN_LM.md](GEMMA3_TINY_TOKEN_LM.md) for a tiny *token-ID only* pipeline with six decoder blocks, a 5-local/1-global attention pattern, scaled and tied input/output embeddings, final RMSNorm, greedy token scheduling and per-layer K/V caches. The CPU backend passed local tests. The RTX 4050 CUDA backend must still be verified on your laptop.

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\gemma3_tiny_lm.py --backend cuda
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

The expected suite count is **129 tests** (115 existing + 14 new). This is not trained Gemma 3 27B, real language generation, or a checkpoint conversion. No new packages or model weights are downloaded.


## Gemma 3 model checkpoint-backed block streaming (experimental)

See [GEMMA3_CHECKPOINT_STREAM.md](GEMMA3_CHECKPOINT_STREAM.md). Unlike the previous six-block in-memory synthetic token pipeline, this milestone reads actual Hugging Face Gemma 3 tensor paths and Samsung LittleBit packed-factor fields from a Safetensors file or sharded manifest, loads **one complete packed decoder block at a time** onto CUDA, keeps per-layer K/V states in a separate GPU pool, and releases the GPU decoder weight allocations after each block. Dense .weight projection files are rejected; they are **not** trained LittleBit checkpoints.

Run the synthetic demo and complete experiment tests in the already installed isolated environment:

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\gemma3_checkpoint_stream.py --backend cuda
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Expected total: **144 tests** (129 previously passed, plus 15 new). The CUDA streaming path is **not** verified until the three new CUDA-dependent tests execute on the RTX 4050. This is still a small synthetic model with no pretrained weights, no tokenizer and no real Gemma 3 27B inference.


## Full Gemma 3 27B checkpoint preflight and resource planning

[GEMMA3_27B_PREFLIGHT.md](GEMMA3_27B_PREFLIGHT.md) documents a new **62-layer / 434-projection** Samsung LittleBit-2 checkpoint format validator and planning-only CPU/GPU budget checker. Unlike the small checkpoint generation demo, the inspector accepts the published full 27B tensor dimensions without allocating dense 27B weights.

A separate one-block-at-a-time reader can load packed factors into CPU memory or, in a context manager, into a **private capped CuPy weight pool** with cleanup checks. It does not run a full 62-layer decoder or certify trained weights. The default resource plan assumes the known **6 GiB VRAM / 16 GiB RAM** hardware and 4096-token context; all memory figures are planning reservations, not measured peaks.

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\gemma3_27b_ready.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Expected full suite: **160 tests** (144 prior + 16 new, two CUDA-dependent). No new weights are downloaded. The output deliberately reports runnable_model = false because genuine trained Gemma 3 27B LittleBit-2 weights, full-scale model execution and tokenizer integration remain unresolved.
