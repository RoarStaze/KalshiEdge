"""Correctness and fail-closed tests for cached metadata and lean CUDA streaming."""
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stream_profile import ProfileConfig, build_checkpoint
from layer_stream import LayerStep, run_cpu_sequence
from cached_stream import CachedLayerReader, LeanCUDALayerStream, summarize_pairs
from safetensors_layer import CheckpointFormatError
from cuda_packed import cuda_is_available


class CachedMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'weights.safetensors'
        self.steps, _ = build_checkpoint(self.path, ProfileConfig(width=33, rank=7, layers=3, repeats=1))
        self.steps = [LayerStep(s.checkpoint, s.prefix) for s in self.steps]

    def test_reuses_one_header_index_and_matches_existing_cpu_reference(self):
        reader = CachedLayerReader()
        outputs = []
        expected = []
        for step in self.steps:
            layer = reader.get(step)
            x = [math.sin(i*.03) for i in range(33)]
            outputs.append(layer.forward_cpu(x))
        self.assertEqual(reader.metadata_index_loads, 1)
        self.assertEqual(reader.cached_files, 1)
        for step, got in zip(self.steps, outputs):
            from safetensors_layer import SafetensorsLittleBitLayer
            ref = SafetensorsLittleBitLayer.from_file(step.checkpoint, step.prefix)
            self.assertEqual(got, ref.forward_cpu([math.sin(i*.03) for i in range(33)]))

    def test_cached_reader_accepts_standard_sharded_manifest(self):
        # Each synthetic layer gets its own Safetensors shard; readers reuse
        # the manifest, but also check previously opened shard identities.
        import json, struct
        from stream_profile import build_checkpoint
        original = self.path.read_bytes()
        length = struct.unpack('<Q', original[:8])[0]
        header = json.loads(original[8:8+length])
        payload = original[8+length:]
        self.path.unlink()
        mapping = {}
        for i in range(3):
            name = f'model-{i+1:05d}-of-00003.safetensors'
            shard_entries={k:v for k,v in header.items() if k.startswith(f'synthetic.layers.{i}.linear.')}
            new_header={}
            data=bytearray()
            for key, meta in shard_entries.items():
                a,b=meta['data_offsets']
                start=len(data); data.extend(payload[a:b])
                new_header[key]={'dtype':meta['dtype'],'shape':meta['shape'],'data_offsets':[start,len(data)]}
                mapping[key]=name
            encoded=json.dumps(new_header).encode('utf-8')
            (Path(self.tmp.name)/name).write_bytes(struct.pack('<Q',len(encoded))+encoded+data)
        (Path(self.tmp.name)/'model.safetensors.index.json').write_text(json.dumps({'weight_map':mapping}))
        reader=CachedLayerReader()
        for i in range(3):
            step=LayerStep(Path(self.tmp.name),self.steps[i].prefix)
            got=reader.get(step)
            self.assertEqual(got.u_shape,(33,7))
        self.assertEqual(reader.metadata_index_loads,1)

    def test_invalidated_when_file_changes_between_layers(self):
        reader = CachedLayerReader()
        reader.get(self.steps[0])
        self.path.write_bytes(self.path.read_bytes() + b'X')
        with self.assertRaises(CheckpointFormatError):
            reader.get(self.steps[1])

    def test_enforces_selected_tensor_budget(self):
        reader = CachedLayerReader(max_layer_bytes=8)
        with self.assertRaisesRegex(CheckpointFormatError, 'budget'):
            reader.get(self.steps[0])

    def test_max_metadata_cache_entries(self):
        reader = CachedLayerReader(max_cached_files=1)
        reader.get(self.steps[0])
        second = Path(self.tmp.name) / 'weights2.safetensors'
        build_checkpoint(second, ProfileConfig(width=33, rank=7, layers=1))
        with self.assertRaisesRegex(CheckpointFormatError, 'cache limit'):
            reader.get(LayerStep(second,'synthetic.layers.0.linear'))

    def test_nonexistent_path_rejected(self):
        with self.assertRaises((OSError,CheckpointFormatError)):
            CachedLayerReader().get(LayerStep(Path(self.tmp.name)/'missing.safetensors','any'))

    def test_invalid_configuration_rejected(self):
        for params in ({'max_cached_files':0}, {'max_layer_bytes':-1}, {'max_cached_files':True}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                CachedLayerReader(**params)


class SummaryContracts(unittest.TestCase):
    def test_pair_medians_and_ratio(self):
        result=summarize_pairs([(20.0,12.0),(22.0,10.0),(21.0,11.0)])
        self.assertEqual(result['baseline_median_ms'],21.0)
        self.assertEqual(result['cached_median_ms'],11.0)
        self.assertEqual(result['speedup_baseline_over_cached'],1.909)

    def test_rejects_nan_and_missing_pairs(self):
        for pairs in ([], [(1.0,float('nan'))], [(0.0,1.0)], [(1.,) ]):
            with self.assertRaises(ValueError):
                summarize_pairs(pairs)

    def test_lean_constructor_rejects_invalid_budgets(self):
        for kw in ({'pool_limit_bytes':0}, {'max_layer_bytes':-1}, {'backend':'bad'}, {'metadata_cache_entries':0}):
            with self.assertRaises(ValueError):
                LeanCUDALayerStream(**kw)


@unittest.skipUnless(cuda_is_available(), 'GPU/CuPy required')
class GPUParityAndEvictionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name)/'weights.safetensors'
        self.steps=[LayerStep(s.checkpoint,s.prefix) for s in build_checkpoint(p,ProfileConfig(width=65,rank=17,layers=3))[0]]
        self.x=[math.sin(i*.013) for i in range(65)]

    def test_lean_gpu_matches_cpu_and_releases_pool(self):
        from layer_stream import CUDALayerStream
        ref=CUDALayerStream().run(self.steps,self.x)
        test=LeanCUDALayerStream().run(self.steps,self.x)
        self.assertEqual(test['eviction_count'],3)
        self.assertEqual(test['metadata_index_loads'],1)
        self.assertEqual(test['per_layer_gc_calls'],0)
        self.assertEqual(test['live_pool_bytes_after_last_evict'],0)
        for got,want in zip(test['output'],ref['output']):
            self.assertAlmostEqual(got,want,delta=1e-4*(1+abs(want)))

    def test_tiny_pool_fails_closed(self):
        with self.assertRaises((MemoryError,RuntimeError)):
            LeanCUDALayerStream(pool_limit_bytes=1).run(self.steps,self.x)

    def test_multiple_runs_release_pool(self):
        runner=LeanCUDALayerStream()
        for _ in range(3):
            report=runner.run(self.steps,self.x)
            self.assertEqual(report['live_pool_bytes_after_last_evict'],0)
