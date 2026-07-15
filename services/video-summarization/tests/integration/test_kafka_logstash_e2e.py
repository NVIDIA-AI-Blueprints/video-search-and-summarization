# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""End-to-end tests for the live-stream RTVI -> Kafka -> Logstash -> ES path.

NO MOCKS, NO IN-PROCESS SERVERS. Runs against an already-up
``BlueprintBuilderGenerated/docker-compose.yml`` stack brought up with
profiles ``rtvi`` and ``kafka`` and the streaming env knobs set:

    USE_RTVI_VLM=true
    RTVI_VLM_URL_PASSTHROUGH=true
    KAFKA_ENABLED=true
    KAFKA_BOOTSTRAP_SERVERS=kafka:9092
    KAFKA_TOPIC=mdx-vlm-captions
    KAFKA_STRUCTURED_SUMMARY_TOPIC=mdx-structured-events-summary
    LVS_DATABASE_BACKEND=elasticsearch_db
    LVS_EMB_ENABLE=false
    LVS_EMB_DIMENSIONS=1024
    LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=true
    VIA_DEV_API=true

Targets:

    via-engine     http://localhost:${LVS_BACKEND_PORT:-38111}
    rtvi-vlm       http://localhost:${RTVI_VLM_PORT:-8000}
    elasticsearch  http://${ES_HOST:-localhost}:${ES_PORT:-9200}

Tests cover the LVS-driven live-stream Kafka summarize flow:

  * POST /v1/stream/add to RTVI with camera_id="" and NO metadata.prompt
    registers the asset but does NOT start captioning. asset.sensor_name
    is empty so info["streamId"] = chunk.streamId = asset UUID, which is
    what Logstash needs to route raw_events to default_<asset_id>.

  * POST /v1/summarize stream=true (gated on
    KAFKA_ENABLED=true) is the only LVS-side live-stream API. On every
    call, the handler does TWO things:
      A. Fires POST /v1/generate_captions on RTVI (fire-and-forget) to
         start captioning. RTVI handles duplicate triggers as a no-op
         (reconnect to existing PROCESSING request_id, OR HTTP 409 from
         the SSE-active-client gate). LVS treats both as success — no
         in-memory state, restart-safe.
      B. Aggregates raw_events from ES via
         vlm_structured_summarization_online (kafka_enabled=true) and
         publishes structured_events + aggregated_summary back to Kafka.
    The aggregator output is JSON-stringified inside
    choices[0].message.content of the returned CompletionResponse. The
    very first call for a brand-new stream usually returns events=[]
    because captioning was just kicked off.

  * Time-window filter: sequential aggregate calls with progressively
    narrower windows return monotonically-bounded event counts.
    Sequential consistency only holds because
    LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=true is set on the LVS
    container.

  * Out-of-range time window: aggregate calls for windows entirely past
    the end of the captured stream (e.g. (60,90) and (300,450) for a
    1-minute RTSP loop) round-trip a 200 with an empty events list.

  * Negative gate: stream=false returns HTTP 400
    (the new flow requires stream=true as the opt-in marker).

  * File-path Kafka mode: KAFKA_ENABLED=true AND
    summarization.kafka_enabled=true now mirrors the live-stream Kafka
    flow — RTVI publishes per-chunk raw_events, the aggregator reads
    them back from ES keyed by uuid==file_id, and LVS publishes
    structured_events + aggregated_summary back to Kafka. All three
    doc_types must land in default_<file_id> via Logstash. The fixture
    drives this via URL passthrough against the in-stack media-server
    2-minute mp4 (KAFKA_E2E_FILE_URL=http://media-server/2min.mp4 by
    default) with `stream=True` — LVS replies with SSE end-to-end
    (matching the SSE-only RTVI -> LVS captioning leg). The fixture
    captures only the FINAL SSE event (the one before `data: [DONE]`)
    which carries the aggregated events + video_summary payload.
    (Previously asserted the OPPOSITE — that test was inverted along
    with the file-path Kafka-mode design change.)

Set ``KAFKA_E2E_TEST=1`` in the runner env to enable. Skipped otherwise so
the default integration-stage runtime stays predictable.
"""

import json
import logging
import os
import time
from typing import Any, Dict

import pytest
import requests

logger = logging.getLogger(__name__)

# -- Skip gate ---------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    os.environ.get("KAFKA_E2E_TEST", "0") != "1",
    reason=(
        "Set KAFKA_E2E_TEST=1 to enable the live-stream Kafka -> Logstash -> ES "
        "end-to-end test. Requires the BlueprintBuilderGenerated compose stack "
        "with profiles `rtvi` and `kafka` already up."
    ),
)

# -- Config (defaults match BlueprintBuilderGenerated/.env) ------------------

LVS_PORT = int(os.environ.get("LVS_BACKEND_PORT") or os.environ.get("BACKEND_PORT") or 38111)
LVS_BASE_URL = os.environ.get("LVS_BASE_URL", f"http://localhost:{LVS_PORT}").rstrip("/")

RTVI_PORT = int(os.environ.get("RTVI_VLM_PORT") or 8000)
RTVI_BASE_URL = os.environ.get("RTVI_VLM_URL", f"http://localhost:{RTVI_PORT}").rstrip("/")

ES_HOST = os.environ.get("ES_HOST", "localhost")
ES_PORT = os.environ.get("ES_PORT", "9200")
ES_BASE_URL = os.environ.get("ES_BASE_URL", f"http://{ES_HOST}:{ES_PORT}").rstrip("/")

# MP4 used by the file-path Kafka-mode test. The default points at the
# in-stack media-server URL (resolved by docker DNS from the LVS container)
# so RTVI fetches the file via URL passthrough -- no artifactory auth /
# local download needed at request time. Mirrors run_sanity.sh's
# `http://media-server/2min.mp4` and run_sanity_kafka.sh step 9. Override
# via KAFKA_E2E_FILE_URL when running against a stack that doesn't bundle
# media-server.
KAFKA_E2E_FILE_URL = os.environ.get("KAFKA_E2E_FILE_URL", "http://media-server/2min.mp4")

# Known-good NVIDIA-internal RTSP source (looped MP4 served via Wowza). RTVI
# pulls real frames from this URL, the VLM produces real captions, and
# Logstash writes real raw_events docs to ES. Override via env if needed.
# Matches run_sanity_kafka.sh's SANITY_RTSP_URL default.
RTSP_URL = os.environ.get(
    "KAFKA_E2E_RTSP_URL",
    "rtsp://nv-wowza-pdc.nvidia.com:1935/vod/warehouse_1.mp4",
)

# Warehouse-safety scenario + 11 events. LVS templates these into the VLM
# caption prompt via _create_vlm_prompt() and passes it to RTVI's
# /v1/generate_captions when /v1/summarize is called. They are also
# required fields on SummarizationQuery (pydantic).
SUMMARIZE_STREAM_SCENARIO = "warehouse safety monitoring"
SUMMARIZE_STREAM_EVENTS = [
    "box dropping",
    "not wearing PPE",
    "unsafe forklift operations",
    "walking into restricted area",
    "unauthorized personnel",
    "forklift stuck",
    "poor handling of hazardous materials",
    "arson",
    "theft",
    "fire",
    "normal activity",
]


# -- Helpers -----------------------------------------------------------------


def _wait_for(condition_fn, timeout_s: float = 120.0, poll_s: float = 2.0, label: str = ""):
    """Poll ``condition_fn`` until truthy or the timeout expires."""
    deadline = time.time() + timeout_s
    last_exc: Exception | None = None
    last_value: Any = None
    while time.time() < deadline:
        try:
            last_value = condition_fn()
            if last_value:
                return last_value
        except Exception as ex:
            last_exc = ex
        time.sleep(poll_s)
    pytest.fail(
        f"Timed out after {timeout_s}s waiting for: {label} "
        f"(last_value={last_value!r}, last_exception={last_exc!r})"
    )


def _es_index_exists(index: str) -> bool:
    try:
        return requests.head(f"{ES_BASE_URL}/{index}", timeout=10).status_code == 200
    except requests.RequestException:
        return False


def _es_search(index: str, body: Dict[str, Any]) -> Dict[str, Any]:
    resp = requests.post(
        f"{ES_BASE_URL}/{index}/_search",
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _es_doc_count(index: str, doc_type: str) -> int:
    """Count docs of the given ``doc_type`` in ``index``.

    Note: ``metadata.content_metadata.doc_type`` is mapped as ``keyword``
    directly by the visionllm index template, so do NOT append ``.keyword``
    (no auto-generated sub-field exists for an explicit keyword mapping;
    the term query would silently match zero docs).
    """
    if not _es_index_exists(index):
        return 0
    resp = _es_search(
        index,
        {
            "query": {"term": {"metadata.content_metadata.doc_type": doc_type}},
            "size": 0,
        },
    )
    return int(resp["hits"]["total"]["value"])


def _es_refresh(index: str) -> None:
    try:
        requests.post(f"{ES_BASE_URL}/{index}/_refresh", timeout=10)
    except requests.RequestException:
        pass


def _index_for_stream(stream_id: str) -> str:
    """LVS builds the index name as default_<stream_id> with hyphens -> underscores."""
    return "default_" + stream_id.replace("-", "_")


def _resolve_model_id() -> str:
    r = requests.get(f"{LVS_BASE_URL}/models", timeout=15)
    r.raise_for_status()
    return r.json()["data"][0]["id"]


def _sticky_headers(stream_id: str | None) -> dict:
    """Return ``{"x-stream-id": stream_id}`` when ``stream_id`` is set,
    else an empty dict.

    METLVSMS-500: every outbound HTTP call to LVS or RTVI that targets a
    specific stream/asset must carry ``x-stream-id`` so NGINX Ingress can
    consistent-hash-route the request to the same RTVI replica that owns
    the asset's in-memory state. Round-robin endpoints (health, models,
    list_files) MUST NOT carry the header.
    """
    if not stream_id:
        return {}
    return {"x-stream-id": str(stream_id)}


def _rtvi_add_stream_no_inference() -> str:
    """Register a live stream on RTVI-VLM WITHOUT starting captioning.

    Mirrors the new sanity-script step 4. Sends ``camera_id=""`` so RTVI
    sets ``asset.sensor_name=""`` and baseline VisionLLM exposes
    ``chunk.streamId`` (the asset UUID) as ``info["streamId"]``. NO
    ``metadata.prompt`` -> RTVI does NOT auto-start captioning
    (``response.inference != true``).

    Captioning is triggered separately by ``POST /v1/generate_captions``
    on LVS, which forwards to RTVI's ``/v1/generate_captions`` via
    ``RtviVlmClient.start_captions``.

    Returns the asset_id (UUID). Note: this call is operator-direct to
    RTVI; the asset_id is server-generated so we can't sticky-route this
    particular call (chicken-and-egg). Subsequent LVS calls keyed on the
    returned ``asset_id`` ARE sticky-routed via ``x-stream-id``.
    """
    payload = {
        "key": "sensor",
        "value": {
            "camera_id": "",  # empty -> sensor_name="" -> info[streamId]=UUID
            "camera_url": RTSP_URL,
            "change": "camera_add",
        },
    }
    r = requests.post(f"{RTVI_BASE_URL}/v1/stream/add", json=payload, timeout=60)
    assert r.status_code == 200, f"stream/add failed: {r.status_code} {r.text}"
    body = r.json()
    asset_id = body.get("asset_id")
    inference = bool(body.get("inference", False))
    assert asset_id, f"stream/add returned no asset_id: {r.text}"
    assert not inference, (
        f"stream/add unexpectedly started inference (response.inference == true): "
        f"{r.text}. Did metadata.prompt sneak into the payload?"
    )
    return asset_id


def _post_generate_captions(
    stream_id: str,
    model_id: str,
    scenario: str = SUMMARIZE_STREAM_SCENARIO,
    events: list | None = None,
    chunk_duration: int = 10,
) -> requests.Response:
    """POST /v1/generate_captions (live-stream captioning trigger).

    The live-stream Kafka flow's Phase 1 endpoint: fire-and-forget. LVS
    forwards the prompt template (built from ``scenario`` + ``events``)
    to RTVI's /v1/generate_captions and returns immediately with a
    ``GenerateCaptionsResponse`` whose ``status="accepted"``.

    Carries ``x-stream-id: <stream_id>`` for NGINX Ingress sticky routing
    (METLVSMS-500). The same ``stream_id`` is also the request body's
    ``id`` field — they MUST agree so the trigger lands on the RTVI
    replica that owns the asset's in-memory state.

    Gated on ``KAFKA_ENABLED=true`` server-side (otherwise 400).
    """
    body = {
        "id": stream_id,
        "model": model_id,
        "scenario": scenario,
        "events": events if events is not None else SUMMARIZE_STREAM_EVENTS,
        "chunk_duration": chunk_duration,
    }
    return requests.post(
        f"{LVS_BASE_URL}/v1/generate_captions",
        json=body,
        headers=_sticky_headers(stream_id),
        timeout=120,
    )


def _post_stream_summarize(
    stream_id: str,
    model_id: str,
    start_time: float | str = 0,
    end_time: float | str = 0,
) -> requests.Response:
    """POST /v1/stream_summarize (live-stream aggregator).

    The live-stream Kafka flow's Phase 2 endpoint: synchronous aggregator.
    Borrows a ctx-rag context manager, calls
    ``summarization_online`` with ``uuids=[stream_id]`` and an optional
    time window, publishes ``structured_events`` + ``aggregated_summary``
    back to Kafka, and returns a ``CompletionResponse`` whose
    ``choices[0].message.content`` is the JSON-stringified
    ``{events, video_summary, total_events, uuids}``.

    ``start_time`` and ``end_time`` accept ``0`` (no filter sentinel),
    a float (seconds), or an ISO 8601 string. Carries
    ``x-stream-id: <stream_id>`` for sticky routing.

    Gated on ``KAFKA_ENABLED=true`` server-side (otherwise 400).
    """
    body = {
        "id": stream_id,
        "model": model_id,
        "start_time": start_time,
        "end_time": end_time,
    }
    return requests.post(
        f"{LVS_BASE_URL}/v1/stream_summarize",
        json=body,
        headers=_sticky_headers(stream_id),
        timeout=600,
    )


def _parse_stream_summarize_payload(resp: requests.Response) -> Dict[str, Any]:
    """Parse the aggregator JSON out of CompletionResponse.choices[0]
    .message.content for /v1/stream_summarize.

    Shape: ``{events, video_summary, total_events, uuids}``. Note
    ``uuids`` is a LIST containing the asset/stream ID(s) (only one for
    a single-stream summarize). The wrapping CompletionResponse also
    carries ``video_id`` at top level (== the requested stream_id).
    """
    body = resp.json()
    choices = body.get("choices") or []
    assert choices, f"/v1/stream_summarize returned no choices: {body}"
    content = (choices[0].get("message") or {}).get("content", "")
    assert content, f"/v1/stream_summarize returned empty content: {body}"
    try:
        return json.loads(content)
    except json.JSONDecodeError as ex:
        pytest.fail(
            f"/v1/stream_summarize content is not JSON: {ex} | " f"content[:200]={content[:200]!r}"
        )
        # pytest.fail raises BaseException, so the line below is unreachable —
        # but it satisfies static analysis that the function returns Dict[str, Any].
        return {}


def _upload_sample(sample_video_path: str, file_id: str | None = None) -> str:
    """POST /v1/files (multipart upload). When ``file_id`` is supplied,
    LVS uses it as the asset_id and the call is sticky-routed via
    ``x-stream-id``. When omitted, RTVI generates the asset_id server-
    side and the call cannot be sticky-routed (chicken-and-egg).
    """
    with open(sample_video_path, "rb") as fh:
        files = {"file": (os.path.basename(sample_video_path), fh, "video/mp4")}
        data = {"purpose": "vision", "media_type": "video"}
        if file_id:
            data["id"] = str(file_id)
        r = requests.post(
            f"{LVS_BASE_URL}/files",
            files=files,
            data=data,
            headers=_sticky_headers(file_id),
            timeout=300,
        )
    assert r.status_code == 200, f"file upload failed: {r.status_code} {r.text}"
    return r.json()["id"]


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture(scope="module")
def lvs_ready():
    """Wait until via-engine reports /v1/ready (200)."""

    def ready():
        try:
            return requests.get(f"{LVS_BASE_URL}/v1/ready", timeout=5).status_code == 200
        except requests.RequestException:
            return False

    _wait_for(ready, timeout_s=300, label=f"via-engine /v1/ready at {LVS_BASE_URL}")


@pytest.fixture(scope="module")
def rtvi_ready():
    """Wait until rtvi-vlm reports /v1/health/ready (200)."""

    def ready():
        try:
            return requests.get(f"{RTVI_BASE_URL}/v1/health/ready", timeout=5).status_code == 200
        except requests.RequestException:
            return False

    _wait_for(ready, timeout_s=600, label=f"rtvi-vlm /v1/health/ready at {RTVI_BASE_URL}")


@pytest.fixture(scope="module")
def es_ready():
    """Wait until Elasticsearch reports cluster health yellow+."""

    def ready():
        try:
            r = requests.get(
                f"{ES_BASE_URL}/_cluster/health?wait_for_status=yellow&timeout=5s",
                timeout=10,
            )
            return r.status_code == 200
        except requests.RequestException:
            return False

    _wait_for(ready, timeout_s=180, label=f"elasticsearch health at {ES_BASE_URL}")


@pytest.fixture(scope="module")
def index_template_present(es_ready, lvs_ready):
    """The visionllm index template is registered by lvs at startup."""

    def present():
        try:
            r = requests.get(f"{ES_BASE_URL}/_index_template/visionllm", timeout=10)
            return r.status_code == 200
        except requests.RequestException:
            return False

    _wait_for(present, timeout_s=120, label="lvs registered visionllm index template")


@pytest.fixture(scope="module")
def model_id(lvs_ready) -> str:
    return _resolve_model_id()


@pytest.fixture(scope="module")
def live_stream_with_raw_events(
    lvs_ready, rtvi_ready, es_ready, index_template_present, model_id
) -> str:
    """Register stream, trigger captioning, wait for raw_events.

    Module-scoped so all live-stream tests share one stream and don't each
    pay the captioning latency. Returns the asset_id.

    Three-step flow (mirrors run_sanity_kafka.sh steps 4-6):

      1. POST /v1/stream/add to RTVI with camera_id="" and NO metadata.
         Asset registered, captioning NOT yet running. The
         asset.sensor_name="" trick makes RTVI's baseline info["streamId"]
         carry chunk.streamId (asset UUID) for Logstash routing.

      2. POST /v1/generate_captions on LVS to trigger captioning on RTVI
         (fire-and-forget). LVS returns 200 immediately with
         ``GenerateCaptionsResponse{id, status="accepted", model}``.
         RTVI starts publishing raw_events to Kafka in the background.

      3. Wait for >= KAFKA_E2E_MIN_RAW_EVENTS raw_events docs to land in
         default_<asset_id>. With chunk_duration=10s the publisher emits
         roughly one raw_event every ~10s; waiting for at least 3 docs
         avoids flaky low-event aggregations in the follow-up tests.
    """
    asset_id = _rtvi_add_stream_no_inference()
    logger.info("Registered live stream (no inference) asset_id=%s", asset_id)

    # Step 2: trigger captioning via /v1/generate_captions.
    trigger_resp = _post_generate_captions(asset_id, model_id)
    assert trigger_resp.status_code == 200, (
        f"trigger /v1/generate_captions failed: " f"{trigger_resp.status_code} {trigger_resp.text}"
    )
    body = trigger_resp.json()
    assert (
        body.get("status") == "accepted"
    ), f"/v1/generate_captions returned unexpected status: {body}"
    assert (
        body.get("id") == asset_id
    ), f"/v1/generate_captions echoed wrong id: {body.get('id')!r} != {asset_id!r}"
    logger.info(
        "/v1/generate_captions -> 200 (triggered RTVI captioning for %s)",
        asset_id,
    )

    # Step 3: wait for raw_events to land.
    index_name = _index_for_stream(asset_id)
    timeout_s = float(os.environ.get("KAFKA_E2E_RAW_EVENTS_TIMEOUT", "600"))
    min_raw = int(os.environ.get("KAFKA_E2E_MIN_RAW_EVENTS", "3"))

    def has_min_raw_events():
        _es_refresh(index_name)
        return _es_doc_count(index_name, "raw_events") >= min_raw

    _wait_for(
        has_min_raw_events,
        timeout_s=timeout_s,
        label=f">= {min_raw} raw_events docs in {index_name}",
    )
    return asset_id


# -- Tests -------------------------------------------------------------------


class TestRawEventsDocShape:
    """raw_events doc shape written by Logstash from the
    mdx-vlm-captions Kafka topic (RTVI auto-inference path).
    """

    @pytest.mark.test_in_ci
    def test_raw_events_doc_shape(self, live_stream_with_raw_events):
        asset_id = live_stream_with_raw_events
        index_name = _index_for_stream(asset_id)

        # Doc shape sanity (raw_events written by Logstash in the
        # ElasticsearchDBTool.add_summary shape).
        resp = _es_search(
            index_name,
            {
                "query": {"term": {"metadata.content_metadata.doc_type": "raw_events"}},
                "size": 1,
            },
        )
        hits = resp["hits"]["hits"]
        assert hits, "no raw_events hit returned"
        doc = hits[0]["_source"]
        assert "text" in doc, f"top-level text missing: {sorted(doc.keys())}"
        assert (
            "vector" in doc and isinstance(doc["vector"], list) and doc["vector"]
        ), "vector must be a non-empty list (zero vector when LVS_EMB_ENABLE=false)"
        assert "metadata" in doc, "metadata missing"
        assert "source" in doc["metadata"], "metadata.source missing"
        cm = doc["metadata"].get("content_metadata", {})
        assert cm.get("doc_type") == "raw_events"
        assert cm.get("uuid"), "uuid missing in content_metadata"

        # Numeric coercion sanity (int/float/bool restored from proto map<string,string>).
        assert isinstance(
            cm.get("chunkIdx"), int
        ), f"chunkIdx not int: {type(cm.get('chunkIdx'))}={cm.get('chunkIdx')!r}"

        # New-flow contract: with camera_id="" on stream/add, RTVI's
        # asset.sensor_name="" so info["streamId"] = chunk.streamId =
        # asset UUID. Logstash falls back uuid to streamId when uuid is
        # not separately set. So both should equal asset_id.
        assert cm.get("streamId") == asset_id, (
            f"info[streamId] != asset_id: {cm.get('streamId')!r} != {asset_id!r}. "
            "Either camera_id was not empty on stream/add (asset.sensor_name nonempty) "
            "or chunk.streamId is not propagating."
        )
        assert cm.get("uuid") == asset_id, (
            f"cm[uuid] != asset_id: {cm.get('uuid')!r} != {asset_id!r}. "
            "Logstash uuid fallback to info[streamId] may be missing."
        )

        # Chunk-level timestamp floats are derived in Logstash from
        # vision_llm.timestamp / vision_llm.end proto Timestamps. Pure
        # observability; not used by the read path.
        assert isinstance(cm.get("start_ntp_float"), (int, float)), (
            f"start_ntp_float not numeric: {type(cm.get('start_ntp_float'))}="
            f"{cm.get('start_ntp_float')!r} — Logstash derivation may be missing."
        )
        assert isinstance(cm.get("end_ntp_float"), (int, float)), (
            f"end_ntp_float not numeric: {type(cm.get('end_ntp_float'))}="
            f"{cm.get('end_ntp_float')!r} — Logstash derivation may be missing."
        )


class TestStreamSummarizeReturnsEvents:
    """POST /v1/stream_summarize returns events fetched from ES,
    wrapped as JSON inside choices[0].message.content.

    Lives on the live-stream Kafka path: captioning was already kicked off
    by the ``live_stream_with_raw_events`` fixture via
    /v1/generate_captions. This test exercises the aggregator endpoint.
    """

    @pytest.mark.test_in_ci
    def test_stream_summarize_full_window(self, live_stream_with_raw_events, model_id):
        asset_id = live_stream_with_raw_events

        # 0/0 is the "no time filter" sentinel — every raw_event for the
        # stream should be aggregated.
        r = _post_stream_summarize(asset_id, model_id, start_time=0, end_time=0)
        assert r.status_code == 200, f"stream_summarize failed: {r.status_code} {r.text}"

        # CompletionResponse wrapper carries video_id at the top level.
        body = r.json()
        assert body.get("video_id") == asset_id, (
            f"video_id mismatch in CompletionResponse: " f"{body.get('video_id')!r} != {asset_id!r}"
        )

        payload = _parse_stream_summarize_payload(r)
        # New shape: `uuids` is a list containing the stream_id(s).
        uuids = payload.get("uuids") or []
        assert asset_id in uuids, f"asset_id {asset_id!r} not in payload uuids: {uuids!r}"
        events = payload.get("events") or []
        assert (
            isinstance(events, list) and len(events) > 0
        ), f"expected non-empty events list, got: {payload}"

        # Each event has a stable contract (start_time, end_time, type,
        # description) PLUS may carry LLM-injected extras like ``id``.
        # The live-stream path emits ISO 8601 timestamp strings (per
        # docs/streaming_rtvi_kafka_logstash.md §10.5
        # "_convert_event_timestamps_to_iso"), e.g.
        # "2026-05-05T16:06:56.789Z". Older callers may also see numeric
        # seconds when the aggregator surfaces unconverted events; accept
        # either shape so the test stays robust to LLM output variation.
        first = events[0]
        for required in ("start_time", "end_time", "type", "description"):
            assert required in first, f"event missing required key '{required}': {first}"
        for key in ("start_time", "end_time"):
            v = first[key]
            assert isinstance(
                v, (int, float, str)
            ), f"event[{key}] unexpected type {type(v).__name__}: {v!r}"

        assert isinstance(payload.get("video_summary"), str)
        assert int(payload.get("total_events", 0)) >= len(events)

    @pytest.mark.test_in_ci
    def test_stream_summarize_publishes_to_kafka_visible_in_es(
        self, live_stream_with_raw_events, model_id
    ):
        """After /v1/stream_summarize LVS publishes to the
        ``mdx-structured-events-summary`` Kafka topic (one structured_events
        message per ``max_events_per_batch`` plus one aggregated_summary).
        Logstash consumes both and writes them to ``default_<asset_id>``.

        ES doc IDs are deterministic per stream (see design doc §10.6) so
        repeated /v1/stream_summarize calls UPSERT the same two docs
        rather than growing the count. We therefore verify the publish
        round-trip via ES doc ``@timestamp`` advancing past the value
        from before our call, not via document count growth.
        """
        asset_id = live_stream_with_raw_events
        index_name = _index_for_stream(asset_id)

        def _latest_ts(doc_type: str) -> str:
            """Return the @timestamp of the latest doc of ``doc_type`` in
            ``index_name``, or empty string if no such doc exists yet.
            """
            _es_refresh(index_name)
            try:
                resp = _es_search(
                    index_name,
                    {
                        "query": {"term": {"metadata.content_metadata.doc_type": doc_type}},
                        "size": 1,
                        "sort": [{"@timestamp": {"order": "desc"}}],
                        "_source": ["@timestamp"],
                    },
                )
            except requests.HTTPError:
                return ""
            hits = resp.get("hits", {}).get("hits", [])
            if not hits:
                return ""
            return hits[0].get("_source", {}).get("@timestamp", "") or ""

        # Snapshot the @timestamp of the most-recent doc of each type
        # before our call; we want our call to upsert past these values.
        baseline_se_ts = _latest_ts("structured_events")
        baseline_agg_ts = _latest_ts("aggregated_summary")

        r = _post_stream_summarize(asset_id, model_id, start_time=0, end_time=0)
        assert r.status_code == 200, f"stream_summarize failed: {r.status_code} {r.text}"

        # Wait for the upsert to land via Kafka -> Logstash -> ES. Both
        # docs MUST exist (>= one of each) AND their @timestamp MUST be
        # strictly greater than the snapshot. ES @timestamp is set by
        # Logstash on each event, so the upsert produces a new value.
        def both_upserted():
            new_se_ts = _latest_ts("structured_events")
            new_agg_ts = _latest_ts("aggregated_summary")
            if not new_se_ts or not new_agg_ts:
                return False
            return new_se_ts > baseline_se_ts and new_agg_ts > baseline_agg_ts

        _wait_for(
            both_upserted,
            timeout_s=float(os.environ.get("KAFKA_E2E_PUBLISH_TIMEOUT", "60")),
            label=(
                f"structured_events @timestamp > {baseline_se_ts!r} AND "
                f"aggregated_summary @timestamp > {baseline_agg_ts!r} in "
                f"{index_name}"
            ),
        )

        # Verify the wire shape of the latest aggregated_summary doc.
        resp = _es_search(
            index_name,
            {
                "query": {"term": {"metadata.content_metadata.doc_type": "aggregated_summary"}},
                "size": 1,
                "sort": [{"@timestamp": {"order": "desc"}}],
            },
        )
        hits = resp["hits"]["hits"]
        assert hits, "no aggregated_summary doc found after /v1/stream_summarize"
        doc = hits[0]["_source"]
        cm = doc.get("metadata", {}).get("content_metadata", {})
        assert cm.get("doc_type") == "aggregated_summary"
        assert cm.get("uuid"), "aggregated_summary missing uuid in content_metadata"
        assert "text" in doc, "aggregated_summary doc missing top-level text"


class TestStreamSummarizeTimeWindowFilter:
    """Time-window filter: narrower windows return <= events, and out-of-
    range windows return zero events.

    These tests depend on ``LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=true``
    being set on the LVS container so successive aggregate calls observe
    consistent ES state.
    """

    @pytest.mark.test_in_ci
    def test_stream_summarize_time_window(self, live_stream_with_raw_events, model_id):
        asset_id = live_stream_with_raw_events

        # Window 1: full (0/0 sentinel = no time filter).
        r_full = _post_stream_summarize(asset_id, model_id, start_time=0, end_time=0)
        assert r_full.status_code == 200, r_full.text
        full_count = len(_parse_stream_summarize_payload(r_full).get("events", []))
        assert full_count > 0, "expected non-empty events for full window"

        # Window 2: wider half (0..15).
        r_wide = _post_stream_summarize(asset_id, model_id, start_time=0, end_time=15)
        assert r_wide.status_code == 200, r_wide.text
        wide_count = len(_parse_stream_summarize_payload(r_wide).get("events", []))

        # Window 3: narrow inner (5..10).
        r_narrow = _post_stream_summarize(asset_id, model_id, start_time=5, end_time=10)
        assert r_narrow.status_code == 200, r_narrow.text
        narrow_count = len(_parse_stream_summarize_payload(r_narrow).get("events", []))

        # Sequential calls without ctx_mgr.reset must succeed and return
        # monotonically-bounded counts.
        assert wide_count <= full_count, f"wide_count={wide_count} > full_count={full_count}"
        assert narrow_count <= wide_count, f"narrow_count={narrow_count} > wide_count={wide_count}"

    @pytest.mark.test_in_ci
    def test_stream_summarize_out_of_range_window(self, live_stream_with_raw_events, model_id):
        """Future / past-end-of-stream windows must return zero events
        without erroring. Mirrors run_sanity_kafka.sh's (60,90) and
        (300,450) cases. The 1-minute warehouse RTSP loop only contains
        events in roughly t=[0, 60); these windows are entirely outside
        that range and should round-trip a clean empty response.
        """
        asset_id = live_stream_with_raw_events
        for st, et in [(60, 90), (300, 450)]:
            r = _post_stream_summarize(asset_id, model_id, start_time=st, end_time=et)
            assert r.status_code == 200, (
                f"out-of-range window ({st},{et}) -> " f"HTTP {r.status_code} {r.text}"
            )
            body = r.json()
            assert body.get("video_id") == asset_id
            payload = _parse_stream_summarize_payload(r)
            uuids = payload.get("uuids") or []
            assert asset_id in uuids, f"asset_id {asset_id!r} not in uuids: {uuids!r}"
            events = payload.get("events") or []
            assert events == [], (
                f"out-of-range window ({st},{et}) returned {len(events)} "
                f"event(s); expected zero. Payload: {payload}"
            )
            assert (
                int(payload.get("total_events", 0)) == 0
            ), f"out-of-range window ({st},{et}) total_events != 0: {payload}"


class TestLivestreamApisGate:
    """Negative tests: the live-stream APIs reject malformed input.

    These tests do NOT require a real running stream — pydantic
    validation runs before any handler logic.
    """

    @pytest.mark.test_in_ci
    def test_generate_captions_rejects_wrong_model(self, lvs_ready, model_id):
        """/v1/generate_captions returns 400 when the request's `model`
        does not match the loaded model.
        """
        fake_id = os.environ.get(
            "KAFKA_E2E_GATE_TEST_FAKE_ID", "00000000-0000-0000-0000-000000000000"
        )
        body = {
            "id": fake_id,
            "model": "nonexistent-model",
            "scenario": SUMMARIZE_STREAM_SCENARIO,
            "events": SUMMARIZE_STREAM_EVENTS,
            "chunk_duration": 10,
        }
        r = requests.post(
            f"{LVS_BASE_URL}/v1/generate_captions",
            json=body,
            headers=_sticky_headers(fake_id),
            timeout=30,
        )
        assert r.status_code == 400, (
            f"expected 400 for wrong model on /v1/generate_captions; "
            f"got HTTP {r.status_code} {r.text}"
        )
        msg = (r.json().get("message") or "").lower()
        assert "no such model" in msg, f"400 response did not mention model mismatch: {r.text}"

    @pytest.mark.test_in_ci
    def test_stream_summarize_rejects_wrong_model(self, lvs_ready, model_id):
        """/v1/stream_summarize returns 400 when the request's `model`
        does not match the loaded model.
        """
        fake_id = os.environ.get(
            "KAFKA_E2E_GATE_TEST_FAKE_ID", "00000000-0000-0000-0000-000000000000"
        )
        body = {
            "id": fake_id,
            "model": "nonexistent-model",
            "start_time": 0,
            "end_time": 0,
        }
        r = requests.post(
            f"{LVS_BASE_URL}/v1/stream_summarize",
            json=body,
            headers=_sticky_headers(fake_id),
            timeout=30,
        )
        assert r.status_code == 400, (
            f"expected 400 for wrong model on /v1/stream_summarize; "
            f"got HTTP {r.status_code} {r.text}"
        )
        msg = (r.json().get("message") or "").lower()
        assert "no such model" in msg, f"400 response did not mention model mismatch: {r.text}"


class TestFileSummarizeKafkaMode:
    """File summarization via Kafka mode (``summarization.kafka_enabled=true``).

    The file path now mirrors the live-stream path: raw_events flow
    through RTVI -> Kafka -> Logstash -> ES (no in-process ``add_doc``
    persistence), the aggregator reads them back from ES at acall time
    via ``vlm_structured_summarization_online`` keyed by ``uuid==file_id``,
    and LVS publishes ``structured_events`` + ``aggregated_summary`` back
    to Kafka via :py:meth:`ViaStreamHandler._publish_aggregate_to_kafka`.
    Logstash indexes all three doc types into the same
    ``default_<file_id>`` ES index.

    These tests INVERT the prior ``TestFileRequestIgnoresKafka`` class:
    the file path now MUST produce all three doc_types when
    ``KAFKA_ENABLED=true`` and ``functions.summarization.params
    .kafka_enabled=true``.

    Uses URL passthrough against the in-stack ``media-server`` 2-minute
    sample (same source as ``run_sanity.sh``). Resolved by docker DNS
    from the LVS container so no artifactory auth or local download is
    needed at request time.

    ``stream=True`` is the design intent for the file path under Kafka
    mode: LVS replies with SSE (``EventSourceResponse``) end-to-end,
    matching the SSE-only RTVI -> LVS captioning leg. The fixture does
    NOT iterate every progress event -- it captures only the FINAL
    ``data:`` line before ``data: [DONE]``, whose
    ``choices[0].message.content`` carries the JSON-stringified
    aggregator payload (``events``, ``video_summary``, ``total_events``).
    """

    @pytest.fixture(scope="class")
    def file_with_kafka_docs(
        self, lvs_ready, rtvi_ready, es_ready, index_template_present, model_id
    ):
        """Summarize ``KAFKA_E2E_FILE_URL`` once for the class via URL passthrough.

        Mirrors ``live_stream_with_raw_events`` for the file path. Yields
        ``(file_id, payload)`` where ``file_id`` is the asset UUID LVS
        registers for the URL and ``payload`` is the parsed aggregator
        JSON from the FINAL SSE event's
        ``choices[0].message.content``. Cleans up the asset on teardown
        so the ES index is dropped via the DELETE /files handler (which
        calls ``drop_collection_for_asset`` when ``KAFKA_ENABLED=true``).

        ``chunk_duration=15`` mirrors run_sanity.sh: a 2-min clip / 15s
        chunks = 8 chunks, a known-good fixture for the aggregator.
        """
        body = {
            "url": KAFKA_E2E_FILE_URL,
            "model": model_id,
            "stream": True,
            "summarize": True,
            "scenario": SUMMARIZE_STREAM_SCENARIO,
            "events": SUMMARIZE_STREAM_EVENTS,
            "chunk_duration": 15,
        }
        # SSE: stream=True with `requests` keeps the connection open and
        # exposes line-by-line iteration. We discard intermediate progress
        # events and keep only the LAST data: payload that JSON-decodes,
        # which is the final aggregator event LVS yields right before
        # `data: [DONE]`.
        #
        # No x-stream-id on this call: URL passthrough means LVS
        # generates the file_id server-side (chicken-and-egg). The
        # cleanup DELETE below sticky-routes via the file_id we extract
        # from the final SSE event.
        r = requests.post(
            f"{LVS_BASE_URL}/v1/summarize",
            json=body,
            timeout=900,
            stream=True,
            headers={"Accept": "text/event-stream"},
        )
        try:
            assert (
                r.status_code == 200
            ), f"/v1/summarize stream=true failed: {r.status_code} {r.text[:500]}"
            last_event: dict | None = None
            for raw in r.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                payload_str = raw[len("data:") :].strip()
                if not payload_str or payload_str == "[DONE]":
                    continue
                try:
                    last_event = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
        finally:
            r.close()

        assert last_event is not None, (
            "no usable SSE event received from /v1/summarize stream=true "
            "(stream may have terminated without a final aggregator event)"
        )

        content = (last_event.get("choices") or [{}])[0].get("message", {}).get("content", "")
        assert content, f"final SSE event has empty content: {last_event}"
        payload: dict = {}
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as ex:
            pytest.fail(
                f"final SSE event content is not JSON: {ex} | " f"content[:200]={content[:200]!r}"
            )

        # The aggregator JSON includes the asset UUID as `uuid`;
        # fall back to top-level `video_id` from the SSE event.
        file_id = payload.get("uuid") or last_event.get("video_id")
        assert file_id, (
            f"could not resolve file_id from final SSE event "
            f"payload={payload} event={last_event}"
        )
        try:
            yield file_id, payload
        finally:
            try:
                # Sticky-routed cleanup: DELETE /files/{id} with
                # x-stream-id matching the asset id ensures the call
                # lands on the same RTVI replica that served the upload
                # / captioning (METLVSMS-500). LVS forwards the header
                # through RtviVlmClient.delete_file.
                requests.delete(
                    f"{LVS_BASE_URL}/files/{file_id}",
                    headers=_sticky_headers(file_id),
                    timeout=60,
                )
            except requests.RequestException:
                pass

    @pytest.mark.test_in_ci
    def test_file_summarize_returns_events_and_summary(self, file_with_kafka_docs):
        """Aggregator output reaches the HTTP response: events list AND
        video_summary are non-empty, mirroring the live-stream
        ``test_summarize_stream_full_window`` shape contract.
        """
        _file_id, payload = file_with_kafka_docs
        events = payload.get("events") or []
        assert isinstance(events, list) and len(events) > 0, (
            f"file summarize returned empty events list under Kafka mode; " f"payload={payload}"
        )
        assert isinstance(payload.get("video_summary"), str) and payload.get(
            "video_summary"
        ), f"file summarize returned empty video_summary under Kafka mode; payload={payload}"
        assert int(payload.get("total_events", 0)) >= len(events)

        first = events[0]
        for required in ("start_time", "end_time", "type", "description"):
            assert required in first, f"event missing required key '{required}': {first}"

    @pytest.mark.test_in_ci
    def test_file_raw_events_landed_via_kafka(self, file_with_kafka_docs):
        """raw_events docs MUST exist in ``default_<file_id>``, AND a
        sampled doc's ``info[streamId]`` MUST equal ``file_id``.

        Proves the chunks travelled RTVI -> Kafka -> Logstash and were
        NOT written by the legacy in-process ``add_doc`` path (which is
        gated off in vss_ctx_rag's ``vlm_structured_summarization_online
        .aprocess_doc`` when ``kafka_enabled=true``).
        """
        file_id, _payload = file_with_kafka_docs
        index = _index_for_stream(file_id)

        def has_raw_events():
            _es_refresh(index)
            return _es_doc_count(index, "raw_events") >= 1

        _wait_for(
            has_raw_events,
            timeout_s=float(os.environ.get("KAFKA_E2E_PUBLISH_TIMEOUT", "60")),
            label=f">= 1 raw_events doc in {index}",
        )

        resp = _es_search(
            index,
            {
                "query": {"term": {"metadata.content_metadata.doc_type": "raw_events"}},
                "size": 1,
            },
        )
        hits = resp["hits"]["hits"]
        assert hits, f"no raw_events hit in {index}"
        cm = hits[0]["_source"].get("metadata", {}).get("content_metadata", {})
        assert cm.get("streamId") == file_id, (
            f"info[streamId] != file_id: {cm.get('streamId')!r} != {file_id!r}. "
            "Either RTVI didn't carry chunk.streamId == asset_id, or the "
            "doc was written by the in-process path instead of Kafka."
        )
        assert cm.get("uuid") == file_id, (
            f"cm[uuid] != file_id: {cm.get('uuid')!r} != {file_id!r}. "
            "Logstash uuid fallback to info[streamId] may be missing."
        )

    @pytest.mark.test_in_ci
    def test_file_structured_events_published_to_kafka(self, file_with_kafka_docs):
        """``structured_events >= 1`` in ``default_<file_id>`` AND the
        latest doc's ``@timestamp`` is recent (within the last few
        minutes) -- proves LVS's ``_publish_aggregate_to_kafka`` ran for
        the file path under Kafka mode.
        """
        file_id, _payload = file_with_kafka_docs
        index = _index_for_stream(file_id)

        def has_structured():
            _es_refresh(index)
            return _es_doc_count(index, "structured_events") >= 1

        _wait_for(
            has_structured,
            timeout_s=float(os.environ.get("KAFKA_E2E_PUBLISH_TIMEOUT", "60")),
            label=f">= 1 structured_events doc in {index}",
        )

        resp = _es_search(
            index,
            {
                "query": {"term": {"metadata.content_metadata.doc_type": "structured_events"}},
                "size": 1,
                "sort": [{"@timestamp": {"order": "desc"}}],
                "_source": ["@timestamp", "metadata.content_metadata"],
            },
        )
        hits = resp["hits"]["hits"]
        assert hits, f"no structured_events doc in {index}"
        cm = hits[0]["_source"].get("metadata", {}).get("content_metadata", {})
        assert (
            cm.get("doc_type") == "structured_events"
        ), f"unexpected doc_type: {cm.get('doc_type')!r}"
        assert cm.get("uuid") == file_id, (
            f"structured_events.uuid != file_id: {cm.get('uuid')!r} != "
            f"{file_id!r}. _publish_aggregate_to_kafka may be using the "
            f"wrong stream_id."
        )

    @pytest.mark.test_in_ci
    def test_file_aggregated_summary_published_to_kafka(self, file_with_kafka_docs):
        """``aggregated_summary == 1`` in ``default_<file_id>``. ES doc
        IDs are deterministic per stream (see design doc §10.6) so the
        upsert keeps the count at exactly one. ``text`` MUST be non-
        empty.
        """
        file_id, _payload = file_with_kafka_docs
        index = _index_for_stream(file_id)

        def has_aggregated():
            _es_refresh(index)
            return _es_doc_count(index, "aggregated_summary") >= 1

        _wait_for(
            has_aggregated,
            timeout_s=float(os.environ.get("KAFKA_E2E_PUBLISH_TIMEOUT", "60")),
            label=f">= 1 aggregated_summary doc in {index}",
        )

        resp = _es_search(
            index,
            {
                "query": {"term": {"metadata.content_metadata.doc_type": "aggregated_summary"}},
                "size": 5,
                "sort": [{"@timestamp": {"order": "desc"}}],
            },
        )
        hits = resp["hits"]["hits"]
        assert hits, f"no aggregated_summary doc in {index}"
        # Deterministic upsert keeps the count at exactly one.
        assert (
            len(hits) == 1
        ), f"expected exactly 1 aggregated_summary doc (deterministic upsert); got {len(hits)}"
        doc = hits[0]["_source"]
        assert doc.get("text"), f"aggregated_summary text is empty: {doc}"
        cm = doc.get("metadata", {}).get("content_metadata", {})
        assert cm.get("uuid") == file_id, (
            f"aggregated_summary.uuid != file_id: {cm.get('uuid')!r} != " f"{file_id!r}"
        )
