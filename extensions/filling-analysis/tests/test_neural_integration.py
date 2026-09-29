# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prevent same-video legacy numbers or a different GPU model from passing as neural results."""
import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from backend.app import AnalysisStore, Settings, _validate_result
from backend.segmentation import SegmentationProxy


CONTRACT = {"engine": "rfdetr", "algorithm": "rfdetr-mask-cycle-v2",
            "model_hashes": {"bottle": "a" * 64, "liquid": "b" * 64},
            "segmentation_pipeline_sha256": "c" * 64}


def result_fixture():
    return {"version": "2", "source_sha256": "d" * 64, "algorithm": CONTRACT["algorithm"],
            "analysis_kind": "bottle-cycles", "sample_fps": 24, "runtime_seconds": 1,
            "samples": [], "events": [], "bottles": [], "cycles": [],
            "summary": {"total": 0, "normal": 0, "underfill": 0, "overflow": 0, "uncertain": 0},
            "quality": {"measurement_engine": "rfdetr"}, "measurement": dict(CONTRACT)}


def source_fixture():
    return {"id": "fixture", "sha256": "d" * 64, "duration": 10,
            "analysis_kind": "bottle-cycles", "expected_measurement": CONTRACT}


def test_same_video_calibrated_result_is_not_a_neural_measurement():
    old = result_fixture()
    old["algorithm"] = "single-station-pixel-cycle-v1"
    old.pop("measurement")
    with pytest.raises(ValueError, match="stale"):
        _validate_result(old, source_fixture())


def test_current_models_and_pipeline_are_required_even_for_same_source():
    result = result_fixture()
    result["measurement"]["model_hashes"] = {"bottle": "a" * 64, "liquid": "e" * 64}
    with pytest.raises(ValueError, match="stale"):
        _validate_result(result, source_fixture())


def test_neural_analysis_routes_through_gpu_result_and_never_legacy_analyzer(tmp_path, monkeypatch):
    settings = Settings(media_path=tmp_path / "fixture.mp4", data_dir=tmp_path,
                        segmentation_url="http://gpu-worker:8091")
    store = AnalysisStore(settings)
    source = source_fixture()
    monkeypatch.setattr(store, "source", lambda: source)
    monkeypatch.setattr(store, "context", lambda: ("cache-key", {"expected_measurement": CONTRACT}, source))
    calls = []
    gpu_result = {"provenance": {"pipeline_sha256": "c" * 64}, "samples": [{"frame_index": 0}]}

    async def gpu(self, source_id, expected, progress_callback=None, force=False):
        calls.append((source_id, expected, force))
        return gpu_result

    def aggregate(result, video_path, **kwargs):
        assert result is gpu_result
        return result_fixture()

    monkeypatch.setattr(SegmentationProxy, "analyze", gpu)
    monkeypatch.setitem(sys.modules, "backend.neural_vision", SimpleNamespace(analyze_segmentation=aggregate))
    store._run("cache-key", {"expected_measurement": CONTRACT}, source, True)
    assert store._state["status"] == "complete"
    assert calls == [("fixture", CONTRACT, True)]
    assert store._result["algorithm"] == "rfdetr-mask-cycle-v2"
    assert json.loads((tmp_path / "analyses/cache-key.json").read_text())["result"]["measurement"] == CONTRACT


def test_gpu_failure_stays_an_error_instead_of_publishing_old_numbers(tmp_path, monkeypatch):
    store = AnalysisStore(Settings(media_path=tmp_path / "fixture.mp4", data_dir=tmp_path,
                                   segmentation_url="http://gpu-worker:8091"))
    source = source_fixture()
    monkeypatch.setattr(store, "source", lambda: source)
    async def failed(*args, **kwargs):
        raise RuntimeError("GPU unavailable")
    monkeypatch.setattr(SegmentationProxy, "analyze", failed)
    store._run("cache-key", {}, source)
    assert store._state["status"] == "error"
    assert store._result is None
    assert not (tmp_path / "analyses/cache-key.json").exists()


def test_pinned_gpu_model_mismatch_fails_before_result_retrieval():
    proxy = SegmentationProxy(None, "http://gpu-worker:8091")
    async def start(*args, **kwargs):
        return {"status": "complete", "progress": 1,
                "models": {"bottle": {"checkpoint_sha256": "a" * 64},
                           "liquid": {"checkpoint_sha256": "e" * 64}},
                "pipeline_sha256": "c" * 64, "sampling_mode": "every-source-frame-v2"}
    proxy.start = start
    with pytest.raises(ValueError, match="pinned"):
        asyncio.run(proxy.analyze("fixture", CONTRACT))


def test_large_result_compression_preserves_json_and_does_not_compress_ranged_media():
    from backend.app import MeasurementJSONCompression
    from fastapi import FastAPI
    from fastapi.responses import Response
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.add_middleware(MeasurementJSONCompression)
    content = {"samples": [{"t": n / 24, "polygon": [[.123456, .789012]] * 20} for n in range(300)]}
    @app.get("/api/segmentation/result")
    def masks():
        return content
    @app.get("/api/media")
    def media():
        return Response(b"x" * 9000, status_code=206, media_type="video/mp4",
                        headers={"Content-Range": "bytes 0-8999/18000"})
    client = TestClient(app)
    response = client.get("/api/segmentation/result", headers={"Accept-Encoding": "gzip"})
    assert response.headers["Content-Encoding"] == "gzip"
    assert response.json() == content
    assert int(response.headers["Content-Length"]) < len(response.content) / 5
    clip = client.get("/api/media", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-8999"})
    assert clip.status_code == 206 and "Content-Encoding" not in clip.headers
    assert clip.content == b"x" * 9000
