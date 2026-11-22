"""Gemma 3 single-token decoder math, never full 27B inference."""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemma3_decoder import (DecoderSpec, Gemma3KVCache, Gemma3PackedDecoder,
                            rms_norm, gelu_tanh, apply_rope, attention_one, layer_kind,
                            make_synthetic_decoder, demo)


class Gemma3Math(unittest.TestCase):
    def test_local_global_pattern_every_sixth_decoder_layer(self):
        self.assertEqual([layer_kind(i) for i in range(12)],
                         ['sliding_attention'] * 5 + ['full_attention'] +
                         ['sliding_attention'] * 5 + ['full_attention'])
        with self.assertRaises(ValueError):
            layer_kind(-1)

    def test_rmsnorm_uses_one_plus_weight(self):
        x = np.array([3., -2., 1., 4.], dtype=np.float32)
        w = np.array([0., -.25, .5, 1.], dtype=np.float32)
        actual = rms_norm(x, w, 1e-6)
        expected = np.array([float(z) * (1. + float(scale)) /
                             math.sqrt(sum(float(a)**2 for a in x)/len(x) + 1e-6)
                             for z, scale in zip(x, w)])
        np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-7)
        np.testing.assert_allclose(rms_norm(x, np.zeros_like(x), 1e-6),
                                   x/math.sqrt(float((x*x).mean())+1e-6), rtol=1e-6)

    def test_gelu_pytorch_tanh_matches_scalar_equation(self):
        x = np.array([-6., -2., -.8, 0., .5, 2., 6.], dtype=np.float32)
        expected = [0.5*float(v)*(1.0+math.tanh(math.sqrt(2/math.pi)*
                         (float(v) + 0.044715*float(v)**3))) for v in x]
        np.testing.assert_allclose(gelu_tanh(x), expected, rtol=2e-5, atol=2e-6)

    def test_rope_rotates_split_halves_and_global_factor(self):
        x = np.array([[1., 2., 3., 4.], [-1., .5, .2, .7]], dtype=np.float32)
        np.testing.assert_array_equal(apply_rope(x, 0, theta=10000.), x)
        for theta, factor in [(10000., 1.), (1000000., 8.)]:
            pos = 7
            got = apply_rope(x, pos, theta=theta, factor=factor)
            angles = [(pos / factor) * theta ** (-j / 4.) for j in (0, 2)]
            angles = angles + angles
            expected = np.array([[float(a)*math.cos(ang) + float(b)*math.sin(ang)
                 for a, b, ang in zip(row, [-row[2], -row[3], row[0], row[1]], angles)]
                 for row in x], dtype=np.float32)
            np.testing.assert_allclose(got, expected, rtol=1e-6, atol=1e-6)
            np.testing.assert_allclose((got**2).sum(-1), (x**2).sum(-1), rtol=1e-5)
        self.assertFalse(np.allclose(apply_rope(x, 7, theta=10000.),
                                     apply_rope(x, 7, theta=1000000., factor=8.)))

    def test_attention_gqa_query_heads_share_correct_kv_head(self):
        cfg = DecoderSpec(hidden=8, intermediate=12, q_heads=4, kv_heads=2,
                          head_dim=2, sliding_window=2, query_pre_attn_scalar=4.)
        q = np.array([[1, 0], [0, 1], [1, 0], [0, 1]], dtype=np.float32)
        k = np.array([[1, 0], [0, 1]], dtype=np.float32)
        v = np.array([[11, 12], [31, 32]], dtype=np.float32)
        cache = Gemma3KVCache('full_attention', 2)
        out = attention_one(q, k, v, cache, position=0, cfg=cfg)
        np.testing.assert_allclose(out, [[11, 12], [11, 12], [31, 32], [31, 32]], atol=1e-6)
        self.assertEqual(cache.positions, [0])

    def test_sliding_cache_evicts_old_tokens_global_retains(self):
        cfg = DecoderSpec(hidden=8, intermediate=12, q_heads=4, kv_heads=2,
                          head_dim=2, sliding_window=3, query_pre_attn_scalar=4.)
        q = np.ones((4, 2), dtype=np.float32)
        k = np.ones((2, 2), dtype=np.float32)
        for kind in ('sliding_attention', 'full_attention'):
            cache = Gemma3KVCache(kind, 3)
            for pos in range(9):
                v = np.full((2, 2), pos, dtype=np.float32)
                out = attention_one(q, k, v, cache, position=pos, cfg=cfg)
                if kind == 'sliding_attention' and pos >= 2:
                    np.testing.assert_allclose(out, np.full((4, 2), pos-1), atol=1e-6)
                elif pos == 8:
                    np.testing.assert_allclose(out, np.full((4, 2), 4), atol=1e-6)
            expected = [6, 7, 8] if kind == 'sliding_attention' else list(range(9))
            self.assertEqual(cache.positions, expected)
            self.assertEqual(cache.keys.shape, (len(expected), 2, 2))
        with self.assertRaises(ValueError):
            attention_one(q, k, v, cache, position=8, cfg=cfg)

    def test_attention_softcap_changes_logits_and_stays_finite(self):
        base = dict(hidden=8, intermediate=12, q_heads=4, kv_heads=2, head_dim=2,
                    sliding_window=3, query_pre_attn_scalar=4.)
        q = np.array([[4, 0]]*4, dtype=np.float32)
        k = np.array([[2, 0]]*2, dtype=np.float32)
        v0 = np.zeros((2, 2), dtype=np.float32)
        v1 = np.ones((2, 2), dtype=np.float32)
        def calc(cap):
            cfg = DecoderSpec(**base, attn_softcap=cap)
            state = Gemma3KVCache('full_attention', cfg.sliding_window)
            attention_one(q, k, v0, state, position=0, cfg=cfg)
            return attention_one(q, -k, v1, state, position=1, cfg=cfg)
        self.assertTrue(np.isfinite(calc(1.0)).all())
        self.assertFalse(np.allclose(calc(None), calc(1.0)))


class Gemma3DecoderIntegration(unittest.TestCase):
    def setUp(self):
        self.cfg = DecoderSpec(hidden=8, intermediate=12, q_heads=4, kv_heads=2,
                               head_dim=4, sliding_window=3, query_pre_attn_scalar=8.,
                               global_rope_factor=8.)

    def test_build_tiny_decoder_consumes_all_seven_packed_projections(self):
        layer = make_synthetic_decoder(self.cfg, seed=19)
        self.assertIsInstance(layer, Gemma3PackedDecoder)
        self.assertEqual(len(layer.projections), 7)
        from packed_linear import LittleBitPackedLinear
        self.assertTrue(all(isinstance(p, LittleBitPackedLinear) for p in layer.projections.values()))
        for kind in ('sliding_attention', 'full_attention'):
            state = Gemma3KVCache(kind, self.cfg.sliding_window)
            x = np.linspace(-0.3, .3, self.cfg.hidden, dtype=np.float32)
            first = layer.forward_token(x, 0, state)
            second = layer.forward_token(x * 0.5, 1, state)
            self.assertEqual(first.shape, (self.cfg.hidden,))
            self.assertTrue(np.isfinite(first).all())
            self.assertTrue(np.isfinite(second).all())
            self.assertEqual(state.positions, [0, 1])

    def test_decoder_matches_independent_python_reference_two_tokens(self):
        # Independent scalar/list formulas, without PyTorch or new installations.
        layer = make_synthetic_decoder(self.cfg, seed=27)
        inputs = [np.linspace(-.4, .6, 8, dtype=np.float32),
                  np.linspace(.3, -.5, 8, dtype=np.float32)]
        def normalize(values, weights):
            a = np.asarray(values, dtype=np.float32)
            weights = np.asarray(weights, dtype=np.float32)
            denom = np.sqrt(np.mean(a*a, axis=-1, keepdims=True) + self.cfg.eps)
            return (a/denom*(1+weights)).astype(np.float32)
        def project(name, values):
            return np.asarray(layer.projections[name].forward(values.tolist()), dtype=np.float32)
        for kind in ('sliding_attention', 'full_attention'):
            cache = Gemma3KVCache(kind, self.cfg.sliding_window)
            k_store, v_store = [], []
            for pos, x in enumerate(inputs):
                got = layer.forward_token(x, pos, cache)
                theta = self.cfg.local_rope_theta if kind == 'sliding_attention' else self.cfg.global_rope_theta
                factor = 1. if kind == 'sliding_attention' else self.cfg.global_rope_factor
                def rotary(heads):
                    freq = [pos / factor * theta**(-i/self.cfg.head_dim) for i in range(0,self.cfg.head_dim,2)]
                    ang = freq+freq
                    mid = self.cfg.head_dim//2
                    return np.asarray([[float(a)*math.cos(t)+float(b)*math.sin(t)
                     for a,b,t in zip(head, np.concatenate((-head[mid:],head[:mid])), ang)]
                     for head in heads], dtype=np.float32)
                initial=normalize(x,layer.norms['input_layernorm'])
                q=rotary(normalize(project('self_attn.q_proj',initial).reshape(4,4),layer.norms['q_norm']))
                k=rotary(normalize(project('self_attn.k_proj',initial).reshape(2,4),layer.norms['k_norm']))
                v=project('self_attn.v_proj',initial).reshape(2,4)
                k_store.append(k)
                v_store.append(v)
                if kind=='sliding_attention':
                    k_store=k_store[-3:]
                    v_store=v_store[-3:]
                keys=np.stack(k_store)
                vals=np.stack(v_store)
                context=[]
                for head in range(4):
                    kvh=head//2
                    scores=np.asarray([float(np.dot(q[head],kv)) /
                          math.sqrt(self.cfg.query_pre_attn_scalar) for kv in keys[:,kvh,:]],dtype=np.float32)
                    weights=np.exp(scores-float(scores.max()))
                    weights=weights/weights.sum()
                    context.append(np.sum(vals[:,kvh,:]*weights[:,None],axis=0))
                attended=project('self_attn.o_proj',np.asarray(context,dtype=np.float32).reshape(-1))
                post=x+normalize(attended,layer.norms['post_attention_layernorm'])
                feed=normalize(post,layer.norms['pre_feedforward_layernorm'])
                gate=project('mlp.gate_proj',feed)
                up=project('mlp.up_proj',feed)
                act=np.asarray([.5*float(v)*(1+math.tanh(math.sqrt(2/math.pi)*(float(v)+.044715*float(v)**3)))
                                for v in gate],dtype=np.float32)
                mlp=project('mlp.down_proj',act*up)
                expected=post+normalize(mlp,layer.norms['post_feedforward_layernorm'])
                np.testing.assert_allclose(got,expected,rtol=4e-5,atol=3e-5)

    def test_bad_dimensions_and_position_are_rejected(self):
        with self.assertRaises(ValueError):
            DecoderSpec(hidden=8, intermediate=12, q_heads=3, kv_heads=2, head_dim=4)
        with self.assertRaises(ValueError):
            DecoderSpec(hidden=8, intermediate=12, q_heads=4, kv_heads=2, head_dim=3)
        layer = make_synthetic_decoder(self.cfg)
        cache = Gemma3KVCache('sliding_attention', self.cfg.sliding_window)
        with self.assertRaises(ValueError):
            layer.forward_token(np.ones(7, dtype=np.float32), 0, cache)
        with self.assertRaises(ValueError):
            layer.forward_token(np.ones(8, dtype=np.float32), -1, cache)

    def test_default_demo_explicitly_reports_synthetic_not_model(self):
        result = demo()
        self.assertEqual(result['status'], 'GEMMA3_PACKED_DECODER_REFERENCE_OK')
        self.assertEqual(result['checkpoint'], 'SYNTHETIC_ONLY')
        self.assertFalse(result['trained_model'])
        self.assertFalse(result['whole_llm'])
        self.assertFalse(result['cuda_executed'])
        self.assertEqual(result['packed_projection_count'], 7)


if __name__ == '__main__':
    unittest.main()
