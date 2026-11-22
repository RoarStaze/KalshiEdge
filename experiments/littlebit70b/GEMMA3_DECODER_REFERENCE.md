# Gemma 3 decoder reference using Samsung LittleBit packed projections

**Milestone:** An executable NumPy CPU numerical reference for one **tiny Gemma 3 text decoder block**, using **seven LittleBit binary-packed projection operators** from the existing packed_linear.py.

**NOT delivered:** a trained Gemma 3 27B model, tokenizer, embeddings, logits, whole-model weights, CUDA attention execution, multimodal input, or actual text generation. This does not prove that 27B can run with 6GB VRAM and 16GB system RAM.

## Upstream numerical specification

Primary references:
- Hugging Face [official Gemma 3 decoder/attention/RoPE/configuration](https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma3/modular_gemma3.py).
- Hugging Face [Gemma 2 attention/decoder reference](https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma2/modular_gemma2.py), inherited by Gemma 3.
- Hugging Face [Gemma RMSNorm, gated MLP, split-half RoPE](https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma/modeling_gemma.py).
- [Samsung LittleBit binary-packed layer](https://github.com/SamsungLabs/LittleBit/blob/main/quantization/modules/littlebit.py).

The CPU reference includes:

1. **Gemma RMSNorm:** normalized FP32 activations multiplied by (1 + weight), rather than weight alone.
2. **Q/K/V/O attention:** four packed LittleBit projections. Separate Q and K RMSNorm **before** rotation. Grouped-query attention maps multiple Q heads to a KV head.
3. **Rotary embeddings:** split-half rotation, local base 10,000 and global base 1,000,000. Optional global linear factor; toy example factor 8. Real inference must honor the exact model config.
4. **Attention:** scale logits by 1/sqrt(query_pre_attn_scalar), optional tanh logit softcap, stable causal softmax.
5. **K/V cache:** single-sequence and one-token-at-a-time FP32 reference. Sliding attention evicts old entries; global attention retains causal history. Gemma 3 uses five local layers followed by one global layer in each six-layer group.
6. **Gated MLP:** down_proj(gelu_tanh(gate_proj(x)) * up_proj(x)) using three packed projections.
7. **Four block norms and two residual additions:** input RMSNorm -> attention -> post-attention norm -> residual; pre-feedforward norm -> gated MLP -> post-feedforward norm -> residual.

The demo processes five tokens independently in each attention type with tiny synthetic dimensions: hidden 8, intermediate 12, four Q heads, two KV heads, head dimension 4, and sliding window 3. **No genuine trained checkpoint is involved.**

## Run in your existing Windows PowerShell CUDA environment

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\gemma3_decoder.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Expected demo fields:
- status: GEMMA3_PACKED_DECODER_REFERENCE_OK
- checkpoint: SYNTHETIC_ONLY
- trained_model: false
- whole_llm: false
- cuda_executed: false
- packed_projection_count: 7
- sliding_cache_tokens_after: 3
- global_cache_tokens_after: 5

The decoder demo deliberately runs on **CPU as a numerical reference**. The separate existing CUDA tests still execute on your RTX 4050.

The suite should have **106 tests** (95 existing tests, plus 11 new decoder checks). This milestone requires NumPy already present in the isolated CuPy environment, no new downloads. Independent PyTorch cross-checks were completed in the development environment, but the shipped decoder and its 11 tests do **not** import or require PyTorch.

## What remains

The working packed CUDA layer engine and this new **Gemma 3 decoder CPU reference** are currently separate. They have not been integrated into full GPU text generation. Before end-to-end inference, additional milestones include GPU versions of normalization, RoPE, grouped-query attention, gated activation and KV cache; real checkpoint norm/projection loading with validated tensor types; embedding and output logits; and actual tested quantized weights.

The public Gemma 3 27B manifest previously inspected by the user contains **434 ordinary dense projection tensors and zero LittleBit packed projection tensors**. A complete, quality-preserving trained Gemma 3 27B LittleBit-2 checkpoint remains unavailable in this project. The idealized 0.55 BPW scenario is not measured checkpoint storage or a guarantee of 27B VRAM feasibility.
