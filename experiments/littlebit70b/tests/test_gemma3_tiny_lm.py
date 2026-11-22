"""Checks complete token-ID path: tied embeddings, six blocks, greedy generation."""
import math
import sys
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cuda_packed import cuda_is_available
from gemma3_tiny_lm import TinyPackedLM, make_demo_model, run_demo
from gemma3_decoder import rms_norm


class TinyCPUModelTests(unittest.TestCase):
    def test_six_decoder_layers_follow_five_local_one_global_pattern(self):
        model = make_demo_model(backend='cpu', depth=6, seed=19)
        self.assertEqual(model.layer_kinds, ['sliding_attention'] * 5 + ['full_attention'])
        self.assertEqual(len(model.layers), 6)
        self.assertEqual(model.embedding.shape, (32, 8))
        self.assertEqual(len(model.layers[0].projections), 7)

    def test_token_embedding_scaling_final_norm_and_tied_output_logits(self):
        model = make_demo_model(backend='cpu', depth=1, seed=7)
        state = model.new_state()
        token_id = 3
        logits = model.step(token_id, state)
        embedded = model.embedding[token_id] * math.sqrt(model.config.hidden)
        reference_decoder = model.cpu_layers[0]
        from gemma3_decoder import Gemma3KVCache
        cache = Gemma3KVCache('sliding_attention', model.config.sliding_window)
        hidden = reference_decoder.forward_token(embedded, 0, cache)
        expected = rms_norm(hidden, model.final_norm, model.config.eps) @ model.embedding.T
        np.testing.assert_allclose(logits, expected, rtol=1e-6, atol=1e-6)
        self.assertEqual(logits.shape, (32,))
        self.assertEqual(state.position, 1)

    def test_local_cache_eviction_and_global_retention_over_six_tokens(self):
        model = make_demo_model(backend='cpu', depth=6, seed=17, max_tokens=12)
        state = model.new_state()
        for token in [1, 2, 3, 4, 5, 6]:
            logits = model.step(token, state)
            self.assertTrue(np.isfinite(logits).all())
        self.assertEqual(state.position, 6)
        self.assertEqual([len(cache.positions) for cache in state.caches], [3, 3, 3, 3, 3, 6])
        self.assertEqual(state.caches[0].positions, [3, 4, 5])
        self.assertEqual(state.caches[-1].positions, list(range(6)))

    def test_greedy_generation_is_deterministic_and_only_token_ids(self):
        first = make_demo_model(backend='cpu', seed=13)
        second = make_demo_model(backend='cpu', seed=13)
        a = first.generate([1, 4, 7], max_new_tokens=3)
        b = second.generate([1, 4, 7], max_new_tokens=3)
        self.assertEqual(a['generated_token_ids'], b['generated_token_ids'])
        self.assertEqual(a['prompt_token_ids'], [1, 4, 7])
        self.assertEqual(a['tokens_consumed'], 6)
        self.assertEqual(len(a['generated_token_ids']), 3)
        self.assertTrue(all(isinstance(t, int) and 0 <= t < 32 for t in a['generated_token_ids']))
        self.assertFalse(a['trained_model'])
        self.assertFalse(a['text_generation_available'])
        self.assertFalse(a['whole_llm'])

    def test_prefill_matches_sequential_steps(self):
        model = make_demo_model(backend='cpu', depth=2)
        expected_state = model.new_state()
        a = None
        for token in [2, 9, 3]:
            a = model.step(token, expected_state)
        b, state = model.prefill([2, 9, 3])
        np.testing.assert_allclose(a, b, atol=1e-6)
        self.assertEqual(expected_state.position, state.position)

    def test_softcap_changes_logits_without_changing_model_outputs_nan(self):
        raw = make_demo_model(backend='cpu', depth=1, seed=29)
        capped = make_demo_model(backend='cpu', depth=1, seed=29, final_logit_softcap=.05)
        logits = raw.step(3, raw.new_state())
        capped_logits = capped.step(3, capped.new_state())
        expected = .05 * np.tanh(logits / .05)
        np.testing.assert_allclose(capped_logits, expected, atol=1e-6, rtol=1e-6)
        self.assertTrue(np.isfinite(capped_logits).all())
        self.assertLessEqual(float(np.max(np.abs(capped_logits))), .050001)

    def test_rejects_invalid_input_ids_and_incomplete_prompt(self):
        model = make_demo_model(backend='cpu')
        state = model.new_state()
        for invalid in (-1, 32, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                model.step(invalid, state)
        self.assertEqual(state.position, 0)
        with self.assertRaises(ValueError):
            model.prefill([])
        with self.assertRaises(ValueError):
            model.generate([1], max_new_tokens=0)

    def test_enforces_max_tokens_without_mutating_caches(self):
        model = make_demo_model(backend='cpu', max_tokens=4)
        state = model.new_state()
        for token in [1, 2, 3, 4]:
            model.step(token, state)
        before = [list(cache.positions) for cache in state.caches]
        with self.assertRaises((ValueError, MemoryError)):
            model.step(2, state)
        self.assertEqual(state.position, 4)
        self.assertEqual(before, [cache.positions for cache in state.caches])
        with self.assertRaises(ValueError):
            model.generate([1,2,3], max_new_tokens=2)

    def test_state_isolation_and_owner_validation(self):
        a = make_demo_model(backend='cpu', seed=7)
        b = make_demo_model(backend='cpu', seed=8)
        state_a = a.new_state()
        a.step(1, state_a)
        with self.assertRaises(ValueError):
            b.step(1, state_a)
        state_b = b.new_state()
        b.step(1, state_b)
        self.assertEqual(state_a.position, state_b.position)
        self.assertIsNot(state_a.caches[0], state_b.caches[0])

    def test_invalid_dimensions_depth_vocabulary_and_backend(self):
        for kw in ({'depth': 0}, {'depth': 13}, {'vocab': 1}, {'vocab': 257},
                   {'max_tokens': 0}, {'max_tokens': 4097}, {'backend': 'fake'},
                   {'final_logit_softcap': 0}, {'depth': True}):
            with self.subTest(kw=kw), self.assertRaises((TypeError, ValueError)):
                make_demo_model(**kw)

    def test_cpu_demo_does_not_pretend_to_have_trained_model(self):
        output = run_demo(backend='cpu')
        self.assertEqual(output['status'], 'GEMMA3_TINY_TOKEN_PIPELINE_OK')
        self.assertFalse(output['cuda_executed'])
        self.assertEqual(output['checkpoint'], 'SYNTHETIC_ONLY')
        self.assertFalse(output['trained_model'])
        self.assertFalse(output['text_generation_available'])
        self.assertEqual(output['decoder_blocks'], 6)
        self.assertEqual(output['local_attention_blocks'], 5)
        self.assertEqual(output['global_attention_blocks'], 1)


@unittest.skipUnless(cuda_is_available(), 'CuPy GPU required')
class TinyGPUModelTests(unittest.TestCase):
    def test_cuda_logits_and_greedy_tokens_match_cpu(self):
        cpu = make_demo_model(backend='cpu', seed=44)
        cuda = make_demo_model(backend='cuda', seed=44)
        a, st_a = cpu.prefill([2, 3, 7])
        b, st_b = cuda.prefill([2, 3, 7])
        np.testing.assert_allclose(a, b.get(), rtol=5e-4, atol=5e-4)
        self.assertEqual(st_a.position, st_b.position)
        a_ids = cpu.generate([2, 3, 7], max_new_tokens=2)['generated_token_ids']
        b_ids = cuda.generate([2, 3, 7], max_new_tokens=2)['generated_token_ids']
        self.assertEqual(a_ids, b_ids)

    def test_gpu_state_cache_global_and_local_eviction(self):
        gpu = make_demo_model(backend='cuda', seed=6, max_tokens=8)
        state = gpu.new_state()
        for t in (1, 3, 5, 7, 9):
            logits = gpu.step(t, state)
            self.assertEqual(logits.shape, (32,))
        self.assertEqual([len(c.positions) for c in state.caches], [3, 3, 3, 3, 3, 5])

    def test_cuda_demo_confirms_real_device_and_cpu_parity(self):
        out = run_demo(backend='cuda')
        self.assertEqual(out['status'], 'GEMMA3_TINY_TOKEN_PIPELINE_OK')
        self.assertTrue(out['cuda_executed'])
        self.assertFalse(out['trained_model'])
        self.assertFalse(out['text_generation_available'])
        self.assertLess(out['max_abs_logit_error'], 1e-3)

if __name__ == '__main__':
    unittest.main()
