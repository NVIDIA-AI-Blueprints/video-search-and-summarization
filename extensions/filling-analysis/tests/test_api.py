# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""API contracts use controlled fixtures; actual-video checks run separately."""

import hashlib
import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.app as api
from backend.tools import answer_question, create_mcp_server


def measured_result(path):
    return {
        "version": "test-v1", "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "algorithm": "test-fixture", "sample_fps": 1, "runtime_seconds": 0.01,
        "samples": [{"t": t, "scene_id": "shot-a", "phase": "rising", "bottles": [{
            "id": "a-1", "label": "Bottle A", "box": [0.1, 0.1, 0.2, 0.7],
            "level": level, "confidence": 0.7, "surface": [], "mask": [],
        }]} for t, level in [(0, 0.2), (1, None), (2, 0.8), (3, 0.85), (4, 0.9)]],
        "events": [], "bottles": [{"id": "a-1", "label": "Bottle A", "color": "#00ff00", "scene_id": "shot-a"}],
        "quality": {"kind": "synthetic-unit-test-fixture"},
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    media = tmp_path / "fixture.mp4"
    media.write_bytes(bytes(range(128)))
    vision = tmp_path / "vision.py"
    vision.write_text("# fixture algorithm v1\n")
    calibration = tmp_path / "calibration.json"
    calibration.write_text('{"fixture":1}')
    monkeypatch.setattr(api, "_probe_media", lambda p: {"width": 200, "height": 100, "fps": 10, "duration": 10.0})
    settings = api.Settings(media_path=media, data_dir=tmp_path / "data", vision_path=vision,
                            calibration_path=calibration, frontend_dir=tmp_path / "dist", enable_mcp=False)
    calls = []

    def analyzer(path, output_path=None, progress_callback=None):
        calls.append(path)
        if progress_callback:
            progress_callback(0.5)
        return measured_result(media)

    return settings, analyzer, calls


def finish(client):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        state = client.get("/api/analysis").json()
        if state["status"] != "running":
            return state
        time.sleep(0.01)
    raise AssertionError("Fixture analysis did not complete")


def test_metadata_is_source_derived_and_unreviewed_file_has_no_chapters(setup):
    settings, analyzer, _ = setup
    with TestClient(api.create_app(settings, analyzer)) as client:
        source = client.get("/api/source").json()
        assert source["sha256"] == hashlib.sha256(settings.media_path.read_bytes()).hexdigest()
        assert source["id"] == "sha256:" + source["sha256"]
        assert source["chapters"] == []
        assert source["mode"] == "recorded"


@pytest.mark.parametrize("header,code,body", [
    ("bytes=2-5", 206, bytes(range(2, 6))),
    ("bytes=-3", 206, bytes(range(125, 128))),
    ("bytes=125-", 206, bytes(range(125, 128))),
    ("bytes=126-999", 206, bytes(range(126, 128))),
    ("bytes=128-", 416, b""), ("bytes=5-2", 416, b""),
    ("bytes=-0", 416, b""), ("bytes=0-2,4-6", 416, b""),
])
def test_media_byte_ranges(setup, header, code, body):
    settings, analyzer, _ = setup
    with TestClient(api.create_app(settings, analyzer)) as client:
        response = client.get("/api/media", headers={"Range": header})
        assert response.status_code == code
        assert response.content == body
        assert response.headers["accept-ranges"] == "bytes"
        if code == 416:
            assert response.headers["content-range"] == "bytes */128"
        else:
            assert int(response.headers["content-length"]) == len(body)


def test_media_head_and_if_range(setup):
    settings, analyzer, _ = setup
    with TestClient(api.create_app(settings, analyzer)) as client:
        assert client.head("/api/media").headers["content-length"] == "128"
        response = client.get("/api/media", headers={"Range": "bytes=0-1", "If-Range": '"different"'})
        assert response.status_code == 200
        assert len(response.content) == 128


def test_matching_cache_force_and_input_invalidation(setup):
    settings, analyzer, calls = setup
    with TestClient(api.create_app(settings, analyzer)) as client:
        assert client.get("/api/analysis/result").status_code == 409
        client.post("/api/analysis", json={})
        assert finish(client)["status"] == "complete"
        assert len(calls) == 1
        assert client.post("/api/analysis", json={}).json()["cached"] is True
        client.post("/api/analysis", json={"force": True})
        assert finish(client)["status"] == "complete"
        assert len(calls) == 2
        settings.calibration_path.write_text('{"fixture":2}')
        assert client.get("/api/analysis/result").status_code == 409
        client.post("/api/analysis", json={})
        assert finish(client)["status"] == "complete"
        assert len(calls) == 3
        settings.vision_path.write_text("# fixture algorithm v2\n")
        client.post("/api/analysis", json={})
        assert finish(client)["status"] == "complete"
        assert len(calls) == 4
        settings.media_path.write_bytes(b"changed source bytes")
        client.post("/api/analysis", json={})
        assert finish(client)["status"] == "complete"
        assert len(calls) == 5
    with TestClient(api.create_app(settings, analyzer)) as client:
        assert client.get("/api/analysis").json()["cached"] is True
        assert len(calls) == 5


def test_failed_force_does_not_hide_failure_with_previous_cache(setup):
    settings, analyzer, calls = setup
    fail = [False]

    def sometimes_invalid(*args, **kwargs):
        result = analyzer(*args, **kwargs)
        if fail[0]:
            result["samples"][0]["bottles"][0]["level"] = float("nan")
        return result

    with TestClient(api.create_app(settings, sometimes_invalid)) as client:
        client.post("/api/analysis", json={})
        assert finish(client)["status"] == "complete"
        fail[0] = True
        client.post("/api/analysis", json={"force": True})
        assert finish(client)["status"] == "error"
        assert client.get("/api/analysis/result").status_code == 409


def test_non_object_cache_is_ignored_and_recomputed(setup):
    settings, analyzer, calls = setup
    app = api.create_app(settings, analyzer)
    key = app.state.analysis.context()[0]
    cache = settings.data_dir / "analyses" / f"{key}.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("[]")
    with TestClient(app) as client:
        assert client.get("/api/analysis").json()["status"] == "idle"
        client.post("/api/analysis", json={})
        assert finish(client)["status"] == "complete"
        assert len(calls) == 1


def test_force_during_active_job_is_explicit_conflict(setup):
    settings, analyzer, _ = setup
    release = threading.Event()

    def blocked(*args, **kwargs):
        release.wait(2)
        return analyzer(*args, **kwargs)

    with TestClient(api.create_app(settings, blocked)) as client:
        client.post("/api/analysis", json={})
        assert client.post("/api/analysis", json={"force": True}).status_code == 409
        release.set()
        assert finish(client)["status"] == "complete"


@pytest.mark.parametrize("url", ["/api/snapshot?t=-1", "/api/snapshot?t=10", "/api/snapshot?t=nan",
                                      "/api/evidence?start=1&end=11", "/api/evidence?start=3&end=2"])
def test_invalid_evidence_bounds_rejected_before_ffmpeg(setup, url):
    settings, analyzer, _ = setup
    with TestClient(api.create_app(settings, analyzer)) as client:
        assert client.get(url).status_code == 422


def test_export_preserves_null_measurements_and_query_provenance(setup):
    settings, analyzer, _ = setup
    with TestClient(api.create_app(settings, analyzer)) as client:
        client.post("/api/analysis", json={})
        assert finish(client)["status"] == "complete"
        csv = client.get("/api/export.csv")
        assert csv.status_code == 200
        assert "visible-height" in csv.text
        assert ",Bottle A,rising,,0.7," in csv.text
        query = client.post("/api/query", json={"question": "highest height", "scene_id": "shot-a"}).json()
        assert query["method"] == "measurement-tools"
        assert query["evidence"][0]["t"] == 4
        assert client.post("/api/query", json={"question": "What volume leaked?"}).json()["supported"] is False


def test_reference_first_observed_and_unreadable_gap(setup):
    settings, _, _ = setup
    result = measured_result(settings.media_path)
    answer = answer_question(result, "which reached the reference first?", 0.75, "shot-a")
    assert "Bottle A at 2.00s" in answer["answer"]
    assert "unreadable gap; crossing unknown" in answer["answer"]
    assert answer["first_observed"]["unreadable_gap_before"] is True
    assert answer["first_observed"]["exact_crossing_time_known"] is False
    result["samples"][0]["bottles"][0]["level"] = 0.8
    answer = answer_question(result, "which reached the reference first?", 0.75, "shot-a")
    assert "already at or above at its first readable sample; crossing unknown" in answer["answer"]
    assert answer["first_observed"]["already_qualifying_at_first_readable_sample"] is True


def test_reference_overshoot_counts_before_later_exact_threshold(setup):
    settings, _, _ = setup
    result = measured_result(settings.media_path)
    result["sample_fps"] = 5
    result["samples"] = [
        {"t": t, "scene_id": "shot-a", "phase": "rising", "bottles": [
            {"id": "front", "label": "Front", "level": front, "confidence": 0.9},
            {"id": "rear", "label": "Rear", "level": rear, "confidence": 0.9},
        ]}
        for t, front, rear in [(25.2, 0.73, 0.72), (25.4, 0.757, 0.74), (25.6, 0.79, 0.75)]
    ]
    answer = answer_question(result, "which reached the reference first?", 0.75, "shot-a")
    assert answer["reference_criterion"]["operator"] == ">="
    assert answer["first_observed"]["bottle_id"] == "front"
    assert answer["first_observed"]["t"] == 25.4
    assert answer["first_observed"]["level"] == 0.757
    assert answer["first_observed"]["exact_crossing_time_known"] is False
    assert answer["first_observed"]["previous_readable_observation"] == {"t": 25.2, "level": 0.73}
    assert [(r["bottle_id"], r["t"], r["level"]) for r in answer["ordered_reference_observations"]] == [
        ("front", 25.4, 0.757), ("rear", 25.6, 0.75)]
    assert "Front at 25.40s; Rear at 25.60s" in answer["answer"]


def test_comparisons_cannot_cross_camera_calibrations(setup):
    settings, _, _ = setup
    result = measured_result(settings.media_path)
    result["samples"][-1]["scene_id"] = "shot-b"
    for question in ["fastest bottle", "highest height", "which reached the reference first?"]:
        assert answer_question(result, question, 0.8)["supported"] is False
    assert "measurement windows differ" in answer_question(result, "summary")["answer"]


def test_rate_does_not_bridge_null_gap(setup):
    settings, _, _ = setup
    result = measured_result(settings.media_path)
    result["samples"] = result["samples"][:3]
    answer = answer_question(result, "fastest bottle", scene_id="shot-a")
    assert "not enough" in answer["answer"]


def test_vss_unavailable_remains_unavailable_and_public_origin_is_separate(setup):
    settings, analyzer, _ = setup
    settings = api.Settings(**{**settings.__dict__, "vss_base_url": "http://internal:7777", "vss_public_url": "http://public:7777"})

    def unavailable(request):
        assert request.url.host == "internal"
        raise httpx.ConnectError("fixture: stopped", request=request)

    with TestClient(api.create_app(settings, analyzer, httpx.MockTransport(unavailable))) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert health["vss"]["available"] is False
        assert health["vss"]["base_url"] == "http://public:7777"
        assert client.get("/api/vss/sources").json()["sources"] == []


def test_vss_actual_identifiers_and_evidence_boundary_are_preserved(setup):
    settings, analyzer, _ = setup
    settings = api.Settings(**{**settings.__dict__, "vss_base_url": "http://vss:7777"})
    stream_id = "631962f2-5e96-4394-9eba-9fc0febf7306"  # Explicit test fixture, not a deployed-source claim.
    requests = []

    def upstream(request):
        requests.append(request)
        if request.url.path.endswith("sensor/list"):
            return httpx.Response(200, json=[{"sensorId": stream_id, "name": "fixture-source", "password": "do-not-return"}])
        if request.url.path.endswith("/streams"):
            return httpx.Response(200, json=[{"streamId": stream_id, "isMain": True}])
        assert request.url.path == f"/vst/api/v1/storage/file/{stream_id}/url"
        assert request.url.params["container"] == "mp4"
        assert request.url.params["disableAudio"] == "true"
        return httpx.Response(200, json={"videoUrl": "http://evidence/actual.mp4", "startTime": "2026-09-21T12:00:00.100Z", "streamId": stream_id})

    with TestClient(api.create_app(settings, analyzer, httpx.MockTransport(upstream))) as client:
        source = client.get("/api/vss/sources").json()["sources"][0]
        assert source["sensorId"] == source["stream_id"] == stream_id
        assert source["name"] == "fixture-source"
        assert "password" not in source
        parameters = {"stream_id": stream_id, "start_time": "2026-09-21T12:00:00Z", "end_time": "2026-09-21T12:00:06Z"}
        evidence = client.get("/api/vss/evidence", params=parameters).json()
        assert evidence["actual"]["startTime"] == "2026-09-21T12:00:00.100Z"
        assert evidence["requested"]["startTime"] == "2026-09-21T12:00:00Z"
        assert evidence["actual"]["videoUrl"] == "http://evidence/actual.mp4"
        count = len(requests)
        assert client.get("/api/vss/evidence", params={**parameters, "stream_id": "guess"}).status_code == 422
        assert client.get("/api/vss/evidence", params={**parameters, "end_time": "2026-09-21T11:59:59Z"}).status_code == 422
        assert len(requests) == count


def test_frontend_fallback_does_not_swallow_unknown_api(setup):
    settings, analyzer, _ = setup
    settings.frontend_dir.mkdir()
    (settings.frontend_dir / "index.html").write_text("<main>Test shell</main>")
    with TestClient(api.create_app(settings, analyzer)) as client:
        assert client.get("/review").status_code == 200
        assert client.get("/api/unknown").status_code == 404
        assert client.get("/assets/missing.js").status_code == 404


def test_real_mcp_protocol_session_and_source_tool(setup):
    settings, analyzer, _ = setup
    settings = api.Settings(**{**settings.__dict__, "enable_mcp": True})
    with TestClient(api.create_app(settings, analyzer)) as client:
        headers = {"Accept": "application/json, text/event-stream"}
        initialized = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "api-tests", "version": "1"}}})
        assert initialized.status_code == 200
        headers["Mcp-Session-Id"] = initialized.headers["mcp-session-id"]
        headers["MCP-Protocol-Version"] = "2025-06-18"
        acknowledged = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert acknowledged.status_code == 202
        listed = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert {tool["name"] for tool in listed.json()["result"]["tools"]} == {
            "source_metadata", "measured_samples", "query_measurements", "vss_evidence", "vss_sources",
            "select_vss_source", "analyze_fill_progress", "fill_analysis_status", "measurement_evidence"}
        schema = next(tool["inputSchema"] for tool in listed.json()["result"]["tools"] if tool["name"] == "query_measurements")
        assert "reference_level" not in schema["properties"]
        percent = schema["properties"]["reference_percent"]
        numeric = next(value for value in percent["anyOf"] if value.get("type") == "number")
        assert numeric["minimum"] == 0 and numeric["maximum"] == 100
        assert "75" in percent["description"]
        assert schema["additionalProperties"] is False
        called = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "source_metadata", "arguments": {}}})
        assert called.status_code == 200
        assert called.json()["result"].get("isError", False) is False


def test_mcp_reference_percent_converts_explicitly_and_rejects_old_argument(setup):
    settings, analyzer, _ = setup
    settings = api.Settings(**{**settings.__dict__, "enable_mcp": True})
    with TestClient(api.create_app(settings, analyzer)) as client:
        client.post("/api/analysis", json={})
        assert finish(client)["status"] == "complete"
        headers = {"Accept": "application/json, text/event-stream"}
        init = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "percent-tests", "version": "1"}}})
        headers.update({"Mcp-Session-Id": init.headers["mcp-session-id"], "MCP-Protocol-Version": "2025-06-18"})
        client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        base = {"question": "which reached the reference first?", "scene_id": "shot-a"}

        def query(arguments):
            response = client.post("/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                   "params": {"name": "query_measurements", "arguments": arguments}})
            assert response.status_code == 200
            return response.json()["result"]

        result = query({**base, "reference_percent": 75})
        assert result.get("isError", False) is False
        answer = result.get("structuredContent") or json.loads(next(item["text"] for item in result["content"] if item["type"] == "text"))
        assert answer["reference_criterion"]["reference_level"] == 0.75
        assert answer["first_observed"]["t"] == 2
        fractional_percent = query({**base, "reference_percent": 0.75})
        fractional_answer = fractional_percent.get("structuredContent") or json.loads(next(item["text"] for item in fractional_percent["content"] if item["type"] == "text"))
        assert fractional_answer["reference_criterion"]["reference_level"] == 0.0075  # Always percent; no magnitude-based guessing.
        assert query({**base, "reference_percent": 101})["isError"] is True
        assert query({**base, "reference_percent": -1})["isError"] is True
        assert query({**base, "reference_level": 0.75})["isError"] is True
        assert query({**base, "reference_level": 75})["isError"] is True
        # HTTP deliberately retains its documented fraction interface.
        assert client.post("/api/query", json={**base, "reference_level": 0.75}).json()["first_observed"]["t"] == 2


@pytest.fixture
def atomic_query_fixture(setup):
    settings, _, _ = setup
    result = measured_result(settings.media_path)
    result["samples"] = result["samples"][:3]
    stream_id = "631962f2-5e96-4394-9eba-9fc0febf7306"
    clock = "2026-09-21T12:00:00Z"
    result["provenance"] = {"stream_id": stream_id, "actual_start_time": clock}
    source = {"id": "vss:" + stream_id + ":" + result["source_sha256"], "stream_id": stream_id,
              "sha256": result["source_sha256"], "actual_start_time": clock, "duration": 2.5}
    clip = {"available": True, "source_id": source["id"], "stream_id": stream_id, "source_clock_origin": clock,
            "requested": {"streamId": stream_id, "startTime": "2026-09-21T12:00:01Z", "endTime": "2026-09-21T12:00:02.5Z"},
            "actual": {"streamId": stream_id, "startTime": "2026-09-21T12:00:00.5Z", "videoUrl": "http://internal/test.mp4"},
            "public_url": "https://vss.example/test.mp4", "local_clip_url": "/filling/api/evidence?start=1&end=2.5"}
    calls = []

    def run(provider, include_evidence=True):
        server = create_mcp_server(lambda: source, lambda: result, measurement_evidence_provider=provider)
        tool = server._tool_manager.get_tool("query_measurements")
        return asyncio.run(tool.run({"question": "which reached the reference first?", "reference_percent": 75,
                                     "scene_id": "shot-a", "include_evidence": include_evidence}))

    return result, source, clip, calls, run


def test_atomic_mcp_query_fetches_bounded_actual_evidence_and_can_opt_out(atomic_query_fixture):
    _, _, clip, calls, run = atomic_query_fixture

    async def evidence(start, end):
        calls.append((start, end))
        return clip

    answer = run(evidence)
    assert calls == [(1.0, 2.5)]  # One second before first observation, clipped at source end.
    assert answer["first_observed"]["t"] == 2
    assert answer["video_evidence"] == clip
    assert answer["video_evidence"]["actual"]["startTime"] != answer["video_evidence"]["requested"]["startTime"]
    assert "[Open clip](https://vss.example/test.mp4)" in answer["answer"]
    without = run(evidence, include_evidence=False)
    assert "video_evidence" not in without
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["wrong-source", "wrong-upstream-stream", "unavailable", "exception"])
def test_atomic_mcp_query_retains_measurements_without_unverified_links(atomic_query_fixture, failure):
    _, _, clip, _, run = atomic_query_fixture

    async def evidence(start, end):
        if failure == "wrong-source":
            return {**clip, "source_id": "different-selection"}
        if failure == "wrong-upstream-stream":
            return {**clip, "actual": {**clip["actual"], "streamId": "c5561d05-46bd-4cf7-9cbe-9ab5e83cc012"}}
        if failure == "unavailable":
            return {"available": False, "reason": "Fixture VIOS is offline"}
        raise RuntimeError("Fixture failed retrieval")

    answer = run(evidence)
    assert answer["supported"] is True and answer["first_observed"]["t"] == 2
    assert answer["video_evidence"]["available"] is False
    assert answer["video_evidence"]["reason"]
    assert "public_url" not in answer["video_evidence"] and "local_clip_url" not in answer["video_evidence"]
    assert "Open clip" not in answer["answer"]


def test_atomic_mcp_query_rejects_changed_source_before_fetch(atomic_query_fixture):
    _, source, _, calls, run = atomic_query_fixture
    source["stream_id"] = "c5561d05-46bd-4cf7-9cbe-9ab5e83cc012"

    async def evidence(start, end):
        calls.append((start, end))
        raise AssertionError("Must not fetch evidence for a changed source")

    answer = run(evidence)
    assert not calls
    assert answer["first_observed"]["t"] == 2
    assert answer["video_evidence"]["available"] is False


@pytest.fixture
def native_setup(setup, monkeypatch):
    settings, _, _ = setup
    settings = api.Settings(**{**settings.__dict__, "source_mode": "vss", "vss_base_url": "http://vss:7777",
                               "vss_public_url": "http://public:7777", "vss_media_origin": "http://vst-ingress:30888", "public_prefix": "/filling"})
    data = settings.media_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(api, "REVIEWED_SOURCE_SHA256", digest)
    ids = ["631962f2-5e96-4394-9eba-9fc0febf7306", "c5561d05-46bd-4cf7-9cbe-9ab5e83cc012"]
    requests, calls = [], []

    def upstream(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("sensor/list"):
            return httpx.Response(200, json=[{"sensorId": ident, "name": "fixture-" + str(i), "state": "online"} for i, ident in enumerate(ids)])
        if path.endswith("/streams"):
            ident = path.split("/")[-2]
            return httpx.Response(200, json=[{"streamId": ident, "isMain": True}])
        if path.endswith("/timelines"):
            return httpx.Response(200, json={ident: [{"startTime": f"2026-09-21T12:0{i}:00Z", "endTime": f"2026-09-21T12:0{i}:10Z"}] for i, ident in enumerate(ids)})
        if "/storage/file/" in path and path.endswith("/list"):
            return httpx.Response(404)
        if path.endswith("/url"):
            ident = path.split("/")[-2]
            return httpx.Response(200, json={"videoUrl": "http://vst-ingress:30888/vst/storage/fixture.mp4", "startTime": request.url.params["startTime"], "streamId": ident})
        if path == "/vst/storage/fixture.mp4":
            return httpx.Response(200, content=data)
        raise AssertionError("Unexpected mocked request " + path)

    def verifier(candidate, reference, expected):
        assert reference == settings.media_path
        assert candidate != reference
        assert candidate.read_bytes() == data
        return {"verified": True, "candidate_sha256": digest, "reference_sha256": expected, "offset_seconds": 0, "method": "test-only-fixture"}

    def analyzer(path, output_path=None, progress_callback=None, approved_reference=None):
        assert Path(path) != settings.media_path
        assert approved_reference["verified"] is True
        calls.append(path)
        return measured_result(Path(path))

    return settings, analyzer, verifier, httpx.MockTransport(upstream), ids, requests, calls


def test_native_mode_never_falls_back_to_local_file(native_setup):
    settings, analyzer, verifier, transport, *_ = native_setup
    with TestClient(api.create_app(settings, analyzer, transport, verifier)) as client:
        state = client.get("/api/analysis").json()
        assert state["status"] == "idle" and state["requires_selection"] is True
        for path in ("/api/source", "/api/media", "/api/analysis/result"):
            assert client.get(path).status_code == 409
        assert client.post("/api/analysis", json={}).status_code == 409


def test_native_multiple_reviewed_profiles_are_source_bound_and_switchable(native_setup, monkeypatch, tmp_path):
    import backend.profiles as profiles

    settings, analyzer, _, transport, ids, *_ = native_setup
    original = settings.media_path.read_bytes()
    balanced = b"different-reviewed-full-recording-fixture"
    balanced_digest = hashlib.sha256(balanced).hexdigest()
    monkeypatch.setattr(api, "BALANCED_SHA256", balanced_digest)
    monkeypatch.setattr(profiles, "BALANCED_SHA256", balanced_digest)
    additional = tmp_path / "balanced.mp4"
    additional.write_bytes(balanced)
    settings = api.Settings(**{**settings.__dict__, "additional_media_path": additional})
    current = [original]

    def upstream(request):
        if request.url.path.endswith("/url"):
            current[0] = balanced if ids[1] in request.url.path else original
        if request.url.path == "/vst/storage/fixture.mp4":
            return httpx.Response(200, content=current[0])
        return transport.handler(request)

    def verifier(candidate, reference, expected):
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        return {"version": "video-identity-v2", "verified": digest == expected, "candidate_sha256": digest,
                "reference_sha256": expected, "offset_seconds": 0, "method": "file-sha256", "time_alignment": "exact-zero-offset"}

    app = api.create_app(settings, analyzer, httpx.MockTransport(upstream), verifier)
    with TestClient(app) as client:
        first = client.post("/api/vss/select", json={"stream_id": ids[0]}).json()
        assert first["profile_id"] == "original-filling-shots"
        first_key = app.state.analysis.context()[0]
        response = client.post("/api/vss/select", json={"stream_id": ids[1]})
        assert response.status_code == 200, response.text
        second = response.json()
        assert second["analysis_kind"] == "bottle-cycles"
        assert second["calibration_reference_sha256"] == balanced_digest
        assert second["chapters"] == []
        assert second["stream_id"] == ids[1]
        second_key, fingerprint, _ = app.state.analysis.context()
        assert first_key != second_key
        assert fingerprint["profile_id"] == "balanced-single-station"
        assert fingerprint["profiles_sha256"]
        returned = client.post("/api/vss/select", json={"stream_id": ids[0]}).json()
        assert returned["profile_id"] == "original-filling-shots"
        assert app.state.analysis.context()[0] == first_key


def test_native_selection_analysis_cache_provenance_and_evidence(native_setup):
    settings, analyzer, verifier, transport, ids, requests, calls = native_setup
    app = api.create_app(settings, analyzer, transport, verifier)
    with TestClient(app) as client:
        response = client.post("/api/vss/select", json={"stream_id": ids[0]})
        assert response.status_code == 200, response.text
        source = response.json()
        assert source["origin"] == "vss-vios"
        assert source["stream_id"] == ids[0]
        assert source["media_url"].startswith("/filling/api/media?source_id=vss%3A")
        assert source["actual_start_time"] == "2026-09-21T12:00:00Z"
        assert client.get("/api/analysis").json()["source_id"] == source["id"]
        assert client.get("/api/media?source_id=stale").status_code == 409
        assert client.get("/api/evidence?start=1&end=2&source_id=stale").status_code == 409
        assert client.get("/api/snapshot?t=1&source_id=stale").status_code == 409
        assert client.get("/api/media", headers={"Range": "bytes=0-1"}).content == bytes(range(2))
        client.post("/api/analysis", json={})
        assert finish(client)["status"] == "complete"
        first = client.get("/api/analysis/result").json()
        assert first["provenance"]["stream_id"] == ids[0]
        query = client.post("/api/query", json={"question": "highest", "scene_id": "shot-a"}).json()
        assert query["evidence"][0]["stream_id"] == ids[0]
        assert query["evidence"][0]["recorded_at"] == "2026-09-21T12:00:04+00:00"
        assert ids[0] in client.get("/api/export.csv").text
        evidence = client.get("/api/measurement-evidence?start=2&end=4").json()
        assert evidence["requested"]["startTime"] == "2026-09-21T12:00:02+00:00"
        assert evidence["actual"]["videoUrl"].startswith("http://vst-ingress:30888/")
        assert evidence["public_url"] == "http://public:7777/vst/storage/fixture.mp4"
        assert evidence["local_clip_url"].startswith("/filling/")
        assert "source_id=vss%3A" in evidence["local_clip_url"]
        assert client.post("/api/vss/select", json={"sensor_id": ids[1]}).status_code == 200
        assert client.get("/api/analysis/result").status_code == 409
        client.post("/api/analysis", json={})
        assert finish(client)["status"] == "complete"
        second = client.get("/api/analysis/result").json()
        assert first["source_sha256"] == second["source_sha256"]
        assert first["provenance"]["cache_key"] != second["provenance"]["cache_key"]
        assert second["provenance"]["actual_start_time"] == "2026-09-21T12:01:00Z"
        assert len(calls) == 2
    # Restart preserves only the internally materialized/verified source binding.
    with TestClient(api.create_app(settings, analyzer, transport, verifier)) as client:
        assert client.get("/api/source").json()["stream_id"] == ids[1]
        assert client.get("/api/analysis/result").json()["provenance"]["stream_id"] == ids[1]


def test_native_selection_rejects_unverified_or_caller_proof(native_setup):
    settings, analyzer, _, transport, ids, *_ = native_setup
    verifier = lambda *args: {"verified": False, "offset_seconds": None, "reason": "different frames"}
    with TestClient(api.create_app(settings, analyzer, transport, verifier)) as client:
        response = client.post("/api/vss/select", json={"stream_id": ids[0]})
        assert response.status_code == 422
        assert "different frames" in response.text
        assert client.get("/api/source").status_code == 409
        assert client.post("/api/vss/select", json={"stream_id": ids[0], "identity_proof": {"verified": True}}).status_code == 422
        assert client.post("/api/vss/select", json={"stream_id": "invented"}).status_code == 422


def test_native_selection_and_analysis_are_serialized(native_setup):
    settings, _, verifier, transport, ids, *_ = native_setup
    entered, release = threading.Event(), threading.Event()

    def analyzer(path, **kwargs):
        entered.set()
        release.wait(3)
        return measured_result(Path(path))

    app = api.create_app(settings, analyzer, transport, verifier)
    with TestClient(app) as client:
        assert client.post("/api/vss/select", json={"stream_id": ids[0]}).status_code == 200
        client.post("/api/analysis", json={})
        assert entered.wait(1)
        assert client.post("/api/vss/select", json={"stream_id": ids[1]}).status_code == 409
        release.set()
        assert finish(client)["status"] == "complete"
        app.state.analysis.select_begin()
        assert client.get("/api/analysis/result").status_code == 409
        assert client.post("/api/analysis", json={}).status_code == 409
        app.state.analysis.selecting = False


def test_native_uploaded_file_uses_actual_file_id_without_clip_remux(native_setup):
    settings, analyzer, verifier, transport, ids, _, _ = native_setup
    file_id = "c2da53c2-5369-47e4-a7cd-023b98d8df66"  # Synthetic API fixture.
    requested = []

    def upstream(request):
        requested.append(request.url)
        if request.url.path.endswith("/storage/file/" + ids[0] + "/list"):
            return httpx.Response(200, json={ids[0]: [{"metadata": {"id": file_id, "sensorId": ids[0], "timestamp": int(datetime(2026, 9, 21, 12, tzinfo=timezone.utc).timestamp() * 1000)}}]})
        if request.url.path == "/vst/api/v1/storage/file":
            assert dict(request.url.params) == {"id": file_id}
            return httpx.Response(200, content=settings.media_path.read_bytes())
        assert not request.url.path.endswith("/url"), "Full-file selection must not request remuxed clip export"
        return transport.handle_request(request)

    with TestClient(api.create_app(settings, analyzer, httpx.MockTransport(upstream), verifier)) as client:
        response = client.post("/api/vss/select", json={"stream_id": ids[0]})
        assert response.status_code == 200, response.text
        source = response.json()
        assert source["file_id"] == file_id
        assert source["retrieval"]["method"] == "vios-original-file-by-id"
        assert source["actual_start_time"] == "2026-09-21T12:00:00Z"
        client.post("/api/analysis", json={})
        assert finish(client)["status"] == "complete"
        assert client.get("/api/analysis/result").json()["provenance"]["file_id"] == file_id


@pytest.mark.parametrize("wrong_sensor,offset_ms,status", [(True, 0, 502), (False, 1000, 422)])
def test_native_full_file_metadata_requires_same_sensor_and_clock(native_setup, wrong_sensor, offset_ms, status):
    settings, analyzer, verifier, transport, ids, *_ = native_setup

    def upstream(request):
        if request.url.path.endswith("/storage/file/" + ids[0] + "/list"):
            return httpx.Response(200, json={ids[0]: [{"metadata": {"id": "c2da53c2-5369-47e4-a7cd-023b98d8df66",
                "sensorId": ids[1] if wrong_sensor else ids[0], "timestamp": int(datetime(2026, 9, 21, 12, tzinfo=timezone.utc).timestamp() * 1000) + offset_ms}}]})
        return transport.handle_request(request)

    with TestClient(api.create_app(settings, analyzer, httpx.MockTransport(upstream), verifier)) as client:
        assert client.post("/api/vss/select", json={"stream_id": ids[0]}).status_code == status
        assert client.get("/api/source").status_code == 409


def test_expected_source_guard_rejects_stale_analysis_before_start(setup):
    settings, analyzer, calls = setup
    with TestClient(api.create_app(settings, analyzer)) as client:
        stale = client.post("/api/analysis", json={"source_id": "stale-recording"})
        assert stale.status_code == 409
        assert calls == []
        source_id = client.get("/api/source").json()["id"]
        accepted = client.post("/api/analysis", json={"source_id": source_id})
        assert accepted.status_code == 200
        assert finish(client)["status"] == "complete"
        assert len(calls) == 1


def test_expected_source_guard_rejects_stale_query_and_evidence(setup):
    settings, analyzer, _ = setup
    with TestClient(api.create_app(settings, analyzer)) as client:
        client.post("/api/analysis", json={})
        assert finish(client)["status"] == "complete"
        stale = client.post("/api/query", json={"question": "summary", "source_id": "stale-recording"})
        assert stale.status_code == 409
        source_id = client.get("/api/source").json()["id"]
        accepted = client.post("/api/query", json={"question": "summary", "source_id": source_id})
        assert accepted.status_code == 200
        stale_clip = client.get("/api/measurement-evidence", params={"start": 0, "end": 1, "source_id": "stale-recording"})
        assert stale_clip.status_code == 409
        assert "source_id" in stale_clip.json()["detail"]
