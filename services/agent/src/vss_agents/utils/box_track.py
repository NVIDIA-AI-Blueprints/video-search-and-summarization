# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""A reviewer's box followed through the clip (``POST /api/v1/incidents/{id}/box/track``).

The reviewer boxes an item at a second and names it; this seeds the object tracker
(:mod:`vss_agents.utils.object_tracking`) with that box and runs it back and forward over a short window, the
open-vocabulary detector re-anchoring it with the item's name and description. The answer is the contract track:
samples, span, and when the item could not be followed to the end of the window, ``partial`` with the second it was
lost at and a plain reason. Nothing is stored.
"""

from collections.abc import Sequence
import os
import time
from typing import Any

from vss_agents.utils.object_tracking import Detect
from vss_agents.utils.object_tracking import ObjectTrackingSettings
from vss_agents.utils.object_tracking import TrackerFactory
from vss_agents.utils.object_tracking import track_item
from vss_agents.utils.track_evidence import ObjectSeed
from vss_agents.utils.track_evidence import Sample

REVIEWER_SOURCE = "reviewer"
DEFAULT_WINDOW_SECONDS = 3.0
MAX_WINDOW_SECONDS = 30.0
REQUEST_TIME_CAP_SECONDS = 50.0  # the endpoint answers within about a minute
MAX_LABEL_CHARS = 240


class BoxTrackUnreadableError(RuntimeError):
    """The clip could not be opened or read at the box's second."""


def detector_label(name: str, description: str) -> str:
    """What the detector is asked to find: the item's name, then its description."""
    name, description = name.strip(), description.strip()
    label = f"{name}: {description}" if description else name
    return label[:MAX_LABEL_CHARS]


def request_settings(base: ObjectTrackingSettings) -> ObjectTrackingSettings:
    """The analysis tracker's settings, with the request's own time cap (they are otherwise the same)."""
    return ObjectTrackingSettings(
        **{**base.__dict__, "time_cap_seconds": min(base.time_cap_seconds, REQUEST_TIME_CAP_SECONDS)}
    )


def _sample_json(sample: Sample) -> dict[str, Any]:
    # ``conf`` is the contract's name; ``score`` is what the stored AI track files and the console's track reader use.
    return {"t": round(sample.t, 3), "box": list(sample.box), "conf": sample.score, "score": sample.score}


def _mmss(seconds: float) -> str:
    whole = int(max(0.0, seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def track_from_box(
    video_ref: str | os.PathLike[str],
    *,
    seconds: float,
    box: Sequence[float],
    name: str,
    description: str,
    back_seconds: float = DEFAULT_WINDOW_SECONDS,
    forward_seconds: float = DEFAULT_WINDOW_SECONDS,
    detect: Detect,
    settings: ObjectTrackingSettings,
    tracker_factory: TrackerFactory,
) -> dict[str, Any]:
    """The contract ``track`` for ``box`` at ``seconds``; blocking (run it on a thread).

    Raises :class:`BoxTrackUnreadableError` when the clip cannot be read.
    """
    seed = ObjectSeed(
        item_id="reviewer",
        label=detector_label(name, description),
        box=(round(box[0]), round(box[1]), round(box[2]), round(box[3])),
        seconds=seconds,
    )
    back = min(max(back_seconds, 0.0), MAX_WINDOW_SECONDS)
    forward = min(max(forward_seconds, 0.0), MAX_WINDOW_SECONDS)
    active = request_settings(settings)
    track, telemetry = track_item(
        video_ref,
        seed,
        detect=detect,
        settings=active,
        tracker_factory=tracker_factory,
        deadline=time.monotonic() + active.time_cap_seconds,
        spans=(back, forward),
    )
    if telemetry.get("unreadable"):
        raise BoxTrackUnreadableError("the video could not be read")
    samples = list(track.samples) if track is not None else [Sample(seed.seconds, seed.box, seed.score)]
    lost_at = telemetry.get("lost_at_forward")
    if lost_at is None:
        lost_at = telemetry.get("lost_at_backward")
    capped = telemetry.get("capped")
    skipped = telemetry.get("skipped")
    partial = bool(track is None or lost_at is not None or capped)
    reason: str | None = None
    if skipped:
        reason = str(skipped)
    elif track is None:
        reason = "the tracker could not follow the box to another frame"
    elif lost_at is not None:
        reason = f"lost the item at {_mmss(lost_at)}"
    elif capped:
        reason = "stopped early: time limit" if capped == "time" else "stopped early: detector call limit"
    return {
        "source": REVIEWER_SOURCE,
        "samples": [_sample_json(sample) for sample in samples],
        "span": [samples[0].t, samples[-1].t],
        "partial": partial,
        "lost_at": lost_at if partial and lost_at is not None else None,
        "reason": reason,
    }
