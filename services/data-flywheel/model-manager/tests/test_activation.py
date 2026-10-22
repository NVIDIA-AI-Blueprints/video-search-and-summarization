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

"""Unit tests for ExplicitActivation staging + PollActivation placeholder."""

import contextlib
import hashlib
import json
import logging
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import requests
from pydantic import ValidationError

from src.adapters import activation as activation_mod
from src.adapters.activation import (
    METADATA_FILENAME,
    SPEC_FILENAME,
    _artifact_filename,
    _derived_idempotency_key,
)
from src.adapters.aoi.activation import ExplicitActivation, PollActivation
from src.adapters.aoi.inference import BuildHandle, BuildStatus
from src.config import Settings, settings
from src.core.errors import ModelManagerError

# Completed inline, so a staging test that ignores the build never polls.
_LOADED_INLINE = (BuildHandle(phase="succeeded"),)


class FakeInference:
    """Stand-in for ``AOIInferenceClient`` scripted with load/build outcomes.

    ``accepts`` is what successive load POSTs return and ``phases`` is what
    successive build-status reads return, so a test can play out a whole async
    load — accepted, building, succeeded — without patching HTTP. In both lists
    the final element repeats.
    """

    def __init__(
        self,
        *,
        accepts=_LOADED_INLINE,
        phases=("succeeded",),
        error: str | None = None,
        models=None,
    ):
        self._accepts = list(accepts)
        self._phases = list(phases)
        self._error = error
        self._models = models
        self.load_calls: list[tuple[str, str, str | None]] = []
        self.status_calls: list[dict] = []
        self.unload_calls: list[tuple[str, str]] = []
        self.list_models_calls = 0

    def load_async(self, name, version, *, idempotency_key=None):
        self.load_calls.append((name, version, idempotency_key))
        return self._accepts.pop(0) if len(self._accepts) > 1 else self._accepts[0]

    def build_status(self, *, build_id=None, name=None, version=None):
        self.status_calls.append({"build_id": build_id, "name": name, "version": version})
        phase = self._phases.pop(0) if len(self._phases) > 1 else self._phases[0]
        return BuildStatus(phase=phase, build_id=build_id, error=self._error)

    def list_models(self):
        self.list_models_calls += 1
        return self._models if self._models is not None else []

    def unload(self, name, version, *, idempotency_key=None):
        self.unload_calls.append((name, version))


def _write_artifact(tmp_path, data: bytes):
    src = tmp_path / "source" / "model.onnx"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)
    return src


def _loaded(name="m", version="1", sha=""):
    """Return the single expected load call for a staging test."""
    return (name, version, _derived_idempotency_key(name, version, sha))


class _FakeStreamingResponse:
    """Stand-in for a ``requests.Response`` opened with ``stream=True``.

    Serves the body only through ``iter_content`` so tests can prove the
    download path streams to disk; touching ``.content`` fails the assertion
    that the whole body is never buffered in memory.
    """

    def __init__(self, status_code: int, chunks: list[bytes]):
        self.status_code = status_code
        self._chunks = chunks
        self.content_accessed = False
        self.closed = False

    @property
    def content(self):  # pragma: no cover - accessing this is the failure mode
        self.content_accessed = True
        raise AssertionError("streaming download must not read response.content")

    def iter_content(self, chunk_size=None):
        yield from self._chunks

    def close(self):
        self.closed = True


def test_stage_and_load_file_scheme_verifies_and_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"changenet-weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    inference = FakeInference()
    activation = ExplicitActivation(inference=inference)
    repo_path = activation.stage_and_load("m", "1", f"file://{src}", sha)

    assert repo_path == str(tmp_path / "repo" / "m" / "1")
    assert inference.load_calls == [_loaded(sha=sha)]
    assert (tmp_path / "repo" / "m" / "1" / "model.onnx").read_bytes() == data


def test_stage_and_load_stages_pth_source_as_canonical_model_pth(tmp_path, monkeypatch):
    # The inference resolves a canonical model.<ext>, so a .pth source stages as
    # model.pth, not under the source basename.
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"changenet-weights"
    src = tmp_path / "source" / "weights_epoch12.pth"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()

    activation = ExplicitActivation(inference=FakeInference())
    activation.stage_and_load("m", "1", f"file://{src}", sha)

    assert (tmp_path / "repo" / "m" / "1" / "model.pth").read_bytes() == data
    assert not (tmp_path / "repo" / "m" / "1" / "weights_epoch12.pth").exists()


def test_stage_and_load_stages_spec_beside_weights(tmp_path, monkeypatch):
    # spec.yaml lands beside the weights BEFORE the load, so the serving stack
    # can rebuild or convert.
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"changenet-weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()
    spec_src = tmp_path / "source" / "baseline_spec.yaml"
    spec_src.write_text("model:\n  name: changenet\n")

    inference = FakeInference()
    activation = ExplicitActivation(inference=inference)
    activation.stage_and_load("m", "1", f"file://{src}", sha, spec_uri=f"file://{spec_src}")

    staged_spec = tmp_path / "repo" / "m" / "1" / "spec.yaml"
    assert staged_spec.read_text() == "model:\n  name: changenet\n"
    assert (tmp_path / "repo" / "m" / "1" / "model.onnx").read_bytes() == data
    assert inference.load_calls == [_loaded(sha=sha)]


def test_stage_and_load_without_spec_uri_stages_no_spec(tmp_path, monkeypatch):
    # No spec published: weights only, as before.
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    activation = ExplicitActivation(inference=FakeInference())
    activation.stage_and_load("m", "1", f"file://{src}", sha)

    assert not (tmp_path / "repo" / "m" / "1" / "spec.yaml").exists()


def test_stage_and_load_always_restages_a_changed_spec(tmp_path, monkeypatch):
    # Newest content wins: an existing spec.yaml never survives a changed
    # publish, and the atomic rename leaves no temp behind.
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()
    spec_src = tmp_path / "source" / "spec.yaml"

    activation = ExplicitActivation(inference=FakeInference())

    spec_src.write_text("v: 1\n")
    activation.stage_and_load("m", "1", f"file://{src}", sha, spec_uri=f"file://{spec_src}")
    spec_src.write_text("v: 2\n")
    activation.stage_and_load("m", "1", f"file://{src}", sha, spec_uri=f"file://{spec_src}")

    version_dir = tmp_path / "repo" / "m" / "1"
    assert (version_dir / "spec.yaml").read_text() == "v: 2\n"
    assert not (version_dir / "spec.yaml.tmp").exists()


def test_stage_and_load_writes_metadata_sidecar_when_threshold_set(tmp_path, monkeypatch):
    # metadata.json lands beside the weights BEFORE the load, in the extensible
    # sidecar schema and with no leftover .tmp.
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    inference = FakeInference()
    activation = ExplicitActivation(inference=inference)
    activation.stage_and_load("m", "1", f"file://{src}", sha, threshold=0.42)

    version_dir = tmp_path / "repo" / "m" / "1"
    sidecar = version_dir / METADATA_FILENAME
    assert json.loads(sidecar.read_text()) == {
        "schema_version": 1,
        "model_name": "m",
        "version": "1",
        "threshold": 0.42,
    }
    assert not sidecar.with_name(METADATA_FILENAME + ".tmp").exists()
    assert inference.load_calls == [_loaded(sha=sha)]


def test_stage_and_load_writes_metadata_sidecar_for_zero_threshold(tmp_path, monkeypatch):
    # 0.0 is falsy but valid: it must read as present, not "unset".
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    activation = ExplicitActivation(inference=FakeInference())
    activation.stage_and_load("m", "1", f"file://{src}", sha, threshold=0.0)

    version_dir = tmp_path / "repo" / "m" / "1"
    assert json.loads((version_dir / METADATA_FILENAME).read_text())["threshold"] == 0.0


def test_stage_and_load_without_threshold_writes_no_sidecar(tmp_path, monkeypatch):
    # No threshold: no sidecar, as with spec_uri.
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    activation = ExplicitActivation(inference=FakeInference())
    activation.stage_and_load("m", "1", f"file://{src}", sha)

    assert not (tmp_path / "repo" / "m" / "1" / METADATA_FILENAME).exists()


def test_stage_and_load_always_restages_a_changed_threshold(tmp_path, monkeypatch):
    # Newest threshold wins: an existing metadata.json never masks a changed
    # value, and the atomic rename leaves no temp behind.
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    activation = ExplicitActivation(inference=FakeInference())
    activation.stage_and_load("m", "1", f"file://{src}", sha, threshold=0.1)
    activation.stage_and_load("m", "1", f"file://{src}", sha, threshold=0.9)

    version_dir = tmp_path / "repo" / "m" / "1"
    assert json.loads((version_dir / METADATA_FILENAME).read_text())["threshold"] == 0.9
    assert [p.name for p in version_dir.iterdir() if p.name.endswith(".tmp")] == []


def test_stage_and_load_checksum_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    src = _write_artifact(tmp_path, b"real-bytes")

    inference = FakeInference()
    activation = ExplicitActivation(inference=inference)
    with pytest.raises(ModelManagerError) as excinfo:
        activation.stage_and_load("m", "1", f"file://{src}", "deadbeef")

    assert excinfo.value.code == "checksum_mismatch"
    assert excinfo.value.http_status == 409
    assert inference.load_calls == []


def test_stage_and_load_missing_source(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    activation = ExplicitActivation(inference=FakeInference())
    with pytest.raises(ModelManagerError) as excinfo:
        activation.stage_and_load("m", "1", f"file://{tmp_path}/missing.onnx", "abc")
    assert excinfo.value.code == "not_found"


def test_stage_and_load_unsupported_scheme(tmp_path, monkeypatch):
    # file/http(s) only; s3:// and mlflow:// are unsupported (501). DFW presigns
    # object-store locators, so MM never sees s3:// on the happy path.
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    activation = ExplicitActivation(inference=FakeInference())
    for uri in ("mlflow://model/1", "s3://bucket/models/changenet/model.onnx"):
        with pytest.raises(ModelManagerError) as excinfo:
            activation.stage_and_load("m", "1", uri, "abc")
        assert excinfo.value.code == "internal"
        assert excinfo.value.http_status == 501


def test_stage_and_load_http_streams_chunks_to_disk(tmp_path, monkeypatch):
    # Streamed via iter_content, never buffered through .content: multi-GB files.
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    chunks = [b"aaaa", b"bbbb"]
    data = b"".join(chunks)
    sha = hashlib.sha256(data).hexdigest()
    fake = _FakeStreamingResponse(200, chunks)

    inference = FakeInference()
    activation = ExplicitActivation(inference=inference)
    with patch("src.clients.http.requests.request", return_value=fake) as req:
        activation.stage_and_load("m", "1", "https://weights.example.com/changenet/model.onnx", sha)

    assert req.call_args.kwargs["stream"] is True
    assert fake.content_accessed is False
    assert (tmp_path / "repo" / "m" / "1" / "model.onnx").read_bytes() == data
    assert inference.load_calls == [_loaded(sha=sha)]


def test_stage_and_load_https_presigned_url_stages_canonical_name(tmp_path, monkeypatch):
    # A presigned HTTPS URL takes the same http(s) branch, and its SigV4 query is
    # ignored when deriving the filename and extension.
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    chunks = [b"presigned-", b"weights"]
    data = b"".join(chunks)
    sha = hashlib.sha256(data).hexdigest()
    presigned_uri = (
        "https://bucket.s3.amazonaws.com/models/changenet/weights.onnx"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Credential=AKIA%2F20250722%2Fus-east-1%2Fs3%2Faws4_request"
        "&X-Amz-Date=20250722T000000Z&X-Amz-Expires=900"
        "&X-Amz-SignedHeaders=host&X-Amz-Signature=deadbeef"
    )
    fake = _FakeStreamingResponse(200, chunks)

    inference = FakeInference()
    activation = ExplicitActivation(inference=inference)
    with patch("src.clients.http.requests.request", return_value=fake):
        activation.stage_and_load("m", "1", presigned_uri, sha)

    version_dir = tmp_path / "repo" / "m" / "1"
    assert (version_dir / "model.onnx").read_bytes() == data

    assert not any("?" in p.name for p in version_dir.iterdir())
    assert inference.load_calls == [_loaded(sha=sha)]


def test_stage_and_load_http_non_2xx_closes_and_maps_to_registry_unreachable(tmp_path, monkeypatch):
    # A non-2xx closes the stream and maps to a retryable registry_unreachable
    # (502) before any body is read.
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    fake = _FakeStreamingResponse(404, [b"nope"])

    activation = ExplicitActivation(inference=FakeInference())
    with patch("src.clients.http.requests.request", return_value=fake):
        with pytest.raises(ModelManagerError) as excinfo:
            activation.stage_and_load("m", "1", "https://weights.example.com/model.onnx", "abc")

    assert excinfo.value.code == "registry_unreachable"
    assert excinfo.value.http_status == 502
    assert excinfo.value.retryable is True
    assert fake.closed is True


def test_poll_activation_not_implemented():
    poll = PollActivation()
    with pytest.raises(NotImplementedError):
        poll.stage_and_load("m", "1", "file:///x", "abc")
    with pytest.raises(NotImplementedError):
        poll.unload("m", "1")
    with pytest.raises(NotImplementedError):
        poll.delete("m", "1")


# ------------------------------------------------------- async load + build poll
@pytest.fixture
def staged(tmp_path, monkeypatch):
    """Stage a valid artifact and run the load against a scripted inference.

    Collapses the setup every build-orchestration test needs — a repo root, an
    artifact on disk, its digest — so those tests express only the build
    behaviour they are about.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    def _run(inference, *, name="m", version="1", idempotency_key=None):
        activation = ExplicitActivation(inference=inference)
        return activation.stage_and_load(
            name, version, f"file://{src}", sha, idempotency_key=idempotency_key
        )

    _run.sha = sha
    _run.repo_root = tmp_path / "repo"
    return _run


def _accepted(build_id="b-1"):
    return BuildHandle(phase="building", build_id=build_id)


def test_pull_waits_for_an_accepted_build_to_reach_succeeded(staged):
    """A 202 only starts the work; pull returns when the build is actually done.

    This is the whole point of doing the wait here: the version has to be READY
    before anything tries to route to it, because the routing PUT refuses a target
    that is not.
    """
    inference = FakeInference(accepts=(_accepted(),), phases=("building", "building", "succeeded"))
    staged(inference)

    assert [c["build_id"] for c in inference.status_calls] == ["b-1", "b-1", "b-1"]


def test_pull_raises_when_the_build_fails(staged):
    inference = FakeInference(
        accepts=(_accepted(),), phases=("failed",), error="trtexec returned 1"
    )
    with pytest.raises(ModelManagerError) as excinfo:
        staged(inference)

    assert excinfo.value.code == "load_failed"
    assert excinfo.value.retryable is True
    assert "trtexec returned 1" in excinfo.value.message


def test_a_permission_failure_names_the_mount_fix_and_is_not_retryable(staged):
    """The conversion writes its ONNX/plan output into the directory MM created.

    When that directory is not writable by the serving process the build fails
    with a PermissionError, and no number of retries will change a mount — so the
    error says what to fix instead of inviting the caller to burn its budget.
    """
    inference = FakeInference(
        accepts=(_accepted(),),
        phases=("failed",),
        error="PermissionError: [Errno 13] Permission denied: '/models/m/1/model.onnx'",
    )
    with pytest.raises(ModelManagerError) as excinfo:
        staged(inference)

    assert excinfo.value.retryable is False
    assert "fsGroup" in excinfo.value.message


def test_an_idle_build_is_confirmed_against_the_repository_index(staged):
    """``idle`` is a 200 and not a verdict: the build may have finished unobserved.

    Treating it as a failure would fail a promotion whose model is sitting there
    READY, so the real outcome is read from ``/v1/models``.
    """
    inference = FakeInference(
        accepts=(_accepted(),),
        phases=("idle",),
        models=[{"name": "m", "version": "1", "state": "READY"}],
    )
    staged(inference)

    assert inference.list_models_calls == 1
    # Exactly one load POST: an idle-but-READY target needs no rebuild.
    assert len(inference.load_calls) == 1


def test_a_row_with_no_state_does_not_confirm_a_build(staged):
    """An absent ``state`` is not evidence, and this is the confirming read.

    Reading it as READY reports a successful pull for a version that may not be
    serving, and the promotion then discovers the truth at the routing PUT, which
    refuses a target that is not READY. The serving backend requires the field to
    be present and READY everywhere it judges the same question, so this matches
    it: the row confirms nothing, the load is re-driven on the same idempotency
    key, and a genuinely broken load fails loudly instead of quietly.
    """
    inference = FakeInference(
        accepts=(_accepted(), BuildHandle(phase="succeeded")),
        phases=("idle",),
        models=[{"name": "m", "version": "1"}],
    )
    staged(inference)

    assert len(inference.load_calls) == 2


def test_a_lowercase_ready_state_still_confirms_a_build(staged):
    """Case is normalized rather than compared verbatim.

    The strict check must not turn into an accidentally stricter one: the backend
    upper-cases the field before comparing it, so a payload spelling it ``ready``
    describes a version that is genuinely serving.
    """
    inference = FakeInference(
        accepts=(_accepted(),),
        phases=("idle",),
        models=[{"name": "m", "version": "1", "state": "ready"}],
    )
    staged(inference)

    assert len(inference.load_calls) == 1


def test_a_lost_build_id_re_attaches_by_model_and_version(staged):
    """A build_id that stops resolving is not a failed load.

    The ``{model_name, version}`` lookup is what a Model Manager restart re-attaches
    through, so the poll falls back to it rather than re-POSTing.
    """
    inference = FakeInference(accepts=(_accepted(),), phases=("unknown", "succeeded"))
    staged(inference)

    assert inference.status_calls[0]["build_id"] == "b-1"
    assert inference.status_calls[1] == {"build_id": None, "name": "m", "version": "1"}
    assert len(inference.load_calls) == 1


def test_an_ambiguous_timeout_re_attaches_instead_of_re_posting(staged):
    """A load POST whose response was lost is resolved by asking what is running.

    The POST may have been accepted, so the recovery is a cheap status read by
    target — not another POST that could start a second engine build.
    """
    inference = FakeInference(accepts=(BuildHandle(phase="unknown"),), phases=("succeeded",))
    staged(inference)

    assert inference.status_calls == [{"build_id": None, "name": "m", "version": "1"}]
    assert len(inference.load_calls) == 1


def test_nothing_in_flight_and_not_ready_re_drives_the_load_with_the_same_key(staged):
    """The one case that legitimately rebuilds: the previous attempt failed.

    Its idempotency reservation has been released on purpose, so re-POSTing the
    same key starts a fresh build. Against a live build or a cached success the
    same call re-attaches instead.
    """
    inference = FakeInference(
        accepts=(_accepted(), BuildHandle(phase="succeeded")),
        phases=("idle",),
        models=[],
    )
    staged(inference, idempotency_key="promo-7:pull")

    assert [key for _, _, key in inference.load_calls] == ["promo-7:pull", "promo-7:pull"]


def test_a_persistently_idle_backend_fails_rather_than_looping(staged):
    """The re-POST is allowed once. A backend that keeps answering idle is broken,
    and rebuilding on every pass would be a build loop, not a recovery."""
    inference = FakeInference(
        accepts=(_accepted(), BuildHandle(phase="building", build_id=None)),
        phases=("idle",),
        models=[],
    )
    with pytest.raises(ModelManagerError) as excinfo:
        staged(inference)

    assert excinfo.value.code == "load_failed"
    assert excinfo.value.retryable is True
    assert len(inference.load_calls) == 2


def test_a_build_that_outlasts_the_budget_is_a_readiness_timeout(staged, monkeypatch):
    monkeypatch.setattr(settings, "build_poll_timeout", 0)
    inference = FakeInference(accepts=(_accepted(),), phases=("building",))
    with pytest.raises(ModelManagerError) as excinfo:
        staged(inference)

    assert excinfo.value.code == "readiness_timeout"
    assert excinfo.value.retryable is True


def test_one_pull_never_spends_more_than_one_build_budget(staged, monkeypatch):
    """A synchronous load POST that burns the budget must not then earn a new one.

    In synchronous mode the POST blocks for the whole conversion against
    ``build_poll_timeout``. If it times out, the poll that follows used to start
    its own fresh ``build_poll_timeout``, so a single pull could run for two full
    budgets and blow through the caller's 900s timeout. The budget is one deadline
    spanning both.
    """
    monkeypatch.setattr(settings, "load_async", False)
    monkeypatch.setattr(settings, "build_poll_timeout", 600.0)

    clock = {"now": 1000.0}
    monkeypatch.setattr("src.adapters.aoi.activation.monotonic", lambda: clock["now"])

    class SlowSyncInference(FakeInference):
        """A sync load POST that consumes the entire budget, then times out."""

        def load_async(self, name, version, *, idempotency_key=None):
            clock["now"] += settings.build_poll_timeout
            return super().load_async(name, version, idempotency_key=idempotency_key)

    inference = SlowSyncInference(
        accepts=(BuildHandle(phase="unknown"),), phases=("building",), models=[]
    )
    with pytest.raises(ModelManagerError) as excinfo:
        staged(inference)

    assert excinfo.value.code == "readiness_timeout"
    elapsed = clock["now"] - 1000.0
    assert elapsed == pytest.approx(600.0), f"pull spent {elapsed}s against a 600s budget"
    assert len(inference.load_calls) == 1, "the load was re-driven after the budget was gone"


def test_an_exhausted_budget_stops_before_re_driving_the_load(staged, monkeypatch):
    """The re-POST is the expensive branch, so it is gated on the deadline too.

    Reaching it with no budget left is how the doubling got in: in synchronous
    mode that second POST blocks for another whole conversion.
    """
    monkeypatch.setattr(settings, "build_poll_timeout", 600.0)
    clock = {"now": 0.0}
    monkeypatch.setattr("src.adapters.aoi.activation.monotonic", lambda: clock["now"])

    class ExpiringInference(FakeInference):
        """Reports idle, and has already used the budget by the time it answers."""

        def build_status(self, *, build_id=None, name=None, version=None):
            clock["now"] += settings.build_poll_timeout
            return super().build_status(build_id=build_id, name=name, version=version)

    inference = ExpiringInference(
        accepts=(BuildHandle(phase="building", build_id=None),), phases=("idle",), models=[]
    )
    with pytest.raises(ModelManagerError) as excinfo:
        staged(inference)

    assert excinfo.value.code == "readiness_timeout"
    assert len(inference.load_calls) == 1


def test_a_build_that_finishes_late_is_still_honoured(staged, monkeypatch):
    """A terminal phase wins over the deadline.

    Discarding a build that has already succeeded because the clock just passed
    the budget would fail a promotion whose model is loaded and serving.
    """
    monkeypatch.setattr(settings, "build_poll_timeout", 600.0)
    clock = {"now": 0.0}
    monkeypatch.setattr("src.adapters.aoi.activation.monotonic", lambda: clock["now"])

    class LateInference(FakeInference):
        """Succeeds, but only reports it after the budget has elapsed."""

        def build_status(self, *, build_id=None, name=None, version=None):
            clock["now"] += settings.build_poll_timeout * 2
            return super().build_status(build_id=build_id, name=name, version=version)

    staged(LateInference(accepts=(_accepted(),), phases=("succeeded",)))


def test_a_malformed_models_row_does_not_break_the_readiness_confirmation(staged):
    """Only the response envelope is validated upstream, so rows can be junk.

    Raising here would lose a build that is progressing normally, on the strength
    of an unrelated bad entry.
    """
    inference = FakeInference(
        accepts=(_accepted(),),
        phases=("idle",),
        models=["junk", None, 42, {"name": "m", "version": "1", "state": "READY"}],
    )
    staged(inference)


def test_a_caller_supplied_idempotency_key_is_used_verbatim(staged):
    """DFW's key is stable across its own retries of the same promotion, which is
    what makes it the right thing to re-attach through."""
    inference = FakeInference()
    staged(inference, idempotency_key="promo-7:pull")
    assert inference.load_calls == [("m", "1", "promo-7:pull")]


def test_a_derived_key_is_used_when_the_caller_sends_none(staged):
    """The fallback is a function of immutable inputs, so the same weights for the
    same version always reserve the same build."""
    inference = FakeInference()
    staged(inference)
    assert inference.load_calls == [("m", "1", _derived_idempotency_key("m", "1", staged.sha))]


def test_derived_keys_differ_per_version_and_per_artifact():
    same = _derived_idempotency_key("m", "1", "aa")
    assert _derived_idempotency_key("m", "1", "AA") == same, "digest case must not matter"
    assert _derived_idempotency_key("m", "2", "aa") != same
    assert _derived_idempotency_key("m", "1", "bb") != same
    assert _derived_idempotency_key("other", "1", "aa") != same


# ---------------------------------------------------------------------- delete
def test_delete_removes_the_version_directory(tmp_path, monkeypatch):
    """MM owns the repository's writes, so it owns the deletes; nothing else does."""
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    version_dir = tmp_path / "repo" / "m" / "1"
    version_dir.mkdir(parents=True)
    (version_dir / "model.pth").write_bytes(b"weights")
    (version_dir / "model.plan").write_bytes(b"engine")

    ExplicitActivation(inference=FakeInference()).delete("m", "1")

    assert not version_dir.exists()

    assert (tmp_path / "repo" / "m").exists()


def test_delete_of_an_absent_directory_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    ExplicitActivation(inference=FakeInference()).delete("m", "does-not-exist")


# ---------------------------------------------------------------- temp staging
# Sidecars publish through a temp file and an atomic rename. Three properties of
# that temp regress easily: unique per request, cleaned up on a failed download,
# and normal repository permissions on the published file.


def test_an_overlapping_pull_does_not_steal_the_others_temp_file(tmp_path, monkeypatch):
    """Two pulls for the same {name, version} must not share a temp path.

    Single-threaded on purpose: the inner pull runs to completion from inside the
    outer pull's spec download, which is the exact interleaving a threaded test
    would be trying to hit, without the flake. A fixed ``spec.yaml.tmp`` makes the
    inner rename consume the outer's temp, and the outer ``os.replace`` then dies
    with a bare FileNotFoundError -- which escapes as an un-enveloped 500, because
    the endpoint only catches ModelManagerError.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    spec = tmp_path / "source" / "spec.yaml"
    spec.write_text("spec: yes\n")
    sha = hashlib.sha256(data).hexdigest()

    outer = ExplicitActivation(inference=FakeInference())
    real_download = outer._download
    reentered = False

    def _download_and_reenter(artifact_uri, dest):
        nonlocal reentered
        real_download(artifact_uri, dest)
        if dest.name.endswith(".tmp") and not reentered:
            reentered = True
            # A fresh instance, so this inner pull uses the real _download.
            ExplicitActivation(inference=FakeInference()).stage_and_load(
                "m", "1", f"file://{src}", sha, spec_uri=f"file://{spec}"
            )

    monkeypatch.setattr(outer, "_download", _download_and_reenter)
    outer.stage_and_load("m", "1", f"file://{src}", sha, spec_uri=f"file://{spec}")

    assert reentered is True, "the interleaving never happened; the test proves nothing"
    version_dir = tmp_path / "repo" / "m" / "1"
    assert (version_dir / SPEC_FILENAME).read_text() == "spec: yes\n"


def test_a_failed_spec_download_leaves_no_temp_behind(tmp_path, monkeypatch):
    """The staging temp is cleaned up on the failure path, not just the happy one."""
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    activation = ExplicitActivation(inference=FakeInference())
    real_download = activation._download

    def _fail_on_spec(artifact_uri, dest):
        if dest.name.endswith(".tmp"):
            dest.write_text("partial")
            raise ModelManagerError(
                code="registry_unreachable", message="boom", retryable=True, http_status=502
            )
        real_download(artifact_uri, dest)

    monkeypatch.setattr(activation, "_download", _fail_on_spec)
    with pytest.raises(ModelManagerError) as excinfo:
        activation.stage_and_load("m", "1", f"file://{src}", sha, spec_uri="file:///nope")

    assert excinfo.value.code == "registry_unreachable", "the original error must survive cleanup"
    version_dir = tmp_path / "repo" / "m" / "1"
    # The directory survives deliberately. An earlier revision removed it here and
    # raced a concurrent pull: both callers saw it absent, both created it, and
    # the failing one deleted the directory the other was staging into. Residue is
    # prevented by NOT creating the directory for a request that cannot succeed.
    assert [p.name for p in version_dir.iterdir() if p.name.endswith(".tmp")] == []


def test_a_partly_staged_version_directory_is_kept(tmp_path, monkeypatch):
    """Cleanup removes only what it created AND left empty.

    The artifact lands, then the spec download fails. The directory now holds
    real bytes, so removing it would destroy a successful download on the way
    out of an unrelated failure -- and the next pull would re-fetch it. Only a
    directory this call created and did not write to is reclaimed.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    activation = ExplicitActivation(inference=FakeInference())
    real_download = activation._download

    def _fail_only_on_spec(artifact_uri, dest):
        if "spec" in dest.name:
            raise ModelManagerError(
                code="registry_unreachable", message="boom", retryable=True, http_status=502
            )
        real_download(artifact_uri, dest)

    monkeypatch.setattr(activation, "_download", _fail_only_on_spec)
    with pytest.raises(ModelManagerError):
        activation.stage_and_load("m", "1", f"file://{src}", sha, spec_uri="file:///nope")

    version_dir = tmp_path / "repo" / "m" / "1"
    assert version_dir.exists(), "a directory holding a staged artifact must be kept"
    assert (version_dir / "model.onnx").read_bytes() == data
    assert not list(version_dir.glob("*.tmp")), "a staging temp was left behind"


def test_a_pull_rejected_before_writing_leaves_no_version_directory(tmp_path, monkeypatch):
    """A bad request must not permanently pollute a shared model repository.

    The version directory is created before the URI scheme is even dispatched,
    so a pull rejected for an unsupported scheme used to leave an empty
    directory behind. On a Triton-style repo an empty version directory IS a
    version, so a caller's typo became a phantom version that outlived it -- and
    on a shared mount nobody owns it afterwards.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))

    activation = ExplicitActivation(inference=FakeInference())
    with pytest.raises(ModelManagerError):
        activation.stage_and_load("m", "1", "s3://bucket/key", "0" * 64)

    assert not (
        tmp_path / "repo" / "m" / "1"
    ).exists(), "a rejected pull left a phantom version directory in the repository"


def test_staged_files_keep_the_repositorys_normal_permissions(tmp_path, monkeypatch):
    """The serving stack is a different uid reading these through a shared group.

    Publishing through a 0600 temp (``tempfile.mkstemp``) would survive
    ``os.replace``, which preserves the temp's mode -- and an unreadable
    metadata.json is not an error there, it is a silent fallback to a hardcoded
    default threshold. Asserted against a control file rather than a literal bit,
    because the correct mode is whatever the umask gives every other write here.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    spec = tmp_path / "source" / "spec.yaml"
    spec.write_text("spec: yes\n")
    sha = hashlib.sha256(data).hexdigest()

    ExplicitActivation(inference=FakeInference()).stage_and_load(
        "m", "1", f"file://{src}", sha, spec_uri=f"file://{spec}", threshold=0.5
    )

    version_dir = tmp_path / "repo" / "m" / "1"
    control = version_dir / "control"
    control.write_text("x")
    expected = control.stat().st_mode & 0o777
    for name in (SPEC_FILENAME, METADATA_FILENAME):
        assert (version_dir / name).stat().st_mode & 0o777 == expected, name


def test_an_absent_threshold_does_not_clear_a_staged_one(tmp_path, monkeypatch):
    """Characterization: absent means "not supplied", not "remove the old one".

    A supplied threshold is always rewritten (see
    ``test_stage_and_load_always_restages_a_changed_threshold``), so the sidecar is
    deliberately mutable. Deleting it on an absent threshold would not restore
    "no threshold" -- the serving stack has no such state and silently falls back
    to a hardcoded default, changing the decision boundary on live traffic.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    activation = ExplicitActivation(inference=FakeInference())
    activation.stage_and_load("m", "1", f"file://{src}", sha, threshold=0.42)
    before = (tmp_path / "repo" / "m" / "1" / METADATA_FILENAME).read_text()
    activation.stage_and_load("m", "1", f"file://{src}", sha)

    assert (tmp_path / "repo" / "m" / "1" / METADATA_FILENAME).read_text() == before


def test_two_concurrent_pulls_publish_one_valid_artifact(tmp_path, monkeypatch):
    """Two overlapping pulls leave exactly one complete, digest-valid artifact.

    Nothing serialises the two threads. Each downloads to its own temp file and
    publishes by rename after verifying, so both may fetch and the result is
    still one correct artifact.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights" * 4096
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    downloads = []
    barrier = threading.Barrier(2, timeout=10)

    def _slow_download(self, artifact_uri, dest):
        downloads.append(dest)
        # Force the two downloads to overlap.
        with contextlib.suppress(threading.BrokenBarrierError, Exception):
            barrier.wait(timeout=0.5)
        dest.write_bytes(data)

    monkeypatch.setattr(ExplicitActivation, "_download", _slow_download)

    errors: list[BaseException] = []

    def _pull():
        try:
            ExplicitActivation(inference=FakeInference()).stage_and_load(
                "m", "1", f"file://{src}", sha
            )
        except BaseException as exc:  # - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=_pull) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, f"a concurrent pull raised: {errors}"
    published = tmp_path / "repo" / "m" / "1" / "model.onnx"
    assert published.read_bytes() == data, "the published artifact must be one writer's bytes"
    assert hashlib.sha256(published.read_bytes()).hexdigest() == sha

    assert not list(published.parent.glob("*.tmp")), "a staging temp was left behind"
    # Racing for the same destination is not an error.
    assert len(errors) == 0
    # Each attempt has its own temp, so neither touches bytes the other is
    # still writing.
    assert len(set(downloads)) == len(downloads), f"attempts shared a temp file: {downloads}"
    # With no lock, two overlapping first pulls both fetch. Revisit if dedup
    # is ever reintroduced.
    assert len(downloads) == 2, (
        f"expected both overlapping pulls to download (no dedup by design), "
        f"got {len(downloads)}: {downloads}"
    )


def test_a_failing_pull_does_not_delete_a_concurrent_pulls_directory(tmp_path, monkeypatch):
    """A pull that fails must not remove the directory another pull is staging into.

    The both-succeed pair above is not the dangerous interleaving, and being green
    on it is what let a real corruption ship. This is the dangerous one: one pull
    creates the version directory and then fails its digest check while a second
    pull for the same {name, version} sits inside its download with its temp not
    yet created -- so the directory is legitimately empty at the instant the
    failing pull cleans up. A "remove it only if we created it and it was still
    empty" cleanup then deletes the second pull's directory out from under it and
    the valid artifact never lands.

    This is a cross-request property, which is the point. Every other guard here
    is a single-request post-condition or a source grep, and both can be written
    to agree with a broken implementation -- one of them was: at the commit that
    carried the bug, a sibling test asserted the version directory's *removal* as
    the correct outcome. A test whose subject is the OTHER request cannot be
    satisfied that way.

    Ordered entirely by events, never by timing: the doomed pull parks in
    ``_download`` until the valid pull is parked in its own, and the valid pull
    does not write until the doomed one has finished unwinding. Only the failing
    pull needs to have created the directory, so the ``exists()``/``mkdir`` race
    never has to be won and there is nothing to lose. Measured red on every run
    against the commit that carried the cleanup, and green under CPU saturation
    and on a single core.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights" * 4096
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    doomed_owns_the_dir = threading.Event()
    valid_is_mid_download = threading.Event()
    doomed_has_unwound = threading.Event()

    def _download(self, artifact_uri, dest):
        if artifact_uri.endswith("#doomed"):
            doomed_owns_the_dir.set()
            assert valid_is_mid_download.wait(timeout=10), "the valid pull never downloaded"
            dest.write_bytes(b"not the expected bytes")
            return
        valid_is_mid_download.set()
        assert doomed_has_unwound.wait(timeout=10), "the doomed pull never unwound"
        dest.write_bytes(data)

    monkeypatch.setattr(ExplicitActivation, "_download", _download)
    errors: dict[str, BaseException] = {}

    def _doomed():
        try:
            ExplicitActivation(inference=FakeInference()).stage_and_load(
                "m", "1", f"file://{src}#doomed", "0" * 64
            )
        except BaseException as exc:  # - asserted below
            errors["doomed"] = exc
        finally:
            # In `finally` so the valid pull cannot block forever: an unexpected
            # death in the doomed one fails loudly rather than hanging.
            doomed_has_unwound.set()

    def _valid():
        try:
            ExplicitActivation(inference=FakeInference()).stage_and_load(
                "m", "1", f"file://{src}#valid", sha
            )
        except BaseException as exc:  # - asserted below
            errors["valid"] = exc

    doomed = threading.Thread(target=_doomed, daemon=True)
    doomed.start()
    assert doomed_owns_the_dir.wait(timeout=10), "the doomed pull never created the directory"
    valid = threading.Thread(target=_valid, daemon=True)
    valid.start()
    for thread in (doomed, valid):
        thread.join(timeout=30)

    assert isinstance(errors.get("doomed"), ModelManagerError)
    assert errors["doomed"].code == "checksum_mismatch"
    assert "valid" not in errors, f"the failing pull broke the valid one: {errors['valid']!r}"
    published = tmp_path / "repo" / "m" / "1" / "model.onnx"
    assert (
        published.exists()
    ), "the failing pull removed the version directory the valid pull was staging into"
    assert hashlib.sha256(published.read_bytes()).hexdigest() == sha


def test_a_repeat_pull_downloads_nothing(tmp_path, monkeypatch):
    """A repeat pull skips the download when the artifact is already at the digest.

    This is what stops a retry storm re-fetching multi-GB weights. The check is
    on disk, so it also holds across a restart or a second replica.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights" * 1024
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    downloads: list = []
    real_download = ExplicitActivation._download

    def _counting_download(self, artifact_uri, dest):
        downloads.append(dest)
        return real_download(self, artifact_uri, dest)

    monkeypatch.setattr(ExplicitActivation, "_download", _counting_download)

    act = ExplicitActivation(inference=FakeInference())
    act.stage_and_load("m", "1", f"file://{src}", sha)
    assert len(downloads) == 1, "the first pull must actually fetch"

    for _ in range(3):
        act.stage_and_load("m", "1", f"file://{src}", sha)

    assert len(downloads) == 1, (
        f"a re-driven pull must not re-fetch an artifact already at the "
        f"requested digest, but {len(downloads)} downloads ran"
    )
    published = tmp_path / "repo" / "m" / "1" / "model.onnx"
    assert hashlib.sha256(published.read_bytes()).hexdigest() == sha


def test_a_corrupt_artifact_is_repaired_by_the_next_pull(tmp_path, monkeypatch):
    """An artifact that does not match the requested digest is replaced, not trusted.

    The digest check decides between skip and fetch, so it doubles as a repair
    for a file left by an interrupted write or a bad disk.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"the real weights" * 512
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    published = tmp_path / "repo" / "m" / "1" / "model.onnx"
    published.parent.mkdir(parents=True, exist_ok=True)
    published.write_bytes(b"corrupt leftovers")

    ExplicitActivation(inference=FakeInference()).stage_and_load("m", "1", f"file://{src}", sha)

    assert published.read_bytes() == data, "a mismatched artifact must be replaced, not trusted"
    assert hashlib.sha256(published.read_bytes()).hexdigest() == sha
    assert not list(published.parent.glob("*.tmp")), "a staging temp was left behind"


def test_a_digest_mismatch_publishes_nothing(tmp_path, monkeypatch):
    """Verify before publish, so a bad download never lands at the final path."""
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    src = _write_artifact(tmp_path, b"actual bytes")
    wrong_sha = hashlib.sha256(b"expected something else").hexdigest()

    act = ExplicitActivation(inference=FakeInference())
    with pytest.raises(ModelManagerError) as excinfo:
        act.stage_and_load("m", "1", f"file://{src}", wrong_sha)

    assert excinfo.value.code == "checksum_mismatch"
    version_dir = tmp_path / "repo" / "m" / "1"
    assert not (version_dir / "model.onnx").exists(), "a mismatched artifact must not be published"
    assert not list(version_dir.glob("*.tmp")), "the staging temp must be cleaned up"


def _staged(tmp_path, monkeypatch, data=b"weights", *, mode=0o555):
    """A repo with {m,1} already staged and its version directory read-only.

    The shape of a re-pull onto a mount whose permissions changed under it: the
    weights are present and digest-matching from an earlier pull, so the guarded
    download is skipped and the sidecar writes are the next thing to run.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    version_dir = tmp_path / "repo" / "m" / "1"
    version_dir.mkdir(parents=True)
    (version_dir / "model.onnx").write_bytes(data)
    os.chmod(version_dir, mode)
    return version_dir


def test_an_unwritable_model_repo_is_enveloped_and_not_retryable(tmp_path, monkeypatch):
    """A model repo the Model Manager cannot write must not be retried forever.

    Makes the repository genuinely unwritable rather than faking a failure past
    the ``mkdir``: on a fresh shared volume that is the FIRST write of the verb,
    it runs before any download, and a fake injected into ``_download`` proves
    nothing about it. A permission denial must surface as a ``ModelManagerError``
    envelope, non-retryable, carrying the operator remedy.
    """
    if os.getuid() == 0:
        pytest.skip("running as root: a 0o555 directory does not deny root")
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(settings, "model_repo_path", str(repo))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()
    os.chmod(repo, 0o555)

    act = ExplicitActivation(inference=FakeInference())
    try:
        with pytest.raises(ModelManagerError) as excinfo:
            act.stage_and_load("m", "1", f"file://{src}", sha)
    finally:
        os.chmod(repo, 0o755)

    err = excinfo.value
    assert err.http_status == 500
    assert err.retryable is False, "a mount that cannot be written will not become writable"
    assert "must be writable by both" in err.message, "the operator remedy was dropped"


def test_an_unwritable_version_directory_is_enveloped_when_staging_the_spec(tmp_path, monkeypatch):
    """The spec sidecar is staged outside the download's guard; it needs one too.

    ``spec_uri`` is set for a first model or a spec change, so this block runs a
    full download and a disk write on the promotion path of every new model.
    """
    if os.getuid() == 0:
        pytest.skip("running as root: a 0o555 directory does not deny root")
    data = b"weights"
    version_dir = _staged(tmp_path, monkeypatch, data)
    spec = tmp_path / "source" / "spec.yaml"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("a: 1\n")
    sha = hashlib.sha256(data).hexdigest()

    act = ExplicitActivation(inference=FakeInference())
    try:
        with pytest.raises(ModelManagerError) as excinfo:
            act.stage_and_load(
                "m", "1", f"file://{tmp_path}/source/model.onnx", sha, spec_uri=f"file://{spec}"
            )
    finally:
        os.chmod(version_dir, 0o755)

    assert excinfo.value.retryable is False
    assert "must be writable by both" in excinfo.value.message


def test_an_unwritable_version_directory_is_enveloped_when_staging_the_metadata(
    tmp_path, monkeypatch
):
    """The metadata sidecar is the third staging block and was equally unguarded."""
    if os.getuid() == 0:
        pytest.skip("running as root: a 0o555 directory does not deny root")
    data = b"weights"
    version_dir = _staged(tmp_path, monkeypatch, data)
    sha = hashlib.sha256(data).hexdigest()

    act = ExplicitActivation(inference=FakeInference())
    try:
        with pytest.raises(ModelManagerError) as excinfo:
            act.stage_and_load(
                "m", "1", f"file://{tmp_path}/source/model.onnx", sha, threshold=0.42
            )
    finally:
        os.chmod(version_dir, 0o755)

    assert excinfo.value.retryable is False
    assert "must be writable by both" in excinfo.value.message


def test_a_re_pull_that_cannot_read_the_staged_artifact_is_enveloped(tmp_path, monkeypatch):
    """The idempotence short-circuit hashes the file on disk, and that read can fail.

    This is the DFW retry path: the first pull's response was lost, the same
    idempotency key comes back, and the digest check opens the artifact already
    staged. An artifact left unreadable (wrong uid after a mount change, a
    crashed writer) failed here, before any guard.
    """
    if os.getuid() == 0:
        pytest.skip("running as root: a 0o000 file is still readable by root")
    data = b"weights"
    version_dir = _staged(tmp_path, monkeypatch, data, mode=0o755)
    os.chmod(version_dir / "model.onnx", 0o000)
    sha = hashlib.sha256(data).hexdigest()

    act = ExplicitActivation(inference=FakeInference())
    try:
        with pytest.raises(ModelManagerError) as excinfo:
            act.stage_and_load("m", "1", f"file://{tmp_path}/source/model.onnx", sha)
    finally:
        os.chmod(version_dir / "model.onnx", 0o644)

    assert excinfo.value.code == "internal"
    assert excinfo.value.retryable is False, "a mode-000 file does not become readable"
    assert "model.onnx" in excinfo.value.message, "the operator needs the file that failed"


def test_a_load_failure_is_not_relabelled_as_a_staging_failure(tmp_path, monkeypatch):
    """The guard must stop at the end of staging, not swallow the backend load.

    Pins the boundary the fix depends on: ``_load_to_ready`` is a backend call,
    ``requests`` exceptions subclass ``OSError``, and wrapping the whole method
    would report a serving-stack outage as an unwritable model repository and
    hand the operator a mount remedy for a problem that is not on the mount.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    def _load_denied(self, name, version, idempotency_key):
        raise PermissionError(13, "Permission denied", "/backend/socket")

    monkeypatch.setattr(ExplicitActivation, "_load_to_ready", _load_denied)

    act = ExplicitActivation(inference=FakeInference())
    with pytest.raises(PermissionError):
        act.stage_and_load("m", "1", f"file://{src}", sha)


def test_a_transient_staging_failure_stays_retryable(tmp_path, monkeypatch):
    """Only a permission denial is terminal; a full disk stays retryable."""
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights"
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    def _no_space(self, artifact_uri, dest):
        raise OSError(28, "No space left on device", str(dest))

    monkeypatch.setattr(ExplicitActivation, "_download", _no_space)

    act = ExplicitActivation(inference=FakeInference())
    with pytest.raises(ModelManagerError) as excinfo:
        act.stage_and_load("m", "1", f"file://{src}", sha)

    assert excinfo.value.retryable is True
    assert "No space left" in excinfo.value.message


def test_a_checksum_mismatch_keeps_its_own_error(tmp_path, monkeypatch):
    """The OS-error translation must not relabel a digest mismatch as an IO failure.

    ``checksum_mismatch`` is raised inside the same block and must stay
    non-retryable.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    src = _write_artifact(tmp_path, b"actual bytes")
    wrong = hashlib.sha256(b"something else").hexdigest()

    act = ExplicitActivation(inference=FakeInference())
    with pytest.raises(ModelManagerError) as excinfo:
        act.stage_and_load("m", "1", f"file://{src}", wrong)

    assert excinfo.value.code == "checksum_mismatch"
    assert excinfo.value.retryable is False


def test_a_rejected_pull_touches_the_filesystem_not_at_all(tmp_path, monkeypatch):
    """Validation precedes creation -- the property that removes the race.

    An earlier revision created the version directory first and deleted it again
    on the way out. That cleanup could not be made safe without a lock this verb
    promises not to take: two concurrent pulls for the same version both see the
    directory absent, both believe they created it, and the one that fails
    deletes the directory the other is staging into -- during the whole
    connect-and-headers latency, while it is legitimately still empty.

    Rejecting the locator BEFORE ``mkdir`` removes the residue without the
    cleanup, so there is no window to race. Asserted the way it is observable:
    with the repository unwritable, an unsupported scheme must still come back as
    the 501 scheme error. If anything touched the filesystem first, the
    PermissionError would win and the code would be 500.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(settings, "model_repo_path", str(repo))
    repo.chmod(0o555)  # nothing may be created under it
    try:
        activation = ExplicitActivation(inference=FakeInference())
        with pytest.raises(ModelManagerError) as excinfo:
            activation.stage_and_load("m", "1", "s3://bucket/key", "0" * 64)
    finally:
        repo.chmod(0o755)

    assert excinfo.value.http_status == 501, (
        "the pull touched the filesystem before validating the locator: it "
        f"failed with {excinfo.value.http_status} ({excinfo.value.code}) rather "
        "than the 501 scheme rejection"
    )
    assert not (repo / "m").exists(), "a rejected pull created a directory"


def test_no_code_path_removes_a_version_directory(tmp_path, monkeypatch):
    """The invariant behind the above, stated once so a future cleanup cannot creep back.

    A version directory is only ever created and written to. Nothing removes one
    on a failure path -- ``cleanup``/``delete_version`` is the only verb allowed
    to delete, and it is gated. A helpful-looking rmdir on the staging error path
    is what raced a concurrent pull.
    """
    import inspect

    from src.adapters import activation as activation_mod

    source = inspect.getsource(activation_mod.StagingActivation.stage_and_load)
    source += inspect.getsource(activation_mod.StagingActivation._stage)
    source += inspect.getsource(activation_mod.StagingActivation._stage_into)

    for forbidden in ("rmdir", "rmtree"):
        assert forbidden not in source, (
            f"{forbidden} reappeared on the staging path; removing a version "
            "directory there races a concurrent pull for the same version"
        )


def test_a_dropped_download_stays_retryable(tmp_path, monkeypatch):
    """A transport failure mid-stream is the one case retry MUST stay open for.

    An earlier revision classified the whole ``requests`` exception tree as a
    caller fault, to stop a malformed URL being re-driven forever. That also
    caught a connection reset partway through a multi-GB weights transfer --
    where ``_download`` does not resume, and ``request_with_retries`` only covers
    failures at request INITIATION, so a re-drive is the only recovery there is.
    Marking it non-retryable removed the sole path back from a dropped transfer.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    activation = ExplicitActivation(inference=FakeInference())

    def _reset_mid_stream(artifact_uri, dest):
        raise requests.exceptions.ChunkedEncodingError("Connection broken: ConnectionResetError")

    monkeypatch.setattr(activation, "_download", _reset_mid_stream)
    with pytest.raises(ModelManagerError) as excinfo:
        activation.stage_and_load("m", "1", "http://host/w.pth", "0" * 64)

    assert excinfo.value.retryable is True, (
        "a mid-stream reset was branded non-retryable; there is no resume, so "
        "this removes the only recovery a dropped transfer has"
    )


@pytest.mark.parametrize(
    "bad_uri",
    ["http://host:notaport/x", "://nohost/x", "http://"],
)
def test_an_unfixable_locator_is_not_retryable(tmp_path, monkeypatch, bad_uri):
    """The other half: a locator the caller got wrong must not be re-driven.

    Kept alongside the test above because the two pull in opposite directions
    and the first attempt at this collapsed them into one over-broad rule.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    activation = ExplicitActivation(inference=FakeInference())

    with pytest.raises(ModelManagerError) as excinfo:
        activation.stage_and_load("m", "1", bad_uri, "0" * 64)

    assert (
        excinfo.value.retryable is False
    ), f"{bad_uri!r} cannot come right on a re-drive, so it must not ask for one"


def test_an_already_staged_version_ignores_an_unfetchable_locator(tmp_path, monkeypatch):
    """The pre-flight must gate the FETCH, not the request.

    ``stage_and_load`` promises to converge "whatever the starting state", and an
    artifact already present with a matching digest skips the download entirely.
    An earlier revision validated the locator unconditionally before doing
    anything, so a re-drive of a version that was already staged and verified
    failed on a locator it never needed to use -- breaking the idempotency the
    docstring states.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights" * 64
    sha = hashlib.sha256(data).hexdigest()

    version_dir = tmp_path / "repo" / "m" / "1"
    version_dir.mkdir(parents=True)
    (version_dir / _artifact_filename("s3://bucket/key")).write_bytes(data)

    activation = ExplicitActivation(inference=FakeInference())
    # No exception: the digest short-circuit runs and the locator is irrelevant.
    assert activation.stage_and_load("m", "1", "s3://bucket/key", sha).endswith("m/1")


def test_a_spec_rejected_pull_does_leave_the_staged_artifact_behind(tmp_path, monkeypatch):
    """States the residue the artifact-locator guarantee does NOT cover.

    ``test_a_rejected_pull_touches_the_filesystem_not_at_all`` covers a rejected
    *artifact* locator: nothing is created, so nothing is left. A rejected
    ``spec_uri`` is the opposite by design -- the artifact has already downloaded
    and is deliberately kept, so a corrected re-drive lands on the digest
    short-circuit instead of re-fetching (see
    ``test_a_bad_spec_uri_does_not_discard_the_staged_artifact``).

    The cost is a version directory holding weights but no spec, which the
    inventory's on-disk listing will report as a known version. That is the
    accepted side of the trade, and it matches the pre-refactor behaviour: on the
    merge base ``_stage`` creates the directory, downloads, and only then reaches
    ``spec_uri``, so this residue is not something the staging rework introduced.

    Pinned because the guarantee was stated unscoped in prose while being scoped
    to the artifact locator in the code, and nothing tested the difference.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights" * 64
    sha = hashlib.sha256(data).hexdigest()
    source = tmp_path / "model.onnx"
    source.write_bytes(data)

    activation = ExplicitActivation(inference=FakeInference())
    with pytest.raises(ModelManagerError) as excinfo:
        activation.stage_and_load(
            "ph", "7", f"file://{source}", sha, spec_uri="s3://deft-specs/ph/7/spec.yaml"
        )
    assert excinfo.value.http_status == 501

    version_dir = tmp_path / "repo" / "ph" / "7"
    staged = version_dir / "model.onnx"
    assert staged.exists(), "the downloaded artifact is deliberately kept"
    assert hashlib.sha256(staged.read_bytes()).hexdigest() == sha
    assert not (version_dir / "spec.yaml").exists()
    # The artifact plus the staging marker this pull never released: a re-drive
    # resumes from these bytes while the backend calls the directory deletable.
    assert sorted(p.name for p in version_dir.iterdir()) == [
        activation_mod.STAGING_LOCK_FILENAME,
        "model.onnx",
    ]


def test_the_short_circuit_is_scoped_to_the_locators_extension(tmp_path, monkeypatch):
    """Pins how far the locator-independent re-drive actually reaches.

    ``_artifact_filename`` derives the staged name from the URI suffix, so
    "already staged" is answered about ``model.onnx`` or ``model.pth``, not about
    the version. A re-drive that keeps the extension short-circuits and never
    needs the scheme; one that changes it is asking about a file that is not
    there, so it is a real fetch and an unfetchable scheme is correctly refused.

    This is the boundary of the idempotency the sibling test asserts, found by
    driving both variants at the wire. Pinned so the scope is stated rather than
    assumed either way.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights" * 64
    sha = hashlib.sha256(data).hexdigest()

    version_dir = tmp_path / "repo" / "m" / "1"
    version_dir.mkdir(parents=True)
    (version_dir / "model.onnx").write_bytes(data)

    activation = ExplicitActivation(inference=FakeInference())

    # Same extension: short-circuits, scheme never consulted.
    assert activation.stage_and_load("m", "1", "s3://bucket/key.onnx", sha).endswith("m/1")

    # A different extension names model.pth, which is absent, so this is a fetch
    # and the scheme is refused with the staged artifact intact.
    with pytest.raises(ModelManagerError) as excinfo:
        activation.stage_and_load("m", "1", "s3://bucket/key.pth", sha)
    assert excinfo.value.http_status == 501
    assert (version_dir / "model.onnx").read_bytes() == data


def test_a_bad_spec_uri_does_not_discard_the_staged_artifact(tmp_path, monkeypatch):
    """Failing fast is the wrong trade when the skipped work is the expensive part.

    An earlier revision pre-validated ``spec_uri`` alongside the artifact, so a
    bad spec rejected the pull before the weights downloaded. The pull fails
    either way -- but with the artifact staged, a corrected re-drive skips the
    multi-GB fetch on the digest short-circuit. Rejecting first throws that away.
    """
    monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
    data = b"weights" * 64
    src = _write_artifact(tmp_path, data)
    sha = hashlib.sha256(data).hexdigest()

    activation = ExplicitActivation(inference=FakeInference())
    with pytest.raises(ModelManagerError):
        activation.stage_and_load("m", "2", f"file://{src}", sha, spec_uri="s3://bad/spec")

    staged = tmp_path / "repo" / "m" / "2" / "model.onnx"
    assert staged.exists() and staged.read_bytes() == data, (
        "the artifact was discarded because the SPEC locator was bad; a corrected "
        "retry now re-downloads it"
    )


class TestTheStagingInterlock:
    """``.staging.lock`` -- the writer's declaration that a pull owns a directory.

    Measured on deft: a directory that merely exists is published as deletable
    within seconds (``mkdir 9`` -> ``deletable: ['2','4','9']``, ``rm -rf 9`` ->
    ``['2','4']``), and that list is the single delete authority. So the backend
    nominates an in-flight pull's directory; the marker is the contradiction.
    """

    def _pull(self, tmp_path, monkeypatch, *, data=b"weights" * 64, **kwargs):
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        src = _write_artifact(tmp_path, data)
        sha = hashlib.sha256(data).hexdigest()
        activation = ExplicitActivation(inference=FakeInference())
        return activation, f"file://{src}", sha

    def test_the_lock_is_present_for_the_whole_download_and_gone_after(self, tmp_path, monkeypatch):
        """The end state alone would stay green with the marker written after the
        download rather than at ``mkdir``, which is the window that matters.
        """
        activation, uri, sha = self._pull(tmp_path, monkeypatch)
        version_dir = tmp_path / "repo" / "m" / "1"
        seen = []
        real_download = activation._download

        def _observe(artifact_uri, dest):
            seen.append(activation_mod.staging_in_progress("m", "1"))
            return real_download(artifact_uri, dest)

        monkeypatch.setattr(activation, "_download", _observe)
        activation.stage_and_load("m", "1", uri, sha)

        assert seen == [True], "the directory was not marked while its artifact downloaded"
        assert not (version_dir / activation_mod.STAGING_LOCK_FILENAME).exists()
        assert activation_mod.staging_in_progress("m", "1") is False

    def test_the_lock_is_still_held_while_the_version_loads(self, tmp_path, monkeypatch):
        """``_stage`` returns once the bytes land, but the load that follows compiles
        an engine back into the version directory. Releasing at the end of
        ``_stage`` leaves it unmarked for the whole build.
        """
        activation, uri, sha = self._pull(tmp_path, monkeypatch)
        seen = []
        monkeypatch.setattr(
            activation,
            "_load_to_ready",
            lambda name, version, key: seen.append(
                activation_mod.staging_in_progress(name, version)
            ),
        )
        activation.stage_and_load("m", "1", uri, sha)

        assert seen == [True], "the directory was unmarked while its engine built"
        assert activation_mod.staging_in_progress("m", "1") is False

    def test_a_directory_already_on_disk_is_claimed_before_it_is_re_hashed(
        self, tmp_path, monkeypatch
    ):
        """The digest short-circuit hashes the staged artifact, which takes seconds
        on a real one. Claiming after that read leaves the directory unmarked
        for the whole hash.
        """
        activation, uri, sha = self._pull(tmp_path, monkeypatch)
        activation.stage_and_load("m", "1", uri, sha)

        seen = []
        real_sha256_of = activation_mod._sha256_of

        def _observe(path):
            seen.append(activation_mod.staging_in_progress("m", "1"))
            return real_sha256_of(path)

        monkeypatch.setattr(activation_mod, "_sha256_of", _observe)
        activation.stage_and_load("m", "1", uri, sha)

        assert seen == [True], "the re-drive hashed an unmarked directory"
        assert activation_mod.staging_in_progress("m", "1") is False

    def test_the_lock_carries_a_timestamp_an_operator_can_age(self, tmp_path, monkeypatch):
        """A leaked marker makes a directory uncollectable, so a human has to be able
        to tell a stale one from a live one without guessing.
        """
        activation, uri, sha = self._pull(tmp_path, monkeypatch)
        version_dir = tmp_path / "repo" / "m" / "1"
        captured = {}

        def _capture(artifact_uri, dest):
            captured["lock"] = json.loads(
                (version_dir / activation_mod.STAGING_LOCK_FILENAME).read_text()
            )
            raise ModelManagerError(
                code="internal", message="boom", retryable=True, http_status=500
            )

        monkeypatch.setattr(activation, "_download", _capture)
        with pytest.raises(ModelManagerError):
            activation.stage_and_load("m", "1", uri, sha)

        assert captured["lock"]["pid"] == os.getpid()
        assert captured["lock"]["claimed_at"] == pytest.approx(time.time(), abs=60)
        # Epoch for a script to subtract, ISO for a human reading a `cat`.
        assert captured["lock"]["claimed_at_iso"].startswith(datetime.now(UTC).strftime("%Y-%m-%d"))
        assert captured["lock"]["model_name"] == "m" and captured["lock"]["version"] == "1"

    def test_a_pull_that_fails_leaves_its_lock_behind(self, tmp_path, monkeypatch):
        """A re-drive resumes from a partly staged directory, and the backend calls
        it deletable like any other, so releasing on the failure path would hand
        the sweep the bytes a retry is about to write into.
        """
        activation, uri, sha = self._pull(tmp_path, monkeypatch)
        with pytest.raises(ModelManagerError):
            activation.stage_and_load("m", "1", uri, sha, spec_uri="s3://bad/spec")

        assert activation_mod.staging_in_progress("m", "1") is True
        assert (
            tmp_path / "repo" / "m" / "1" / "model.onnx"
        ).exists(), "the artifact was discarded, so this is not the leak case being pinned"

    def test_a_leaked_lock_is_logged_and_released_by_the_next_successful_pull(
        self, tmp_path, monkeypatch, caplog
    ):
        """The marker's name is fixed rather than per-request, so the next pull of
        the same version owns and releases it. Logged because an operator has no
        other signal that a directory stopped being reclaimable.
        """
        activation, uri, sha = self._pull(tmp_path, monkeypatch)
        version_dir = tmp_path / "repo" / "m" / "1"
        version_dir.mkdir(parents=True)
        (version_dir / activation_mod.STAGING_LOCK_FILENAME).write_text(
            '{"claimed_at_iso": "2026-09-01T00:00:00+00:00", "pid": 4242}'
        )

        with caplog.at_level(logging.WARNING, logger=activation_mod.__name__):
            activation.stage_and_load("m", "1", uri, sha)

        assert activation_mod.staging_in_progress("m", "1") is False
        assert any(
            "leaked" in record.getMessage() for record in caplog.records
        ), "a directory that had silently stopped being reclaimable was adopted without a word"

    def test_an_absent_directory_is_not_staging(self, tmp_path, monkeypatch):
        """The one place this predicate may answer False: True would make cleanup of
        an already-absent version raise instead of reporting the reclaim done.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        assert activation_mod.staging_in_progress("m", "nope") is False

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permissions")
    def test_an_unreadable_directory_reads_as_staging(self, tmp_path, monkeypatch):
        """Not readable is never "go ahead" -- a broken NFS mount or a changed uid
        is how this read fails in production.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        version_dir = tmp_path / "repo" / "m" / "1"
        version_dir.mkdir(parents=True)
        version_dir.chmod(0o000)
        try:
            assert activation_mod.staging_in_progress("m", "1") is True
        finally:
            version_dir.chmod(0o755)

    def test_a_version_path_that_is_not_a_directory_reads_as_staging(self, tmp_path, monkeypatch):
        """``Path.exists`` answers False for ENOTDIR as well as ENOENT, so a version
        path that is a regular file would read as "not staging". Only ENOENT may
        answer False, which is why this stats explicitly.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        (tmp_path / "repo" / "m").mkdir(parents=True)
        (tmp_path / "repo" / "m" / "1").write_text("not a directory")

        assert activation_mod.staging_in_progress("m", "1") is True

    def test_the_predicate_refuses_a_path_that_escapes_the_repository(self, tmp_path, monkeypatch):
        """Same containment proof as the rmtree it guards, because it takes the same fields."""
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        with pytest.raises(ModelManagerError) as excinfo:
            activation_mod.staging_in_progress("m", "../../etc")
        assert excinfo.value.http_status == 400


class TestAStagingMarkerExpires:
    """``_claim_staging`` leaks by design: a process killed mid-stage never removes
    its marker, and only another pull of the same version clears it. Honoured
    forever, one interrupted pull costs that directory for the life of the
    deployment. The expiring cases assert the WARNING too, because an operator
    cannot otherwise tell an expiry from the guard not working.
    """

    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        version_dir = tmp_path / "repo" / "m" / "1"
        version_dir.mkdir(parents=True)
        return version_dir

    @staticmethod
    def _write_marker(version_dir, *, body, age_s):
        """Write a marker whose BOTH clocks read ``age_s`` seconds old."""
        lock = version_dir / activation_mod.STAGING_LOCK_FILENAME
        lock.write_text(body)
        claimed = time.time() - age_s
        os.utime(lock, (claimed, claimed))
        return lock

    def test_a_marker_past_the_bound_reads_as_abandoned_and_says_so(self, repo, caplog):
        """The whole point: the directory becomes collectable again, loudly."""
        self._write_marker(
            repo, body=json.dumps({"claimed_at": time.time() - 200_000, "pid": 4242}), age_s=200_000
        )

        with caplog.at_level(logging.WARNING, logger=activation_mod.__name__):
            assert activation_mod.staging_in_progress("m", "1") is False

        message = "\n".join(record.getMessage() for record in caplog.records)
        assert activation_mod.STAGING_LOCK_FILENAME in message
        # The marker's contents, so the operator sees which pull leaked.
        assert "4242" in message

    def test_a_marker_inside_the_bound_is_still_honoured(self, repo):
        """The control: an expiry firing on a live pull is worse than no expiry."""
        self._write_marker(repo, body=json.dumps({"claimed_at": time.time()}), age_s=30)
        assert activation_mod.staging_in_progress("m", "1") is True

    def test_a_garbled_marker_still_expires_on_the_filesystem_clock(self, repo, caplog):
        """A truncated marker is what a killed process leaves, so trusting only the
        timestamp inside it would fail on the one case this exists for. The
        file's mtime is a second clock the same writer set.
        """
        self._write_marker(repo, body="{not json", age_s=200_000)

        with caplog.at_level(logging.WARNING, logger=activation_mod.__name__):
            assert activation_mod.staging_in_progress("m", "1") is False
        assert any("abandoned" in record.getMessage() for record in caplog.records)

    def test_the_youngest_clock_wins(self, repo):
        """A back-dated ``claimed_at`` -- clock skew, a restored file -- must not
        expire a claim the filesystem says was made moments ago.
        """
        self._write_marker(repo, body=json.dumps({"claimed_at": time.time() - 200_000}), age_s=0)
        assert activation_mod.staging_in_progress("m", "1") is True

    def test_a_zero_bound_restores_permanent_honouring(self, repo, monkeypatch):
        """The operator's off switch, for a deployment that would rather leak disk."""
        monkeypatch.setattr(settings, "staging_lock_max_age_s", 0.0)
        self._write_marker(repo, body=json.dumps({"claimed_at": 0}), age_s=200_000)
        assert activation_mod.staging_in_progress("m", "1") is True

    def test_a_negative_bound_is_rejected_rather_than_silently_disabling_the_expiry(self):
        """The comparison is ``bound <= 0``, so ``-1`` would silently mean "never
        expire" just as ``0`` does. Rejected where the value is parsed, so the
        operator finds out at startup and not from a volume that never drains.
        """
        with pytest.raises(ValidationError):
            Settings(staging_lock_max_age_s=-1.0)

        assert Settings(staging_lock_max_age_s=0.0).staging_lock_max_age_s == 0.0


class TestTheDirectoryAgeIsMeasuredFromTheNewestEntry:
    """Measured: a directory's mtime moves when an entry is created or renamed, not
    when an existing entry is written into. A pull creates its scratch file once
    and writes it for the whole download, so the directory's own clock reads from
    the start of the pull -- which is why the floor walks the entries instead.
    """

    def test_a_long_download_does_not_age_past_the_floor(self, tmp_path, monkeypatch):
        """A multi-GB pull outlasting the 3600s floor presented as a directory
        untouched for an hour, which is all the floor checks before an rmtree.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        version_dir = tmp_path / "repo" / "m" / "1"
        version_dir.mkdir(parents=True)
        scratch = version_dir / ".model.pth.deadbeef.tmp"
        scratch.write_bytes(b"partial")
        # A download two hours in: directory created at pull start, scratch file
        # being written now.
        old = time.time() - 7200
        os.utime(version_dir, (old, old))

        age = activation_mod.version_age_seconds("m", "1")

        assert age is not None and age < 60, (
            f"a download in flight right now read as {age:.0f}s old; measured from "
            "the directory's own mtime it clears any floor a long pull outlasts"
        )

    def test_a_settled_version_still_reads_its_true_age(self, tmp_path, monkeypatch):
        """The correction must not make everything read young, or the floor never
        fires.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        version_dir = tmp_path / "repo" / "m" / "1"
        version_dir.mkdir(parents=True)
        (version_dir / "model.pth").write_bytes(b"weights")
        old = time.time() - 7200
        for path in (version_dir / "model.pth", version_dir):
            os.utime(path, (old, old))

        assert activation_mod.version_age_seconds("m", "1") == pytest.approx(7200, abs=60)

    def test_a_nested_build_artifact_counts(self, tmp_path, monkeypatch):
        """The serving build writes back a level down, so a top-level-only scan would
        miss a build still emitting.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        version_dir = tmp_path / "repo" / "m" / "1"
        (version_dir / "1").mkdir(parents=True)
        (version_dir / "1" / "model.plan").write_bytes(b"engine")
        old = time.time() - 7200
        os.utime(version_dir, (old, old))
        os.utime(version_dir / "1", (old, old))

        age = activation_mod.version_age_seconds("m", "1")
        assert age is not None and age < 60

    def test_an_unreadable_directory_is_unknown_rather_than_old(self, tmp_path, monkeypatch):
        """Genuinely unreadable, not merely absent -- against a repository that does
        not exist this would pass on the ENOENT path and prove nothing.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        version_dir = tmp_path / "repo" / "m" / "1"
        version_dir.mkdir(parents=True)
        real_stat = Path.stat

        def _denied(self, *args, **kwargs):
            if self == version_dir:
                raise PermissionError(13, "Permission denied")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", _denied)

        assert activation_mod.version_age_seconds("m", "1") is None

    def test_an_absent_directory_says_absent_rather_than_unknown(self, tmp_path, monkeypatch):
        """Could-not-read refuses the reclaim; is-not-there must not. Collapsed into
        ``None``, every retry after a successful reclaim became a permanent 409.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        (tmp_path / "repo" / "m").mkdir(parents=True)

        assert activation_mod.version_age_seconds("m", "1") == activation_mod.DIRECTORY_ABSENT

    def test_an_entry_that_vanishes_mid_walk_reads_as_just_modified(self, tmp_path, monkeypatch):
        """An entry listed a moment ago and gone now means the tree was modified
        inside this call -- zero is its true age. An ``except OSError`` around the
        whole walk reports a routine rename as an unreadable directory instead.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        version_dir = tmp_path / "repo" / "m" / "1"
        version_dir.mkdir(parents=True)
        doomed = version_dir / ".model.onnx.deadbeef.tmp"
        doomed.write_bytes(b"partial")
        old = time.time() - 7200
        os.utime(version_dir, (old, old))

        real_rglob = type(version_dir).rglob

        def _vanishing(self, pattern):
            for entry in real_rglob(self, pattern):
                entry.unlink()
                yield entry

        monkeypatch.setattr(type(version_dir), "rglob", _vanishing)

        assert activation_mod.version_age_seconds("m", "1") == 0.0


class TestProvingAVersionDirectoryAbsent:
    """``version_directory_absent`` -- the read that lets cleanup report a reclaim
    done, which retires the caller's nomination for good. So it answers True only
    on a positive observation; every failed read is False and the ordinary
    cleanup guards decide.
    """

    def test_a_missing_version_directory_is_proven_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        (tmp_path / "repo" / "m").mkdir(parents=True)

        assert activation_mod.version_directory_absent("m", "1") is True

    def test_a_directory_that_is_there_is_not_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        (tmp_path / "repo" / "m" / "1").mkdir(parents=True)

        assert activation_mod.version_directory_absent("m", "1") is False

    def test_an_unreadable_version_directory_is_not_proven_absent(
        self, tmp_path, monkeypatch, caplog
    ):
        """A permission change on a live repository would otherwise report every
        version as already reclaimed, against weights that are still there.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        version_dir = tmp_path / "repo" / "m" / "1"
        version_dir.mkdir(parents=True)
        real_stat = Path.stat

        def _denied(self, *args, **kwargs):
            if self == version_dir:
                raise PermissionError(13, "Permission denied")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", _denied)

        with caplog.at_level(logging.INFO):
            assert activation_mod.version_directory_absent("m", "1") is False

        assert "absence is unproven" in caplog.text

    def test_a_repository_root_that_is_not_there_proves_nothing(
        self, tmp_path, monkeypatch, caplog
    ):
        """At the version directory an unmounted volume and a swept repo both stat
        ``FileNotFoundError``. The root is what proves the volume is there, so
        without it one missing mount reports the whole deployment reclaimed.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "not-mounted"))

        with caplog.at_level(logging.WARNING):
            assert activation_mod.version_directory_absent("m", "1") is False

        assert "not a directory" in caplog.text

    def test_a_version_that_escapes_the_repository_raises_rather_than_answers(
        self, tmp_path, monkeypatch
    ):
        """The containment proof runs first and its refusal is not an absence: the
        path was never resolved to anything this service owns.
        """
        monkeypatch.setattr(settings, "model_repo_path", str(tmp_path / "repo"))
        (tmp_path / "repo" / "m").mkdir(parents=True)

        with pytest.raises(ModelManagerError) as excinfo:
            activation_mod.version_directory_absent("m", "../../etc")

        assert excinfo.value.code == "bad_request"
