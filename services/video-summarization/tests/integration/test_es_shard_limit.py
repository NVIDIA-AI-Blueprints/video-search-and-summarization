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

"""End-to-end regression test for ES shard exhaustion.

NO MOCKS. Runs against the real BlueprintBuilderGenerated stack with
``cluster.max_shards_per_node`` deliberately set low (default 2) so the
cap is easy to hit. Two scenarios live in this module, gated by
pytest markers so the CI helper can run them against two SEPARATE
compose stacks (one per mode):

  * ``retain_mode``  — ``LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=true``.
    Each completed summarize KEEPS its ``default_<file_id>`` index.
    Sequential summarizes succeed up to the cap, after which subsequent
    requests return HTTP 503 with the classified shard-limit message.

  * ``drop_mode``    — ``LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=false``
    (or unset). Each completed summarize drops its
    ``default_<file_id>`` index in
    ``check_status_remove_req_id`` (the post-completion hook added in
    A2-ii). Many sequential summarizes (well past the cap) all succeed
    because the cluster reclaims the shard between requests.

Why the modes can't share a stack: ``LVS_DISABLE_DB_RESET_ON_REQUEST_DONE``
is read at LVS container start. Switching it requires a stack restart.
The CI helper handles the lifecycle; this module just selects the
right scenario via ``-m retain_mode`` or ``-m drop_mode``.

Set ``ES_SHARD_LIMIT_TEST=1`` in the runner env to enable. Skipped
otherwise so the test does not run by accident in CI stages that
forgot to provision a low-cap ES.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, List

import pytest
import requests

logger = logging.getLogger(__name__)

# -- Skip gate ---------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    os.environ.get("ES_SHARD_LIMIT_TEST", "0") != "1",
    reason=(
        "Set ES_SHARD_LIMIT_TEST=1 to enable the ES shard-limit regression "
        "test. Requires a BlueprintBuilderGenerated compose stack brought up "
        "with ES_MAX_SHARDS_PER_NODE=<low value> and "
        "LVS_DISABLE_DB_RESET_ON_REQUEST_DONE matching the test marker "
        "(true for retain_mode, false for drop_mode). Drive via the CI "
        "helper runEsShardLimitTest."
    ),
)

# -- Config ------------------------------------------------------------------

LVS_PORT = int(os.environ.get("LVS_BACKEND_PORT") or os.environ.get("BACKEND_PORT") or 38111)
LVS_BASE_URL = os.environ.get("LVS_BASE_URL", f"http://localhost:{LVS_PORT}").rstrip("/")

ES_HOST = os.environ.get("ES_HOST", "localhost")
ES_PORT = os.environ.get("ES_PORT", "9200")
ES_BASE_URL = os.environ.get("ES_BASE_URL", f"http://{ES_HOST}:{ES_PORT}").rstrip("/")

# CI uses Artifactory URLs directly and does not enable the compose media profile.
SHARD_LIMIT_FILE_URL = os.environ.get(
    "SHARD_LIMIT_FILE_URL",
    "https://artifactory.nvidia.com/artifactory/sw-ds-generic-bld-local/lmm/streams/warehouse_gopro_1m_720.mp4",  # noqa: E501
)

# Configured cap on the elasticsearch container. The CI helper sets this
# to 2 so the cap is hit after very few summarizes; the test reads it back
# from env so smaller / larger overrides keep working without code edits.
ES_MAX_SHARDS = int(os.environ.get("ES_MAX_SHARDS_PER_NODE", "2"))

# Drop-mode runs N >> ES_MAX_SHARDS sequential summarizes to prove the
# index-drop path keeps the cluster shard pool drained indefinitely.
# Default 4x the cap is enough headroom to demonstrate reclamation
# without ballooning runtime.
DROP_MODE_REQUESTS = int(os.environ.get("ES_SHARD_LIMIT_DROP_REQUESTS", str(ES_MAX_SHARDS * 4)))

# Per-summarize HTTP timeout. Each summarize is bounded by RTVI VLM
# captioning latency on the 2-min mp4 (~1-3 min). Generous default to
# absorb cold-start variance.
SUMMARIZE_TIMEOUT = int(os.environ.get("ES_SHARD_LIMIT_SUMMARIZE_TIMEOUT", "900"))

# Scenario / events used to template the VLM caption prompt — same
# warehouse-safety wording the other integration tests use so the
# captioning job stays known-good.
SCENARIO = "warehouse safety monitoring"
EVENTS = [
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


def _wait_for(condition_fn, timeout_s: float, poll_s: float = 2.0, label: str = ""):
    """Poll ``condition_fn`` until truthy or the timeout expires."""
    deadline = time.time() + timeout_s
    last_value: Any = None
    last_exc: Exception | None = None
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


def _resolve_model_id() -> str:
    r = requests.get(f"{LVS_BASE_URL}/models", timeout=15)
    r.raise_for_status()
    return r.json()["data"][0]["id"]


def _index_for_id(asset_id: str) -> str:
    return "default_" + asset_id.replace("-", "_")


def _es_index_exists(index: str) -> bool:
    try:
        return requests.head(f"{ES_BASE_URL}/{index}", timeout=10).status_code == 200
    except requests.RequestException:
        return False


def _es_active_shard_count() -> int:
    """Read ``active_shards`` from the ES cluster stats. Used by drop-mode
    tests to assert the shard pool is reclaimed between summarize calls.
    """
    r = requests.get(f"{ES_BASE_URL}/_cluster/stats", timeout=15)
    r.raise_for_status()
    return int(r.json().get("indices", {}).get("shards", {}).get("total", 0))


def _post_file_summarize(model_id: str) -> requests.Response:
    """POST /v1/summarize with the in-stack mp4 URL.

    Uses ``stream=False`` (the bug's stress harness shape) so the HTTP
    response carries the full ``CompletionResponse`` synchronously and
    ``status_code`` reflects the success / failure of the summarize call
    end-to-end. No ``id`` field — LVS generates a fresh asset_id server
    side, which is what we need for "create a new index per request".
    """
    body = {
        "url": SHARD_LIMIT_FILE_URL,
        "model": model_id,
        "stream": False,
        "summarize": True,
        "scenario": SCENARIO,
        "events": EVENTS,
        "chunk_duration": 15,
    }
    return requests.post(
        f"{LVS_BASE_URL}/v1/summarize",
        json=body,
        timeout=SUMMARIZE_TIMEOUT,
    )


def _extract_file_id(resp: requests.Response) -> str | None:
    """Extract the asset / file id from a successful CompletionResponse.

    LVS exposes it both as the top-level ``video_id`` and (for stream=true)
    inside ``choices[0].message.content``'s aggregator JSON. For stream=false
    we read ``video_id`` directly.
    """
    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError):
        return None
    return body.get("video_id") or body.get("id")


# -- Fixtures (shared across both modes) ------------------------------------


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
def es_ready():
    """Wait until Elasticsearch reports cluster health yellow+."""

    def ready():
        try:
            return (
                requests.get(
                    f"{ES_BASE_URL}/_cluster/health?wait_for_status=yellow&timeout=5s",
                    timeout=10,
                ).status_code
                == 200
            )
        except requests.RequestException:
            return False

    _wait_for(ready, timeout_s=180, label=f"elasticsearch health at {ES_BASE_URL}")


@pytest.fixture(scope="module")
def model_id(lvs_ready) -> str:
    return _resolve_model_id()


# -- Test 1: retain mode -- shard cap fills, 503 returned --------------------


@pytest.mark.es_shard_limit
@pytest.mark.retain_mode
class TestEsShardLimitRetainMode:
    """``LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=true`` keeps every
    completed summarize's ``default_<file_id>`` index, so the cluster
    shard pool monotonically grows. Once it reaches
    ``cluster.max_shards_per_node``, any subsequent /v1/summarize that
    attempts to create a new index must surface a classified 503 with
    the generic shard-limit message — not hang, not return 200 with an
    empty summary, and not 500.

    Reproduction shape from the original 12-hour stress run:
      * 8138 successful requests filled the default cap (1000).
      * Subsequent requests hung until the 3600s client timeout.
      * VLM-side query state stayed at "processing 0%".

    This test compresses that into ``ES_MAX_SHARDS_PER_NODE+1`` requests
    by setting the cap to 2 (configurable via env). PASS criteria:
      * First ES_MAX_SHARDS requests return HTTP 200.
      * Request ES_MAX_SHARDS+1 returns HTTP 503.
      * The 503 response carries the classified shard-limit message
        (not "max_shards_per_node = 2" — that would leak internals).
    """

    @pytest.fixture(scope="class", autouse=True)
    def gate_assertion(self, lvs_ready, es_ready):
        """Defensive check — if the harness misconfigured the stack,
        fail loudly here rather than letting the assertions below
        misattribute the failure mode."""
        gate = os.environ.get("LVS_DISABLE_DB_RESET_ON_REQUEST_DONE", "false").lower()
        if gate not in ("true", "1"):
            pytest.fail(
                "TestEsShardLimitRetainMode requires the lvs container to "
                "have been started with LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=true. "
                f"Currently observed value (from pytest env): {gate!r}. "
                "The CI helper runEsShardLimitTest must export it on the "
                "compose call, not just in the pytest container env."
            )

    @pytest.mark.test_in_ci
    def test_summarize_succeeds_until_cap(self, model_id):
        """First ES_MAX_SHARDS sequential summarizes succeed, each
        leaving its ``default_<file_id>`` index behind.
        """
        successful_ids: List[str] = []
        for i in range(ES_MAX_SHARDS):
            r = _post_file_summarize(model_id)
            assert r.status_code == 200, (
                f"Retain mode: request {i + 1}/{ES_MAX_SHARDS} unexpectedly "
                f"failed with HTTP {r.status_code}: {r.text[:500]}"
            )
            file_id = _extract_file_id(r)
            assert file_id, f"request {i + 1} returned no file_id: {r.text[:500]}"
            successful_ids.append(file_id)

        # Each successful summarize must have left its index in place.
        # Refresh the cluster state then assert ALL indices exist.
        for file_id in successful_ids:
            assert _es_index_exists(_index_for_id(file_id)), (
                f"Retain mode: index for {file_id} was unexpectedly dropped — "
                "did LVS_DISABLE_DB_RESET_ON_REQUEST_DONE leak as false?"
            )

    @pytest.mark.test_in_ci
    def test_request_past_cap_returns_503_with_shard_limit_message(self, model_id):
        """The (ES_MAX_SHARDS + 1)-th summarize must return HTTP 503
        with the classified shard-limit user message. The previous test
        filled the cap; this one only fires the +1 request.
        """
        r = _post_file_summarize(model_id)
        assert r.status_code == 503, (
            f"Retain mode: request past cap returned HTTP {r.status_code}, "
            f"expected 503. Body: {r.text[:500]}"
        )
        # The classified user message MUST surface in the response body
        # (FastAPI exception handler renders ViaException -> JSON with
        # message field).
        body_text = r.text.lower()
        assert "shard limit exceeded" in body_text, (
            f"Retain mode: 503 response did not include the classified shard-limit "
            f"message. Body: {r.text[:1000]}"
        )
        # Must NOT leak the actual configured cap into the user-facing message.
        assert str(ES_MAX_SHARDS) not in body_text or "max_shards_per_node" not in body_text, (
            f"Retain mode: 503 response leaked the configured shard cap "
            f"({ES_MAX_SHARDS}) into the user-facing message: {r.text[:500]}"
        )

    @pytest.mark.test_in_ci
    def test_subsequent_request_also_503(self, model_id):
        """A second request past the cap must also 503 — the failure mode
        is deterministic, not flaky / transient. Without this guard, the
        bug's "hang for 3600s" symptom could regress as a one-off 503
        followed by silent 500 / hangs.
        """
        r = _post_file_summarize(model_id)
        assert r.status_code == 503, (
            f"Retain mode: second past-cap request returned HTTP "
            f"{r.status_code}, expected 503 (deterministic failure). "
            f"Body: {r.text[:500]}"
        )


# -- Test 2: drop mode -- per-request index drop keeps cluster healthy ------


@pytest.mark.es_shard_limit
@pytest.mark.drop_mode
class TestEsShardLimitDropMode:
    """``LVS_DISABLE_DB_RESET_ON_REQUEST_DONE`` unset / ``false`` runs
    the file-path completion handler's index-drop path
    (A2-ii in the design plan). After each summarize completes,
    ``check_status_remove_req_id`` calls
    ``drop_collection_for_asset(file_id, force_legacy=True)`` so the
    per-file index is binned and its shard returned to the cluster pool.

    The cluster shard count therefore stays at ~1 (the in-flight
    request's index) regardless of how many summarizes have run. We
    fire ``DROP_MODE_REQUESTS`` (default 4 * ES_MAX_SHARDS) sequential
    summarizes at a low ES cap and assert ALL succeed.
    """

    @pytest.fixture(scope="class", autouse=True)
    def gate_assertion(self, lvs_ready, es_ready):
        gate = os.environ.get("LVS_DISABLE_DB_RESET_ON_REQUEST_DONE", "false").lower()
        if gate in ("true", "1"):
            pytest.fail(
                "TestEsShardLimitDropMode requires the lvs container to have "
                "been started with LVS_DISABLE_DB_RESET_ON_REQUEST_DONE unset "
                "or 'false'. Currently observed value (from pytest env): "
                f"{gate!r}. The CI helper runEsShardLimitTest must NOT export "
                "it as 'true' for the drop-mode phase."
            )

    @pytest.mark.test_in_ci
    def test_many_summarizes_succeed_below_cap(self, model_id):
        """Fire DROP_MODE_REQUESTS (>> ES_MAX_SHARDS) sequential
        summarizes. ALL must return HTTP 200. Between requests, the
        previous request's index must already be gone (the drop runs
        synchronously inside ``check_status_remove_req_id`` after
        ``wait_for_request_done`` returns).
        """
        previous_file_id: str | None = None

        for i in range(DROP_MODE_REQUESTS):
            r = _post_file_summarize(model_id)
            assert r.status_code == 200, (
                f"Drop mode: request {i + 1}/{DROP_MODE_REQUESTS} failed with "
                f"HTTP {r.status_code} (cluster shard pool should not have filled "
                f"because each request drops its own index on completion). "
                f"Body: {r.text[:500]}"
            )
            file_id = _extract_file_id(r)
            assert file_id, f"request {i + 1} returned no file_id: {r.text[:500]}"

            # The PREVIOUS request's index must be gone by now (drop runs
            # synchronously in check_status_remove_req_id, which the HTTP
            # handler invokes BEFORE returning).
            if previous_file_id is not None:
                prev_index = _index_for_id(previous_file_id)
                assert not _es_index_exists(prev_index), (
                    f"Drop mode: previous request's index {prev_index} still "
                    f"exists after the next request started — A2-ii's drop "
                    f"hook did not fire. Cluster shard pool will leak."
                )

            previous_file_id = file_id

    @pytest.mark.test_in_ci
    def test_active_shards_stay_below_cap(self, model_id):
        """At the end of a many-request run, the cluster's active shard
        count MUST be far below ``ES_MAX_SHARDS_PER_NODE`` (the cap was
        only 2 in the harness; healthy steady state is ~1). Run a couple
        more summarizes and probe ``_cluster/stats`` between them.
        """
        # Allow a small tolerance for system indices (e.g. ES management
        # indices) — assert active_shards stays at most cap, not strictly
        # less, so the test is robust to ES internal indexing.
        for i in range(2):
            r = _post_file_summarize(model_id)
            assert r.status_code == 200, (
                f"Drop mode: probe summarize {i + 1} failed with HTTP "
                f"{r.status_code}: {r.text[:500]}"
            )
            shards = _es_active_shard_count()
            assert shards <= ES_MAX_SHARDS, (
                f"Drop mode: cluster reports {shards} active shards, "
                f"which exceeds the configured cap {ES_MAX_SHARDS}. The "
                "drop-on-completion hook is failing to keep the pool "
                "drained."
            )
