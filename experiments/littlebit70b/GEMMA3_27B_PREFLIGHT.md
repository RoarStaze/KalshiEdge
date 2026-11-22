# Gemma 3 27B LittleBit-2 full-size preflight and RAM/VRAM plan

**Scope:** Full-dimension checkpoint metadata validation and one-at-a-time packed decoder-block loading WITHOUT loading the whole model. Supports Gemma 3 27B's 62-layer text architecture and 434 projection modules. **This is not a trained 27B model, a tokenizer, or a runnable 27B chatbot.** The previously verified six-block experimental CUDA decoder remains unchanged.

## New components

The existing Gemma3CheckpointReader is intentionally limited to tiny decoder dimensions. This milestone adds gemma3_27b_ready.py, using the real Gemma3TextArchitecture from the reference model config instead.

- Full Gemma 3 token-embedding tensor shape, final RMSNorm, all six per-block norm tensors, and seven Samsung LittleBit packed projections per block, including optional residual factors/bias.
- Validates original U_shape/V_shape, packed sign words, scale shapes/dtypes, required keys and selected tensor-byte budgets for all 62 layers. This reads only tiny factor-shape metadata, **not** the full model factors.
- Detects and rejects ordinary dense projection weights, missing or malformed tensors, incomplete packed/residual factor sets, inconsistent ranks, excessive header metadata and unsafe sharded file references (via the existing Safetensors reader).
- Supports a local single-file Safetensors checkpoint or a sharded checkpoint directory with model.safetensors.index.json.
- Lazy loading of seven packed factors for just one requested decoder block into CPU memory, without expanding dense matrices.
- Context-managed CUDA conversion for just one decoder block, using a private capped CuPy MemoryPool, and clearing/checking the weight pool when the context exits. The private pool limit does not include all GPU driver/runtime allocations.

## Default 6 GiB GPU / 16 GiB RAM reservation plan at 4096 tokens

These are **planning reservations, not measured peak memory and not proof of 27B feasibility**.

| Component | Planned amount |
|---|---:|
| Tied BF16 text embeddings placed in system RAM | 2.626 GiB |
| FP32 raw K/V (52 local × 1024, 10 global × 4096) | 1.4375 GiB |
| Private GPU packed-weight pool cap | 1.0 GiB |
| Private GPU state (KV/activation) pool cap | 2.0 GiB |
| GPU workspace allowance | 0.5 GiB |
| CUDA context/driver/other reserve | 1.5 GiB |
| Total reserved GPU capacity | 5.0 GiB out of 6.0 GiB |
| Host staging/copy reservation | 2.0 GiB |
| Other host runtime allowance | 0.5 GiB |
| System/OS/other host reserve | 3.0 GiB |
| Total reserved system capacity | 8.126 GiB out of 16.0 GiB |

The 27B raw K/V estimate is FP32 because the current decoder's KV tensors are FP32; BF16 storage savings are not assumed. Actual local F32 embeddings would exceed the listed BF16 reservation and are checked separately if a checkpoint is provided.

The preflight also rejects blocks larger than the host staging cap or exceeding the heuristic GPU allocation rule (four times selected block bytes plus 16 MiB). The multiplier is a conservative **heuristic**, not a measured GPU peak.

**Limitations:** All these are planning caps and arithmetic. They are not process-wide hard limits on total VRAM/RAM. Excluded costs include tokenizer, true CUDA/KV allocation peaks, fragmentation, vision encoder, actual 27B decoder execution, sampling, precision conversions, and unknown pretrained checkpoint tensors. A passing plan never changes runnable_model or trained_checkpoint_verified from false.

## Run on existing Windows CUDA environment

Copy these commands. No new installations or model downloads are performed:

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"

& $py experiments\littlebit70b\gemma3_27b_ready.py

& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Expected planning output:
- status = PLAN_ONLY_NO_CHECKPOINT
- decoder_layers = 62
- projection_count = 434
- matches_target_27b_architecture = true
- runnable_model = false
- trained_checkpoint_verified = false
- actual_gpu_peak_measured = false

**Expected full suite: 160 tests** (144 previously verified on RTX 4050 + 16 new). Two new GPU integration tests validate one-block packed loading/eviction and the deliberately too-small allocation rejection.

Optional: inspect your **already downloaded** public dense-model index, not the shards:

~~~powershell
$index = "$HOME\Desktop\Gemma3-27B-public-index.json"
& $py experiments\littlebit70b\gemma3_27b_ready.py --index $index
~~~

If that file exists, the expected classification is DENSE_NOT_LITTLEBIT. The command only uses a local manifest and does not download any weights.

When a genuine trained LittleBit-2 checkpoint eventually exists, use the following for a metadata-only structural compatibility and memory budget check:

~~~powershell
& $py experiments\littlebit70b\gemma3_27b_ready.py --checkpoint "C:\path\to\genuine-littlebit-checkpoint" --context 4096
~~~

This will **not** generate real text or run 62 decoder blocks. It verifies tensor layout/size and reports budget reservations only. Trained weights, quality and licensing must be established independently.

## Verification

- Full-size metadata fixture traverses 62 decoder layers and all 434 projection descriptions with no giant tensors allocated. Fake header data is explicitly virtual/untrained.
- New suite: 16 tests; 14 passed locally and 2 CUDA tests skipped (no CUDA device on build host).
- Fresh broader available suite: 148 tests, no failures, 32 GPU skips. Python compileall and no-checkpoint CLI succeeded.
- Real-format small checkpoints were tested for sharded loading, packed block factor shapes, missing/dense projections and insufficient memory budgets.
- Existing 144-test CUDA experiment had previously passed on the user's RTX 4050, but the two NEW CUDA block tests are not yet hardware-verified.

Public model/method references: [Samsung LittleBit](https://github.com/SamsungLabs/LittleBit), [Transformers Gemma 3 architecture](https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma3/modular_gemma3.py).

Constraints: free-only, Samsung LittleBit-2, no Qwen, no modifications to KalshiEdge trading operations.
