# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows.clip_scoring import ClipScorer, aggregate_by_tier


def _mapping(size=4):
    return {chr(97 + i): {'sensor_id': str(i), 'filename': chr(97 + i) + '.mp4'} for i in range(size)}


def _query(qid, tier, relevant):
    q = {'query_id': qid, 'query_domain': 'pas', 'query': qid, 'relevant_clip_ids': list(relevant)}
    if tier is not None:
        q['pas_tier'] = tier
    return q


def _row(scorer, qid, tier, relevant, hits):
    return scorer.score(_query(qid, tier, relevant), hits)


def test_score_carries_pas_tier_and_none_when_absent():
    scorer = ClipScorer(_mapping(), [_query('q1', 'hard', ('a', 'c'))])
    row = _row(scorer, 'q1', 'hard', ('a', 'c'), [{'clip_id': 'a'}, {'clip_id': 'c'}])
    assert row['pas_tier'] == 'hard'
    scorer2 = ClipScorer(_mapping(), [_query('q2', None, ('a',))])
    row2 = scorer2.score(_query('q2', None, ('a',)), [{'clip_id': 'a'}])
    assert row2['pas_tier'] is None


def test_aggregate_by_tier_groups_easy_medium_hard_and_overall():
    queries = [_query('e1', 'easy', ('a', 'c')), _query('m1', 'medium', ('a',)),
                _query('h1', 'hard', ('a', 'c')), _query('x1', None, ('a',))]
    scorer = ClipScorer(_mapping(), queries)
    rows = [
        _row(scorer, 'e1', 'easy', ('a', 'c'), [{'clip_id': 'a'}, {'clip_id': 'c'}]),    # ap_at_10 = 1.0
        _row(scorer, 'm1', 'medium', ('a',), [{'clip_id': 'b'}, {'clip_id': 'a'}]),     # ap_at_10 = 0.5
        _row(scorer, 'h1', 'hard', ('a', 'c'), [{'clip_id': 'b'}, {'clip_id': 'a'}]),   # ap_at_10 = 0.5
        _row(scorer, 'x1', None, ('a',), [{'clip_id': 'a'}]),                            # ap_at_10 = 1.0, no tier
    ]
    by_tier = aggregate_by_tier(rows)
    assert set(by_tier) == {'overall', 'easy', 'medium', 'hard'}
    assert by_tier['overall']['mAP@10'] is not None
    assert by_tier['easy']['mAP@10'] == 1.0
    assert by_tier['medium']['mAP@10'] == 0.5
    assert by_tier['hard']['mAP@10'] == 0.25
    # overall averages all four rows, including the tier-less one.
    assert by_tier['overall']['mAP@10'] == (1.0 + 0.5 + 0.25 + 1.0) / 4


def test_aggregate_by_tier_with_no_tiers_returns_overall_only():
    scorer = ClipScorer(_mapping(), [_query('q', None, ('a',))])
    rows = [_row(scorer, 'q', None, ('a',), [{'clip_id': 'a'}])]
    by_tier = aggregate_by_tier(rows)
    assert set(by_tier) == {'overall'}
    assert by_tier['overall']['mAP@10'] == 1.0
