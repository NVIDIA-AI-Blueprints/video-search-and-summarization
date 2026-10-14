#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run original retrieval scorers and compare complete embedding bundles."""
import argparse
import csv
import logging
from pathlib import Path
import sys

import numpy as np
from comparison_common import load_bundle, read_json, sha256_file, write_json
from generate_reference_embeddings import ALIASES, advertised_flags, external_command, run_external


def align(bundle, text_ids, video_ids):
    for name, wanted in (('text', text_ids), ('video', video_ids)):
        ids = bundle[name + '_ids']
        if len(ids) != len(set(ids)) or len(wanted) != len(set(wanted)) or set(ids) != set(wanted):
            raise ValueError(f'Incomplete or duplicate {name} identities')
        bundle[name] = bundle[name][[ids.index(identity) for identity in wanted]]
    return bundle


def cosine_matrix(text, video):
    text, video = text.astype(np.float64), video.astype(np.float64)
    return (text / np.linalg.norm(text, axis=1, keepdims=True)) @ (video / np.linalg.norm(video, axis=1, keepdims=True)).T


def agreement(reference, vss, identities):
    reference, vss = reference.astype(np.float64), vss.astype(np.float64)
    rn, vn = np.linalg.norm(reference, axis=1), np.linalg.norm(vss, axis=1)
    normalized_r, normalized_v = reference / rn[:, None], vss / vn[:, None]
    values = {
        'cosine_similarity': np.sum(normalized_r * normalized_v, axis=1),
        'raw_l2': np.linalg.norm(vss - reference, axis=1),
        'normalized_l2': np.linalg.norm(normalized_v - normalized_r, axis=1),
        'reference_norm': rn, 'vss_norm': vn,
        'max_absolute_difference': np.max(np.abs(vss - reference), axis=1),
    }
    rows = [{'id': identity, **{key: float(value[i]) for key, value in values.items()}} for i, identity in enumerate(identities)]
    summaries = {key: {'count': len(value), 'minimum': float(np.min(value)), 'mean': float(np.mean(value)),
                       'median': float(np.median(value)), 'p05': float(np.percentile(value, 5)),
                       'p95': float(np.percentile(value, 95)), 'maximum': float(np.max(value))}
                 for key, value in values.items()}
    return rows, summaries


def write_csv(path, rows, fields=None):
    if fields is None:
        fields = list(rows[0]) if rows else ['id']
    with Path(path).open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def discover_script(directory, python, required, exclude=(), alternatives=()):
    matches = []
    diagnostics = []
    for script in sorted(Path(directory).resolve().glob('*.py')):
        if script.name in exclude:
            continue
        try:
            flags = advertised_flags(python, script)
            if any(all(any(flag in flags for flag in ALIASES[key]) for key in contract)
                   for contract in (required, *alternatives)):
                matches.append(script)
        except ValueError as exc:
            diagnostics.append(str(exc))
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one external script advertising {required}; found {[s.name for s in matches]}. '
                         f'Supply the original Drive scorers with supported --help interfaces. {diagnostics}')
    return matches[0]


def discover_event_script(directory, python, exclude=()):
    return discover_script(directory, python, ('metrics', 'out'), exclude,
                           alternatives=(('subset', 'emb_dir', 'out'),))


def flatten_metrics(data, prefix=''):
    """Flatten original numeric metrics without implementing new metric definitions."""
    flattened = {}
    if isinstance(data, dict):
        if not data and prefix:
            return {prefix: None}
        # A zero-count slice is unavailable, regardless of a scorer's placeholder zeros.
        if any(data.get(key) == 0 for key in ('count', 'n', 'n_queries', 'num_queries')):
            return {prefix: None}
        for key, value in data.items():
            flattened.update(flatten_metrics(value, f'{prefix}/{key}' if prefix else str(key)))
    elif isinstance(data, (int, float)) and not isinstance(data, bool):
        flattened[prefix] = float(data) if np.isfinite(data) else None
    elif data is None:
        flattened[prefix] = None
    return flattened


def evaluate(args):
    subset = read_json(args.subset)
    reference = load_bundle(args.reference_dir, subset)
    vss = load_bundle(args.vss_dir, subset)
    reference_manifest = reference['manifest']
    if reference_manifest['subset_fingerprint'] != vss['manifest']['subset_fingerprint']:
        raise ValueError('Bundles have different subset fingerprints')
    text_ids = [row['query_id'] for row in subset['queries']]
    video_ids = [row['chunk_id'] for row in subset['gallery']]
    reference = align(reference, text_ids, video_ids)
    vss = align(vss, text_ids, video_ids)
    if reference['text'].shape != vss['text'].shape or reference['video'].shape != vss['video'].shape:
        raise ValueError('Approaches have incompatible embedding dimensions or row counts')
    scorer = discover_script(args.scripts_dir, args.python, ('subset', 'text', 'video', 'out'), ('embed_cosmos.py',))
    event_scorer = discover_event_script(args.scripts_dir, args.python, ('embed_cosmos.py', scorer.name))
    event_flags = advertised_flags(args.python, event_scorer)
    # The original event script recomputes metrics from the aligned embeddings
    # and takes a JSON output filename. Retain the legacy metrics-directory API.
    events_from_embeddings = all(any(flag in event_flags for flag in ALIASES[key])
                                 for key in ('subset', 'emb_dir', 'out'))
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Report output must be empty: {out}')
    out.mkdir(parents=True, exist_ok=True)
    executions = {}
    metrics = {}
    for approach, bundle in (('reference', reference), ('vss', vss)):
        stage = out / approach
        stage.mkdir()
        # Feed aligned copies so external scorer sees the exact common ordering.
        inputs = stage / 'inputs'
        inputs.mkdir()
        np.save(inputs / 'text.npy', bundle['text'])
        np.save(inputs / 'video.npy', bundle['video'])
        write_json(inputs / 'video_ids.json', video_ids)
        scored = stage / 'retrieval'
        command = external_command(args.python, scorer,
            {'subset': Path(args.subset).resolve(), 'text': inputs / 'text.npy', 'video': inputs / 'video.npy',
             'video_ids': inputs / 'video_ids.json', 'out': scored}, {'subset', 'text', 'video', 'out'})
        executions[approach] = {'retrieval': run_external(command, scored)}
        metrics_path = scored / 'metrics.json'
        if not metrics_path.is_file():
            raise ValueError(f'Original scorer must export metrics.json in {scored}; unsupported output contract')
        if not any('rank' in p.name.lower() for p in scored.iterdir() if p.is_file()):
            raise ValueError(f'Original scorer did not export rankings in {scored}')
        events = stage / 'events'
        summary_path = events / 'event_summary.json'
        if events_from_embeddings:
            values = {'subset': Path(args.subset).resolve(), 'emb_dir': inputs, 'out': summary_path}
            required = set(values)
        else:
            values = {'subset': Path(args.subset).resolve(), 'metrics': metrics_path, 'out': events}
            required = {'metrics', 'out'}
        command = external_command(args.python, event_scorer, values, required)
        executions[approach]['events'] = run_external(command, events)
        if not summary_path.is_file():
            raise ValueError(f'Original event summary must export event_summary.json in {events}; unsupported output contract')
        metrics[approach] = {**flatten_metrics(read_json(metrics_path), 'retrieval'),
                             **flatten_metrics(read_json(summary_path), 'events')}
    retrieval_rows = []
    for key in sorted(set(metrics['reference']) | set(metrics['vss'])):
        r, v = metrics['reference'].get(key), metrics['vss'].get(key)
        retrieval_rows.append({'metric': key, 'reference': r, 'vss': v, 'vss_minus_reference': v-r if r is not None and v is not None else None})
    write_csv(out / 'retrieval_comparison.csv', retrieval_rows, ['metric', 'reference', 'vss', 'vss_minus_reference'])
    summaries = {}
    for kind, ids in (('text', text_ids), ('video', video_ids)):
        rows, summaries[kind] = agreement(reference[kind], vss[kind], ids)
        write_csv(out / f'{kind}_agreement.csv', rows)
    rm = cosine_matrix(reference['text'], reference['video'])
    vm = cosine_matrix(vss['text'], vss['video'])
    for name, matrix in (('reference_similarity', rm), ('vss_similarity', vm), ('similarity_difference', vm-rm)):
        np.save(out / f'{name}.npy', matrix)
    summary = {'schema_version': 1, 'subset_fingerprint': reference_manifest['subset_fingerprint'],
               'run_id': reference_manifest['run_id'], 'agreement': summaries, 'retrieval_comparison': retrieval_rows,
               'text_ids': text_ids, 'video_ids': video_ids, 'matrix_difference': {'max_absolute': float(np.max(np.abs(vm-rm))), 'mean_absolute': float(np.mean(np.abs(vm-rm)))},
               'scripts': {s.name: sha256_file(s) for s in (scorer, event_scorer)}, 'executions': executions,
               'checkpoint_parity': 'verified' if all(reference_manifest.get('provenance', {}).get(key) and reference_manifest.get('provenance', {}).get(key) == vss['manifest'].get('provenance', {}).get(key) for key in ('model', 'revision')) else 'unverified'}
    write_json(out / 'summary.json', summary)
    lines = ['# Embedding comparison', '', 'Cosine matrices use exhaustive exported vectors. Empty slices are unavailable.', '',
             '| Kind | Measure | Count | Minimum | Mean | Median | P05 | P95 | Maximum |', '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for kind, measures in summaries.items():
        for measure, values in measures.items():
            lines.append(f'| {kind} | {measure} | ' + ' | '.join(f'{values[key]:.8g}' for key in ('count','minimum','mean','median','p05','p95','maximum')) + ' |')
    lines.extend(['', '| Retrieval metric | Reference | VSS | VSS − reference |', '|---|---:|---:|---:|'])
    for row in retrieval_rows:
        lines.append('| ' + str(row['metric']) + ' | ' + ' | '.join('unavailable' if row[k] is None else f'{row[k]:.8g}' for k in ('reference','vss','vss_minus_reference')) + ' |')
    (out / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('subset', 'vss-dir', 'reference-dir', 'scripts-dir', 'out'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--python', default=sys.executable)
    p.add_argument('--log-level', default='INFO')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=args.log_level)
    try:
        evaluate(args)
        return 0
    except Exception as exc:
        logging.error('%s', exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
