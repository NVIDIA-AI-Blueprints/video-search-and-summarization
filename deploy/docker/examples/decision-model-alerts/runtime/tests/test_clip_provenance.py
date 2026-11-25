# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""A historical verdict may only be displayed beside its original clip bytes."""
import hashlib
import io

import pytest
from fastapi.testclient import TestClient

from agent_pipeline import analytics
from agent_pipeline.clips import clip_path, save_clip
from agent_pipeline.contracts import Rule
from agent_pipeline.incidents import record_review
from agent_pipeline.rules import process_decision
from agent_pipeline.store import Store


def setup_verified_clip(tmp_path, monkeypatch, verdict="confirmed"):
    monkeypatch.setenv("D1_CLIP_CACHE", str(tmp_path / "clips"))
    store = Store(tmp_path / "analytics.sqlite")
    store.put_config("rule", "queue", Rule(stream_id="traffic", config_revision=1, field="noul",
                                          value=.8, consecutive_samples=1).model_dump())
    event = process_decision(store, {"schema_version": 1, "kind": "d1.decision", "id": "decision",
        "stream_id": "traffic", "captured_at": 1000, "config_revision": 1, "answer": {"noul": .9},
        "question": {"type": "noul", "instructions": "Is traffic congested?"}})[0]
    original = b"original-verified-video"
    save_clip(event["id"], io.BytesIO(original))
    with store.transaction() as db:
        record_review(db, {"event_id": event["id"], "stream_id": "traffic", "verification_version": 1,
            "verdict": verdict, "summary": "A closely spaced queue is visible.",
            "evidence": ["Several vehicles barely move."], "uncertainty": ["The cause remains outside view."],
            "clip_sha256": hashlib.sha256(original).hexdigest()})
        db.execute("UPDATE jobs SET status='complete' WHERE id=?", (event["id"],))
    return TestClient(analytics.create_app(store, start_workers=False)), event, original


@pytest.mark.parametrize("verdict", ["confirmed", "rejected", "uncertain"])
def test_evicted_verified_clip_never_resolved_again(tmp_path, monkeypatch, verdict):
    client, event, _ = setup_verified_clip(tmp_path, monkeypatch, verdict)
    clip_path(event["id"]).unlink()

    def forbidden(_):
        pytest.fail("A verified clip must not be replaced by a later VIOS export")

    monkeypatch.setattr(analytics, "resolve_clip", forbidden)
    response = client.get(f"/v1/reviews/{event['id']}/clip")
    assert response.status_code == 410
    assert "no longer cached" in response.json()["detail"]


def test_changed_cache_file_cannot_be_shown_as_verified_evidence(tmp_path, monkeypatch):
    client, event, _ = setup_verified_clip(tmp_path, monkeypatch)
    clip_path(event["id"]).write_bytes(b"different-video-bytes")
    response = client.get(f"/v1/reviews/{event['id']}/clip")
    assert response.status_code == 410
    assert "no longer matches" in response.json()["detail"]


def test_exact_verified_file_keeps_video_range_playback(tmp_path, monkeypatch):
    client, event, original = setup_verified_clip(tmp_path, monkeypatch)
    response = client.get(f"/v1/reviews/{event['id']}/clip", headers={"Range": "bytes=0-7"})
    assert response.status_code == 206
    assert response.content == original[:8]
