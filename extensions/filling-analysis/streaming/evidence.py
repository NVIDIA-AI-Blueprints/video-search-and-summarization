# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Serve only actual persisted diagnostic frames bound to a known event."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path


def snapshots(event, root):
    if not event or not event.get('diagnostic', {}).get('available'):
        return []
    # Paths derive from validated stored event identity, never a client path.
    track = event.get('track_id', '')
    expected = re.fullmatch(re.escape(event['session_id'])+r':epoch-(\d+):(cycle-\d+)', track)
    if not expected or int(expected[1]) != event['epoch']:
        return []
    root = Path(root).resolve()
    directory = (root/event['session_id']/f"epoch-{event['epoch']}"/expected[2]).resolve()
    if not directory.is_relative_to(root):
        return []
    provenance = directory/'provenance.json'
    if not provenance.is_file() or provenance.stat().st_size > 262144:
        return []
    try:
        data = json.loads(provenance.read_text())
    except (ValueError, OSError):
        return []
    if data.get('connection_epoch') != event['epoch'] or not isinstance(data.get('frames'), list) or len(data['frames']) > 256:
        return []
    result, seen = [], set()
    for frame in data['frames']:
        fid, pts = frame.get('frame_seq'), frame.get('pts_seconds')
        if type(fid) is not int or fid < 0 or fid in seen or type(pts) not in (int, float) or not math.isfinite(pts):
            return []
        seen.add(fid)
        path = directory/f'frame-{fid}.jpg'
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 8388608:
            continue
        result.append({'session_id': event['session_id'], 'stream_id': event['stream_id'],
            'event_id': event['event_id'], 'frame_id': fid, 'source_pts_seconds': pts,
            'verified': True, 'verification': 'exact-decoded-frame', 'mime_type': 'image/jpeg', '_path': str(path)})
    return result


def public_snapshots(event, root):
    return [{k: v for k,v in item.items() if k != '_path'} for item in snapshots(event, root)]
