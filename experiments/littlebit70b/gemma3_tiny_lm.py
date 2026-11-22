"""Bounded *synthetic* Gemma 3 token pipeline with LittleBit packed projections.

This connects token IDs -> tied embeddings -> multiple decoder blocks -> final
RMSNorm -> tied output logits -> greedy token IDs. It is NOT a trained Gemma
checkpoint, tokenizer, text generator, 27B runtime or quantization recipe.

No actual pretrained weights are loaded; sizes are intentionally small.
GPU mode reuses the existing packed LittleBit CUDA decoder implementation.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math

import numpy as np

from gemma3_decoder import DecoderSpec, Gemma3KVCache, Gemma3PackedDecoder, layer_kind, make_synthetic_decoder, rms_norm


def _check_positive_int(value, *, label, minimum=1, maximum=256):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{label} must be an integer in {minimum}..{maximum}')
    return value


@dataclass
class TinyLMState:
    owner: int
    position: int
    caches: list


class TinyPackedLM:
    """One-sequence, FP32 tiny autoregressive model with tied embedding table.

    The decoder layers are the real packed-factor toy Gemma math references;
    CUDA mode constructs the corresponding full GPU decoders once. No layer
    streaming, cache sharing between sequences or model-level VRAM limit.
    """

    def __init__(self, cfg: DecoderSpec, cpu_layers: list[Gemma3PackedDecoder],
                 embedding, final_norm, *, backend='cpu', max_tokens=32,
                 final_logit_softcap=None):
        if not isinstance(cfg, DecoderSpec):
            raise TypeError('Expected DecoderSpec')
        if not isinstance(cpu_layers, (list, tuple)) or not 1 <= len(cpu_layers) <= 12:
            raise ValueError('Tiny model requires 1..12 decoder blocks')
        if not all(isinstance(layer, Gemma3PackedDecoder) and layer.cfg == cfg for layer in cpu_layers):
            raise ValueError('Decoder configurations must match')
        _check_positive_int(max_tokens, label='max_tokens')
        if backend not in ('cpu', 'cuda'):
            raise ValueError('backend must be cpu or cuda')
        if final_logit_softcap is not None and (type(final_logit_softcap) not in (int, float)
                or not math.isfinite(final_logit_softcap) or final_logit_softcap <= 0):
            raise ValueError('Final-logit softcap must be finite and positive')
        emb = np.asarray(embedding, dtype=np.float32)
        norm = np.asarray(final_norm, dtype=np.float32)
        if (emb.ndim != 2 or not 2 <= emb.shape[0] <= 256 or emb.shape[1] != cfg.hidden
                or norm.shape != (cfg.hidden,) or not np.isfinite(emb).all()
                or not np.isfinite(norm).all()):
            raise ValueError('Expected finite tiny (vocab, hidden) embeddings and hidden norm')
        self.config = cfg
        self.backend = backend
        self.max_tokens = max_tokens
        self.cpu_layers = list(cpu_layers)
        self.layer_kinds = [layer_kind(i) for i in range(len(cpu_layers))]
        self.embedding = emb.copy()
        self.final_norm = norm.copy()
        self.final_logit_softcap = final_logit_softcap
        self.cp = None
        self.device_embedding = None
        self.device_final_norm = None
        if backend == 'cuda':
            from gemma3_cuda_decoder import CUDAGemma3PackedDecoder
            from cuda_packed import _cupy
            cp = _cupy()  # Fail rather than report CUDA success with a CPU fallback.
            self.cp = cp
            self.device_embedding = cp.asarray(self.embedding, dtype=cp.float32)
            self.device_final_norm = cp.asarray(self.final_norm, dtype=cp.float32)
            self.layers = [CUDAGemma3PackedDecoder.from_cpu(layer) for layer in cpu_layers]
        else:
            self.layers = self.cpu_layers

    @property
    def vocab_size(self):
        return self.embedding.shape[0]

    def new_state(self):
        if self.backend == 'cuda':
            from gemma3_cuda_decoder import CUDAGemma3KVCache
            caches = [CUDAGemma3KVCache(kind, self.config.sliding_window,
                                        max_tokens=self.max_tokens)
                      for kind in self.layer_kinds]
        else:
            caches = [Gemma3KVCache(kind, self.config.sliding_window)
                      for kind in self.layer_kinds]
        return TinyLMState(owner=id(self), position=0, caches=caches)

    def _validate_state(self, state):
        if not isinstance(state, TinyLMState) or state.owner != id(self):
            raise ValueError('State was not created by this model')
        if state.position >= self.max_tokens:
            raise ValueError('Maximum synthetic token context exceeded')
        if len(state.caches) != len(self.layers):
            raise ValueError('State is missing a decoder cache')
        for kind, cache in zip(self.layer_kinds, state.caches):
            if cache.kind != kind or cache.window != self.config.sliding_window:
                raise ValueError('Cache kind/window mismatch')
            # Check before any decoder mutates another layer cache.
            if self.backend == 'cuda':
                cache.validate_next(state.position)
            elif (cache.positions[-1] + 1 if cache.positions else 0) != state.position:
                raise ValueError('CPU cache has unexpected token position')

    def step(self, token_id: int, state: TinyLMState):
        if type(token_id) is not int or not 0 <= token_id < self.vocab_size:
            raise ValueError(f'token_id must be an integer in 0..{self.vocab_size - 1}')
        self._validate_state(state)
        cp = self.cp
        if cp is None:
            hidden = self.embedding[token_id] * np.float32(math.sqrt(self.config.hidden))
        else:
            hidden = self.device_embedding[token_id] * cp.float32(math.sqrt(self.config.hidden))
        for layer, cache in zip(self.layers, state.caches):
            hidden = layer.forward_token(hidden, state.position, cache)
        if cp is None:
            hidden = rms_norm(hidden, self.final_norm, self.config.eps)
            logits = hidden @ self.embedding.T  # Tied language-model head.
        else:
            from gemma3_cuda_decoder import _rmsnorm_cuda
            hidden = _rmsnorm_cuda(cp, hidden, self.device_final_norm, self.config.eps)
            logits = hidden @ self.device_embedding.T
        if self.final_logit_softcap is not None:
            cap = self.final_logit_softcap
            logits = cap * (np.tanh(logits / cap) if cp is None else cp.tanh(logits / cap))
        state.position += 1
        return logits

    def _validate_prompt(self, prompt, *, max_new_tokens=0):
        if not isinstance(prompt, (tuple, list)) or not prompt:
            raise ValueError('Expected nonempty token ID list')
        if any(type(t) is not int or not 0 <= t < self.vocab_size for t in prompt):
            raise ValueError('Prompt contains an invalid synthetic token ID')
        if len(prompt) + max_new_tokens > self.max_tokens:
            raise ValueError('Requested tokens exceed the bounded context limit')

    def prefill(self, prompt):
        self._validate_prompt(prompt)
        state = self.new_state()
        logits = None
        for token in prompt:
            logits = self.step(token, state)
        return logits, state

    def generate(self, prompt, *, max_new_tokens=2):
        _check_positive_int(max_new_tokens, label='max_new_tokens', maximum=16)
        self._validate_prompt(prompt, max_new_tokens=max_new_tokens)
        logits, state = self.prefill(prompt)
        new = []
        for _ in range(max_new_tokens):
            token = int(np.argmax(logits) if self.cp is None else self.cp.argmax(logits).item())
            new.append(token)
            logits = self.step(token, state)  # Caches also include final generated token.
        return {
            'prompt_token_ids': list(prompt), 'generated_token_ids': new,
            'tokens_consumed': state.position, 'decoder_blocks': len(self.layers),
            'backend': self.backend, 'checkpoint': 'SYNTHETIC_ONLY',
            'trained_model': False, 'whole_llm': False,
            'text_generation_available': False, 'tokenizer_available': False,
        }


def make_demo_model(*, backend='cpu', depth=6, vocab=32, seed=17,
                    max_tokens=32, final_logit_softcap=None):
    _check_positive_int(depth, label='depth', maximum=12)
    _check_positive_int(vocab, label='vocab', minimum=2)
    _check_positive_int(max_tokens, label='max_tokens')
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError('seed must be a nonnegative 32-bit integer')
    cfg = DecoderSpec(hidden=8, intermediate=12, q_heads=4, kv_heads=2,
                      head_dim=4, sliding_window=3, query_pre_attn_scalar=8.,
                      global_rope_factor=8.)
    layers = [make_synthetic_decoder(cfg, seed=seed + i * 7) for i in range(depth)]
    rng = np.random.default_rng(seed)
    embedding = rng.normal(0, .07, size=(vocab, cfg.hidden)).astype(np.float32)
    final_norm = np.linspace(-.08, .08, cfg.hidden, dtype=np.float32)
    return TinyPackedLM(cfg, layers, embedding, final_norm, backend=backend,
                        max_tokens=max_tokens, final_logit_softcap=final_logit_softcap)


def run_demo(*, backend='cuda'):
    if backend not in ('cpu', 'cuda'):
        raise ValueError('Only cpu or cuda backends are supported')
    prompt = [1, 4, 7]
    cpu = make_demo_model(backend='cpu', seed=19)
    model = cpu if backend == 'cpu' else make_demo_model(backend='cuda', seed=19)
    # A real GPU demonstration must agree numerically with the independent CPU path.
    reference_logits, _ = cpu.prefill(prompt)
    test_logits, _ = model.prefill(prompt)
    max_error = None
    if backend == 'cuda':
        observed = test_logits.get()
        max_error = float(np.max(np.abs(reference_logits - observed)))
        if not np.allclose(reference_logits, observed, rtol=5e-4, atol=5e-4):
            raise AssertionError('GPU logits differ from CPU packed decoder reference')
    result = model.generate(prompt, max_new_tokens=2)
    if backend == 'cuda':
        cpu_ids = cpu.generate(prompt, max_new_tokens=2)['generated_token_ids']
        if result['generated_token_ids'] != cpu_ids:
            raise AssertionError('GPU and CPU selected different greedy synthetic token IDs')
    result.update({
        'status': 'GEMMA3_TINY_TOKEN_PIPELINE_OK',
        'cuda_executed': backend == 'cuda',
        'local_attention_blocks': model.layer_kinds.count('sliding_attention'),
        'global_attention_blocks': model.layer_kinds.count('full_attention'),
        'max_abs_logit_error': max_error,
        'model_config': 'TINY_SYNTHETIC_REFERENCE_ONLY',
    })
    if backend == 'cuda':
        name = model.cp.cuda.runtime.getDeviceProperties(model.cp.cuda.runtime.getDevice())['name']
        result['device'] = name.decode('utf8', errors='replace') if isinstance(name, bytes) else str(name)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Bounded synthetic LittleBit Gemma token ID generator')
    parser.add_argument('--backend', choices=('cpu', 'cuda'), default='cuda')
    args = parser.parse_args()
    print(json.dumps(run_demo(backend=args.backend), indent=2))
