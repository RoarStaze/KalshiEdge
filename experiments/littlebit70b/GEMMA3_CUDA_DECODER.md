# Gemma 3 text decoder on CUDA using Samsung LittleBit packed projections

**Milestone:** A **tiny** single-token/single-sequence Gemma 3 decoder block that executes on CUDA via the user's already-installed CuPy environment and seven previously verified LittleBit binary-packed warp projections.

**NOT** a trained Gemma 3 27B checkpoint, a usable chatbot, a tokenizer, model generation, a full multi-layer transformer, or proof of 27B memory feasibility.

## Implemented

- Converts the CPU reference's **seven** q/k/v/o and gate/up/down LittleBit packed-linear operators into CUDA-resident packed warp operators **once** at construction. Sign factors are not inflated into dense BF16 weight matrices.
- Gemma's (1 + weight) RMSNorm, Q/K norm before split-half RoPE, local/global RoPE settings, grouped-query causal attention, optional tanh score softcap, four decoder norms, gated tanh-GELU MLP and two residual additions via CuPy CUDA arrays.
- Maintains per-layer, per-sequence FP32 K/V arrays on CUDA, with sliding-window eviction and an **explicit maximum token count for global KV** (default 256, configurable up to 4096). This is a toy concatenate-and-slice cache, **not** an efficient ring buffer.
- Accepts one hidden vector at a time. Intermediate activations remain GPU-resident; outputs transfer to CPU during tests and demonstration.
- Validates CPU against GPU for **five tokens per attention mode**, and refuses to report success if parity fails.
- No model downloads and no allocation of 27B-sized arrays. CPU reference limits enforce tiny shapes.

## Exact PowerShell commands — existing isolated CUDA environment

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\gemma3_cuda_decoder.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Expected fields: status GEMMA3_PACKED_CUDA_DECODER_OK; cuda_executed true; device NVIDIA GeForce RTX 4050 Laptop GPU; packed_projection_count 7; checkpoint SYNTHETIC_ONLY; whole_llm false; trained_model false.

**115 total LittleBit tests expected:** 106 previously verified on RTX 4050 plus 9 new tests (3 CPU-mode and 6 requiring CUDA). On your GPU the six new CUDA tests must actually execute and pass, not skip.

## Verification before merge

- Tests written before implementation: first run failed because gemma3_cuda_decoder did not yet exist, as expected.
- Local combined tiny decoder suite: **20 tests, 14 passed and 6 CUDA skipped**, exit 0; Python compileall exit 0.
- A NumPy-backed execution of the new GPU decoder's arithmetic matched the established CPU packed decoder over six consecutive tokens in both local/global attention modes.
- **The actual CuPy GPU path is NOT yet hardware-verified** because the development environment has no CUDA device. Run on your RTX 4050 for definitive confirmation.

## Limits and next engineering task

- Non-projection operations use CuPy primitives, **not optimized fused attention kernels**. Real GPU speed is not yet known.
- GPU K/V is FP32 and single-sequence. Its token cap is **not a process-wide VRAM limit**. No full-model allocation or KV-cache memory measurement has been made.
- All weights are synthetic. No pretrained, trained or quality-verified Gemma 3 27B LittleBit-2 checkpoint is included.
- Full text inference requires model-wide checkpoint mapping, token embeddings, final norm, output logits, sampling and a real tokenizer, besides high-quality trained weights.
- KalshiEdge's trading logic remains untouched.

References: [Hugging Face Gemma 3](https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma3/modular_gemma3.py), [Gemma RMSNorm/RoPE](https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma/modeling_gemma.py), [Samsung LittleBit](https://github.com/SamsungLabs/LittleBit).
