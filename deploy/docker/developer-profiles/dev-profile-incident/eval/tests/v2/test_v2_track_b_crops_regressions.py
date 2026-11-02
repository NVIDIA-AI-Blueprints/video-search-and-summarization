# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions for the Track B crop wiring after the agent's P4 review: where the tracker's summary lives."""

from __future__ import annotations

import sys
from pathlib import Path

from evalv2.adapters.vss import VssAgentAdapter
from evalv2.core.artifacts import ArtifactRef
from evalv2.tasks.pipeline import crops
from evalv2.tasks.pipeline.runner import PipelineTask

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_track_b import MODEL, SIGNED, FakeAgent, _config  # noqa: E402
from test_v2_track_b_regressions import _tracked_answer, _tracked_ctx  # noqa: E402


def _stored_tracked_run(tmp_path, cap=80, calls=80):
    task = PipelineTask(video_url=lambda clip: SIGNED, video_duration=lambda clip: 30.0)
    config = _config([{"id": "super-b", "adapter": "vss", "model": MODEL, "endpoint": "experiment-agent",
                       "profile": "production-v4-object-tracking"}])
    item = task.plan(config, {"clips": ["Assault001"]})[0]
    status, body = _tracked_answer(cap=cap, calls=calls)
    body["experiment"]["object_tracking"] = True  # P4: the request's setting, a bool
    body["object_tracking"]["time_cap_reached"] = True
    body["object_tracking"]["detector"].update(refused_by_cap=2, cap_reached=True)
    ctx = _tracked_ctx(tmp_path)
    record = task.execute(item, VssAgentAdapter("super-b", MODEL, post=FakeAgent((status, body))), ctx)
    return ctx.store.get(ArtifactRef.parse(record.prediction_ref))


def test_cap_summary_reads_the_trackers_summary_from_the_top_level_field(tmp_path):
    summary = crops.cap_summary([_stored_tracked_run(tmp_path)])
    assert summary["object_tracking_detector"] == {"runs": 1, "cap_reached": 1, "refused_calls": 2,
                                                   "max_calls": 80, "caps": [80]}
    assert summary["object_tracking_time"] == {"runs": 1, "cap_reached": 1}


def test_a_boolean_setting_is_not_mistaken_for_a_summary():
    summary = crops.cap_summary([{"experiment": {"object_tracking": True}, "object_tracking": None}])
    assert summary == {"runs": 1}
