"""Read real Gemma-3 architecture metadata and audit LittleBit compatibility.

Never downloads weights or guesses trained checkpoint readiness. The bundled
reference config records architecture numbers from the public Unsloth mirror
of Gemma-3-27B-IT. Users may supply their own config.json and optional local
Safetensors index for metadata-only compatibility checks.

The memory scenarios use idealized fractional bits-per-dense-parameter for
selected linear matrices, *not* an actual LittleBit checkpoint size. They do
not account for padded binary factors, scaling vectors, residual ranks,
vision encoder, allocator overhead, runtime activations or CPU/GPU offload.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from math import ceil
from pathlib import Path


class ConfigError(ValueError):
    """Unsupported/malformed Gemma model configuration."""


class ManifestError(ValueError):
    """Malformed or unbounded Safetensors weight manifest."""


TARGET_27B = (5376, 21504, 62, 32, 16, 128, 262208)
PACKED_KEYS = ('U_packed', 'V_packed', 'U_shape', 'V_shape', 'u1', 'u2', 'v1', 'v2')
PROJECTIONS = ('self_attn.q_proj', 'self_attn.k_proj', 'self_attn.v_proj',
               'self_attn.o_proj', 'mlp.gate_proj', 'mlp.up_proj', 'mlp.down_proj')
BYTES_BF16 = 2
MAX_CONFIG_BYTES = 128 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def test_fixture_path():
    return Path(__file__).with_name('gemma3_27b_reference_config.json')


def _read_limited_json(path: Path, max_bytes: int, error_type):
    try:
        if path.stat().st_size > max_bytes:
            raise error_type(f'File exceeds the {max_bytes} byte metadata limit')
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise error_type(f'Invalid JSON metadata file: {path.name}') from exc
    if not isinstance(value, dict):
        raise error_type('Metadata must be a JSON object')
    return value


def _positive_int(section, name):
    value = section.get(name)
    if type(value) is not int or value < 1:
        raise ConfigError(f'text_config.{name} must be a positive integer')
    return value


@dataclass(frozen=True)
class Gemma3TextArchitecture:
    hidden: int
    intermediate: int
    layers: int
    q_heads: int
    kv_heads: int
    head_dim: int
    vocab: int
    sliding_window: int
    window_pattern: int
    max_positions: int
    has_vision_encoder: bool

    @property
    def matches_target_27b(self):
        return ((self.hidden, self.intermediate, self.layers, self.q_heads,
                 self.kv_heads, self.head_dim, self.vocab) == TARGET_27B and
                self.sliding_window == 1024 and self.window_pattern == 6)

    @property
    def attention_pattern(self):
        return tuple('sliding_attention' if (index + 1) % self.window_pattern
                     else 'full_attention' for index in range(self.layers))


def read_gemma_config(path):
    source = _read_limited_json(Path(path), MAX_CONFIG_BYTES, ConfigError)
    if source.get('model_type') != 'gemma3' or not isinstance(source.get('text_config'), dict):
        raise ConfigError('Expected multimodal Gemma 3 with a text_config')
    text = source['text_config']
    if text.get('model_type') != 'gemma3_text':
        raise ConfigError('This tool only handles Gemma 3 text architecture')
    arch = Gemma3TextArchitecture(
        hidden=_positive_int(text, 'hidden_size'),
        intermediate=_positive_int(text, 'intermediate_size'),
        layers=_positive_int(text, 'num_hidden_layers'),
        q_heads=_positive_int(text, 'num_attention_heads'),
        kv_heads=_positive_int(text, 'num_key_value_heads'),
        head_dim=_positive_int(text, 'head_dim'),
        vocab=_positive_int(text, 'vocab_size'),
        sliding_window=_positive_int(text, 'sliding_window'),
        window_pattern=_positive_int(text, 'sliding_window_pattern'),
        max_positions=_positive_int(text, 'max_position_embeddings'),
        has_vision_encoder=isinstance(source.get('vision_config'), dict),
    )
    if arch.q_heads % arch.kv_heads != 0 or arch.sliding_window > arch.max_positions:
        raise ConfigError('Inconsistent grouped-query attention or sliding window')
    if arch.window_pattern > arch.layers:
        raise ConfigError('Window pattern exceeds decoder layer count')
    if arch.hidden > 100000 or arch.layers > 1000:
        raise ConfigError('Configuration exceeds expected bounds')
    if text.get('attention_bias') is not False:
        raise ConfigError('Biased attention projections not supported by this estimator')
    return arch


def projection_spec(cfg):
    h, m = cfg.hidden, cfg.intermediate
    q = cfg.q_heads * cfg.head_dim
    kv = cfg.kv_heads * cfg.head_dim
    return {
        'self_attn.q_proj': (q, h),
        'self_attn.k_proj': (kv, h),
        'self_attn.v_proj': (kv, h),
        'self_attn.o_proj': (h, q),
        'mlp.gate_proj': (m, h),
        'mlp.up_proj': (m, h),
        'mlp.down_proj': (h, m),
    }


def enumerate_projection_keys(cfg):
    """Actual HF Gemma3 checkpoint module path, verified against public index."""
    shapes = projection_spec(cfg)
    for index in range(cfg.layers):
        for name in PROJECTIONS:
            yield index, f'language_model.model.layers.{index}.{name}', shapes[name]


def audit_memory(cfg, *, context_tokens=4096, effective_bits=0.55):
    """Idealized text-only scenario; no assertion of model/run/checkpoint fit."""
    if type(context_tokens) is not int or not (1 <= context_tokens <= cfg.max_positions):
        raise ValueError('context_tokens must be within model position range')
    if (type(effective_bits) not in (float, int) or
            not (0 < effective_bits <= 2)):
        raise ValueError('effective_bits must be in (0, 2]')
    shapes = projection_spec(cfg)
    mlp_parameters = sum(o * i for key, (o, i) in shapes.items() if key.startswith('mlp.')) * cfg.layers
    attention_parameters = sum(o * i for key, (o, i) in shapes.items() if key.startswith('self_attn.')) * cfg.layers
    embedding_parameters = cfg.vocab * cfg.hidden  # tied head counted once
    # Four decoder RMSNorms, Q/K head norms and one text final norm.
    norm_parameters_approx = cfg.layers * (4 * cfg.hidden + 2 * cfg.head_dim) + cfg.hidden
    fixed_text_bf16 = (embedding_parameters + norm_parameters_approx) * BYTES_BF16
    mlp_bf16 = mlp_parameters * BYTES_BF16
    attention_bf16 = attention_parameters * BYTES_BF16
    ideal_mlp = ceil(mlp_parameters * float(effective_bits) / 8)
    ideal_attn = ceil(attention_parameters * float(effective_bits) / 8)
    # Idealized K/V count, excluding padding, cache bookkeeping and kernels.
    per_token_kv_bytes = 2 * cfg.kv_heads * cfg.head_dim * BYTES_BF16
    kv_tokens = sum(min(context_tokens, cfg.sliding_window) if t == 'sliding_attention'
                    else context_tokens for t in cfg.attention_pattern)
    ideal_kv = kv_tokens * per_token_kv_bytes
    mlp_only = fixed_text_bf16 + attention_bf16 + ideal_mlp
    all_proj = fixed_text_bf16 + ideal_attn + ideal_mlp
    return {
        'model': 'Gemma 3 multimodal (text-only projection scenarios)',
        'matches_target_27b_architecture': cfg.matches_target_27b,
        'decoder_layers': cfg.layers,
        'local_attention_layers': cfg.attention_pattern.count('sliding_attention'),
        'global_attention_layers': cfg.attention_pattern.count('full_attention'),
        'hidden_size': cfg.hidden, 'vocab_size': cfg.vocab,
        'per_layer_projection_shapes': {key: list(shape) for key, shape in shapes.items()},
        'text_embedding_bf16_bytes': embedding_parameters * 2,
        'text_attention_projection_bf16_bytes': attention_bf16,
        'text_mlp_projection_bf16_bytes': mlp_bf16,
        'text_norm_bf16_bytes_approx': norm_parameters_approx * 2,
        'text_linear_and_embedding_bf16_baseline_bytes': fixed_text_bf16 + attention_bf16 + mlp_bf16,
        'idealized_littlebit_bpw_target': float(effective_bits),
        'idealized_mlp_only_text_weights_bytes': mlp_only,
        'idealized_all_projection_text_weights_bytes': all_proj,
        'idealized_bf16_kv_cache_bytes': ideal_kv,
        'idealized_mlp_only_text_plus_kv_bytes': mlp_only + ideal_kv,
        'idealized_all_projection_text_plus_kv_bytes': all_proj + ideal_kv,
        'kv_context_tokens': context_tokens,
        'scenario_labels': ['untrained LittleBit MLP-only ideal bits-per-matrix-parameter',
                            'untrained LittleBit all-7-projection ideal bits-per-matrix-parameter'],
        'includes_vision_encoder': False, 'includes_runtime_overhead': False,
        'includes_littlebit_factor_padding_scales_or_residual': False,
        'measured_checkpoint_bytes': False, 'trained_checkpoint_available': False,
        'fit_in_6gib_gpu_verified': False, 'runnable_model': False,
        'source_limitations': ('This is arithmetic from config dimensions, not measured LittleBit '
                               'checkpoint size; does not establish quality, speed or residency.'),
    }


def inspect_manifest(path, cfg):
    """Classify module names only; never claim that real weights are valid."""
    if path is None:
        return {
            'classification': 'NO_CHECKPOINT_INDEX_SUPPLIED',
            'usable_littlebit_checkpoint': False,
            'validation_scope': 'metadata only; no weights were loaded',
        }
    manifest = _read_limited_json(Path(path), MAX_MANIFEST_BYTES, ManifestError)
    mapping = manifest.get('weight_map')
    if not isinstance(mapping, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
        raise ManifestError('Expected Safetensors index weight_map with string values')
    counts = dict(dense=0, packed=0, incomplete=0, absent=0)
    incomplete_preview = []
    for _, module, _ in enumerate_projection_keys(cfg):
        dense = module + '.weight' in mapping
        seen = [module + '.' + suffix in mapping for suffix in PACKED_KEYS]
        if all(seen) and not dense:
            counts['packed'] += 1
        elif dense and not any(seen):
            counts['dense'] += 1
        elif dense or any(seen):
            counts['incomplete'] += 1
            if len(incomplete_preview) < 8:
                incomplete_preview.append(module)
        else:
            counts['absent'] += 1
    if counts['dense'] == cfg.layers * 7:
        classification = 'DENSE_NOT_LITTLEBIT'
    elif counts['packed'] == cfg.layers * 7:
        classification = 'ALL_PROJECTION_KEYS_PACKED_TENSORS_UNVERIFIED'
    else:
        classification = 'PARTIAL_OR_MIXED'
    raw_size = manifest.get('metadata', {})
    declared = raw_size.get('total_size') if isinstance(raw_size, dict) else None
    if type(declared) is not int or declared < 0:
        declared = None
    return {
        'classification': classification,
        'total_projection_modules': cfg.layers * 7,
        'dense_projection_modules': counts['dense'],
        'littlebit_packed_projection_modules': counts['packed'],
        'incomplete_projection_modules': counts['incomplete'],
        'absent_projection_modules': counts['absent'],
        'incomplete_examples': incomplete_preview,
        'declared_total_checkpoint_bytes': declared,
        'usable_littlebit_checkpoint': False,
        'validation_scope': 'weight-map key names only; no binary tensors or model quality verified',
    }


def main():
    parser = argparse.ArgumentParser(
        description='Gemma 3 config, LittleBit key format, and idealized text memory audit')
    parser.add_argument('--config', type=Path, default=test_fixture_path(),
                        help='Local Gemma3 config.json; defaults to reviewed 27B reference')
    parser.add_argument('--index', type=Path, help='Optional LOCAL sharded Safetensors index; no download')
    parser.add_argument('--context', type=int, default=4096)
    parser.add_argument('--eff-bit', type=float, default=0.55)
    args = parser.parse_args()
    try:
        cfg = read_gemma_config(args.config)
        report = audit_memory(cfg, context_tokens=args.context, effective_bits=args.eff_bit)
        report['checkpoint_manifest'] = inspect_manifest(args.index, cfg)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
