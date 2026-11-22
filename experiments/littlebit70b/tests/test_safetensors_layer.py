"""Tests for a bounded, genuine Samsung-style safetensors layer reader."""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packed_linear import PackedBinaryMatrix, LittleBitPackedLinear
from safetensors_layer import SafetensorsLittleBitLayer, CheckpointFormatError, write_synthetic_demo, demo
from cuda_packed import cuda_is_available


def fixture(path, tensors, raw_override=None):
    """Create safetensors bytes in the official layout; BF16 via raw uint16."""
    header = {}
    data = bytearray()
    for name, (dtype, shape, raw) in tensors.items():
        start = len(data)
        data.extend(raw)
        header[name] = {'dtype': dtype, 'shape': list(shape), 'data_offsets': [start, len(data)]}
    blob = json.dumps(header, separators=(',', ':')).encode('utf8')
    path.write_bytes(struct.pack('<Q', len(blob)) + blob + data)


def tensors(prefix='model.layers.0.mlp.down_proj.', residual=False):
    v = PackedBinaryMatrix.from_signs([[1, -1, 1, -1, 1], [-1, 1, 1, -1, -1], [1, 1, -1, -1, 1]])
    u = PackedBinaryMatrix.from_signs([[1, 1, -1], [-1, 1, 1]])
    def pack_rows(words):
        return np.asarray(words, dtype='<i4').tobytes()
    def as_float(values, dtype='F32'):
        if dtype == 'BF16':
            bits = np.asarray(values, dtype='<f4').view('<u4')
            return np.right_shift(bits, 16).astype('<u2').tobytes()
        return np.asarray(values, dtype='<f4').tobytes()
    base = {
        'U_packed': ('I32', (2, 1), pack_rows(u.rows)),
        'V_packed': ('I32', (3, 1), pack_rows(v.rows)),
        'U_shape': ('I64', (2,), np.asarray([2, 3], dtype='<i8').tobytes()),
        'V_shape': ('I64', (2,), np.asarray([3, 5], dtype='<i8').tobytes()),
        'u1': ('BF16', (1, 2), as_float([0.25, 0.5], 'BF16')),
        'u2': ('F32', (1, 3), as_float([0.7, 0.9, 1.1])),
        'v1': ('F32', (1, 3), as_float([0.3, 0.6, 0.8])),
        'v2': ('F32', (1, 5), as_float([1., 2., 3., 4., 5.])),
        'bias': ('F32', (2,), as_float([0.1, -0.2])),
    }
    if residual:
        for item in ('U_packed', 'V_packed', 'U_shape', 'V_shape', 'u1', 'u2', 'v1', 'v2'):
            item_dtype, shape, raw = base[item]
            base[(item[0] + '_R' + item[1:]) if item.startswith(('U_', 'V_')) else (item+'_R')] = (item_dtype, shape, raw)
    return {prefix + k:v for k,v in base.items()}


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'weights.safetensors'
        self.prefix = 'model.layers.0.mlp.down_proj.'

    def test_roundtrip_bf16_scaling_bias_and_reference_matvec(self):
        fixture(self.path, tensors())
        layer = SafetensorsLittleBitLayer.from_file(self.path, self.prefix)
        self.assertEqual(layer.u_words.shape, (2, 1))
        self.assertEqual(layer.v_words.shape, (3, 1))
        self.assertEqual(layer.v_shape, (3, 5))
        self.assertEqual(layer.u_shape, (2, 3))
        self.assertEqual(layer.u1.tolist(), [0.25, 0.5])
        self.assertEqual(layer.binary_factor_bytes, 20)
        self.assertEqual(layer.forward_cpu([1, 2, 3, 4, 5]).__len__(), 2)
        cpu = layer.to_cpu_layer()
        self.assertIsInstance(cpu, LittleBitPackedLinear)
        self.assertEqual(layer.forward_cpu([1, 2, 3, 4, 5]), cpu.forward([1, 2, 3, 4, 5]))

    def test_packed_data_keeps_original_sign_bits(self):
        fixture(self.path, tensors())
        layer = SafetensorsLittleBitLayer.from_file(self.path, self.prefix)
        self.assertEqual(layer.u_words.dtype, np.dtype('int32'))
        self.assertEqual(int(layer.v_words[0, 0]) & 0x1f, 0b01010)

    def test_residual_path_matches_twice_base_without_twice_bias(self):
        fixture(self.path, tensors(residual=True))
        layer = SafetensorsLittleBitLayer.from_file(self.path, self.prefix)
        self.assertIsNotNone(layer.residual)
        base_tensors = tensors()
        fixture(self.path, base_tensors)
        simple = SafetensorsLittleBitLayer.from_file(self.path, self.prefix)
        x = [1, -2, 3, -4, 5]
        base = simple.forward_cpu(x)
        with_residual = layer.forward_cpu(x)
        for a,b,c in zip(base,with_residual,simple.bias.tolist()):
            self.assertAlmostEqual(b, 2*a-c, places=5)

    def test_sharded_index_streams_tensors_from_multiple_files(self):
        source = tensors()
        items = list(source.items())
        first, second = dict(items[:4]), dict(items[4:])
        a, b = Path(self.tmp.name)/'model-00001-of-00002.safetensors', Path(self.tmp.name)/'model-00002-of-00002.safetensors'
        fixture(a, first)
        fixture(b, second)
        weight_map = {k: a.name for k in first} | {k: b.name for k in second}
        (Path(self.tmp.name)/'model.safetensors.index.json').write_text(json.dumps({'metadata': {}, 'weight_map': weight_map}))
        layer = SafetensorsLittleBitLayer.from_file(self.tmp.name, self.prefix)
        self.assertEqual(layer.u_shape, (2,3))
        self.assertEqual(layer.v_shape, (3,5))
        self.assertEqual(len(layer.forward_cpu([1., 2., 3., 4., 5.])), 2)

    def test_sharded_index_rejects_path_traversal(self):
        source = tensors()
        weight_map = {k: '../outside.safetensors' for k in source}
        (Path(self.tmp.name)/'model.safetensors.index.json').write_text(json.dumps({'weight_map': weight_map}))
        with self.assertRaises(CheckpointFormatError):
            SafetensorsLittleBitLayer.from_file(self.tmp.name, self.prefix)

    def test_partial_residual_is_rejected(self):
        data = tensors()
        data[self.prefix + 'U_R_packed'] = data[self.prefix + 'U_packed']
        fixture(self.path, data)
        with self.assertRaisesRegex(CheckpointFormatError, 'Incomplete residual'):
            SafetensorsLittleBitLayer.from_file(self.path, self.prefix)

    def test_rejects_wrong_packed_shape(self):
        data = tensors()
        data[self.prefix + 'V_shape'] = ('I64', (2,), np.asarray([3, 33], dtype='<i8').tobytes())
        fixture(self.path, data)
        with self.assertRaisesRegex(CheckpointFormatError, 'V_packed'):
            SafetensorsLittleBitLayer.from_file(self.path, self.prefix)

    def test_rejects_missing_required_scales(self):
        data = tensors()
        del data[self.prefix+'u1']
        fixture(self.path, data)
        with self.assertRaisesRegex(CheckpointFormatError, 'u1'):
            SafetensorsLittleBitLayer.from_file(self.path, self.prefix)

    def test_rejects_bad_offsets_and_invalid_header_before_tensor_read(self):
        self.path.write_bytes(struct.pack('<Q', 50_000_000))
        with self.assertRaises(CheckpointFormatError):
            SafetensorsLittleBitLayer.from_file(self.path, self.prefix)
        fixture(self.path, tensors())
        content = self.path.read_bytes()
        header_size = struct.unpack('<Q', content[:8])[0]
        header = json.loads(content[8:8+header_size])
        header[self.prefix+'U_packed']['data_offsets'][1] = 999999999999
        encoded = json.dumps(header).encode('utf8')
        self.path.write_bytes(struct.pack('<Q', len(encoded))+encoded+content[8+header_size:])
        with self.assertRaises(CheckpointFormatError):
            SafetensorsLittleBitLayer.from_file(self.path, self.prefix)

    def test_memory_limit_is_enforced_before_payload_is_loaded(self):
        fixture(self.path, tensors())
        with self.assertRaisesRegex(CheckpointFormatError, 'budget'):
            SafetensorsLittleBitLayer.from_file(self.path, self.prefix, max_layer_bytes=16)

    def test_synthetic_demo_creates_valid_readable_safetensors(self):
        write_synthetic_demo(self.path, self.prefix)
        layer = SafetensorsLittleBitLayer.from_file(self.path, self.prefix)
        self.assertEqual(layer.v_shape, (3, 5))
        self.assertEqual(demo()['status'], 'LITTLEBIT_SAFETENSORS_LAYER_DEMO_OK')

    @unittest.skipUnless(cuda_is_available(), 'CuPy CUDA GPU required')
    def test_warp_gpu_from_loaded_arrays_matches_cpu(self):
        fixture(self.path, tensors(residual=True))
        layer = SafetensorsLittleBitLayer.from_file(self.path, self.prefix)
        for backend in ('warp', 'scalar'):
            gpu = layer.to_cuda_layer(backend=backend)
            for x in ([1., -2., 3., -4., 5.], [0., 0., 1., 0., 0.]):
                actual = gpu.forward(x).get().tolist()
                for a,b in zip(actual, layer.forward_cpu(x)):
                    self.assertAlmostEqual(a,b,delta=1e-4*(1+abs(b)))


if __name__ == '__main__':
    unittest.main()
