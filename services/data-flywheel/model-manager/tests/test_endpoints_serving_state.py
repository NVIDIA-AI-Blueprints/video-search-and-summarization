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

"""API-layer tests for GET /v1/models/{model_name}/serving-state.

These drive the real FastAPI app and the real ``ModelManager`` over a
``FakeAdapter`` returning scripted ``ServingState``s, so the assertions cover the
actual wire body a caller receives -- not a mocked-out handler.

The endpoint's contract for DFW hinges on two things this module pins down:

1. ``known_versions`` reports every version the backend knows about, *including*
   versions in a non-READY/``UNAVAILABLE`` state, because those still occupy a
   directory in the model repo and a next-version floor computed without them
   would collide.
2. ``routing_available``/``loaded_available`` survive into the response body, so
   a caller can tell "nothing is serving" from "I could not find out" and fail
   closed on the latter.
"""

import pytest
from fastapi.testclient import TestClient

from main import app
from src.adapters.aoi.adapter import AOIServingAdapter
from src.adapters.base import ServingAdapter, ServingState
from src.api.schemas import RoutingEntry
from src.core.errors import ModelManagerError
from src.core.manager import ModelManager

NAME = "changenet_classify"

# The payload the live AOI Inference /v1/models actually returns on the box:
# both versions exist in the Triton repo, neither is currently loaded.
REAL_LIST_MODELS_PAYLOAD = {
    "models": [
        {"name": NAME, "version": "1", "state": "UNAVAILABLE"},
        {"name": NAME, "version": "2", "state": "UNAVAILABLE"},
    ],
    "model_repo": "/app/microservice/triton_model_repo",
}


class FakeAdapter(ServingAdapter):
    """``ServingAdapter`` that returns one scripted ``ServingState`` (or raises)."""

    def __init__(self, state: ServingState | None = None, *, raises: Exception | None = None):
        self._state = state
        self._raises = raises
        self.state_reads: list[str] = []

    def stage(
        self,
        name,
        version,
        artifact_uri,
        sha256,
        *,
        spec_uri=None,
        threshold=None,
        idempotency_key=None,
    ):
        raise AssertionError("stage must not be called by a read-only endpoint")

    def apply_control(
        self, *, control, name, candidate, from_version, canary_weight, idempotency_key=None
    ):
        raise AssertionError("apply_control must not be called by a read-only endpoint")

    def serving_state(self, name, *, destructive=False):
        self.state_reads.append(name)
        if self._raises is not None:
            raise self._raises
        return self._state

    def unload(self, name, version):
        raise AssertionError("unload must not be called by a read-only endpoint")

    def inventory(self, name):
        raise AssertionError("inventory must not be called by a read-only endpoint")

    def delete_version(self, name, version):
        raise AssertionError("delete_version must not be called by a read-only endpoint")


@pytest.fixture
def client(monkeypatch):
    """TestClient whose endpoint singleton is backed by an injectable adapter.

    ``endpoints.py`` binds the module-level ``model_manager`` at import time, so
    the patch has to target the name in the *endpoints* module.
    """

    def _install(adapter: ServingAdapter) -> TestClient:
        manager = ModelManager(adapter=adapter)
        monkeypatch.setattr("src.api.endpoints.model_manager", manager)
        return TestClient(app)

    return _install


def _get(client_factory, adapter, name=NAME):
    return client_factory(adapter).get(f"/v1/models/{name}/serving-state")


# ------------------------------------------------------------------ happy path
def test_serving_state_returns_routing_and_versions(client):
    """Both lookups available: full body, and UNAVAILABLE versions are reported."""
    adapter = FakeAdapter(
        ServingState(
            default_version="2",
            routing=[RoutingEntry(version="2", role="default", weight=1.0)],
            serving_versions={"2"},
            # Unfiltered: the adapter passes through versions whose upstream
            # state is UNAVAILABLE (see test_known_versions_includes_unavailable).
            loaded_versions=["1", "2"],
            ready=True,
        )
    )
    response = _get(client, adapter)

    assert response.status_code == 200
    assert response.json() == {
        "model_name": NAME,
        "default_version": "2",
        "routing": [{"version": "2", "role": "default", "weight": 1.0}],
        "serving_versions": ["2"],
        "known_versions": ["1", "2"],
        # Empty because this fixture marks no version ready. The field is a
        # passthrough of ServingState.ready_versions, not derived from routing.
        "ready_versions": [],
        "ready": True,
        "routing_available": True,
        "loaded_available": True,
    }
    assert adapter.state_reads == [NAME]


def test_known_versions_includes_unavailable_versions_from_real_payload(client):
    """The floor DFW needs must survive the real /v1/models payload end to end.

    Drives the genuine ``AOIServingAdapter`` over the exact upstream body seen on
    the live box, where *every* version is UNAVAILABLE. If anything in the chain
    started filtering on state, ``known_versions`` would come back empty and DFW
    would re-mint a colliding version.
    """

    class FakeInference:
        def list_models(self):
            return REAL_LIST_MODELS_PAYLOAD["models"]

        def ready(self):
            return True

    class FakeAOIClient:
        def get_routing(self):
            return {"mode": "single", "primary_model": {"name": NAME, "version": "2"}}

        def put_routing(self, body):
            raise AssertionError("put_routing must not be called by a read-only endpoint")

    adapter = AOIServingAdapter(
        activation=object(), aoi_client=FakeAOIClient(), aoi_inference=FakeInference()
    )
    body = _get(client, adapter).json()

    assert body["known_versions"] == ["1", "2"]
    assert body["loaded_available"] is True
    # ...and the *serving* set stays narrower than the known set.
    assert body["serving_versions"] == ["2"]
    assert body["default_version"] == "2"


# --------------------------------------------------------- degraded lookups
def test_routing_unavailable_returns_200_with_flag_false(client):
    """A failed routing lookup degrades to a flagged 200, never a 500."""
    adapter = FakeAdapter(
        ServingState(
            default_version=None,
            routing=[],
            serving_versions=set(),
            loaded_versions=["1", "2"],
            ready=True,
            routing_available=False,
        )
    )
    response = _get(client, adapter)

    assert response.status_code == 200
    body = response.json()
    assert body["routing_available"] is False
    assert body["default_version"] is None
    assert body["routing"] == []
    assert body["serving_versions"] == []
    # The other lookup succeeded, so the version floor is still trustworthy.
    assert body["loaded_available"] is True
    assert body["known_versions"] == ["1", "2"]


def test_inference_unreachable_returns_200_with_loaded_flag_false(client):
    """An unreachable inference service yields loaded_available=False, not a 500."""
    adapter = FakeAdapter(
        ServingState(
            default_version="2",
            routing=[RoutingEntry(version="2", role="default", weight=1.0)],
            serving_versions={"2"},
            loaded_versions=[],
            ready=False,
            loaded_available=False,
        )
    )
    response = _get(client, adapter)

    assert response.status_code == 200
    body = response.json()
    assert body["loaded_available"] is False
    # Empty here means "unknown" -- the flag is the only thing distinguishing it
    # from the genuinely-empty case below, and DFW must fail closed on it.
    assert body["known_versions"] == []
    assert body["routing_available"] is True


def test_model_with_no_versions_reports_genuinely_empty_not_unknown(client):
    """Empty lists with both flags true = "nothing is there", a distinct answer."""
    adapter = FakeAdapter(
        ServingState(
            default_version=None,
            routing=[],
            serving_versions=set(),
            loaded_versions=[],
            ready=True,
            routing_available=True,
            loaded_available=True,
        )
    )
    response = _get(client, adapter, name="never_deployed")

    assert response.status_code == 200
    assert response.json() == {
        "model_name": "never_deployed",
        "default_version": None,
        "routing": [],
        "serving_versions": [],
        "known_versions": [],
        "ready_versions": [],
        "ready": True,
        # Both true: this is an authoritative "empty", not a lookup failure.
        "routing_available": True,
        "loaded_available": True,
    }


# ------------------------------------------------------------------- errors
def test_adapter_error_renders_the_shared_error_envelope(client):
    """A ModelManagerError surfaces through _error_response, not as a raw 500."""
    adapter = FakeAdapter(
        raises=ModelManagerError(
            code="registry_unreachable",
            message="AOI Client routing lookup failed",
            retryable=True,
            http_status=503,
        )
    )
    response = _get(client, adapter)

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "registry_unreachable",
            "message": "AOI Client routing lookup failed",
            "retryable": True,
            "details": None,
        }
    }


def test_status_route_keeps_precedence_over_the_model_name_path(client):
    """The literal /v1/models/status/{swap_id} route still wins its shape.

    It is a 410 tombstone now, but it must stay registered and stay ahead of
    /v1/models/{model_name}/serving-state, or a swap-id poll would be answered as
    if "status" were a model name.
    """
    adapter = FakeAdapter(ServingState())
    response = client(adapter).get("/v1/models/status/unknown-swap")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "bad_request"
    assert "convergence" in response.json()["error"]["message"]


def test_blocking_handlers_are_sync_so_they_do_not_hold_the_event_loop():
    """Blocking handlers must be sync def so they run in FastAPI's threadpool.

    There is one event loop, so an async handler that blocks holds the whole
    service, including ``/v1/health``. ``health`` is the deliberate exception: it
    does no blocking work and must stay on the loop so it can answer while the
    threadpool is busy.
    """
    import inspect

    from src.api import endpoints

    # Enumerated from the router so new endpoints are covered automatically.
    handlers = {route.endpoint.__name__: route.endpoint for route in endpoints.router.routes}
    assert "convergence" in handlers, "the router no longer exposes the convergence handler"
    blocking = sorted(handlers.keys() - {"health"})
    offenders = [name for name in blocking if inspect.iscoroutinefunction(handlers[name])]
    assert not offenders, (
        f"{offenders} must be sync def so FastAPI runs them in the threadpool. "
        "If one genuinely does no blocking work, exempt it here; do not "
        "make the check optional."
    )
    assert inspect.iscoroutinefunction(
        endpoints.health
    ), "health must stay async so it answers while the threadpool is saturated"


def _restart(monkeypatch, adapter) -> TestClient:
    """Rebind the endpoints module to a Model Manager that shares no memory.

    A second instance IS a restart now, because there is no process-local state
    left to share. It was not always: ``__init__`` used to default to a
    module-level ``swap_store`` singleton, so two managers built in one process
    silently shared one dict and the "restarted" one could answer about a swap
    it never served. This test was written against a signature that still took a
    ``store``, and was confirmed to fail there for the right reason before the
    store was deleted.
    """
    monkeypatch.setattr("src.api.endpoints.model_manager", ModelManager(adapter=adapter))
    return TestClient(app)


def _landed(response) -> bool:
    """Did this response say, at the HTTP boundary, that the apply landed?

    Requires a 200 that says landed, so a 410 tombstone never counts -- phrased
    as "answered 200 AND says landed" rather than "is not a 404" so it stayed
    correct across the tombstone.
    """
    if response is None or response.status_code != 200:
        return False
    return response.json().get("converged") is True


def test_a_restarted_model_manager_still_says_whether_the_apply_landed(client, monkeypatch):
    """The restart claim, proven at the HTTP boundary with no containers.

    The restart is modelled by ``_restart``, which builds an independent manager.
    The response is read with plain dicts and ``.get()`` so a missing ``applied``
    key fails on the claim rather than on a KeyError.

    The adapter already reflects the full switch, so the apply is a no-op against
    live state. This adapter allows ``apply_control`` because the test drives a
    write endpoint.
    """

    class _WritableAdapter(FakeAdapter):
        def apply_control(
            self, *, control, name, candidate, from_version, canary_weight, idempotency_key=None
        ):
            return None

    live = ServingState(
        default_version="2",
        routing=[RoutingEntry(version="2", role="default", weight=1.0)],
        serving_versions={"2"},
        loaded_versions=["1", "2"],
        ready_versions=["2"],
        ready=True,
    )
    adapter = _WritableAdapter(live)

    applied = client(adapter).post(
        "/v1/models/apply",
        json={"model_name": NAME, "version": "2", "control": "full_switch"},
    )
    assert applied.status_code == 202
    body = applied.json()

    # ---- the Model Manager restarts here ----
    restarted = _restart(monkeypatch, adapter)

    answers = [restarted.get(f"/v1/models/status/{body.get('swap_id')}")]
    if isinstance(body.get("applied"), dict):
        answers.append(
            restarted.post("/v1/models/convergence", json={"expectation": body["applied"]})
        )

    assert any(_landed(a) for a in answers), (
        "a restarted Model Manager has no surface that can say whether the apply "
        "landed: " + ", ".join(f"{a.request.method} -> {a.status_code}" for a in answers)
    )
