# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Artifact validation and deployment transport shared by standalone stages."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.parse import quote

import numpy as np
import requests

SCHEMA_VERSION = 1


def read_json(path):
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


def write_json(path, data):
    """Atomically replace a checkpoint; private by default, including on failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sha256_file(path):
    hasher = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def script_hashes(directory):
    """Fingerprint external scripts and support resources, excluding tool caches."""
    directory = Path(directory).resolve()
    excluded = {'__pycache__', '.pytest_cache', '.git', '.mypy_cache', '.ruff_cache'}
    hashes = {}
    for path in sorted(directory.rglob('*')):
        relative = path.relative_to(directory)
        if any(part in excluded for part in relative.parts) or path.suffix in {'.pyc', '.pyo'}:
            continue
        if path.is_file():
            hashes[relative.as_posix()] = sha256_file(path)
    return hashes


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def subset_fingerprint(subset):
    value = {**subset, 'meta': dict(subset.get('meta', {}))}
    for field in ('subset_fingerprint', 'run_id'):
        value['meta'].pop(field, None)
    return digest(value)


def metadata(selection, **extra):
    return {'schema_version': SCHEMA_VERSION, 'run_id': selection['run_id'],
            'subset_fingerprint': selection['subset_fingerprint'], **extra}


def validate_ids(ids, name):
    if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x for x in ids):
        raise ValueError(f'{name}: expected nonempty string identities')
    if len(ids) != len(set(ids)):
        raise ValueError(f'{name}: duplicate identities')
    return ids


def validate_selection(selection):
    if selection.get('schema_version') != SCHEMA_VERSION or not selection.get('run_id') or not selection.get('subset_fingerprint'):
        raise ValueError('selection schema, run ID, or subset fingerprint is missing')
    video_ids = validate_ids(selection.get('video_ids'), 'video_ids')
    text_ids = validate_ids(selection.get('text_ids'), 'text_ids')
    clips, queries = selection.get('clips'), selection.get('queries')
    if not isinstance(clips, list) or [r.get('dataset_id') for r in clips] != video_ids:
        raise ValueError('selection clip identities differ')
    if not isinstance(queries, list) or [r.get('query_id') for r in queries] != text_ids:
        raise ValueError('selection query identities differ')
    for clip in clips:
        if not all(isinstance(clip.get(k), str) and clip[k] for k in ('filename', 'video_path', 'media_sha256')):
            raise ValueError('selection missing media information')
        if not Path(clip['video_path']).is_absolute() or Path(clip['video_path']).name != clip['filename']:
            raise ValueError('selection requires absolute matching media paths')
    return selection


def load_inputs(subset_path, selection_path):
    subset, selection = read_json(subset_path), read_json(selection_path)
    validate_selection(selection)
    fingerprint = subset_fingerprint(subset)
    if subset.get('meta', {}).get('subset_fingerprint') != fingerprint or selection.get('subset_fingerprint') != fingerprint:
        raise ValueError('subset fingerprint mismatch; prepare subset again')
    if selection.get('schema_version') != SCHEMA_VERSION or not selection.get('run_id'):
        raise ValueError('selection schema/run_id is missing or unsupported')
    if subset['meta'].get('run_id') != selection['run_id']:
        raise ValueError('subset and selection run IDs differ')
    video_ids = validate_ids(selection['video_ids'], 'video_ids')
    text_ids = validate_ids(selection['text_ids'], 'text_ids')
    if video_ids != [r['chunk_id'] for r in subset['gallery']] or text_ids != [r['query_id'] for r in subset['queries']]:
        raise ValueError('subset and selection identity order differs')
    if video_ids != [r['dataset_id'] for r in selection['clips']]:
        raise ValueError('selection clip order differs')
    if text_ids != [r['query_id'] for r in selection['queries']]:
        raise ValueError('selection query order differs')
    expected_selection = subset['meta'].get('selection_fingerprint')
    if expected_selection:
        content = dict(selection)
        content.pop('subset_fingerprint', None)
        content.pop('run_id', None)
        if digest(content) != expected_selection:
            raise ValueError('selection fingerprint mismatch')
    return subset, selection


def validate_vectors(array, rows=None):
    with np.errstate(over='ignore', invalid='ignore'):
        values = np.asarray(array, dtype=np.float32)
    if values.ndim != 2 or not values.shape[0] or not values.shape[1]:
        raise ValueError('embeddings must have shape (rows, dimensions) with positive dimensions')
    if rows is not None and values.shape[0] != rows:
        raise ValueError(f'embedding rows {values.shape[0]} != expected {rows}')
    if not np.isfinite(values).all():
        raise ValueError('nonfinite embedding values')
    if np.any(np.linalg.norm(values.astype(np.float64), axis=1) == 0):
        raise ValueError('zero-norm embedding vector')
    return values


def write_bundle(out, subset, selection, text, video, provenance):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    text = validate_vectors(text, len(selection['text_ids']))
    video = validate_vectors(video, len(selection['video_ids']))
    if text.shape[1] != video.shape[1]:
        raise ValueError('text/video embedding dimensions differ')
    for name, array in [('text', text), ('video', video)]:
        with tempfile.NamedTemporaryFile(dir=out, delete=False) as stream:
            temporary = stream.name
            np.save(stream, array, allow_pickle=False)
        os.replace(temporary, out / (name + '.npy'))
    write_json(out / 'text_ids.json', selection['text_ids'])
    write_json(out / 'video_ids.json', selection['video_ids'])
    artifacts = {name: sha256_file(out / name) for name in ('text.npy', 'video.npy', 'text_ids.json', 'video_ids.json')}
    write_json(out / 'bundle.json', metadata(selection, dimension=text.shape[1],
               text_count=text.shape[0], video_count=video.shape[0], text_ids=selection['text_ids'],
               video_ids=selection['video_ids'], artifacts=artifacts, provenance=provenance))


def load_bundle(directory, subset, selection=None):
    directory = Path(directory)
    manifest = read_json(directory / 'bundle.json')
    if manifest.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('unsupported bundle schema')
    fingerprint = subset_fingerprint(subset)
    if manifest.get('subset_fingerprint') != fingerprint:
        raise ValueError('bundle subset fingerprint mismatch')
    if manifest.get('run_id') != subset.get('meta', {}).get('run_id'):
        raise ValueError('bundle run_id mismatch')
    names = ('text.npy', 'video.npy', 'text_ids.json', 'video_ids.json')
    for name in names:
        if manifest.get('artifacts', {}).get(name) != sha256_file(directory / name):
            raise ValueError(f'bundle artifact hash mismatch: {name}')
    result = {'manifest': manifest}
    for kind, records, key in [('text', subset['queries'], 'query_id'), ('video', subset['gallery'], 'chunk_id')]:
        ids = validate_ids(read_json(directory / (kind + '_ids.json')), kind + '_ids')
        expected = [row[key] for row in records]
        if set(ids) != set(expected) or ids != manifest.get(kind + '_ids'):
            raise ValueError(f'{kind} bundle identity mismatch')
        array = np.load(directory / (kind + '.npy'), allow_pickle=False)
        if array.dtype != np.float32:
            raise ValueError('bundle embeddings must be float32')
        result[kind] = validate_vectors(array, len(expected))
        result[kind + '_ids'] = ids
    if result['text'].shape[1] != result['video'].shape[1] or result['text'].shape[1] != manifest.get('dimension'):
        raise ValueError('bundle dimension mismatch')
    return result


def network_args(parser):
    parser.add_argument('--deployment-config', default=None)
    parser.add_argument('--auth-config', default=None)
    parser.add_argument('--ca-bundle', default=None)
    parser.add_argument('--log-level', default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])


def deployment_document(path=None):
    # Use the repository's standard parser, including its version validation.
    root = next((p for p in Path(__file__).resolve().parents if (p / 'libs/vss/cli/src').is_dir()), None)
    if root:
        source = str(root / 'libs/vss/cli/src')
        if source not in sys.path:
            sys.path.insert(0, source)
    from vss_cli.config import Deployment, load
    deployment = Deployment.from_json(read_json(path)) if path else load()
    return deployment.to_json()


def load_deployment(path=None):
    return {name: service['url'] for name, service in deployment_document(path)['services'].items()}


class Network:
    """Service-scoped credentials and TLS; never forward agent auth to uploads."""
    def __init__(self, config, auth_path=None, ca_bundle=None):
        self.config = config
        self.auth = read_json(auth_path) if auth_path else {}
        self.verify = ca_bundle or True
        if ca_bundle and not Path(ca_bundle).is_file():
            raise ValueError('CA bundle does not exist')
        for service, value in self.auth.items():
            if service not in ('agent', 'rt_embed', 'elasticsearch', 'upload') or not isinstance(value, dict):
                raise ValueError('invalid service authentication configuration')
            if set(value) - {'bearer_env', 'headers_env', 'username_env', 'password_env'}:
                raise ValueError('authentication accepts environment references only')
            self._credentials(service)  # Fail before any request if credentials are absent.

    def _credentials(self, service):
        spec = self.auth.get(service, {})
        def env(name):
            if not isinstance(name, str) or not name or name not in os.environ:
                raise ValueError('missing credential environment variable reference')
            return os.environ[name]
        headers = {header: env(name) for header, name in spec.get('headers_env', {}).items()}
        if 'bearer_env' in spec:
            headers['Authorization'] = 'Bearer ' + env(spec['bearer_env'])
        auth = None
        if 'username_env' in spec or 'password_env' in spec:
            auth = (env(spec.get('username_env')), env(spec.get('password_env')))
        return headers, auth

    def _send(self, service, method, url, **kwargs):
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username or parts.password:
            raise ValueError('service URL must use HTTP(S) without embedded credentials')
        headers, auth = self._credentials(service)
        headers.update(kwargs.pop('headers', {}))
        kwargs.setdefault('timeout', 30)
        try:
            # Redirects could forward custom credential headers to another origin.
            response = requests.request(method, url, headers=headers, auth=auth,
                                        verify=self.verify, allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f'{service} request failed ({type(exc).__name__}); verify route reachability and certificate trust') from None
        if 300 <= response.status_code < 400:
            raise RuntimeError(f'{service} returned redirect; configure a directly reachable route')
        return response

    def request(self, service, method, path='', **kwargs):
        base = self.config.get(service)
        if not base:
            raise ValueError(f'deployment has no {service} route; run vss configure --base-url <origin>')
        return self._send(service, method, base.rstrip('/') + ('/' + path.lstrip('/') if path else ''), **kwargs)

    def absolute_upload(self, url, method='POST', **kwargs):
        return self._send('upload', method, url, **kwargs)

    def require_model(self, model):
        response = self.request('rt_embed', 'GET', 'v1/models')
        if response.status_code != 200:
            raise RuntimeError(f'RT-embed model discovery failed (HTTP {response.status_code})')
        models = response.json().get('data', [])
        if model not in [row.get('id') for row in models if isinstance(row, dict)]:
            raise ValueError(f'RT-embed does not advertise requested model {model!r}; choose the loaded model explicitly')

    def require_index(self, index):
        if not isinstance(index, str) or not index or index in ('.', '..') or any(c in index for c in '/?#'):
            raise ValueError('Elasticsearch index must be an index name or pattern')
        response = self.request('elasticsearch', 'GET', quote(index, safe='*,-_') + '/_mapping')
        if response.status_code != 200 or not response.json():
            raise ValueError(f'Elasticsearch index/pattern {index!r} is unavailable (HTTP {response.status_code})')

    def preflight(self, services):
        for service in services:
            if service == 'elasticsearch':
                response = self.request(service, 'GET', '')
                if response.status_code != 200:
                    raise RuntimeError(f'elasticsearch preflight failed (HTTP {response.status_code}); expose the configured service route')
            elif service == 'agent':
                base = self.config.get(service)
                if not base:
                    raise ValueError('deployment has no agent route; run vss configure --base-url <origin>')
                # Docker and Helm expose /openapi.json beside /api, preserving
                # any enclosing origin prefix (deploy/helm/services/common/README.md).
                schema_base = base.rstrip('/').removesuffix('/api')
                response = self._send(service, 'GET', schema_base + '/openapi.json')
                if response.status_code != 200:
                    raise RuntimeError('agent OpenAPI unavailable; cannot verify required ingestion routes before uploading')
                paths = response.json().get('paths', {})
                if not any(path.rstrip('/').endswith('/v1/videos') and 'post' in ops for path, ops in paths.items()) or not any(path.endswith('/complete') and 'post' in ops for path, ops in paths.items()):
                    raise RuntimeError('agent lacks required upload/completion routes; use a compatible search profile')
            elif service == 'rt_embed':
                response = self.request(service, 'GET', 'v1/models')
                if response.status_code != 200:
                    raise RuntimeError(f'RT-embed model route preflight failed (HTTP {response.status_code})')
                # GET on a POST-only route should return 405, not 404; no model computation.
                response = self.request(service, 'GET', 'v1/generate_text_embeddings')
                if response.status_code != 405:
                    raise RuntimeError(f'RT-embed text route unavailable (HTTP {response.status_code}); configure a Cosmos compatible route')
            else:
                raise ValueError(f'unknown preflight service {service}')


def preflight(deployment, auth_config=None, ca_bundle=None, es_index=None, model=None):
    net = Network(deployment, auth_config, ca_bundle)
    net.preflight(['agent', 'rt_embed', 'elasticsearch'])
    if es_index is not None:
        net.require_index(es_index)
    if model is not None:
        net.require_model(model)


def cli_main(function, parser):
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format='%(levelname)s: %(message)s')
    try:
        result = function(args)
        return 0 if result is None else result
    except (Exception, KeyboardInterrupt) as exc:
        logging.error('%s', exc)
        return 1
