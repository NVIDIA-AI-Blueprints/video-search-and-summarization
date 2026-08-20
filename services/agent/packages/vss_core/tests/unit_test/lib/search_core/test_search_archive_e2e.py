# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Hermetic end-to-end coverage for search_core and `vss search run`."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from typing import TYPE_CHECKING
from typing import Any
from urllib.parse import quote

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


_STREAM_ID = "11111111-2222-3333-4444-555555555555"
_START_TIME = "2026-01-02T03:04:05.000Z"
_END_TIME = "2026-01-02T03:04:11Z"


@dataclass(frozen=True)
class _RecordedRequest:
    method: str
    raw_path: str
    path: str
    body: Any
    headers: dict[str, str]


class _MockSearchServices:
    """One lightweight HTTP server that impersonates ES, embed, and VST surfaces."""

    def __init__(self) -> None:
        self._requests: list[_RecordedRequest] = []
        self._lock = threading.Lock()
        self.search_response = _embed_search_response()
        self.vlm_models_status = 200
        handler = self._handler_class()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def external_vst_url(self) -> str:
        return "http://vst.example.test"

    @property
    def requests(self) -> list[_RecordedRequest]:
        with self._lock:
            return list(self._requests)

    def requests_for(self, path: str) -> list[_RecordedRequest]:
        return [request for request in self.requests if request.path == path]

    def requests_ending_with(self, suffix: str) -> list[_RecordedRequest]:
        return [request for request in self.requests if request.path.endswith(suffix)]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parent._handle(self, "GET")

            def do_HEAD(self) -> None:
                parent._handle(self, "HEAD")

            def do_POST(self) -> None:
                parent._handle(self, "POST")

            def log_message(self, _format: str, *_args: Any) -> None:
                return None

        return Handler

    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        path = handler.path.split("?", 1)[0]
        body = self._read_json_body(handler)
        with self._lock:
            self._requests.append(
                _RecordedRequest(
                    method=method,
                    raw_path=handler.path,
                    path=path,
                    body=body,
                    headers=dict(handler.headers.items()),
                )
            )

        if method in {"GET", "HEAD"} and path == "/":
            self._send_json(handler, _elastic_root(), include_body=method != "HEAD")
            return
        if method == "GET" and path == "/v1/models":
            self._send_json(
                handler,
                {
                    "data": [
                        {"id": "cosmos-embed1-448p-anomaly-detection"},
                        {"id": "cosmos-reason3"},
                    ]
                },
                status=self.vlm_models_status,
            )
            return
        if method == "POST" and path == "/v1/generate_text_embeddings":
            self._send_json(handler, {"data": [{"embeddings": [0.1, 0.2, 0.3]}]})
            return
        if method == "POST" and path == "/api/v1/generate_text_embeddings":
            self._send_json(handler, {"data": [[0.3, 0.2, 0.1]]})
            return
        if method == "POST" and path == "/v1/chat/completions":
            self._send_json(
                handler,
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "subject:forklift": True,
                                        "red": True,
                                    }
                                )
                            }
                        }
                    ]
                },
            )
            return
        if method == "GET" and path.startswith("/_cat/indices"):
            # `vss configure` reads the index inventory from the cluster
            # itself, so the mock has to answer it.
            self._send_json(
                handler,
                [{"index": "mdx-embed-filtered-2025-01-01"}, {"index": "mdx-behavior-2025-01-01"}],
            )
            return
        if method == "POST" and path.endswith("/_search"):
            self._send_json(handler, self._search_response_for(path, body))
            return
        if method == "GET" and path == "/vst/api/v1/sensor/streams":
            self._send_json(handler, [{_STREAM_ID: [{"name": "warehouse_clip", "url": "rtsp://example.test/stream"}]}])
            return
        if method == "GET" and path == "/vst/api/v1/storage/timelines":
            self._send_json(handler, {_STREAM_ID: [{"startTime": _START_TIME, "endTime": _END_TIME}]})
            return
        if method == "GET" and path == f"/vst/api/v1/storage/file/{_STREAM_ID}/url":
            self._send_json(handler, {"videoUrl": f"{self.base_url}/clips/{_STREAM_ID}.mp4"})
            return
        if method == "GET" and path == f"/clips/{_STREAM_ID}.mp4":
            self._send_bytes(handler, b"mock mp4 bytes", content_type="video/mp4")
            return

        self._send_json(handler, {"error": f"unexpected {method} {path}"}, status=404)

    def _search_response_for(self, path: str, body: Any) -> dict[str, Any]:
        # Embed targets the pinned anchor (video_file) or the wildcard minus that
        # anchor (rtsp); match those two exactly so a wrong index is not absorbed.
        if path in (
            "/mdx-embed-filtered-2025-01-01/_search",
            "/mdx-embed-filtered-*,-mdx-embed-filtered-2025-01-01/_search",
        ):
            return self.search_response
        if isinstance(body, dict) and body.get("query", {}).get("term", {}).get("object.id.keyword") is not None:
            return _object_embedding_response()
        return _behavior_search_response()

    @staticmethod
    def _read_json_body(handler: BaseHTTPRequestHandler) -> Any:
        length = int(handler.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return None
        raw = handler.rfile.read(length)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _send_json(
        handler: BaseHTTPRequestHandler,
        payload: dict[str, Any] | list[Any],
        *,
        status: int = 200,
        include_body: bool = True,
    ) -> None:
        encoded = json.dumps(payload).encode("utf-8") if include_body else b""
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(encoded)))
        handler.send_header("X-Elastic-Product", "Elasticsearch")
        handler.end_headers()
        if encoded:
            handler.wfile.write(encoded)

    @staticmethod
    def _send_bytes(
        handler: BaseHTTPRequestHandler,
        payload: bytes,
        *,
        status: int = 200,
        content_type: str = "application/octet-stream",
    ) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)


@pytest.fixture
def mock_services() -> Iterator[_MockSearchServices]:
    services = _MockSearchServices()
    try:
        yield services
    finally:
        services.close()


@pytest.fixture
def search_config(tmp_path: Path, mock_services: _MockSearchServices) -> Path:
    path = tmp_path / "search-config.yml"
    path.write_text(
        f"""
functions:
  embed_search:
    es_endpoint: {mock_services.base_url}
    es_index: mdx-embed-filtered-2025-01-01
    cosmos_embed_endpoint: {mock_services.base_url}
    vst_internal_url: {mock_services.base_url}
    vst_external_url: {mock_services.external_vst_url}
    default_max_results: 10
  attribute_search:
    rtvi_cv_endpoint: {mock_services.base_url}
    behavior_index: mdx-behavior-2025-01-01
    enable_frame_lookup: false
  search:
    use_attribute_search: true
    enable_critic: false
    default_max_results: 5
    embed_confidence_threshold: 0.1
    behavior_es_endpoint: {mock_services.base_url}
""".lstrip(),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def agent_root() -> Path:
    return Path(__file__).resolve().parents[6]


def test_search_archive_cli_e2e_returns_search_output_json(
    agent_root: Path,
    mock_services: _MockSearchServices,
) -> None:
    result = _run_search_archive(
        agent_root,
        mock_services,
        "--query",
        "red forklift",
        "--source-type",
        "video_file",
        "--video-source",
        "warehouse_clip",
        "--top-k",
        "1",
    )

    assert result.returncode == 0, result.stderr
    payload, marker = _result_and_marker(result.stdout)
    _assert_search_marker(marker, persisted=False)
    assert payload["data"] == [
        {
            "video_name": "warehouse_clip.mp4",
            "description": "red forklift near loading bay",
            "start_time": _START_TIME,
            "end_time": _END_TIME,
            "sensor_id": _STREAM_ID,
            # startTime is percent-encoded (quote(..., safe="")): the timestamp
            # is untrusted data, so ':'/'+'/etc. are escaped to prevent query
            # tampering. '%3A' decodes back to ':' at the VST server.
            "screenshot_url": (
                f"{mock_services.base_url}/vst/api/v1/replay/stream/"
                f"{_STREAM_ID}/picture?startTime={quote(_START_TIME, safe='')}"
            ),
            "similarity": 0.86,
            "object_ids": [],
            "verification": {
                "result": "confirmed",
                "criteria_met": {
                    "subject:forklift": True,
                    "red": True,
                },
            },
        }
    ]
    assert payload["search_messages"] == []

    embed_request = _single(mock_services.requests_for("/v1/generate_text_embeddings"))
    assert embed_request.body["text_input"] == ["red forklift"]
    assert embed_request.body["model"] == "cosmos-embed1-448p-anomaly-detection"

    # No preflight probe in the request path any more: index discovery
    # happens once at `vss configure` time, so this is the search itself.
    search_request = mock_services.requests_ending_with("/_search")[-1]
    # video_file selects the pinned uploads anchor, never a discovered date.
    assert search_request.path == "/mdx-embed-filtered-2025-01-01/_search"
    # Two multipliers compound: Search doubles top_k so merging adjacent
    # windows can still yield top_k results, and EmbedSearch overfetches 5x
    # because the video-source filter may discard KNN hits. top_k=1 -> 2 -> 10.
    assert search_request.body["size"] == 10
    assert search_request.body["query"]["bool"]["must"][0]["nested"]["query"]["knn"]["query_vector"] == [0.1, 0.2, 0.3]
    assert "warehouse_clip" in json.dumps(search_request.body)
    assert not any(request.path in {"/generate", "/api/v1/generate"} for request in mock_services.requests)
    assert len(mock_services.requests_for("/v1/models")) == 1
    assert len(mock_services.requests_for("/v1/chat/completions")) == 1


def test_search_archive_cli_rtsp_subtracts_uploads_anchor(
    agent_root: Path,
    mock_services: _MockSearchServices,
) -> None:
    # Regression: the rtsp flag previously subtracted a discovered "uploads base",
    # returning nothing in a stream-first / single-index deployment. It must now
    # subtract the pinned uploads anchor from the family wildcard, so live data
    # is always in scope regardless of ingestion order.
    result = _run_search_archive(
        agent_root,
        mock_services,
        "--query",
        "person at entrance",
        "--source-type",
        "rtsp",
        "--video-source",
        _STREAM_ID,
        "--top-k",
        "1",
    )

    assert result.returncode == 0, result.stderr
    search_request = mock_services.requests_ending_with("/_search")[-1]
    # rtsp queries the wildcard minus the pinned uploads anchor. The CLI joins
    # the multi-index target with commas in the request path.
    assert search_request.path == "/mdx-embed-filtered-*,-mdx-embed-filtered-2025-01-01/_search"


def test_search_archive_cli_attribute_only_uses_rtvi_cv_and_behavior_search(
    agent_root: Path,
    mock_services: _MockSearchServices,
) -> None:
    result = _run_search_archive(
        agent_root,
        mock_services,
        "--attribute",
        "white jacket",
        "--top-k",
        "1",
        # search_mode is the sub-action now
        action="attribute",
    )

    assert result.returncode == 0, result.stderr
    payload, marker = _result_and_marker(result.stdout)
    _assert_search_marker(marker, persisted=False)
    assert payload["data"][0]["object_ids"] == ["42"]
    assert "critic_result" not in payload["data"][0]
    assert payload["data"][0]["verification"]["result"] == "confirmed"
    assert mock_services.requests_for("/v1/generate_text_embeddings") == []
    assert mock_services.requests_for("/api/v1/generate_text_embeddings")[-1].body == {
        "text_input": "white jacket",
        "model": "",
    }
    assert mock_services.requests_ending_with("/_search")[-1].path == "/mdx-behavior-2025-01-01/_search"


def test_search_archive_cli_without_vlm_returns_unverified_hits(
    agent_root: Path,
    mock_services: _MockSearchServices,
) -> None:
    result = _run_search_archive(
        agent_root,
        mock_services,
        "--query",
        "red forklift",
        "--top-k",
        "1",
        include_vlm=False,
    )

    assert result.returncode == 0, result.stderr
    payload, marker = _result_and_marker(result.stdout)
    _assert_search_marker(marker, persisted=False)
    assert payload["data"][0]["verification"] == {
        "result": "unverified",
        "criteria_met": None,
    }
    assert mock_services.requests_for("/v1/chat/completions") == []


def test_search_archive_cli_unreachable_vlm_probes_once_and_returns_unverified_hits(
    agent_root: Path,
    mock_services: _MockSearchServices,
) -> None:
    mock_services.vlm_models_status = 503

    result = _run_search_archive(
        agent_root,
        mock_services,
        "--query",
        "red forklift",
        "--top-k",
        "1",
    )

    assert result.returncode == 0, result.stderr
    payload, marker = _result_and_marker(result.stdout)
    _assert_search_marker(marker, persisted=False)
    assert payload["data"][0]["verification"]["result"] == "unverified"
    assert len(mock_services.requests_for("/v1/models")) == 1
    assert mock_services.requests_for("/v1/chat/completions") == []


def test_search_archive_cli_explicit_fusion_for_action_plus_attributes(
    agent_root: Path,
    mock_services: _MockSearchServices,
) -> None:
    result = _run_search_archive(
        agent_root,
        mock_services,
        "--query",
        "person in a white jacket climbing a ladder",
        "--attribute",
        "white jacket",
        "--top-k",
        "1",
        # search_mode is the sub-action now
        action="fusion",
    )

    assert result.returncode == 0, result.stderr
    payload, marker = _result_and_marker(result.stdout)
    _assert_search_marker(marker, persisted=False)
    assert payload["data"][0]["object_ids"] == ["42"]
    assert _single(mock_services.requests_for("/v1/generate_text_embeddings")).body["text_input"] == [
        "person in a white jacket climbing a ladder"
    ]
    assert mock_services.requests_for("/api/v1/generate_text_embeddings")[-1].body["text_input"] == "white jacket"
    search_paths = [request.path for request in mock_services.requests_ending_with("/_search")]
    # video_file selects the pinned uploads anchor on both the embed and
    # behavior legs.
    assert search_paths.count("/mdx-embed-filtered-2025-01-01/_search") == 1
    assert search_paths.count("/mdx-behavior-2025-01-01/_search") == 1


def test_search_archive_cli_object_id_path_skips_query_embedding(
    agent_root: Path,
    mock_services: _MockSearchServices,
) -> None:
    result = _run_search_archive(
        agent_root,
        mock_services,
        "--object-id",
        "42",
        "--top-k",
        "1",
        # search_mode is the sub-action now
        action="object",
    )

    assert result.returncode == 0, result.stderr
    payload, marker = _result_and_marker(result.stdout)
    _assert_search_marker(marker, persisted=False)
    assert payload["data"][0]["object_ids"] == ["42"]
    assert mock_services.requests_for("/v1/generate_text_embeddings") == []
    behavior_searches = mock_services.requests_ending_with("/_search")
    assert len(behavior_searches) == 2
    assert behavior_searches[0].body["query"]["term"]["object.id.keyword"] == "42"
    assert behavior_searches[1].body["knn"]["query_vector"] == [0.3, 0.2, 0.1]


def test_search_archive_cli_rejects_removed_critic_options(
    agent_root: Path,
    mock_services: _MockSearchServices,
) -> None:
    result = _run_search_archive(
        agent_root,
        mock_services,
        "--query",
        "red forklift",
        "--use-critic",
    )

    assert result.returncode == 2
    assert "No such option" in result.stderr and "--use-critic" in result.stderr
    assert mock_services.requests_for("/v1/chat/completions") == []


def test_search_archive_cli_validation_errors_exit_2(
    agent_root: Path,
    mock_services: _MockSearchServices,
) -> None:
    result = _run_search_archive(
        agent_root,
        mock_services,
        "--query",
        "red forklift",
        "--timestamp-start",
        "not-a-timestamp",
    )

    assert result.returncode == 2
    assert "[vss] invalid input:" in result.stderr
    assert mock_services.requests == []


def test_search_archive_cli_unconfigured_deployment_exits_4(tmp_path: Path, agent_root: Path) -> None:
    """No recorded deployment is a configuration error, not a usage error.

    Endpoints are no longer per-call flags (NFR-6), so the failure this guards
    moved: it is now an empty ``$VSS_CONFIG_HOME``, and the message has to name
    the command that fixes it.
    """
    env = _subprocess_env(agent_root)
    env["VSS_CONFIG_HOME"] = str(tmp_path / "empty-config-home")
    result = subprocess.run(
        [*_search_archive_command(), "embed", "--query", "red forklift", "--log-level", "ERROR"],
        cwd=agent_root,
        env=env,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )

    assert result.returncode == 4, result.stderr
    assert "vss configure" in result.stderr


@pytest.mark.asyncio
async def test_vss_search_facade_e2e_uses_concrete_clients_with_mock_services(
    mock_services: _MockSearchServices,
) -> None:
    from vss_core.search_core import SearchRuntime
    from vss_core.search_core.clients.elastic import ElasticClient
    from vss_core.search_core.host import VSSSearch

    # Values passed in directly: config-file loading was removed, so a caller
    # supplies what it already knows (the CLI reads `vss configure`'s record,
    # the NAT adapter reads its own config).
    runtime = SearchRuntime.from_kwargs(
        es_endpoint=mock_services.base_url,
        behavior_es_endpoint=mock_services.base_url,
        cosmos_embed_endpoint=mock_services.base_url,
        rtvi_cv_endpoint=mock_services.base_url,
        vst_internal_url=mock_services.base_url,
        vst_external_url=mock_services.external_vst_url,
        video_embed_index="mdx-embed-filtered-2025-01-01",
        behavior_index="mdx-behavior-2025-01-01",
        embed_confidence_threshold=0.1,
        default_max_results=5,
    )

    try:
        async with VSSSearch.from_runtime(runtime) as vss:
            out = await vss.search(
                query="red forklift",
                original_query="red forklift",
                source_type="video_file",
                video_sources=["warehouse_clip"],
                top_k=1,
            )
    finally:
        await ElasticClient.close_all()

    assert len(out.data) == 1
    assert out.data[0].video_name == "warehouse_clip.mp4"
    assert out.data[0].similarity == 0.86
    assert _single(mock_services.requests_for("/v1/generate_text_embeddings")).body["text_input"] == ["red forklift"]
    search_request = _single(mock_services.requests_ending_with("/_search"))
    assert search_request.path == "/mdx-embed-filtered-2025-01-01/_search"


def _run_search_archive(
    agent_root: Path,
    services: _MockSearchServices,
    *args: str,
    action: str = "embed",
    include_vlm: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [*_search_archive_command(), action, *_runtime_args(services), *args]
    config_home = Path(tempfile.mkdtemp(prefix="vss-e2e-config-"))
    _write_deployment_config(config_home, services, include_vlm=include_vlm)
    env = _subprocess_env(agent_root)
    env["VSS_CONFIG_HOME"] = str(config_home)
    return subprocess.run(
        command,
        cwd=agent_root,
        env=env,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )


def _search_archive_command() -> list[str]:
    # Search is a `vss` domain operation; the standalone search-archive
    # script was folded into `vss search run`. Invoke the source module so
    # this hermetic suite does not depend on a potentially stale local console
    # script; package-install coverage exercises the generated script.
    return [
        sys.executable,
        "-c",
        "from vss_cli import main; raise SystemExit(main())",
        "search",
        "run",
    ]


def _runtime_args(services: _MockSearchServices) -> list[str]:
    """Per-call flags that survive NFR-6.

    Endpoints and index names are no longer passed here -- they come from the
    recorded deployment (see :func:`_write_deployment_config`), because they
    describe a deployment rather than a request.
    """
    return ["--embed-confidence-threshold", "0.1", "--log-level", "ERROR"]


def _write_deployment_config(
    home: Path,
    services: _MockSearchServices,
    *,
    include_vlm: bool = True,
) -> None:
    """Write the config `vss configure` would have produced for the mocks."""
    home.mkdir(parents=True, exist_ok=True)
    configured_services: dict[str, dict[str, Any]] = {
        "agent": {"url": services.base_url},
        "vst": {"url": f"{services.base_url}/vst"},
        "elasticsearch": {
            "url": services.base_url,
            "indices": ["mdx-embed-filtered-2025-01-01", "mdx-behavior-2025-01-01"],
        },
        "rt_embed": {
            "url": services.base_url,
            "models": ["cosmos-embed1-448p-anomaly-detection"],
        },
        "rtvi_cv": {"url": services.base_url},
    }
    if include_vlm:
        configured_services["rt_vlm"] = {
            "url": services.base_url,
            "models": ["cosmos-reason3"],
        }

    (home / "config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "base_url": services.base_url,
                "written_at": "2025-01-01T00:00:00+00:00",
                "services": configured_services,
            }
        ),
        encoding="utf-8",
    )


def _subprocess_env(agent_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = str(agent_root / "src")
    if env.get("PYTHONPATH"):
        pythonpath = pythonpath + os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = pythonpath
    env["PYTHONUNBUFFERED"] = "1"
    env["VSS_AGENT_CONFIG_FILE"] = "/this/path/must/not-be-read"
    for key in (
        "ELASTIC_SEARCH_ENDPOINT",
        "COSMOS_EMBED_ENDPOINT",
        "RTVI_CV_BASE_URL",
        "VST_INTERNAL_URL",
        "VST_EXTERNAL_URL",
    ):
        env[key] = "http://env-fallback-must-not-be-used.invalid"
    return env


def _json_lines(stdout: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip().startswith("{")]


def _result_and_marker(stdout: str) -> tuple[dict[str, Any], dict[str, Any]]:
    lines = _json_lines(stdout)
    assert len(lines) == 2
    return lines[0], lines[-1]


def _assert_search_marker(marker: dict[str, Any], *, persisted: bool) -> None:
    assert marker["event"] == "vss_job_completed"
    assert marker["group"] == "search"
    assert marker["job_id"].startswith("search-")
    assert marker["status"] == "completed"
    assert marker["persisted"] is persisted
    assert marker["exit_hint"] == 0


def _single(items: list[_RecordedRequest]) -> _RecordedRequest:
    assert len(items) == 1
    return items[0]


def _elastic_root() -> dict[str, Any]:
    return {
        "name": "mock-es",
        "cluster_name": "mock-cluster",
        "cluster_uuid": "mock-cluster-uuid",
        "version": {
            "number": "8.17.0",
            "build_flavor": "default",
            "build_type": "tar",
            "build_hash": "mock",
            "build_date": "2026-01-01T00:00:00.000Z",
            "build_snapshot": False,
            "lucene_version": "9.12.0",
            "minimum_wire_compatibility_version": "7.17.0",
            "minimum_index_compatibility_version": "7.0.0",
        },
        "tagline": "You Know, for Search",
    }


def _embed_search_response() -> dict[str, Any]:
    response_data = {
        "video_name": "warehouse_clip.mp4",
        "description": "red forklift near loading bay",
        "start_time": _START_TIME,
        "end_time": _END_TIME,
    }
    return {
        "took": 1,
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "max_score": 0.93,
            "hits": [
                {
                    "_index": "video_embeddings",
                    "_id": "hit-1",
                    "_score": 0.93,
                    "_source": {
                        "timestamp": _START_TIME,
                        "end": _END_TIME,
                        "sensor": {
                            "id": "warehouse_clip",
                            "stream_id": _STREAM_ID,
                            "description": "Warehouse loading bay",
                            "info": {
                                "path": f"/archive/{_STREAM_ID}/warehouse_clip.mp4",
                                "url": "file:///archive/warehouse_clip.mp4",
                            },
                        },
                        "llm": {
                            "queries": [{"response": json.dumps(response_data)}],
                            "visionEmbeddings": {"vector": [0.1, 0.2, 0.3]},
                        },
                    },
                }
            ],
        },
    }


def _behavior_search_response() -> dict[str, Any]:
    return {
        "took": 1,
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "max_score": 0.91,
            "hits": [
                {
                    "_index": "mdx-behavior-2025-01-01",
                    "_id": "behavior-1",
                    "_score": 0.91,
                    "_source": {
                        "timestamp": _START_TIME,
                        "end": _END_TIME,
                        "sensor": {"id": "warehouse_clip", "stream_id": _STREAM_ID},
                        "object": {
                            "id": "42",
                            "type": "person",
                            "bbox": {"leftX": 1, "rightX": 2, "topY": 3, "bottomY": 4},
                        },
                        "embeddings": {"vector": [0.3, 0.2, 0.1]},
                    },
                }
            ],
        },
    }


def _object_embedding_response() -> dict[str, Any]:
    return {
        "took": 1,
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "max_score": 1.0,
            "hits": [
                {
                    "_index": "mdx-behavior-2025-01-01",
                    "_id": "object-42",
                    "_score": 1.0,
                    "_source": {"embeddings": {"vector": [0.3, 0.2, 0.1]}},
                }
            ],
        },
    }
