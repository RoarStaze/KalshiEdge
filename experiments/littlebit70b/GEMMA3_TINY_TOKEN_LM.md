# Six-block Gemma 3 synthetic token pipeline with packed LittleBit

**Milestone:** One tiny text-model execution skeleton running six Gemma 3 decoder blocks in sequence, with synthetic token IDs, tied embeddings, final RMSNorm, logits, greedy generation and per-layer sliding/global K/V caches. CPU and CUDA modes share the same synthetic packed LittleBit weights. **This is not a trained language model or a 27B checkpoint.**

## What was added

- gemma3_tiny_lm.py builds six independent packed-factor decoder blocks (five local/sliding, one global).
- Tiny synthetic embeddings: 32 vocabulary entries and hidden size 8. Inputs are scaled by sqrt(hidden_size), matching Gemma's scaled word embeddings.
- Final (1 + weight) RMSNorm and tied-embedding output head produce logits over 32 synthetic token IDs, with optional tanh logit softcapping.
- Causal single-token prefilling and deterministic greedy selection; generated tokens are fed back into the decoder stack.
- Per-layer K/V caches, local-window eviction, global-cache retention and cross-model state isolation.
- Hard toy size limits: 1–12 blocks, 2–256 synthetic vocabulary entries, at most 256 context tokens, and at most 16 generated IDs.
- GPU mode retains embedding table, packed layers, activations, K/V caches and final logits on CUDA. It transfers the argmax scalar to CPU per generated token; this is not a production scheduler.

**Produces integers, not English words.** No tokenizer, learned model vocabulary, pretrained weights or real Gemma 3 quality is claimed.

## Run now (Windows PowerShell)

No installs required. Reuse the existing isolated RTX 4050 CuPy environment:

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"

& $py experiments\littlebit70b\gemma3_tiny_lm.py --backend cuda

& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Optional CPU-only demonstration:

~~~powershell
& $py experiments\littlebit70b\gemma3_tiny_lm.py --backend cpu
~~~

Expected GPU result: status GEMMA3_TINY_TOKEN_PIPELINE_OK; cuda_executed true; decoder_blocks 6; local_attention_blocks 5; global_attention_blocks 1; prompt_token_ids [1,4,7]; generated_token_ids two integers between 0 and 31; checkpoint SYNTHETIC_ONLY; trained_model false; whole_llm false; text_generation_available false; tokenizer_available false. A nontrivial CPU/GPU logit mismatch raises an error instead of reporting success.

**Expected full suite: 129 tests** (115 previously verified on the RTX 4050 + 14 new tests including 3 hardware-specific). No GPU tests should skip on your laptop.

## Verification before merge

- New tests failed as expected with ModuleNotFoundError before implementation.
- New suite: 14 tests, 11 passed and 3 GPU tests skipped; no CUDA device in the development environment.
- Combined decoder-focused suite: 34 tests, 25 passed and 9 CUDA-dependent skipped; exit code 0.
- Python compilation succeeded. CPU demonstration returned GEMMA3_TINY_TOKEN_PIPELINE_OK with prompt [1,4,7], generated [5,23] for seed 19. These are synthetic symbol IDs only.
- **New six-block GPU execution is not yet verified**; RTX 4050 run is the acceptance gate.

## Architecture references

Official [Gemma 3 text model](https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma3/modular_gemma3.py) scales embeddings by sqrt(hidden), runs decoder blocks and applies final RMSNorm. [Gemma 2 CausalLM](https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma2/modular_gemma2.py), inherited by Gemma 3, applies the tied output LM head and optional tanh final-logit softcap. [Samsung LittleBit](https://github.com/SamsungLabs/LittleBit) defines the packed factor representation.

## Explicit limits and follow-up

This tiny demo has **six** blocks, not Gemma 3 27B's **62** large layers. CUDA mode currently retains every tiny block in GPU memory; the separate bounded-layer streaming engine is **not yet integrated into this model pipeline**. Supports single sequence, FP32 K/V, synthetic weights and no tokenizer. It is not optimized, does not demonstrate 27B tokens/sec, real model quality or fit in 6GB VRAM.

No trained, quality-validated Gemma 3 27B LittleBit-2 checkpoint has been identified. The original dense Gemma checkpoint is NOT usable as though it were LittleBit compressed. Meaningful 27B inference will require trained compressed weights, correct full-model tensor mapping, model-wide memory orchestration and quality verification.

**Constraints:** Free-only; Samsung LittleBit/LittleBit-2; no Qwen; no KalshiEdge trading changes.
