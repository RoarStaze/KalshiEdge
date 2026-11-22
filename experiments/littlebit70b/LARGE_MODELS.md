# Large LittleBit checkpoint status — 2026-11-22

**Goal:** A trained LittleBit or LittleBit-2 model with 27B+ parameters on the HP OMEN (16 GB RAM / RTX 4050 6 GB). **No other quantizers.** Preferred free-only non-Qwen research target: Gemma 3 27B + LittleBit-2 (70B remains an aspirational size).

## Verified source findings

- [Samsung's official repository](https://github.com/SamsungLabs/LittleBit) documents quantization-aware training and evaluation but does **not** document a packaged, trained 70B checkpoint or a demonstrated 6GB GPU inference runtime. LittleBit-2 changes initialization (`--use_itq`), not the runtime.
- [LittleBitLLM](https://huggingface.co/LittleBitLLM/littlebit) describes its 8B release as an early 0.5-epoch checkpoint and 14B as planned, not released.
- [Haverbex/Qwen3.8-27B-LittleBit-0.1bit](https://huggingface.co/Haverbex/Qwen3.8-27B-LittleBit-0.1bit) applies LittleBit to 192 decoder MLP projections; the 0.1-bit target **does not cover all model weights**. Other weights remain BF16. The uploader reports **21.57 GB stored weights**, **zero recovery-training steps**, a loader that expands factors, and ARC-Challenge on a small subset of 29.69% versus 96.88% for the original. Other released 27B variants are similarly experimental.
- A [packed-factor decoding issue](https://github.com/SamsungLabs/LittleBit/issues/21) has been reported upstream. Check status and exact version before evaluating new packed checkpoints.

## One-command candidate check (metadata only)

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
python experiments/littlebit70b/large_check.py
```

For machine-readable output:

```powershell
python experiments/littlebit70b/large_check.py --json --output littlebit-large-audit.json
```

This script checks Hugging Face repository metadata without downloading model weights. A model's name can suggest a parameter count; **it cannot prove it**. If the search fails, the tool reports the failure rather than claiming there are no candidates.

## Deployment gates

1. Identify an actually trained LittleBit checkpoint with provenance and whole-model effective BPW.
2. Verify reliable benchmark scores and language/coding quality.
3. Verify packed inference that does not expand most model weights to BF16.
4. Measure VRAM, RAM, KV cache, and inference throughput on the target hardware.
5. Only then add an interactive runner and a controlled checkpoint download workflow.

**Current state: NOT READY.** No 27B–70B candidate passed all gates. This scanner is not a trained 70B model and does not run a model.

## Non-Qwen target decision (2026-11-22)

**User constraint:** Do not use any Qwen or QwQ model. Only Samsung LittleBit/LittleBit-2 compression. No paid cloud compute. Do not suggest ordinary GGUF, GPTQ or AWQ as a substitute.

**Selected model family:** [Google Gemma 3 27B Instruct](https://huggingface.co/google/gemma-3-27b-it), as an experimental target **not a pretrained LittleBit checkpoint**. Samsung explicitly supports Gemma 3 in [LittleBit](https://github.com/SamsungLabs/LittleBit), and [LittleBit-2's ICML paper](https://proceedings.mlr.press/v306/lee26y.html) evaluates Gemma-3 27B. Google's model may require accepting the model usage license before accessing original weights.

**Important feasibility restriction:** A 27B teacher plus quantization-aware training cannot be trained with the laptop's RTX 4050 6GB and 16GB RAM. Free hosted GPU allocations may also be insufficient, and no free allocation is guaranteed. **Do not download the original 27B weights or launch training** until a real, adequate free GPU resource is identified. The published research is not a trained downloadable checkpoint; any advertised 0.1 or 0.55 BPW is a target for selected model layers, not evidence of a runnable compressed artifact.

**Next user action, read-only:**

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
Get-Content experiments\littlebit70b\LARGE_MODELS.md
```

No model has been installed, compressed or successfully run. This change only revises the research target.
