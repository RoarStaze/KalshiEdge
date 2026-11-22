# LittleBit CUDA packed-factor milestone

**Target:** Windows 11, RTX 4050 Laptop GPU (6 GB), 16 GB RAM.

**Model goal:** Free-only, non-Qwen Gemma 3 27B LittleBit-2. **This milestone is not that model.**

The file cuda_packed.py implements a correctness-first CUDA binary-factor matrix-vector kernel using CuPy RawKernel and Samsung-compatible packed signs. Each GPU thread calculates one output row, reading uint32 packed words and applying sign bits directly to a float32 input. Two such operations with scale vectors implement the synthetic LittleBit linear layer; binary factors remain packed in GPU memory across repeated calls. This is not optimized LLM inference.

## Run on your laptop in PowerShell

Keep GPU dependencies isolated from the KalshiEdge trading application.

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

python -m venv "$HOME\Desktop\LittleBit-CUDA-venv"
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install "cupy-cuda12x[ctk]"

& $py experiments\littlebit70b\cuda_packed.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

CuPy's official Windows/CUDA 12 installation guide: https://docs.cupy.dev/en/stable/install.html . You need compatible NVIDIA drivers. Avoid installing conflicting CuPy packages in the same environment.

## Acceptance checks

If CuPy can access the NVIDIA GPU and NVRTC compiles the kernel, the demonstration prints JSON with:

- status: CUDA_PACKED_FACTOR_DEMO_OK
- checkpoint: SYNTHETIC_ONLY
- trained_model: false
- cuda_kernel_executed: true
- max_abs_error: CPU vs GPU result comparison

If the CUDA runtime is absent, the demonstration exits with a nonzero code and CUDA_NOT_AVAILABLE. If NVRTC compilation fails, the program fails instead of pretending it ran. In a non-GPU environment four new tests skip; on your RTX 4050 all four must run and pass. A green suite with GPU tests skipped is not GPU verification.

## GPU parity test coverage

- Packed matvec parity for row widths 1, 7, 31, 32, 33, 63, 64, 65, 127.
- Signed bit-31 compatibility and padded word tails.
- Two-factor LittleBit layer with scaling, optional residual, and bias.
- Invalid vector length.
- CPU-safe import when CuPy is not installed.

## Explicit limitations

No trained Gemma checkpoint is included, created or downloaded. The GPU kernel's O(rows * columns) per-element work is a starting point, not a performance-optimized kernel. Full LLM memory usage, tokens per second, and quality are **unverified**. Training a 27B model using teacher/student QAT still requires much larger GPU resources than this laptop has.

Next after a passing GPU test: benchmark against the CPU version, improve CUDA warp/block mapping, and build a validated streaming checkpoint reader with strict memory limits.

Official Samsung method: https://github.com/SamsungLabs/LittleBit .
