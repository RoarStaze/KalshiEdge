# Gemma 3 LittleBit checkpoint-backed decoder-block streaming

**New milestone:** Connect the previously verified six-block synthetic Gemma 3 token pipeline to a **real-format Safetensors checkpoint loader**. Instead of holding every decoder's packed factors in GPU memory, the CUDA path loads one decoder block from disk, executes it using the established packed CUDA warp kernels, releases the block's packed weights, and proceeds to the next block. Per-layer K/V cache stays GPU-resident across tokens.

This is **NOT a pretrained or trained Gemma 3 27B checkpoint**, not a text chatbot, not a general 27B runtime and not 27B feasibility proof. The demonstration uses **only tiny synthetic weights** (hidden width 8, intermediate 12, six blocks, toy vocabulary 32). It does **not** download anything.

## Actual tensor naming

The checkpoint reader requires Hugging Face Gemma 3 text-model names:

- Embedding: language_model.model.embed_tokens.weight
- Final RMSNorm: language_model.model.norm.weight
- For each layer i: language_model.model.layers.i.input_layernorm.weight, post_attention_layernorm.weight, pre_feedforward_layernorm.weight, post_feedforward_layernorm.weight
- Q/K head norms: language_model.model.layers.i.self_attn.q_norm.weight and k_norm.weight
- For each of the seven attention and MLP projections (self_attn.q_proj, k_proj, v_proj, o_proj; mlp.gate_proj, up_proj, down_proj): Samsung LittleBit U_packed, V_packed, U_shape, V_shape, u1, u2, v1, v2 and optional residual/bias tensors

The loader refuses dense .weight projections, conflicting dense-and-packed projection names, incomplete factor sets, invalid shapes, incompatible ranks, malformed scaling and norm tensors, and selected tensor-byte limit violations. These checks happen **before generating any token**, to avoid partially mutating KV caches due to known malformed metadata. A tensor-name match does NOT prove pretrained quality or license compatibility.

Input supports either a **single .safetensors file** or a directory with a standard sharded model.safetensors.index.json manifest. It reuses the prior bounded Safetensors reader; only the selected tensors for a given decoder block are read on demand.

The synthetic writer is test-only; it creates a standard Safetensors file with 374 keys using actual Gemma module names, verified independently with the official Safetensors Python reader. It does not quantize or reconstruct dense 27B weights.

## Run on the RTX 4050 — PowerShell

No new installations are required. Use the existing isolated CuPy CUDA environment.

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"

& $py experiments\littlebit70b\gemma3_checkpoint_stream.py --backend cuda

& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Optional CPU demonstration:

~~~powershell
& $py experiments\littlebit70b\gemma3_checkpoint_stream.py --backend cpu
~~~

**Expected demo fields:** status GEMMA3_PACKED_CHECKPOINT_STREAM_OK, backend cuda, cuda_executed true, checkpoint SYNTHETIC_ONLY, trained_model false, whole_llm false, decoder_blocks 6, prompt_token_ids [1,4,7], generated_token_ids [5,23], loaded_block_count 30, all_decoder_weights_resident_on_gpu false, weights_live_after_step_bytes 0 and a small max_abs_logit_error against the independent in-memory CPU reference.

The complete LittleBit experiment test suite should now contain **144 tests** (129 previously verified on the RTX 4050 + 15 new tests, 3 requiring CUDA). No CUDA tests should skip on the laptop.

## Pool bounds: what is measured and what is not

StreamedTinyGemma3 uses **two independent private CuPy memory pools**:

- Decoder packed weights: defaults to a 16 MiB pool cap; releases the GPU block and checks live allocations after each block.
- Persistent activations, embeddings, final norm and per-block K/V: separate 16 MiB pool cap; K/V allocations survive decoder weight eviction.

The selected decoder checkpoint tensor data and embeddings are each validated against separate defaults of 8 MiB per decoder block and 4 MiB for embedding plus final norm. Context is limited to 256 tokens, generation to 16 synthetic token IDs, and up to 12 toy decoder blocks. These are **toy-reference bounds**, not production limits for the real 27B model.

CuPy pool limits and the reported weight_pool_peak_bytes/state_pool_peak_bytes do **NOT** measure or cap total GPU VRAM, CUDA context, kernel compilation allocations, other allocators, fragmentation, or total CPU RSS. The return field total_device_vram_peak_measured is false.

**Performance:** this design reloads all seven decoder packed projections for every token. It is correctness-first and likely slow without layer batching, prefetch and cache management. It has not been benchmarked on real model weights.

## Verification completed before GitHub merge

- Tests authored first: import failed for missing new module (expected red).
- Local new-suite run: **15 tests: 12 passed, 3 GPU skipped**, no failures, exit 0.
- Locally assembled available combined suite: **132 tests, no failures, 30 GPU skips**, exit 0. The development host has no CUDA access; this assembled suite does not include every prior laptop test, hence is less than 144.
- Python compileall succeeded.
- Official Safetensors safe_open independently recognized all **374** generated tensor keys.
- Checkpoint-backed CPU demo returned GEMMA3_PACKED_CHECKPOINT_STREAM_OK; generated synthetic IDs [5,23]; maximum logit error approximately 7.45e-8.
- New CUDA loader/eviction has **NOT been hardware-verified**; the three new GPU tests are the RTX 4050 acceptance gate.

## Remaining blockers

The earlier architecture audit showed the publicly inspected Gemma 3 27B model index contains 434 ordinary dense projection tensors and **zero recognized Samsung LittleBit packed projections**. The new streaming loader **cannot** turn an uncompressed .weight checkpoint into valid trained LittleBit weights. It is currently limited by the tiny DecoderSpec (hidden dimension at most 256), so it is not a drop-in full 27B model loader.

A future genuine 27B runner still needs a verified trained compressed checkpoint; full-size decoder instantiation/memory planning; tokenizer; correct precision and multilingual/multimodal behavior; robust device memory scheduling; actual benchmark and quality evaluation. Without trained weights, output token IDs remain meaningless synthetic symbols.

References: [Gemma 3 Transformers implementation](https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma3/modular_gemma3.py), [public Gemma 3 27B checkpoint index](https://huggingface.co/unsloth/gemma-3-27b-it/blob/main/model.safetensors.index.json), [Samsung LittleBit](https://github.com/SamsungLabs/LittleBit).

Constraints preserved: **free-only, no Qwen, Samsung LittleBit-2 research, no KalshiEdge trading changes**.
