# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
"""PhysicalAI v1.1.2 representative-frame PAS relevance, independent of retrieval."""
import re

VERSION = 'physicalai-pas-frame-supported-v1'
UNKNOWN = {'', 'unknown', 'none', 'not visible', 'no', 'other', 'nothing', 'n/a'}
FIELDS = {'top_outer_type', 'top_outer_color', 'bottom_type', 'bottom_color',
          'shoe_type', 'shoe_color', 'viewpoint', 'action', 'accessories'}


def supports(requirements, annotations):
    def values(value):
        items = value if isinstance(value, list) else [value]
        return [re.sub(r'\s+', ' ', str(v).strip().lower()) for v in items]
    for field, required in requirements.items():
        key = re.sub(r'\[\d+\]$', '', field)
        if key not in FIELDS:
            raise ValueError(f'Unsupported PAS attribute: {field}')
        actual = values(annotations[key.replace('_', ' ')])
        if not all(want not in UNKNOWN and any(
                candidate not in UNKNOWN and want in candidate for candidate in actual)
                for want in values(required)):
            return False
    return True


def frame_supported_queries(queries, tracks, gallery):
    """Return copied queries and explicit exclusions; leave event GT unchanged."""
    by_id = {t['track_uid']: t for t in tracks}
    if len(by_id) != len(tracks):
        raise ValueError('Duplicate PAS track IDs')
    eligible = {uid for uid, t in by_id.items() if t['eligible_for_gt']}
    kept, excluded = [], []
    for q in queries:
        if q['query_domain'] != 'pas':
            kept.append(dict(q))
            continue
        released = q['relevant_track_uids']
        if len(released) != len(set(released)) or not set(released) <= eligible:
            raise ValueError(f'Invalid released PAS positive tracks: {q["query_id"]}')
        supported = [uid for uid in released if supports(
            q['resolved_attributes'], by_id[uid]['representative_frame']['frame_attribute'])]
        clips = sorted({by_id[uid]['chunk_id'] for uid in supported})
        if not set(clips) <= set(gallery):
            raise ValueError(f'PAS positives outside gallery: {q["query_id"]}')
        if not clips:
            excluded.append({'query_id': q['query_id'], 'query_domain': 'pas',
                             'reason': 'no_frame_supported_positives'})
            continue
        updated = {**q, 'relevant_clip_ids': clips, 'n_relevant': len(clips),
                   'relevant_track_uids': supported, 'n_relevant_tracks': len(supported)}
        # Original temporal segments describe the old GT, not this derived GT.
        updated.pop('segments', None)
        kept.append(updated)
    return kept, excluded
