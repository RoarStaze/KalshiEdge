"""Budget and checkpoint validation for a Gemma3-27B-scale metadata loader."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemma3_bridge import read_gemma_config, test_fixture_path
from gemma3_tiny_lm import make_demo_model
from gemma3_checkpoint_stream import write_synthetic_checkpoint
from cuda_packed import cuda_is_available
from gemma3_27b_ready import (MemoryLimits, prepare_memory_plan, LargeGemmaCheckpoint,
                              CheckpointRejected, build_report)


def _tiny_arch():
    from gemma3_bridge import Gemma3TextArchitecture
    return Gemma3TextArchitecture(hidden=8, intermediate=12, layers=6,
        q_heads=4, kv_heads=2, head_dim=4, vocab=32, sliding_window=3,
        window_pattern=6, max_positions=64, has_vision_encoder=False)


class BudgetPlannerTests(unittest.TestCase):
    def test_real_27b_config_is_accepted_without_allocating_model(self):
        cfg = read_gemma_config(test_fixture_path())
        self.assertTrue(cfg.matches_target_27b)
        report = build_report(cfg, context_tokens=4096)
        self.assertFalse(report['runnable_model'])
        self.assertEqual(report['status'], 'PLAN_ONLY_NO_CHECKPOINT')
        self.assertEqual(report['decoder_layers'], 62)
        self.assertEqual(report['projection_count'], 434)
        self.assertEqual(report['memory']['kv_dtype'], 'float32')
        self.assertGreater(report['memory']['kv_cache_bytes'], 1_000_000_000)
        self.assertFalse(report['memory']['actual_gpu_peak_measured'])
        self.assertFalse(report['memory']['actual_ram_peak_measured'])
        self.assertEqual(report['memory']['embedding_location'], 'cpu')
        self.assertFalse(report['trained_checkpoint_verified'])

    def test_kv_cache_estimate_matches_causal_local_and_global_storage(self):
        cfg = read_gemma_config(test_fixture_path())
        result = prepare_memory_plan(cfg, context_tokens=4096)
        expected = (52 * 1024 + 10 * 4096) * 2 * 16 * 128 * 4
        self.assertEqual(result['kv_cache_bytes'], expected)
        self.assertGreater(result['cpu_embedding_bytes'], 2 * 1024 ** 3)

    def test_rejects_higher_context_than_budget_allows(self):
        cfg = read_gemma_config(test_fixture_path())
        with self.assertRaisesRegex(ValueError, 'KV cache|state pool|GPU budget'):
            prepare_memory_plan(cfg, context_tokens=16384)

    def test_rejects_impossible_ram_or_vram_and_nonfinite_limits(self):
        cfg = read_gemma_config(test_fixture_path())
        for kw in ({'gpu_total_gib': 2}, {'system_total_gib': 4},
                   {'gpu_total_gib': True}, {'gpu_total_gib': float('nan')},
                   {'weight_pool_mib': 0}, {'max_host_block_mib': -1},
                   {'state_pool_mib': 1}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                prepare_memory_plan(cfg, context_tokens=4096, limits=MemoryLimits(**kw))

    def test_context_checked_before_any_file_access(self):
        cfg = read_gemma_config(test_fixture_path())
        with self.assertRaises(ValueError):
            build_report(cfg, context_tokens=0, checkpoint=Path('nonexistent'))


class CheckpointStreamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'synthetic.safetensors'
        self.original = make_demo_model(backend='cpu', depth=6, seed=19, max_tokens=16)
        write_synthetic_checkpoint(self.path, self.original)
        self.cfg = _tiny_arch()
        self.limits = MemoryLimits(weight_pool_mib=64, state_pool_mib=64,
                                   max_host_block_mib=64, gpu_workspace_mib=64)

    def test_preflight_uses_actual_layer_data_without_loading_all_layers(self):
        checkpoint = LargeGemmaCheckpoint(self.path, self.cfg, max_block_bytes=4*1024*1024)
        self.assertEqual(checkpoint.decoder_layers, 6)
        self.assertEqual(checkpoint.packed_projection_count, 42)
        self.assertEqual(checkpoint.max_layer_bytes > 0, True)
        self.assertEqual(checkpoint.embedding_dtype, 'F32')
        self.assertFalse(checkpoint.trained_checkpoint_verified)
        self.assertFalse(checkpoint.runnable_model)
        block = checkpoint.load_cpu_block_factors(4)
        self.assertEqual(len(block), 7)
        self.assertEqual(block['mlp.gate_proj'].u_shape, (12, 4))
        self.assertEqual(block['mlp.down_proj'].v_shape, (4, 12))

    def test_sharded_checkpoint_preflight_and_one_block_read(self):
        directory = Path(self.tmp.name) / 'shards'
        write_synthetic_checkpoint(directory, self.original, sharded=True)
        scanner = LargeGemmaCheckpoint(directory, self.cfg, max_block_bytes=4*1024*1024)
        self.assertEqual(scanner.packed_projection_count, 42)
        self.assertEqual(len(scanner.load_cpu_block_factors(0)), 7)

    def test_rejects_real_dense_gemma_projections(self):
        directory = Path(self.tmp.name) / 'shards'
        write_synthetic_checkpoint(directory, self.original, sharded=True)
        index = directory / 'model.safetensors.index.json'
        manifest = json.loads(index.read_text())
        manifest['weight_map']['language_model.model.layers.0.mlp.up_proj.weight'] = 'synthetic-00001.safetensors'
        index.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(CheckpointRejected, 'dense'):
            LargeGemmaCheckpoint(directory, self.cfg)

    def test_rejects_truncated_missing_late_projection(self):
        directory = Path(self.tmp.name) / 'shards'
        write_synthetic_checkpoint(directory, self.original, sharded=True)
        index = directory / 'model.safetensors.index.json'
        manifest = json.loads(index.read_text())
        del manifest['weight_map']['language_model.model.layers.5.mlp.down_proj.U_packed']
        index.write_text(json.dumps(manifest))
        with self.assertRaises(CheckpointRejected):
            LargeGemmaCheckpoint(directory, self.cfg)

    def test_rejects_insufficient_one_block_budget_before_loading(self):
        with self.assertRaisesRegex(CheckpointRejected, 'block budget'):
            LargeGemmaCheckpoint(self.path, self.cfg, max_block_bytes=128)

    def test_budget_does_not_claim_full_model_or_trained_quality(self):
        report = build_report(self.cfg, context_tokens=8, checkpoint=self.path,
                              limits=self.limits)
        self.assertEqual(report['status'], 'PACKED_CHECKPOINT_STRUCTURALLY_VALID')
        self.assertFalse(report['runnable_model'])
        self.assertFalse(report['trained_checkpoint_verified'])
        self.assertFalse(report['full_27b_inference_implemented'])
        self.assertGreater(report['checkpoint']['max_block_bytes'], 0)
        self.assertFalse(report['memory']['actual_gpu_peak_measured'])

    def test_rejects_full_model_weights_not_in_memory_budget(self):
        with self.assertRaises(CheckpointRejected):
            build_report(self.cfg, context_tokens=8, checkpoint=self.path,
                         limits=self.limits, max_block_bytes=128)


@unittest.skipUnless(cuda_is_available(), 'CUDA device required')
class CUDABlockLoaderTests(unittest.TestCase):
    def test_loads_one_cuda_block_then_releases_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'synthetic.safetensors'
            model = make_demo_model(backend='cpu', depth=6, seed=19)
            write_synthetic_checkpoint(p, model)
            reader = LargeGemmaCheckpoint(p, _tiny_arch())
            with reader.cuda_block(0, pool_limit_bytes=32*1024*1024) as (cuda_ops, pool):
                self.assertEqual(len(cuda_ops), 7)
                self.assertGreater(pool.used_bytes(), 0)
                got = cuda_ops['mlp.gate_proj'].forward([.1] * 8).get()
                expect = model.cpu_layers[0].projections['mlp.gate_proj'].forward([.1] * 8)
                np.testing.assert_allclose(got, expect, atol=1e-4, rtol=1e-4)
            self.assertEqual(pool.used_bytes(), 0)

    def test_fails_closed_on_tiny_cuda_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'synthetic.safetensors'
            write_synthetic_checkpoint(p, make_demo_model(backend='cpu', depth=6))
            reader = LargeGemmaCheckpoint(p, _tiny_arch())
            with self.assertRaises(MemoryError):
                with reader.cuda_block(0, pool_limit_bytes=1):
                    pass


class FullSizeStructuralTests(unittest.TestCase):
    def test_62_layer_434_projection_descriptors_validate_without_weight_buffers(self):
        # Virtual headers stand in for a hypothetical packed checkpoint; there
        # are NO pretrained weights and NO large factor arrays in this test.
        from gemma3_bridge import projection_spec
        cfg = read_gemma_config(test_fixture_path())
        shapes = projection_spec(cfg)
        class VirtualGroup:
            def __init__(self):
                self.single = self
                self.data_start = 1024
                self._shards = {}
                self.weight_map = None
                self.checked = set()
            def contains(self, key):
                return False
            def read(self, key):
                info = self.descriptor(key)
                name = key.rsplit('.', 1)[-1]
                if name == 'U_shape':
                    return np.array([info['module_dims'][0], 96], dtype='<i8')
                if name == 'V_shape':
                    return np.array([96, info['module_dims'][1]], dtype='<i8')
                raise AssertionError('Large weights must NEVER be read in preflight')
            def descriptor(self, key):
                self.checked.add(key)
                if key == 'language_model.model.embed_tokens.weight':
                    dtype, shape = 'BF16', [cfg.vocab, cfg.hidden]
                elif key == 'language_model.model.norm.weight':
                    dtype, shape = 'BF16', [cfg.hidden]
                else:
                    module = key.split('.layers.', 1)[1].split('.', 1)[1]
                    projection = next((p for p in shapes if module.startswith(p + '.')), None)
                    if projection is None:
                        dtype = 'BF16'
                        shape = [cfg.head_dim] if module.endswith(('q_norm.weight','k_norm.weight')) else [cfg.hidden]
                    else:
                        out, inn = shapes[projection]
                        suffix = module[len(projection)+1:]
                        if suffix == 'U_packed': dtype, shape = 'I32', [out, 3]
                        elif suffix == 'V_packed': dtype, shape = 'I32', [96, (inn+31)//32]
                        elif suffix in ('U_shape', 'V_shape'): dtype, shape = 'I64', [2]
                        elif suffix in ('u1','u2','v1','v2'):
                            dtype='BF16'
                            shape = [{'u1':out,'u2':96,'v1':96,'v2':inn}[suffix]]
                        else: raise AssertionError(suffix)
                width = 4 if dtype == 'I32' else 8 if dtype == 'I64' else 2
                count = 1
                for dim in shape: count *= dim
                result={'dtype':dtype,'shape':shape,'data_offsets':[0,count*width]}
                if key.endswith(('U_shape','V_shape')):
                    prefix = key.split('.layers.', 1)[1].split('.', 1)[1].rsplit('.', 1)[0]
                    result['module_dims'] = shapes[prefix]
                return result
        virtual = VirtualGroup()
        with patch('gemma3_27b_ready._ShardGroup', return_value=virtual):
            scan = LargeGemmaCheckpoint('VIRTUAL-NOT-ON-DISK', cfg, max_block_bytes=1024*1024*1024)
        self.assertEqual(scan.decoder_layers, 62)
        self.assertEqual(scan.packed_projection_count, 434)
        self.assertGreater(scan.max_layer_bytes, 0)
        self.assertTrue(any('layers.61.mlp.down_proj.U_packed' in k for k in virtual.checked))
        self.assertFalse(scan.runnable_model)

    def test_manifest_only_dense_file_is_never_claimed_usable(self):
        cfg = read_gemma_config(test_fixture_path())
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'model.safetensors.index.json'
            from gemma3_bridge import enumerate_projection_keys
            mapping = {name+'.weight':'model-00001.safetensors'
                       for _, name, _ in enumerate_projection_keys(cfg)}
            file.write_text(json.dumps({'weight_map':mapping}))
            result = build_report(cfg, manifest=file)
        self.assertEqual(result['status'], 'MANIFEST_METADATA_ONLY_NOT_RUNNABLE')
        self.assertEqual(result['checkpoint']['classification'], 'DENSE_NOT_LITTLEBIT')
        self.assertFalse(result['runnable_model'])


if __name__ == '__main__': unittest.main()
