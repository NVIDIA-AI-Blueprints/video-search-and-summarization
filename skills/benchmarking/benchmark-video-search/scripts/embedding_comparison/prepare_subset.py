#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Select exact dataset filenames, preserving gallery/query order and labels."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import uuid

from comparison_common import cli_main, digest, sha256_file, read_json, write_json, subset_fingerprint


def prepare(data, video_list, out):
    data, out = Path(data).resolve(), Path(out)
    requested = Path(video_list).read_text(encoding='utf-8').splitlines()
    if not requested or any(not name or name != Path(name).name for name in requested):
        raise ValueError('video list must contain one exact clip filename per line, with no blank lines or paths')
    if len(requested) != len(set(requested)):
        raise ValueError('duplicate filenames in video list')
    release = read_json(data / 'manifest.json')
    ground_truth = read_json(data / 'gt/queries_gt.json')
    gallery_path = data / 'additional_gt/clips_gt.json'
    gallery = read_json(gallery_path).get('gallery') if gallery_path.is_file() else release.get('gallery', release.get('clips'))
    if not isinstance(gallery, list) or not gallery:
        raise ValueError('manifest requires a nonempty gallery or clips list')
    identities = [row.get('chunk_id') for row in gallery]
    if any(not isinstance(x, str) or not x for x in identities) or len(set(identities)) != len(identities):
        raise ValueError('missing or duplicate gallery chunk_id')
    manifest_records = release.get('clips', release.get('gallery', []))
    manifest_ids = [row.get('chunk_id') for row in manifest_records]
    if len(set(manifest_ids)) != len(manifest_ids):
        raise ValueError('duplicate manifest chunk_id')
    manifest_index = {row['chunk_id']: row for row in manifest_records}
    def media_name(row):
        source = manifest_index.get(row['chunk_id'], {})
        path = row.get('video') or row.get('path') or row.get('video_path') or source.get('path')
        if not isinstance(path, str) or not path:
            raise ValueError(f'gallery clip {row["chunk_id"]} has no media path')
        manifest_path = source.get('path')
        if manifest_path and (data / path).resolve() != (data / manifest_path).resolve():
            raise ValueError(f'gallery and manifest paths disagree for {row["chunk_id"]}')
        return path
    counts = Counter(Path(media_name(row)).name for row in gallery)
    for filename in requested:
        if counts[filename] != 1:
            raise ValueError(f'{filename}: expected exactly one gallery match, found {counts[filename]}')
    selected, clips = [], []
    for position, row in enumerate(gallery):
        path = Path(media_name(row))
        if path.name not in requested:
            continue
        path = (data / path).resolve()
        if not path.is_file():
            raise ValueError(f'selected media missing: {path}')
        media_hash = sha256_file(path)
        expected_hash = row.get('sha256') or manifest_index.get(row['chunk_id'], {}).get('sha256')
        if expected_hash and expected_hash != media_hash:
            raise ValueError(f'media hash differs from dataset manifest: {path.name}')
        record = deepcopy(row)
        record['video_path'] = str(path)
        selected.append(record)
        clips.append({'dataset_id': row['chunk_id'], 'filename': path.name, 'video_path': str(path),
                      'media_sha256': media_hash, 'original_gallery_index': position})
    selected_ids = {row['chunk_id'] for row in selected}
    original_queries = ground_truth.get('queries')
    if not isinstance(original_queries, list):
        raise ValueError('ground truth queries must be a list')
    candidates = [row.get('query_id', row.get('id')) for row in original_queries]
    id_counts = Counter(value for value in candidates if isinstance(value, str) and value)
    queries, query_records = [], []
    for position, original in enumerate(original_queries):
        if original.get('query_domain') != 'event':
            continue
        relevant = original.get('relevant_clip_ids')
        if not isinstance(relevant, list) or any(not isinstance(x, str) for x in relevant):
            raise ValueError(f'query row {position}: invalid relevant_clip_ids')
        if len(relevant) != len(set(relevant)) or any(x not in identities for x in relevant):
            raise ValueError(f'query row {position}: duplicate or unknown relevance identity')
        retained = [identity for identity in relevant if identity in selected_ids]
        if not retained:
            continue
        if not isinstance(original.get('query'), str):
            raise ValueError(f'query row {position}: text must be a string')
        candidate = candidates[position]
        query_id = candidate if isinstance(candidate, str) and candidate and id_counts[candidate] == 1 else f'row:{position}'
        record = deepcopy(original)
        label_provenance = {}
        for key, value in original.items():
            if key.startswith('relevant_clip_ids') and isinstance(value, list):
                record[key] = [identity for identity in value if identity in selected_ids]
                label_provenance[key] = {'original': value, 'retained': record[key]}
        record.update(query_id=query_id, n_relevant=len(retained))
        queries.append(record)
        query_records.append({'query_id': query_id, 'original_query_id': candidate,
                              'original_row_index': position, 'original_relevant_clip_ids': relevant,
                              'retained_relevant_clip_ids': retained, 'relevance_provenance': label_provenance})
    if not queries or not any(not row.get('near_universal', False) for row in queries):
        raise ValueError('no event query remains evaluable after original near_universal exclusions')
    text_ids = [row['query_id'] for row in queries]
    if len(set(text_ids)) != len(text_ids):
        # A user-supplied ID may collide with a generated row ID; never silently lose it.
        raise ValueError('original query ID collides with generated row-index sidecar ID')
    selection = {'schema_version': 1, 'video_ids': [row['chunk_id'] for row in selected],
                 'text_ids': text_ids, 'clips': clips, 'queries': query_records,
                 'source_hashes': {'manifest.json': sha256_file(data / 'manifest.json'),
                                   'gt/queries_gt.json': sha256_file(data / 'gt/queries_gt.json')}}
    if gallery_path.is_file():
        selection['source_hashes']['additional_gt/clips_gt.json'] = sha256_file(gallery_path)
    meta = deepcopy(ground_truth.get('meta', {}))
    meta.update(schema_version=1, n_gallery=len(selected), n_queries=len(queries),
                n_event_queries=len(queries), n_pas_queries=0, query_domains={'event': len(queries)},
                selection_fingerprint=digest(selection))
    subset = {'meta': meta, 'gallery': selected, 'queries': queries}
    fingerprint, run_id = subset_fingerprint(subset), str(uuid.uuid4())
    subset['meta'].update(subset_fingerprint=fingerprint, run_id=run_id)
    selection.update(subset_fingerprint=fingerprint, run_id=run_id)
    write_json(out / 'subset.json', subset)
    write_json(out / 'selection.json', selection)
    return subset, selection


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--video-list', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--log-level', default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])
    return parser


def main(args):
    prepare(args.data, args.video_list, args.out)


if __name__ == '__main__':
    raise SystemExit(cli_main(main, build_parser()))
