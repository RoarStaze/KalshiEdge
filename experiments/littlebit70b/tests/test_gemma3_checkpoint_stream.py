"""Tests of actual Gemma3 naming and one-at-a-time packed checkpoint loading."""
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemma3_tiny_lm import make_demo_model
from gemma3_checkpoint_stream import (
    Gemma3CheckpointReader, StreamedTinyGemma3, write_synthetic_checkpoint,
    demo, MODEL_PREFIX, PROJECTIONS, NORM_KEYS,
)
from safetensors_layer import CheckpointFormatError
from cuda_packed import cuda_is_available


class CheckpointStreamingCPU(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'synthetic.safetensors'
        self.original = make_demo_model(backend='cpu', depth=6, seed=19, max_tokens=16)
        write_synthetic_checkpoint(self.path, self.original)

    def test_writes_gemma3_weight_keys_and_all_seven_packed_projections(self):
        raw = self.path.read_bytes()
        h = struct.unpack('<Q', raw[:8])[0]
        header = json.loads(raw[8:8 + h])
        self.assertIn('language_model.model.embed_tokens.weight', header)
        self.assertIn('language_model.model.norm.weight', header)
        for i in range(6):
            prefix = f'{MODEL_PREFIX}.layers.{i}.'
            for projection in PROJECTIONS:
                for part in ('U_packed', 'V_packed', 'U_shape', 'V_shape', 'u1', 'u2', 'v1', 'v2'):
                    self.assertIn(prefix + projection + '.' + part, header)
                self.assertNotIn(prefix + projection + '.weight', header)
            for suffix in NORM_KEYS.values():
                self.assertIn(prefix + suffix + '.weight', header)

    def test_reader_preflights_all_layers_and_catches_actual_sizes(self):
        reader = Gemma3CheckpointReader(self.path, self.original.config, 6)
        self.assertEqual(reader.layer_count, 6)
        self.assertEqual(reader.vocab_size, 32)
        self.assertGreater(reader.total_projection_bytes, 0)
        self.assertLess(reader.max_block_bytes_seen, 1024*1024)
        self.assertEqual(len(reader.projection_prefixes(2)), 7)
        block = reader.load_cpu_block(5)
        self.assertEqual(len(block.projections), 7)
        self.assertEqual(block.norms['q_norm'].shape, (4,))

    def test_streamed_cpu_prefill_and_greedy_tokens_match_in_memory(self):
        stream = StreamedTinyGemma3(self.path, self.original.config, 6, backend='cpu', max_tokens=16)
        expected, _ = self.original.prefill([1,4,7])
        actual, _ = stream.prefill([1,4,7])
        np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=1e-5)
        expected_gen = self.original.generate([1,4,7], max_new_tokens=2)
        actual_gen = stream.generate([1,4,7], max_new_tokens=2)
        self.assertEqual(actual_gen['generated_token_ids'], expected_gen['generated_token_ids'])
        self.assertEqual(actual_gen['loaded_block_count'], 5 * 6)
        self.assertEqual(actual_gen['checkpoint'], 'SYNTHETIC_ONLY')
        self.assertFalse(actual_gen['trained_model'])
        self.assertFalse(actual_gen['whole_llm'])

    def test_sharded_manifest_reader_matches_single_file(self):
        folder = Path(self.tmp.name) / 'shards'
        write_synthetic_checkpoint(folder, self.original, sharded=True)
        target = StreamedTinyGemma3(folder, self.original.config, 6, backend='cpu', max_tokens=16)
        actual = target.generate([2, 4, 8], max_new_tokens=2)
        expect = self.original.generate([2, 4, 8], max_new_tokens=2)
        self.assertEqual(actual['generated_token_ids'], expect['generated_token_ids'])

    def test_fails_if_packed_projection_is_missing_before_cache_mutation(self):
        # Build a forged sharded manifest that drops the packed factor.
        folder = Path(self.tmp.name) / 'shards'
        write_synthetic_checkpoint(folder, self.original, sharded=True)
        index = folder / 'model.safetensors.index.json'
        manifest = json.loads(index.read_text())
        del manifest['weight_map'][f'{MODEL_PREFIX}.layers.4.mlp.down_proj.U_packed']
        index.write_text(json.dumps(manifest))
        with self.assertRaises(CheckpointFormatError):
            Gemma3CheckpointReader(folder, self.original.config, 6)

    def test_rejects_ambiguous_dense_plus_packed_projection(self):
        folder = Path(self.tmp.name) / 'shards'
        write_synthetic_checkpoint(folder, self.original, sharded=True)
        index = folder / 'model.safetensors.index.json'
        manifest = json.loads(index.read_text())
        manifest['weight_map'][f'{MODEL_PREFIX}.layers.0.self_attn.q_proj.weight'] = 'synthetic-00000.safetensors'
        index.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(CheckpointFormatError, 'dense'):
            Gemma3CheckpointReader(folder, self.original.config, 6)

    def test_rejects_insufficient_decoder_block_byte_budget(self):
        with self.assertRaisesRegex(CheckpointFormatError, 'budget'):
            Gemma3CheckpointReader(self.path, self.original.config, 6, max_block_bytes=128)

    def test_rejects_insufficient_embedding_byte_budget(self):
        with self.assertRaisesRegex(CheckpointFormatError, 'embedding'):
            Gemma3CheckpointReader(self.path, self.original.config, 6, max_embedding_bytes=8)

    def test_rejects_malformed_scale_in_late_layer_at_preflight(self):
        folder = Path(self.tmp.name) / 'shards'
        write_synthetic_checkpoint(folder, self.original, sharded=True)
        key = f'{MODEL_PREFIX}.layers.5.mlp.down_proj.v2'
        for candidate in folder.glob('*.safetensors'):
            raw = candidate.read_bytes()
            header_size = struct.unpack('<Q', raw[:8])[0]
            header = json.loads(raw[8:8+header_size])
            if key not in header:
                continue
            header[key]['shape'] = [3, 4]  # Same byte count, invalid scale dimensions.
            payload = raw[8+header_size:]
            encoded = json.dumps(header).encode('utf8')
            candidate.write_bytes(struct.pack('<Q', len(encoded)) + encoded + payload)
            break
        with self.assertRaisesRegex(CheckpointFormatError, 'scaling'):
            Gemma3CheckpointReader(folder, self.original.config, 6)

    def test_rejects_invalid_norm_tensor_shape(self):
        folder = Path(self.tmp.name) / 'shards'
        write_synthetic_checkpoint(folder, self.original, sharded=True)
        path = folder / 'synthetic-00000.safetensors'
        # Other coverage tests check invalid manifest; this checks in-file descriptor.
        shard_names = list(folder.glob('*.safetensors'))
        changed = False
        for candidate in shard_names:
            raw = candidate.read_bytes()
            size = struct.unpack('<Q', raw[:8])[0]
            header = json.loads(raw[8:8 + size])
            key = f'{MODEL_PREFIX}.layers.0.input_layernorm.weight'
            if key not in header:
                continue
            header[key]['shape'] = [1, 8]  # Same bytes, wrong normalization shape.
            blob = json.dumps(header).encode('utf8')
            candidate.write_bytes(struct.pack('<Q', len(blob)) + blob + raw[8 + size:])
            changed = True
            break
        self.assertTrue(changed)
        with self.assertRaisesRegex(CheckpointFormatError, 'norm'):
            Gemma3CheckpointReader(folder, self.original.config, 6)

    def test_rejects_invalid_ids_context_and_foreign_state(self):
        a = StreamedTinyGemma3(self.path, self.original.config, 6, backend='cpu', max_tokens=4)
        b = StreamedTinyGemma3(self.path, self.original.config, 6, backend='cpu', max_tokens=4)
        state = a.new_state()
        with self.assertRaises(ValueError):
            b.step(1, state)
        with self.assertRaises(ValueError):
            a.step(-1, state)
        with self.assertRaises(ValueError):
            a.generate([1, 2, 3], max_new_tokens=2)
        self.assertEqual(state.position, 0)

    def test_cpu_demo_explicitly_synthetic(self):
        r = demo(backend='cpu')
        self.assertEqual(r['status'], 'GEMMA3_PACKED_CHECKPOINT_STREAM_OK')
        self.assertEqual(r['checkpoint'], 'SYNTHETIC_ONLY')
        self.assertFalse(r['trained_model'])
        self.assertEqual(r['decoder_blocks'], 6)
        self.assertFalse(r['cuda_executed'])


@unittest.skipUnless(cuda_is_available(), 'CUDA GPU and CuPy required')
class CheckpointStreamingCUDA(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'checkpoint.safetensors'
        self.original = make_demo_model(backend='cpu', depth=6, seed=19, max_tokens=16)
        write_synthetic_checkpoint(self.path, self.original)

    def test_streamed_cuda_logits_and_greedy_tokens_match_cpu(self):
        cpum = StreamedTinyGemma3(self.path, self.original.config, 6, backend='cpu', max_tokens=16)
        gpum = StreamedTinyGemma3(self.path, self.original.config, 6, backend='cuda', max_tokens=16,
                                  weight_pool_bytes=8 * 1024 * 1024, state_pool_bytes=8 * 1024 * 1024)
        expected, _ = cpum.prefill([1,4,7])
        actual, _ = gpum.prefill([1,4,7])
        np.testing.assert_allclose(actual.get(), expected, rtol=5e-4, atol=5e-4)
        result = gpum.generate([1,4,7], max_new_tokens=2)
        reference = cpum.generate([1,4,7], max_new_tokens=2)
        self.assertEqual(result['generated_token_ids'], reference['generated_token_ids'])
        self.assertEqual(result['weights_live_after_step_bytes'], 0)
        self.assertGreater(result['weight_pool_peak_bytes'], 0)
        self.assertEqual(result['loaded_block_count'], 30)

    def test_gpu_pool_budget_raises_instead_of_using_unbounded_fallback(self):
        gpum = StreamedTinyGemma3(self.path, self.original.config, 6, backend='cuda', max_tokens=16,
                                  weight_pool_bytes=1024, state_pool_bytes=8 * 1024 * 1024)
        with self.assertRaises((MemoryError, RuntimeError)):
            gpum.generate([1,4,7], max_new_tokens=2)

    def test_gpu_demo_confirms_actual_device_and_weight_evictions(self):
        report = demo(backend='cuda')
        self.assertTrue(report['cuda_executed'])
        self.assertEqual(report['decoder_blocks'], 6)
        self.assertEqual(report['weights_live_after_step_bytes'], 0)
        self.assertLess(report['max_abs_logit_error'], 1e-3)


if __name__ == '__main__':
    unittest.main()
