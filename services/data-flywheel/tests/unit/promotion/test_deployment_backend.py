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

"""Unit tests for the Model Manager HTTP deployment backend (Plan 2, §8).

The HTTP layer is mocked by patching ``requests`` inside the client module (the
``responses`` library is not installed). Tests assert request bodies/URLs per
verb, PromotionStatus parsing (routing[]/loaded_versions), ErrorEnvelope ->
BackendError mapping, retry-on-transient behavior, and rollback == apply(
full_switch, previous).
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.lib.promotion.deployment_backend import (
    DeploymentBackendFactory,
)
from src.lib.promotion.deployment_backend.base import (
    ApplyResult,
    BackendError,
    PromotionControl,
    PullResult,
)
from src.lib.promotion.deployment_backend.model_manager_client import ModelManagerBackend

MODULE = "src.lib.promotion.deployment_backend.model_manager_client"
BASE_URL = "http://model-manager:8090"


def _make_response(status_code: int, json_body: dict | None) -> MagicMock:
    """Build a fake ``requests.Response`` with ``status_code``/``json()``/``url``."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.url = f"{BASE_URL}/v1/models/test"
    if json_body is None:
        resp.content = b""
        resp.json.side_effect = ValueError("no json")
    else:
        resp.content = b"{}"
        resp.json.return_value = json_body
    return resp


@pytest.fixture
def backend() -> ModelManagerBackend:
    # backoff 0 keeps retry tests fast. The short timeout (5) and pull_timeout
    # (55) are deliberately unequal, so a test asserting which budget a POST
    # picked up cannot pass by accident.
    return ModelManagerBackend(
        {
            "base_url": BASE_URL,
            "timeout": 5,
            "pull_timeout": 55,
            "max_retries": 3,
            "retry_backoff_factor": 0,
        }
    )


# -- Registration ------------------------------------------------------------ #
def test_backend_is_registered_with_factory():
    assert "model_manager" in DeploymentBackendFactory.get_available_kinds()
    created = DeploymentBackendFactory.create("model_manager", {"base_url": BASE_URL})
    assert isinstance(created, ModelManagerBackend)


def test_base_url_is_stripped_of_trailing_slash():
    b = ModelManagerBackend({"base_url": "http://x:9/", "timeout": 3})
    assert b.base_url == "http://x:9"
    assert b.timeout == 3.0


def test_pull_timeout_defaults_to_900_when_unset():
    # A config that never set pull_timeout gets the long default, so large pull
    # downloads no longer race the 30s one.
    b = ModelManagerBackend({"base_url": BASE_URL, "timeout": 30})
    assert b.timeout == 30.0
    assert b.pull_timeout == 900.0


def test_pull_timeout_is_read_from_config():
    b = ModelManagerBackend({"base_url": BASE_URL, "timeout": 30, "pull_timeout": 1200})
    assert b.pull_timeout == 1200.0


# -- pull -------------------------------------------------------------------- #
def test_pull_sends_correct_body_and_parses_response(backend):
    resp = _make_response(
        201,
        {
            "model_name": "changenet-aoi",
            "version": "1.4.2",
            "repo_path": "/models/changenet-aoi/2/",
            "status": "staged",
        },
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        result = backend.pull(
            "changenet-aoi",
            "1.4.2",
            "mlflow://changenet-aoi/1.4.2?token=secret",
            "onnx",
            "9f2ac41",
            idempotency_key="idem-1",
        )

    assert isinstance(result, PullResult)
    assert result == PullResult(
        model_name="changenet-aoi",
        version="1.4.2",
        repo_path="/models/changenet-aoi/2/",
        status="staged",
    )
    args, kwargs = mock_post.call_args
    assert args[0] == f"{BASE_URL}/v1/models/pull"

    assert kwargs["timeout"] == 55.0
    assert kwargs["json"] == {
        "model_name": "changenet-aoi",
        "version": "1.4.2",
        "artifact_uri": "mlflow://changenet-aoi/1.4.2?token=secret",
        "format": "onnx",
        "sha256": "9f2ac41",
        "idempotency_key": "idem-1",
    }


def test_pull_uses_pull_timeout_not_the_short_timeout(backend):
    # A >600MB weight pull routinely exceeds the short 30s timeout.
    resp = _make_response(
        201,
        {"model_name": "m", "version": "1", "repo_path": "/models/m/1/", "status": "staged"},
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.pull("m", "1", "uri", "onnx", "abc")
    _, kwargs = mock_post.call_args
    assert kwargs["timeout"] == 55.0  # pull_timeout, not timeout (5.0)


def test_apply_uses_the_short_timeout(backend):
    resp = _make_response(
        202,
        {
            "swap_id": "s1",
            "to_version": "1",
            "control": "shadow",
            "started_at": "2026-05-27T0Z",
            "applied": {
                "model_name": "m",
                "to_version": "1",
                "control": "shadow",
                "from_version": None,
                "canary_weight": None,
                "key": "k",
            },
        },
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.apply("m", "1", PromotionControl.SHADOW)
    _, kwargs = mock_post.call_args
    assert kwargs["timeout"] == 5.0


def test_cleanup_keeps_the_short_timeout(backend):
    # Only pull has a dedicated budget; cleanup is a lookup-sized request here.
    resp = _make_response(200, {"version": "1", "status": "removed", "disk_reclaimed": True})
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.cleanup("m", "1")
    _, kwargs = mock_post.call_args
    assert kwargs["timeout"] == 5.0


def test_pull_includes_spec_uri_when_present(backend):
    resp = _make_response(
        201,
        {"model_name": "m", "version": "1", "repo_path": "/p/", "status": "staged"},
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.pull(
            "m",
            "1",
            "uri",
            "onnx",
            "abc",
            spec_uri="s3://aoi-artifacts/runs/1/baseline_spec.yaml",
        )
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["spec_uri"] == "s3://aoi-artifacts/runs/1/baseline_spec.yaml"


def test_pull_omits_spec_uri_when_absent(backend):
    resp = _make_response(
        201,
        {"model_name": "m", "version": "1", "repo_path": "/p/", "status": "staged"},
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.pull("m", "1", "uri", "onnx", "abc")
    _, kwargs = mock_post.call_args
    assert "spec_uri" not in kwargs["json"]


def test_pull_does_not_log_spec_uri(backend):
    resp = _make_response(
        201,
        {"model_name": "m", "version": "1", "repo_path": "/p/", "status": "staged"},
    )
    secret_spec = "s3://bucket/spec.yaml?X-Amz-Signature=TOPSECRET"
    with (
        patch(f"{MODULE}.requests.post", return_value=resp),
        patch(f"{MODULE}.logger") as mock_logger,
    ):
        backend.pull("m", "1", "uri", "onnx", "abc", spec_uri=secret_spec)
    for call in mock_logger.info.call_args_list:
        assert "TOPSECRET" not in str(call)
        assert secret_spec not in str(call)


def test_pull_includes_threshold_when_present(backend):
    resp = _make_response(
        201,
        {"model_name": "m", "version": "1", "repo_path": "/p/", "status": "staged"},
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.pull("m", "1", "uri", "onnx", "abc", threshold=0.42)
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["threshold"] == 0.42


def test_pull_omits_threshold_when_absent(backend):
    resp = _make_response(
        201,
        {"model_name": "m", "version": "1", "repo_path": "/p/", "status": "staged"},
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.pull("m", "1", "uri", "onnx", "abc")
    _, kwargs = mock_post.call_args
    assert "threshold" not in kwargs["json"]


def test_pull_omits_idempotency_key_when_absent(backend):
    resp = _make_response(
        201,
        {
            "model_name": "m",
            "version": "1",
            "repo_path": "/models/m/1/",
            "status": "staged",
        },
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.pull("m", "1", "uri", "onnx", "abc")
    _, kwargs = mock_post.call_args
    assert "idempotency_key" not in kwargs["json"]


def test_pull_does_not_log_artifact_uri(backend):
    resp = _make_response(
        201,
        {"model_name": "m", "version": "1", "repo_path": "/p/", "status": "staged"},
    )
    secret_uri = "s3://bucket/model?X-Amz-Signature=TOPSECRET"
    with (
        patch(f"{MODULE}.requests.post", return_value=resp),
        patch(f"{MODULE}.logger") as mock_logger,
    ):
        backend.pull("m", "1", secret_uri, "onnx", "abc")
    for call in mock_logger.info.call_args_list:
        assert "TOPSECRET" not in str(call)
        assert secret_uri not in str(call)


# -- apply / rollback -------------------------------------------------------- #
def test_apply_sends_control_value_and_parses_response(backend):
    resp = _make_response(
        202,
        {
            "swap_id": "swap-01HX",
            "from_version": "1.4.1",
            "to_version": "1.4.2",
            "control": "canary",
            "started_at": "2026-05-27T11:20:02Z",
            "noop": False,
            "applied": {
                "model_name": "changenet-aoi",
                "to_version": "1.4.2",
                "control": "canary",
                "from_version": "1.4.1",
                "canary_weight": 0.25,
                "key": "idem-1",
            },
        },
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        result = backend.apply(
            "changenet-aoi",
            "1.4.2",
            PromotionControl.CANARY,
            canary_weight=0.05,
            idempotency_key="idem-2",
        )

    assert isinstance(result, ApplyResult)
    assert result.swap_id == "swap-01HX"
    assert result.from_version == "1.4.1"
    assert result.to_version == "1.4.2"
    assert result.control is PromotionControl.CANARY
    assert result.started_at == "2026-05-27T11:20:02Z"

    args, kwargs = mock_post.call_args
    assert args[0] == f"{BASE_URL}/v1/models/apply"
    assert kwargs["json"] == {
        "model_name": "changenet-aoi",
        "version": "1.4.2",
        "control": "canary",
        "canary_weight": 0.05,
        "idempotency_key": "idem-2",
    }


def test_apply_omits_optional_fields(backend):
    resp = _make_response(
        202,
        {
            "swap_id": "s1",
            "to_version": "2",
            "control": "full_switch",
            "started_at": "2026-05-27T11:20:02Z",
            "applied": {
                "model_name": "m",
                "to_version": "2",
                "control": "full_switch",
                "from_version": None,
                "canary_weight": None,
                "key": "k",
            },
        },
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        result = backend.apply("m", "2", PromotionControl.FULL_SWITCH)
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {"model_name": "m", "version": "2", "control": "full_switch"}
    assert result.from_version is None


def test_rollback_is_apply_full_switch_previous(backend):
    resp = _make_response(
        202,
        {
            "swap_id": "s-rb",
            "from_version": "1.4.2",
            "to_version": "1.4.1",
            "control": "full_switch",
            "started_at": "2026-05-27T12:00:00Z",
            "applied": {
                "model_name": "changenet-aoi",
                "to_version": "1.4.1",
                "control": "full_switch",
                "from_version": "1.4.2",
                "canary_weight": None,
                "key": "idem-rb",
            },
        },
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        result = backend.rollback("changenet-aoi", "1.4.1", idempotency_key="idem-rb")

    assert result.control is PromotionControl.FULL_SWITCH
    assert result.to_version == "1.4.1"
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {
        "model_name": "changenet-aoi",
        "version": "1.4.1",
        "control": "full_switch",
        "idempotency_key": "idem-rb",
    }


# -- status ------------------------------------------------------------------ #
def test_an_enveloped_410_is_mapped_by_its_envelope_and_not_retried(backend):
    """The retired status route answers 410; the client must classify it exactly.

    Covers that `_error_from_response` prefers the ErrorEnvelope over the
    per-status fallback, and that a non-5xx is not retried. The poll loop reads
    `http_status` off this error to decide tick versus terminal.
    """
    resp = _make_response(
        410,
        {"error": {"code": "bad_request", "message": "poll convergence", "retryable": False}},
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        with pytest.raises(BackendError) as exc_info:
            backend.convergence({"model_name": "m", "key": "k"})

    err = exc_info.value
    assert err.code == "bad_request", "the envelope's code must win over the 410 fallback"
    assert err.retryable is False
    assert err.http_status == 410
    assert mock_post.call_count == 1, "a non-5xx must not be retried"


def test_an_unenveloped_status_falls_back_to_its_status_code(backend):
    """No envelope in the body: fall back on the HTTP status alone.

    404 maps to `not_found` and stays non-retryable, so a not-yet-rolled replica
    costs a tick rather than the promotion.
    """
    with patch(f"{MODULE}.requests.post", return_value=_make_response(404, None)):
        with pytest.raises(BackendError) as exc_info:
            backend.convergence({"model_name": "m", "key": "k"})

    assert exc_info.value.code == "not_found"
    assert exc_info.value.retryable is False
    assert exc_info.value.http_status == 404


# -- unload ------------------------------------------------------------------ #
def test_unload_sends_body_and_parses_response(backend):
    """The only place in DFW that pins the unload path.

    Mutation: point ``unload`` at ``/v1/models/cleanup`` -> red. That mutation is
    the whole hazard: cleanup deletes, and the promotion path calls this on the
    retired baseline.
    """
    resp = _make_response(200, {"version": "1.4.0", "status": "removed"})
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        result = backend.unload("changenet-aoi", "1.4.0")
    assert result == {"version": "1.4.0", "status": "removed"}
    args, kwargs = mock_post.call_args
    assert args[0] == f"{BASE_URL}/v1/models/unload"
    assert kwargs["json"] == {"model_name": "changenet-aoi", "version": "1.4.0"}


def test_unload_does_not_warn_about_a_missing_disk_reclaimed(backend, caplog):
    """Absence of ``disk_reclaimed`` is this endpoint's CONTRACT, not a degradation.

    Mutation: ``def unload(...): return self.cleanup(...)`` -> cleanup's "upgrade
    the Model Manager to find out" warning fires on every promotion.
    """
    import logging

    resp = _make_response(200, {"version": "1", "status": "removed"})
    with caplog.at_level(logging.WARNING):
        with patch(f"{MODULE}.requests.post", return_value=resp):
            backend.unload("changenet-aoi", "1")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_unload_against_an_old_model_manager_logs_and_reraises(backend, caplog):
    """A 404 must NOT become a cleanup fallback -- that would delete the rollback
    target. Mutation: add a ``return self.cleanup(...)`` in the 404 branch -> red
    on both the raise and the log text."""
    import logging

    resp = _make_response(404, None)
    with caplog.at_level(logging.ERROR):
        with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
            with pytest.raises(BackendError) as exc_info:
                backend.unload("changenet-aoi", "1")

    assert exc_info.value.http_status == 404
    assert "NOT falling back" in caplog.text

    assert [c.args[0] for c in mock_post.call_args_list] == [f"{BASE_URL}/v1/models/unload"]


def test_unload_keeps_the_short_timeout(backend):
    """Not the pull budget: an eviction is a control-plane call, not a download.

    Mutation: pass ``timeout=self.pull_timeout`` -> red (5 vs 55).
    """
    resp = _make_response(200, {"version": "1", "status": "removed"})
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.unload("changenet-aoi", "1")
    assert mock_post.call_args.kwargs["timeout"] == 5


def test_default_backend_unload_raises_not_implemented():
    """Concrete, not abstract, so an out-of-tree backend still instantiates -- and no
    silent delegation to ``cleanup``, which deletes.
    """
    from src.lib.promotion.deployment_backend.base import DeploymentBackend

    class OnlyCleanup(DeploymentBackend):
        def __init__(self):
            self.cleanup_calls = []

        def pull(self, *a, **k):
            raise NotImplementedError

        def apply(self, *a, **k):
            raise NotImplementedError

        def convergence(self, *a, **k):
            raise NotImplementedError

        def rollback(self, *a, **k):
            raise NotImplementedError

        def cleanup(self, model_name, version, **kwargs):
            self.cleanup_calls.append((model_name, version))
            return {"version": version, "status": "removed", "disk_reclaimed": True}

    b = OnlyCleanup()  # instantiable: unload is concrete, not abstract
    with pytest.raises(NotImplementedError):
        b.unload("m", "1")
    assert b.cleanup_calls == []


def test_cleanup_abc_declares_the_kwargs_the_sweep_sends():
    """The sweep calls ``cleanup(model, version, min_age_s=..., dry_run=...)``. An ABC
    that does not declare them strands any implementation written to the old
    signature, and blocks autospec-hardening the sweep's backend mocks.
    """
    import inspect

    from src.lib.promotion.deployment_backend.base import DeploymentBackend

    base = inspect.signature(DeploymentBackend.cleanup).parameters
    for name in ("min_age_s", "dry_run", "evicted_by_caller"):
        assert name in base  # clean failure, not KeyError below
        assert base[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert base["min_age_s"].default is None
    # Ships inert: the narrowing flags must never default on, and the one that
    # WIDENS must never arrive by omission.
    assert base["dry_run"].default is False
    assert base["evicted_by_caller"].default is False

    impl = inspect.signature(ModelManagerBackend.cleanup).parameters
    assert set(base) <= set(impl)


def test_default_backend_cleanup_raises_not_implemented():
    """Concrete, not abstract, matching ``unload``: the sweep's keyword-only
    arguments are exactly the kind of contract change that would strand an
    out-of-tree backend.

    Mutation: restore ``@abstractmethod`` on ``cleanup`` -> ``NoVerbs()`` raises
    ``TypeError: Can't instantiate abstract class`` -> red.
    """
    from src.lib.promotion.deployment_backend.base import DeploymentBackend

    class NoVerbs(DeploymentBackend):
        def pull(self, *a, **k):
            raise NotImplementedError

        def apply(self, *a, **k):
            raise NotImplementedError

        def convergence(self, *a, **k):
            raise NotImplementedError

        def rollback(self, *a, **k):
            raise NotImplementedError

    b = NoVerbs()  # instantiable: cleanup is concrete, not abstract
    with pytest.raises(NotImplementedError):
        b.cleanup("m", "1", min_age_s=1.0, dry_run=True)


# -- cleanup ----------------------------------------------------------------- #
def test_cleanup_sends_body_and_parses_response(backend):
    resp = _make_response(200, {"version": "1.4.0", "status": "removed", "disk_reclaimed": True})
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        result = backend.cleanup("changenet-aoi", "1.4.0")
    assert result == {"version": "1.4.0", "status": "removed", "disk_reclaimed": True}
    args, kwargs = mock_post.call_args
    assert args[0] == f"{BASE_URL}/v1/models/cleanup"
    assert kwargs["json"] == {"model_name": "changenet-aoi", "version": "1.4.0"}


def test_cleanup_surfaces_an_eviction_that_did_not_reclaim_the_directory(backend):
    """The response is a 200 either way, so this field is the only signal.

    The eviction succeeded and the version's directory is still on the shared
    volume. Reporting that as a plain success is how the repository grows by one
    directory per promotion with nothing in the logs to say so.
    """
    resp = _make_response(200, {"version": "1.4.0", "status": "removed", "disk_reclaimed": False})
    with patch(f"{MODULE}.requests.post", return_value=resp):
        result = backend.cleanup("changenet-aoi", "1.4.0")
    assert result["status"] == "removed"
    assert result["disk_reclaimed"] is False


def test_a_dry_run_that_withholds_the_reclaim_does_not_warn(backend, caplog):
    """The sweep ships ``dry_run: True``, so this is its first posture -- and the
    shipped sweep warned on that happy path, once per nominee per tick. An alarm that
    is always wrong gets filtered within a week and is missing when it matters.
    """
    resp = _make_response(200, {"version": "1.4.0", "status": "removed", "disk_reclaimed": False})
    with caplog.at_level(logging.INFO), patch(f"{MODULE}.requests.post", return_value=resp):
        result = backend.cleanup("changenet-aoi", "1.4.0", dry_run=True)
    assert result["disk_reclaimed"] is False
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert any("withheld the reclaim as asked" in r.getMessage() for r in caplog.records)


def test_a_real_cleanup_that_did_not_reclaim_still_warns(backend, caplog):
    """The counterweight: suppressing the dry-run case must not silence the real one."""
    resp = _make_response(200, {"version": "1.4.0", "status": "removed", "disk_reclaimed": False})
    with caplog.at_level(logging.INFO), patch(f"{MODULE}.requests.post", return_value=resp):
        backend.cleanup("changenet-aoi", "1.4.0", dry_run=False)
    assert any(
        r.levelno >= logging.WARNING and "did not reclaim its directory" in r.getMessage()
        for r in caplog.records
    )


def test_cleanup_treats_an_absent_disk_reclaimed_as_not_reclaimed(backend):
    """An older Model Manager omits the field, which is "unknown", not "done".

    Reading it as a reclaim would manufacture the reassuring answer for exactly the
    deployments that cannot report the truthful one.
    """
    resp = _make_response(200, {"version": "1.4.0", "status": "removed"})
    with patch(f"{MODULE}.requests.post", return_value=resp):
        result = backend.cleanup("changenet-aoi", "1.4.0")
    assert result["disk_reclaimed"] is False


def test_cleanup_omits_min_age_s_and_dry_run_by_default(backend):
    """An older Model Manager must see a byte-identical request.

    Mutation: always send both keys -> red.
    """
    resp = _make_response(200, {"version": "1", "status": "removed", "disk_reclaimed": True})
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.cleanup("changenet-aoi", "1")
    assert mock_post.call_args.kwargs["json"] == {"model_name": "changenet-aoi", "version": "1"}


def test_cleanup_forwards_min_age_s_and_dry_run_when_given(backend):
    """Mutation: drop the two kwargs from the body -> red, and in production the
    sweep's "dry run" would delete while its age floor evaporated."""
    resp = _make_response(200, {"version": "1", "status": "removed", "disk_reclaimed": False})
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.cleanup("changenet-aoi", "1", min_age_s=86400.0, dry_run=True)
    assert mock_post.call_args.kwargs["json"] == {
        "model_name": "changenet-aoi",
        "version": "1",
        "min_age_s": 86400.0,
        "dry_run": True,
    }


def test_cleanup_omits_evicted_by_caller_unless_the_caller_asserts_one(backend):
    """The one field here that can WIDEN a delete decision: on a backend that drops a
    version's status row on unload, it stands in for the missing row. A caller holding
    no eviction stamp must send nothing at all, or every caller gets an authority only
    the sweep's stamped nominees have earned.

    Mutation: send the key unconditionally, or default the parameter to True ->
    red on the first assertion.
    """
    resp = _make_response(200, {"version": "1", "status": "removed", "disk_reclaimed": True})
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.cleanup("changenet-aoi", "1")
    assert mock_post.call_args.kwargs["json"] == {"model_name": "changenet-aoi", "version": "1"}

    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        backend.cleanup("changenet-aoi", "1", evicted_by_caller=True)
    assert mock_post.call_args.kwargs["json"] == {
        "model_name": "changenet-aoi",
        "version": "1",
        "evicted_by_caller": True,
    }


# -- error mapping ----------------------------------------------------------- #
def test_error_envelope_maps_to_backend_error(backend):
    resp = _make_response(
        409,
        {
            "error": {
                "code": "checksum_mismatch",
                "message": "digest differs",
                "retryable": False,
                "details": {"expected": "aaa", "actual": "bbb"},
            }
        },
    )
    with patch(f"{MODULE}.requests.post", return_value=resp):
        with pytest.raises(BackendError) as exc_info:
            backend.pull("m", "1", "uri", "onnx", "bbb")
    err = exc_info.value
    assert err.code == "checksum_mismatch"
    assert err.message == "digest differs"
    assert err.retryable is False
    assert err.http_status == 409
    assert err.details == {"expected": "aaa", "actual": "bbb"}


def test_non_retryable_backend_error_is_not_retried(backend):
    resp = _make_response(
        422,
        {"error": {"code": "bad_request", "message": "bad", "retryable": False}},
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        with pytest.raises(BackendError):
            backend.apply("m", "1", PromotionControl.SHADOW)
    assert mock_post.call_count == 1


# -- retry behavior ---------------------------------------------------------- #
def test_retry_on_http_500_then_success(backend):
    err_resp = _make_response(
        500, {"error": {"code": "internal", "message": "boom", "retryable": True}}
    )
    ok_resp = _make_response(
        201,
        {"model_name": "m", "version": "1", "repo_path": "/p/", "status": "staged"},
    )
    with patch(f"{MODULE}.requests.post", side_effect=[err_resp, ok_resp]) as mock_post:
        result = backend.pull("m", "1", "uri", "onnx", "abc")
    assert result.status == "staged"
    assert mock_post.call_count == 2


def test_an_enveloped_500_is_taken_at_its_word_and_not_retried(backend):
    """A 5xx that declares itself non-retryable must end the attempt, not re-drive it.

    This is the one field the Model Manager's error-envelope work actually changes
    on this side of the wire. Before it, an unhandled server-side exception came
    back as a bare ``text/plain`` 500, so ``_error_from_response`` had nothing to
    read and fell back to ``retryable = status >= 500``. A permanently broken
    server -- an unwritable model repository, a misconfigured backend URL -- was
    therefore re-driven three times here and then up to five more by Celery, and
    the promotion burned its whole budget on a fault no retry could fix.

    The Model Manager now states the verdict explicitly, and the only thing that
    makes that worth anything is this client believing it over the status code.
    The 422 and 405 reshapes from the same work are NOT pinned here on purpose:
    both parse to the identical ``code``/``retryable``/``http_status`` they did
    before, so a test over them would pass whatever the server sent and prove
    nothing. This one does not -- neutering the envelope's ``retryable`` back to a
    status-derived guess turns the single POST below into three.
    """
    resp = _make_response(
        500,
        {
            "error": {
                "code": "internal",
                "message": "Unhandled RuntimeError serving POST /v1/models/pull",
                "retryable": False,
                "details": None,
            }
        },
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        with pytest.raises(BackendError) as excinfo:
            backend.pull("m", "1", "uri", "onnx", "abc")

    assert excinfo.value.retryable is False, "the envelope's verdict must win over the 5xx status"
    assert excinfo.value.http_status == 500
    assert excinfo.value.code == "internal"
    # The point of the whole exercise: one attempt, not three.
    assert mock_post.call_count == 1


def test_retry_on_500_without_envelope_is_retryable(backend):
    err_resp = _make_response(503, None)
    ok_resp = _make_response(200, {"version": "1", "status": "removed"})
    with patch(f"{MODULE}.requests.post", side_effect=[err_resp, ok_resp]) as mock_post:
        result = backend.cleanup("m", "1")
    assert result["status"] == "removed"
    assert mock_post.call_count == 2


def test_retry_on_transient_connection_error_then_success(backend):
    ok_resp = _make_response(
        202,
        {
            "swap_id": "s1",
            "to_version": "2",
            "control": "shadow",
            "started_at": "2026-05-27T11:20:02Z",
            "applied": {
                "model_name": "m",
                "to_version": "2",
                "control": "shadow",
                "from_version": None,
                "canary_weight": None,
                "key": "k",
            },
        },
    )
    with patch(
        f"{MODULE}.requests.post",
        side_effect=[requests.exceptions.ConnectionError("Connection aborted"), ok_resp],
    ) as mock_post:
        result = backend.apply("m", "2", PromotionControl.SHADOW)
    assert result.swap_id == "s1"
    assert mock_post.call_count == 2


def test_retry_exhausted_raises_last_error(backend):
    err_resp = _make_response(
        500, {"error": {"code": "internal", "message": "boom", "retryable": True}}
    )
    with patch(f"{MODULE}.requests.post", return_value=err_resp) as mock_post:
        with pytest.raises(BackendError) as exc_info:
            backend.pull("m", "1", "uri", "onnx", "abc")
    assert exc_info.value.code == "internal"
    assert mock_post.call_count == 3


class TestServingStateReadIsDefensive:
    """`serving_state` promises None, never an exception.

    It is called on the fast path to minting a version. A serving stack that
    answers with a null or wrong-typed field must degrade to "unknown" — which
    the minting caller already handles — rather than propagate a TypeError out
    of a method documented not to raise.
    """

    @staticmethod
    def _backend():
        return ModelManagerBackend({"base_url": "http://mm:8090", "timeout": 1})

    @staticmethod
    def _reads(payload):
        return patch("requests.get", return_value=_make_response(200, payload))

    def test_well_formed_payload_is_parsed(self):
        payload = {
            "model_name": "changenet_classify",
            "default_version": "2",
            "routing": [{"version": "2", "role": "default", "weight": 1.0}],
            "serving_versions": ["2"],
            "known_versions": ["1", "2"],
            "ready": True,
            "routing_available": True,
            "loaded_available": True,
        }
        with self._reads(payload):
            state = self._backend().serving_state("changenet_classify")
        assert state.known_versions == ["1", "2"]
        assert state.default_version == "2"
        assert [e.version for e in state.routing] == ["2"]
        assert state.routing_available and state.known_available

    @pytest.mark.parametrize("field", ["routing", "serving_versions", "known_versions"])
    @pytest.mark.parametrize("value", [None, 5, "nope", {"a": 1}])
    def test_a_malformed_list_field_is_rejected_not_partially_trusted(self, field, value):
        """A present-but-unusable field makes the WHOLE answer untrustworthy.

        JSON null reaches Python as None and `.get(k, [])` does not default when
        the key is present, so iterating it used to raise out of a method that
        promises not to. Returning the parts that happened to parse is just as
        wrong: a body with `loaded_available: true` and an unusable
        `known_versions` would read as "the stack holds nothing", and the caller
        would mint from the day-0 floor straight onto a live version.
        """
        payload = {"known_versions": ["1"], "loaded_available": True, field: value}
        with self._reads(payload):
            assert self._backend().serving_state("m") is None

    def test_a_wrong_typed_field_is_not_read_element_wise(self):
        """A string is iterable, so it silently produced ["1", "2"] from "12"."""
        with self._reads({"known_versions": "12", "loaded_available": True}):
            assert self._backend().serving_state("m") is None

    def test_an_absent_field_is_a_legitimate_empty(self):
        """Absent is not malformed — an older or partial payload just omits it."""
        with self._reads({"loaded_available": True, "routing_available": True}):
            state = self._backend().serving_state("m")
        assert state is not None
        assert state.known_versions == []

    @pytest.mark.parametrize("payload", [{}, {"ready_versions": None}, {"ready_versions": "12"}])
    def test_an_unreportable_ready_set_is_none_not_empty(self, payload):
        """``ready_versions`` is the only field where empty and unknown differ in
        DIRECTION: the rollback precheck refuses on an empty one, so an older Model
        Manager that omits it would have every rollback refused.

        Mutation: parse it like the lists above (absent -> ``[]``) -> red.
        """
        with self._reads({"loaded_available": True, **payload}):
            state = self._backend().serving_state("m")
        assert state is not None
        assert state.ready_versions is None

    def test_a_reported_ready_set_is_parsed(self):
        """The converse: a real empty list stays empty, so a stack that truly holds
        nothing servable is not silently read as unknown.

        Mutation: collapse a falsy list to None -> red on the second half.
        """
        with self._reads({"ready_versions": [2, "3"], "loaded_available": True}):
            assert self._backend().serving_state("m").ready_versions == ["2", "3"]
        with self._reads({"ready_versions": [], "loaded_available": True}):
            assert self._backend().serving_state("m").ready_versions == []

    def test_missing_fields_default_to_unknown_not_available(self):
        with self._reads({}):
            state = self._backend().serving_state("m")
        assert state is not None
        assert state.routing_available is False
        assert state.known_available is False

    def test_non_object_body_is_unknown(self):
        with self._reads(["not", "an", "object"]):
            assert self._backend().serving_state("m") is None


# -- convergence -- the stateless poll --------------------------------------- #
def test_convergence_posts_the_expectation_verbatim(backend):
    """The token goes back exactly as it came, unknown fields and all.

    Re-serialising only the fields DFW knows about would 422 as soon as the
    Model Manager gains a field. The unknown key here stands in for that.
    """
    token = {
        "model_name": "changenet-aoi",
        "to_version": "1.4.2",
        "control": "canary",
        "from_version": "1.4.1",
        "canary_weight": 0.25,
        "key": "idem-7",
        "a_field_this_dfw_has_never_heard_of": {"nested": True},
    }
    resp = _make_response(200, {"converged": True, "reason": None})
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        report = backend.convergence(token)

    args, kwargs = mock_post.call_args
    assert args[0] == f"{BASE_URL}/v1/models/convergence"
    assert kwargs["json"] == {"expectation": token}, "the expectation was not echoed verbatim"
    assert kwargs["timeout"] == 5.0, "a poll must use the short budget, not the pull budget"
    assert report.converged is True
    assert report.reason is None


def test_convergence_parses_an_unknown_reason_code_as_a_plain_string(backend):
    """A reason code DFW has never seen must not break the poll.

    The Model Manager owns that vocabulary and may add to it, so ``code`` stays
    a ``str`` and only ``terminal`` is branched on.
    """
    resp = _make_response(
        200,
        {
            "converged": False,
            "reason": {
                "code": "a_reason_invented_after_this_test_was_written",
                "message": "not there yet",
                "terminal": False,
                "detail": {"expected": "0.25", "observed": "0.10"},
            },
        },
    )
    with patch(f"{MODULE}.requests.post", return_value=resp):
        report = backend.convergence({"model_name": "m", "key": "k"})

    assert report.converged is False
    assert report.reason.code == "a_reason_invented_after_this_test_was_written"
    assert report.reason.terminal is False
    assert report.reason.detail == {"expected": "0.25", "observed": "0.10"}


def test_convergence_defaults_an_unreadable_body_to_not_yet(backend):
    """An unreadable body keeps polling instead of deciding.

    It is no evidence either way, so "not converged, not terminal" spends a tick.
    """
    with patch(f"{MODULE}.requests.post", return_value=_make_response(200, {})):
        report = backend.convergence({"model_name": "m", "key": "k"})

    assert report.converged is False
    assert report.reason is None


def test_convergence_404_and_422_stay_distinguishable(backend):
    """The poll loop branches on these differently, so keep them distinct.

    A 404 is a control plane that has not rolled yet, so spend a tick. A 422 is
    an outright rejection of the expectation, and waiting cannot fix that.
    """
    not_found = _make_response(404, {"error": {"code": "not_found", "retryable": False}})
    with patch(f"{MODULE}.requests.post", return_value=not_found):
        with pytest.raises(BackendError) as excinfo:
            backend.convergence({"model_name": "m", "key": "k"})
    assert excinfo.value.http_status == 404
    assert excinfo.value.retryable is False

    unprocessable = _make_response(422, {"error": {"code": "bad_request", "retryable": False}})
    with patch(f"{MODULE}.requests.post", return_value=unprocessable):
        with pytest.raises(BackendError) as excinfo:
            backend.convergence({"model_name": "m", "key": "k"})
    assert excinfo.value.http_status == 422


def test_apply_without_an_applied_object_is_a_non_retryable_backend_error(backend):
    """An older Model Manager fails the gate legibly instead of crashing.

    A bare KeyError would escape the controller's handler and burn the Celery
    retries on a response whose shape will never change. A non-retryable
    BackendError fails at the deploy gate, before anything is serving.
    """
    resp = _make_response(
        202,
        {
            "swap_id": "s1",
            "to_version": "2",
            "control": "shadow",
            "started_at": "2026-05-27T11:20:02Z",
        },
    )
    with patch(f"{MODULE}.requests.post", return_value=resp) as mock_post:
        with pytest.raises(BackendError) as excinfo:
            backend.apply("m", "2", PromotionControl.SHADOW)

    assert excinfo.value.code == "bad_request"
    assert excinfo.value.retryable is False
    assert "applied" in excinfo.value.message
    assert mock_post.call_count == 1, "a shape error must not be retried"


def test_apply_threads_the_token_and_the_noop_flag_through(backend):
    """``applied`` is carried as the raw dict; ``noop`` is surfaced, not inferred."""
    token = {"model_name": "m", "to_version": "2", "control": "shadow", "key": "k"}
    resp = _make_response(
        202,
        {
            "swap_id": "s1",
            "to_version": "2",
            "control": "shadow",
            "started_at": "2026-05-27T11:20:02Z",
            "applied": token,
            "noop": True,
        },
    )
    with patch(f"{MODULE}.requests.post", return_value=resp):
        result = backend.apply("m", "2", PromotionControl.SHADOW)

    assert result.applied == token
    assert result.noop is True
    assert result.swap_id == "s1"


def test_apply_works_when_the_response_carries_no_swap_id(backend):
    """`swap_id` is optional on the way in, so DFW must not depend on it.

    The other fixtures here still send one because the Model Manager still mints
    it as a log correlation token. This pins that an apply response without it
    parses and still carries everything the promotion needs.
    """
    token = {
        "model_name": "changenet-aoi",
        "to_version": "1.4.2",
        "control": "full_switch",
        "from_version": "1.4.1",
        "canary_weight": None,
        "key": "idem-9",
    }
    resp = _make_response(
        202,
        {
            "to_version": "1.4.2",
            "control": "full_switch",
            "started_at": "2026-05-27T11:20:02Z",
            "from_version": "1.4.1",
            "applied": token,
            "noop": False,
        },
    )
    with patch(f"{MODULE}.requests.post", return_value=resp):
        result = backend.apply("changenet-aoi", "1.4.2", PromotionControl.FULL_SWITCH)

    assert result.swap_id is None
    assert result.applied == token, "the poll token is what the promotion needs, and it is intact"
    assert result.to_version == "1.4.2"
    assert result.from_version == "1.4.1"
    assert result.control is PromotionControl.FULL_SWITCH
    assert result.noop is False


# -- fake fidelity -- the fakes must not be friendlier than production ------- #
def _real_mm_schemas():
    """Load the Model Manager's OWN schema module, by path.

    model-manager/ is a separate package that is not importable from here, and
    its ``src`` would collide with DFW's. Loading it under a unique module name
    is what lets this test validate against the REAL contract rather than a
    DFW-side restatement of it -- which would defeat the point, since the whole
    finding was that our restatement had drifted.
    """
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "model-manager"
    if not (root / "src" / "api" / "schemas.py").exists():  # pragma: no cover
        pytest.skip("model-manager sources not present")
    sys.path.insert(0, str(root))
    try:
        spec = importlib.util.spec_from_file_location(
            "_mm_schemas_under_test", root / "src" / "api" / "schemas.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(root))


def test_the_e2e_fake_emits_a_contract_valid_convergence_report():
    """The anti-drift pin the fidelity fix shipped without.

    The finding was that the fakes had drifted into a friendlier service than
    production -- omitting ``observed`` and ``at``, which the real
    ``ConvergenceReport`` requires. Fixing the content without pinning it just
    resets the clock: a full revert of that fix left all 238 promotion tests
    green, because nothing validated a fake body against the real model.

    This validates STRICTLY, so a missing required field fails rather than
    being tolerated, and it loads the real schema from model-manager/ rather
    than restating it here.
    """
    from tests.unit.promotion._e2e_fakes import FakeModelManagerHTTP

    schemas = _real_mm_schemas()
    fake = FakeModelManagerHTTP(default_version="1")
    fake.ready_versions = ["1", "2"]

    for expectation in (
        {"model_name": "m", "to_version": "1", "control": "full_switch", "key": "k"},
        {"model_name": "m", "to_version": "2", "control": "shadow", "key": "k"},
    ):
        body = fake._converged(expectation)
        # Raises ValidationError if a required field is missing or mistyped.
        report = schemas.ConvergenceReport.model_validate(body)
        assert report.observed is not None, "observed is required on the real contract"
        assert report.at is not None, "at is required on the real contract"


def test_the_e2e_fake_emits_a_contract_valid_serving_state():
    """Same, for the projection convergence's ``observed`` shares.

    The two are deliberately one function in the fake so they cannot drift
    apart; this asserts the shape they share is the real one -- including
    ``ready_versions``, which the fake omitted before the fix.
    """
    from tests.unit.promotion._e2e_fakes import FakeModelManagerHTTP

    schemas = _real_mm_schemas()
    fake = FakeModelManagerHTTP(default_version="1")

    state = schemas.ServingStateResponse.model_validate(fake._state_body("m"))
    assert state.ready_versions is not None
    # `loaded_available`, not `known_available`: the latter only worked through a
    # legacy alias and is a shape production never sends.
    assert "loaded_available" in fake._state_body("m")
    assert "known_available" not in fake._state_body("m")
