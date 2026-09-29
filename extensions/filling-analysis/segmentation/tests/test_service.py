# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Worker contract tests: no GPU/models, network, or production source mutation."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

from segmentation import service


def make_job(path: Path, *, duration=1.5):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return service.Job(
        source_id="vss:test-stream:" + digest, source_sha256=digest,
        source_file=path.name, duration=duration, width=64, height=48, fps=24,
        stream_id="test-stream", source_clock_origin="2025-01-01T00:00:00.123Z",
    )


def test_liquid_clipped_to_bottle_and_polygons_map_to_original_coordinates():
    bottle = np.zeros((40, 30), bool)
    bottle[4:35, 5:25] = True
    bottle[4:15, 5:12] = False  # real nonrectangular boundary
    oversized_liquid = np.ones((20, 15), bool)
    liquid = service.clip_liquid(oversized_liquid, bottle)
    assert liquid.shape == bottle.shape
    assert not np.any(liquid & ~bottle)
    assert np.array_equal(liquid, bottle)
    rings = service.polygons(liquid, offset=(100, 200), size=(720, 1280))
    assert rings
    assert all((105 / 1280 - 1e-6) <= x <= (124 / 1280 + 1e-6)
               and (204 / 720 - 1e-6) <= y <= (234 / 720 + 1e-6)
               for ring in rings for x, y in ring)
    assert len(rings[0]) > 4  # no synthetic rectangle around the liquid


def test_source_path_rejects_traversal_and_outside_symlink(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(service, "DATA", data.resolve())
    payload = b"fixture source"
    digest = hashlib.sha256(payload).hexdigest()
    good = data / (digest + ".mp4")
    good.write_bytes(payload)
    job = make_job(good)
    assert service.source_path(job) == good.resolve()

    outside = tmp_path / (digest + ".mp4")
    outside.write_bytes(payload)
    for filename in ["../" + outside.name, str(outside)]:
        with pytest.raises(HTTPException) as error:
            service.source_path(job.model_copy(update={"source_file": filename}))
        assert error.value.status_code == 422
    good.unlink()
    good.symlink_to(outside)
    with pytest.raises(HTTPException) as error:
        service.source_path(job)
    assert error.value.status_code == 422


def test_job_preserves_exact_sampling_and_recording_clock(tmp_path, monkeypatch):
    source_bytes = b"isolated decode fixture"
    digest = hashlib.sha256(source_bytes).hexdigest()
    source = tmp_path / (digest + ".mp4")
    source.write_bytes(source_bytes)
    monkeypatch.setattr(service, "RESULTS", tmp_path / "results")
    job = make_job(source, duration=3 / 24)
    frames = [np.full((48, 64, 3), value, np.uint8) for value in (20, 80, 140)]
    commands = []

    class Decoder:
        def __init__(self):
            self.stdout = io.BytesIO(b"".join(f.tobytes() for f in frames))
            self.stderr = io.BytesIO()
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    def popen(command, **kwargs):
        commands.append(command)
        return Decoder()

    probe = {"streams": [{"width": 64, "height": 48, "avg_frame_rate": "24/1", "nb_frames": "3"}],
             "format": {"duration": "0.125"}}
    monkeypatch.setattr(service.subprocess, "check_output", lambda *a, **k: json.dumps(probe).encode())
    monkeypatch.setattr(service.subprocess, "Popen", popen)
    seen = []
    mask = np.zeros((48, 64), bool)
    mask[10:40, 20:40] = True

    class Bottle:
        def predict(self, frame, *, color_order):
            assert color_order == "BGR"
            seen.append(int(frame[0, 0, 0]))
            return [SimpleNamespace(raw_mask=mask, box=[20/64, 10/48, 20/64, 30/48],
                                    bottle_mask=service.polygons(mask), confidence=.9)]

    class Liquid:
        def predict(self, crop_rgb):
            return {"mask": np.ones(crop_rgb.shape[:2], bool), "confidence": .8}

    worker = service.Worker()
    worker.models = {"bottle": {"checkpoint_sha256": "fixture-b"}, "liquid": {"checkpoint_sha256": "fixture-l"}}
    worker.ready = True
    worker.bottle, worker.liquid = Bottle(), Liquid()
    key = worker.key(job.source_id, job.source_sha256, job.stream_id, job.source_clock_origin)
    worker.states[key] = {"status": "running", "progress": 0}
    worker.active = key
    worker.run(key, job, source)
    assert worker.states[key]["status"] == "complete"
    result = worker.results[key]
    assert [sample["t"] for sample in result["samples"]] == [0.0, 1 / 24, 2 / 24]
    assert [sample["frame_index"] for sample in result["samples"]] == [0, 1, 2]
    assert result["source_fps"] == result["sample_fps"] == 24
    assert result["schema_version"] == 2
    assert seen == [20, 80, 140]
    assert "-vf" not in commands[0]  # every decoded frame reaches both models
    assert result["samples"][0]["instances"][0]["measurement"]["level"] is not None
    assert result["provenance"]["source_clock_origin"] == "2025-01-01T00:00:00.123Z"
    assert result["provenance"]["stream_id"] == "test-stream"
    assert result["source_sha256"] == digest
    assert worker.active is None


def test_persisted_cache_is_invalidated_by_model_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "RESULTS", tmp_path)
    worker = service.Worker()
    worker.ready = True
    worker.models = {"bottle": {"checkpoint_sha256": "A"}, "liquid": {"checkpoint_sha256": "B"}}
    source_id, source_sha = "source-uuid", "a" * 64
    old_key = worker.key(source_id, source_sha)
    valid = {"schema_version": 2, "source_id": source_id, "source_sha256": source_sha, "models": worker.models,
             "provenance": {"cache_key": old_key}, "samples": []}
    (tmp_path / (old_key + ".json")).write_text(json.dumps(valid))
    assert worker.state(source_id, source_sha)["status"] == "complete"

    worker.models = {"bottle": {"checkpoint_sha256": "A"}, "liquid": {"checkpoint_sha256": "CHANGED"}}
    new_key = worker.key(source_id, source_sha)
    assert new_key != old_key
    assert worker.state(source_id, source_sha)["status"] == "idle"
    # Even if stale content is moved under the new filename, metadata must reject it.
    (tmp_path / (new_key + ".json")).write_text(json.dumps(valid))
    assert worker.state(source_id, source_sha)["status"] == "idle"


def test_recording_clock_and_stream_invalidate_cache():
    worker = service.Worker()
    base = worker.key("same-id", "a"*64, "stream-a", "2025-01-01T00:00:00Z")
    assert base != worker.key("same-id", "a"*64, "stream-a", "2026-01-01T00:00:00Z")
    assert base != worker.key("same-id", "a"*64, "stream-b", "2025-01-01T00:00:00Z")
