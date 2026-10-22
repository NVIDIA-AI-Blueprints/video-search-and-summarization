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

"""Wire contract for the unified control client: bodies, paths, error mapping."""

from unittest.mock import patch

import pytest

from src.adapters.unified.client import UnifiedModelClient
from src.core.errors import ModelManagerError


class FakeResponse:
    """Minimal stand-in for ``requests.Response`` used to mock HTTP calls."""

    def __init__(self, status_code: int, json_body=None, text: str = ""):
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.closed = False

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json

    def close(self):
        self.closed = True


def _client():
    return UnifiedModelClient(base_url="http://inf")


def _err(code, message="boom", **extra):
    return {
        "status": "HTTP/1.1 400",
        "reason": f"{code}, {message}",
        "error": {"code": code, "message": message, **extra},
    }


class TestGoldenBodies:
    """Exact request shapes. The spec rejects any field the target lacks."""

    def test_load_uses_the_key_value_envelope(self):
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            _client().load("m", "1")
        method, url = req.call_args.args
        assert (method, url) == ("POST", "http://inf/api/v1/model/load")
        assert req.call_args.kwargs["json"] == {
            "key": "model",
            "value": {"name": "m", "version": "1"},
        }

    def test_load_omits_idempotency_key_entirely(self):
        """The unified load envelope is additionalProperties:false.

        There is no idempotency field to carry, so one must never be emitted --
        the backend answers FIELD_UNSUPPORTED rather than ignoring it.
        """
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            _client().load("m", "1")
        assert set(req.call_args.kwargs["json"]["value"]) == {"name", "version"}

    def test_batch_size_is_sent_only_when_asked_for(self):
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            _client().load("m", "1", batch_size=4)
        assert req.call_args.kwargs["json"]["value"]["batch_size"] == 4

    def test_unload_uses_the_model_envelope(self):
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "unloaded"})
            _client().unload("m", "2")
        method, url = req.call_args.args
        assert (method, url) == ("POST", "http://inf/api/v1/model/unload")
        assert req.call_args.kwargs["json"] == {
            "key": "model",
            "value": {"name": "m", "version": "2"},
        }

    def test_put_routing_uses_the_routing_envelope(self):
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            _client().put_routing({"model": {"name": "m", "version": "2"}})
        method, url = req.call_args.args
        assert (method, url) == ("POST", "http://inf/api/v1/model/routing")
        assert req.call_args.kwargs["json"] == {
            "key": "routing",
            "value": {"default": {"model": {"name": "m", "version": "2"}}},
        }

    def test_status_filter_goes_through_params_not_a_bare_kwarg(self):
        """``requests`` owns the name ``stream``; the unified API also defines it.

        Passing the filter as a bare kwarg would make requests stream the
        response instead of filtering it, silently.
        """
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"model-status": {}})
            _client().get_status("m", "1")
        assert req.call_args.kwargs["params"] == {"model": "m", "version": "1"}
        assert "stream" not in req.call_args.kwargs

    def test_status_unwraps_the_hyphenated_key(self):
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"model-status": {"models": []}})
            assert _client().get_status("m") == {"models": []}

    def test_routing_read_unwraps_the_routing_key(self):
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"routing": {"default": {"a": 1}}})
            assert _client().get_routing() == {"default": {"a": 1}}


class TestIfRevision:
    def test_emitted_when_supplied(self):
        """No caller supplies one today; a backend without CAS says so itself."""
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            _client().put_routing({"model": {"name": "m", "version": "2"}}, if_revision=42)
        assert req.call_args.kwargs["json"]["value"]["if_revision"] == 42

    def test_omitted_entirely_when_not_supplied(self):
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            _client().put_routing({"model": {"name": "m", "version": "2"}})
        assert "if_revision" not in req.call_args.kwargs["json"]["value"]


class TestLoadTransport:
    def test_the_load_is_never_retried(self, monkeypatch):
        """A load that is still building looks exactly like one that was lost.

        Replaying it starts a second engine build inside the backoff window
        rather than recovering anything -- and the serving stack documents that
        concurrent reload as a use-after-free race against in-flight inference.
        """
        import requests as requests_mod

        with (
            patch("src.clients.http.requests.request") as req,
            patch("src.clients.http.sleep"),
        ):
            req.side_effect = requests_mod.Timeout("slow")
            with pytest.raises(ModelManagerError) as excinfo:
                _client().load("m", "1")
        assert req.call_count == 1
        assert excinfo.value.code == "load_failed"

    @pytest.mark.parametrize("failure", ["timeout", "server_error"], ids=["on-timeout", "on-5xx"])
    def test_a_compare_and_set_routing_write_is_never_replayed(self, failure):
        """A CAS write that failed may well have landed.

        Replaying it comes back REVISION_CONFLICT against the revision the first
        attempt bumped, so we would report a failure for a write that succeeded.
        That is true of a 5xx as much as a timeout, so the transport must not
        retry it at all — re-driving a CAS write is the caller's job, because
        only the caller can re-read the revision first.
        """
        import requests as requests_mod

        with (
            patch("src.clients.http.requests.request") as req,
            patch("src.clients.http.sleep"),
        ):
            if failure == "timeout":
                req.side_effect = requests_mod.Timeout("slow")
            else:
                req.return_value = FakeResponse(503, _err("MODEL_OPERATION_FAILED"))
            with pytest.raises(ModelManagerError):
                _client().put_routing({"model": {"name": "m", "version": "2"}}, if_revision=7)
        assert req.call_count == 1

    def test_a_plain_routing_write_is_still_retried_on_timeout(self):
        import requests as requests_mod

        with (
            patch("src.clients.http.requests.request") as req,
            patch("src.clients.http.sleep"),
        ):
            req.side_effect = requests_mod.Timeout("slow")
            with pytest.raises(ModelManagerError):
                _client().put_routing({"model": {"name": "m", "version": "2"}})
        assert req.call_count > 1

    def test_load_uses_its_own_long_timeout(self, monkeypatch):
        from src.config import settings

        monkeypatch.setattr(settings, "unified_load_timeout", 777.0)
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            _client().load("m", "1")
        assert req.call_args.kwargs["timeout"] == 777.0


class TestErrorMapping:
    """Mapping keys on (status, code) -- the code alone is not a discriminator."""

    @pytest.mark.parametrize(
        ("status", "code", "expect_code", "retryable"),
        [
            # MODEL_NOT_LOADED carries two different meanings at two statuses.
            (404, "MODEL_NOT_LOADED", "not_found", False),
            (409, "MODEL_NOT_LOADED", "conflict", True),
            (409, "MODEL_IN_USE", "conflict", False),
            (400, "VERSION_INVALID", "bad_request", False),
            (400, "INVALID_ARGUMENT", "bad_request", False),
            (409, "MODE_DISABLED", "conflict", False),
            # New in the revised contract, and the one 409 besides
            # MODEL_NOT_LOADED that clears without anyone intervening.
            (409, "UPDATE_IN_FLIGHT", "conflict", True),
            (500, "MODEL_OPERATION_FAILED", "internal", True),
        ],
    )
    def test_status_and_code_pairs(self, status, code, expect_code, retryable):
        with (
            patch("src.clients.http.requests.request") as req,
            patch("src.clients.http.sleep"),
        ):
            req.return_value = FakeResponse(status, _err(code))
            with pytest.raises(ModelManagerError) as excinfo:
                _client().unload("m", "1")
        assert (excinfo.value.code, excinfo.value.retryable) == (expect_code, retryable)

    def test_field_unsupported_is_reported_as_our_own_misconfiguration(self):
        """We built a body this target does not implement.

        That means the configured profile disagrees with the deployed service --
        a local misconfiguration, not something the caller could have avoided.

        The faulted field is deliberately one with NO dedicated branch. The
        three that have one -- ``canary``/``shadow`` name a promotion stage,
        ``routes`` names a setting -- are answered with an actionable 400
        instead, and each is pinned separately. This case is the fallthrough.
        """
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(
                400,
                _err("FIELD_UNSUPPORTED", "'gpus' is not supported", field="value.default.gpus"),
            )
            with pytest.raises(ModelManagerError) as excinfo:
                _client().put_routing({"model": {"name": "m", "version": "1"}})
        assert excinfo.value.code == "internal"
        assert "gpus" in excinfo.value.message

    def test_an_unsupported_routes_arm_names_the_setting_that_produced_it(self):
        """``routes`` is the one unsupported field we emit on purpose.

        Blaming the Model Manager with a 500 would be wrong twice over: the
        operator set ``MODEL_MANAGER_UNIFIED_STREAM_ROUTES`` themselves, and the
        log line would point at this service rather than at the switch to flip.
        """
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(
                400, _err("FIELD_UNSUPPORTED", "'routes' is not supported", field="value.routes")
            )
            with pytest.raises(ModelManagerError) as excinfo:
                _client().put_routing({"model": {"name": "m", "version": "1"}})
        assert (excinfo.value.code, excinfo.value.http_status) == ("bad_request", 400)
        assert "MODEL_MANAGER_UNIFIED_STREAM_ROUTES" in excinfo.value.message

    def test_a_nested_routes_fault_is_not_mistaken_for_a_shadow_stage(self):
        """The stage heuristic matches on a SUBSTRING, so order matters.

        A backend that faults ``value.routes[0].shadow`` rather than the arm as a
        whole would otherwise be answered "disable the shadow stage" -- which
        does not remove the arm, so the next rung fails again with nothing
        connecting it to the setting the operator actually changed.
        """
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(
                400,
                _err("FIELD_UNSUPPORTED", "unsupported", field="value.routes[0].shadow"),
            )
            with pytest.raises(ModelManagerError) as excinfo:
                _client().put_routing({"model": {"name": "m", "version": "1"}})
        assert "MODEL_MANAGER_UNIFIED_STREAM_ROUTES" in excinfo.value.message
        assert "deployment_stages" not in excinfo.value.message

    def test_load_409_update_in_flight_is_retryable(self):
        """The table above drives ``unload``; ``load`` has its own call site.

        ``load`` translates through a separate branch with a 5xx-only refinement
        in between, so a fix applied to the shared mapping is not automatically
        exercised on the path that matters most -- the pull gate. A terminal
        classification here destroys a promotion over a condition the contract
        says to wait out.
        """
        with (
            patch("src.clients.http.requests.request") as req,
            patch("src.clients.http.sleep"),
        ):
            req.return_value = FakeResponse(
                409,
                _err(
                    "UPDATE_IN_FLIGHT",
                    "a model update for this model is still in progress",
                    field="value.name",
                ),
            )
            with pytest.raises(ModelManagerError) as excinfo:
                _client().load("m", "1")
        assert (excinfo.value.code, excinfo.value.retryable) == ("conflict", True)

    def test_field_unknown_is_reported_as_our_own_defect(self):
        """The revision splits the two field errors, and they blame differently.

        FIELD_UNSUPPORTED means the field is valid for the operation but this
        profile does not implement it. FIELD_UNKNOWN means the operation does not
        define the field AT ALL -- so we put something bogus on the wire, and the
        operator must be pointed at this service rather than at the caller.
        Untreated it falls to the terminal ``bad_request`` default with no ERROR
        log, which blames DFW for a body DFW never composed.
        """
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(
                400,
                _err(
                    "FIELD_UNKNOWN",
                    "'banana' is not a recognized field for model/load",
                    field="value.banana",
                ),
            )
            with pytest.raises(ModelManagerError) as excinfo:
                _client().put_routing({"model": {"name": "m", "version": "1"}})
        assert excinfo.value.code == "internal"

    @pytest.mark.parametrize("status", [408, 429])
    def test_shed_requests_are_retryable_not_terminal(self, status):
        """Backpressure must not fail a promotion permanently.

        The service sheds POST overflow after a 10ms wait on an in-flight
        semaphore it shares with inference traffic, answering in FastAPI's own
        ``{"detail": ...}`` shape from BEFORE the handler runs. Classified as a
        client error, DFW marks the promotion failed and rolls back a routing
        write that never took effect.
        """
        with (
            patch("src.clients.http.requests.request") as req,
            patch("src.clients.http.sleep"),
        ):
            req.return_value = FakeResponse(
                status, {"detail": "API request admission limit reached"}
            )
            with pytest.raises(ModelManagerError) as excinfo:
                _client().put_routing({"model": {"name": "m", "version": "1"}})
        assert "admission limit" in excinfo.value.message
        assert excinfo.value.retryable is True
        assert excinfo.value.code != "bad_request"

    def test_a_deterministic_build_failure_is_terminal_with_the_remedy(self):
        """The unified load funnels every build outcome through one 500.

        A version directory the serving process cannot write to will fail
        identically on every retry, so surfacing it as transient makes the caller
        re-drive a GPU build that can never succeed. The legacy adapter already
        classifies this terminally; both must give the same answer.
        """
        with (
            patch("src.clients.http.requests.request") as req,
            patch("src.clients.http.sleep"),
        ):
            req.return_value = FakeResponse(
                500,
                _err(
                    "MODEL_OPERATION_FAILED",
                    "export_onnx.py failed: [Errno 13] Permission denied: '/models/m/2/model.onnx'",
                ),
            )
            with pytest.raises(ModelManagerError) as excinfo:
                _client().load("m", "2")
        assert excinfo.value.retryable is False
        assert "fsGroup" in excinfo.value.message

    def test_an_ordinary_5xx_build_failure_stays_retryable(self):
        with (
            patch("src.clients.http.requests.request") as req,
            patch("src.clients.http.sleep"),
        ):
            req.return_value = FakeResponse(
                500, _err("MODEL_OPERATION_FAILED", "CUDA out of memory")
            )
            with pytest.raises(ModelManagerError) as excinfo:
                _client().load("m", "2")
        assert excinfo.value.retryable is True

    def test_non_json_error_body_still_produces_a_message(self):
        with (
            patch("src.clients.http.requests.request") as req,
            patch("src.clients.http.sleep"),
        ):
            req.return_value = FakeResponse(400, None, text="<html>nope</html>")
            with pytest.raises(ModelManagerError) as excinfo:
                _client().unload("m", "1")
        assert "nope" in excinfo.value.message

    def test_hint_is_folded_into_the_message(self):
        with (
            patch("src.clients.http.requests.request") as req,
            patch("src.clients.http.sleep"),
        ):
            req.return_value = FakeResponse(
                409, _err("MODEL_IN_USE", "in use", hint="update routing first")
            )
            with pytest.raises(ModelManagerError) as excinfo:
                _client().unload("m", "1")
        assert "update routing first" in excinfo.value.message


# One policy per rung of the DFW ladder, plus the body the full_switch conflict
# retry re-sends. Which keys a policy carries is what decides whether it can be
# mirrored as a ``StreamRoute``, so each rung has to be stated separately.
_ROUTING_POLICIES = {
    "shadow": {
        "model": {"name": "m", "version": "1"},
        "shadow": {"name": "m", "version": "2"},
    },
    "canary": {
        "model": {"name": "m", "version": "1"},
        "canary": {"name": "m", "version": "2"},
        "canary_percent": 5.0,
    },
    "full-switch": {"model": {"name": "m", "version": "2"}},
    "clear-the-shadow": {"model": {"name": "m", "version": "2"}, "shadow": None},
}


class TestStreamRouteMirror:
    """``unified_stream_routes``: the routing write also names the streams.

    RTVI-CV merges the default policy but does not re-derive the binding of a
    stream that is already attached, so a default-only promotion of a live
    shadow builds a fresh inference bin for the candidate against the OUTGOING
    version's ensemble and dies when that ensemble is evicted. The ``routes``
    arm keeps the attached streams on the instance the load built for them.

    The flag is OFF by default, which is what the rest of this file -- and the
    rest of the suite -- already asserts by construction: every body pinned
    elsewhere is a flag-off body.
    """

    def _sent(self, policy, **kwargs):
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            _client().put_routing(policy, **kwargs)
        assert req.call_count == 1, "one request, never two -- see put_routing"
        return req.call_args.kwargs["json"]["value"]

    @pytest.mark.parametrize("rung", ["shadow", "canary", "full-switch", "clear-the-shadow"])
    def test_off_by_default_the_body_is_exactly_the_default_policy(self, rung):
        """The portable body, unchanged, for every rung.

        ``test_put_routing_uses_the_routing_envelope`` above already pins the
        full-switch shape end to end; this widens it to the rungs that shape
        does not reach, because the mirror is keyed on WHICH policy keys are
        present and each rung presents a different set.
        """
        policy = _ROUTING_POLICIES[rung]
        assert self._sent(policy) == {"default": policy}

    @pytest.mark.parametrize("rung", ["shadow", "full-switch", "clear-the-shadow"])
    def test_on_the_same_policy_is_also_named_for_the_streams(self, rung, monkeypatch):
        """One POST carrying both arms, at every route-expressible rung.

        The shadow rung matters as much as the switch: a promotion whose shadow
        write stayed default-only never attached the candidate to the live
        streams, so the switch is still the first bind and still takes the bad
        path.
        """
        from src.config import settings

        monkeypatch.setattr(settings, "unified_stream_routes", True)
        policy = _ROUTING_POLICIES[rung]
        assert self._sent(policy) == {
            "default": policy,
            "routes": [{"streams": "all", **policy}],
        }

    def test_on_a_canary_policy_is_not_mirrored_at_all(self, monkeypatch):
        """``StreamRoute`` is additionalProperties:false over {streams, model, shadow}.

        There is no canary field to carry, and mirroring the policy MINUS the
        canary would be worse than not mirroring: ``{streams: all, model: X}``
        pins every stream to the primary and silently defeats the canary the
        write was meant to establish.
        """
        from src.config import settings

        monkeypatch.setattr(settings, "unified_stream_routes", True)
        policy = _ROUTING_POLICIES["canary"]
        assert self._sent(policy) == {"default": policy}

    def test_on_a_policy_that_merely_mentions_canary_is_not_mirrored(self, monkeypatch):
        """The predicate is route-EXPRESSIBILITY, not "is there a live canary".

        A ``canary: null`` clear names no canary version and would mirror
        harmlessly, but ``StreamRoute`` has no field to put it in, so it is
        excluded by the same subset test. No caller emits this policy today --
        ``_surviving_roles`` expresses every clear as ``shadow: null`` -- which
        is precisely why the branch needs pinning here rather than upstream.
        """
        from src.config import settings

        monkeypatch.setattr(settings, "unified_stream_routes", True)
        policy = {"model": {"name": "m", "version": "2"}, "canary": None}
        assert self._sent(policy) == {"default": policy}

    def test_on_routes_and_if_revision_coexist(self, monkeypatch):
        """Both are rtvi-cv-only extensions, so they travel together or not at all."""
        from src.config import settings

        monkeypatch.setattr(settings, "unified_stream_routes", True)
        policy = {"model": {"name": "m", "version": "2"}}
        assert self._sent(policy, if_revision=42) == {
            "default": policy,
            "routes": [{"streams": "all", **policy}],
            "if_revision": 42,
        }

    def test_on_the_mirror_is_a_copy_not_an_alias_of_the_default(self, monkeypatch):
        """A shared dict would let a later edit of one arm rewrite the other."""
        from src.config import settings

        monkeypatch.setattr(settings, "unified_stream_routes", True)
        value = self._sent({"model": {"name": "m", "version": "2"}})
        assert value["routes"][0] is not value["default"]
