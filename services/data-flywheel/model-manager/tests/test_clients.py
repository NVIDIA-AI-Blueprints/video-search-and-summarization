# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for the AOI Inference / AOI Client HTTP clients."""

from unittest.mock import patch

import pytest
import requests

from src.adapters.aoi.client import AOIClientClient
from src.adapters.aoi.inference import AOIInferenceClient, BuildHandle
from src.clients.http import error_code_of, list_field, request_with_retries
from src.config import settings
from src.core.errors import ModelManagerError


class FakeResponse:
    """Minimal stand-in for ``requests.Response`` used to mock HTTP calls."""

    def __init__(self, status_code: int, json_body=None, content: bytes = b"", text: str = ""):
        self.status_code = status_code
        self._json = json_body
        self.content = content
        self.text = text
        self.closed = False

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json

    def close(self):
        self.closed = True


# ------------------------------------------------------------- request_with_retries
def test_retry_closes_discarded_5xx_response_before_retrying():
    """A 5xx response discarded mid-retry must be closed so its streamed
    (stream=True) connection is released back to the pool instead of leaking."""
    bad = FakeResponse(503)
    good = FakeResponse(200, {"ok": True})
    with (
        patch("src.clients.http.requests.request", side_effect=[bad, good]) as req,
        patch("src.clients.http.sleep"),
    ):
        result = request_with_retries("GET", "http://x/download", timeout=1.0, stream=True)

    assert result is good
    assert bad.closed is True, "discarded 5xx response was not closed (connection leak)"
    assert good.closed is False
    assert req.call_count == 2
    assert req.call_args_list[0].kwargs["stream"] is True


def test_timeout_exhaustion_reports_that_it_timed_out():
    """The exhaustion error distinguishes a timeout from a refused connection.

    A caller that started expensive work needs the difference: a refused
    connection never landed, while a timeout leaves the request's fate unknown.
    """
    with (
        patch("src.clients.http.requests.request", side_effect=requests.Timeout("slow")),
        patch("src.clients.http.sleep"),
    ):
        with pytest.raises(ModelManagerError) as excinfo:
            request_with_retries("GET", "http://x", timeout=1.0)
    assert excinfo.value.details == {"timed_out": True}

    with (
        patch("src.clients.http.requests.request", side_effect=requests.ConnectionError("no")),
        patch("src.clients.http.sleep"),
    ):
        with pytest.raises(ModelManagerError) as excinfo:
            request_with_retries("GET", "http://x", timeout=1.0)
    assert excinfo.value.details == {"timed_out": False}


# ------------------------------------------------------------------ error codes
def test_error_code_of_reads_the_nested_error_object():
    """The contract nests the code, and every 409/404 branch dispatches on it.

    A shape this cannot parse reads as "no code", which sends a recoverable
    conflict down the terminal generic-failure path.
    """
    assert (
        error_code_of(FakeResponse(409, {"error": {"code": "idempotency_in_progress"}}))
        == "idempotency_in_progress"
    )


def test_error_code_of_still_reads_the_legacy_flat_string():
    """Older AOI builds put a bare string under the same key."""
    assert (
        error_code_of(FakeResponse(409, {"error": "load_model_already_loaded"}))
        == "load_model_already_loaded"
    )


@pytest.mark.parametrize(
    "body",
    [None, {}, {"error": None}, {"error": {}}, {"error": {"code": 7}}, {"error": 7}, ["nope"]],
)
def test_error_code_of_returns_none_for_shapes_without_a_code(body):
    assert error_code_of(FakeResponse(409, body)) is None


# --------------------------------------------------------------- AOI Inference
def test_load_async_posts_typed_body_with_json_content_type():
    """The body must be flat, explicit about ``version``, and typed as JSON.

    All three matter to the same failure: this endpoint reads typed fields, so a
    body it cannot parse — or one without ``version`` — falls back to *every*
    staged version and rebuilds the whole model instead of the one asked for.
    """
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(202, {"build_id": "b-1", "phase": "building"})
        handle = client.load_async("m", "1", idempotency_key="k-1")

    method, url = req.call_args.args
    assert method == "POST"
    assert url == "http://inf/v1/models/load"
    assert req.call_args.kwargs["json"] == {
        "model_name": "m",
        "version": "1",
        "idempotency_key": "k-1",
    }
    assert req.call_args.kwargs["headers"]["Content-Type"] == "application/json"
    assert req.call_args.kwargs["params"] == {"wait": 0}
    assert handle == BuildHandle(phase="building", build_id="b-1")


def test_load_async_202_is_not_reported_as_loaded():
    """A 202 means a build STARTED. Reading it as success reports a model that
    is minutes away from being able to serve."""
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(202, {"build_id": "b-9", "phase": "building"})
        assert client.load_async("m", "1").phase == "building"


def test_load_async_200_is_terminal_success():
    """The synchronous path, a replayed idempotent result, and an already-READY
    version all come back 200 with nothing left to poll."""
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, {"model_name": "m", "version": "1"})
        assert client.load_async("m", "1") == BuildHandle(phase="succeeded")


def test_load_async_sync_mode_omits_wait_and_uses_the_build_budget(monkeypatch):
    """With the async toggle off the POST blocks for the whole conversion, so it
    borrows the build budget instead of the 30s request timeout."""
    monkeypatch.setattr(settings, "load_async", False)
    monkeypatch.setattr(settings, "build_poll_timeout", 480.0)
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, {"model_name": "m", "version": "1"})
        assert client.load_async("m", "1").phase == "succeeded"
    assert req.call_args.kwargs["params"] is None
    assert req.call_args.kwargs["timeout"] == 480.0


def test_load_async_409_idempotency_in_progress_is_a_re_attach():
    """The nested-error 409 means "your build is already running", which is the
    happy path of a retry — not a failure, and not a second conversion."""
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(409, {"error": {"code": "idempotency_in_progress"}})
        handle = client.load_async("m", "1", idempotency_key="k-1")
    assert handle == BuildHandle(phase="building", build_id=None)


def test_load_async_does_not_retry_an_ambiguous_timeout():
    """A timed-out load POST may well have been accepted.

    Replaying it inside the backoff window asks a backend that is already
    converting to convert again — one client-side timeout becoming several
    concurrent engine builds. The ambiguity is reported instead, for the caller to
    resolve by asking what is actually in flight.
    """
    client = AOIInferenceClient(base_url="http://inf")
    with (
        patch("src.clients.http.requests.request", side_effect=requests.Timeout("slow")) as req,
        patch("src.clients.http.sleep") as slept,
    ):
        handle = client.load_async("m", "1", idempotency_key="k-1")

    assert handle == BuildHandle(phase="unknown")
    assert req.call_count == 1, "the load POST was replayed after a timeout"
    slept.assert_not_called()


def test_a_connection_error_on_load_is_still_retried():
    """Unlike a timeout, a refused connection never reached the backend, so there
    is no in-flight build for a retry to duplicate."""
    client = AOIInferenceClient(base_url="http://inf")
    with (
        patch(
            "src.clients.http.requests.request",
            side_effect=[requests.ConnectionError("refused"), FakeResponse(202, {"build_id": "b"})],
        ) as req,
        patch("src.clients.http.sleep"),
    ):
        assert client.load_async("m", "1").build_id == "b"
    assert req.call_count == 2


def test_load_409_already_loaded_is_idempotent():
    """Legacy tolerance: older builds report an already-loaded version this way,
    which describes the desired state rather than a failure."""
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(409, {"error": "load_model_already_loaded", "message": "x"})
        assert client.load_async("m", "1").phase == "succeeded"


def test_load_500_maps_to_load_failed():
    client = AOIInferenceClient(base_url="http://inf")
    with (
        patch("src.clients.http.requests.request") as req,
        patch("src.clients.http.sleep"),
    ):
        req.return_value = FakeResponse(500, {"error": "load_model_failed", "message": "x"})
        with pytest.raises(ModelManagerError) as excinfo:
            client.load_async("m", "1")
    assert excinfo.value.code == "load_failed"
    assert excinfo.value.retryable is True


def test_load_404_maps_to_not_found():
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(404, {"error": {"code": "model_not_found"}})
        with pytest.raises(ModelManagerError) as excinfo:
            client.load_async("m", "1")
    assert excinfo.value.code == "not_found"
    assert excinfo.value.retryable is False


# ----------------------------------------------------------------- build status
def test_build_status_by_build_id():
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(
            200, {"phase": "building", "building": True, "elapsed_s": 94.2}
        )
        status = client.build_status(build_id="b-1")
    method, url = req.call_args.args
    assert (method, url) == ("GET", "http://inf/v1/models/build-status")
    assert req.call_args.kwargs["params"] == {"build_id": "b-1"}
    assert status.phase == "building"


def test_build_status_by_model_and_version_reads_idle_as_a_200():
    """``idle`` is a 200, not a 404, and says nothing failed.

    Conflating it with an error would fail a promotion whose build had already
    finished before the poll got a look at it.
    """
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, {"phase": "idle", "building": False, "version": "2"})
        status = client.build_status(name="m", version="2")
    assert req.call_args.kwargs["params"] == {"model_name": "m", "version": "2"}
    assert status.phase == "idle"


def test_build_status_404_is_an_unknown_build_id_not_an_idle_target():
    """A 404 only ever means this ``build_id`` is unknown. It is deliberately kept
    distinct from ``idle``, which is an authoritative statement about a target."""
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(404, {"error": {"code": "unknown_build_id"}})
        status = client.build_status(build_id="b-gone")
    assert status.phase == "unknown"
    assert status.build_id == "b-gone"


def test_build_status_failed_carries_the_error_through():
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(
            200,
            {"phase": "failed", "building": False, "http_status": 500, "error": "trtexec exploded"},
        )
        status = client.build_status(build_id="b-1")
    assert status.phase == "failed"
    assert status.error == "trtexec exploded"
    assert status.http_status == 500


def test_build_status_unrecognised_phase_degrades_to_unknown():
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, {"phase": "reticulating"})
        assert client.build_status(build_id="b-1").phase == "unknown"


# ------------------------------------------------------------------- inventory
def test_list_versions_returns_the_raw_inventory():
    client = AOIInferenceClient(base_url="http://inf")
    payload = {
        "model_name": "m",
        "routing_mode": "shadow",
        "versions": [{"version": "3", "on_disk": True, "deletable": True}],
        "deletable": ["3"],
    }
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, payload)
        assert client.list_versions("m") == payload
    method, url = req.call_args.args
    assert (method, url) == ("GET", "http://inf/v1/models/versions")
    assert req.call_args.kwargs["params"] == {"model_name": "m"}


# ---------------------------------------------------------------------- unload
def test_unload_posts_the_flat_typed_body():
    """Same hazard as load: a body whose ``version`` is not read defaults to every
    staged version, turning one eviction into a mass eviction."""
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, {"status": "unloaded"})
        client.unload("m", "1", idempotency_key="k-2")
    assert req.call_args.kwargs["json"] == {
        "model_name": "m",
        "version": "1",
        "idempotency_key": "k-2",
    }
    assert req.call_args.kwargs["headers"]["Content-Type"] == "application/json"


def test_unload_404_not_loaded_is_idempotent():
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(404, {"error": "unload_model_not_loaded", "message": "x"})
        client.unload("m", "1")  # no raise
    assert req.call_args.kwargs["json"] == {"model_name": "m", "version": "1"}


def test_unload_409_serving_version_is_a_conflict_not_a_bad_request():
    """The server refuses to evict a version that is taking traffic.

    ``ModelManager.cleanup`` refuses the identical condition locally as
    ``conflict``/409. The local check does not make this one redundant — routing can
    change between the two — so the same situation must not read as a 400 just
    because the server was the one to catch it.

    Non-retryable, matching the local guard: nothing changes until routing moves
    off the version. The server's message names that action, so it is preserved.
    """
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(
            409,
            {
                "error": {
                    "code": "model_version_serving",
                    "message": "version 2 is part of the active rollout ['2']; "
                    "switch routing away from it before unloading.",
                    "retryable": False,
                }
            },
        )
        with pytest.raises(ModelManagerError) as excinfo:
            client.unload("m", "2")

    assert excinfo.value.code == "conflict"
    assert excinfo.value.http_status == 409
    assert excinfo.value.retryable is False
    assert "model_version_serving" in excinfo.value.message


def test_unload_409_idempotency_in_progress_is_the_retryable_conflict():
    """The one 409 on this path that clears without anyone intervening."""
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(409, {"error": {"code": "idempotency_in_progress"}})
        with pytest.raises(ModelManagerError) as excinfo:
            client.unload("m", "2", idempotency_key="k")

    assert excinfo.value.code == "conflict"
    assert excinfo.value.retryable is True


def test_load_409_idempotency_conflict_is_a_conflict_not_an_internal_fault():
    """A key reused with different parameters is the caller's problem, not a fault.

    It was reaching the generic branch and being reported as ``internal``/502, which
    points the reader at the service instead of at the key collision they own.
    Terminal: the same key with the same mismatched parameters is refused every time.
    """
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(
            409,
            {
                "error": {
                    "code": "idempotency_conflict",
                    "message": "idempotency_key was already used with different parameters",
                }
            },
        )
        with pytest.raises(ModelManagerError) as excinfo:
            client.load_async("m", "1", idempotency_key="k")

    assert excinfo.value.code == "conflict"
    assert excinfo.value.http_status == 409
    assert excinfo.value.retryable is False
    assert "idempotency_conflict" in excinfo.value.message


def test_list_models_returns_models_array():
    client = AOIInferenceClient(base_url="http://inf")
    models = [{"name": "m", "version": "1", "status": "loaded"}]
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, {"models": models})
        assert client.list_models() == models


def test_ready_true_on_200_false_on_503():
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, {"status": "ready", "loaded_model_count": 1})
        assert client.ready() is True
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(503, {"status": "not_ready", "loaded_model_count": 0})
        assert client.ready() is False


# ------------------------------------------------------------------ AOI Client
def test_get_routing_returns_state():
    client = AOIClientClient(base_url="http://cli")
    state = {"mode": "single", "primary_model": {"name": "m", "version": "1"}, "updated_at": "t"}
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, state)
        assert client.get_routing() == state


def test_put_routing_single_ok():
    client = AOIClientClient(base_url="http://cli")
    config = {"mode": "single", "primary_model": {"name": "m", "version": "2"}}
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, {**config, "updated_at": "t"})
        result = client.put_routing(config)
    method, url = req.call_args.args
    assert method == "PUT"
    assert url == "http://cli/v1/model-routing"
    assert req.call_args.kwargs["json"] == config
    assert result["mode"] == "single"


@pytest.mark.parametrize("percent", [0, 100, 150, None])
def test_put_routing_rejects_out_of_range_canary(percent):
    client = AOIClientClient(base_url="http://cli")
    config = {
        "mode": "canary",
        "primary_model": {"name": "m", "version": "1"},
        "canary_model": {"name": "m", "version": "2"},
        "canary_percent": percent,
    }
    with patch("src.clients.http.requests.request") as req:
        with pytest.raises(ModelManagerError) as excinfo:
            client.put_routing(config)
    assert excinfo.value.code == "bad_request"
    req.assert_not_called()


def test_put_routing_409_is_a_conflict_naming_the_not_ready_target():
    """The routing PUT refuses a target that is not yet READY.

    Reported as a retryable conflict rather than an internal fault: the request was
    well-formed and becomes valid once the version finishes loading, and a caller
    told "internal error, 502" would look for the fault in the wrong place.

    The server's own error code is carried through rather than replaced with a
    fixed "not READY" phrase, because this status covers more than one condition
    (see the mode-disabled case below) and a hardcoded reason would misdescribe the
    others. Uses the flat ``{"error": "<code>"}`` envelope the routing endpoint
    actually sends, which differs from the nested one the model endpoints use.
    """
    client = AOIClientClient(base_url="http://cli")
    config = {"mode": "single", "primary_model": {"model_name": "m", "model_version": "2"}}
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(409, {"error": "model_routing_target_not_ready"})
        with pytest.raises(ModelManagerError) as excinfo:
            client.put_routing(config)

    assert excinfo.value.code == "conflict"
    assert excinfo.value.http_status == 409
    assert excinfo.value.retryable is True
    assert "model_routing_target_not_ready" in excinfo.value.message
    assert "single" in excinfo.value.message


def test_put_routing_409_mode_disabled_is_a_terminal_conflict():
    """A rollout mode switched off in configuration never becomes available.

    Shares the 409 with the not-ready case but is the opposite of transient:
    retrying only spends the caller's budget and then reports a configuration
    problem as a timeout.
    """
    client = AOIClientClient(base_url="http://cli")
    config = {"mode": "canary", "canary_percent": 10}
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(409, {"error": "model_routing_mode_disabled"})
        with pytest.raises(ModelManagerError) as excinfo:
            client.put_routing(config)

    assert excinfo.value.code == "conflict"
    assert excinfo.value.http_status == 409
    assert excinfo.value.retryable is False
    assert "model_routing_mode_disabled" in excinfo.value.message


def test_list_models_absent_models_key_is_an_empty_inventory():
    """An omitted array field is a legitimate partial payload, not a broken one."""
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, {"model_repo": "/repo"})
        assert client.list_models() == []


@pytest.mark.parametrize("models", [None, "not-a-list", {}, 7])
def test_list_models_raises_rather_than_returning_a_non_list(models):
    """``"models": null`` is the trap: the ``.get`` default never applies to it.

    The declared return type has to be a guarantee, because every caller iterates
    the result. Raising puts a malformed envelope onto the path callers already
    have for an unreadable inventory, which is the correct reading of it.
    """
    client = AOIInferenceClient(base_url="http://inf")
    with (
        patch("src.clients.http.requests.request") as req,
        patch("src.clients.http.sleep"),
    ):
        req.return_value = FakeResponse(200, {"models": models})
        with pytest.raises(ModelManagerError) as excinfo:
            client.list_models()

    assert excinfo.value.retryable is True
    assert "not an array" in excinfo.value.message


def test_list_models_survives_a_2xx_that_is_not_json():
    """A gateway can answer 200 with an HTML page; that must not be a 500.

    ``response.json()`` raises ``ValueError``, which is not a ``ModelManagerError``
    and so would slip past the callers' degrade-gracefully guards entirely.
    """
    client = AOIInferenceClient(base_url="http://inf")
    with patch("src.clients.http.requests.request") as req:
        # json_body=None makes FakeResponse.json() raise, as requests does.
        req.return_value = FakeResponse(200, None, text="<html>502 Bad Gateway</html>")
        assert client.list_models() == []


def test_get_routing_survives_a_2xx_that_is_not_json():
    """Same hazard on the routing read, which gates cleanup's other guard."""
    client = AOIClientClient(base_url="http://cli")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, None, text="<html>502 Bad Gateway</html>")
        assert client.get_routing() == {}


def test_a_non_dict_json_body_reads_as_an_empty_object():
    """A JSON array or scalar where an object was promised is equally unusable."""
    client = AOIClientClient(base_url="http://cli")
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, ["not", "an", "object"])
        assert client.get_routing() == {}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({}, []),
        ({"other": 1}, []),
        ({"k": []}, []),
        ({"k": ["a"]}, ["a"]),
        ({"k": ("a",)}, ["a"]),
        ({"k": None}, None),
        ({"k": "ab"}, None),
        ({"k": b"ab"}, None),
        ({"k": {}}, None),
        ({"k": 7}, None),
    ],
)
def test_list_field_separates_an_absent_key_from_an_unusable_value(body, expected):
    """Absent yields ``[]``; present-but-not-an-array yields ``None``.

    A bare string is rejected despite being iterable — it would otherwise read
    element-wise into a plausible-looking list of characters.
    """
    assert list_field(body, "k") == expected


@pytest.mark.parametrize("status", [401, 403])
def test_put_routing_access_denied_is_not_retryable(status):
    """A rejected credential is the one upstream failure retrying cannot fix.

    It used to land in the generic branch, which is retryable and flattens the
    status to 502 — so a permissions problem became a retry loop that eventually
    reported the service as unwell instead of naming the real cause.
    """
    client = AOIClientClient(base_url="http://cli")
    with (
        patch("src.clients.http.requests.request") as req,
        patch("src.clients.http.sleep"),
    ):
        req.return_value = FakeResponse(status, {"error": {"code": "forbidden"}})
        with pytest.raises(ModelManagerError) as excinfo:
            client.put_routing({"mode": "single"})

    assert excinfo.value.retryable is False
    assert excinfo.value.http_status == status, "the original status was flattened"
    assert "credentials/permissions" in excinfo.value.message


@pytest.mark.parametrize("status", [401, 403])
def test_load_access_denied_is_not_retryable_and_keeps_its_status(status):
    """Classified the same way on every write path, so the three cannot drift.

    Load previously reported this as a generic ``internal`` 502, which named
    neither the cause nor the status.
    """
    client = AOIInferenceClient(base_url="http://inf")
    with (
        patch("src.clients.http.requests.request") as req,
        patch("src.clients.http.sleep"),
    ):
        req.return_value = FakeResponse(status, {"error": {"code": "forbidden"}})
        with pytest.raises(ModelManagerError) as excinfo:
            client.load_async("m", "1")

    assert excinfo.value.retryable is False
    assert excinfo.value.http_status == status
    assert "credentials/permissions" in excinfo.value.message


@pytest.mark.parametrize("status", [401, 403])
def test_unload_access_denied_is_not_reported_as_a_bad_request(status):
    """Unload previously fell into its 4xx branch and blamed the request body."""
    client = AOIInferenceClient(base_url="http://inf")
    with (
        patch("src.clients.http.requests.request") as req,
        patch("src.clients.http.sleep"),
    ):
        req.return_value = FakeResponse(status, {"error": {"code": "forbidden"}})
        with pytest.raises(ModelManagerError) as excinfo:
            client.unload("m", "1")

    assert excinfo.value.retryable is False
    assert excinfo.value.http_status == status
    assert "credentials/permissions" in excinfo.value.message


# ------------------------------------------------------------ base URL resolution
def test_both_clients_default_to_the_single_control_plane_url(monkeypatch):
    """One HAProxy-pinned control replica serves ``/v1/models/*`` and the routing
    PUT alike, so unset per-API overrides resolve to the same control address."""
    monkeypatch.setattr(settings, "aoi_control_url", "http://control:5000")
    monkeypatch.setattr(settings, "aoi_inference_url", None)
    monkeypatch.setattr(settings, "aoi_client_url", None)

    assert AOIInferenceClient().base_url == "http://control:5000"
    assert AOIClientClient().base_url == "http://control:5000"


def test_per_api_overrides_still_win_when_the_apis_are_split(monkeypatch):
    """A deployment that serves the two APIs separately keeps doing so."""
    monkeypatch.setattr(settings, "aoi_control_url", "http://control:5000")
    monkeypatch.setattr(settings, "aoi_inference_url", "http://inference:8080")
    monkeypatch.setattr(settings, "aoi_client_url", "http://routing:8081")

    assert AOIInferenceClient().base_url == "http://inference:8080"
    assert AOIClientClient().base_url == "http://routing:8081"


def test_put_routing_accepts_valid_canary():
    client = AOIClientClient(base_url="http://cli")
    config = {
        "mode": "canary",
        "primary_model": {"name": "m", "version": "1"},
        "canary_model": {"name": "m", "version": "2"},
        "canary_percent": 5,
    }
    with patch("src.clients.http.requests.request") as req:
        req.return_value = FakeResponse(200, {**config, "updated_at": "t"})
        result = client.put_routing(config)
    assert result["canary_percent"] == 5
