# LittleBit 70B feasibility lab

Isolated research prototype. **Not a working 70B inference engine.** No trading code or credentials are used.

Hardware target: Windows 11, RTX 4050 Laptop GPU (6 GB VRAM), 16 GB system RAM.

## Gate before downloading weights

A checkpoint must be a *trained, packed* 70B LittleBit-compatible model, not an untrained projection or a filename claiming 0.1 BPW. Its model card must document architecture, quantized tensor coverage, effective bits per parameter, evaluation results, and the exact runtime/kernel.

Use the zero-dependency preflight:

```powershell
cd "$HOME\Desktop\KalshiEdge"
git pull --ff-only origin main
python experiments/littlebit70b/preflight.py --parameters 70000000000 --effective-bpw 0.23 --overhead-gib 2
python -m unittest discover -s experiments/littlebit70b/tests -v
```

The default 0.23 BPW is an **illustrative scenario**, not a verified LittleBit 70B compression ratio. Change it only with checkpoint evidence.

## Acceptance criteria

1. A legitimate pretrained 70B compressed checkpoint is publicly accessible and legally redistributable.
2. A loader and kernel actually run its compressed representation without materializing the full BF16 model.
3. Measured peak VRAM <= 6 GB and RAM <= 16 GB on the target laptop.
4. Generated text passes basic coherence and repeatability checks.
5. Coding benchmark quality is compared with a smaller local baseline.

No automatic downloads or model execution occur here. A failed memory estimate must never be represented as a working deployment.
