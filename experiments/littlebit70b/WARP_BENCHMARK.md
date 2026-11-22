# Experimental warp-parallel LittleBit CUDA kernel

**This milestone is a synthetic packed-factor inference benchmark, not a trained Gemma 3 27B model.**

Your RTX 4050 successfully executed the **original** CUDA packed-factor kernel and passed 24/24 project tests. The experimental replacement in [cuda_warp.py](cuda_warp.py) is **not yet hardware-verified** and is deliberately kept separate.

## What is different

The verified CUDA kernel uses one CUDA thread per output row, each performing serial work over the entire input. The new experimental kernel maps **one warp (32 threads) to each output row**, distributes input columns across lanes and combines their partial sums using NVIDIA's __shfl_down_sync intrinsic. The original path remains unchanged.

Both kernels operate directly on Samsung-compatible packed uint32 binary factor words (0 = +1, 1 = -1). Neither expands the complete binary matrices into float32 or BF16. Float32 activations and scale vectors are still needed.

- GPU parity tests cover widths 1, 7, 31, 32, 33, 63, 64, 65, 127 and 257, plus partial output warps.
- Signed-high-bit and partial-word padding compatibility are verified.
- The full two-factor, scaled layer with residual factors and bias is compared against CPU math.
- Benchmark's packed binary matrix is limited to <=32 MiB by default and uses only deterministic synthetic data.

## PowerShell commands — no new packages

Use the **existing** isolated environment. Do not reinstall CuPy into KalshiEdge's production Python environment:

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"

& $py experiments\littlebit70b\cuda_warp.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

The benchmark runs three small-to-medium matrix shapes on the RTX 4050, printing a JSON array. Each contains measured scalar and warp GPU-event time per kernel call (milliseconds), a speedup ratio, parity error, shape, and GPU name.

**Acceptance:** For each shape: status must be CUDA_WARP_BENCHMARK_OK and max_abs_error within tolerance. The test suite now contains 30 tests; on the target laptop no CUDA-dependent tests should skip.

**Important:** The speedup is a **measured value**, not a promise. A ratio greater than 1 means warp kernel was faster for that matrix; less than 1 means slower. The timing excludes CPU preparation and transfers, and microbenchmark results may include host launch gaps. It does not measure full language-model token generation.

## Next engineering step

Review the GPU results on the actual RTX 4050. Promote the warp kernel only if it passes all parity checks and improves relevant representative matrix shapes. Then benchmark full two-factor-layer latency and memory.

The original verified backend remains the default until promotion. This milestone does not solve trained 27B model availability or expensive QAT.

Official references: [NVIDIA warp shuffle operations](https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/cpp-language-extensions.html), [CuPy RawKernel](https://docs.cupy.dev/en/stable/reference/generated/cupy.RawKernel.html).
