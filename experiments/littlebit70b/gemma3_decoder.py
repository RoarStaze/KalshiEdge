"""Tiny *CPU reference* for Gemma 3's text decoder using LittleBit projections.

Implements single-token causal grouped-query attention (local or global),
Gemma's (1+w) RMSNorm, Q/K normalization, split-half RoPE, gated tanh-GELU
MLP and four decoder norms. The seven projections can be supplied by our
Samsung-compatible packed LittleBit factor implementation.

This is numerical reference code for tiny synthetic layers: not an optimized
GPU transformer, an actual Gemma checkpoint, multimodal processing or an LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math

import numpy as np


@dataclass(frozen=True)
class DecoderSpec:
    hidden: int
    intermediate: int
    q_heads: int
    kv_heads: int
    head_dim: int
    sliding_window: int = 1024
    query_pre_attn_scalar: float = 168.0
    eps: float = 1e-6
    local_rope_theta: float = 10000.0
    global_rope_theta: float = 1000000.0
    global_rope_factor: float = 1.0
    attn_softcap: float | None = None

    def __post_init__(self):
        for name in ('hidden', 'intermediate', 'q_heads', 'kv_heads', 'head_dim', 'sliding_window'):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f'{name} must be a positive integer')
        if self.q_heads % self.kv_heads or self.head_dim % 2:
            raise ValueError('Grouped query heads must divide, and RoPE head dim must be even')
        if self.hidden > 256 or self.intermediate > 1024 or self.q_heads > 64 or self.head_dim > 128:
            # Constrain the reference to small, inspectable CPU numerical cases.
            raise ValueError('Reference decoder is intentionally limited to tiny configurations')
        for name in ('query_pre_attn_scalar', 'eps', 'local_rope_theta',
                     'global_rope_theta', 'global_rope_factor'):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be finite and positive')
        if self.attn_softcap is not None and (
            not isinstance(self.attn_softcap, (int, float)) or
            not math.isfinite(self.attn_softcap) or self.attn_softcap <= 0
        ):
            raise ValueError('attn_softcap must be positive or None')


def layer_kind(index: int, pattern: int = 6) -> str:
    """Gemma 3 decoder: local on indices 0..4, global on index 5, etc."""
    if type(index) is not int or index < 0 or type(pattern) is not int or pattern < 1:
        raise ValueError('Invalid layer index/pattern')
    return 'full_attention' if (index + 1) % pattern == 0 else 'sliding_attention'


def rms_norm(x, weight, eps=1e-6):
    a = np.asarray(x, dtype=np.float32)
    w = np.asarray(weight, dtype=np.float32)
    if a.ndim not in (1, 2) or w.ndim != 1 or a.shape[-1] != w.size:
        raise ValueError('Gemma RMSNorm requires matching last dimensions')
    if not isinstance(eps, (int, float)) or not np.isfinite(eps) or eps <= 0:
        raise ValueError('Invalid norm epsilon')
    # Official Gemma family multiplies normalized values by (1 + weight).
    return (a / np.sqrt(np.mean(np.square(a), axis=-1, keepdims=True) + eps)
            * (1.0 + w)).astype(np.float32)


def gelu_tanh(x):
    a = np.asarray(x, dtype=np.float32)
    return (0.5 * a * (1.0 + np.tanh(np.sqrt(2.0 / np.pi)
                                    * (a + np.float32(0.044715) * a**3)))).astype(np.float32)


def apply_rope(heads, position: int, *, theta: float, factor: float = 1.0):
    """HF split-half RoPE, frequency position/factor for linear global scaling."""
    x = np.asarray(heads, dtype=np.float32)
    if x.ndim != 2 or x.shape[1] < 2 or x.shape[1] % 2:
        raise ValueError('RoPE requires (heads, even head_dim)')
    if type(position) is not int or position < 0 or not (np.isfinite(theta) and theta > 0
                                                        and np.isfinite(factor) and factor > 0):
        raise ValueError('Invalid rotary position/base/factor')
    head_dim = x.shape[1]
    inverse = 1.0 / np.power(float(theta), np.arange(0, head_dim, 2, dtype=np.float32) / head_dim)
    angles = np.tile((position / float(factor)) * inverse, 2).astype(np.float32)
    h = head_dim // 2
    rotated = np.concatenate((-x[:, h:], x[:, :h]), axis=-1)
    return (x * np.cos(angles) + rotated * np.sin(angles)).astype(np.float32)


class Gemma3KVCache:
    """Single-layer, single-sequence, causal FP32 K/V reference cache."""

    def __init__(self, kind: str, window: int):
        if kind not in ('sliding_attention', 'full_attention'):
            raise ValueError('Unsupported attention kind')
        if type(window) is not int or window < 1:
            raise ValueError('Sliding window must be a positive integer')
        self.kind = kind
        self.window = window
        self.positions: list[int] = []
        self.keys: np.ndarray | None = None
        self.values: np.ndarray | None = None

    def append(self, key, value, *, position: int):
        k = np.asarray(key, dtype=np.float32)
        v = np.asarray(value, dtype=np.float32)
        if type(position) is not int or position < 0 or position != (self.positions[-1] + 1 if self.positions else 0):
            raise ValueError('Expected the next contiguous causal token position')
        if k.ndim != 2 or k.shape != v.shape:
            raise ValueError('KV must be same-shaped (kv_heads, head_dim)')
        if self.keys is not None and k.shape != self.keys.shape[1:]:
            raise ValueError('KV head shape changed')
        self.keys = k[None, ...].copy() if self.keys is None else np.concatenate((self.keys, k[None, ...]))
        self.values = v[None, ...].copy() if self.values is None else np.concatenate((self.values, v[None, ...]))
        self.positions.append(position)
        if self.kind == 'sliding_attention' and len(self.positions) > self.window:
            self.positions = self.positions[-self.window:]
            self.keys = self.keys[-self.window:].copy()
            self.values = self.values[-self.window:].copy()


def attention_one(query, key, value, cache: Gemma3KVCache, *, position: int, cfg: DecoderSpec):
    """One-token causal GQA: q/k are already RMS-normalized and RoPE-rotated."""
    q = np.asarray(query, dtype=np.float32)
    k = np.asarray(key, dtype=np.float32)
    v = np.asarray(value, dtype=np.float32)
    if q.shape != (cfg.q_heads, cfg.head_dim) or k.shape != (cfg.kv_heads, cfg.head_dim) or v.shape != k.shape:
        raise ValueError('Incorrect Q/K/V head shapes')
    if cache.window != cfg.sliding_window:
        raise ValueError('Cache sliding window differs from decoder configuration')
    cache.append(k, v, position=position)
    repeats = cfg.q_heads // cfg.kv_heads
    output = np.empty_like(q)
    for h in range(cfg.q_heads):
        kvh = h // repeats
        logits = (cache.keys[:, kvh, :] @ q[h]) * (1 / math.sqrt(cfg.query_pre_attn_scalar))
        if cfg.attn_softcap is not None:
            logits = np.tanh(logits / cfg.attn_softcap) * cfg.attn_softcap
        weights = np.exp(logits - np.max(logits))
        weights = weights / np.sum(weights)
        output[h] = weights @ cache.values[:, kvh, :]
    return output


class Gemma3PackedDecoder:
    """One Gemma 3 text decoder block; projection objects expose .forward(list).

    The seven projections may be Samsung LittleBit packed linear operators.
    This class is intentionally CPU-only to provide a correctness reference.
    """

    def __init__(self, cfg: DecoderSpec, projections: dict, norms: dict):
        self.cfg = cfg
        self.projections = dict(projections)
        self.norms = {k: np.asarray(v, dtype=np.float32) for k, v in norms.items()}
        dims = {
            'self_attn.q_proj': (cfg.hidden, cfg.q_heads * cfg.head_dim),
            'self_attn.k_proj': (cfg.hidden, cfg.kv_heads * cfg.head_dim),
            'self_attn.v_proj': (cfg.hidden, cfg.kv_heads * cfg.head_dim),
            'self_attn.o_proj': (cfg.q_heads * cfg.head_dim, cfg.hidden),
            'mlp.gate_proj': (cfg.hidden, cfg.intermediate),
            'mlp.up_proj': (cfg.hidden, cfg.intermediate),
            'mlp.down_proj': (cfg.intermediate, cfg.hidden),
        }
        self._dims = dims
        if set(self.projections) != set(dims):
            raise ValueError('Gemma 3 decoder requires all seven projection modules')
        sizes = {'input_layernorm': cfg.hidden, 'post_attention_layernorm': cfg.hidden,
                 'pre_feedforward_layernorm': cfg.hidden, 'post_feedforward_layernorm': cfg.hidden,
                 'q_norm': cfg.head_dim, 'k_norm': cfg.head_dim}
        if set(self.norms) != set(sizes) or any(self.norms[k].shape != (v,) for k,v in sizes.items()):
            raise ValueError('Gemma 3 decoder requires four block norms and Q/K norms')

    def _projection(self, name: str, x):
        features_in, features_out = self._dims[name]
        a = np.asarray(x, dtype=np.float32)
        if a.shape != (features_in,):
            raise ValueError('Projection input dimension mismatch: ' + name)
        y = np.asarray(self.projections[name].forward(a.tolist()), dtype=np.float32)
        if y.shape != (features_out,):
            raise ValueError('Projection output dimension mismatch: ' + name)
        return y

    def forward_token(self, hidden, position: int, cache: Gemma3KVCache):
        x = np.asarray(hidden, dtype=np.float32)
        if x.shape != (self.cfg.hidden,) or type(position) is not int or position < 0:
            raise ValueError('Bad decoder hidden state or absolute position')
        input_norm = rms_norm(x, self.norms['input_layernorm'], self.cfg.eps)
        q = self._projection('self_attn.q_proj', input_norm).reshape(self.cfg.q_heads, self.cfg.head_dim)
        k = self._projection('self_attn.k_proj', input_norm).reshape(self.cfg.kv_heads, self.cfg.head_dim)
        v = self._projection('self_attn.v_proj', input_norm).reshape(self.cfg.kv_heads, self.cfg.head_dim)
        # Official Gemma 3 normalizes q/k BEFORE applying rotary embeddings.
        q = rms_norm(q, self.norms['q_norm'], self.cfg.eps)
        k = rms_norm(k, self.norms['k_norm'], self.cfg.eps)
        if cache.kind == 'sliding_attention':
            theta, factor = self.cfg.local_rope_theta, 1.0
        else:
            theta, factor = self.cfg.global_rope_theta, self.cfg.global_rope_factor
        q = apply_rope(q, position, theta=theta, factor=factor)
        k = apply_rope(k, position, theta=theta, factor=factor)
        attended = attention_one(q, k, v, cache, position=position, cfg=self.cfg).reshape(-1)
        attn_out = self._projection('self_attn.o_proj', attended)
        after_attn = x + rms_norm(attn_out, self.norms['post_attention_layernorm'], self.cfg.eps)
        feed = rms_norm(after_attn, self.norms['pre_feedforward_layernorm'], self.cfg.eps)
        gate = self._projection('mlp.gate_proj', feed)
        up = self._projection('mlp.up_proj', feed)
        mlp_out = self._projection('mlp.down_proj', gelu_tanh(gate) * up)
        result = after_attn + rms_norm(mlp_out, self.norms['post_feedforward_layernorm'], self.cfg.eps)
        return result.astype(np.float32)


def make_synthetic_decoder(cfg: DecoderSpec, *, seed: int = 17):
    """Construct ALL SEVEN packed LittleBit projections using tiny random signs."""
    from packed_linear import PackedBinaryMatrix, LittleBitPackedLinear
    if type(seed) is not int or seed < 0:
        raise ValueError('Seed must be a nonnegative integer')
    rng = np.random.default_rng(seed)
    dims = {
        'self_attn.q_proj': (cfg.hidden, cfg.q_heads * cfg.head_dim),
        'self_attn.k_proj': (cfg.hidden, cfg.kv_heads * cfg.head_dim),
        'self_attn.v_proj': (cfg.hidden, cfg.kv_heads * cfg.head_dim),
        'self_attn.o_proj': (cfg.q_heads * cfg.head_dim, cfg.hidden),
        'mlp.gate_proj': (cfg.hidden, cfg.intermediate),
        'mlp.up_proj': (cfg.hidden, cfg.intermediate),
        'mlp.down_proj': (cfg.intermediate, cfg.hidden),
    }
    ops = {}
    for name, (n_in, n_out) in dims.items():
        rank = min(4, n_in, n_out)
        v = PackedBinaryMatrix.from_signs(rng.choice([-1,1], (rank, n_in)).tolist())
        u = PackedBinaryMatrix.from_signs(rng.choice([-1,1], (n_out, rank)).tolist())
        ops[name] = LittleBitPackedLinear(
            v, u, v1=[0.45]*rank, v2=[0.4]*n_in,
            u1=[0.35]*n_out, u2=[0.35]*rank)
    norms = {name: np.zeros((cfg.head_dim if name in ('q_norm', 'k_norm') else cfg.hidden,),
                            dtype=np.float32)
             for name in ('input_layernorm', 'post_attention_layernorm',
                          'pre_feedforward_layernorm', 'post_feedforward_layernorm',
                          'q_norm', 'k_norm')}
    return Gemma3PackedDecoder(cfg, ops, norms)


def demo():
    cfg = DecoderSpec(hidden=8, intermediate=12, q_heads=4, kv_heads=2,
                      head_dim=4, sliding_window=3, query_pre_attn_scalar=8., global_rope_factor=8.)
    decoder = make_synthetic_decoder(cfg)
    last_outputs = {}
    last_cache = {}
    for kind in ('sliding_attention', 'full_attention'):
        cache = Gemma3KVCache(kind, cfg.sliding_window)
        for pos in range(5):
            value = np.sin((np.arange(cfg.hidden, dtype=np.float32) + pos) * 0.1)
            out = decoder.forward_token(value, pos, cache)
        last_outputs[kind] = [round(float(v), 6) for v in out[:3]]
        last_cache[kind] = len(cache.positions)
    return {
        'status': 'GEMMA3_PACKED_DECODER_REFERENCE_OK',
        'checkpoint': 'SYNTHETIC_ONLY', 'trained_model': False,
        'whole_llm': False, 'cuda_executed': False,
        'model_config': 'TINY_REFERENCE_ONLY',
        'packed_projection_count': len(decoder.projections),
        'test_tokens_per_mode': 5,
        'sliding_cache_tokens_after': last_cache['sliding_attention'],
        'global_cache_tokens_after': last_cache['full_attention'],
        'output_first_3_by_attention_mode': last_outputs,
    }


if __name__ == '__main__':
    print(json.dumps(demo(), indent=2))
