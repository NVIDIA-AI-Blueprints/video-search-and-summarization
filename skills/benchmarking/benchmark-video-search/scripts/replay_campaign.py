# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline clip/native rescoring. Reads evidence only; writes a separate report directory."""
import argparse
import csv
import io
from collections import Counter
from pathlib import Path
from flows.capture import atomic_json, atomic_bytes, read_json, verify, digest
from flows.metrics import evaluate_query
from flows.clip_scoring import ClipScorer, aggregate, CONFIG, VERSION as SCORER_VERSION, verdict

MODES = ('embed', 'attribute', 'live-routed', 'forced-fusion')


def successful(campaign, mode, query_id):
    records = []
    for attempt in sorted((campaign / 'attempts' / mode / query_id).glob('*')):
        if (attempt / 'complete.json').exists():
            verify(attempt)
            records.append(attempt)
    if len(records) > 1:
        raise ValueError(f'Multiple successful attempts for {mode}/{query_id}')
    return records[0] if records else None


def replay(campaign, destination, scorer="clips", *, pas_frame_supported=False, modes=None):
    if scorer not in ("clips", "native", "segments"):
        raise ValueError(f"Unknown scorer: {scorer}")
    if pas_frame_supported and scorer != 'clips':
        raise ValueError('Frame-supported PAS GT requires clip scoring')
    campaign, destination = Path(campaign).resolve(), Path(destination).resolve()
    if destination == campaign or campaign in destination.parents:
        raise ValueError('Reports must be separate from immutable campaign evidence')
    snapshot_seal = verify(campaign / 'snapshot')
    manifest = read_json(campaign / 'campaign.json')
    if digest(campaign / 'campaign.json') != read_json(campaign / 'identity.json')['sha256']:
        raise ValueError('Campaign identity changed')
    queries = read_json(campaign / 'snapshot' / 'queries.json')
    input_counts = dict(Counter(q['query_domain'] for q in queries))
    excluded, gt_metadata = [], {'protocol': 'released'}
    mapping = read_json(campaign / 'snapshot/gallery-mapping.json') if scorer == 'clips' else None
    if pas_frame_supported:
        from flows.pas_ground_truth import frame_supported_queries, VERSION
        track_file = 'dataset/additional_gt/tracks_pas_attributes.json'
        if track_file not in snapshot_seal['sha256']:
            raise ValueError('Frame-supported PAS requires sealed track annotations in snapshot')
        queries, excluded = frame_supported_queries(queries,
            read_json(campaign / 'snapshot' / track_file)['tracks'], mapping)
        gt_metadata = {'protocol': VERSION,
                       'tracks_sha256': snapshot_seal['sha256'][track_file],
                       'code_sha256': digest(Path(__file__).parent / 'flows/pas_ground_truth.py')}
    clip_scorer = ClipScorer(mapping, queries) if scorer == 'clips' else None
    if not clip_scorer and any('segments' not in q for q in queries):
        raise ValueError('Native scoring requires explicit segment annotations')
    available_modes = manifest.get('inputs', {}).get('settings', {}).get('modes', MODES)
    selected_modes = available_modes if modes is None else modes
    if not selected_modes or len(set(selected_modes)) != len(selected_modes) or not set(selected_modes) <= set(available_modes):
        raise ValueError('Select unique modes present in the captured campaign')
    destination.mkdir(parents=True, exist_ok=False)
    result = {'source_campaign': str(campaign), 'campaign_id': manifest['campaign_id'],
              'campaign_sha256': digest(campaign / 'campaign.json'), 'scorer': SCORER_VERSION if clip_scorer else 'native-segment-v1',
              'scorer_sha256': digest(Path(__file__).parent / ('flows/clip_scoring.py' if clip_scorer else 'flows/metrics.py')),
              'replay_sha256': digest(__file__), 'config': CONFIG if clip_scorer else {'filter': 'reject only; no backfill'}, 'format_version': 2, 'collection_settings': manifest.get('inputs', {}).get('settings', {}), 'modes': {}}
    table = ['| Mode | Domain | Scoring | Answered / expected | mAP | MRR | Precision | Recall | F1 | HIT@1 | HIT@3 | HIT@5 | HIT@10 |',
             '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    native_comparison = ['| Mode | Domain | View | Queries | Native mAP | Clip observed mAP | Clip mAP@10 |', '|---|---|---|---:|---:|---:|---:|']
    result['ground_truth'] = gt_metadata
    result['cohort'] = {'input': input_counts,
                        'eligible': dict(Counter(q['query_domain'] for q in queries)),
                        'excluded': excluded}
    for mode in selected_modes:
        rows, failures = [], []
        for q in queries:
            attempt = successful(campaign, mode, q['query_id'])
            if attempt is None:
                failures.append({'query_id': q['query_id'], 'state': 'unanswered',
                    'attempts': [str(p.relative_to(campaign)) for p in
                        sorted((campaign / 'attempts' / mode / q['query_id']).glob('*'))]})
                continue
            request = read_json(attempt / 'request.json')
            proc = read_json(attempt / 'process.json')
            envelope = read_json(attempt / 'response.json')['envelope']
            hits = envelope['data']
            latency = proc['elapsed_s']
            dec_s = 0
            if request.get('decomposition_ref'):
                decpath = campaign / request['decomposition_ref']
                verify(decpath)
                dec_s = read_json(decpath / 'process.json')['elapsed_s']
            rows.append({'query_id': q['query_id'], 'domain': q['query_domain'],
                'attempt': str(attempt.relative_to(campaign)), 'path': request['plan']['path'],
                'hits': len(hits), 'unique_clips': len({h.get('video_name') for h in hits}),
                'critic': dict(Counter(verdict(h) for h in hits)), 'latency_s': latency,
                'decomposition_s': dec_s, 'timings': envelope.get('timings'),
                'raw': evaluate_query(q['query'], hits, q['segments'], latency) if 'segments' in q else None,
                'critic_filtered': evaluate_query(q['query'], [h for h in hits if verdict(h) != 'rejected'], q['segments'], latency) if 'segments' in q else None})
            if clip_scorer:
                row = rows[-1]
                row['native'] = {key: row[key] for key in ('raw', 'critic_filtered')}
                row.update(clip_scorer.pair(q, hits, latency))
                row['unique_clips'] = row['raw']['unique_clips']
                row['retrieval'] = {'requested_hits': request['plan'].get('top_k'),
                    'cap_saturated': len(hits) >= (request['plan'].get('top_k') or 1000),
                    'filters': {key: request['plan'].get(key) for key in ('video_sources', 'timestamp_start', 'timestamp_end')}}
        groups = {}
        for domain in ('all', 'event', 'pas'):
            selected = [r for r in rows if domain == 'all' or r['domain'] == domain]
            n = len(selected)
            expected = sum(domain == 'all' or q['query_domain'] == domain for q in queries)
            summary = {'answered': n, 'expected': expected, 'partial': n != expected,
                'routing': dict(Counter(r['path'] for r in selected)),
                'critic': dict(sum((Counter(r['critic']) for r in selected), Counter())),
                'hits': sum(r['hits'] for r in selected),
                'queries_with_hits': sum(r['hits'] > 0 for r in selected),
                'mean_unique_clips': sum(r['unique_clips'] for r in selected) / n if n else None,
                'mean_latency_s': sum(r['latency_s'] for r in selected) / n if n else None,
                'mean_decomposition_s': sum(r['decomposition_s'] for r in selected) / n if n else None}
            if clip_scorer:
                summary['queries_at_request_cap'] = sum(r['retrieval']['cap_saturated'] for r in selected)
                summary['duplicate_hits'] = sum(r['raw']['duplicate_hits'] for r in selected)
                summary['critic_effect'] = {key: sum(r['critic_effect'][key] for r in selected) for key in ('relevant_clips_removed', 'irrelevant_clips_removed')}
                summary['critic_verdict_coverage'] = sum(r['critic'].get('confirmed', 0) + r['critic'].get('rejected', 0) for r in selected) / summary['hits'] if summary['hits'] else None
            for scoring in ('raw', 'critic_filtered'):
                metrics = {label: sum(r[scoring][key] for r in selected) / n if n else None
                    for label, key in [('mAP','average_precision'),('MRR','reciprocal_rank'),('precision','precision'),('recall','recall'),('F1','f1')]}
                metrics.update({f'HIT@{k}': sum(r[scoring]['hit_at_k'][k] for r in selected) / n if n else None for k in (1,3,5,10)})
                summary[scoring] = metrics
                if clip_scorer:
                    summary[scoring] = aggregate([r[scoring] for r in selected])
                    native = [r['native'][scoring]['average_precision'] for r in selected if r['native'][scoring] is not None]
                    if len(native) == n and n:
                        native_comparison.append(f'| {mode} | {domain} | {scoring} | {n} | {sum(native)/n:.6f} | {summary[scoring]["mAP"]:.6f} | {summary[scoring]["mAP@10"]:.6f} |')
                table.append(f'| {mode} | {domain} | {scoring} | {n} / {expected} | ' + ' | '.join('NA' if x is None else f'{x:.6f}' for x in metrics.values()) + ' |')
            groups[domain] = summary
        result['modes'][mode] = {'groups': groups, 'queries': rows, 'unanswered': failures}
    if clip_scorer:
        table = ['Clip metrics: observed rankings only; unreturned relevant clips receive no credit. Partial reports use answered queries as the denominator.', '', '| Mode | Domain | View | Answered / expected | Coverage | mAP | mAP@10 | nDCG | nDCG@10 | P@10 | R@10 | HIT@1 | HIT@5 | HIT@10 |', '|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
        for mode, data in result['modes'].items():
            for domain, group in data['groups'].items():
                for view in ('raw', 'critic_filtered'):
                    m = group[view]
                    values = [m[k] for k in ('mAP', 'mAP@10', 'nDCG', 'nDCG@10', 'P@10', 'R@10', 'HIT@1', 'HIT@5', 'HIT@10')]
                    table.append(f'| {mode} | {domain} | {view} | {group["answered"]} / {group["expected"]} | {m["mAP_scope"]} | ' + ' | '.join('NA' if v is None else f'{v:.6f}' for v in values) + ' |')
        map_table = ['Clip mAP@10 on observed rankings; AP@10 divides by min(R, 10). Total averages eligible queries directly.', '', '| Mode | View | Event | PAS | Total |', '|---|---|---:|---:|---:|']
        per_query = io.StringIO()
        writer = csv.writer(per_query)
        writer.writerow(['mode', 'query_id', 'domain', 'view', 'AP', 'AP@10', 'nDCG@10', 'unique_clips', 'relevant_clips', 'gallery_size', 'search_latency_s'])
        for mode, data in result['modes'].items():
            for view in ('raw', 'critic_filtered'):
                values = [data['groups'][d][view]['mAP@10'] for d in ('event', 'pas', 'all')]
                map_table.append(f'| {mode} | {view} | ' + ' | '.join(('NA' if v is None else f'{v:.6f}') + f' ({data["groups"][d]["answered"]}/{data["groups"][d]["expected"]})' for d, v in zip(('event', 'pas', 'all'), values)) + ' |')
            for row in data['queries']:
                for view in ('raw', 'critic_filtered'):
                    m = row[view]
                    writer.writerow([mode, row['query_id'], row['domain'], view, m['average_precision'], m['ap_at_10'], m['ndcg_at_10'], m['unique_clips'], m['relevant_count'], m['gallery_size'], row['latency_s']])
        atomic_bytes(destination / 'map-summary.md', ('\n'.join(map_table) + '\n').encode())
        atomic_bytes(destination / 'per-query.csv', per_query.getvalue().encode())
        atomic_bytes(destination / 'native-vs-clips.md', ('\n'.join(native_comparison) + '\n').encode())
    atomic_json(destination / 'report.json', result)
    atomic_bytes(destination / 'comparison.md', ('\n'.join(table) + '\n').encode())
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('campaign', type=Path)
    p.add_argument('--report-dir', required=True, type=Path)
    p.add_argument('--scorer', choices=['clips', 'native', 'segments'], default='clips')
    p.add_argument('--pas-frame-supported', action='store_true',
                   help='Derive PAS clip positives from sealed representative-frame annotations; exclude unsupported queries')
    p.add_argument('--modes', nargs='+', choices=MODES, help='Only replay these captured modes')
    a = p.parse_args()
    replay(a.campaign, a.report_dir, a.scorer, pas_frame_supported=a.pas_frame_supported, modes=a.modes)


if __name__ == '__main__':
    main()
