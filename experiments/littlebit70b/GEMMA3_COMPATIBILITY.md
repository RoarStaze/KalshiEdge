# Gemma 3 27B + Samsung LittleBit-2 architecture audit

**This is a configuration and checkpoint-metadata audit only. No trained checkpoint, model weights, inference or quality-preserving quantization is supplied. It does not prove that the 27B model runs in 6GB VRAM or 16GB system RAM.**

This project now audits the actual target architecture instead of indefinitely optimizing synthetic matrix-vector kernels.

## Source verification

- [Public Unsloth Gemma 3 27B IT configuration](https://huggingface.co/unsloth/gemma-3-27b-it/blob/main/config.json): text hidden size 5,376, intermediate size 21,504, 62 decoder layers, 32 query heads, 16 KV heads, head dimension 128, vocabulary size 262,208, sliding window 1,024 and one global attention layer per six decoder layers.
- [Public Unsloth original BF16 model index](https://huggingface.co/unsloth/gemma-3-27b-it/blob/main/model.safetensors.index.json): names include language_model.model.layers.0.mlp.down_proj.weight, not Samsung U_packed/V_packed names. Its index metadata reports total_size 54,864,813,280 bytes, spread over 12 model shards. This is **not** a LittleBit checkpoint.
- [Hugging Face Transformers Gemma 3 architecture](https://github.com/huggingface/transformers/blob/main/src/transformers/models/gemma3/modular_gemma3.py): q/k/v/o projections, gate/up/down MLP, RMSNorm, RoPE, alternating global/local attention and SigLIP vision path.
- [Samsung LittleBit code](https://github.com/SamsungLabs/LittleBit/blob/main/quantization/modules/littlebit.py): saved U_packed, V_packed, U_shape, V_shape and learned scales.

The bundled gemma3_27b_reference_config.json is an **abbreviated architecture-only reference** based on the published model config. It is not an exact official model file or a training artifact. The tied LM head is assumed from the Transformers Gemma 3 default.

## Run without downloading weights

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main

$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\gemma3_bridge.py

& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

This milestone adds **11 new tests**, making **95 total expected** (previously 84, plus 11). The new config audit is CPU-only; the existing tests exercise CUDA on your RTX 4050.

## Optional: inspect real publicly available model metadata, not weights

The public Safetensors index is about 127KB. These commands download only that JSON file, NOT the heavy model shards:

~~~powershell
$index = "$HOME\Desktop\Gemma3-27B-public-index.json"
Invoke-WebRequest "https://huggingface.co/unsloth/gemma-3-27b-it/resolve/main/model.safetensors.index.json" -OutFile $index

& $py experiments\littlebit70b\gemma3_bridge.py --index $index
~~~

Expected classification: DENSE_NOT_LITTLEBIT. The script checks module names from metadata only and never claims a checkpoint is runnable. Even when all packed keys appear, the weight contents, licenses, training quality and actual transformer compatibility remain unverified.

## Text-only idealized storage scenarios, 4096-token context

**Estimates, not measured checkpoint sizes.** Apply a theoretical 0.55 bits per original dense parameter only to chosen linear projections. Real LittleBit has U and V factors, scaling vectors, possible residuals, and word padding. Actual quality and size may differ substantially.

| Scenario | Idealized storage |
|---|---:|
| BF16 text projections, embedding and basic norms | 50.31 GiB |
| LittleBit 0.55 target BPW on three MLP projections only | 11.63 GiB |
| LittleBit 0.55 target BPW on all seven decoder projections | 4.27 GiB |
| Tied text embedding BF16 alone | 2.63 GiB |
| Idealized BF16 K/V cache at 4096-token context | 0.719 GiB, additional |

KV arithmetic assumes 52 sliding-window layers of at most 1024 KV positions, 10 global attention layers, 16 KV heads of dimension 128 and BF16 K/V. It excludes GPU allocator fragmentation, activation buffers, positional embedding overhead, model vision encoder, compiler/driver memory, and other residual tensors.

The 4.27 GiB theoretical all-projection case does **not** prove the complete model fits a 6GB GPU. Compressing attention could also harm quality and is not validated by Samsung's MLP experiments. No trained acceptable Gemma 3 27B LittleBit checkpoint is currently confirmed.

## Next engineering action

The packed CUDA kernels, checkpoint reader, and cached streaming path are functional prerequisites. The next substantive work is implementing Gemma 3's actual transformer features: RMSNorm, rotary embeddings, grouped-query attention with sliding/global cache, gate/up/down nonlinear MLP, text embeddings and logits, backed by numerical tests. Real 27B inference additionally requires licensed, trained quality-preserving LittleBit weights and full memory profiling.

**Constraints preserved:** free-only, no Qwen, no other quantizer replacing LittleBit, no trading code changes.
