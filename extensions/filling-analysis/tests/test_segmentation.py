# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from backend.segmentation import SegmentationProxy


class Store:
    def __init__(self, tmp_path):
        self.lock = threading.RLock()
        self.settings = SimpleNamespace(data_dir=tmp_path)
        self.current = {"id": "vss:recording-a:abc", "sha256": "a" * 64, "duration": 10.,
                        "width": 1280, "height": 720, "fps": 24., "actual_start_time": "2025-01-01T00:00:00Z",
                        "stream_id": "recording-a"}

    def source(self):
        return dict(self.current)

    def media_path(self):
        return self.settings.data_dir / "vss" / (self.current["sha256"] + ".mp4")


def run(awaitable):
    return asyncio.run(awaitable)


def test_start_is_bound_to_actual_selected_recording(tmp_path):
    store = Store(tmp_path)
    seen = []

    def request(req):
        import json
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, json={"status": "running", "progress": 0,
                                       "source_id": body["source_id"], "source_sha256": body["source_sha256"],
                                       "stream_id":body["stream_id"], "source_clock_origin":body["source_clock_origin"]})

    proxy = SegmentationProxy(store, "http://gpu-worker:8091", httpx.MockTransport(request))
    result = run(proxy.start(store.current["id"]))
    assert result["status"] == "running"
    assert seen[0]["source_file"] == "vss/" + "a" * 64 + ".mp4"
    assert seen[0]["source_clock_origin"] == store.current["actual_start_time"]
    assert seen[0]["force"] is False


def test_stale_browser_source_never_submits_gpu_job(tmp_path):
    proxy = SegmentationProxy(Store(tmp_path), "http://gpu-worker:8091", httpx.MockTransport(lambda _: pytest.fail("must not call worker")))
    with pytest.raises(HTTPException) as caught:
        run(proxy.start("old-source"))
    assert caught.value.status_code == 409


def test_worker_result_for_another_video_is_rejected(tmp_path):
    store = Store(tmp_path)
    proxy = SegmentationProxy(store, "http://gpu-worker:8091", httpx.MockTransport(lambda _: httpx.Response(200,
        json={"source_id": store.current["id"], "source_sha256": "b" * 64})))
    with pytest.raises(HTTPException) as caught:
        run(proxy.result(store.current["id"]))
    assert caught.value.status_code == 502


def test_selection_change_during_request_is_rejected(tmp_path):
    store = Store(tmp_path)

    def request(_):
        response = dict(source_id=store.current["id"], source_sha256=store.current["sha256"], status="complete",
                        stream_id=store.current["stream_id"], source_clock_origin=store.current["actual_start_time"])
        store.current["id"] = "vss:recording-b:def"
        return httpx.Response(200, json=response)

    proxy = SegmentationProxy(store, "http://gpu-worker:8091", httpx.MockTransport(request))
    with pytest.raises(HTTPException) as caught:
        run(proxy.result(store.current["id"]))
    assert caught.value.status_code == 409


def test_worker_unavailable_is_explicit_and_does_not_fake_masks(tmp_path):
    store = Store(tmp_path)
    result = run(SegmentationProxy(store, None).status(store.current["id"]))
    assert result["status"] == "unavailable"
    assert "samples" not in result


def test_clock_change_with_same_uuid_and_bytes_is_rejected(tmp_path):
    store = Store(tmp_path)
    def request(_):
        response = dict(source_id=store.current["id"], source_sha256=store.current["sha256"], status="complete",
                        stream_id=store.current["stream_id"], source_clock_origin=store.current["actual_start_time"])
        store.current["actual_start_time"] = "2026-01-01T00:00:00Z"
        return httpx.Response(200, json=response)
    proxy = SegmentationProxy(store, "http://gpu-worker:8091", httpx.MockTransport(request))
    with pytest.raises(HTTPException) as caught:
        run(proxy.result(store.current["id"]))
    assert caught.value.status_code == 409
