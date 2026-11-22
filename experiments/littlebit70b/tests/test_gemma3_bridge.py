import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from gemma3_bridge import (read_gemma_config, projection_spec, audit_memory, inspect_manifest,
                           enumerate_projection_keys, ConfigError, ManifestError, test_fixture_path)


class Gemma3BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = read_gemma_config(test_fixture_path())

    def test_matches_published_27b_shapes(self):
        cfg = self.config
        self.assertEqual(cfg.layers, 62)
        self.assertEqual(cfg.hidden, 5376)
        self.assertEqual(cfg.intermediate, 21504)
        self.assertEqual(cfg.vocab, 262208)
        self.assertEqual(cfg.q_heads, 32)
        self.assertEqual(cfg.kv_heads, 16)
        self.assertEqual(cfg.head_dim, 128)
        self.assertTrue(cfg.matches_target_27b)
        self.assertEqual(cfg.attention_pattern.count('sliding_attention'), 52)
        self.assertEqual(cfg.attention_pattern.count('full_attention'), 10)
        self.assertEqual(cfg.attention_pattern[5], 'full_attention')
        self.assertEqual(cfg.attention_pattern[6], 'sliding_attention')

    def test_projection_shapes_and_exact_parameter_counts(self):
        shapes = projection_spec(self.config)
        self.assertEqual(shapes['self_attn.q_proj'], (4096, 5376))
        self.assertEqual(shapes['self_attn.k_proj'], (2048, 5376))
        self.assertEqual(shapes['self_attn.v_proj'], (2048, 5376))
        self.assertEqual(shapes['self_attn.o_proj'], (5376, 4096))
        self.assertEqual(shapes['mlp.gate_proj'], (21504, 5376))
        self.assertEqual(shapes['mlp.up_proj'], (21504, 5376))
        self.assertEqual(shapes['mlp.down_proj'], (5376, 21504))

    def test_bf16_embedding_floor_is_not_quantized_by_littlebit_linear(self):
        report = audit_memory(self.config, context_tokens=4096, effective_bits=0.55)
        self.assertEqual(report['text_embedding_bf16_bytes'], 5376 * 262208 * 2)
        self.assertGreater(report['idealized_mlp_only_text_weights_bytes'], 6 * (1024**3))
        self.assertGreater(report['idealized_mlp_only_text_plus_kv_bytes'], report['idealized_mlp_only_text_weights_bytes'])
        self.assertLess(report['idealized_all_projection_text_weights_bytes'], report['idealized_mlp_only_text_weights_bytes'])
        self.assertFalse(report['measured_checkpoint_bytes'])
        self.assertFalse(report['trained_checkpoint_available'])
        self.assertFalse(report['includes_vision_encoder'])
        self.assertFalse(report['includes_runtime_overhead'])

    def test_kv_math_respects_sliding_window_and_full_layers(self):
        first = audit_memory(self.config, context_tokens=1024)
        longer = audit_memory(self.config, context_tokens=4096)
        full = 10 * 4096 * 2 * 16 * 128 * 2
        local = 52 * 1024 * 2 * 16 * 128 * 2
        self.assertEqual(longer['idealized_bf16_kv_cache_bytes'], local + full)
        self.assertGreater(longer['idealized_bf16_kv_cache_bytes'], first['idealized_bf16_kv_cache_bytes'])

    def test_rejects_invalid_context_and_bits(self):
        for context, bit in [(0, 0.55), (131073, 0.55), (4096, 0), (4096, -2), (4096, 10), (True, 0.55)]:
            with self.subTest(context=context, bit=bit), self.assertRaises(ValueError):
                audit_memory(self.config, context_tokens=context, effective_bits=bit)

    def test_rejects_incorrect_or_partial_config(self):
        raw = json.loads(Path(test_fixture_path()).read_text())
        for modify in [lambda d: d.update(model_type='qwen'),
                       lambda d: d['text_config'].update(num_key_value_heads=0),
                       lambda d: d['text_config'].pop('head_dim'),
                       lambda d: d['text_config'].update(sliding_window_pattern=0)]:
            clone = copy.deepcopy(raw)
            modify(clone)
            with tempfile.TemporaryDirectory() as t:
                file = Path(t) / 'config.json'
                file.write_text(json.dumps(clone))
                with self.assertRaises(ConfigError):
                    read_gemma_config(file)

    def test_manifest_identifies_original_dense_checkpoint_not_littlebit(self):
        manifest = {'metadata': {'total_size': 54864813280}, 'weight_map': {}}
        for layer, name, _ in enumerate_projection_keys(self.config):
            manifest['weight_map'][name + '.weight'] = 'model-00001-of-00012.safetensors'
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / 'model.safetensors.index.json'
            file.write_text(json.dumps(manifest))
            result = inspect_manifest(file, self.config)
        self.assertEqual(result['total_projection_modules'], 62 * 7)
        self.assertEqual(result['dense_projection_modules'], 62 * 7)
        self.assertEqual(result['littlebit_packed_projection_modules'], 0)
        self.assertEqual(result['incomplete_projection_modules'], 0)
        self.assertEqual(result['classification'], 'DENSE_NOT_LITTLEBIT')
        self.assertEqual(result['declared_total_checkpoint_bytes'], 54864813280)
        self.assertFalse(result['usable_littlebit_checkpoint'])

    def test_manifest_rejects_fake_littlebit_partial_packing(self):
        names = list(enumerate_projection_keys(self.config))
        fake = {names[0][1] + '.U_packed': 'model-00001.safetensors',
                names[0][1] + '.V_packed': 'model-00001.safetensors'}
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / 'index.json'
            file.write_text(json.dumps({'weight_map': fake}))
            result = inspect_manifest(file, self.config)
        self.assertEqual(result['incomplete_projection_modules'], 1)
        self.assertEqual(result['littlebit_packed_projection_modules'], 0)
        self.assertFalse(result['usable_littlebit_checkpoint'])

    def test_manifest_recognizes_full_littlebit_factors_but_no_full_model(self):
        names = list(enumerate_projection_keys(self.config))
        keys = ('U_packed', 'V_packed', 'U_shape', 'V_shape', 'u1', 'u2', 'v1', 'v2')
        fake = {names[0][1] + '.' + suffix: 'test.safetensors' for suffix in keys}
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / 'index.json'
            file.write_text(json.dumps({'weight_map': fake}))
            result = inspect_manifest(file, self.config)
        self.assertEqual(result['littlebit_packed_projection_modules'], 1)
        self.assertEqual(result['absent_projection_modules'], 62*7 - 1)
        self.assertEqual(result['classification'], 'PARTIAL_OR_MIXED')
        self.assertFalse(result['usable_littlebit_checkpoint'])

    def test_manifest_rejects_non_json_and_large_files(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / 'index.json'
            file.write_text('not json')
            with self.assertRaises(ManifestError):
                inspect_manifest(file, self.config)
            file.write_bytes(b' ' * (5*1024*1024))
            with self.assertRaises(ManifestError):
                inspect_manifest(file, self.config)

    def test_no_index_does_not_claim_checkpoint_exists(self):
        r = inspect_manifest(None, self.config)
        self.assertEqual(r['classification'], 'NO_CHECKPOINT_INDEX_SUPPLIED')
        self.assertFalse(r['usable_littlebit_checkpoint'])


if __name__ == '__main__':
    unittest.main()
