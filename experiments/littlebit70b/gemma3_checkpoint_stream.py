"""Bounded real-*format* Gemma3 LittleBit checkpoint model-level streaming.

Supports a tiny six-decoder text-model *execution skeleton* with Samsung's
actual U_packed/V_packed factor keys and Hugging Face Gemma3 tensor names.
Loads only ONE decoder block's packed weights on GPU at a time, keeps each
block's KV cache on GPU, and evicts packed decoder allocations between blocks.

NOT a trained model, an actual 27B loader, full-model VRAM bound or tokenizer.
This module is intentionally bounded by the tiny DecoderSpec constraints.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import gc
import json
import math
from pathlib import Path
import struct
from tempfile import TemporaryDirectory

import numpy as np

from gemma3_decoder import DecoderSpec, Gemma3KVCache, Gemma3PackedDecoder, layer_kind, rms_norm
from safetensors_layer import _ShardGroup, SafetensorsLittleBitLayer, CheckpointFormatError, REQUIRED, RESIDUAL_NAMES

MODEL_PREFIX = 'language_model.model'
PROJECTIONS = ('self_attn.q_proj', 'self_attn.k_proj', 'self_attn.v_proj', 'self_attn.o_proj',
               'mlp.gate_proj', 'mlp.up_proj', 'mlp.down_proj')
NORM_KEYS = {'input_layernorm': 'input_layernorm',
             'post_attention_layernorm': 'post_attention_layernorm',
             'pre_feedforward_layernorm': 'pre_feedforward_layernorm',
             'post_feedforward_layernorm': 'post_feedforward_layernorm',
             'q_norm': 'self_attn.q_norm', 'k_norm': 'self_attn.k_norm'}
FLOAT_TYPES = ('F32', 'F16', 'BF16')
EMBED_KEY = MODEL_PREFIX + '.embed_tokens.weight'
FINAL_NORM_KEY = MODEL_PREFIX + '.norm.weight'


def _verify_size(value, label, *, lower=1, upper=512 * 1024 * 1024):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f'{label} must be an integer in {lower}..{upper}')
    return value


def _bytes_in(group, key):
    info = group.descriptor(key)
    start, end = info['data_offsets']
    return end - start


def _read_float(group, key, shape):
    info = group.descriptor(key)
    if info['dtype'] not in FLOAT_TYPES or tuple(info['shape']) != tuple(shape):
        raise CheckpointFormatError(f'Invalid shape/dtype for norm or embedding tensor {key}')
    arr = group.read(key).astype(np.float32, copy=False)
    if not np.isfinite(arr).all():
        raise CheckpointFormatError('Non-finite norm/embedding tensor ' + key)
    return arr


class Gemma3CheckpointReader:
    """Read real-format packed Gemma3 tensors without full checkpoint loading.

    Preflights ALL requested decoder block metadata before any model state is
    updated. Model-wide CPU memory and raw file size are not comprehensively
    bounded here; selected per-block data and embedding bytes are capped.
    """

    def __init__(self, path, cfg: DecoderSpec, layer_count: int, *,
                 max_block_bytes=8 * 1024 * 1024,
                 max_embedding_bytes=4 * 1024 * 1024):
        if not isinstance(cfg, DecoderSpec):
            raise TypeError('Expected bounded DecoderSpec')
        _verify_size(layer_count, 'layer_count', upper=12)
        _verify_size(max_block_bytes, 'max_block_bytes')
        _verify_size(max_embedding_bytes, 'max_embedding_bytes')
        self.path = Path(path)
        self.cfg = cfg
        self.layer_count = layer_count
        self.max_block_bytes = max_block_bytes
        self.max_embedding_bytes = max_embedding_bytes
        self.group = _ShardGroup(path)
        # This marker is only an assertion by the file author, not validation
        # of whether any arbitrary checkpoint is genuinely trained.
        if self.group.single:
            meta = self.group.single.index.get('__metadata__', {})
        else:
            meta = json.loads((self.path / 'model.safetensors.index.json').read_text()).get('metadata', {})
        self.synthetic = isinstance(meta, dict) and meta.get('synthetic') == 'true'
        if self.group.contains('lm_head.weight'):
            raise CheckpointFormatError('Untied lm_head.weight is not supported')
        embedding = self.group.descriptor(EMBED_KEY)
        if (embedding['dtype'] not in FLOAT_TYPES or len(embedding['shape']) != 2
                or embedding['shape'][1] != cfg.hidden
                or not 2 <= embedding['shape'][0] <= 256):
            raise CheckpointFormatError('Invalid or oversized Gemma embedding tensor')
        self.vocab_size = embedding['shape'][0]
        embed_bytes = _bytes_in(self.group, EMBED_KEY) + _bytes_in(self.group, FINAL_NORM_KEY)
        if embed_bytes > max_embedding_bytes:
            raise CheckpointFormatError('Gemma embedding tensor budget exceeded')
        _read_float(self.group, FINAL_NORM_KEY, (cfg.hidden,))
        self.total_projection_bytes = 0
        self.max_block_bytes_seen = 0
        for index in range(layer_count):
            layer_bytes = 0
            prefix = self._layer_prefix(index)
            for name, (expected_in, expected_out) in self._projection_shapes().items():
                module = prefix + name + '.'
                if self.group.contains(module + 'weight'):
                    raise CheckpointFormatError('Ordinary dense projection cannot replace packed LittleBit factors: ' + module)
                names = [module + suffix for suffix in REQUIRED]
                extra = [module + suffix for suffix in RESIDUAL_NAMES]
                if any(self.group.contains(x) for x in extra):
                    if not all(self.group.contains(x) for x in extra):
                        raise CheckpointFormatError('Incomplete LittleBit residual factors: ' + module)
                    names.extend(extra)
                if self.group.contains(module + 'bias'):
                    names.append(module + 'bias')
                # Check every required key and original shape WITHOUT reading
                # large factor tensors into memory at preflight.
                for key in names:
                    layer_bytes += _bytes_in(self.group, key)
                u_shape = self.group.read(module + 'U_shape')
                v_shape = self.group.read(module + 'V_shape')
                if (u_shape.dtype != np.dtype('int64') or v_shape.dtype != np.dtype('int64')
                        or u_shape.shape != (2,) or v_shape.shape != (2,)):
                    raise CheckpointFormatError('Malformed LittleBit shape tensors: ' + module)
                u_out, u_rank = map(int, u_shape)
                v_rank, v_in = map(int, v_shape)
                if u_out != expected_out or v_in != expected_in or u_rank != v_rank or u_rank <= 0:
                    raise CheckpointFormatError('Packed projection shape mismatch: ' + module)
                if (tuple(self.group.descriptor(module + 'U_packed')['shape'])
                        != (expected_out, (u_rank+31)//32)
                        or tuple(self.group.descriptor(module + 'V_packed')['shape'])
                        != (u_rank, (expected_in+31)//32)):
                    raise CheckpointFormatError('Packed factor tensor dimension mismatch: ' + module)
                # Up-front metadata verification: malformed later layers must
                # fail before any streaming step mutates earlier KV caches.
                for tag, rows, columns in (
                    ('U_packed', expected_out, (u_rank + 31)//32),
                    ('V_packed', u_rank, (expected_in + 31)//32),
                    ('U_shape', 2, None), ('V_shape', 2, None),
                ):
                    entry = self.group.descriptor(module + tag)
                    expected_shape = (rows,) if columns is None else (rows, columns)
                    expected_dtype = 'I64' if tag.endswith('_shape') else 'I32'
                    if entry['dtype'] != expected_dtype or tuple(entry['shape']) != expected_shape:
                        raise CheckpointFormatError('Invalid packed tensor metadata: ' + module + tag)
                expected_scales = {'u1': expected_out, 'u2': u_rank,
                                   'v1': u_rank, 'v2': expected_in}
                for tag, length in expected_scales.items():
                    entry = self.group.descriptor(module + tag)
                    if entry['dtype'] not in FLOAT_TYPES or tuple(entry['shape']) not in ((length,), (1, length)):
                        raise CheckpointFormatError('Invalid packed scaling tensor metadata: ' + module + tag)
                if self.group.contains(module + 'bias'):
                    entry = self.group.descriptor(module + 'bias')
                    if entry['dtype'] not in FLOAT_TYPES or tuple(entry['shape']) not in (
                            (expected_out,), (1, expected_out)):
                        raise CheckpointFormatError('Invalid packed bias metadata: ' + module)
                if any(self.group.contains(x) for x in extra):
                    for key in extra:
                        entry = self.group.descriptor(key)
                        if entry['dtype'] not in ('I32', 'I64', *FLOAT_TYPES):
                            raise CheckpointFormatError('Invalid residual factor dtype: ' + key)
                self.total_projection_bytes += sum(_bytes_in(self.group, key) for key in names)
            for suffix, expected in self._norm_shapes().items():
                key = prefix + NORM_KEYS[suffix] + '.weight'
                info = self.group.descriptor(key)
                if info['dtype'] not in FLOAT_TYPES or tuple(info['shape']) != (expected,):
                    raise CheckpointFormatError('Invalid norm tensor shape/dtype: ' + key)
                layer_bytes += _bytes_in(self.group, key)
                _read_float(self.group, key, (expected,))
            if layer_bytes > max_block_bytes:
                raise CheckpointFormatError('Gemma decoder block tensor budget exceeded')
            self.max_block_bytes_seen = max(self.max_block_bytes_seen, layer_bytes)

    def _projection_shapes(self):
        cfg = self.cfg
        h, m, q, kv = cfg.hidden, cfg.intermediate, cfg.q_heads*cfg.head_dim, cfg.kv_heads*cfg.head_dim
        return {'self_attn.q_proj': (h, q), 'self_attn.k_proj': (h, kv),
                'self_attn.v_proj': (h, kv), 'self_attn.o_proj': (q, h),
                'mlp.gate_proj': (h, m), 'mlp.up_proj': (h, m),
                'mlp.down_proj': (m, h)}

    def _norm_shapes(self):
        return {name: (self.cfg.head_dim if name in ('q_norm', 'k_norm') else self.cfg.hidden)
                for name in NORM_KEYS}

    def _layer_prefix(self, index):
        if type(index) is not int or not 0 <= index < self.layer_count:
            raise ValueError('Decoder layer index out of range')
        return f'{MODEL_PREFIX}.layers.{index}.'

    def projection_prefixes(self, index):
        prefix = self._layer_prefix(index)
        return {name: prefix + name for name in PROJECTIONS}

    def read_embed_and_final_norm(self):
        # Already validated size, dtype and shape in __init__.
        emb = _read_float(self.group, EMBED_KEY, (self.vocab_size, self.cfg.hidden))
        norm = _read_float(self.group, FINAL_NORM_KEY, (self.cfg.hidden,))
        return emb, norm

    def _block_components(self, index):
        prefix = self._layer_prefix(index)
        # Upstream packed loader verifies scales, residuals, word layout etc.
        packed = {name: SafetensorsLittleBitLayer.from_file(
            self.path, prefix + name, max_layer_bytes=self.max_block_bytes,
            _shard_group=self.group) for name in PROJECTIONS}
        norms = {_name: _read_float(self.group, prefix + suffix + '.weight',
                                   (self._norm_shapes()[_name],))
                 for _name, suffix in NORM_KEYS.items()}
        return packed, norms

    def load_cpu_block(self, index):
        packed, norms = self._block_components(index)
        return Gemma3PackedDecoder(self.cfg, {name: op.to_cpu_layer() for name, op in packed.items()}, norms)

    def load_cuda_block(self, index):
        from gemma3_cuda_decoder import CUDAGemma3PackedDecoder
        packed, norms = self._block_components(index)
        return CUDAGemma3PackedDecoder(self.cfg,
                                       {name: op.to_cuda_layer(backend='warp') for name, op in packed.items()},
                                       norms)


@dataclass
class StreamState:
    owner: int
    position: int
    caches: list


class StreamedTinyGemma3:
    """Reload each decoder block from disk for each token, keep K/V on device.

    Capped private CuPy pool for decoder weight allocations and a second pool
    for embeddings, activations and persistent KV cache. These caps DO NOT
    cover runtime, driver, kernel compilation or other GPU allocations.
    """

    def __init__(self, path, cfg: DecoderSpec, layer_count, *, backend='cpu', max_tokens=32,
                 max_block_bytes=8*1024*1024, max_embedding_bytes=4*1024*1024,
                 weight_pool_bytes=16*1024*1024, state_pool_bytes=16*1024*1024):
        if backend not in ('cpu', 'cuda'):
            raise ValueError('backend must be cpu or cuda')
        _verify_size(max_tokens, 'max_tokens', upper=256)
        _verify_size(weight_pool_bytes, 'weight_pool_bytes')
        _verify_size(state_pool_bytes, 'state_pool_bytes')
        self.reader = Gemma3CheckpointReader(path, cfg, layer_count,
            max_block_bytes=max_block_bytes, max_embedding_bytes=max_embedding_bytes)
        self.cfg = cfg
        self.backend = backend
        self.max_tokens = max_tokens
        self.layer_count = layer_count
        self.kinds = [layer_kind(i) for i in range(layer_count)]
        self.embed, self.final_norm = self.reader.read_embed_and_final_norm()
        self.vocab_size = self.reader.vocab_size
        self.cp = None
        self.weight_pool = None
        self.state_pool = None
        self.weight_pool_peak = 0
        self.state_pool_peak = 0
        self.loaded_block_count = 0
        if backend == 'cuda':
            from cuda_packed import _cupy
            cp = _cupy()
            self.cp = cp
            self.weight_pool = cp.cuda.MemoryPool()
            self.state_pool = cp.cuda.MemoryPool()
            self.weight_pool.set_limit(size=weight_pool_bytes)
            self.state_pool.set_limit(size=state_pool_bytes)
            with cp.cuda.using_allocator(self.state_pool.malloc):
                self.device_embed = cp.asarray(self.embed, dtype=cp.float32)
                self.device_norm = cp.asarray(self.final_norm, dtype=cp.float32)

    def new_state(self):
        if self.backend == 'cuda':
            from gemma3_cuda_decoder import CUDAGemma3KVCache
            caches = [CUDAGemma3KVCache(kind, self.cfg.sliding_window, max_tokens=self.max_tokens)
                      for kind in self.kinds]
        else:
            caches = [Gemma3KVCache(kind, self.cfg.sliding_window) for kind in self.kinds]
        return StreamState(id(self), 0, caches)

    def _validate_state(self, state):
        if not isinstance(state, StreamState) or state.owner != id(self):
            raise ValueError('Stream state belongs to another model')
        if state.position >= self.max_tokens:
            raise ValueError('Token context exceeded configured model limit')
        if len(state.caches) != self.layer_count:
            raise ValueError('Decoder cache count mismatch')
        for kind, cache in zip(self.kinds, state.caches):
            if cache.kind != kind or cache.window != self.cfg.sliding_window:
                raise ValueError('Decoder cache mode/window mismatch')
            if self.backend == 'cuda':
                cache.validate_next(state.position)
            elif (cache.positions[-1]+1 if cache.positions else 0) != state.position:
                raise ValueError('Decoder cache position mismatch')

    def step(self, token_id, state):
        if type(token_id) is not int or not 0 <= token_id < self.vocab_size:
            raise ValueError('Invalid synthetic token ID')
        self._validate_state(state)
        cp = self.cp
        if cp is None:
            hidden = self.embed[token_id] * np.float32(math.sqrt(self.cfg.hidden))
        else:
            try:
                with cp.cuda.using_allocator(self.state_pool.malloc):
                    hidden = self.device_embed[token_id] * cp.float32(math.sqrt(self.cfg.hidden))
            except cp.cuda.memory.OutOfMemoryError as exc:
                raise MemoryError('Persistent CUDA state pool exhausted') from exc
        for index, cache in enumerate(state.caches):
            if cp is None:
                decoder = self.reader.load_cpu_block(index)
                hidden = decoder.forward_token(hidden, state.position, cache)
                decoder = None
            else:
                decoder = None
                try:
                    with cp.cuda.using_allocator(self.weight_pool.malloc):
                        decoder = self.reader.load_cuda_block(index)
                        self.weight_pool_peak = max(self.weight_pool_peak, int(self.weight_pool.total_bytes()))
                    with cp.cuda.using_allocator(self.state_pool.malloc):
                        hidden = decoder.forward_token(hidden, state.position, cache)
                        self.state_pool_peak = max(self.state_pool_peak, int(self.state_pool.total_bytes()))
                except cp.cuda.memory.OutOfMemoryError as exc:
                    raise MemoryError('Private CUDA weight/state allocation limit exhausted') from exc
                finally:
                    cp.cuda.get_current_stream().synchronize()
                    decoder = None
                    gc.collect()
                    self.weight_pool.free_all_blocks()
                    if self.weight_pool.used_bytes() != 0:
                        raise RuntimeError('Packed decoder GPU weight allocations not released after block')
            self.loaded_block_count += 1
        if cp is None:
            logits = rms_norm(hidden, self.final_norm, self.cfg.eps) @ self.embed.T
        else:
            from gemma3_cuda_decoder import _rmsnorm_cuda
            try:
                with cp.cuda.using_allocator(self.state_pool.malloc):
                    logits = _rmsnorm_cuda(cp, hidden, self.device_norm, self.cfg.eps) @ self.device_embed.T
                    self.state_pool_peak = max(self.state_pool_peak, int(self.state_pool.total_bytes()))
            except cp.cuda.memory.OutOfMemoryError as exc:
                raise MemoryError('Persistent CUDA state pool exhausted when computing logits') from exc
        state.position += 1
        return logits

    def prefill(self, prompt):
        self._check_prompt(prompt)
        state = self.new_state()
        output = None
        for token in prompt:
            output = self.step(token, state)
        return output, state

    def _check_prompt(self, prompt, new=0):
        if not isinstance(prompt, (list, tuple)) or not prompt or any(
                type(t) is not int or t < 0 or t >= self.vocab_size for t in prompt):
            raise ValueError('Nonempty valid synthetic token ID list required')
        if type(new) is not int or new < 0 or len(prompt) + new > self.max_tokens:
            raise ValueError('Requested token count exceeds bounded context')

    def generate(self, prompt, *, max_new_tokens=2):
        if type(max_new_tokens) is not int or not 1 <= max_new_tokens <= 16:
            raise ValueError('max_new_tokens must be in 1..16')
        self._check_prompt(prompt, new=max_new_tokens)
        start_count = self.loaded_block_count
        logits, state = self.prefill(prompt)
        out = []
        for _ in range(max_new_tokens):
            token = int(np.argmax(logits)) if self.cp is None else int(self.cp.argmax(logits).item())
            out.append(token)
            logits = self.step(token, state)
        return {
            'prompt_token_ids': list(prompt), 'generated_token_ids': out,
            'tokens_consumed': state.position,
            'loaded_block_count': self.loaded_block_count - start_count,
            'decoder_blocks': self.layer_count, 'backend': self.backend,
            'checkpoint': 'SYNTHETIC_ONLY' if self.reader.synthetic else 'UNVERIFIED_LOCAL_CHECKPOINT',
            'trained_model': False, 'whole_llm': False,
            'text_generation_available': False, 'tokenizer_available': False,
            'cuda_executed': self.cp is not None,
            'weight_pool_peak_bytes': self.weight_pool_peak if self.cp is not None else None,
            'state_pool_peak_bytes': self.state_pool_peak if self.cp is not None else None,
            'weights_live_after_step_bytes': (int(self.weight_pool.used_bytes()) if self.cp is not None else None),
            'total_device_vram_peak_measured': False,
        }


def _serialize_safetensors(destination, tensors):
    """Write toy tensor data with the standard safetensors header layout."""
    if destination.exists():
        raise FileExistsError('Refusing to overwrite an existing checkpoint')
    header = {'__metadata__': {'synthetic': 'true', 'purpose': 'untrained Tiny Gemma test only'}}
    payload = bytearray()
    for name, value in tensors.items():
        arr = np.ascontiguousarray(value)
        dtype = {np.dtype('float32'): 'F32', np.dtype('int32'): 'I32',
                 np.dtype('int64'): 'I64'}.get(arr.dtype)
        if dtype is None:
            raise ValueError('Unsupported synthetic tensor dtype ' + str(arr.dtype))
        begin = len(payload)
        payload.extend(arr.tobytes())
        header[name] = {'dtype': dtype, 'shape': list(arr.shape),
                        'data_offsets': [begin, len(payload)]}
    encoded = json.dumps(header, separators=(',', ':')).encode('utf8')
    encoded += b' ' * (-len(encoded) % 8)
    if len(encoded) + len(payload) > 16*1024*1024:
        raise ValueError('Synthetic checkpoint cannot exceed 16 MiB')
    destination.write_bytes(struct.pack('<Q', len(encoded)) + encoded + payload)


def write_synthetic_checkpoint(path, model, *, sharded=False):
    """Create testing-only checkpoint with real Gemma3 + Samsung keys.

    Only supports TinyPackedLM with CPU synthetic untrained factor weights.
    Never converts original dense pretrained 27B weights.
    """
    from gemma3_tiny_lm import TinyPackedLM
    if not isinstance(model, TinyPackedLM) or model.backend != 'cpu':
        raise TypeError('Expected synthetic CPU TinyPackedLM')
    path = Path(path)
    tensors_by_group = []
    tensors_by_group.append({EMBED_KEY: model.embedding.astype('<f4'),
                             FINAL_NORM_KEY: model.final_norm.astype('<f4')})
    for i, block in enumerate(model.cpu_layers):
        prefix = f'{MODEL_PREFIX}.layers.{i}.'
        group = {}
        for name, op in block.projections.items():
            root = prefix + name + '.'
            group[root + 'U_packed'] = np.asarray(op.u.rows, dtype='<i4')
            group[root + 'V_packed'] = np.asarray(op.v.rows, dtype='<i4')
            group[root + 'U_shape'] = np.asarray(op.u.shape, dtype='<i8')
            group[root + 'V_shape'] = np.asarray(op.v.shape, dtype='<i8')
            for part in ('u1', 'u2', 'v1', 'v2'):
                group[root + part] = np.asarray([getattr(op, part)], dtype='<f4')
            if op.bias is not None:
                group[root + 'bias'] = np.asarray(op.bias, dtype='<f4')
            if op.residual is not None:
                raise ValueError('Synthetic writer does not yet support residual factors')
        for short, suffix in NORM_KEYS.items():
            group[prefix + suffix + '.weight'] = np.asarray(block.norms[short], dtype='<f4')
        tensors_by_group.append(group)
    if sharded:
        path.mkdir(parents=True, exist_ok=False)
        weight_map = {}
        for index, tensors in enumerate(tensors_by_group):
            name = f'synthetic-{index:05d}.safetensors'
            _serialize_safetensors(path / name, tensors)
            for key in tensors:
                weight_map[key] = name
        (path / 'model.safetensors.index.json').write_text(
            json.dumps({'metadata': {'synthetic': 'true'}, 'weight_map': weight_map}, indent=2))
    else:
        combined = {}
        for group in tensors_by_group:
            combined.update(group)
        _serialize_safetensors(path, combined)
    return path


def demo(*, backend='cuda'):
    from gemma3_tiny_lm import make_demo_model
    if backend not in ('cpu', 'cuda'):
        raise ValueError('backend must be cpu or cuda')
    with TemporaryDirectory(prefix='gemma3-packed-checkpoint-') as root:
        path = Path(root) / 'SYNTHETIC_ONLY.safetensors'
        model = make_demo_model(backend='cpu', depth=6, seed=19, max_tokens=16)
        write_synthetic_checkpoint(path, model)
        loaded = StreamedTinyGemma3(path, model.config, 6, backend=backend, max_tokens=16)
        prompt = [1, 4, 7]
        cpu_reference, _ = model.prefill(prompt)
        test_logits, _ = loaded.prefill(prompt)
        if backend == 'cuda':
            observation = test_logits.get()
            err = float(np.max(np.abs(cpu_reference - observation)))
            if not np.allclose(observation, cpu_reference, rtol=5e-4, atol=5e-4):
                raise AssertionError('Checkpoint-loaded CUDA logits do not match CPU reference')
        else:
            err = float(np.max(np.abs(cpu_reference - test_logits)))
            if not np.allclose(test_logits, cpu_reference, rtol=1e-5, atol=1e-5):
                raise AssertionError('Checkpoint-loaded CPU logits do not match in-memory CPU')
        loaded = StreamedTinyGemma3(path, model.config, 6, backend=backend, max_tokens=16)
        result = loaded.generate(prompt, max_new_tokens=2)
        expected_tokens = model.generate(prompt, max_new_tokens=2)['generated_token_ids']
        if result['generated_token_ids'] != expected_tokens:
            raise AssertionError('Checkpoint-loaded token IDs differ from source model')
        result.update({
            'status': 'GEMMA3_PACKED_CHECKPOINT_STREAM_OK',
            'max_abs_logit_error': err,
            'checkpoint_file_bytes': path.stat().st_size,
            'projected_shapes_verified': True,
            'per_token_decoder_weight_reloads': 6,
            'all_decoder_weights_resident_on_gpu': False,
        })
        if backend == 'cuda':
            cp = loaded.cp
            name = cp.cuda.runtime.getDeviceProperties(cp.cuda.runtime.getDevice())['name']
            result['device'] = name.decode('utf8', errors='replace') if isinstance(name, bytes) else str(name)
        return result


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description='Synthetic Gemma3 LittleBit full-block Safetensors streaming')
    cli.add_argument('--backend', choices=['cpu', 'cuda'], default='cuda')
    args = cli.parse_args()
    print(json.dumps(demo(backend=args.backend), indent=2))
