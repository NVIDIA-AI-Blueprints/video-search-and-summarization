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

"""
Bounding and downsampling of a track's point series as it grows.

A track accumulates points for as long as it stays alive, so the series has to be capped without
losing the shape of the trajectory. That is done by keeping 1 point in every N and doubling N
whenever the series outgrows ``behaviorMaxPoints``. Three invariants make it work, and they are the
reason this lives on its own rather than inline in state management:

* **The stride is continuous across batches.** ``sample_phase`` is carried on the state rather than
  restarted per batch, so a run of single-message batches cannot retain 100% of points the way a
  per-batch ``[::sampling]`` slice would.
* **The stride is continuous across halving.** Doubling N mid-series would otherwise shift which
  points survive; the parity correction in :func:`halve_if_needed` keeps the 1-in-2N pattern aligned
  with what came before.
* **``tail_ts`` tracks the last points, and only at stride 1.** Late messages are placed by
  bisecting that window, which is only meaningful while every point is retained.

Every function mutates the :class:`ObjectState` in place and keeps ``points``, ``bboxes`` and
``tail_ts`` mutually consistent.
"""

import bisect
import logging

from mdx.analytics.core.schema.models import Message, ObjectState

logger = logging.getLogger(__name__)

TAIL_CAP = 16  # max tail_ts entries — bounds bisect-insert memory at sampling == 1


def append_sampled(state: ObjectState, messages: list[Message]) -> None:
    """
    Append in-order messages to the track, keeping 1 point in every ``state.sampling``.

    ``sample_phase`` persists on the state, so the stride stays exact across batch boundaries.
    Timestamps are tracked only at stride 1, where :func:`insert_tolerance_messages` can use them.

    :param ObjectState state: Track state to extend, mutated in place.
    :param list[Message] messages: In-order messages for this track, oldest first.
    :return: None
    """
    for msg in messages:
        if state.sample_phase == 0:
            state.points.append(msg.object.coordinate)
            state.bboxes.append(msg.object.bbox)
            if state.sampling == 1:
                state.tail_ts.append(msg.timestamp)
        state.sample_phase = (state.sample_phase + 1) % state.sampling

    if state.sampling == 1 and len(state.tail_ts) > TAIL_CAP:
        state.tail_ts = state.tail_ts[-TAIL_CAP:]


def insert_tolerance_messages(state: ObjectState, messages: list[Message], message_key: str) -> None:
    """
    Place late-but-acceptable messages at their chronological position in the series.

    Only correct at stride 1: above it the retained points are a sample, so there is no position that
    would keep the stride meaningful, and the messages are dropped. A message older than the tracked
    ``tail_ts`` window is also dropped, since its position cannot be determined.

    :param ObjectState state: Track state to insert into, mutated in place.
    :param list[Message] messages: Messages inside the end tolerance window.
    :param str message_key: Track key, for logging.
    :return: None
    """
    if state.sampling != 1:
        return

    for msg in messages:
        ts = msg.timestamp
        if not state.tail_ts or ts < state.tail_ts[0]:
            logger.warning(
                f"Tolerance-window message (ts={ts}) precedes tracked tail window for {message_key}; dropping"
            )
            continue
        rel_idx = bisect.bisect_right(state.tail_ts, ts)
        abs_idx = len(state.points) - len(state.tail_ts) + rel_idx
        state.points.insert(abs_idx, msg.object.coordinate)
        state.bboxes.insert(abs_idx, msg.object.bbox)
        state.tail_ts.insert(rel_idx, ts)


def halve_if_needed(state: ObjectState, max_points: int) -> None:
    """
    Halve the series and double the stride once it outgrows ``max_points``.

    The parity correction is what preserves the 1-in-N pattern across the boundary: without it the
    doubled stride would resume out of step with the points already kept. ``tail_ts`` is cleared
    because bisect-insert is no longer valid above stride 1.

    :param ObjectState state: Track state to halve, mutated in place.
    :param int max_points: Point cap from ``behaviorMaxPoints``.
    :return: None
    """
    if len(state.points) <= max_points:
        return

    j = len(state.points) - (0 if state.sample_phase == 0 else 1)
    state.sample_phase += state.sampling * (j % 2)
    state.sampling *= 2
    state.points = state.points[::2]
    state.bboxes = state.bboxes[::2]
    state.tail_ts = []
