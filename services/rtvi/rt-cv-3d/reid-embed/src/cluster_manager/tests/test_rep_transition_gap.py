# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Regression tests for the surviving-cluster rep-transition merge gap.

When two clusters merge, ``surviving_cid = min(old_ids)`` but the merged
rep becomes the lower-``min_frame`` member across all merged clusters. If
the *absorbed* cluster supplied that rep, the surviving cluster adopts a
new rep and its own prior rows (live / tracking / compressed) would be
orphaned under the stale rep -- the ``{retired_rep: surviving_rep}`` entry
collapses to a self-map and every cascade skips it.

``add_surviving_rep_transitions`` closes that gap by emitting an explicit
``{old_surviving_rep: new_surviving_rep}`` entry, and
``build_tracking_updates_from_rep_map`` consumes the augmented map so the
tracking cascade migrates the rows too.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Dict

from cluster_manager.compression_labeling import (
    add_surviving_rep_transitions,
    cluster_id_to_object_id,
)
from cluster_manager.tracking_relabel import build_tracking_updates_from_rep_map


@dataclass
class _Arch:
    """Minimal stand-in: cluster_id_to_object_id only reads rep_object_id."""

    rep_object_id: Dict[int, int] = field(default_factory=dict)


class _Tobj:
    def __init__(self, object_id: str, frame_id: int):
        self.object_id = object_id
        self.frame_id = frame_id
        # Rep-transition rows are the merge's already-clustered members;
        # the builder now migrates only clustered entries.
        self.is_clustered = True


def test_survivor_adopting_absorbed_rep_gets_migrated():
    # Pre-merge: c1 rep=10001 (later first frame), c13 rep=10002 (earlier).
    # The service captures the emitted object_id per cluster (offset-agnostic).
    pre_archive = _Arch({1: 10001, 13: 10002})
    pre = {
        1: cluster_id_to_object_id(1, pre_archive),
        13: cluster_id_to_object_id(13, pre_archive),
    }
    old_c1_obj = pre[1]

    # Post-merge: surviving c1 (=min(1,13)) adopts c13's earlier rep 10002.
    # c13 retired but its rep entry is intentionally not pruned.
    post_archive = _Arch({1: 10002, 13: 10002})
    new_c1_obj = cluster_id_to_object_id(1, post_archive)
    assert old_c1_obj != new_c1_obj  # the survivor's rep actually changed

    # retired_map = {13: 1} -> retired_rep_map collapses to a self-map that
    # every cascade skips, so c1's old-rep rows would be orphaned.
    retired_rep_map = {cluster_id_to_object_id(13, post_archive): cluster_id_to_object_id(1, post_archive)}
    (k,) = retired_rep_map  # single entry
    assert retired_rep_map[k] == k  # self-map

    add_surviving_rep_transitions(retired_rep_map, {1}, pre, post_archive)

    # The fix migrates c1's stranded old-rep rows to the new rep.
    assert retired_rep_map[old_c1_obj] == new_c1_obj


def test_no_transition_when_survivor_keeps_its_rep():
    pre_archive = _Arch({1: 10001, 13: 10002})
    pre = {
        1: cluster_id_to_object_id(1, pre_archive),
        13: cluster_id_to_object_id(13, pre_archive),
    }
    # Surviving c1 kept its own rep (it had the lower min_frame).
    post_archive = _Arch({1: 10001, 13: 10001})
    rep_map = {"20002": "20001"}
    add_surviving_rep_transitions(rep_map, {1}, pre, post_archive)
    assert rep_map == {"20002": "20001"}  # no spurious extra entry


def test_freshly_allocated_survivor_has_nothing_to_migrate():
    post_archive = _Arch({5: 10005})
    rep_map: Dict[str, str] = {}
    add_surviving_rep_transitions(rep_map, {5}, {}, post_archive)
    assert rep_map == {}


def test_tracking_updates_from_rep_map_moves_and_skips_self():
    db = {"20001": {"0": [_Tobj("20001", 100), _Tobj("20001", 101)]}}
    updates = build_tracking_updates_from_rep_map(db, contextlib.nullcontext(), {"20001": "20002"})
    assert sorted(u[2] for u in updates) == [100, 101]
    assert all(u[0] == "20001" and u[3] == "20002" for u in updates)
    # self-map produces no updates
    assert build_tracking_updates_from_rep_map(db, contextlib.nullcontext(), {"20002": "20002"}) == []
