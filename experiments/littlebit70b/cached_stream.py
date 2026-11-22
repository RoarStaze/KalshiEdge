"""Experimental bounded LittleBit CUDA streamer with cached Safetensors metadata.

Keeps the verified CUDALayerStream unchanged. Safetensors descriptor/header
parsing is reused only within one run; the actual tensor bytes are read every
layer. The new path avoids the per-layer forced Python garbage collector, but
it still synchronizes CUDA and releases packed weights at each boundary.

Private CuPy pool cap does not measure or limit total device VRAM. Synthetic
benchmarks are not a full Gemma checkpoint, transformer, or language model.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
from pathlib import Path
import statistics
from tempfile import TemporaryDirectory
from time import perf_counter


class CachedLayerReader:
    """Per-run metadata cache, with fail-closed file identity checks.

    Files are expected immutable during a run. Cached metadata is not shared
    across runs. This prevents stale offsets from being reused in later runs.
    """

    def __init__(self, *, max_layer_bytes=64*1024*1024, max_cached_files=8):
        if type(max_layer_bytes) is not int or max_layer_bytes < 1:
            raise ValueError('max_layer_bytes must be positive int')
        if type(max_cached_files) is not int or not 1 <= max_cached_files <= 8:
            raise ValueError('max_cached_files must be an integer in 1..8')
        self.max_layer_bytes=max_layer_bytes
        self.max_cached_files=max_cached_files
        self._groups={}
        self.metadata_index_loads=0

    @property
    def cached_files(self):
        return len(self._groups)

    @staticmethod
    def _identity(path):
        """Path size + modification timestamp for best-effort change detection."""
        target=Path(path).resolve()
        if target.is_dir():
            target=target/'model.safetensors.index.json'
        stat=target.stat()
        return (target,stat.st_size,stat.st_mtime_ns)

    def get(self, step):
        from safetensors_layer import _ShardGroup, SafetensorsLittleBitLayer, CheckpointFormatError
        from layer_stream import LayerStep
        if not isinstance(step, LayerStep):
            raise TypeError('Expected LayerStep')
        path=Path(step.checkpoint).resolve()
        if path not in self._groups:
            if len(self._groups)>=self.max_cached_files:
                raise CheckpointFormatError('Metadata cache limit exceeded')
            identity=self._identity(path)
            group=_ShardGroup(path)
            self._groups[path]=(identity,group)
            self.metadata_index_loads+=1
        identity,group=self._groups[path]
        if self._identity(path)!=identity:
            raise CheckpointFormatError('Checkpoint changed during cached read')
        # For sharded checkpoint files, verify the already-opened shard
        # files are unchanged before reading another layer.
        if group.single is None:
            if not hasattr(self, '_seen_shards'):
                self._seen_shards = {}
            for shard in group._shards.values():
                original = self._seen_shards.get(shard.path)
                if original is not None and self._identity(shard.path) != original:
                    raise CheckpointFormatError('Checkpoint shard changed during cached read')
        result=SafetensorsLittleBitLayer.from_file(
            path,step.prefix,max_layer_bytes=self.max_layer_bytes,_shard_group=group)
        if group.single is None:
            for shard in group._shards.values():
                if shard.path not in self._seen_shards:
                    self._seen_shards[shard.path]=self._identity(shard.path)
        return result


class LeanCUDALayerStream:
    """Stream sequential packed linear layers with metadata cache; CUDA only."""

    def __init__(self, *,pool_limit_bytes=32*1024*1024,max_layer_bytes=64*1024*1024,
                 backend='warp',metadata_cache_entries=8):
        if type(pool_limit_bytes) is not int or pool_limit_bytes<1:
            raise ValueError('pool_limit_bytes must be positive int')
        if type(max_layer_bytes) is not int or max_layer_bytes<1:
            raise ValueError('max_layer_bytes must be positive int')
        if backend not in ('warp','scalar'):
            raise ValueError('backend must be warp or scalar')
        if type(metadata_cache_entries) is not int or not 1<=metadata_cache_entries<=8:
            raise ValueError('metadata_cache_entries must be in 1..8')
        self.pool_limit_bytes=pool_limit_bytes
        self.max_layer_bytes=max_layer_bytes
        self.backend=backend
        self.metadata_cache_entries=metadata_cache_entries
        self.pool=None

    def run(self,steps,vector):
        from cuda_packed import _cupy
        from layer_stream import LayerStep, MemoryBudgetError
        if not steps or any(not isinstance(s,LayerStep) for s in steps):
            raise ValueError('Expected nonempty sequence of LayerStep')
        cp=_cupy()
        self.pool=cp.cuda.MemoryPool()
        self.pool.set_limit(size=self.pool_limit_bytes)
        reader=CachedLayerReader(max_layer_bytes=self.max_layer_bytes,
                                 max_cached_files=self.metadata_cache_entries)
        current=list(vector)
        peak=0
        evicted=0
        retained=0
        for i,step in enumerate(steps):
            layer=reader.get(step)
            if len(current)!=layer.v_shape[1]:
                raise ValueError(f'Layer {i}: input dimension does not match {layer.v_shape[1]}')
            gpu_layer=None
            gpu_output=None
            try:
                with cp.cuda.using_allocator(self.pool.malloc):
                    gpu_layer=layer.to_cuda_layer(backend=self.backend)
                    gpu_output=gpu_layer.forward(current)
                    current=gpu_output.get().tolist()
                    peak=max(peak,int(self.pool.total_bytes()))
            except cp.cuda.memory.OutOfMemoryError as exc:
                raise MemoryBudgetError(f'Layer {i}: private pool limit exceeded') from exc
            finally:
                cp.cuda.get_current_stream().synchronize()
                gpu_output=None
                gpu_layer=None
                layer=None
                self.pool.free_all_blocks()
                retained=int(self.pool.total_bytes())
                evicted+=1
                if self.pool.used_bytes()!=0:
                    raise MemoryBudgetError('GPU allocations remain after layer eviction')
        return {
            'status':'LITTLEBIT_CACHED_CUDA_STREAM_OK',
            'output':current,'layer_count':len(steps),'eviction_count':evicted,
            'gpu_pool_limit_bytes':self.pool_limit_bytes,
            'peak_pool_bytes':peak,
            'retained_pool_bytes_after_last_evict':retained,
            'live_pool_bytes_after_last_evict':int(self.pool.used_bytes()),
            'metadata_index_loads':reader.metadata_index_loads,
            'cached_files':reader.cached_files,
            'per_layer_gc_calls':0,
            'checkpoint':'UNVERIFIED_LOCAL_CHECKPOINT',
            'trained_model':None,'whole_llm':False,'cuda_executed':True,
            'backend':self.backend,
        }


def summarize_pairs(pairs):
    if not pairs:
        raise ValueError('At least one paired sample required')
    baseline=[]; cached=[]
    for pair in pairs:
        if not isinstance(pair,(tuple,list)) or len(pair)!=2:
            raise ValueError('Expected 2-element timing pair')
        a,b=pair
        if any(type(t) not in (float,int) or not math.isfinite(t) or t<=0 for t in (a,b)):
            raise ValueError('Pair timings must be positive finite numbers')
        baseline.append(float(a)); cached.append(float(b))
    ba=statistics.median(baseline); ca=statistics.median(cached)
    return {'sample_count':len(pairs),'baseline_median_ms':round(ba,3),
            'cached_median_ms':round(ca,3),
            'speedup_baseline_over_cached':round(ba/ca,3),
            'baseline_range_ms':[round(min(baseline),3),round(max(baseline),3)],
            'cached_range_ms':[round(min(cached),3),round(max(cached),3)],
            'timing_variability_warning':max(baseline)>=2*min(baseline) or max(cached)>=2*min(cached)}


def paired_profile(config):
    from cuda_packed import _cupy
    from layer_stream import LayerStep,CUDALayerStream
    from stream_profile import build_checkpoint
    import numpy as np
    cp=_cupy()
    with TemporaryDirectory(prefix='littlebit-cached-profile-') as directory:
        path=Path(directory)/'SYNTHETIC_ONLY.safetensors'
        raw_steps,file_bytes=build_checkpoint(path,config)
        steps=[LayerStep(s.checkpoint,s.prefix) for s in raw_steps]
        x=np.sin(np.arange(config.width,dtype=np.float32)*np.float32(.013)).tolist()
        kwargs={'pool_limit_bytes':config.pool_limit_mib*1024*1024,'backend':'warp'}
        base_ref=CUDALayerStream(**kwargs).run(steps,x)['output']
        cached_ref=LeanCUDALayerStream(**kwargs).run(steps,x)
        if any(abs(a-b)>1e-4*(1+abs(a)) for a,b in zip(base_ref,cached_ref['output'])):
            raise AssertionError('Cached path numerically differs from reference')
        pairs=[]
        report=cached_ref
        for i in range(config.repeats):
            order=('baseline','cached') if i%2==0 else ('cached','baseline')
            times={}
            for name in order:
                runner=(CUDALayerStream(**kwargs) if name=='baseline' else LeanCUDALayerStream(**kwargs))
                t0=perf_counter(); result=runner.run(steps,x); times[name]=(perf_counter()-t0)*1000
                if len(result['output'])!=len(base_ref) or any(
                    abs(a-b)>1e-4*(1+abs(a)) for a,b in zip(base_ref,result['output'])):
                    raise AssertionError('CUDA result mismatch')
                if result['eviction_count']!=config.layers or result['live_pool_bytes_after_last_evict']!=0:
                    raise RuntimeError('Failed layer eviction / live pool allocations')
                if name=='cached':report=result
            pairs.append((times['baseline'],times['cached']))
        name=cp.cuda.runtime.getDeviceProperties(cp.cuda.runtime.getDevice())['name']
        if isinstance(name,bytes):name=name.decode('utf-8',errors='replace')
        return {'status':'LITTLEBIT_CACHED_STREAM_COMPARISON_OK',
                'checkpoint':'SYNTHETIC_ONLY','trained_model':False,'whole_llm':False,'cuda_executed':True,
                'device':str(name),'width':config.width,'rank':config.rank,'layers':config.layers,
                'checkpoint_file_bytes':file_bytes,'metadata_index_loads':report['metadata_index_loads'],
                'per_layer_gc_calls_cached':0,
                'cached_pool_peak_bytes':report['peak_pool_bytes'],
                'live_pool_bytes_after_run':report['live_pool_bytes_after_last_evict'],
                'vram_peak_measured':False,
                'timing_scope':'Disk reads, CPU/GPU transfers, CUDA, synchronizations, Python cleanup',
                **summarize_pairs(pairs)}


def main():
    from stream_profile import ProfileConfig
    parser=argparse.ArgumentParser(description='Paired cached vs baseline LittleBit layer stream profile')
    parser.add_argument('--width',type=int,default=1024)
    parser.add_argument('--rank',type=int,default=128)
    parser.add_argument('--layers',type=int,default=8)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--pool-limit-mib',type=int,default=32)
    parser.add_argument('--max-file-mib',type=int,default=16)
    args=parser.parse_args()
    cfg=ProfileConfig(width=args.width,rank=args.rank,layers=args.layers,
                      repeats=args.repeats,pool_limit_mib=args.pool_limit_mib,
                      max_file_mib=args.max_file_mib)
    print(json.dumps(paired_profile(cfg),indent=2))

if __name__=='__main__':main()
