"""Bounded reader for a single Samsung LittleBit packed layer in safetensors.

Samsung's state_dict names: U_packed, V_packed, U_shape, V_shape,
u1,u2,v1,v2, optional bias; residual uses U_R_packed/V_R_packed etc.
The original 27B model / tokenizer is not included. This is NOT a full LLM.

No torch, safetensors third-party package, or network access is required.
Numpy is provided by the isolated CuPy environment.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import struct
from tempfile import TemporaryDirectory

import numpy as np

from packed_linear import PackedBinaryMatrix, LittleBitPackedLinear


class CheckpointFormatError(ValueError):
    """Invalid, unsupported, or oversized packed layer."""


DTYPES = {'I32': ('<i4', 4), 'I64': ('<i8', 8), 'F32': ('<f4', 4),
          'F16': ('<f2', 2), 'BF16': ('<u2', 2)}
REQUIRED = ('U_packed', 'V_packed', 'U_shape', 'V_shape', 'u1', 'u2', 'v1', 'v2')
RESIDUAL_NAMES = ('U_R_packed', 'V_R_packed', 'U_R_shape', 'V_R_shape',
                  'u1_R', 'u2_R', 'v1_R', 'v2_R')


class _TensorShard:
    """Read a small number of tensors directly, with up-front byte budgets."""

    def __init__(self, path, max_header_bytes=16 * 1024 * 1024):
        self.path = Path(path)
        size = self.path.stat().st_size
        if size < 10:
            raise CheckpointFormatError('File too small to be safetensors')
        with self.path.open('rb') as handle:
            raw_size = handle.read(8)
            header_bytes = struct.unpack('<Q', raw_size)[0]
            if header_bytes < 2 or header_bytes > max_header_bytes or header_bytes > size - 8:
                raise CheckpointFormatError('Invalid or oversized safetensors header')
            try:
                self.index = json.loads(handle.read(header_bytes).decode('utf8'))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CheckpointFormatError('Invalid safetensors JSON header') from exc
        if not isinstance(self.index, dict):
            raise CheckpointFormatError('Safetensors header must be an object')
        self.data_start = header_bytes + 8
        self.data_length = size - self.data_start

    def descriptor(self, name):
        if name not in self.index:
            raise CheckpointFormatError('Missing tensor ' + name)
        info = self.index[name]
        if not isinstance(info, dict) or info.get('dtype') not in DTYPES:
            raise CheckpointFormatError('Unsupported tensor dtype ' + name)
        shape, offsets = info.get('shape'), info.get('data_offsets')
        if (not isinstance(shape, list) or not shape or
                any(type(dim) is not int or dim < 0 for dim in shape) or
                not isinstance(offsets, list) or len(offsets) != 2 or
                any(type(v) is not int for v in offsets)):
            raise CheckpointFormatError('Invalid tensor metadata ' + name)
        start, end = offsets
        if start < 0 or end < start or end > self.data_length:
            raise CheckpointFormatError('Tensor offset out of bounds ' + name)
        width = DTYPES[info['dtype']][1]
        expected = width
        for dim in shape:
            expected *= dim
        if expected != end - start:
            raise CheckpointFormatError('Tensor shape and byte count mismatch ' + name)
        return info

    def read(self, name):
        info = self.descriptor(name)
        start, end = info['data_offsets']
        size = end - start
        with self.path.open('rb') as f:
            f.seek(self.data_start + start)
            data = f.read(size)
        if len(data) != size:
            raise CheckpointFormatError('Short tensor read ' + name)
        arr = np.frombuffer(data, dtype=DTYPES[info['dtype']][0]).copy()
        if info['dtype'] == 'BF16':
            arr = (arr.astype('<u4') << 16).view('<f4')
        return arr.reshape(info['shape'])


class _ShardGroup:
    """Resolve only the requested tensor keys from a model's shards."""

    def __init__(self, path):
        source = Path(path)
        self.single = None
        self.weight_map = None
        self.root = None
        self._shards = {}
        if source.is_file():
            self.single = _TensorShard(source)
            return
        if not source.is_dir():
            raise CheckpointFormatError('Checkpoint path is not a safetensors file or directory')
        self.root = source.resolve()
        index = self.root / 'model.safetensors.index.json'
        try:
            if index.stat().st_size > 16 * 1024 * 1024:
                raise CheckpointFormatError('Oversized safetensors shard index')
            manifest = json.loads(index.read_text(encoding='utf8'))
        except (OSError, ValueError) as exc:
            raise CheckpointFormatError('Invalid or missing model.safetensors.index.json') from exc
        weight_map = manifest.get('weight_map') if isinstance(manifest, dict) else None
        if not isinstance(weight_map, dict):
            raise CheckpointFormatError('Invalid shard weight_map')
        self.weight_map = weight_map

    def contains(self, key):
        return key in (self.single.index if self.single else self.weight_map)

    def _get_shard(self, key):
        if self.single:
            return self.single
        if key not in self.weight_map:
            raise CheckpointFormatError('Missing tensor ' + key)
        filename = self.weight_map[key]
        # Restrict references to flat shard filenames; no directory traversal.
        if (not isinstance(filename, str) or not filename.endswith('.safetensors') or
                filename in ('.', '..') or filename != Path(filename).name or
                '/' in filename or '\\' in filename):
            raise CheckpointFormatError('Unsafe shard filename in index')
        target = self.root / filename
        if not target.resolve().is_relative_to(self.root):
            raise CheckpointFormatError('Shard path escapes checkpoint directory')
        if filename not in self._shards:
            self._shards[filename] = _TensorShard(target)
        shard = self._shards[filename]
        if key not in shard.index:
            raise CheckpointFormatError('Shard index points to a missing tensor ' + key)
        return shard

    def descriptor(self, key):
        return self._get_shard(key).descriptor(key)

    def read(self, key):
        return self._get_shard(key).read(key)


@dataclass
class _PathFactors:
    u_words: np.ndarray
    v_words: np.ndarray
    u_shape: tuple[int, int]
    v_shape: tuple[int, int]
    u1: np.ndarray
    u2: np.ndarray
    v1: np.ndarray
    v2: np.ndarray

    @property
    def binary_factor_bytes(self):
        return self.u_words.nbytes + self.v_words.nbytes

    def cpu_parts(self):
        return (PackedBinaryMatrix.from_words(self.v_words.tolist(), self.v_shape[1]),
                PackedBinaryMatrix.from_words(self.u_words.tolist(), self.u_shape[1]),
                self.v1.tolist(), self.v2.tolist(), self.u1.tolist(), self.u2.tolist())

    def gpu_path(self, backend):
        from cuda_packed import CUDAPackedMatrix, _CudaPath, _cupy
        from cuda_warp import WarpCUDAPackedMatrix, _WarpPath
        cp = _cupy()
        packed_class, path_class = ((WarpCUDAPackedMatrix, _WarpPath) if backend == 'warp'
                                    else (CUDAPackedMatrix, _CudaPath))
        def mat(array, cols):
            words = cp.asarray(array.view('<u4'), dtype=cp.uint32)
            return packed_class(words=words, columns=cols, rows=array.shape[0],
                                storage_bytes=array.nbytes)
        return path_class(v=mat(self.v_words, self.v_shape[1]),
                          u=mat(self.u_words, self.u_shape[1]),
                          v1=cp.asarray(self.v1, dtype=cp.float32),
                          v2=cp.asarray(self.v2, dtype=cp.float32),
                          u1=cp.asarray(self.u1, dtype=cp.float32),
                          u2=cp.asarray(self.u2, dtype=cp.float32))


@dataclass
class SafetensorsLittleBitLayer:
    primary: _PathFactors
    bias: np.ndarray | None
    residual: _PathFactors | None = None

    @property
    def u_words(self):
        return self.primary.u_words

    @property
    def v_words(self):
        return self.primary.v_words

    @property
    def u_shape(self):
        return self.primary.u_shape

    @property
    def v_shape(self):
        return self.primary.v_shape

    @property
    def u1(self):
        return self.primary.u1

    @property
    def binary_factor_bytes(self):
        return self.primary.binary_factor_bytes + (self.residual.binary_factor_bytes if self.residual else 0)

    @classmethod
    def from_file(cls, path, prefix, *, max_layer_bytes=64 * 1024 * 1024, _shard_group=None):
        if type(max_layer_bytes) is not int or max_layer_bytes < 1:
            raise ValueError('max_layer_bytes must be a positive integer')
        prefix = prefix.rstrip('.') + '.'
        if prefix == '.' or '\x00' in prefix:
            raise CheckpointFormatError('Invalid module prefix')
        shard = _shard_group if _shard_group is not None else _ShardGroup(path)
        main = [prefix + name for name in REQUIRED]
        residual = [prefix + name for name in RESIDUAL_NAMES]
        has_residual = any(shard.contains(name) for name in residual)
        if has_residual and not all(shard.contains(name) for name in residual):
            raise CheckpointFormatError('Incomplete residual factor set')
        selected = main + (residual if has_residual else [])
        bias_name = prefix + 'bias'
        if shard.contains(bias_name):
            selected.append(bias_name)
        total = 0
        for name in selected:
            meta = shard.descriptor(name)
            start, end = meta['data_offsets']
            total += end - start
            if total > max_layer_bytes:
                raise CheckpointFormatError('Layer tensor data exceeds memory budget')

        def path(suffix):
            up, vp = ('U' + suffix + '_packed', 'V' + suffix + '_packed')
            us, vs = ('U' + suffix + '_shape', 'V' + suffix + '_shape')
            u_words, v_words = shard.read(prefix+up), shard.read(prefix+vp)
            u_shape_array, v_shape_array = shard.read(prefix+us), shard.read(prefix+vs)
            if (u_words.dtype != np.dtype('<i4') or v_words.dtype != np.dtype('<i4') or
                u_shape_array.dtype != np.dtype('<i8') or v_shape_array.dtype != np.dtype('<i8') or
                u_shape_array.shape != (2,) or v_shape_array.shape != (2,)):
                raise CheckpointFormatError('Invalid packed matrix dtypes or shape metadata')
            u_shape, v_shape = tuple(map(int, u_shape_array)), tuple(map(int, v_shape_array))
            if any(v <= 0 for v in u_shape + v_shape) or u_shape[1] != v_shape[0]:
                raise CheckpointFormatError('Incompatible U/V original dimensions')
            if u_words.shape != (u_shape[0], (u_shape[1] + 31)//32):
                raise CheckpointFormatError('Invalid U_packed tensor shape')
            if v_words.shape != (v_shape[0], (v_shape[1] + 31)//32):
                raise CheckpointFormatError('Invalid V_packed tensor shape')
            scales = []
            for label, expected in [('u1', u_shape[0]), ('u2', u_shape[1]),
                                    ('v1', v_shape[0]), ('v2', v_shape[1])]:
                array = shard.read(prefix+label+suffix)
                if array.dtype.kind not in 'f' or array.shape not in ((1, expected), (expected,)):
                    raise CheckpointFormatError('Invalid scale tensor ' + label + suffix)
                scales.append(array.reshape(-1).astype('float32'))
            return _PathFactors(u_words, v_words, u_shape, v_shape, *scales)

        primary = path('')
        additional = path('_R') if has_residual else None
        if additional and (additional.u_shape != primary.u_shape or additional.v_shape != primary.v_shape):
            raise CheckpointFormatError('Residual factor dimensions differ from primary')
        bias = shard.read(bias_name) if shard.contains(bias_name) else None
        if bias is not None:
            if bias.dtype.kind not in 'f' or bias.shape not in ((primary.u_shape[0],), (1, primary.u_shape[0])):
                raise CheckpointFormatError('Invalid bias tensor')
            bias = bias.reshape(-1).astype('float32')
        return cls(primary, bias, additional)

    def to_cpu_layer(self):
        return LittleBitPackedLinear(*self.primary.cpu_parts(),
                                     bias=self.bias.tolist() if self.bias is not None else None,
                                     residual=self.residual.cpu_parts() if self.residual else None)

    def forward_cpu(self, vector):
        return self.to_cpu_layer().forward(vector)

    def to_cuda_layer(self, *, backend='warp'):
        if backend not in ('warp', 'scalar'):
            raise ValueError("backend must be 'warp' or 'scalar'")
        from cuda_packed import CUDALittleBitLinear, _cupy
        from cuda_warp import WarpCUDALittleBitLinear
        cp = _cupy()
        factory = WarpCUDALittleBitLinear if backend == 'warp' else CUDALittleBitLinear
        return factory(primary=self.primary.gpu_path(backend),
                       residual=self.residual.gpu_path(backend) if self.residual else None,
                       bias=cp.asarray(self.bias, dtype=cp.float32) if self.bias is not None else None)


def write_synthetic_demo(path, prefix='model.layers.0.mlp.down_proj.'):
    """Write a tiny official-layout test file; NEVER a pretrained checkpoint."""
    prefix = prefix.rstrip('.') + '.'
    v = PackedBinaryMatrix.from_signs([[1, -1, 1, -1, 1], [-1, 1, 1, -1, -1], [1, 1, -1, -1, 1]])
    u = PackedBinaryMatrix.from_signs([[1, 1, -1], [-1, 1, 1]])
    raw = {
        'U_packed': ('I32', np.asarray(u.rows, dtype='<i4')),
        'V_packed': ('I32', np.asarray(v.rows, dtype='<i4')),
        'U_shape': ('I64', np.asarray([2, 3], dtype='<i8')),
        'V_shape': ('I64', np.asarray([3, 5], dtype='<i8')),
        'u1': ('F32', np.asarray([[.25, .5]], dtype='<f4')),
        'u2': ('F32', np.asarray([[.7, .9, 1.1]], dtype='<f4')),
        'v1': ('F32', np.asarray([[.3, .6, .8]], dtype='<f4')),
        'v2': ('F32', np.asarray([[1., 2., 3., 4., 5.]], dtype='<f4')),
        'bias': ('F32', np.asarray([.1, -.2], dtype='<f4')),
    }
    payload = bytearray()
    header = {}
    for name, (dtype, arr) in raw.items():
        begin = len(payload)
        payload.extend(arr.tobytes())
        header[prefix+name] = {'dtype': dtype, 'shape': list(arr.shape),
                               'data_offsets': [begin, len(payload)]}
    blob = json.dumps(header).encode('utf8')
    Path(path).write_bytes(struct.pack('<Q', len(blob))+blob+payload)


def demo():
    with TemporaryDirectory() as temp:
        filename = Path(temp) / 'SYNTHETIC_LITTLEBIT.safetensors'
        prefix = 'model.layers.0.mlp.down_proj.'
        write_synthetic_demo(filename, prefix)
        layer = SafetensorsLittleBitLayer.from_file(filename, prefix)
        x = [1., -2., 3., -4., 5.]
        expected = layer.forward_cpu(x)
        report = {
            'status': 'LITTLEBIT_SAFETENSORS_LAYER_DEMO_OK',
            'checkpoint': 'SYNTHETIC_ONLY',
            'trained_model': False,
            'whole_llm': False,
            'source_format': 'Samsung LittleBit-2 layer keys in safetensors',
            'input_features': layer.v_shape[1],
            'rank': layer.v_shape[0],
            'output_features': layer.u_shape[0],
            'packed_binary_bytes': layer.binary_factor_bytes,
            'cuda_executed': False,
        }
        from cuda_packed import cuda_is_available
        if cuda_is_available():
            gpu = layer.to_cuda_layer(backend='warp')
            actual = gpu.forward(x).get().tolist()
            error = max(abs(a-b) for a,b in zip(expected,actual))
            if any(abs(a-b) > 1e-4*(1+abs(a)) for a,b in zip(expected,actual)):
                raise AssertionError('Loaded CUDA layer mismatches CPU reference')
            report['cuda_executed'] = True
            report['max_abs_error'] = error
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Read one Samsung-compatible LittleBit packed safetensors layer')
    parser.add_argument('--checkpoint', help='Local safetensors shard (never downloaded automatically)')
    parser.add_argument('--prefix', help='Layer prefix, e.g. model.layers.0.mlp.down_proj')
    parser.add_argument('--max-layer-mib', type=int, default=64)
    args = parser.parse_args()
    if args.checkpoint:
        if not args.prefix:
            parser.error('--prefix is required when --checkpoint is supplied')
        layer = SafetensorsLittleBitLayer.from_file(args.checkpoint, args.prefix,
                                                   max_layer_bytes=args.max_layer_mib * 1024 * 1024)
        print(json.dumps({'status': 'LITTLEBIT_PACKED_LAYER_LOADED',
                          'training_quality': 'UNVERIFIED', 'whole_llm': False,
                          'u_shape': layer.u_shape, 'v_shape': layer.v_shape,
                          'residual': layer.residual is not None,
                          'packed_binary_bytes': layer.binary_factor_bytes}, indent=2))
    else:
        print(json.dumps(demo(), indent=2))
