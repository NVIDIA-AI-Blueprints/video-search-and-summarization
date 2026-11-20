# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The input preview can only expose fresh frames from the monitored capture."""
import io
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from agent_pipeline.contracts import StreamConfig
from agent_pipeline.ingest import create_app
from agent_pipeline.store import Store


@pytest.fixture
def preview_setup(tmp_path):
    store = Store(tmp_path / "ingest.sqlite")
    config = StreamConfig(rtsp_url="rtsp://camera/flowing",
                          question={"instructions": "Is there a traffic queue?"},
                          input_mode="video").model_dump()
    revision = store.put_config("stream", "traffic", config)
    buffer = io.BytesIO()
    Image.new("RGB", (48, 32), (40, 120, 80)).save(buffer, format="JPEG")
    capture = SimpleNamespace(uri=config["rtsp_url"], lock=threading.Lock(),
                              stop=threading.Event(), frame=buffer.getvalue(),
                              frame_time=time.monotonic() - 0.1, seq=42)
    app = create_app(store, start_workers=False)
    app.state.runtime.captures["traffic"] = capture

    def forbidden_capture(*_args):
        pytest.fail("A preview request must not create a capture")

    app.state.runtime.capture_factory = forbidden_capture
    with TestClient(app) as client:
        yield client, app.state.runtime, store, config, revision, capture
    store.db.close()


def test_preview_serves_exact_cached_jpeg_and_host_arrival_headers(preview_setup):
    client, runtime, store, config, revision, capture = preview_setup
    expected_captured = time.time() - (time.monotonic() - capture.frame_time)
    response = client.get(f"/v1/streams/traffic/preview?revision={revision}")
    assert response.status_code == 200
    assert response.content == capture.frame
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-sequence"] == "42"
    assert response.headers["x-stream-revision"] == str(revision)
    assert float(response.headers["x-frame-captured-at"]) == pytest.approx(expected_captured, abs=0.05)
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (48, 32)
    assert runtime.captures["traffic"] is capture
    assert store.rows("SELECT count(*) AS n FROM outbox")[0]["n"] == 0


@pytest.mark.parametrize("case,status", [
    ("unknown", 404), ("disabled", 409), ("revision", 409),
    ("uri_changed", 409), ("stopped", 409), ("missing_capture", 503),
    ("missing_frame", 503), ("stale", 503), ("future", 503),
])
def test_preview_rejects_unavailable_or_different_inputs(preview_setup, case, status):
    client, runtime, store, config, revision, capture = preview_setup
    stream_id = "traffic"
    if case == "unknown":
        stream_id = "unknown"
    elif case == "disabled":
        store.put_config("stream", stream_id, {**config, "enabled": False})
    elif case == "revision":
        revision += 1
    elif case == "uri_changed":
        revision = store.put_config("stream", stream_id, {**config, "rtsp_url": "rtsp://camera/other"})
    elif case == "stopped":
        capture.stop.set()
    elif case == "missing_capture":
        runtime.captures.clear()
    elif case == "missing_frame":
        capture.frame = None
    elif case == "stale":
        capture.frame_time = time.monotonic() - 2.1
    elif case == "future":
        capture.frame_time = time.monotonic() + 10
    response = client.get(f"/v1/streams/{stream_id}/preview?revision={revision}")
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "x-frame-sequence" not in response.headers


@pytest.mark.parametrize("change", ["capture", "configuration", "disabled", "deleted"])
def test_preview_rechecks_source_after_waiting_for_frame_lock(preview_setup, change):
    client, runtime, store, config, revision, capture = preview_setup

    class ChangeOnUnlock:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            if change == "capture":
                runtime.captures["traffic"] = SimpleNamespace(uri=capture.uri)
            elif change == "configuration":
                store.put_config("stream", "traffic", {**config, "rtsp_url": "rtsp://camera/other"})
            elif change == "disabled":
                store.put_config("stream", "traffic", {**config, "enabled": False})
            elif change == "deleted":
                with store.transaction() as db:
                    db.execute("DELETE FROM config WHERE kind='stream' AND id='traffic'")

    capture.lock = ChangeOnUnlock()
    # Omit the optional revision to check that server-side race protection works
    # independently of the browser's requested revision.
    response = client.get("/v1/streams/traffic/preview")
    assert response.status_code == (404 if change == "deleted" else 409)
    assert response.headers["cache-control"] == "no-store"
