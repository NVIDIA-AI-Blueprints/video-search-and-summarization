# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resume identity includes the effective source list submitted for decomposition."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows import pipeline
from test_runner_recovery import RecordingBackend, config


class RecordingDecomposer:
    def __init__(self):
        self.calls = []

    def describe(self):
        return {"model": "test", "source_type": "video_file", "system_prompt": "fixed"}

    def decompose(self, query, video_sources=None):
        self.calls.append((query, list(video_sources or [])))
        return {"query": query, "attributes": [], "has_action": True}, 0.01


@pytest.mark.parametrize("gallery", [False, True])
def test_resume_uses_effective_prompt_sources_not_unrelated_inventory(
    tmp_path, monkeypatch, gallery
):
    cfg = replace(config(tmp_path), vst_url="http://vst")
    if not gallery:
        cfg = replace(cfg, gallery_gt=None)
        monkeypatch.setattr(
            pipeline.flows,
            "load_dataset_file",
            lambda *args: {
                "queries": {
                    "first": [{"video_name": "c0"}],
                    "second": [{"video_name": "c1"}],
                }
            },
        )
    inventory = {
        "s0": {"name": "c0", "url": "/storage/c0.mp4"},
        "s1": {"name": "c1", "url": "/storage/c1.mp4"},
    }
    listing = Mock(side_effect=lambda *args: dict(inventory))
    monkeypatch.setattr(pipeline.flows, "list_sensor_sources", listing)
    monkeypatch.setattr(
        pipeline.flows,
        "inventory_snapshot",
        lambda *args: {
            "ok": True,
            "count": len(inventory),
            "names": sorted(row["name"] for row in inventory.values()),
        },
    )
    decomposer = RecordingDecomposer()
    pipeline.run_retrieve(cfg, RecordingBackend(), decomposer, None, None)
    assert decomposer.calls == [("first", ["c0", "c1"]), ("second", ["c0", "c1"])]
    checkpoint = str(cfg.retrieval_out) + ".checkpoint"
    header = json.loads(
        (tmp_path / "run.retrieval.json.checkpoint/checkpoint.json").read_text()
    )
    assert header["identity_components"]["prompt_sources"] == ["c0", "c1"]

    inventory["new"] = {"name": "unrelated", "url": "/storage/unrelated.mp4"}
    resumed_decomposer = RecordingDecomposer()
    resumed_backend = RecordingBackend()
    resumed = replace(cfg, resume_from=checkpoint)
    if gallery:
        artifact = pipeline.run_retrieve(
            resumed, resumed_backend, resumed_decomposer, None, None
        )
        assert artifact.described["resume_reused_queries"] == 2
        # Gallery runs retain their fixed gallery scope, despite VST additions.
        listing.assert_not_called()
    else:
        resumed = replace(
            resumed, retrieval_out=str(tmp_path / "resumed.retrieval.json")
        )
        with pytest.raises(SystemExit, match="resume settings.*differ"):
            pipeline.run_retrieve(
                resumed, resumed_backend, resumed_decomposer, None, None
            )
        assert listing.call_count == 2
    assert resumed_decomposer.calls == []
    assert resumed_backend.calls == []
