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

"""The optional pre-load engine build, and the switch that turns it off.

The single most important property here is the OFF case: with no builder
configured, not one HTTP request may be made to a builder and the load body must
be byte-identical to what it was before this feature existed. Every other test
in this file describes what happens once the switch is on.
"""

import json
from unittest.mock import patch

import pytest

from src.adapters.unified.builder import build_extra_load_params
from src.adapters.unified.client import UnifiedModelClient
from src.config import settings
from src.core.errors import ModelManagerError

NAME, VERSION = "GDINO", "2"
BUILDER = "http://builder:8088"


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def close(self):
        pass


def _ready(**over):
    """A spec-shaped ReadyBuildStatus. Every required field is present."""
    body = {
        "name": NAME,
        "version": VERSION,
        "state": "ready",
        "started_at": "2026-08-26T10:00:00Z",
        "completed_at": "2026-08-26T10:05:00Z",
        "serving_manifest": "/models/rtvi/GDINO/2/serving/manifest.json",
        "engine_sha256": "a" * 64,
        "extra_load_params": {"config_file": "/models/rtvi/GDINO/2/serving/nvinferserver.pbtxt"},
    }
    body.update(over)
    return body


def _err(code, message, hint=None):
    """The builder's ErrorResponse -- the same envelope the inference API uses."""
    error = {"code": code, "message": message}
    if hint:
        error["hint"] = hint
    return {"status": "HTTP/1.1 409", "reason": f"{code}, {message}", "error": error}


class TestTheSwitch:
    """With no builder configured, nothing about the Model Manager changes."""

    def test_no_builder_means_no_request_and_no_extra_params(self, monkeypatch):
        monkeypatch.setattr(settings, "builder_url", None)
        with patch("src.clients.http.requests.request") as req:
            assert build_extra_load_params(NAME, VERSION, deadline=0.0) == {}
        assert req.call_count == 0, "a deployment with no builder must never call one"

    def test_an_empty_builder_url_is_also_off(self, monkeypatch):
        """compose passes settings through ``${VAR:-}``, which arrives as ''."""
        monkeypatch.setattr(settings, "builder_url", "")
        with patch("src.clients.http.requests.request") as req:
            assert build_extra_load_params(NAME, VERSION, deadline=0.0) == {}
        assert req.call_count == 0

    def test_the_load_body_is_unchanged_when_no_params_are_supplied(self):
        """The byte-identical guarantee, asserted on the wire."""
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            UnifiedModelClient(base_url="http://inf").load(NAME, VERSION)
        assert req.call_args.kwargs["json"] == {
            "key": "model",
            "value": {"name": NAME, "version": VERSION},
        }


class TestRelay:
    def test_extra_load_params_are_relayed_verbatim(self):
        extra = {"config_file": "/models/rtvi/GDINO/2/serving/nvinferserver.pbtxt"}
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            UnifiedModelClient(base_url="http://inf").load(NAME, VERSION, extra_load_params=extra)
        assert req.call_args.kwargs["json"]["value"] == {
            "name": NAME,
            "version": VERSION,
            "config_file": "/models/rtvi/GDINO/2/serving/nvinferserver.pbtxt",
        }

    def test_an_unrecognised_key_is_relayed_rather_than_dropped(self):
        """``ExtraLoadParams`` is additionalProperties:true, so it can grow.

        Relaying a key we do not recognise lets the backend reject it loudly --
        it owns the field set. Dropping it silently would risk a load that binds
        the wrong artifact, which is the failure this whole step exists to stop.
        """
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            UnifiedModelClient(base_url="http://inf").load(
                NAME, VERSION, extra_load_params={"engine_file": "/e.plan", "future_key": 1}
            )
        value = req.call_args.kwargs["json"]["value"]
        assert value["engine_file"] == "/e.plan"
        assert value["future_key"] == 1


class TestBuildOutcomes:
    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "builder_url", BUILDER)

    def test_a_build_that_is_already_ready_needs_no_poll(self):
        """A re-driven pull attaches to the immutable build and returns at once."""
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, _ready())
            params = build_extra_load_params(NAME, VERSION, deadline=1e9)
        assert params == {"config_file": "/models/rtvi/GDINO/2/serving/nvinferserver.pbtxt"}
        assert req.call_count == 1, "no status GET is needed when the POST already says ready"

    def test_the_build_is_polled_until_it_is_ready(self):
        states = [
            FakeResponse(
                202, {"name": NAME, "version": VERSION, "state": "queued", "submitted_at": "t"}
            ),
            FakeResponse(
                200, {"name": NAME, "version": VERSION, "state": "building", "started_at": "t"}
            ),
            FakeResponse(200, _ready()),
        ]
        with patch("src.clients.http.requests.request", side_effect=states) as req:
            params = build_extra_load_params(NAME, VERSION, deadline=1e9)
        assert params["config_file"].endswith("nvinferserver.pbtxt")
        assert [c.args[0] for c in req.call_args_list] == ["POST", "GET", "GET"]

    def test_a_failed_build_fails_the_promotion_and_quotes_the_builder(self):
        failed = {
            "name": NAME,
            "version": VERSION,
            "state": "error",
            "started_at": "t",
            "completed_at": "t",
            "error": {
                "code": "TRTEXEC_FAILED",
                "message": "unsupported op Foo",
                "hint": "re-export the ONNX",
            },
        }
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, failed)
            with pytest.raises(ModelManagerError) as excinfo:
                build_extra_load_params(NAME, VERSION, deadline=1e9)
        # The builder models its own retries via `attempt` and offers no rebuild.
        assert excinfo.value.retryable is False
        assert "TRTEXEC_FAILED" in excinfo.value.message
        assert "unsupported op Foo" in excinfo.value.message
        assert "re-export the ONNX" in excinfo.value.message

    def test_ready_without_extra_load_params_is_refused(self):
        """``ReadyBuildStatus`` requires them, so absence is a contract violation.

        Loading anyway is the dangerous reading: against a backend that resolves
        artifacts by name, a load carrying no build output binds the incumbent
        version's engine and reports success.
        """
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, _ready(extra_load_params=None))
            with pytest.raises(ModelManagerError) as excinfo:
                build_extra_load_params(NAME, VERSION, deadline=1e9)
        assert excinfo.value.retryable is False
        assert "extra_load_params" in excinfo.value.message

    def test_a_missing_staged_directory_is_terminal(self):
        """404 means the builder and we disagree about the model repo root."""
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(404, _err("BUILD_NOT_STAGED", "no such directory"))
            with pytest.raises(ModelManagerError) as excinfo:
                build_extra_load_params(NAME, VERSION, deadline=1e9)
        assert excinfo.value.retryable is False, "no retry reconciles two different mounts"
        assert excinfo.value.http_status == 404

    def test_a_build_contract_violation_is_terminal(self):
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(409, _err("BUILD_CONTRACT", "bare plan staged"))
            with pytest.raises(ModelManagerError) as excinfo:
                build_extra_load_params(NAME, VERSION, deadline=1e9)
        assert excinfo.value.retryable is False

    def test_an_unreachable_builder_fails_the_promotion(self, monkeypatch):
        """Never skipped.

        Skipping would load without the build output, which is exactly the input
        that makes a new version silently serve the incumbent's engine. A builder
        outage must cost one stalled promotion, not a corrupt one.
        """
        import requests as requests_mod

        monkeypatch.setattr(settings, "request_max_retries", 0)
        with (
            patch("src.clients.http.requests.request", side_effect=requests_mod.ConnectionError()),
            patch("src.clients.http.sleep"),
        ):
            with pytest.raises(ModelManagerError) as excinfo:
                build_extra_load_params(NAME, VERSION, deadline=1e9)
        assert excinfo.value.retryable is True, "an outage is worth re-driving"

    def test_an_unrecognised_state_is_waited_out_not_treated_as_failure(self):
        """A state this client has never heard of must not become an outage."""
        states = [
            FakeResponse(
                202, {"name": NAME, "version": VERSION, "state": "queued", "submitted_at": "t"}
            ),
            FakeResponse(
                200, {"name": NAME, "version": VERSION, "state": "verifying", "started_at": "t"}
            ),
            FakeResponse(200, _ready()),
        ]
        with patch("src.clients.http.requests.request", side_effect=states):
            assert build_extra_load_params(NAME, VERSION, deadline=1e9)["config_file"]

    def test_a_build_that_outlasts_the_budget_is_retryable(self):
        """The build survives the timeout, so a re-driven pull re-attaches."""
        building = FakeResponse(
            200, {"name": NAME, "version": VERSION, "state": "building", "started_at": "t"}
        )
        with patch("src.clients.http.requests.request", return_value=building):
            with pytest.raises(ModelManagerError) as excinfo:
                build_extra_load_params(NAME, VERSION, deadline=0.0)
        assert excinfo.value.retryable is True
        assert excinfo.value.http_status == 504


class TestBudgetAndIdentity:
    """Two guards found in review, both cheap and both protecting a promotion."""

    def test_a_reserved_key_from_the_builder_is_refused(self):
        """A builder may not retarget the load.

        ``ExtraLoadParams`` forbids ``name``/``version`` by schema, but we do not
        validate the builder's response against that schema. A builder that
        ignored it would point the request at a DIFFERENT version while the
        activation flow kept polling -- and reporting on -- the original tuple,
        so the promotion would report success against a version it never loaded.
        """
        for key in ("name", "version"):
            with patch("src.clients.http.requests.request") as req:
                req.return_value = FakeResponse(200, {"state": "ready"})
                with pytest.raises(ModelManagerError) as excinfo:
                    UnifiedModelClient(base_url="http://inf").load(
                        NAME, VERSION, extra_load_params={key: "9"}
                    )
            assert excinfo.value.retryable is False
            assert key in excinfo.value.message
            assert req.call_count == 0, "the request must never leave with a retargeted identity"

    def test_the_load_request_honours_a_supplied_timeout(self):
        """The load takes what is LEFT of the budget, not a fresh one.

        Without this the stages stack: a build can burn its own timeout and the
        load request would still open a full `unified_load_timeout`, so the pair
        outlasts the caller's pull budget and DFW re-POSTs the pull while this
        load is still running.
        """
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            UnifiedModelClient(base_url="http://inf").load(NAME, VERSION, timeout=12.5)
        assert req.call_args.kwargs["timeout"] == 12.5

    def test_the_load_request_defaults_to_the_full_load_timeout(self):
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(200, {"state": "ready"})
            UnifiedModelClient(base_url="http://inf").load(NAME, VERSION)
        assert req.call_args.kwargs["timeout"] == settings.unified_load_timeout


class TestStatusNotYetObservable:
    """A 404 from the status poll is not the same condition as a 404 from POST."""

    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "builder_url", BUILDER)

    def test_a_status_read_that_has_not_caught_up_does_not_kill_a_good_build(self):
        """The contract gives the two 404s different meanings:

            POST .../build            404 "The staged name/version directory does not exist."
            GET  .../build/{n}/{v}    404 "No build status exists for this name/version."

        The first means we and the builder disagree about the model repo root and
        no retry reconciles two mounts. The second can simply mean the status has
        not been persisted yet -- the contract does not order that write against
        the 202 -- and the very next poll may return ``ready``. Treating it as
        terminal would let a status read that has not caught up destroy a build
        that was running perfectly well.
        """
        states = [
            FakeResponse(
                202, {"name": NAME, "version": VERSION, "state": "queued", "submitted_at": "t"}
            ),
            FakeResponse(404, _err("BUILD_STATUS_NOT_FOUND", "no build status exists")),
            FakeResponse(200, _ready()),
        ]
        with patch("src.clients.http.requests.request", side_effect=states):
            params = build_extra_load_params(NAME, VERSION, deadline=1e9)
        assert params["config_file"].endswith("nvinferserver.pbtxt")

    def test_a_status_that_stops_being_reported_times_out_and_says_so(self):
        """Waiting it out must not turn a vanished build into a silence.

        Bounded by the same deadline that governs the loop, and the message
        distinguishes "running but unfinished" from "the builder stopped
        reporting it" so an operator is not sent looking for a slow build the
        builder no longer knows about.
        """
        # 1st check: inside the budget, so it polls. 2nd: past it, so it gives up.
        ticks = iter([0.0, 1e9])
        with (
            patch(
                "src.clients.http.requests.request",
                side_effect=[
                    FakeResponse(
                        202,
                        {"name": NAME, "version": VERSION, "state": "queued", "submitted_at": "t"},
                    ),
                    FakeResponse(404, _err("BUILD_STATUS_NOT_FOUND", "no build status exists")),
                ],
            ),
            patch("src.adapters.unified.builder.monotonic", lambda: next(ticks)),
        ):
            with pytest.raises(ModelManagerError) as excinfo:
                build_extra_load_params(NAME, VERSION, deadline=1.0)
        assert excinfo.value.retryable is True
        assert "no longer reporting a status" in excinfo.value.message

    def test_a_missing_staged_directory_on_submit_is_still_terminal(self):
        """The POST's 404 keeps its original meaning and stays terminal."""
        with patch("src.clients.http.requests.request") as req:
            req.return_value = FakeResponse(404, _err("BUILD_NOT_STAGED", "no such directory"))
            with pytest.raises(ModelManagerError) as excinfo:
                build_extra_load_params(NAME, VERSION, deadline=1e9)
        assert excinfo.value.retryable is False
        assert excinfo.value.http_status == 404
