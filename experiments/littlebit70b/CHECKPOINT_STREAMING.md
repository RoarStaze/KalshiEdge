# LittleBit-2 packed checkpoint layer reader

**Scope:** A bounded reader for one Samsung LittleBit/LittleBit-2 linear layer from a standard Safetensors file or a sharded model directory containing model.safetensors.index.json. **This is not a 27B checkpoint, a full Gemma inference runtime, or a training method.**

## Actual Samsung tensor names

Samsung's [LittleBitLinear](https://github.com/SamsungLabs/LittleBit/blob/main/quantization/modules/littlebit.py) saves U_packed, V_packed (signed int32 words with 32 LSB-first signs), U_shape, V_shape (original int64 shapes), u1, u2, v1, v2 (floating-point scales), optional bias and optional residual U_R_packed, V_R_packed, U_R_shape, V_R_shape, u1_R, u2_R, v1_R, v2_R.

The safetensors_layer.py reader validates names, shapes, dtypes, offsets, signed words, residual completeness and a default selected tensor-data budget of 64 MiB **per layer**. Header length is limited to 16 MiB. Actual memory overhead and CUDA allocations exceed selected raw bytes.

For sharded checkpoints it reads model.safetensors.index.json and accesses only referenced shard files for the requested layer, rejecting path traversal. There is no network access, full-model allocation or automatic download. It handles BF16/FP16/FP32 scales via NumPy, which is already installed in the isolated CuPy environment.

## Exact PowerShell commands

~~~powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
$py = "$HOME\Desktop\LittleBit-CUDA-venv\Scripts\python.exe"
& $py experiments\littlebit70b\safetensors_layer.py
& $py -m unittest discover -s experiments\littlebit70b\tests -v
~~~

Expected status: LITTLEBIT_SAFETENSORS_LAYER_DEMO_OK, checkpoint SYNTHETIC_ONLY, trained_model false, whole_llm false, cuda_executed true on the RTX 4050. The demonstration constructs a temporary standard-format packed layer, reads it, runs the warp CUDA kernel, and checks against the CPU output. It deletes the temporary file.

Expected experiment suite: **45 tests** (30 previous + 12 reader + 3 benchmark-summary tests). The new GPU loader parity test skips without a CUDA GPU.

## Optional: inspect a locally present Samsung-packed checkpoint

Do NOT download a 27B model just for this command. If a checkpoint file or sharded model directory becomes available, adapt the path and tensor prefix as appropriate:

~~~powershell
& $py experiments\littlebit70b\safetensors_layer.py --checkpoint "C:\PATH\TO\CHECKPOINT" --prefix "model.layers.0.mlp.down_proj" --max-layer-mib 64
~~~

A successful layer read does not establish trained quality, whole-model compatibility or sufficient RAM for an LLM.

## Optional repeated benchmark

The prior single-run RTX 4050 synthetic speedups were 1.50x, 7.34x and 58.15x across three shapes. Because timing by matrix size was inconsistent, those figures require independent repeated measurements. This tool executes five passes per shape, reversing the shape order on alternate rounds, and computes medians plus a warning for 2x or greater timing spread:

~~~powershell
& $py experiments\littlebit70b\repeat_benchmark.py
~~~

These are isolated synthetic GPU kernel timings, **not Gemma 27B tokens per second**.

## Limitations and next development

No trained Gemma 3 27B checkpoint, tokenizer, attention implementation, full-model loader, layer eviction, KV cache, or 27B QAT is supplied. The selected tensor-data budget is not a peak-memory guarantee. The loader's new CUDA code path requires testing on your RTX 4050. Upstream LittleBit source uses CC BY-NC 4.0; checkpoint and base model licenses must also be considered.

Next: test real checkpoint compatibility if a trained LittleBit checkpoint becomes available, then integrate multiple layer groups with bounded GPU allocation and eviction. Training 27B from scratch at the desired quality remains blocked by free-only compute.
