# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pure clip scoring shared by online collection and offline replay.

No retrieval or normalization happens here. AP divides by all relevant clips;
AP@10 divides by min(R, 10). Rankings are never sorted or backfilled.
"""
from collections import Counter
from math import log2
from pathlib import Path

VERSION = 'clips-v2'
CUTOFFS = (1, 3, 5, 10)
CONFIG = {'ranking': 'response order; first occurrence per clip',
          'ap_denominator': 'all ground-truth relevant clips',
          'ap_at_10_denominator': 'min(R, 10)',
          'precision_at_k_denominator': 'k', 'relevance': 'binary',
          'filter': 'reject only, before deduplication; no backfill'}


def verdict(hit):
    key = 'verification' if 'verification' in hit else 'critic_result'
    if key not in hit:
        return 'missing'
    value = hit[key]
    if value is None:
        return 'null'
    if isinstance(value, dict):
        return str(value['result']) if 'result' in value else 'missing-result'
    return 'invalid'


class ClipScorer:
    def __init__(self, mapping, queries):
        if not mapping:
            raise ValueError('An explicit nonempty gallery mapping is required')
        self.gallery = set(mapping)
        self.aliases = {}
        for cid, item in mapping.items():
            for field, value in [('clip_id', cid), ('sensor_id', item.get('sensor_id')),
                                 ('video_name', Path(item['filename']).stem),
                                 ('video_name', item['filename'])]:
                if not value:
                    raise ValueError(f'Missing {field} for {cid}')
                key = (field, value)
                if key in self.aliases and self.aliases[key] != cid:
                    raise ValueError(f'Ambiguous gallery identity: {key}')
                self.aliases[key] = cid
        ids = [q['query_id'] for q in queries]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate query IDs')
        for q in queries:
            relevant = q['relevant_clip_ids']
            if not relevant or len(set(relevant)) != len(relevant) or not set(relevant) <= self.gallery:
                raise ValueError(f'Invalid relevance set: {q["query_id"]}')

    def resolve(self, hit):
        resolved = set()
        for field in ('clip_id', 'sensor_id', 'video_name'):
            value = hit.get(field)
            if value is not None:
                cid = self.aliases.get((field, value))
                if cid is None:
                    # VST display filenames can include an upload suffix. Sensor IDs
                    # are authoritative; never strip or guess filename suffixes.
                    if field == 'video_name' and resolved:
                        continue
                    raise ValueError(f'Unknown clip identity {field}={value!r}')
                resolved.add(cid)
        if len(resolved) != 1:
            raise ValueError(f'Missing or conflicting clip identities: {resolved}')
        return resolved.pop()

    def score(self, query, hits, latency=0):
        relevant = set(query['relevant_clip_ids'])
        ids = [self.resolve(h) for h in hits]
        ranking = list(dict.fromkeys(ids))
        labels = [int(cid in relevant) for cid in ranking]
        r, n = len(relevant), len(ranking)
        total = sum(labels)
        precision = total / n if n else 0.0
        recall = total / r
        cumulative = 0
        contributions = []
        for rank, label in enumerate(labels, 1):
            cumulative += label
            contributions.append(cumulative / rank if label else 0.0)
        def ndcg(k):
            dcg = sum(label / log2(i + 2) for i, label in enumerate(labels[:k]))
            ideal = sum(1 / log2(i + 2) for i in range(min(r, k)))
            return dcg / ideal if ideal else 0.0
        result = {'query_id': query['query_id'], 'query': query['query'],
                  'average_precision': sum(contributions) / r,
                  'ap_at_10': sum(contributions[:10]) / min(r, 10),
                  'ndcg': ndcg(len(self.gallery)), 'ndcg_at_10': ndcg(10),
                  'reciprocal_rank': next((1 / (i + 1) for i, x in enumerate(labels) if x), 0.0),
                  'precision': precision, 'recall': recall,
                  'f1': 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
                  'hit_at_k': {}, 'at_k': {}, 'ranking': ranking,
                  'relevant_count': r, 'retrieved_relevant': total,
                  'raw_hits': len(ids), 'unique_clips': n, 'duplicate_hits': len(ids) - n,
                  'gallery_size': len(self.gallery), 'gallery_coverage': n / len(self.gallery),
                  'ranking_complete': n == len(self.gallery), 'latency_s': latency}
        for k in CUTOFFS:
            count = sum(labels[:k])
            p, rec = count / k, count / r
            result['hit_at_k'][k] = int(count > 0)
            result['at_k'][k] = {'precision': p, 'recall': rec,
                'f1': 2 * p * rec / (p + rec) if p + rec else 0.0,
                'ndcg': ndcg(k)}
        return result

    def pair(self, query, hits, latency=0):
        raw = self.score(query, hits, latency)
        kept = [h for h in hits if verdict(h) != 'rejected']
        filtered = self.score(query, kept, latency)
        lost = set(raw['ranking']) - set(filtered['ranking'])
        relevant = set(query['relevant_clip_ids'])
        return {'raw': raw, 'critic_filtered': filtered,
                'critic_effect': {'relevant_clips_removed': len(lost & relevant),
                                 'irrelevant_clips_removed': len(lost - relevant)},
                'critic': dict(Counter(verdict(h) for h in hits))}


def aggregate(rows):
    keys = {'mAP': 'average_precision', 'mAP@10': 'ap_at_10', 'nDCG': 'ndcg',
            'nDCG@10': 'ndcg_at_10', 'MRR': 'reciprocal_rank',
            'precision': 'precision', 'recall': 'recall', 'F1': 'f1'}
    def mean(values):
        return sum(values) / len(rows) if rows else None
    result = {label: mean([r[key] for r in rows]) for label, key in keys.items()}
    for k in CUTOFFS:
        result[f'HIT@{k}'] = mean([r['hit_at_k'][k] for r in rows])
        for label, key in [('P', 'precision'), ('R', 'recall'), ('F1', 'f1'), ('nDCG', 'ndcg')]:
            result[f'{label}@{k}'] = mean([r['at_k'][k][key] for r in rows])
    result['complete_rankings'] = sum(r['ranking_complete'] for r in rows)
    result['mAP_scope'] = ('full-gallery' if rows and all(r['ranking_complete'] for r in rows)
                           else 'observed-ranking; incomplete gallery coverage')
    return result
