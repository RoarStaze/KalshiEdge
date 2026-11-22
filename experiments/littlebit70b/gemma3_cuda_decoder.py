"""Experimental full tiny Gemma 3 text decoder execution on CUDA via CuPy.

Reuses LittleBit's verified packed warp CUDA projection kernel for all seven
linear weights; RMSNorm, Q/K RoPE, grouped-query attention, gated MLP, and
small causal KV caches remain GPU-resident. CPU outputs are fetched only for
numerical verification in the demonstration.

This is NOT a checkpoint conversion, a real Gemma 3 27B model, a tokenizer,
accelerated 27B inference, or quality-preserving quantization. Supports only
small single-token/single-sequence FP32 numerical reference configurations.
"""
from __future__ import annotations

import json
import math

import numpy as np

from gemma3_decoder import DecoderSpec, Gemma3PackedDecoder, Gemma3KVCache, make_synthetic_decoder
from cuda_packed import _cupy


def _validate_cache_limit(max_tokens):
    if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
        raise ValueError('max_tokens must be an integer from 1 to 4096')
    return max_tokens


class CUDAGemma3KVCache:
    """A GPU-resident FP32 reference KV cache with explicit token limit.

    One cache instance belongs to ONE decoder layer and one sequence. Global
    attention retains all seen tokens up to max_tokens; sliding attention
    retains only the most recent window tokens. This is not an efficient
    production ring buffer and does not account for full process VRAM.
    """
    def __init__(self, kind: str, window: int, *, max_tokens: int = 256):
        if kind not in ('sliding_attention', 'full_attention'):
            raise ValueError('Unsupported attention kind')
        if type(window) is not int or window < 1 or window > 4096:
            raise ValueError('window must be an integer from 1 to 4096')
        _validate_cache_limit(max_tokens)
        self.kind = kind
        self.window = window
        self.max_tokens = max_tokens
        self.positions: list[int] = []
        self.next_position = 0
        self.keys = None
        self.values = None

    def validate_next(self, position: int):
        if type(position) is not int or position != self.next_position:
            raise ValueError(f'Expected contiguous token position {self.next_position}')
        if self.kind == 'full_attention' and len(self.positions) >= self.max_tokens:
            raise MemoryError('Global GPU KV cache token budget exceeded')

    def append(self, key, value, *, position: int):
        cp = _cupy()
        self.validate_next(position)
        k = cp.asarray(key, dtype=cp.float32)
        v = cp.asarray(value, dtype=cp.float32)
        if k.ndim != 2 or k.shape != v.shape:
            raise ValueError('KV shapes must match (kv_heads, head_dim)')
        if self.keys is not None and k.shape != self.keys.shape[1:]:
            raise ValueError('KV head dimensions changed mid-stream')
        if self.keys is None:
            keys, values = k[None, :, :].copy(), v[None, :, :].copy()
        else:
            # The demonstration caches tiny token counts, deliberately simple.
            keys = cp.concatenate((self.keys, k[None, :, :]), axis=0)
            values = cp.concatenate((self.values, v[None, :, :]), axis=0)
        pos = self.positions + [position]
        if self.kind == 'sliding_attention' and len(pos) > self.window:
            pos = pos[-self.window:]
            keys = keys[-self.window:, :, :].copy()
            values = values[-self.window:, :, :].copy()
        self.positions, self.keys, self.values = pos, keys, values
        self.next_position += 1


def _rmsnorm_cuda(cp, x, weight, eps):
    a = cp.asarray(x, dtype=cp.float32)
    w = cp.asarray(weight, dtype=cp.float32)
    if a.shape[-1] != w.shape[0]:
        raise ValueError('RMSNorm weight dimension mismatch')
    return a / cp.sqrt(cp.mean(a * a, axis=-1, keepdims=True) + cp.float32(eps)) * (1.0 + w)


def _rope_cuda(cp, x, position: int, theta: float, factor: float):
    dim = x.shape[-1]
    half = dim // 2
    inv = 1.0 / cp.power(cp.float32(theta), cp.arange(0, dim, 2, dtype=cp.float32) / cp.float32(dim))
    freqs = cp.concatenate((inv, inv), axis=0) * cp.float32(position / factor)
    rotated = cp.concatenate((-x[:, half:], x[:, :half]), axis=-1)
    return (x * cp.cos(freqs) + rotated * cp.sin(freqs)).astype(cp.float32)


def _attention_cuda(cp, q, k, v, cache: CUDAGemma3KVCache, position: int, cfg: DecoderSpec):
    if q.shape != (cfg.q_heads, cfg.head_dim) or k.shape != (cfg.kv_heads, cfg.head_dim) or v.shape != k.shape:
        raise ValueError('Incorrect Q/K/V dimensions')
    cache.append(k, v, position=position)
    nrep = cfg.q_heads // cfg.kv_heads
    keys = cp.repeat(cache.keys, nrep, axis=1)       # [tokens, query_heads, head_dim]
    values = cp.repeat(cache.values, nrep, axis=1)
    logits = cp.einsum('thd,hd->ht', keys, q) * cp.float32(1 / math.sqrt(cfg.query_pre_attn_scalar))
    if cfg.attn_softcap is not None:
        logits = cp.tanh(logits / cp.float32(cfg.attn_softcap)) * cp.float32(cfg.attn_softcap)
    logits = logits - cp.max(logits, axis=-1, keepdims=True)
    probs = cp.exp(logits)
    probs = probs / cp.sum(probs, axis=-1, keepdims=True)
    return cp.einsum('ht,thd->hd', probs, values).astype(cp.float32)


class CUDAGemma3PackedDecoder:
    """One *tiny* Gemma3 decoder block using seven preloaded packed CUDA layers."""
    def __init__(self, cfg: DecoderSpec, projections, norms):
        cp = _cupy()
        if not isinstance(cfg, DecoderSpec) or set(projections) != {
            'self_attn.q_proj', 'self_attn.k_proj', 'self_attn.v_proj', 'self_attn.o_proj',
            'mlp.gate_proj', 'mlp.up_proj', 'mlp.down_proj',
        }:
            raise ValueError('Expected a DecoderSpec and all seven CUDA projections')
        self.cfg = cfg
        self.cp = cp
        self.projections = dict(projections)
        self.norms = {key: cp.asarray(value, dtype=cp.float32) for key, value in norms.items()}

    @classmethod
    def from_cpu(cls, decoder: Gemma3PackedDecoder):
        if not isinstance(decoder, Gemma3PackedDecoder):
            raise TypeError('Expected Gemma3PackedDecoder CPU reference')
        from cuda_warp import WarpCUDALittleBitLinear
        return cls(decoder.cfg,
                   {name: WarpCUDALittleBitLinear.from_cpu(op)
                    for name, op in decoder.projections.items()},
                   decoder.norms)

    def _proj(self, name, vector):
        result = self.projections[name].forward(vector)
        return result.astype(self.cp.float32, copy=False)

    def forward_token(self, hidden, position: int, cache: CUDAGemma3KVCache):
        cp = self.cp
        if not isinstance(cache, CUDAGemma3KVCache) or cache.window != self.cfg.sliding_window:
            raise ValueError('Expected a compatible CUDAGemma3KVCache')
        cache.validate_next(position)
        x = cp.asarray(hidden, dtype=cp.float32)
        if x.shape != (self.cfg.hidden,):
            raise ValueError('Expected one hidden activation vector')
        cfg = self.cfg
        normed = _rmsnorm_cuda(cp, x, self.norms['input_layernorm'], cfg.eps)
        q = self._proj('self_attn.q_proj', normed).reshape(cfg.q_heads, cfg.head_dim)
        k = self._proj('self_attn.k_proj', normed).reshape(cfg.kv_heads, cfg.head_dim)
        v = self._proj('self_attn.v_proj', normed).reshape(cfg.kv_heads, cfg.head_dim)
        q = _rmsnorm_cuda(cp, q, self.norms['q_norm'], cfg.eps)
        k = _rmsnorm_cuda(cp, k, self.norms['k_norm'], cfg.eps)
        local = cache.kind == 'sliding_attention'
        theta = cfg.local_rope_theta if local else cfg.global_rope_theta
        factor = 1.0 if local else cfg.global_rope_factor
        q = _rope_cuda(cp, q, position, theta, factor)
        k = _rope_cuda(cp, k, position, theta, factor)
        attention = _attention_cuda(cp, q, k, v, cache, position, cfg).reshape(-1)
        attn_out = self._proj('self_attn.o_proj', attention)
        residual = x + _rmsnorm_cuda(cp, attn_out, self.norms['post_attention_layernorm'], cfg.eps)
        feed = _rmsnorm_cuda(cp, residual, self.norms['pre_feedforward_layernorm'], cfg.eps)
        gate = self._proj('mlp.gate_proj', feed)
        up = self._proj('mlp.up_proj', feed)
        activated = 0.5 * gate * (1.0 + cp.tanh(cp.float32(math.sqrt(2.0/math.pi)) *
                                                (gate + cp.float32(.044715) * gate**3)))
        mlp = self._proj('mlp.down_proj', activated * up)
        return (residual + _rmsnorm_cuda(cp, mlp, self.norms['post_feedforward_layernorm'], cfg.eps)).astype(cp.float32)


def run_demo():
    """Run true GPU operations; verify against independent CPU implementation."""
    cp = _cupy()  # Fail explicitly without CUDA.
    cfg = DecoderSpec(hidden=8, intermediate=12, q_heads=4, kv_heads=2,
                      head_dim=4, sliding_window=3, query_pre_attn_scalar=8.,
                      global_rope_factor=8.)
    cpu = make_synthetic_decoder(cfg, seed=17)
    gpu = CUDAGemma3PackedDecoder.from_cpu(cpu)
    errors = []
    last_cache = {}
    for kind in ('sliding_attention', 'full_attention'):
        cpu_cache = Gemma3KVCache(kind, cfg.sliding_window)
        cuda_cache = CUDAGemma3KVCache(kind, cfg.sliding_window, max_tokens=16)
        for pos in range(5):
            vector = np.sin((np.arange(cfg.hidden, dtype=np.float32) + pos) * .1)
            cpu_expected = cpu.forward_token(vector, pos, cpu_cache)
            device_output = gpu.forward_token(vector, pos, cuda_cache)
            actual = device_output.get()  # Only validation/final output crosses CPU boundary.
            err = float(np.max(np.abs(cpu_expected - actual)))
            errors.append(err)
            if not np.allclose(cpu_expected, actual, rtol=5e-4, atol=5e-4):
                raise AssertionError(f'CPU / CUDA Gemma decoder mismatch: {kind} token {pos}, error={err}')
        last_cache[kind] = len(cuda_cache.positions)
    device_name = cp.cuda.runtime.getDeviceProperties(cp.cuda.runtime.getDevice())['name']
    if isinstance(device_name, bytes):
        device_name = device_name.decode('utf8', errors='replace')
    return {
        'status': 'GEMMA3_PACKED_CUDA_DECODER_OK', 'checkpoint': 'SYNTHETIC_ONLY',
        'trained_model': False, 'whole_llm': False, 'cuda_executed': True,
        'device': str(device_name), 'model_config': 'TINY_REFERENCE_ONLY',
        'packed_projection_count': 7, 'test_tokens_per_attention_mode': 5,
        'sliding_cache_tokens_after': last_cache['sliding_attention'],
        'global_cache_tokens_after': last_cache['full_attention'],
        'max_abs_error': max(errors), 'kv_cache_dtype': 'float32',
        'model_generation_available': False,
    }


if __name__ == '__main__':
    print(json.dumps(run_demo(), indent=2))
