# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from streaming.storage import EventStore


def test_latest_batch_and_followup_cursor_do_not_replay_preserved_history(tmp_path):
    store = EventStore(tmp_path / 'events.sqlite')
    for number in range(1200):
        store.append({'id': f'event-{number}', 'session_id': 'selected', 'stream_id': 'camera', 'number': number})
    store.append({'id': 'other', 'session_id': 'other', 'stream_id': 'other'})
    recent = store.events('selected', limit=30, latest=True)
    assert [item['number'] for item in recent] == list(range(1170, 1200))
    cursor = recent[-1]['seq']
    assert store.events('selected', after_seq=cursor, limit=30) == []
    created = store.append({'id': 'new', 'session_id': 'selected', 'stream_id': 'camera'})
    assert store.events('selected', after_seq=cursor, limit=30) == [created]
    assert [item['number'] for item in store.events('selected', limit=2)] == [0, 1]
