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

"""End-to-end promotion through ``ModelManager`` against a unified backend.

``FakeUnifiedServer`` implements the unified state machine in-process and
enforces the spec's strictness rules, so a request carrying an unsupported field
fails the test rather than being quietly tolerated. Unload granularity is one of
those knobs: both shipped backends now evict per version, so that is the
default, and ``unload_granularity="model"`` still reproduces the older build
whose whole-model eviction is the reason the escape hatch exists.

The backend's shape — synchronous or accepted-then-warming load, canary or no
canary, CAS or no CAS — is a knob on the FAKE, never on the adapter. That is the
point of the suite: one adapter, no capability declaration, and it has to work
against every combination by reading what the backend actually answers.
"""

import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.adapters.unified.adapter import UnifiedServingAdapter
from src.adapters.unified.client import UnifiedModelClient
from src.api.schemas import ApplyRequest, CleanupRequest, PullRequest, UnloadRequest
from src.config import settings
from src.core.errors import ModelManagerError
from src.core.manager import ModelManager

NAME = "changenet_classify"

_ENVELOPE_FIELDS = {"key", "value"}
_LOAD_FIELDS = {"name", "version", "batch_size"}
_UNLOAD_FIELDS = {"name", "version"}
_ROUTING_FIELDS = {"default", "routes", "if_revision"}
_POLICY_FIELDS = {"model", "shadow", "canary", "canary_percent"}
# ``StreamRoute`` is additionalProperties:false over these three -- no canary.
_STREAM_ROUTE_FIELDS = {"streams", "model", "shadow"}


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def close(self):
        pass


def _error(status, code, message, field=None):
    error = {"code": code, "message": message}
    if field is not None:
        error["field"] = field
    return _Response(
        status,
        {"status": f"HTTP/1.1 {status}", "reason": f"{code}, {message}", "error": error},
    )


class FakeUnifiedServer:
    """In-process unified control API, strict about the spec's field rules."""

    def __init__(
        self,
        versions=("1",),
        *,
        sync_load=True,
        canary=True,
        if_revision=False,
        stream_routes=False,
        warm_polls=1,
        warm_outcome="ready",
        omit_unloaded=False,
        update_in_flight=(),
        already_loaded_conflict=False,
        unload_granularity="version",
        publishes_deletable=False,
        deletable_override=None,
        unload_status=200,
        unload_state="unloaded",
    ):
        # The revised contract lets a backend DROP an unloaded version's row
        # rather than keep a tombstone, and lets load or unload answer 409
        # UPDATE_IN_FLIGHT during a native update. Both default OFF.
        #
        # Unload granularity is a property of the BUILD: the spec says only
        # "unloads a model or model version". "model" is the older deft build.
        self.unload_granularity = unload_granularity
        self.omit_unloaded = omit_unloaded
        self.update_in_flight = set(update_in_flight)
        # The revision lists "already-loaded models" among its Conflict cases;
        # our repeat-safety story assumes the other legal answer, a no-op 200.
        self.already_loaded_conflict = already_loaded_conflict
        # Backend SHAPE, per fake: the adapter does not know which is true.
        self.sync_load = sync_load
        self.canary = canary
        self.if_revision = if_revision
        # ``value.routes`` is x-supported-by [rtvi-cv] and the spec's own worked
        # example of a field AOI Inference must REJECT. Default OFF, so the
        # central leak assertion catches a default-on regression unasked.
        self.stream_routes = stream_routes
        # The last routes arm accepted, so a test can assert what was named.
        self.routes = None

        self.state = {v: ("ready" if v == "1" else "unloaded") for v in versions}
        self.routing = {"mode": "single", "model": {"name": NAME, "version": "1"}}
        self.rejected: list[str] = []
        self.calls: list[str] = []
        # A 202 load leaves the version WARMING until `warm_polls` status reads.
        # Marking it ready in _load makes the 202 body a lie and lets a client
        # that never waits pass the suite.
        self.warm_polls = warm_polls
        self.warm_outcome = warm_outcome
        self._warming: dict[str, int] = {}
        # RTVI-CV MERGES the routing policy rather than replacing it, confirmed
        # live in the S-series E2E. The merge below is SLOT-COUPLED -- naming
        # ``shadow`` suppresses the whole carry-over -- which is an assumption,
        # not an observation. See ``_surviving_roles``.
        self.merge_routing = False
        # deft-aoi-inference carries a `deletable` array on its model entry and
        # RTVI-CV publishes the key nowhere, so the adapter branches on the
        # response rather than being told which it faces.
        self.publishes_deletable = publishes_deletable
        # A list the fake's own state would not produce: the only way to say
        # "nothing is deletable" while every precondition holds.
        self.deletable_override = deletable_override
        # Status line and reported state, separately. deft answers 200; RTVI-CV
        # answers 202 with `state: "unloaded"`, the row gone by the next read.
        # The third shape is a 2xx affirming nothing, under which an absent row
        # could equally be a drain still in progress.
        self.unload_status = unload_status
        self.unload_state = unload_state
        # RTVI-CV per-version extras: running instances and attached streams.
        # Empty by default, which is the AOI shape.
        self.instances: dict[str, list[dict]] = {}
        # Rows publishing no readable ``state``: non-conformant, but modelled so
        # the destructive path can be shown refusing an unreadable field.
        self.stateless: set[str] = set()

    # -- transport --
    def handle(self, method, url, **kwargs):
        path = url.split("://", 1)[1].split("/", 1)[1]
        self.calls.append(f"{method} /{path}")
        body = kwargs.get("json")
        if method == "GET" and path.startswith("api/v1/model/status"):
            return self._status()
        if method == "GET" and path.startswith("api/v1/model/routing"):
            return _Response(
                200, {"status": "HTTP/1.1 200 OK", "routing": {"default": self._default_policy()}}
            )
        if method == "POST" and path == "api/v1/model/load":
            return self._load(body)
        if method == "POST" and path == "api/v1/model/unload":
            return self._unload(body)
        if method == "POST" and path == "api/v1/model/routing":
            return self._routing(body)
        return _error(404, "MODEL_NOT_LOADED", f"no route for /{path}")

    # -- strictness --
    def _reject_unknown(self, obj, allowed, prefix):
        """The spec: an unsupported field MUST be rejected, never ignored."""
        for key in obj:
            if key not in allowed:
                self.rejected.append(f"{prefix}.{key}")
                return _error(400, "FIELD_UNSUPPORTED", f"'{key}' is not a recognized field")
        return None

    def _envelope(self, body, expected_key, value_fields):
        if not isinstance(body, dict):
            return None, _error(400, "INVALID_REQUEST", "body must be an object")
        bad = self._reject_unknown(body, _ENVELOPE_FIELDS, "")
        if bad:
            return None, bad
        if body.get("key") != expected_key:
            return None, _error(400, "INVALID_REQUEST", f"key must be '{expected_key}'")
        value = body.get("value")
        if not isinstance(value, dict):
            return None, _error(400, "INVALID_REQUEST", "value must be an object")
        bad = self._reject_unknown(value, value_fields, "value")
        if bad:
            return None, bad
        return value, None

    def _check_version(self, version):
        if not isinstance(version, str):
            return _error(400, "VERSION_INVALID", "value.version must be a string")
        if not version.isdigit():
            return _error(400, "VERSION_INVALID", "model version must be a non-negative integer")
        return None

    # -- operations --
    def _load(self, body):
        value, err = self._envelope(body, "model", _LOAD_FIELDS)
        if err:
            return err
        bad = self._check_version(value.get("version"))
        if bad:
            return bad
        version = value["version"]
        if version in self.update_in_flight:
            return _error(
                409,
                "UPDATE_IN_FLIGHT",
                "a model update for this model is still in progress",
                field="value.name",
            )
        if self.already_loaded_conflict and version in self.state:
            # Keyed on "the backend has heard of this version", not on ready: a
            # test forcing a non-servable state would otherwise disarm it.
            return _error(409, "MODEL_ALREADY_LOADED", f"'{NAME}@{version}' is already loaded")
        if self.sync_load:
            self.state[version] = "ready"
            status, state = 200, "ready"
        else:
            self.state[version] = "warming"
            self._warming[version] = self.warm_polls
            status, state = 202, "warming"
        return _Response(
            status,
            {
                "status": f"HTTP/1.1 {status}",
                "reason": f"'{NAME}@{version}' loaded",
                "model": {"name": NAME, "version": version},
                "state": state,
            },
        )

    def _unload(self, body):
        value, err = self._envelope(body, "model", _UNLOAD_FIELDS)
        if err:
            return err
        if value.get("version") is None:
            return _error(400, "VERSION_INVALID", "value.version is required by AOI Inference")
        version = value["version"]
        if version in self.update_in_flight:
            return _error(
                409,
                "UPDATE_IN_FLIGHT",
                "a model update for this model is still in progress",
                field="value.name",
            )
        active = {
            v
            for v in (
                self.routing.get("model", {}).get("version"),
                self.routing.get("shadow", {}).get("version"),
                self.routing.get("canary", {}).get("version"),
            )
            if v
        }
        if version in active:
            return _error(409, "MODEL_IN_USE", "model is the active primary, shadow, or canary")
        if self.unload_granularity == "model":
            # The older deft build: the version is validated and echoed in a 200,
            # then every version of the model is evicted anyway.
            for v in self.state:
                self.state[v] = "unloaded"
        else:
            self.state[version] = "unloaded"
        return _Response(
            self.unload_status,
            {
                "status": f"HTTP/1.1 {self.unload_status}",
                "reason": f"'{NAME}@{version}' unloaded",
                "model": {"name": NAME, "version": version},
                "state": self.unload_state,
            },
        )

    def _routing(self, body):
        value, err = self._envelope(body, "routing", _ROUTING_FIELDS)
        if err:
            return err
        if "if_revision" in value and not self.if_revision:
            self.rejected.append("value.if_revision")
            return _error(400, "FIELD_UNSUPPORTED", "'if_revision' is not supported")
        routes = value.get("routes")
        if routes is not None and not self.stream_routes:
            self.rejected.append("value.routes")
            return _error(
                400, "FIELD_UNSUPPORTED", "'routes' is not supported", field="value.routes"
            )
        if routes is not None:
            if not isinstance(routes, list) or not routes:
                return _error(400, "INVALID_REQUEST", "value.routes must be a non-empty array")
            for route in routes:
                if not isinstance(route, dict) or "streams" not in route:
                    return _error(400, "INVALID_REQUEST", "a route must name its streams")
                bad = self._reject_unknown(route, _STREAM_ROUTE_FIELDS, "value.routes[]")
                if bad:
                    return bad
        policy = value.get("default")
        if not isinstance(policy, dict):
            return _error(400, "INVALID_REQUEST", "value.default is required")
        bad = self._reject_unknown(policy, _POLICY_FIELDS, "value.default")
        if bad:
            return bad
        if "canary" in policy and not self.canary:
            # In `error.field` as the real service does, which is what lets the
            # adapter turn it into a policy remedy.
            return _error(
                400,
                "FIELD_UNSUPPORTED",
                "'canary' is not supported",
                field="value.default.canary",
            )

        for ref_key in ("model", "shadow", "canary"):
            ref = policy.get(ref_key)
            if ref is None:
                continue
            if self.state.get(ref.get("version")) != "ready":
                return _error(409, "MODEL_NOT_LOADED", f"{ref.get('version')} is not READY")

        percent = policy.get("canary_percent")
        if "canary" in policy and not (percent is not None and 0 < percent < 100):
            return _error(400, "INVALID_ARGUMENT", "canary_percent must be > 0 and < 100")

        # REPLACE semantics, verified against aoi-inference v0.0.1: the whole
        # policy is swapped, so an omitted shadow/canary is cleared.
        # `merge_routing` is the opposite reading, where it is PRESERVED.
        previous = self.routing if self.merge_routing else {}
        routing = {"model": policy["model"]}
        if "shadow" in policy:
            # A JSON null clears the shadow, so leave the slot empty rather than
            # storing a null. Naming the slot is still an explicit statement, so
            # this branch must come first or the merge below brings it back.
            if policy["shadow"] is not None:
                routing["shadow"] = policy["shadow"]
        elif "canary" in policy:
            routing.update({"canary": policy["canary"], "canary_percent": percent})
        elif self.merge_routing:
            for carried in ("shadow", "canary", "canary_percent"):
                if carried in previous:
                    routing[carried] = previous[carried]
        routing["mode"] = (
            "shadow" if "shadow" in routing else "canary" if "canary" in routing else "single"
        )
        self.routing = routing
        # No per-stream binding is modelled: what is under test is WHICH routes
        # are named and whether the profile tolerates them.
        self.routes = routes
        return _Response(
            200, {"status": "HTTP/1.1 200 OK", "reason": "model routing updated", "state": "ready"}
        )

    # -- reading --
    def _default_policy(self):
        policy = {"model": self.routing["model"]}
        for key in ("shadow", "canary", "canary_percent"):
            if key in self.routing:
                policy[key] = self.routing[key]
        return policy

    def _routed_roles(self):
        roles = {}
        for wire_key, ref in self.routing.items():
            if wire_key in ("model", "shadow", "canary") and isinstance(ref, dict):
                roles.setdefault(ref["version"], []).append(wire_key)
        return roles

    def _tick_warming(self):
        """Advance any warming version toward its terminal state on a status read."""
        for version, remaining in list(self._warming.items()):
            if remaining <= 1:
                self.state[version] = self.warm_outcome
                del self._warming[version]
            else:
                self._warming[version] = remaining - 1

    def _status(self):
        self._tick_warming()
        roles = self._routed_roles()
        versions = []
        for version in sorted(self.state):
            if self.omit_unloaded and self.state[version] == "unloaded":
                # "Implementations may omit unloaded versions instead of
                # retaining entries with state: unloaded."
                continue
            row = {"version": version}
            if version not in self.stateless:
                row["state"] = self.state[version]
            if version in roles:
                row["default_for"] = roles[version]
            if version in self.instances:
                row["instances"] = self.instances[version]
            if self.publishes_deletable:
                # deft carries the flag per row too; the adapter reads only the
                # model-level array.
                row["deletable"] = self._deletable(version, roles)
            versions.append(row)
        model = {"name": NAME, "versions": versions}
        if self.publishes_deletable:
            model["deletable"] = (
                list(self.deletable_override)
                if self.deletable_override is not None
                else [v for v in sorted(self.state) if self._deletable(v, roles)]
            )
        return _Response(
            200,
            {
                "status": "HTTP/1.1 200 OK",
                "reason": "GET_MODEL_STATUS_INFO_SUCCESS",
                "model-status": {
                    "routing": {**self.routing},
                    "models": [model],
                },
            },
        )

    def _deletable(self, version, roles):
        """The backend's own answer, from state only it can see authoritatively."""
        return self.state[version] == "unloaded" and version not in roles


@pytest.fixture
def _servers():
    """Every FakeUnifiedServer built during a test, for the leak assertion."""
    return []


@pytest.fixture
def harness(monkeypatch, tmp_path, _servers):
    """Return a factory yielding (manager, server) wired to a temp model repo."""
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    monkeypatch.setattr(settings, "readiness_max_checks", 2)

    def _make(versions=("1",), **server_kwargs):
        server = FakeUnifiedServer(versions=versions, **server_kwargs)
        _servers.append(server)
        client = UnifiedModelClient(base_url="http://inf")
        adapter = UnifiedServingAdapter(client=client)
        manager = ModelManager(adapter=adapter)
        return manager, server

    return _make


@pytest.fixture
def artifact(tmp_path):
    """A real file plus its digest, so staging's verification actually runs."""
    path = tmp_path / "model.pth"
    path.write_bytes(b"weights")
    return f"file://{path}", hashlib.sha256(b"weights").hexdigest()


def _pull(manager, server, artifact, version):
    uri, digest = artifact
    with patch("src.clients.http.requests.request", side_effect=server.handle):
        return manager.pull(
            PullRequest(
                model_name=NAME, version=version, artifact_uri=uri, format="onnx", sha256=digest
            )
        )


def _retire(manager, server, version):
    """``cleanup`` no longer unloads, so a reclaim test has to retire the version
    first -- as the field does, hours earlier, at the full switch.
    """
    with patch("src.clients.http.requests.request", side_effect=server.handle):
        return manager.unload(UnloadRequest(model_name=NAME, version=version))


def _converged(manager, server, applied):
    """Ask whether an applied intent has landed, inside the request patch.

    ``convergence`` re-reads live serving state on every call, so it must run
    under the patch or it reaches for the real network instead of the fake.
    """
    with patch("src.clients.http.requests.request", side_effect=server.handle):
        return manager.convergence(applied).converged


def _apply(manager, server, control, version, weight=None):
    with patch("src.clients.http.requests.request", side_effect=server.handle):
        return manager.apply(
            ApplyRequest(model_name=NAME, version=version, control=control, canary_weight=weight)
        )


@pytest.fixture(autouse=True)
def _no_silent_field_leak(_servers):
    """Fail any test in which the adapter sent a field the profile lacks.

    The spec makes an unsupported field a hard rejection, so a leak is a real
    defect — but a negative-path test that raises for some OTHER reason would
    swallow it. Asserting centrally after every test means no individual test has
    to remember to check.
    """
    yield
    leaked = [field for server in _servers for field in server.rejected]
    assert leaked == [], f"the adapter sent field(s) the profile does not support: {leaked}"


class TestFullPromotion:
    def test_a_canary_capable_backend_walks_shadow_canary_full(self, harness, artifact):
        """The whole DFW ladder against the AOI profile, through ModelManager."""
        manager, server = harness()

        pull = _pull(manager, server, artifact, "2")
        assert pull.status == "staged"
        assert server.state["2"] == "ready"

        shadow = _apply(manager, server, "shadow", "2")
        assert _converged(manager, server, shadow.applied) is True
        assert server.routing["mode"] == "shadow"

        canary = _apply(manager, server, "canary", "2", weight=0.05)
        assert _converged(manager, server, canary.applied) is True
        assert server.routing["mode"] == "canary"
        assert server.routing["canary_percent"] == pytest.approx(5.0)

        full = _apply(manager, server, "full_switch", "2")
        assert _converged(manager, server, full.applied) is True
        assert server.routing["mode"] == "single"
        assert server.routing["model"]["version"] == "2"
        # Replace semantics: the canary is gone without being named.
        assert "canary" not in server.routing
        assert server.rejected == []

    def test_a_backend_without_canary_walks_shadow_then_full(self, harness, artifact):
        """The ladder DFW produces with deployment_stages.canary=false.

        The adapter is not told the backend lacks canary; it simply is never
        asked for one, because the promotion policy is what selects the stages.
        """
        manager, server = harness(sync_load=False, canary=False)

        _pull(manager, server, artifact, "2")
        shadow = _apply(manager, server, "shadow", "2")
        assert _converged(manager, server, shadow.applied) is True

        full = _apply(manager, server, "full_switch", "2")
        assert _converged(manager, server, full.applied) is True
        assert server.routing == {"mode": "single", "model": {"name": NAME, "version": "2"}}
        assert server.rejected == []

    def test_a_canary_a_backend_cannot_serve_is_refused_and_names_the_policy(
        self, harness, artifact
    ):
        """The Model Manager does not carry a table of who supports what.

        A backend that cannot serve a canary says so per the contract, and the
        adapter turns that into a promotion-policy remedy rather than an opaque
        internal error — because the policy is what asked for the stage.
        """
        manager, server = harness(sync_load=False, canary=False)
        _pull(manager, server, artifact, "2")
        with pytest.raises(ModelManagerError) as excinfo:
            _apply(manager, server, "canary", "2", weight=0.05)
        assert excinfo.value.code == "bad_request"
        assert excinfo.value.retryable is False
        assert "deployment_stages.canary=false" in excinfo.value.message
        # The whole policy is validated before anything mutates.
        assert server.routing["model"]["version"] == "1"


class TestAsyncLoad:
    """The rtvi-cv profile answers 202 and warms in the background."""

    def test_pull_waits_for_a_202_load_to_become_servable(self, harness, artifact):
        """Returning on the 202 would hand back a version that cannot be routed.

        A routing write is rejected while its target is not READY, so there is no
        "route it and let it warm up" state for a slow load to settle into — the
        wait has to happen inside pull, which is what owns the budget for it.
        """
        manager, server = harness(sync_load=False, warm_polls=2)
        _pull(manager, server, artifact, "2")
        assert server.state["2"] == "ready"
        assert sum(1 for c in server.calls if "status" in c) >= 2, "it actually polled"

    def test_a_202_load_that_fails_is_terminal_not_retried_forever(self, harness, artifact):
        """`state: failed` is a terminal answer and must be read as one."""
        manager, server = harness(sync_load=False, warm_polls=1, warm_outcome="failed")
        with pytest.raises(ModelManagerError) as excinfo:
            _pull(manager, server, artifact, "2")
        assert excinfo.value.code == "load_failed"
        assert excinfo.value.retryable is False

    def test_a_202_load_that_never_settles_exhausts_its_budget(
        self, harness, artifact, monkeypatch
    ):
        monkeypatch.setattr(settings, "build_poll_timeout", 0.05)
        manager, server = harness(sync_load=False, warm_polls=10_000)
        with pytest.raises(ModelManagerError) as excinfo:
            _pull(manager, server, artifact, "2")
        assert excinfo.value.code == "readiness_timeout"
        assert excinfo.value.retryable is True

    def test_a_200_that_reports_a_non_servable_state_is_still_waited_out(self, harness, artifact):
        """The body is believed over its own status line.

        A 200 whose state is not servable violates the contract, but the cost of
        trusting it is a version routed before it can serve; the cost of
        distrusting it is one status read.
        """
        manager, server = harness()
        original = server._load

        def _lying_load(body):
            resp = original(body)
            resp._body["state"] = "warming"
            server.state[resp._body["model"]["version"]] = "warming"
            server._warming[resp._body["model"]["version"]] = 1
            return resp

        server._load = _lying_load
        _pull(manager, server, artifact, "2")
        assert server.state["2"] == "ready"
        assert [c for c in server.calls if "status" in c], "it did not take the 200 at face value"

    def test_a_load_that_never_settles_says_which_way_it_failed(
        self, harness, artifact, monkeypatch
    ):
        """ "Never appeared" and "appeared but never became servable" differ.

        They have different causes and different next steps, so a single generic
        timeout sends an operator looking in the wrong place.
        """
        monkeypatch.setattr(settings, "build_poll_timeout", 0.05)
        manager, server = harness(sync_load=False, warm_polls=10_000)
        with pytest.raises(ModelManagerError) as excinfo:
            _pull(manager, server, artifact, "2")
        assert "never became servable" in excinfo.value.message

    def test_a_status_endpoint_that_is_simply_gone_gives_up_early(
        self, harness, artifact, monkeypatch
    ):
        """A blip is not a failure, but a dead endpoint should not cost 600s.

        Reported as itself rather than as a readiness timeout minutes later
        naming the wrong problem.
        """
        monkeypatch.setattr(settings, "build_poll_timeout", 600.0)
        manager, server = harness(sync_load=False, warm_polls=10_000)
        server._status = lambda: _error(500, "MODEL_OPERATION_FAILED", "status is down")
        with pytest.raises(ModelManagerError) as excinfo:
            _pull(manager, server, artifact, "2")
        assert "consecutive attempts" in excinfo.value.message
        assert excinfo.value.retryable is True

    @pytest.mark.parametrize("terminal", ["failed", "error"])
    def test_terminal_states_beyond_the_literal_failed_are_honoured(
        self, harness, artifact, terminal
    ):
        manager, server = harness(sync_load=False, warm_polls=1, warm_outcome=terminal)
        with pytest.raises(ModelManagerError) as excinfo:
            _pull(manager, server, artifact, "2")
        assert excinfo.value.code == "load_failed"
        assert excinfo.value.retryable is False

    @pytest.mark.parametrize("transient", ["unloaded", "unavailable"])
    def test_states_a_healthy_cold_start_passes_through_are_not_terminal(
        self, harness, artifact, monkeypatch, transient
    ):
        """A version legitimately reads as evicted while its engine builds.

        Calling that terminal would abort a healthy cold start, so it waits the
        budget out and reports a retryable timeout instead.
        """
        monkeypatch.setattr(settings, "build_poll_timeout", 0.05)
        manager, server = harness(sync_load=False, warm_polls=1, warm_outcome=transient)
        with pytest.raises(ModelManagerError) as excinfo:
            _pull(manager, server, artifact, "2")
        assert excinfo.value.code == "readiness_timeout"
        assert excinfo.value.retryable is True

    @pytest.mark.parametrize(("status", "code"), [(401, "UNAUTHORIZED"), (404, "MODEL_NOT_LOADED")])
    def test_a_deterministic_status_failure_is_surfaced_immediately(
        self, harness, artifact, status, code
    ):
        """A rejected credential answers identically on every remaining poll.

        Waiting it out would hide the cause behind a readiness timeout AND hand
        back a retryable error, and re-driving a pull re-issues the load — the
        one call this adapter never replays.
        """
        manager, server = harness(sync_load=False, warm_polls=10_000)
        server._status = lambda: _error(status, code, "nope")
        with pytest.raises(ModelManagerError) as excinfo:
            _pull(manager, server, artifact, "2")
        assert excinfo.value.retryable is False
        assert excinfo.value.http_status == status
        assert len([c for c in server.calls if "status" in c]) == 1, "it did not keep polling"

    def test_the_poll_gets_what_is_left_of_the_pull_budget_not_a_fresh_one(
        self, harness, artifact, monkeypatch
    ):
        """A slow load plus a slow warm-up must not stack past one pull budget.

        Otherwise DFW times out and re-POSTs the pull while this one is still
        building — the duplicate engine build ``client.load`` refuses to cause
        itself.
        """
        monkeypatch.setattr(settings, "unified_load_timeout", 0.30)
        monkeypatch.setattr(settings, "build_poll_timeout", 10.0)
        monkeypatch.setattr(settings, "build_poll_interval", 0.01)
        manager, server = harness(sync_load=False, warm_polls=10_000)

        original = server._load

        def _slow_load(body):
            time.sleep(0.25)  # most of the budget spent before polling starts
            return original(body)

        server._load = _slow_load
        began = time.monotonic()
        with pytest.raises(ModelManagerError) as excinfo:
            _pull(manager, server, artifact, "2")
        elapsed = time.monotonic() - began

        assert excinfo.value.code == "readiness_timeout"
        # Capped by the load budget, NOT load_timeout + build_poll_timeout.
        assert elapsed < 1.0, f"poll opened a fresh budget, not the remainder ({elapsed:.2f}s)"

    def test_a_fast_accepted_load_still_gets_the_full_poll_budget(
        self, harness, artifact, monkeypatch
    ):
        """The cap must not punish the case it is not aimed at.

        A 202 that returns instantly should get the whole warm-up window, which
        is why the deadline is not simply opened before the load.
        """
        monkeypatch.setattr(settings, "unified_load_timeout", 900.0)
        monkeypatch.setattr(settings, "build_poll_timeout", 10.0)
        monkeypatch.setattr(settings, "build_poll_interval", 0.0)
        manager, server = harness(sync_load=False, warm_polls=25)
        _pull(manager, server, artifact, "2")
        assert server.state["2"] == "ready"

    def test_a_sync_load_does_not_poll_at_all(self, harness, artifact):
        manager, server = harness()
        _pull(manager, server, artifact, "2")
        assert [c for c in server.calls if "status" in c] == []


class TestRoutingSemanticsGap:
    """The merge-vs-replace gap for full_switch.

    Some backends merge routing rather than replacing it. On those, a full
    switch issued while a shadow is live used to leave the shadow in place, and
    convergence then reported ``routing_not_exclusive``. These tests opt into
    merging and cover the fix: the full switch clears the shadow itself.

    On what these tests do and do not establish. The fake's merge is
    SLOT-COUPLED -- naming ``shadow`` suppresses the whole carry-over, canary
    included -- which is the very assumption ``_surviving_roles`` rests on. So
    no test here can falsify that assumption; it is written into the double.
    What they do pin is the ADAPTER's half: that an occupied role, of either
    kind, makes the clear travel with the switch. That half was genuinely
    untested for the canary, and unpinned code does not survive refactors.
    """

    def test_full_switch_clears_a_surviving_shadow_on_a_merging_backend(self, harness, artifact):
        """A full switch to the version that is also the live shadow.

        A merging backend accepts ``{default: 2, shadow: 2}`` without a 409, so
        the clear is sent up front instead of relying on a retry.
        """
        manager, server = harness(sync_load=False, canary=False)
        server.merge_routing = True
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "shadow", "2")
        full = _apply(manager, server, "full_switch", "2")

        assert _converged(manager, server, full.applied) is True
        assert server.routing == {"mode": "single", "model": {"name": NAME, "version": "2"}}

    def test_full_switch_clears_a_shadow_of_another_version(self, harness, artifact):
        """Shadow v2 live, full switch to v3.

        Nothing asks one model to hold two roles, so a merging backend answers
        200 and the shadow survives. There is no 409, so the retry path never
        fires and the clear has to be sent up front.
        """
        manager, server = harness(versions=("1",), sync_load=False, canary=False)
        server.merge_routing = True
        _pull(manager, server, artifact, "2")
        _pull(manager, server, artifact, "3")
        _apply(manager, server, "shadow", "2")
        full = _apply(manager, server, "full_switch", "3")

        assert (
            _converged(manager, server, full.applied) is True
        ), "a shadow of another version survived the full switch"
        assert server.routing == {"mode": "single", "model": {"name": NAME, "version": "3"}}

    def test_a_full_switch_with_nothing_to_clear_keeps_the_field_off_the_wire(
        self, harness, artifact
    ):
        """The clear is derived from live state, never sent unconditionally.

        A backend with no shadow or canary must not see ``shadow`` on the wire,
        because the contract only promises clearing-by-assignment where the
        implementation supports it.
        """
        manager, server = harness(sync_load=False, canary=False)
        _pull(manager, server, artifact, "2")
        full = _apply(manager, server, "full_switch", "2")

        assert _converged(manager, server, full.applied) is True
        routing_writes = [c for c in server.calls if c.endswith("/api/v1/model/routing")]
        assert routing_writes, f"no routing write was recorded in {server.calls}"
        # Method and path only. A shadow-clearing retry would show up as a second
        # routing write, and the settled routing must carry no shadow key.
        assert (
            len(routing_writes) == 1
        ), f"a backend with nothing to clear took more than one routing write: {server.calls}"
        assert (
            "shadow" not in server.routing
        ), f"a shadow slot was created on a backend that had none: {server.routing}"

    def test_full_switch_clears_a_surviving_canary_on_a_merging_backend(self, harness, artifact):
        """The canary arm of ``_surviving_roles``, which nothing else reached.

        Before this test, deleting ``"canary"`` from that function's role tuple
        left the whole suite green -- the string was live code pinned by nothing.

        Read the limit honestly: this asserts the adapter names a role when only
        a canary is occupied. Whether ``shadow: null`` is SUFFICIENT to clear a
        canary is the backend's half, and this fake asserts that by construction.
        Unverified against a live merging backend -- see ``_surviving_roles``.
        """
        manager, server = harness(sync_load=False, canary=True)
        server.merge_routing = True
        _pull(manager, server, artifact, "2")

        _apply(manager, server, "canary", "2", weight=0.05)
        assert server.routing["mode"] == "canary", "the canary rung did not take"

        full = _apply(manager, server, "full_switch", "2")

        assert _converged(manager, server, full.applied) is True
        assert server.routing == {"mode": "single", "model": {"name": NAME, "version": "2"}}

    def test_full_switch_to_a_third_version_clears_a_surviving_canary(self, harness, artifact):
        """The same, with no 409 anywhere to mask it.

        The backend's conflict retry only fires when the switch target IS the
        occupied arm. Switching to a third version removes that safety net, so
        this is the case where ``_surviving_roles`` is the only thing standing
        between the ladder and a canary that never clears.
        """
        manager, server = harness(sync_load=False, canary=True)
        server.merge_routing = True
        _pull(manager, server, artifact, "2")
        _pull(manager, server, artifact, "3")

        _apply(manager, server, "canary", "2", weight=0.05)
        assert server.routing["mode"] == "canary"

        full = _apply(manager, server, "full_switch", "3")

        assert _converged(manager, server, full.applied) is True
        assert server.routing == {
            "mode": "single",
            "model": {"name": NAME, "version": "3"},
        }, "a canary for a different version survived the switch"


class TestCleanupSafety:
    def test_retiring_the_baseline_and_sweeping_it_leaves_the_live_version_serving(
        self, harness, artifact
    ):
        """The default path, end to end: retire v1, sweep v1, keep serving v2.

        The sweep reports v1 reclaimed without deleting anything: the baseline
        was never pulled into this harness's repository. Actual rmtree lives in
        ``TestDiskReclaim``; this pins that the live version keeps serving.

        Measured against both real backends before this default was flipped.
        deft-aoi-inference pins Triton's ``version_policy`` to
        ``specific:[primary, candidate]``; RTVI-CV refuses to evict a routed
        version at all (409 MODEL_IN_USE).
        """
        manager, server = harness()

        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        assert server.state["2"] == "ready"
        _retire(manager, server, "1")

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            response = manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        assert server.state["1"] == "unloaded"
        assert server.state["2"] == "ready", "the live version must still be serving"
        # Reclaimed with nothing deleted. Reporting that as a refusal re-offered
        # the version to the sweep on every tick, forever.
        assert response.disk_reclaimed is True, "the baseline directory is already gone"
        assert response.status == "removed"

    def test_a_deployment_can_still_disable_unload(self, harness, artifact, monkeypatch):
        """The escape hatch, for an image older than the per-version fix.

        Granularity is a property of the build, so the switch outlives the
        defect: pointed at an older backend, a deployment sets this false and the
        unload verb declines to make the call at all rather than risk the live
        version. The promotion still completes; only the eviction is skipped.

        It no longer gates disk: it protects a live version from a model-granular
        eviction, which a reclaim cannot trigger. The second half says so.
        """
        manager, server = harness()

        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        assert server.state["2"] == "ready"
        # Retired before the flag goes off: the reclaim is a collector and the
        # residency root refuses a version still in memory.
        _retire(manager, server, "1")
        unloads = server.calls.count("POST /api/v1/model/unload")

        monkeypatch.setattr(settings, "unified_allow_unload", False)
        with patch("src.clients.http.requests.request", side_effect=server.handle):
            with pytest.raises(ModelManagerError) as excinfo:
                manager.unload(UnloadRequest(model_name=NAME, version="1"))

        assert excinfo.value.code == "conflict"
        # On the message, because every guard in the stack answers "conflict".
        assert "unload is disabled for this deployment" in excinfo.value.message
        assert server.state["2"] == "ready", "the live version must still be serving"
        assert server.calls.count("POST /api/v1/model/unload") == unloads

        # The reclaim runs regardless and evicts nothing doing it.
        with patch("src.clients.http.requests.request", side_effect=server.handle):
            manager.cleanup(CleanupRequest(model_name=NAME, version="1"))
        assert server.calls.count("POST /api/v1/model/unload") == unloads

    def test_a_model_granular_backend_still_takes_the_live_version_down(self, harness, artifact):
        """Why the escape hatch exists — the defect, preserved as a witness.

        This was the shipped behaviour once: unloading the retired baseline
        evicted the live primary too, while the response still claimed only v1
        went. It is fixed in both backends now, so it lives on as a knob rather
        than as the default -- and as the reason the setting was inverted rather
        than deleted.

        Note what the Model Manager cannot do about it: a model-wide eviction
        empties the resident set, which is exactly what the unload convergence
        check reads as success. Nothing downstream would notice.

        Asserted through ``unload``, the only verb that still reaches the defect.
        """
        manager, server = harness(unload_granularity="model")

        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        _retire(manager, server, "1")

        assert server.state["1"] == "unloaded"
        assert server.state["2"] == "unloaded", "the live version went down with it"

    def test_cleanup_refuses_a_version_that_is_still_routed(self, harness, artifact):
        manager, server = harness()
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        with patch("src.clients.http.requests.request", side_effect=server.handle):
            with pytest.raises(ModelManagerError) as excinfo:
                manager.cleanup(CleanupRequest(model_name=NAME, version="2"))
        assert excinfo.value.code == "conflict"

    def test_a_status_without_routing_is_recovered_from_the_routing_endpoint(
        self, harness, artifact
    ):
        """`routing` is optional on model-status; its absence is not an answer.

        The serving image that dropped this key is what the resolution chain
        exists for. Cleanup must still refuse to delete the live primary -- but
        now it refuses because it READ the routing and saw v1 serving, which is
        a strictly better answer than "I could not tell".
        """
        manager, server = harness()
        _pull(manager, server, artifact, "2")
        original = server._status

        def _status_without_routing():
            resp = original()
            resp._body["model-status"].pop("routing", None)
            return resp

        server._status = _status_without_routing
        with patch("src.clients.http.requests.request", side_effect=server.handle):
            with pytest.raises(ModelManagerError) as excinfo:
                manager.cleanup(CleanupRequest(model_name=NAME, version="1"))
        assert (
            excinfo.value.code == "conflict"
        ), "refused because v1 is routed, not because unreadable"

    def test_cleanup_fails_closed_when_no_source_can_answer(self, harness, artifact):
        """The original guard, now that it takes THREE misses to reach it.

        This is the test the chain must not quietly delete: with every routing
        source blanked, the answer is "unknown", and cleanup must refuse rather
        than read an unreadable policy as an empty one.
        """
        manager, server = harness()
        _pull(manager, server, artifact, "2")
        original_status, original_handle = server._status, server.handle

        def _status_without_any_routing():
            resp = original_status()
            resp._body["model-status"].pop("routing", None)
            resp._body["model-status"].pop("default", None)
            return resp

        def _handle_without_routing_endpoint(method, url, **kwargs):
            if method == "GET" and "api/v1/model/routing" in url:
                return _Response(503, {"status": "HTTP/1.1 503", "reason": "DOWN"})
            return original_handle(method, url, **kwargs)

        server._status = _status_without_any_routing
        with patch(
            "src.clients.http.requests.request", side_effect=_handle_without_routing_endpoint
        ):
            with pytest.raises(ModelManagerError) as excinfo:
                manager.cleanup(CleanupRequest(model_name=NAME, version="1"))
        assert excinfo.value.code == "registry_unreachable"

    def test_cleanup_fails_closed_when_the_backend_is_unreadable(self, harness):
        manager, _server = harness()

        def _down(*a, **k):
            raise __import__("requests").ConnectionError("no route")

        with patch("src.clients.http.requests.request", side_effect=_down):
            with pytest.raises(ModelManagerError) as excinfo:
                manager.cleanup(CleanupRequest(model_name=NAME, version="1"))
        assert excinfo.value.code == "registry_unreachable"


def _record_inventories(manager):
    """Collect every inventory ``cleanup`` composes, without changing one.

    A list that wrongly nominates the live primary leaves no trace on disk here,
    because this caller only deletes what it asked about -- but others read it.
    """
    seen = []
    original = manager.adapter.inventory

    def _spy(name, **kwargs):
        inventory = original(name, **kwargs)
        seen.append(inventory)
        return inventory

    manager.adapter.inventory = _spy
    return seen


class TestDiskReclaim:
    """``cleanup`` reclaiming a retired version's directory, end to end. Asserted
    against the real ``tmp_path`` repo, not call counts: the failure guarded
    against is an ``rmtree`` of weights that are serving.
    """

    @pytest.fixture
    def repo(self, tmp_path):
        return Path(tmp_path) / "repo"

    @pytest.mark.parametrize("publishes_deletable", [True, False])
    def test_cleanup_reclaims_disk_on_both_a_publishing_and_a_silent_backend(
        self, harness, artifact, repo, publishes_deletable
    ):
        """One adapter, no capability flag, both backend shapes. With deft's array
        it takes the backend at its word; on silent RTVI-CV it must reach the
        same answer from the affirmed eviction, or retired directories pile up
        forever. v2 is asserted present too -- "reclaimed the wrong disk" also
        satisfies ``disk_reclaimed is True``.
        """
        manager, server = harness(publishes_deletable=publishes_deletable)

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        _retire(manager, server, "1")

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            response = manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        assert response.disk_reclaimed is True
        assert not (repo / NAME / "1").exists(), "the retired directory was not reclaimed"
        assert (repo / NAME / "2").exists(), "the live version's weights were deleted"
        assert server.state["2"] == "ready", "the live version must still be serving"

    def test_an_explicit_no_streams_attached_does_not_block_the_reclaim(
        self, harness, artifact, repo
    ):
        """``streams: false`` says nothing is attached. A ``bool`` branch ahead of
        the arithmetic (``bool`` subclasses ``int``) reads it as one stream and
        refuses the version forever. Driven through the backend because the
        refusal defended against is the one ``cleanup`` raises.
        """
        manager, server = harness(publishes_deletable=True)

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        _retire(manager, server, "1")
        server.instances["1"] = [{"streams": False}]

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            assert manager.cleanup(CleanupRequest(model_name=NAME, version="1")).disk_reclaimed

        assert not (repo / NAME / "1").exists()

    def test_a_row_that_does_not_say_unloaded_is_never_reclaimed(self, harness, artifact, repo):
        """The payload is malformed -- ``state`` is required -- which is what a field
        defect looks like. Through ``state_of`` an absent state reads as
        ``unloaded`` and empties both residency inputs while the backend still
        publishes the version as deletable.
        """
        manager, server = harness(publishes_deletable=True)

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        _retire(manager, server, "1")
        # Nominated from state only the backend can see, while the row it
        # publishes says nothing.
        server.stateless = {"1"}

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            with pytest.raises(ModelManagerError) as excinfo:
                manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        assert excinfo.value.code == "conflict"
        assert "resident in serving memory" in excinfo.value.message
        assert excinfo.value.retryable is True
        assert (repo / NAME / "1").exists(), "the directory was reclaimed on an unreadable row"

    def test_cleanup_never_reclaims_the_live_primary_under_a_model_wide_unload(
        self, harness, artifact, repo
    ):
        """On the older model-granular build, retiring the idle baseline evicts the
        live primary too: v2's row reads ``state: "unloaded"`` while still
        carrying ``default_for: ["model"]``, and "not resident, therefore
        removable" would delete the weights answering traffic. The published list
        is checked as well as the disk, since this caller only deletes what it
        asked about.
        """
        manager, server = harness(unload_granularity="model", publishes_deletable=False)

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        _retire(manager, server, "1")
        inventories = _record_inventories(manager)

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        assert server.state["2"] == "unloaded", "the model-wide sweep really happened"
        assert (repo / NAME / "2").exists(), "the live version's weights were deleted"
        assert inventories[-1].deletable == ["1"], "the swept primary was offered up"

    def test_a_published_empty_list_withholds_the_delete(self, harness, artifact, repo):
        """Inference is the fallback for silence, never a second opinion. The
        backend knows things this process cannot -- a stream still draining, a
        directory its loader has open -- so its ``[]`` outranks local deduction.
        """
        manager, server = harness(publishes_deletable=True, deletable_override=[])

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        _retire(manager, server, "1")

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            response = manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        assert response.disk_reclaimed is False
        assert (repo / NAME / "1").exists(), "inference talked over the backend's own answer"

    def test_the_reclaim_path_never_calls_unload(self, harness, artifact, repo):
        """The sweep issues no unload at the wire. The eviction already happened at
        promotion time, and re-performing it on RTVI-CV answers ``404
        MODEL_NOT_LOADED`` and reclaims nothing -- the defect this replaces. A
        read naming no version still authorises nothing, asserted alongside.
        """
        manager, server = harness(publishes_deletable=False)

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        # Evicted at the full switch, hours ago as far as the sweep knows.
        _retire(manager, server, "1")
        server.calls.clear()

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            plain = manager.adapter.inventory(NAME)
            response = manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        assert "POST /api/v1/model/unload" not in server.calls
        assert plain.available is True, "it answered, it just answered nothing"
        assert plain.deletable == [], "a read naming no version nominated one"
        assert response.disk_reclaimed is True
        assert not (repo / NAME / "1").exists()

    @pytest.mark.parametrize("publishes_deletable", [True, False])
    def test_the_roots_narrow_a_published_list_as_well_as_an_inferred_one(
        self, harness, artifact, repo, publishes_deletable
    ):
        """Verbatim is not final. The adapter does not second-guess a published
        list, but the backend has been measured publishing a directory that was
        never loaded, and nothing in its answer covers v2 running its engine out
        of v1's directory. Put the roots inside the inference rather than in
        ``cleanup`` and the ``True`` arm reclaims a directory in use.
        """
        manager, server = harness(publishes_deletable=publishes_deletable)

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        _retire(manager, server, "1")
        # v2 is ready and serving, out of v1's directory. Reproduced live.
        server.instances["2"] = [
            {"config": f"/models/{NAME}/1/serving.pbtxt", "engine": f"/models/{NAME}/1/m.engine"}
        ]

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            with pytest.raises(ModelManagerError) as excinfo:
                manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        assert excinfo.value.code == "conflict"
        assert (repo / NAME / "1").exists(), "the directory a live instance reads was removed"

    @pytest.mark.parametrize("asserted", [True, False])
    def test_an_omitted_row_reclaims_disk_only_on_the_callers_assertion(
        self, harness, artifact, repo, asserted
    ):
        """RTVI-CV drops an unloaded version's row entirely, and that absence is
        identical for an evicted, a never-loaded and an unknown version. Only the
        caller's own record of the eviction can tell them apart -- the one term
        in this decision that is not a read.
        """
        manager, server = harness(
            omit_unloaded=True,
            publishes_deletable=False,
        )

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        _retire(manager, server, "1")

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            response = manager.cleanup(
                CleanupRequest(model_name=NAME, version="1", evicted_by_caller=asserted)
            )
        reclaims = asserted

        assert response.disk_reclaimed is reclaims
        assert (repo / NAME / "1").exists() is not reclaims
        assert (repo / NAME / "2").exists(), "the live version's weights were deleted"


class TestOneReclaimsReads:
    """How many times a reclaim reads the backend, pinned so ``cleanup``'s prose
    cannot drift from it again. Not a budget -- but the order must not change:
    the read that authorises is the oldest, the reads that refuse the newest.
    """

    def test_a_reclaim_makes_three_status_reads_and_two_routing_reads(self, harness, artifact):
        manager, server = harness(publishes_deletable=True)

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        _retire(manager, server, "1")
        server.calls.clear()

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        reads = [call for call in server.calls if call.startswith("GET")]
        assert sum("model/status" in call for call in reads) == 3
        assert sum("model/routing" in call for call in reads) == 2
        # A collector writes nothing: no unload, no load, no routing write.
        assert [call for call in server.calls if call.startswith("POST")] == []


class TestUnloadTouchesNoFiles:
    """The new eviction-only verb, asserted against the real ``tmp_path`` repo: the
    failure guarded against is a promotion losing its rollback target. The fake
    serves ``model/routing`` too, or ``_evict`` fails closed with a 503 and every
    "it did not delete" assertion passes for the wrong reason.
    """

    @pytest.fixture
    def repo(self, tmp_path):
        return Path(tmp_path) / "repo"

    def test_unload_evicts_and_leaves_the_directory_on_disk(self, harness, artifact, repo):
        """What the promotion path calls at full switch: v1 leaves the GPU and its
        weights stay put for a rollback. Implementing this as ``cleanup`` with
        ``disk_reclaimed`` discarded takes the directory too, and only the
        filesystem assertion notices.
        """
        manager, server = harness(publishes_deletable=True)

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        inventories = _record_inventories(manager)

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            response = manager.unload(UnloadRequest(model_name=NAME, version="1"))

        assert server.state["1"] == "unloaded", "the eviction did not happen"
        assert server.state["2"] == "ready", "the live version must still be serving"
        assert (repo / NAME / "1").exists(), "unload reclaimed the rollback target"
        assert (repo / NAME / "2").exists()
        assert response.version == "1"
        # One read is the pre-unload build check; the second decides a delete.
        assert len(inventories) == 1
        assert "disk_reclaimed" not in response.model_dump()
        assert not hasattr(response, "disk_reclaimed")

    def test_unload_and_cleanup_refuse_a_routed_version_identically(self, harness, artifact):
        """One refusal stack, two verbs. Wiring the endpoint straight to
        ``adapter.unload`` leaves cleanup refusing the live default while unload
        evicts it.
        """
        manager, server = harness()
        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            with pytest.raises(ModelManagerError) as unload_err:
                manager.unload(UnloadRequest(model_name=NAME, version="2"))
            with pytest.raises(ModelManagerError) as cleanup_err:
                manager.cleanup(CleanupRequest(model_name=NAME, version="2"))

        for err in (unload_err.value, cleanup_err.value):
            assert err.code == "conflict"
            assert err.http_status == 409
            assert err.retryable is False
        assert unload_err.value.message == cleanup_err.value.message
        assert server.state["2"] == "ready", "the live version was evicted"


class TestReclaimPolicy:
    """G0 and ``dry_run``, against the real repository. Both are about WHERE a
    decision sits, so ``server.calls`` is the witness: it separates a guard that
    short-circuits ahead of the reads from one that withholds only the rmtree.
    """

    @pytest.fixture
    def repo(self, tmp_path):
        return Path(tmp_path) / "repo"

    def test_the_age_floor_refuses_before_anything_is_evicted(
        self, harness, artifact, repo, monkeypatch
    ):
        """G0 ahead of ``_evict``, with the eviction as the witness. v1 is freshly
        staged, unrouted and idle -- what a pulled-but-unrouted version looks
        like -- so every guard inside ``_evict`` passes it and only
        ``server.state["1"] == "ready"`` catches a misplaced floor.
        """
        monkeypatch.setattr(settings, "reclaim_min_age_s", 86400.0)
        manager, server = harness(publishes_deletable=True)

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            with pytest.raises(ModelManagerError) as excinfo:
                manager.cleanup(CleanupRequest(model_name=NAME, version="1"))

        assert excinfo.value.code == "conflict"
        assert excinfo.value.retryable is False
        assert "floor" in excinfo.value.message
        assert server.state["1"] == "ready", "the version was evicted before the floor refused"
        assert (repo / NAME / "1").exists()
        # The unload endpoint was never even reached.
        assert "POST /api/v1/model/unload" not in server.calls

    def test_a_dry_run_withholds_only_the_removal(self, harness, artifact, repo):
        """Every guard and the real inventory read happen; only the rmtree does not,
        so what is reported is the server's predicate and not a simulation of it.
        """
        manager, server = harness(publishes_deletable=True)

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        _retire(manager, server, "1")
        inventories = _record_inventories(manager)

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            response = manager.cleanup(CleanupRequest(model_name=NAME, version="1", dry_run=True))

        assert response.disk_reclaimed is False
        assert (repo / NAME / "1").exists(), "a dry run deleted the directory"
        assert len(inventories) == 1, "the read that decides the delete was skipped"
        assert inventories[0].deletable == ["1"], "the withheld delete was not actually earned"
        # The same request without the flag reclaims, so the withholding above is
        # the flag, not some other refusal.
        with patch("src.clients.http.requests.request", side_effect=server.handle):
            wet = manager.cleanup(CleanupRequest(model_name=NAME, version="1"))
        assert wet.disk_reclaimed is True
        assert not (repo / NAME / "1").exists()

    def test_the_deployment_flag_forces_a_dry_run_over_the_request(
        self, harness, artifact, repo, monkeypatch
    ):
        monkeypatch.setattr(settings, "reclaim_dry_run", True)
        manager, server = harness(publishes_deletable=True)

        _pull(manager, server, artifact, "1")
        _pull(manager, server, artifact, "2")
        _apply(manager, server, "full_switch", "2")
        _retire(manager, server, "1")
        server.calls.clear()

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            response = manager.cleanup(CleanupRequest(model_name=NAME, version="1", dry_run=False))

        assert response.disk_reclaimed is False
        assert (repo / NAME / "1").exists()
        assert "GET /api/v1/model/status" in server.calls, "the predicate was not evaluated"

    def test_both_reclaim_policy_defaults_ship_off(self):
        """The upgrade is a no-op for every existing caller of ``cleanup``."""
        assert settings.reclaim_dry_run is False
        assert settings.reclaim_min_age_s == 0.0
        request = CleanupRequest(model_name=NAME, version="1")
        assert request.dry_run is False
        assert request.min_age_s is None


class TestVersionFloor:
    def test_evicted_versions_stay_in_the_reported_floor(self, harness, artifact):
        """DFW mints its next version above this list.

        An evicted version still owns its directory, so dropping it would hand
        back a colliding floor.
        """
        manager, server = harness(versions=("1", "5"))
        _pull(manager, server, artifact, "2")
        with patch("src.clients.http.requests.request", side_effect=server.handle):
            state = manager.serving_state(NAME)
        assert set(state.known_versions) == {"1", "2", "5"}

    def test_a_backend_that_omits_unloaded_rows_still_yields_a_complete_floor(
        self, harness, artifact
    ):
        """The revision lets a conforming backend drop the tombstone entirely.

        "Implementations may omit unloaded versions instead of retaining entries
        with ``state: unloaded``; absence of a requested version from a
        successful status snapshot means that version is not loaded."

        Both representations must therefore produce the SAME floor as the
        sibling test above. What makes the shrink dangerous rather than
        cosmetic is that it is not detectable as a gap: ``loaded_available``
        stays True, so DFW does not fail closed -- it mints confidently onto a
        directory that unified cleanup never reclaimed.
        """
        manager, server = harness(versions=("1",), omit_unloaded=True)
        # v5 is pulled for real, so it OWNS A DIRECTORY: the disk claim is what
        # must survive the row disappearing. A version with neither row nor
        # directory is genuinely gone and must not be resurrected.
        _pull(manager, server, artifact, "5")
        _pull(manager, server, artifact, "2")
        server.state["5"] = "unloaded"

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            state = manager.serving_state(NAME)

        assert set(state.known_versions) == {"1", "2", "5"}
        assert state.loaded_available is True

    def test_a_pull_survives_a_native_update_in_flight(self, harness, artifact):
        """409 UPDATE_IN_FLIGHT must reach the caller as RETRYABLE.

        The contract's own remedy is "wait until model/status no longer reports
        updating, then retry", so classifying it terminal fails a promotion over
        a condition that clears itself.
        """
        manager, server = harness(versions=("1",), update_in_flight=("2",))
        with pytest.raises(ModelManagerError) as excinfo:
            _pull(manager, server, artifact, "2")
        assert excinfo.value.retryable is True, "a self-clearing conflict must be re-drivable"


class TestLoadConflicts:
    """409s on load: which are terminal and which describe the desired state."""

    def test_a_load_refused_as_a_conflict_is_accepted_when_it_is_already_servable(
        self, harness, artifact
    ):
        """The contract permits refusing a redundant load instead of no-op-ing it.

        "already-loaded models" is now enumerated among the Conflict cases, and
        the spec names NO code for it -- so this cannot be keyed on in the error
        mapping and has to be resolved by asking model-status. A re-pull of a
        version that is already serving is the ordinary DFW retry, so answering
        it with a terminal conflict fails a promotion whose desired state has
        already been reached.
        """

        def _loads():
            return [c for c in server.calls if c.endswith("/model/load")]

        manager, server = harness(versions=("1",), already_loaded_conflict=True)
        _pull(manager, server, artifact, "2")
        loads_after_first = len(_loads())

        _pull(manager, server, artifact, "2")

        assert server.state["2"] == "ready"
        assert len(_loads()) == loads_after_first + 1, "resolved via status, not re-issued"

    def test_a_load_conflict_still_raises_when_the_version_is_not_servable(self, harness, artifact):
        """Tolerance is conditional on status AGREEING it is already servable.

        A genuine conflict keeps its classification. (A self-clearing one never
        reaches this check at all -- see the sibling test below.) Swallowing a
        409 unconditionally would report a promotion as pulled with nothing
        loaded.
        """
        manager, server = harness(versions=("1",), already_loaded_conflict=True)
        _pull(manager, server, artifact, "2")
        server.state["2"] = "warming"

        with pytest.raises(ModelManagerError) as excinfo:
            _pull(manager, server, artifact, "2")
        assert excinfo.value.http_status == 409

    def test_a_self_clearing_conflict_is_not_resolved_against_a_ready_row(self, harness, artifact):
        """A CODED self-clearing 409 is outside the tolerance branch's scope.

        UPDATE_IN_FLIGHT is scoped to the MODEL ("a model update for this model
        is still in progress", ``field: value.name``), so a version can read
        ``ready`` while its own load is refused -- that row describes the
        pre-update engine. Accepting it would report a load the backend REFUSED
        as done, against bytes it never saw, and nothing downstream re-checks.

        The tolerance exists for the CODELESS already-loaded case only. Anything
        translate() calls retryable is re-driven instead, which is the contract's
        own remedy: "wait until model/status no longer reports updating, then
        retry".
        """
        manager, server = harness(versions=("1", "2"), update_in_flight=("2",))
        server.state["2"] = "ready"  # load-bearing: status WOULD report it servable

        with pytest.raises(ModelManagerError) as excinfo:
            _pull(manager, server, artifact, "2")

        assert excinfo.value.retryable is True, "a self-clearing conflict must be re-drivable"
        # The guard's POSITION, not just its existence: status cannot say whether
        # a self-clearing conflict has cleared, so asking is waste.
        assert "GET /api/v1/model/status" not in server.calls, "not even asked"


def _second_replica() -> ModelManager:
    """A Model Manager standing in for a separate process behind the same backend.

    It gets its own adapter and client, so nothing but the backend is shared --
    which is the whole claim, and which only became true when the store went.
    ``__init__`` used to default to a module-level ``swap_store`` singleton, so
    two managers built in one process shared one dict and would have agreed by
    accident. This test was written against that signature and confirmed to fail
    there before the store was deleted.
    """
    return ModelManager(
        adapter=UnifiedServingAdapter(client=UnifiedModelClient(base_url="http://inf"))
    )


class TestTwoReplicasOverOneBackend:
    """A poll does not care which process served the apply.

    Both managers run against the same ``FakeUnifiedServer``. It has to be this
    fake and not ``test_manager.py``'s ``FakeAdapter``, whose ``serving_state``
    pops a scripted list and would give the two replicas different observations.
    """

    def test_two_model_managers_over_one_backend_answer_identically(self, harness, artifact):
        """Apply through one process, poll through another.

        If ``applied`` is absent this falls through to ``status``, so the test
        still runs against the older API.
        """
        a, server = harness()
        b = _second_replica()
        _pull(a, server, artifact, "2")
        full = _apply(a, server, "full_switch", "2")

        applied = full.applied

        def says_landed(manager):
            with patch("src.clients.http.requests.request", side_effect=server.handle):
                return manager.convergence(applied).converged

        assert says_landed(a) == says_landed(
            b
        ), "the replica that did not serve the apply gave a different answer"

    def test_two_replicas_agree_on_the_whole_report(self, harness, artifact):
        """Not just the boolean: the reason and its terminality must match too.

        The converged case alone would never compare ``terminal``, so this also
        polls an expectation that live state does not satisfy. ``at`` is
        excluded because it is set per call and would flake.
        """
        a, server = harness()
        b = _second_replica()
        _pull(a, server, artifact, "2")
        landed = _apply(a, server, "full_switch", "2").applied

        # An expectation that live state does NOT satisfy.
        missed = landed.model_copy(update={"control": "canary", "canary_weight": 0.25})

        for expectation in (landed, missed):
            with patch("src.clients.http.requests.request", side_effect=server.handle):
                report_a = a.convergence(expectation)
                report_b = b.convergence(expectation)
            assert report_a.model_dump(exclude={"at"}) == report_b.model_dump(
                exclude={"at"}
            ), f"replicas disagreed about {expectation.control}"

        with patch("src.clients.http.requests.request", side_effect=server.handle):
            assert a.convergence(landed).converged is True
            unconverged = a.convergence(missed)
        assert unconverged.converged is False, "the negative case must actually be negative"
        assert unconverged.reason is not None


class TestStreamRoutesProfile:
    """The ladder end to end with ``unified_stream_routes`` on.

    The gap this closes is not a merge-vs-replace one. RTVI-CV MERGES the
    default policy correctly and still does not re-derive the binding of a
    stream that is already attached: promoting a live shadow with a
    default-only write makes it build a FRESH inference bin for the candidate
    and initialise it with the OUTGOING version's Triton ensemble, which is
    evicted moments later as that version goes idle. Naming the streams in the
    same write keeps them on the instance the load built for them.

    The fake cannot reproduce that -- it has no bins and no ensembles. What it
    can enforce is the half that is ours: that the arm is emitted at every
    route-expressible rung, never at a canary rung, and never at all against a
    profile that does not advertise it.
    """

    def _routes_capable(self, harness, monkeypatch, **kwargs):
        monkeypatch.setattr(settings, "unified_stream_routes", True)
        manager, server = harness(sync_load=False, stream_routes=True, **kwargs)
        server.merge_routing = True
        return manager, server

    def test_the_whole_ladder_lands_with_every_write_naming_its_streams(
        self, harness, artifact, monkeypatch
    ):
        """shadow then full_switch, both mirrored, promotion still converges.

        The full_switch assertion below pins ``shadow: null`` INSIDE the route
        object, which is the field on the promotion path and the one a fake
        could most easily be wrong about. It is not the fake's assumption:

        * The spec makes it legal. ``StreamRoute.shadow`` is ``nullable: true``
          (``unified-inference-api/openapi.yaml``), exactly as
          ``DefaultRoutingPolicy.shadow`` is -- ``additionalProperties: false``
          constrains WHICH keys may appear, and nullability is stated
          separately, per field.
        * A real backend accepted it. Driving RTVI-CV
          (``metropolis-perception:3.3.0-amd64-26.08.1-172-with-api-fixes``)
          through ``POST /v1/models/apply`` with a live stream attached, the
          promotion rung went out as::

              {'default': {'model': {'name': 'GDINO', 'version': '5'},
                           'shadow': None},
               'routes': [{'streams': 'all',
                           'model': {'name': 'GDINO', 'version': '5'},
                           'shadow': None}]}

          answered 202, and the stream moved to GDINO@5 on the instance the
          load had built (``gie1001``) with no second bin created.
        """
        manager, server = self._routes_capable(harness, monkeypatch, canary=False)
        _pull(manager, server, artifact, "2")

        _apply(manager, server, "shadow", "2")
        assert server.routes == [
            {
                "streams": "all",
                "model": {"name": NAME, "version": "1"},
                "shadow": {"name": NAME, "version": "2"},
            }
        ], "the shadow rung left the streams on the outgoing version"

        full = _apply(manager, server, "full_switch", "2")
        assert _converged(manager, server, full.applied) is True
        assert server.routing == {"mode": "single", "model": {"name": NAME, "version": "2"}}
        # ``_surviving_roles`` saw the live shadow, so the switch names it as a
        # clear; ``shadow`` IS route-expressible, so the mirror carries it through
        # rather than dropping to default-only.
        assert server.routes == [
            {"streams": "all", "model": {"name": NAME, "version": "2"}, "shadow": None}
        ]

    def test_the_canary_rung_is_not_mirrored_but_the_switch_that_clears_it_is(
        self, harness, artifact, monkeypatch
    ):
        """The two halves of the canary case, which do not answer the same way.

        The canary RUNG has no ``StreamRoute`` representation -- the schema is
        additionalProperties:false over {streams, model, shadow} -- so it goes
        out default-only. The switch that CLEARS it does not name a canary at
        all: ``_surviving_roles`` expresses every clear as ``shadow: null``, so
        that write is route-shaped and is mirrored like any other switch.
        """
        manager, server = self._routes_capable(harness, monkeypatch, canary=True)
        _pull(manager, server, artifact, "2")

        _apply(manager, server, "canary", "2", weight=0.05)
        assert server.routing["mode"] == "canary", "the canary rung did not take"
        assert server.routes is None, "a canary policy has no StreamRoute representation"

        full = _apply(manager, server, "full_switch", "2")
        assert _converged(manager, server, full.applied) is True
        assert server.routes == [
            {"streams": "all", "model": {"name": NAME, "version": "2"}, "shadow": None}
        ]

    def test_a_profile_without_stream_routes_rejects_the_arm(
        self, harness, artifact, monkeypatch, _servers
    ):
        """Why the setting exists instead of being unconditional.

        AOI Inference declares ``supports-explicit-stream-routes: false`` and the
        contract makes an unsupported field a hard rejection, so turning the flag
        on against that profile fails every promotion -- and it fails at the
        FIRST rung, so nothing is half-applied and no traffic moves.

        The answer names the setting rather than blaming this service: it is the
        one unsupported field we emit on purpose, and the operator who turned it
        on is the one who can turn it off.
        """
        monkeypatch.setattr(settings, "unified_stream_routes", True)
        manager, server = harness(sync_load=False, canary=False)
        _pull(manager, server, artifact, "2")

        with pytest.raises(ModelManagerError) as excinfo:
            _apply(manager, server, "shadow", "2")
        assert (excinfo.value.code, excinfo.value.http_status) == ("bad_request", 400)
        assert "MODEL_MANAGER_UNIFIED_STREAM_ROUTES" in excinfo.value.message
        assert server.rejected == ["value.routes"]
        # The rejection IS this test's subject, so drain it before the central
        # leak assertion re-reports it as an accidental field leak.
        server.rejected.clear()
