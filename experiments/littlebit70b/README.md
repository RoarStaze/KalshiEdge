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
