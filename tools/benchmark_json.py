"""Read-only source benchmark; all writes go to a disposable sibling directory.

Run with repo on PYTHONPATH; optional orjson must be installed independently.
No SQLite connection is opened. Reports warm-cache wall and process CPU times.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import statistics
import tempfile
import time
from unittest.mock import patch

from bookpipe import state_files
from bookpipe.util import atomic_json, digest, read_json

try:
    import orjson
except ImportError:
    orjson = None


def summary(values):
    ordered = sorted(values)
    return {'p50_ms': statistics.median(values),
            'p95_ms': ordered[math.ceil(.95 * len(values)) - 1],
            'max_ms': max(values)}


def measure(cases, samples):
    data = {name: {'wall': [], 'cpu': []} for name in cases}
    for fn in cases.values():
        fn()  # warm up each path; discard its timing
    rng = random.Random(20260927)
    for _ in range(samples):
        names = list(cases)
        rng.shuffle(names)
        for name in names:
            wall, cpu = time.perf_counter(), time.process_time()
            result = cases[name]()
            cpu = time.process_time() - cpu
            wall = time.perf_counter() - wall
            data[name]['wall'].append(wall * 1000)
            data[name]['cpu'].append(cpu * 1000)
            del result
    return {name: {'wall': summary(v['wall']), 'cpu': summary(v['cpu']),
                   'wall_samples_ms': v['wall']} for name, v in data.items()}


def atomic_bytes(path, raw):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.bench-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def benchmark_workflows(root, samples):
    """Compare complete JSON operations on copies, including bounded job replay."""
    from bookpipe.runtime.registry import JobRegistry

    with tempfile.TemporaryDirectory(prefix='.json-workflows-', dir=root.parent) as tmp:
        scratch = Path(tmp)
        book = read_json(root / 'book.json')
        out = scratch / 'book.json'

        def compact_write():
            atomic_bytes(out, json.dumps(book, ensure_ascii=False, separators=(',', ':')).encode())

        def repeated():
            for i in range(10):
                book['_benchmark'] = i
                atomic_json(out, book)

        def batched():
            for i in range(10):
                book['_benchmark'] = i
            atomic_json(out, book)

        batch_samples = min(samples, 10)
        results = {'book_samples': batch_samples, 'book': measure({'atomic_compact_stdlib': compact_write,
                                    'ten_changes_ten_writes': repeated,
                                    'ten_changes_one_write': batched}, batch_samples)}
        source = root.parent / '.runtime'
        if source.exists():
            metadata = read_json(source / 'registry.json')
            paths = [source / 'registry.json'] + [
                source / 'jobs' / (checksum + '.json') for checksum in metadata['jobs'].values()]
            hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            for path in paths:
                target = scratch / path.relative_to(root.parent)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            def startup():
                registry = JobRegistry(scratch)
                registry.close()
            results['jobs_startup'] = measure({'load_all_records': startup}, samples)
            registry = JobRegistry(scratch)
            try:
                largest = max(registry._records, key=lambda key: len(json.dumps(registry._records[key])))
                results['jobs_count'] = len(registry._records)
                results['job_append'] = measure({'append_event': lambda: registry.append(
                    largest, {'kind': 'benchmark', 'text': 'x' * 500})}, samples)
            finally:
                registry.close()
            results['runtime_sources_unchanged'] = all(
                hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in hashes.items())
            if not results['runtime_sources_unchanged']:
                raise RuntimeError('Runtime sources changed during benchmark')
        return results


def benchmark_confirmation(root, samples):
    from bookpipe.infrastructure.project_files import LocalProjectFiles
    from bookpipe.util import plan_fingerprint

    with tempfile.TemporaryDirectory(prefix='.json-confirm-', dir=root.parent) as tmp:
        path = Path(tmp) / 'book.json'
        shutil.copyfile(root / 'book.json', path)
        out = Path(tmp) / 'output.json'
        book = read_json(path)
        files = LocalProjectFiles()
        cases = {
            'app_read_json': lambda: read_json(path),
            'app_atomic_json': lambda: atomic_json(out, book),
            'plan_fingerprint': lambda: plan_fingerprint(book),
            'validated_book_cache_hit_borrow': lambda: files.validated_book(path, plan_fingerprint, readonly=True),
            'validated_book_cache_hit_copy': lambda: files.validated_book(path, plan_fingerprint),
        }
        if orjson:
            cases['atomic_orjson'] = lambda: atomic_bytes(out, orjson.dumps(book))
        return measure(cases, samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=30)
    args = parser.parse_args()
    if args.samples < 2:
        parser.error('at least two samples required')
    root = args.workspace.resolve()
    if args.output.resolve().is_relative_to(root.parent):
        parser.error('write the report outside the workspace root')
    head, commit, tables = state_files.load_state(root)
    state_sources = [root / 'state/HEAD.json',
                     root / 'state/commits' / (head['commit'] + '.json')]
    state_sources += [root / 'state/objects' / (i + '.json') for i in commit['tables'].values()]
    runtime = root.parent / '.runtime'
    jobs = sorted((runtime / 'jobs').glob('*.json'), key=lambda p: p.stat().st_size, reverse=True)
    paths = [root / 'book.json', root / 'analysis_plan.json', root / 'terms.review.json']
    segments = sorted((root / 'analysis_inputs').glob('*.json'))
    if segments:
        paths.append(next((p for p in segments if p.name == 'ch0011_a001.json'), segments[0]))
    if jobs:
        paths.append(jobs[0])
    protected = list(dict.fromkeys(paths + state_sources))
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    report = {'started_at': datetime.now(timezone.utc).isoformat(),
              'environment': {'python': platform.python_version(), 'platform': platform.platform(),
              'cpu_count': os.cpu_count(), 'load_start': os.getloadavg(),
              'orjson': orjson.__version__ if orjson else None},
              'samples': args.samples, 'cache': 'warm; no drop_caches', 'files': {}}
    with tempfile.TemporaryDirectory(prefix='.json-benchmark-', dir=root.parent) as tmp:
        scratch = Path(tmp)
        assert scratch.stat().st_dev == root.stat().st_dev
        report['same_filesystem'] = True
        for index, source in enumerate(paths):
            raw = source.read_bytes()
            value = json.loads(raw)
            pretty = json.dumps(value, ensure_ascii=False, indent=2).encode() + b'\n'
            compact = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
            fast = orjson.dumps(value) if orjson else None
            if fast is not None:
                assert json.loads(fast) == value
                assert orjson.loads(raw) == value
            copied = scratch / f'input-{index}.json'
            copied.write_bytes(raw)
            out = scratch / 'output.json'
            def modify_write():
                obj = read_json(copied)
                # Representative one-field change; existing structure is retained.
                if isinstance(obj, dict):
                    obj['_benchmark'] = 1
                atomic_json(out, obj)
            cases = {
                'read_bytes': copied.read_bytes,
                'decode_utf8': lambda: raw.decode('utf-8'),
                'parse_stdlib_bytes': lambda: json.loads(raw),
                'app_read_json': lambda: read_json(copied),
                'serialize_pretty': lambda: json.dumps(value, ensure_ascii=False, indent=2).encode() + b'\n',
                'serialize_compact': lambda: json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode(),
                'canonical_digest': lambda: digest(value),
                'write_pretty_no_fsync': lambda: out.write_bytes(pretty),
                'atomic_pretty_bytes': lambda: atomic_bytes(out, pretty),
                'atomic_compact_bytes': lambda: atomic_bytes(out, compact),
                'app_atomic_json': lambda: atomic_json(out, value),
                'read_modify_atomic_json': modify_write,
            }
            if orjson:
                cases.update({'parse_orjson': lambda: orjson.loads(raw),
                              'serialize_orjson': lambda: orjson.dumps(value),
                              'atomic_orjson': lambda: atomic_bytes(out, orjson.dumps(value))})
            report['files'][str(source)] = {'bytes': len(raw), 'pretty_bytes': len(pretty),
                                            'compact_bytes': len(compact),
                                            'timings': measure(cases, args.samples)}
            print(f'Finished {source.name} ({len(raw)} bytes)', flush=True)
        # Disaggregate durable write stages, always on a fresh disposable file.
        raw = paths[0].read_bytes()
        stages = {k: [] for k in ('write_and_flush', 'fsync_file', 'rename', 'fsync_directory')}
        for _ in range(args.samples):
            fd, name = tempfile.mkstemp(dir=scratch)
            with os.fdopen(fd, 'wb') as f:
                start = time.perf_counter(); f.write(raw); f.flush()
                stages['write_and_flush'].append((time.perf_counter()-start)*1000)
                start = time.perf_counter(); os.fsync(f.fileno())
                stages['fsync_file'].append((time.perf_counter()-start)*1000)
            start = time.perf_counter(); os.replace(name, scratch / 'stages.json')
            stages['rename'].append((time.perf_counter()-start)*1000)
            fd = os.open(scratch, os.O_DIRECTORY)
            start = time.perf_counter(); os.fsync(fd)
            stages['fsync_directory'].append((time.perf_counter()-start)*1000)
            os.close(fd)
        report['durable_write_stages'] = {k: summary(v) for k, v in stages.items()}
        # Exercise only JSON functions, never connect_state/snapshot/restore.
        state_copy = scratch / 'workspace'
        for p in state_sources:
            target = state_copy / p.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
        mutable = copy.deepcopy(tables)
        current_head, revision = head, commit['revision']
        if not mutable['chunks']:
            raise RuntimeError('benchmark requires a workspace with chunks')
        def publish_change():
            nonlocal current_head, revision
            revision += 1
            mutable['chunks'][0]['status'] = f'benchmark-{revision}'
            current_head, _ = state_files.publish(state_copy, mutable, current_head, revision)
        report['state'] = {'table_bytes': {k: (root/'state/objects'/(i+'.json')).stat().st_size
                                          for k, i in commit['tables'].items()},
                           'timings': measure({'load_state': lambda: state_files.load_state(state_copy),
                                               'publish_one_chunk': publish_change}, args.samples)}
        counts = {'reads': 0, 'read_bytes': 0, 'writes': 0, 'written_bytes': 0, 'fsync': 0}
        original_read, original_write, original_sync = state_files.read_json, state_files.atomic_json, os.fsync
        def counted_read(p):
            counts['reads'] += 1; counts['read_bytes'] += p.stat().st_size
            return original_read(p)
        def counted_write(p, obj):
            original_write(p, obj)
            counts['writes'] += 1; counts['written_bytes'] += p.stat().st_size
        def counted_sync(fd):
            counts['fsync'] += 1
            return original_sync(fd)
        with patch.object(state_files, 'read_json', counted_read), patch.object(state_files, 'atomic_json', counted_write), patch.object(os, 'fsync', counted_sync):
            publish_change()
        report['state']['one_chunk_io_counts'] = counts
        # Append illustrates a log operation, not an equivalent random update.
        record = {'kind': 'benchmark', 'segment': 'ch0011_a001', 'text': 'ąęłó' * 100}
        line = json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
        log = scratch / 'events.jsonl'
        def append(durable):
            payload = json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
            with log.open('ab') as f:
                f.write(payload)
                if durable:
                    f.flush(); os.fsync(f.fileno())
        report['jsonl'] = {'record_bytes': len(line), 'timings': measure({
            'append_buffered': lambda: append(False), 'append_fsync': lambda: append(True)}, args.samples)}
        print('Finished state, fsync and JSONL', flush=True)
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    report['sources_unchanged'] = before == after
    report['verified_source_files'] = len(protected)
    report['workflows'] = benchmark_workflows(root, args.samples)
    report['confirmation'] = benchmark_confirmation(root, args.samples)
    report['environment']['load_end'] = os.getloadavg()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    if before != after:
        raise RuntimeError('Source changed during benchmark (possibly concurrent application activity)')

if __name__ == '__main__':
    main()
