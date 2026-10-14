#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Export exhaustive sensor vectors and exact Cosmos query embeddings."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import logging
from pathlib import Path
import sys
import time
from urllib.parse import quote

import numpy as np
from comparison_common import (Network, digest, load_deployment, load_inputs, metadata,
    network_args, read_json, sha256_file, validate_vectors, write_bundle, write_json)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('subset', 'selection', 'ingestion', 'es-index', 'model', 'out'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--wait-timeout', type=float, default=1200)
    p.add_argument('--poll-interval', type=float, default=10)
    p.add_argument('--vector-field', default='llm.visionEmbeddings.vector')
    p.add_argument('--sensor-field', default='sensor.id.keyword')
    p.add_argument('--resume', action='store_true')
    network_args(p)
    return p


def values_at(value, parts):
    """Walk dotted paths through nested arrays without losing entry cardinality."""
    if not parts:
        return [value]
    if isinstance(value, list):
        return [item for row in value for item in values_at(row, parts)]
    if isinstance(value, dict):
        # Elasticsearch fields responses may group nested fields under a dotted key.
        for count in range(len(parts), 0, -1):
            key = '.'.join(parts[:count])
            if key in value:
                return values_at(value[key], parts[count:])
        return []
    return []


def extract_vectors(hit, field, model):
    source = hit.get('_source') or {}
    llm_info = (source.get('llm') or {}).get('info') or {}
    source_model = llm_info.get('modelId') or llm_info.get('model_id')
    if source_model and source_model != model:
        raise ValueError(f'Indexed model {source_model!r} differs from requested model {model!r}')
    entries = values_at(source, field.split('.')[:-1])
    entries = [item for value in entries for item in (value if isinstance(value, list) else [value])]
    found = []
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict) or field.split('.')[-1] not in entry:
            continue
        declared = entry.get('model') or entry.get('model_id') or entry.get('modelName') or source_model
        if declared and declared != model:
            raise ValueError(f'Indexed model {declared!r} differs from requested model {model!r}')
        vector = entry[field.split('.')[-1]]
        found.append((vector, {'index': hit['_index'], 'document_id': hit['_id'], 'entry_position': position,
                              'value_origin': 'original_source', 'model': declared, 'revision': entry.get('revision') or llm_info.get('revision'), 'model_version': source.get('version')}))
    if not found:
        returned_values = values_at(hit.get('fields') or {}, field.split('.'))
        vectors = [vector for returned in returned_values for vector in
                   (returned if returned and isinstance(returned[0], list) else [returned])]
        for position, vector in enumerate(vectors):
            found.append((vector, {'index': hit['_index'], 'document_id': hit['_id'], 'entry_position': position,
                                  'value_origin': 'reconstructed_fields', 'model': source_model, 'revision': llm_info.get('revision'), 'model_version': source.get('version')}))
    return found


def scan_sensor(net, index, sensor_field, sensor_id, vector_field):
    body = {'size': 1000, 'query': {'term': {sensor_field: sensor_id}}, '_source': True,
            'fields': [vector_field], 'sort': ['_doc']}
    response = net.request('elasticsearch', 'POST', '/' + quote(index, safe='*,-_') + '/_search',
                           params={'scroll': '2m'}, json=body, timeout=60)
    response.raise_for_status()
    result = response.json()
    scroll_id = result.get('_scroll_id')
    try:
        while True:
            if result.get('timed_out') or result.get('_shards', {}).get('failed', 0):
                raise RuntimeError('Elasticsearch search incomplete (timeout or failed shards)')
            hits = result.get('hits', {}).get('hits', [])
            for hit in hits:
                yield hit
            if not hits:
                break
            if not scroll_id:
                total = result.get('hits', {}).get('total', len(hits))
                count = total.get('value', 0) if isinstance(total, dict) else total
                if count > len(hits) or (isinstance(total, dict) and total.get('relation') == 'gte'):
                    raise RuntimeError('Elasticsearch did not return a scroll handle for exhaustive export')
                break
            response = net.request('elasticsearch', 'POST', '/_search/scroll',
                                   json={'scroll': '2m', 'scroll_id': scroll_id}, timeout=60)
            response.raise_for_status()
            result = response.json()
            scroll_id = result.get('_scroll_id', scroll_id)
    finally:
        if scroll_id:
            response = net.request('elasticsearch', 'DELETE', '/_search/scroll', json={'scroll_id': [scroll_id]}, timeout=30)
            response.raise_for_status()


def embed_text(net, config, model, text):
    """Use Cosmos client with authenticated transport; retain its exact protocol fallback."""
    try:
        from vss_core.search_core.clients.cosmos_embed import CosmosEmbedClient
    except (ImportError, AttributeError):
        response = net.request('rt_embed', 'POST', '/v1/generate_text_embeddings',
                               json={'text_input': [text], 'model': model}, timeout=120)
        response.raise_for_status()
        return response.json()['data'][0]['embeddings'], 'cosmos-protocol-adapter'

    class Transport:
        async def post(self, url, **kwargs):
            return await asyncio.to_thread(net.request, 'rt_embed', 'POST', '/v1/generate_text_embeddings', timeout=120, **kwargs)
    client = CosmosEmbedClient(config['rt_embed'], model=model)
    client._client = Transport()
    return asyncio.run(client.get_text_embedding(text)), 'vss_core.CosmosEmbedClient'


@contextmanager
def collection_attempt(checkpoint, state):
    """Record failures only after this invocation accepts checkpoint dependencies."""
    try:
        yield
    except Exception as exc:
        state['complete'] = False
        state['error'] = {'type': type(exc).__name__, 'message': str(exc) if isinstance(exc, (ValueError, TimeoutError)) else 'Network or response failure; check configured routes and resume checkpoints'}
        state['duration_seconds'] = time.time() - state.get('started_epoch_seconds', time.time())
        try:
            write_json(checkpoint, state)
        except OSError:
            logging.error('Could not persist collection failure checkpoint')
        raise


def run(args, network=None):
    if args.wait_timeout < 0 or args.poll_interval <= 0:
        raise ValueError('Wait timeout must be nonnegative and poll interval positive')
    subset, selection = load_inputs(args.subset, args.selection)
    ingestion = read_json(args.ingestion)
    if ingestion.get('subset_fingerprint') != selection['subset_fingerprint'] or ingestion.get('run_id') != selection['run_id']:
        raise ValueError('Ingestion and subset identities differ')
    records = ingestion['clips']
    ingested = {r['dataset_id']: r for r in records}
    if len(ingested) != len(records) or set(ingested) != set(selection['video_ids']):
        raise ValueError('Ingestion identities must exactly match selection')
    sensors = [ingested[cid].get('sensor_id') for cid in selection['video_ids']]
    if not all(sensors) or len(set(sensors)) != len(sensors):
        raise ValueError('Missing or duplicate sensor IDs')
    config = load_deployment(args.deployment_config)
    dep = digest({'subset': selection['subset_fingerprint'], 'ingestion': ingestion, 'deployment': config,
                  'model': args.model, 'index': args.es_index, 'vector_field': args.vector_field,
                  'sensor_field': args.sensor_field,
                  'auth_config': sha256_file(args.auth_config) if args.auth_config else None,
                  'ca_bundle': sha256_file(args.ca_bundle) if args.ca_bundle else None})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    checkpoint = out / 'collection.json'
    if checkpoint.exists():
        if not args.resume:
            raise ValueError('Collection exists; use --resume or a new output directory')
        state = read_json(checkpoint)
        if state.get('dependency_fingerprint') != dep:
            raise ValueError('Collection dependencies changed; use a new output directory')
    else:
        state = metadata(selection, dependency_fingerprint=dep, clips={}, texts={}, complete=False,
                         model=args.model, index=args.es_index, vector_field=args.vector_field)
    with collection_attempt(checkpoint, state):
        state['complete'] = False
        state['started_epoch_seconds'] = time.time()
        state.pop('error', None)
        write_json(checkpoint, state)
        (out / 'bundle.json').unlink(missing_ok=True)
        net = network or Network(config, args.auth_config, args.ca_bundle)
        net.preflight(['elasticsearch', 'rt_embed'])
        net.require_model(args.model)
        net.require_index(args.es_index)
        snapshots = {}
        for route in ('_mapping', '_settings'):
            response = net.request('elasticsearch', 'GET', '/' + quote(args.es_index, safe='*,-_') + '/' + route, timeout=60)
            response.raise_for_status()
            snapshots[route] = response.json()
        write_json(out / 'elasticsearch.json', metadata(selection, **snapshots))
        deadline = time.monotonic() + args.wait_timeout
        while True:
            pending = []
            # Always reread videos on resume: stale cardinality cannot certify the deployment.
            for cid, sensor in zip(selection['video_ids'], sensors):
                matching = []
                for hit in scan_sensor(net, args.es_index, args.sensor_field, sensor, args.vector_field):
                    matching.extend(extract_vectors(hit, args.vector_field, args.model))
                if len(matching) > 1:
                    raise ValueError(f'{cid}: expected one indexed vector, found {len(matching)}')
                if not matching:
                    state['clips'].pop(cid, None)
                    pending.append(cid)
                    continue
                vector, provenance = matching[0]
                vector = validate_vectors(np.asarray([vector]), rows=1)[0]
                state['clips'][cid] = {'sensor_id': sensor, 'vector': vector.tolist(), **provenance}
            write_json(checkpoint, state)
            if not pending:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('No retrievable vector before deadline for: ' + ', '.join(map(str, pending)))
            time.sleep(min(args.poll_interval, remaining))
        client_paths = set()
        for query_id, query in zip(selection['text_ids'], subset['queries']):
            key = str(query_id)
            exact = query['query']
            if key in state['texts']:
                if state['texts'][key]['text_sha256'] != digest(exact):
                    raise ValueError('Query checkpoint text differs')
                continue
            vector, client_path = embed_text(net, config, args.model, exact)
            client_paths.add(client_path)
            vector = validate_vectors(np.asarray([vector]), rows=1)[0]
            state['texts'][key] = {'vector': vector.tolist(), 'text_sha256': digest(exact), 'client': client_path}
            write_json(checkpoint, state)
        text = validate_vectors(np.asarray([state['texts'][str(q)]['vector'] for q in selection['text_ids']]), rows=len(selection['text_ids']))
        video = validate_vectors(np.asarray([state['clips'][cid]['vector'] for cid in selection['video_ids']]), rows=len(sensors))
        if text.shape[1] != video.shape[1]:
            raise ValueError('Text and video dimensions differ')
        revisions = sorted({r['revision'] for r in state['clips'].values() if r.get('revision')})
        declared_models = sorted({r['model'] for r in state['clips'].values() if r.get('model')})
        versions = sorted({r['model_version'] for r in state['clips'].values() if r.get('model_version')})
        write_bundle(out, subset, selection, text, video,
                     {'approach': 'vss', 'model': args.model, 'checkpoint_parity': 'unverified',
                      'index': args.es_index, 'vector_field': args.vector_field,
                      'indexed_models': declared_models, 'indexed_revisions': revisions, 'indexed_model_versions': versions,
                      'preprocessing': 'deployment pipeline preserved; not independently controlled by collector',
                      'text_clients': sorted({r['client'] for r in state['texts'].values()}),
                      'mapping_settings_sha256': sha256_file(out / 'elasticsearch.json')})
        state['complete'] = True
        state['duration_seconds'] = time.time() - state['started_epoch_seconds']
        write_json(checkpoint, state)
        return out


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper()))
    try:
        run(args)
        return 0
    except Exception as exc:
        # Avoid exporting transport exception URLs, which may carry credentials.
        logging.error('Collection failed: %s', str(exc) if isinstance(exc, (ValueError, TimeoutError)) else type(exc).__name__)
        return 1


if __name__ == '__main__':
    sys.exit(main())
