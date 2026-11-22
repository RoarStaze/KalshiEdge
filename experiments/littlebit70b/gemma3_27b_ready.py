"""Gemma 3 27B-scale LittleBit checkpoint preflight and memory reservation plan.

Metadata validation (not model inference): accepts all 62 decoder layers, 434
Samsung-packed projection modules and full 27B embedding dimensions without
allocating a dense matrix or a full model. A context manager can load ONE
packed block into a bounded *private* CUDA pool for experimental use.

Private CuPy pool limits are not caps on total VRAM. Resource calculations
are conservative PLANNING reservations, not measured peak memory. There is
no trained Gemma3-27B LittleBit checkpoint, 27B decoder runtime or tokenizer.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import gc
import json
import math
from pathlib import Path

from gemma3_bridge import (Gemma3TextArchitecture, test_fixture_path,
                           read_gemma_config, inspect_manifest, projection_spec)
from safetensors_layer import (CheckpointFormatError, _ShardGroup,
                               SafetensorsLittleBitLayer, REQUIRED,
                               RESIDUAL_NAMES, DTYPES)

MIB = 1024 ** 2
GIB = 1024 ** 3
PROJECTIONS = ('self_attn.q_proj', 'self_attn.k_proj', 'self_attn.v_proj',
               'self_attn.o_proj', 'mlp.gate_proj', 'mlp.up_proj', 'mlp.down_proj')
NORM_NAMES = ('input_layernorm', 'post_attention_layernorm',
              'pre_feedforward_layernorm', 'post_feedforward_layernorm',
              'self_attn.q_norm', 'self_attn.k_norm')
FLOAT_TYPES = ('F16', 'BF16', 'F32')
PREFIX = 'language_model.model'


class CheckpointRejected(ValueError):
    """Reject malformed, dense, incomplete or overbudget checkpoints."""


@dataclass(frozen=True)
class MemoryLimits:
    """User-requested caps and *reserved* private-pool allocations.

    Do not confuse the allocations with verified whole-process peaks. The
    development prototype's KV arrays are FP32; BF16 KV must be implemented
    and measured separately before treating smaller figures as attainable.
    """
    gpu_total_gib: float = 6.0
    system_total_gib: float = 16.0
    gpu_reserve_gib: float = 1.5
    system_reserve_gib: float = 3.0
    weight_pool_mib: int = 1024
    state_pool_mib: int = 2048
    gpu_workspace_mib: int = 512
    max_host_block_mib: int = 1024
    host_workspace_mib: int = 512

    def validate(self):
        for name in ('gpu_total_gib', 'system_total_gib', 'gpu_reserve_gib', 'system_reserve_gib'):
            x = getattr(self, name)
            if type(x) not in (int, float) or not math.isfinite(x) or x <= 0 or x > 1024:
                raise ValueError(f'{name} must be a finite positive GiB quantity')
        for name in ('weight_pool_mib', 'state_pool_mib', 'gpu_workspace_mib',
                     'max_host_block_mib', 'host_workspace_mib'):
            x = getattr(self, name)
            if type(x) is not int or not 1 <= x <= 65536:
                raise ValueError(f'{name} must be a positive integer MiB quantity')
        if self.gpu_reserve_gib >= self.gpu_total_gib:
            raise ValueError('GPU reserve must be smaller than GPU budget')
        if self.system_reserve_gib >= self.system_total_gib:
            raise ValueError('System reserve must be smaller than RAM budget')
        return self


def _positive_context(cfg, context_tokens):
    if type(context_tokens) is not int or not (1 <= context_tokens <= cfg.max_positions):
        raise ValueError('Context tokens outside model position range')


def _kv_bytes(cfg, context_tokens, element_bytes=4):
    positions = sum(min(cfg.sliding_window, context_tokens) if kind == 'sliding_attention'
                    else context_tokens for kind in cfg.attention_pattern)
    return positions * 2 * cfg.kv_heads * cfg.head_dim * element_bytes


def prepare_memory_plan(cfg: Gemma3TextArchitecture, *, context_tokens=4096,
                        limits: MemoryLimits | None = None):
    """Fail closed on requested RAM/VRAM reservations and FP32 KV lower bound.

    These figures are *not* observed total memory and not proof of model fit.
    CPU hosts tied embedding in BF16-size scenario. Real checkpoint dtype
    is checked separately if checkpoint file is present.
    """
    if not isinstance(cfg, Gemma3TextArchitecture):
        raise TypeError('Expected parsed Gemma3TextArchitecture')
    _positive_context(cfg, context_tokens)
    limits = (limits or MemoryLimits()).validate()
    kv_bytes = _kv_bytes(cfg, context_tokens, 4)
    embedding_bf16 = cfg.vocab * cfg.hidden * 2
    state_cap = limits.state_pool_mib * MIB
    weight_cap = limits.weight_pool_mib * MIB
    host_block = limits.max_host_block_mib * MIB
    # KV is a persistent lower bound; reserve extra space for transient
    # arrays, scaled down for small test-sized pools.
    state_headroom = min(64 * MIB, max(MIB, state_cap // 8))
    if kv_bytes + state_headroom > state_cap:
        raise ValueError('FP32 KV cache plus headroom exceeds state pool budget')
    gpu_committed = (weight_cap + state_cap + limits.gpu_workspace_mib * MIB
                     + math.ceil(limits.gpu_reserve_gib * GIB))
    cpu_committed = (embedding_bf16 + 2 * host_block + limits.host_workspace_mib * MIB
                     + math.ceil(limits.system_reserve_gib * GIB))
    if gpu_committed > limits.gpu_total_gib * GIB:
        raise ValueError('Reserved GPU budget exceeds available GPU total')
    if cpu_committed > limits.system_total_gib * GIB:
        raise ValueError('Estimated RAM budget exceeds system total')
    return {
        'status': 'RESERVATION_PLAN_WITHIN_LIMITS_NOT_MEASURED',
        'kv_dtype': 'float32', 'context_tokens': context_tokens,
        'kv_cache_bytes': kv_bytes, 'embedding_location': 'cpu',
        'cpu_embedding_bytes': embedding_bf16,
        'max_host_block_bytes': host_block,
        'cpu_block_plus_copy_reservation_bytes': 2 * host_block,
        'weight_pool_cap_bytes': weight_cap, 'state_pool_cap_bytes': state_cap,
        'gpu_workspace_reservation_bytes': limits.gpu_workspace_mib * MIB,
        'gpu_driver_and_other_reserve_bytes': math.ceil(limits.gpu_reserve_gib * GIB),
        'cpu_other_reserve_bytes': math.ceil(limits.system_reserve_gib * GIB),
        'planned_gpu_reservation_bytes': gpu_committed,
        'planned_ram_reservation_bytes': cpu_committed,
        'gpu_capacity_bytes': math.floor(limits.gpu_total_gib * GIB),
        'system_capacity_bytes': math.floor(limits.system_total_gib * GIB),
        'actual_gpu_peak_measured': False, 'actual_ram_peak_measured': False,
        'full_model_gpu_resident': False,
        'whole_model_runnable': False,
        'note': 'Reservation plan, NOT measured process memory or guaranteed Gemma3 inference',
    }


def _shape(info, shape, key, *, dtypes):
    if info['dtype'] not in dtypes or tuple(info['shape']) not in shape:
        raise CheckpointRejected(f'Invalid tensor dtype/shape: {key}')


class LargeGemmaCheckpoint:
    """Full-dimension 27B-capable metadata preflight with lazy packed block reads.

    Does not use toy DecoderSpec. Never materializes dense full weights; only
    reads tiny factor-shape metadata during initialization. Does not certify
    checkpoint training, provenance or inference viability.
    """

    def __init__(self, path, cfg: Gemma3TextArchitecture, *,
                 max_block_bytes=1024 * MIB, max_header_budget_bytes=64 * MIB):
        if not isinstance(cfg, Gemma3TextArchitecture):
            raise TypeError('Expected Gemma3 architecture metadata')
        if (type(max_block_bytes) is not int or not 1 <= max_block_bytes <= 2 * GIB or
                type(max_header_budget_bytes) is not int or not 1 <= max_header_budget_bytes <= 256*MIB):
            raise ValueError('Invalid selected-block/header byte budget')
        self.cfg = cfg
        self.path = Path(path)
        self.max_block_bytes_budget = max_block_bytes
        self.max_header_budget_bytes = max_header_budget_bytes
        self.packed_projection_count = 0
        self.decoder_layers = cfg.layers
        self.max_layer_bytes = 0
        self.total_selected_projection_bytes = 0
        self.trained_checkpoint_verified = False
        self.runnable_model = False
        try:
            self.group = _ShardGroup(self.path)
            if self.group.single is None and len(set(self.group.weight_map.values())) > 64:
                raise CheckpointRejected('Too many shards for metadata budget')
            self._preflight(max_header_budget_bytes)
        except (CheckpointFormatError, OSError) as exc:
            raise CheckpointRejected(str(exc)) from exc

    def _descriptor(self, key):
        info = self.group.descriptor(key)
        # Enforce metadata memory during shard discovery, not only at the end.
        if self.group.single:
            header_bytes = self.group.single.data_start - 8
        else:
            header_bytes = sum(s.data_start - 8 for s in self.group._shards.values())
        if header_bytes > self.max_header_budget_bytes:
            raise CheckpointRejected('Aggregate checkpoint headers exceed metadata budget')
        return info

    def _bytes(self, key):
        i = self._descriptor(key)
        return i['data_offsets'][1] - i['data_offsets'][0]

    def _preflight(self, max_header_budget_bytes):
        if self.group.contains('lm_head.weight') or self.group.contains(PREFIX + '.lm_head.weight'):
            raise CheckpointRejected('Untied/dedicated LM head unsupported')
        embed_key = PREFIX + '.embed_tokens.weight'
        final_key = PREFIX + '.norm.weight'
        embed = self._descriptor(embed_key)
        _shape(embed, ((self.cfg.vocab, self.cfg.hidden),), embed_key, dtypes=FLOAT_TYPES)
        self.embedding_dtype = embed['dtype']
        self.embedding_bytes = self._bytes(embed_key)
        final = self._descriptor(final_key)
        _shape(final, ((self.cfg.hidden,),), final_key, dtypes=FLOAT_TYPES)
        self.final_norm_bytes = self._bytes(final_key)
        specs = projection_spec(self.cfg)
        for index in range(self.cfg.layers):
            block_size = 0
            prefix = f'{PREFIX}.layers.{index}.'
            for name in PROJECTIONS:
                op = prefix + name + '.'
                if self.group.contains(op + 'weight'):
                    raise CheckpointRejected('dense projection is not LittleBit: ' + op)
                u_shape = self.group.read(op + 'U_shape')
                v_shape = self.group.read(op + 'V_shape')
                if (u_shape.shape != (2,) or v_shape.shape != (2,) or
                        u_shape.dtype != 'int64' or v_shape.dtype != 'int64'):
                    raise CheckpointRejected('Invalid packed factor-shape metadata: ' + op)
                out, inp = specs[name]
                u_out, rank = (int(v) for v in u_shape)
                v_rank, v_in = (int(v) for v in v_shape)
                if u_out != out or v_in != inp or not 1 <= rank <= 65536 or v_rank != rank:
                    raise CheckpointRejected('LittleBit projection shape mismatch: ' + op)
                required_shapes = {
                    'U_packed': ((out, (rank + 31)//32),),
                    'V_packed': ((rank, (inp + 31)//32),),
                    'U_shape': ((2,),), 'V_shape': ((2,),),
                    'u1': ((out,), (1, out)), 'u2': ((rank,), (1, rank)),
                    'v1': ((rank,), (1, rank)), 'v2': ((inp,), (1, inp)),
                }
                for suffix in REQUIRED:
                    key = op + suffix
                    _shape(self._descriptor(key), required_shapes[suffix], key,
                           dtypes=('I32',) if suffix.endswith('_packed') else
                                  ('I64',) if suffix.endswith('_shape') else FLOAT_TYPES)
                    block_size += self._bytes(key)
                residual = [op + n for n in RESIDUAL_NAMES]
                if any(self.group.contains(x) for x in residual):
                    if not all(self.group.contains(x) for x in residual):
                        raise CheckpointRejected('Incomplete residual packed factor set: ' + op)
                    residual_shapes = {
                        'U_R_packed': required_shapes['U_packed'],
                        'V_R_packed': required_shapes['V_packed'],
                        'U_R_shape': ((2,),), 'V_R_shape': ((2,),),
                        'u1_R': required_shapes['u1'], 'u2_R': required_shapes['u2'],
                        'v1_R': required_shapes['v1'], 'v2_R': required_shapes['v2'],
                    }
                    for suffix in RESIDUAL_NAMES:
                        key = op + suffix
                        _shape(self._descriptor(key), residual_shapes[suffix], key,
                               dtypes=('I32',) if suffix.endswith('_packed') else
                                      ('I64',) if suffix.endswith('_shape') else FLOAT_TYPES)
                        block_size += self._bytes(key)
                    if (tuple(int(v) for v in self.group.read(op+'U_R_shape')) != (out, rank) or
                            tuple(int(v) for v in self.group.read(op+'V_R_shape')) != (rank, inp)):
                        raise CheckpointRejected('Residual original dimensions mismatch: '+op)
                if self.group.contains(op + 'bias'):
                    key = op + 'bias'
                    _shape(self._descriptor(key), ((out,), (1, out)), key, dtypes=FLOAT_TYPES)
                    block_size += self._bytes(key)
                self.packed_projection_count += 1
            for norm in NORM_NAMES:
                name = prefix + norm + '.weight'
                expected = self.cfg.head_dim if norm.endswith(('q_norm', 'k_norm')) else self.cfg.hidden
                _shape(self._descriptor(name), ((expected,),), name, dtypes=FLOAT_TYPES)
                block_size += self._bytes(name)
            if block_size > self.max_block_bytes_budget:
                raise CheckpointRejected(f'Packed decoder block budget exceeded: layer {index}, '
                                         f'{block_size} > {self.max_block_bytes_budget} bytes')
            self.max_layer_bytes = max(self.max_layer_bytes, block_size)
            self.total_selected_projection_bytes += block_size
        if self.packed_projection_count != self.cfg.layers * len(PROJECTIONS):
            raise CheckpointRejected('Incomplete decoder projection coverage')
        if self.group.single:
            header_bytes = self.group.single.data_start - 8
        else:
            header_bytes = sum(s.data_start - 8 for s in self.group._shards.values())
        if header_bytes > max_header_budget_bytes:
            raise CheckpointRejected('Aggregate checkpoint headers exceed metadata budget')

    def load_cpu_block_factors(self, index):
        """Materialize seven *packed* projections for just one decoder block.

        The caller owns returned tensors; do not retain many blocks at once.
        Cap is selected original tensor bytes, NOT total Python RSS.
        """
        if type(index) is not int or not 0 <= index < self.cfg.layers:
            raise ValueError('Decoder block index outside model')
        result = {}
        for name in PROJECTIONS:
            prefix = f'{PREFIX}.layers.{index}.{name}'
            try:
                result[name] = SafetensorsLittleBitLayer.from_file(
                    self.path, prefix, max_layer_bytes=self.max_block_bytes_budget,
                    _shard_group=self.group)
            except CheckpointFormatError as exc:
                raise CheckpointRejected(str(exc)) from exc
        return result

    @contextmanager
    def cuda_block(self, index, *, pool_limit_bytes=1024*MIB):
        """Load ONE packed decoder block in a capped private CuPy pool.

        On exit invalidate the result dict and ensure private pool has zero
        live allocations. Does not bound other CUDA allocations or KV caches.
        """
        if type(pool_limit_bytes) is not int or not 1 <= pool_limit_bytes <= 4*GIB:
            raise ValueError('Invalid private CUDA pool limit')
        from cuda_packed import _cupy
        cp = _cupy()
        pool = cp.cuda.MemoryPool()
        pool.set_limit(size=pool_limit_bytes)
        cuda_ops = {}
        try:
            factors = self.load_cpu_block_factors(index)
            try:
                with cp.cuda.using_allocator(pool.malloc):
                    cuda_ops.update({name: layer.to_cuda_layer(backend='warp')
                                     for name, layer in factors.items()})
                yield cuda_ops, pool
            except cp.cuda.memory.OutOfMemoryError as exc:
                raise MemoryError('CUDA packed decoder block exceeded private pool limit') from exc
        finally:
            cp.cuda.get_current_stream().synchronize()
            cuda_ops.clear()
            gc.collect()
            pool.free_all_blocks()
            if pool.used_bytes() != 0:
                raise MemoryError('GPU packed allocations still live after block eviction')


def build_report(cfg, *, context_tokens=4096, checkpoint=None,
                 manifest=None, limits=None, max_block_bytes=None):
    """Build honest readiness report: metadata can pass, inference remains blocked."""
    if checkpoint is not None and manifest is not None:
        raise ValueError('Use either local checkpoint path or metadata index, not both')
    limits = (limits or MemoryLimits()).validate()
    memory = prepare_memory_plan(cfg, context_tokens=context_tokens, limits=limits)
    result = {
        'status': 'PLAN_ONLY_NO_CHECKPOINT', 'model': 'Gemma 3 text',
        'decoder_layers': cfg.layers, 'projection_count': cfg.layers * 7,
        'matches_target_27b_architecture': cfg.matches_target_27b,
        'trained_checkpoint_verified': False, 'runnable_model': False,
        'full_27b_inference_implemented': False,
        'checkpoint': None, 'memory': memory,
    }
    if manifest is not None:
        result['checkpoint'] = inspect_manifest(manifest, cfg)
        result['status'] = 'MANIFEST_METADATA_ONLY_NOT_RUNNABLE'
    elif checkpoint is not None:
        limit = max_block_bytes if max_block_bytes is not None else limits.max_host_block_mib * MIB
        check = LargeGemmaCheckpoint(checkpoint, cfg, max_block_bytes=limit)
        # Conservative private pool planning; packed factor GPU arrays and
        # FP32 scales can exceed original F16 checkpoint bytes.
        conservative_weight_bytes = 4 * check.max_layer_bytes + 16*MIB
        if conservative_weight_bytes > memory['weight_pool_cap_bytes']:
            raise CheckpointRejected('Decoder block estimated GPU pool use exceeds weight pool cap')
        if check.max_layer_bytes > memory['max_host_block_bytes']:
            raise CheckpointRejected('Decoder block exceeds host staging reservation')
        # Actual embedding may be F32 instead of planned BF16; adjust the
        # RAM bound before reporting the checkpoint metadata compatible.
        actual_cpu_reservation = (memory['planned_ram_reservation_bytes'] -
                                  memory['cpu_embedding_bytes'] + check.embedding_bytes)
        if actual_cpu_reservation > memory['system_capacity_bytes']:
            raise CheckpointRejected('Actual checkpoint embedding would exceed RAM reservation')
        memory['actual_embedding_bytes'] = check.embedding_bytes
        memory['planned_ram_reservation_bytes_with_checkpoint'] = actual_cpu_reservation
        result['status'] = 'PACKED_CHECKPOINT_STRUCTURALLY_VALID'
        result['checkpoint'] = {
            'path': str(check.path), 'decoder_layers': check.decoder_layers,
            'packed_projections': check.packed_projection_count,
            'max_block_bytes': check.max_layer_bytes,
            'total_selected_decoder_tensor_bytes': check.total_selected_projection_bytes,
            'embedding_tensor_bytes': check.embedding_bytes,
            'embedding_dtype': check.embedding_dtype,
            'quality_verified': False, 'training_verified': False,
            'full_text_generation_implemented': False,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description='27B-size Samsung LittleBit-2 model planning, no weights downloaded')
    parser.add_argument('--config', type=Path, default=test_fixture_path())
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--checkpoint', type=Path, help='Local packed Safetensors file or sharded directory')
    group.add_argument('--index', type=Path, help='Local manifest JSON only; no weights opened')
    parser.add_argument('--context', type=int, default=4096)
    parser.add_argument('--gpu-gib', type=float, default=6)
    parser.add_argument('--ram-gib', type=float, default=16)
    parser.add_argument('--weight-pool-mib', type=int, default=1024)
    parser.add_argument('--state-pool-mib', type=int, default=2048)
    parser.add_argument('--max-block-mib', type=int, default=1024)
    args = parser.parse_args()
    try:
        cfg = read_gemma_config(args.config)
        limits = MemoryLimits(gpu_total_gib=args.gpu_gib, system_total_gib=args.ram_gib,
                              weight_pool_mib=args.weight_pool_mib, state_pool_mib=args.state_pool_mib,
                              max_host_block_mib=args.max_block_mib)
        report = build_report(cfg, context_tokens=args.context, checkpoint=args.checkpoint,
                              manifest=args.index, limits=limits)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
