# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Online clip evaluation; captures are replayable with replay_campaign.py."""
import json
import hashlib
import subprocess
import sys
import importlib.metadata
from pathlib import Path
from types import SimpleNamespace
import uuid

from .capture import atomic_bytes, atomic_json, digest, now, read_json, seal
from .clip_scoring import ClipScorer, VERSION


def prepare(data_path, ingestion_path):
    path = Path(data_path)
    if path.is_dir():
        queries = read_json(path / 'gt/queries_gt.json')['queries']
        gallery = read_json(path / 'additional_gt/clips_gt.json')['gallery']
    else:
        data = read_json(path)
        queries, gallery = data['queries'], data['gallery']
    records = read_json(ingestion_path)['clips']
    mapping = {c['dataset_id']: c for c in records}
    ids = [g['chunk_id'] for g in gallery]
    if len(mapping) != len(records) or len(ids) != len(set(ids)) or set(ids) != set(mapping):
        raise ValueError('Ingestion mapping must exactly match the explicit gallery')
    for q in queries:
        q.setdefault('query_domain', 'unknown')
        if not isinstance(q['query_id'], str) or not q['query_id'].replace('_', '').replace('-', '').isalnum():
            raise ValueError('Query IDs must be safe path components (letters, digits, underscores, hyphens)')
        if not isinstance(q.get('query'), str) or not q['query'].strip():
            raise ValueError('Query text is required')
    if len({q['query'] for q in queries}) != len(queries):
        raise ValueError('Online routing requires unique query texts')
    ClipScorer(mapping, queries)
    return queries, mapping


def deployment_identity(backend, mapping):
    """Read only through the configured CLI; retain no endpoint credentials."""
    def command(*argv):
        return subprocess.check_output([*backend.vss_cmd, *argv], text=True, cwd=backend.cwd)
    config = json.loads(command('configure', 'show'))
    inventory = json.loads(command('vios', 'list', '--type', 'video', '--raw'))
    sensors = sorted((s['sensor_id'], Path(s['name']).stem) for s in inventory['sensors'])
    expected = {(v['sensor_id'], Path(v['filename']).stem) for v in mapping.values()}
    if not expected <= set(sensors):
        raise ValueError('Benchmark gallery does not match the configured source inventory')
    return {'inventory': sensors, 'cli_version': command('--version').strip(),
            'configuration_sha256': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
            'models': {k:v.get('models', []) for k,v in config.get('services', {}).items() if isinstance(v, dict)}}


def run(args, backend, queries, mapping):
    from replay_campaign import replay
    from run_capture_campaign import decompose
    if not backend.pass_original_query:
        raise ValueError('Clip evaluation requires CLI support for --original-query')
    backend.evaluation_top_k = args.top_k
    backend.evaluation_gallery_sources = [Path(c['filename']).stem for c in mapping.values()]
    backend.critic_eval_count = args.top_k
    backend.evaluation_no_persist = True
    backend.merge_adjacent = False
    backend.timeout = args.search_timeout
    live = not args.no_decompose and not args.decompositions
    if live and not (args.llm_url and args.llm_model):
        raise ValueError('Live clip evaluation requires explicit --llm-url and --llm-model')
    mode = 'live-routed' if live else ('stored-routed' if backend.decompositions else args.search_path)
    if args.search_path == 'attribute' and not backend.attributes and not live and not backend.decompositions:
        # Entire original text is one attribute in the fixed attribute baseline.
        backend.decompositions = {q['query']: {'attributes': [q['query']], 'has_action': False} for q in queries}
    if args.dry_run:
        print(json.dumps({'scorer': VERSION, 'gallery_size': len(mapping), 'queries': len(queries),
            'retrieval_depth': args.retrieval_depth, 'critic_eval_count': args.top_k,
            'live_decomposition': live, 'example_argv': backend.build_argv(queries[0]['query'])}, indent=2))
        return
    output = Path(args.output_file or f'results/clips-{uuid.uuid4().hex}.json').resolve()
    evidence = output.with_name(output.stem + '-capture')
    reports = output.with_name(output.stem + '-report')
    if output.exists() or evidence.exists() or reports.exists():
        raise ValueError('Output/capture/report already exists; use a fresh output path')
    baseline = deployment_identity(backend, mapping)
    evidence.mkdir(parents=True)
    snapshot = evidence / 'snapshot'
    atomic_json(snapshot / 'deployment.json', baseline)
    atomic_json(snapshot / 'versions.json', {'python': sys.version, 'packages': sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions())})
    atomic_json(snapshot / 'queries.json', queries)
    atomic_json(snapshot / 'gallery-mapping.json', mapping)
    atomic_json(snapshot / 'stored-decompositions.json', backend.decompositions)
    atomic_bytes(snapshot / 'ingestion.json', Path(args.ingestion_manifest).read_bytes())
    for source in Path(__file__).parent.parent.rglob('*.py'):
        atomic_bytes(snapshot / 'code' / source.relative_to(Path(__file__).parent.parent), source.read_bytes())
    seal(snapshot, [str(p.relative_to(snapshot)) for p in snapshot.rglob('*') if p.is_file()])
    settings = {'modes': [mode], 'scorer': 'clips', 'retrieval_depth': args.retrieval_depth,
                'top_k': args.top_k, 'critic_eval_count': args.top_k,
                'min_cosine_similarity': args.min_cosine_similarity, 'top_percent_filter': None,
                'merge_adjacent': False, 'concurrency': 1, 'search_timeout_s': args.search_timeout}
    atomic_json(evidence / 'campaign.json', {'campaign_id': str(uuid.uuid4()), 'created_at': now(),
        'inputs': {'settings': settings}, 'capture_guarantee': 'All exposed output at requested depth; no hidden candidates'})
    atomic_json(evidence / 'identity.json', {'sha256': digest(evidence / 'campaign.json')})
    scorer = ClipScorer(mapping, queries)
    for q in queries:
        reference = None
        if live:
            dec, reference = decompose(SimpleNamespace(output_dir=evidence, llm_url=args.llm_url,
                llm_model=args.llm_model), q, backend.evaluation_gallery_sources)
            backend.decompositions[q['query']] = dec
        directory = evidence / 'attempts' / mode / q['query_id'] / uuid.uuid4().hex
        backend.capture_directory = str(directory)
        backend.capture_metadata = {'query_id': q['query_id'], 'domain': q.get('query_domain', 'unknown'),
                                    'requested_mode': mode, 'decomposition_ref': reference}
        hits, latency = backend.search(q['query'])
        # Derived failures are separate from evidence and never repeat retrieval.
        try:
            pair = scorer.pair(q, hits, latency)
            print(f"{q['query_id']}: AP={pair['raw']['average_precision']:.6f}; "
                  f"{pair['raw']['unique_clips']}/{len(mapping)} clips")
        except (ValueError, KeyError, TypeError) as exc:
            atomic_json(evidence / 'scoring-failures' / f'{q["query_id"]}.json', {'error': str(exc)})
    current = deployment_identity(backend, mapping)
    atomic_json(evidence / 'checks/final.json', current)
    if current != baseline:
        raise ValueError('Deployment configuration or gallery changed during collection; evidence retained')
    report = replay(evidence, reports)
    atomic_json(output, report)
    return report
