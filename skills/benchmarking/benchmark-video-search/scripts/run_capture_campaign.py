# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Sequential four-path, 600-query campaign against an existing 80-clip gallery."""
from __future__ import annotations
import argparse
from collections import Counter
import fcntl
import hashlib
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from flows.capture import (atomic_bytes, atomic_json, digest, now, read_json, recover,
                           seal, verify, VERSION)
from flows.query import CliQueryBackend
from flows.routing import plan_for
from replay_campaign import MODES, replay, successful

REPO = Path(__file__).resolve().parents[4]
FUSION = ['--fusion-method','rrf','--w-attribute','0.55','--w-embed','0.35',
          '--w-tag','0','--rrf-k','60','--rrf-w','0.5','--embed-confidence-threshold','0.1']
SETTINGS = {'retrieval_depth': 'gallery', 'scorer': 'clips', 'top_k': 1000, 'concurrency': 1, 'merge_adjacent': False, 'min_cosine_similarity': -1,
            'critic_eval_count': 1000, 'search_timeout_s': 3600, 'fusion_args': FUSION, 'top_percent_filter': None,
            'temperature': 0, 'reasoning': False, 'max_tokens': 16384, 'modes': list(MODES)}


def command(argv):
    return subprocess.check_output(argv, text=True)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def dataset(root, ingestion):
    queries = read_json(root / 'gt/queries_gt.json')['queries']
    gallery = read_json(root / 'additional_gt/clips_gt.json')['gallery']
    manifest = read_json(ingestion)
    clips = {c['dataset_id']: c for c in manifest['clips']}
    if len(queries) != 600 or Counter(q['query_domain'] for q in queries) != {'event':350,'pas':250}:
        raise ValueError('Expected 350 event and 250 PAS queries')
    if len({q['query_id'] for q in queries}) != 600 or len({q['query'] for q in queries}) != 600:
        raise ValueError('Query IDs and texts must be unique')
    if len(clips) != 80 or len(gallery) != 80 or set(clips) != {g['chunk_id'] for g in gallery}:
        raise ValueError('Ingestion/gallery mismatch')
    if len({c['sensor_id'] for c in clips.values()}) != 80 or any(c['phase'] != 'complete' for c in clips.values()):
        raise ValueError('Invalid ingestion mapping')
    for g in gallery:
        c = clips[g['chunk_id']]
        if Path(g['video']).name != c['filename'] or digest(root / g['video']) != c['media_sha256']:
            raise ValueError(f'Media identity mismatch: {g["chunk_id"]}')
    for q in queries:
        ids = q['relevant_clip_ids']
        if not ids or len(ids) != len(set(ids)) or not set(ids) <= set(clips):
            raise ValueError(f'Invalid relevance set: {q["query_id"]}')
        if not q['query_id'].replace('_','').isalnum():
            raise ValueError('Unsafe query ID')
        q['segments'] = [{'video_name': Path(clips[i]['filename']).stem,
            'start_time':'2025-01-01T00:00:00Z','end_time':'2025-01-01T00:00:05Z'} for i in ids]
    return queries, clips


class Backend(CliQueryBackend):
    def __init__(self, mode, q, sources, decomposition=None, settings=None, **kwargs):
        settings = settings or SETTINGS
        super().__init__(['vss'], top_k=settings['top_k'], critic_eval_count=settings['critic_eval_count'],
                         min_cosine_similarity=settings['min_cosine_similarity'], merge_adjacent=False,
                         timeout=settings.get('search_timeout_s', 300), **kwargs)
        if mode in ('embed','attribute'):
            self.plan = plan_for(q, None, default_path=mode,
                default_attributes=[q] if mode == 'attribute' else [])
            self.plan['video_sources'] = sources
        else:
            if not decomposition:
                raise ValueError('A saved live decomposition is required')
            self.plan = plan_for(q, decomposition)
            if mode == 'forced-fusion':
                self.plan['path'] = 'fusion'
        self.plan['top_k'] = settings['top_k']

    def plan_for_query(self, query):
        return self.plan.copy()

    def build_argv(self, query, plan=None):
        plan = plan or self.plan
        argv = super().build_argv(query, plan) + ['--no-persist']
        return argv + (FUSION if plan['path'] == 'fusion' else [])


def probe(args, clips):
    """Read-only inventory, model, image/config identities and per-source index coverage."""
    import requests
    config = json.loads(command(['vss','configure','show']))
    inventory = json.loads(command(['vss','vios','list','--type','video','--raw']))
    sensors = inventory['sensors']
    actual = {(s['sensor_id'], s['name']) for s in sensors}
    expected = {(c['sensor_id'], Path(c['filename']).stem) for c in clips.values()}
    # VIOS listings may preserve the extension.
    actual = {(i, Path(n).stem) for i,n in actual}
    if actual != expected:
        raise ValueError(f'Gallery changed: missing={expected-actual}, extra={actual-expected}')
    def get(url, **kwargs):
        r = requests.get(url, timeout=60, **kwargs)
        r.raise_for_status()
        return r.json()
    from urllib.parse import urlsplit
    origin = urlsplit(config['base_url'])
    llm_origin = urlsplit(args.llm_url)
    if (origin.scheme,origin.netloc) != (llm_origin.scheme,llm_origin.netloc):
        raise ValueError('LLM must belong to the configured deployment origin')
    ingresses = json.loads(command(['kubectl','-n',args.namespace,'get','ingress','-o','json']))
    routes = [p for item in ingresses['items'] for rule in item['spec'].get('rules',[])
              if rule.get('host') == origin.hostname for p in rule.get('http',{}).get('paths',[])]
    if llm_origin.path.rstrip('/') not in {p['path'].rstrip('/') for p in routes}:
        raise ValueError('LLM URL is not a recorded deployment ingress route')
    models = get(args.llm_url.rstrip('/') + '/v1/models')
    ids = [m['id'] for m in models['data']]
    if ids != [args.llm_model]:
        raise ValueError(f'Deployed model identity mismatch: {ids}')
    pods = json.loads(command(['kubectl','-n',args.namespace,'get','pods','-o','json']))
    identities = []
    for pod in pods['items']:
        if pod['status'].get('phase') == 'Succeeded':
            continue
        identities.append({'name': pod['metadata']['name'], 'uid': pod['metadata']['uid'],
            'containers': [{'name': c['name'], 'image': c['image'], 'imageID': c.get('imageID'),
                            'restartCount':c.get('restartCount')} for c in pod['status'].get('containerStatuses',[])]})
        if not all(c.get('ready') for c in pod['status'].get('containerStatuses', [])):
            raise ValueError(f'Unready deployment pod: {pod["metadata"]["name"]}')
    cms = json.loads(command(['kubectl','-n',args.namespace,'get','configmaps','-o','json']))
    # Hash complete config values, but never copy credentials contained in configs.
    config_hashes = {c['metadata']['name']: fingerprint(c.get('data',{})) for c in cms['items']}
    es = config['services']['elasticsearch']['url']
    indices = get(es + '/_cat/indices/mdx-*?format=json&h=index,uuid,docs.count')
    coverage = {}
    for prefix, field, expected_keys in (
        ('mdx-embed-filtered-', 'sensor.id.keyword', {c['sensor_id'] for c in clips.values()}),
        ('mdx-behavior-', 'sensor.id.keyword', {Path(c['filename']).stem for c in clips.values()}),
        ('mdx-raw-', 'sensorId.keyword', {Path(c['filename']).stem for c in clips.values()})):
        names = sorted(x['index'] for x in indices if x['index'].startswith(prefix))
        if not names:
            raise ValueError(f'Missing index family: {prefix}')
        payload = get(es + '/' + ','.join(names) + '/_search', json={
            'size':0, 'track_total_hits':True, 'aggs':{'sensors':{'terms':{'field':field,'size':10000}}}})
        buckets = payload['aggregations']['sensors']['buckets']
        coverage[prefix] = {'indices':names, 'documents':payload['hits']['total'],
            'sensors':buckets, 'missing':sorted(expected_keys - {b['key'] for b in buckets})}
    return {'inventory':inventory, 'identities':sorted(identities,key=lambda x:x['name']),
        'config_hashes':config_hashes, 'configured_services':config['services'], 'base_url':config['base_url'],
        'models':ids, 'ingress_routes':routes, 'indices':sorted(indices,key=lambda x:x['index']), 'coverage':coverage,
        'cli_version':command(['vss','--version']).strip(),
        'cli_install':cli_identity()}


def cli_identity():
    import shutil
    executable = Path(shutil.which('vss')).resolve()
    python = executable.read_text().splitlines()[0].removeprefix('#!')
    script = """import importlib.metadata as m, hashlib, json
from pathlib import Path
out={}
for name in ('nvidia-vss-cli','nvidia-vss-core'):
 d=m.distribution(name)
 files={str(f):hashlib.sha256(Path(d.locate_file(f)).read_bytes()).hexdigest()
        for f in d.files or [] if str(f).endswith('.py')}
 out[name]={'version':d.version,'python_sha256':files}
print(json.dumps(out,sort_keys=True))
"""
    return json.loads(command([python,'-c',script]))


def snapshot(args, queries, clips):
    directory = args.output_dir / 'snapshot'
    directory.mkdir()
    files = [p for p in args.dataset_root.rglob('*') if p.is_file() and p.suffix in ('.json','.jsonl','.parquet','.md')]
    for p in files:
        atomic_bytes(directory / 'dataset' / p.relative_to(args.dataset_root), p.read_bytes())
    atomic_bytes(directory / 'ingestion.json', args.ingestion_manifest.read_bytes())
    atomic_json(directory / 'queries.json', queries)
    atomic_json(directory / 'gallery-mapping.json', clips)
    # Evidence includes source files and local modifications, not environment variables/config secrets.
    scripts = Path(__file__).parent
    for p in scripts.rglob('*.py'):
        atomic_bytes(directory / 'code/scripts' / p.relative_to(scripts), p.read_bytes())
    for rel in ('services/agent/packages/vss_agents/src/vss_agents/tools/search.py',
                'services/agent/packages/vss_agents/src/vss_agents/utils/reasoning_utils.py'):
        atomic_bytes(directory / 'code' / rel, (REPO / rel).read_bytes())
    atomic_bytes(directory / 'checkout.patch', command(['git','-C',str(REPO),'diff','HEAD']).encode())
    atomic_json(directory / 'versions.json', {'python':sys.version,
        'git_head':command(['git','-C',str(REPO),'rev-parse','HEAD']).strip(),
        'git_status':command(['git','-C',str(REPO),'status','--short']),
        'packages':sorted((d.metadata['Name'],d.version) for d in importlib.metadata.distributions())})
    seal(directory, [str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file()])


def decompose(args, q, sources):
    from flows.decompose import load_prompt, _SYSTEM, _strip_fences
    import requests
    root = args.output_dir / 'decompositions' / q['query_id']
    for previous in sorted(root.glob('*')):
        if (previous / 'complete.json').exists():
            verify(previous)
            return read_json(previous / 'parsed.json'), str(previous.relative_to(args.output_dir))
        # Recover a complete HTTP response even if parsing/sealing was interrupted.
        if (previous / 'process.json').exists() and (previous / 'response.bin').exists():
            proc = read_json(previous / 'process.json')
            if proc.get('status') == 200 and not proc.get('error'):
                if digest(previous / 'response.bin') != proc['response_sha256']:
                    raise ValueError('Decomposition response checksum mismatch')
                value = parse_decomposition(read_json(previous / 'response.bin'))
                atomic_json(previous / 'parsed.json', value)
                seal(previous, ['request.json','response.bin','process.json','parsed.json'])
                return value, str(previous.relative_to(args.output_dir))
    directory = root / uuid.uuid4().hex
    directory.mkdir(parents=True)
    prompt, examples = load_prompt(REPO)
    spec = importlib.util.spec_from_file_location('product_reasoning', REPO / 'services/agent/packages/vss_agents/src/vss_agents/utils/reasoning_utils.py')
    reasoning = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reasoning)
    llm = type('ChatOpenAI', (), {'model_name':args.llm_model})()
    tag = reasoning.get_thinking_tag(llm, False)
    extra = reasoning.get_llm_reasoning_bind_kwargs(llm, False).get('extra_body',{})
    body = {'model':args.llm_model,'temperature':0,'max_tokens':16384,
        'messages':[{'role':'system','content':_SYSTEM + ('\n'+tag if tag else '')},
            {'role':'user','content':prompt.format(video_sources='Video files: '+', '.join(sources),
                    few_shot_examples=examples,user_query=q['query'])}], **extra}
    url = args.llm_url.rstrip('/') + '/v1/chat/completions'
    encoded = json.dumps(body).encode()
    atomic_json(directory / 'request.json', {'query_id':q['query_id'], 'query':q['query'],
        'started_at':now(), 'url':url,'body':body,'body_utf8':encoded.decode(),
        'headers':{'Content-Type':'application/json'},'reasoning':False})
    started = time.monotonic()
    status, error = None, None
    try:
        with requests.post(url, data=encoded, headers={'Content-Type':'application/json'}, stream=True, timeout=120) as r:
            status = r.status_code
            with (directory / 'response.bin').open('xb') as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
                    f.flush()
                    os.fsync(f.fileno())
            r.raise_for_status()
    except BaseException as exc:
        error = {'type':type(exc).__name__, 'message':str(exc), 'uncertain_execution':True}
        raise
    finally:
        atomic_json(directory / 'process.json', {'status':status, 'error':error, 'ended_at':now(),
            'elapsed_s':time.monotonic()-started,
            'response_sha256':digest(directory/'response.bin') if (directory/'response.bin').exists() else None})
    value = parse_decomposition(read_json(directory / 'response.bin'))
    atomic_json(directory / 'parsed.json', value)
    seal(directory, ['request.json','response.bin','process.json','parsed.json'])
    return value, str(directory.relative_to(args.output_dir))


def parse_decomposition(payload):
    from flows.decompose import _strip_fences
    choice = payload['choices'][0]
    if choice['finish_reason'] != 'stop':
        raise ValueError('Incomplete decomposition response')
    value = json.loads(_strip_fences(choice['message']['content']))
    if not isinstance(value, dict) or not isinstance(value.get('query'), str) or not value['query']:
        raise ValueError('Invalid decomposed query; no fallback permitted')
    # The product prompt permits omitted/null parameters; its parser uses
    # extracted.get('attributes', []) or []. Keep the captured object unchanged.
    attributes = value.get('attributes')
    if attributes is not None and (
        not isinstance(attributes, list) or not all(isinstance(a, str) for a in attributes)
    ):
        raise ValueError('Invalid attributes')
    if not isinstance(value.get('has_action'), bool):
        raise ValueError('Missing required has_action')
    for key in ('video_sources','object_ids'):
        if key in value and value[key] is not None and not isinstance(value[key],list):
            raise ValueError(f'Invalid {key}')
    return value


def collect(args, queries, clips):
    sources = [Path(c['filename']).stem for c in clips.values()]
    current = probe(args, clips)
    checks = args.output_dir / 'checks'
    atomic_json(checks / (now().replace(':','-')+'.json'), current)
    baseline_path = args.output_dir / 'baseline.json'
    if baseline_path.exists():
        if current != read_json(baseline_path):
            raise ValueError('Deployment, coverage, or gallery changed materially; collection paused')
        if digest(baseline_path) != read_json(args.output_dir/'baseline-identity.json')['sha256']:
            raise ValueError('Baseline integrity failure')
    else:
        atomic_json(baseline_path, current)
        atomic_json(args.output_dir/'baseline-identity.json', {'sha256':digest(baseline_path)})
    for mode in MODES:
        current = probe(args, clips)
        atomic_json(checks / (mode+'-'+uuid.uuid4().hex+'.json'), current)
        if current != read_json(baseline_path):
            raise ValueError('Deployment, coverage, or gallery changed between modes')
        for index,q in enumerate(queries):
            if successful(args.output_dir, mode, q['query_id']):
                continue
            # Reparse known exit-zero attempts before repeating inference.
            recovered = False
            for old in sorted((args.output_dir/'attempts'/mode/q['query_id']).glob('*')):
                if (old/'process.json').exists():
                    proc = read_json(old/'process.json')
                    if proc['returncode'] == 0 and not proc.get('error'):
                        try:
                            recover(old)
                            recovered = True
                            break
                        except (ValueError, UnicodeError):
                            pass
                if not (old/'uncertain.json').exists():
                    atomic_json(old/'uncertain.json', {'uncertain_execution':True,
                        'reason':'No complete successful response; resume may repeat inference'})
            if recovered:
                continue
            decomposition, reference = None, None
            if mode in ('live-routed','forced-fusion'):
                if mode == 'forced-fusion' and not any((args.output_dir/'decompositions'/q['query_id']).glob('*/complete.json')):
                    raise ValueError('Forced fusion requires an existing live decomposition')
                decomposition, reference = decompose(args, q, sources)
            path = args.output_dir / 'attempts' / mode / q['query_id'] / uuid.uuid4().hex
            backend = Backend(mode, q['query'], sources, decomposition, settings=args.settings, capture_directory=str(path),
                capture_metadata={'query_id':q['query_id'],'domain':q['query_domain'],
                    'requested_mode':mode, 'decomposition_ref':reference, 'smoke_check':index==0})
            hits, seconds = backend.search(q['query'])
            print(f'{mode} {index+1}/600 {q["query_id"]}: {len(hits)} hits, {seconds:.2f}s', flush=True)
        try:
            replay(args.output_dir, args.output_dir.with_name(args.output_dir.name+'-report-'+mode+'-'+uuid.uuid4().hex[:8]), scorer=args.settings.get('scorer', 'native'))
        except OSError:
            raise
        except Exception as exc:
            atomic_json(args.output_dir/'scoring-failures'/(mode+'-'+uuid.uuid4().hex+'.json'),
                {'error':str(exc),'type':type(exc).__name__})
    final_probe = probe(args, clips)
    atomic_json(checks / 'final.json', final_probe)
    if final_probe != read_json(baseline_path):
        raise ValueError('Deployment changed during final mode')
    report = replay(args.output_dir, args.output_dir.with_name(args.output_dir.name+'-report-final-'+uuid.uuid4().hex[:8]), scorer=args.settings.get('scorer', 'native'))
    if any(report['modes'][m]['groups']['all']['answered'] != 600 for m in MODES):
        raise ValueError('Campaign incomplete')
    atomic_json(args.output_dir/'COMPLETE.json', {'successful_records':2400, 'completed_at':now()})



def validate_resume_identity(directory, identity):
    """Accept only exact recorded code amendments; all campaign settings stay pinned.

    Amendments are explicit local provenance records, not an automatic bypass.
    Each sealed amendment retains both source versions and the reviewed reason.
    The original campaign manifest and snapshot remain immutable.
    """
    import copy
    expected = copy.deepcopy(read_json(directory / 'campaign.json')['inputs'])
    campaign_hash = digest(directory / 'campaign.json')
    for amendment_dir in sorted((directory / 'amendments').glob('*')):
        verify(amendment_dir)
        amendment = read_json(amendment_dir / 'amendment.json')
        if amendment['campaign_sha256'] != campaign_hash:
            raise ValueError('Amendment belongs to another campaign')
        for change in amendment['changes']:
            name = change['path']
            if expected['code_sha256'].get(name) != change['before_sha256']:
                raise ValueError(f'Invalid amendment history: {name}')
            if digest(amendment_dir / change['before_file']) != change['before_sha256']:
                raise ValueError(f'Invalid original amendment source: {name}')
            if digest(amendment_dir / change['after_file']) != change['after_sha256']:
                raise ValueError(f'Invalid replacement amendment source: {name}')
            expected['code_sha256'][name] = change['after_sha256']
    if expected != identity:
        raise ValueError('Incompatible resume inputs/configuration/code')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset-root',type=Path,required=True)
    p.add_argument('--ingestion-manifest',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--llm-url',required=True,help='Recorded deployed LLM origin, including ingress path')
    p.add_argument('--llm-model',required=True)
    p.add_argument('--namespace',default='vss-search')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--retrieval-depth', choices=['gallery', 'top-k'])
    p.add_argument('--top-k', type=int)
    p.add_argument('--search-timeout', type=int, help='Capture process timeout in seconds; new campaigns default to 3600')
    args = p.parse_args()
    if args.top_k is not None and not 1 <= args.top_k <= 1000:
        p.error('--top-k must be between 1 and 1000')
    if args.search_timeout is not None and args.search_timeout < 1:
        p.error('--search-timeout must be positive')
    args.settings = dict(SETTINGS)
    if args.resume and (args.output_dir / 'campaign.json').exists():
        args.settings = read_json(args.output_dir / 'campaign.json')['inputs']['settings']
        if args.retrieval_depth is not None and args.retrieval_depth != args.settings.get('retrieval_depth', 'top-k'):
            p.error('Resume must preserve recorded retrieval depth')
        if args.search_timeout is not None and args.search_timeout != args.settings.get('search_timeout_s', 300):
            p.error('Resume must preserve recorded search timeout')
        if args.top_k is not None and args.top_k != args.settings['top_k']:
            p.error('Resume must preserve recorded top-k')
    else:
        if args.search_timeout is not None:
            args.settings['search_timeout_s'] = args.search_timeout
        if args.top_k is not None and args.retrieval_depth != 'top-k':
            p.error('--top-k requires --retrieval-depth top-k')
        if args.retrieval_depth == 'top-k':
            args.settings.update(retrieval_depth='top-k', top_k=args.top_k or 10, critic_eval_count=args.top_k or 10, min_cosine_similarity=0)
    for key in ('dataset_root','ingestion_manifest','output_dir'):
        setattr(args,key,getattr(args,key).resolve())
    queries, clips = dataset(args.dataset_root, args.ingestion_manifest)
    identity = {'format_version':VERSION,'settings':args.settings,'dataset_sha256':fingerprint(queries),
        'dataset_metadata_sha256':{str(p.relative_to(args.dataset_root)):digest(p)
            for p in sorted(args.dataset_root.rglob('*')) if p.is_file() and p.suffix in ('.json','.jsonl','.parquet','.md')},
        'ingestion_sha256':digest(args.ingestion_manifest),'llm_url':args.llm_url,'llm_model':args.llm_model,
        'namespace':args.namespace,'code_sha256':{str(x.relative_to(REPO)):digest(x) for x in
            sorted([*Path(__file__).parent.rglob('*.py'),
                REPO/'services/agent/packages/vss_agents/src/vss_agents/tools/search.py',
                REPO/'services/agent/packages/vss_agents/src/vss_agents/utils/reasoning_utils.py'])}}
    if args.dry_run:
        print(json.dumps({'queries':600,'domains':dict(Counter(q['query_domain'] for q in queries)),
            'gallery':80,'settings':args.settings,'example_argv':{m:Backend(m,queries[0]['query'],
            [Path(c['filename']).stem for c in clips.values()],
            {'query':queries[0]['query'],'attributes':[],'has_action':True}, settings=args.settings).build_argv(queries[0]['query']) for m in MODES}},indent=2))
        return
    args.output_dir.mkdir(parents=True,exist_ok=True)
    with (args.output_dir/'.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        campaign = args.output_dir/'campaign.json'
        if campaign.exists():
            if not args.resume:
                raise ValueError('Campaign exists: use --resume')
            if digest(campaign) != read_json(args.output_dir/'identity.json')['sha256']:
                raise ValueError('Campaign identity integrity failure')
            validate_resume_identity(args.output_dir, identity)
            verify(args.output_dir/'snapshot')
        else:
            atomic_json(campaign, {'campaign_id':str(uuid.uuid4()),'created_at':now(),'inputs':identity,
                'capture_guarantee':'All interface inputs/outputs at recorded retrieval depth. No hidden candidates, embeddings, component scores, or internal critic prompts.'})
            atomic_json(args.output_dir/'identity.json', {'sha256':digest(campaign)})
            snapshot(args, queries, clips)
        def cancelled(signum, frame):
            raise KeyboardInterrupt(f'Signal {signum}')
        signal.signal(signal.SIGTERM, cancelled)
        try:
            collect(args, queries, clips)
        except BaseException as exc:
            atomic_json(args.output_dir/'failures'/(uuid.uuid4().hex+'.json'),
                {'at':now(),'type':type(exc).__name__,'error':str(exc),'state':'paused'})
            raise


if __name__ == '__main__':
    main()
