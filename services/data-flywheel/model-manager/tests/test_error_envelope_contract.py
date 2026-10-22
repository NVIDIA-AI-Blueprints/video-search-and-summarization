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

"""The ErrorEnvelope contract, asserted at the HTTP boundary.

``ErrorEnvelope`` is documented as "the single error shape every non-2xx Model
Manager response renders". This file is the enforcement: it drives every way a
non-2xx can be produced and asserts the body is that shape. It is deliberately
not a test of any one bug -- ``test_activation.py`` pins the individual staging
sites -- so that the next unguarded call gets caught here even though nobody
wrote a test for it.

EVERY client here is built with ``raise_server_exceptions=False``. The default
is ``True``, which re-raises a handler's exception inside the test instead of
returning the response uvicorn would have sent -- so a test using the default
cannot see an un-enveloped body at all. That default is exactly why the bug
these tests pin shipped with a green suite.
"""

import hashlib
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.adapters.activation as activation
from main import app
from src.config import settings
from src.core.errors import ModelManagerError

WEIGHTS = b"weights"
SHA = hashlib.sha256(WEIGHTS).hexdigest()


@pytest.fixture
def client() -> TestClient:
    """A client that returns what an HTTP caller sees rather than re-raising."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An empty model repository the pull endpoint will write into."""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(settings, "model_repo_path", str(root))
    return root


def _pull_body(tmp_path, **overrides) -> dict:
    src = tmp_path / "source" / "model.onnx"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(WEIGHTS)
    body = {
        "model_name": "m",
        "version": "1",
        "artifact_uri": f"file://{src}",
        "format": "onnx",
        "sha256": SHA,
    }
    body.update(overrides)
    return body


def assert_enveloped(response) -> dict:
    """Assert a non-2xx response is an ErrorEnvelope; return the error object."""
    assert not 200 <= response.status_code < 300, "caller must drive a non-2xx"
    assert response.headers["content-type"].startswith("application/json"), (
        f"HTTP {response.status_code} answered {response.headers['content-type']!r} "
        f"with body {response.content!r}; every non-2xx renders an ErrorEnvelope"
    )
    try:
        body = json.loads(response.content)
    except json.JSONDecodeError as exc:  # pragma: no cover - the assert reports it
        raise AssertionError(
            f"HTTP {response.status_code} body is not JSON ({exc}): {response.content!r}"
        ) from exc
    assert isinstance(body, dict) and "error" in body, f"no 'error' key in {body!r}"
    error = body["error"]
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["retryable"], bool), (
        "retryable must be a bool: it is the only thing the caller's retry "
        "decision is allowed to rest on"
    )
    return error


# --------------------------------------------------------------- the contract
def test_every_way_a_non_2xx_is_produced_renders_an_error_envelope(client, tmp_path, monkeypatch):
    """Sweep every mechanism that can emit a non-2xx, not just the known bugs.

    The rows are mechanisms rather than endpoints on purpose: a route's own
    ``except ModelManagerError``, a staging ``OSError`` that no ``except`` sees,
    request validation, Starlette's own routing errors, a hand-built envelope,
    and an outright bug. Each is a different code path to the wire, and only the
    first was ever covered.

    Every row gets a fresh repository so one row's leftovers cannot make the
    next row's fault unreachable.
    """
    if os.getuid() == 0:
        pytest.skip("running as root: a 0o555 directory does not deny root")

    def unsupported_scheme(root):
        return client.post(
            "/v1/models/pull", json=_pull_body(tmp_path, artifact_uri="mlflow://a/b")
        )

    def digest_mismatch(root):
        return client.post("/v1/models/pull", json=_pull_body(tmp_path, sha256="0" * 64))

    def unwritable_repo(root):
        os.chmod(root, 0o555)
        try:
            return client.post("/v1/models/pull", json=_pull_body(tmp_path))
        finally:
            os.chmod(root, 0o755)

    def bug_in_the_handler(root):
        with patch.object(activation, "_artifact_filename", side_effect=ZeroDivisionError):
            return client.post("/v1/models/pull", json=_pull_body(tmp_path))

    def bad_format(root):
        return client.post("/v1/models/pull", json=_pull_body(tmp_path, format="pytorch"))

    def traversal(root):
        return client.post("/v1/models/cleanup", json={"model_name": "m", "version": "../../etc"})

    cases = [
        ("domain error: unsupported scheme", unsupported_scheme, 501),
        ("domain error: digest mismatch", digest_mismatch, 409),
        ("staging OSError: unwritable repo", unwritable_repo, 500),
        ("unhandled bug in a handler", bug_in_the_handler, 500),
        ("request validation: bad format", bad_format, 422),
        ("request validation: traversal", traversal, 422),
        ("hand-built envelope: tombstone", lambda root: client.get("/v1/models/status/x"), 410),
        ("routing: unknown path", lambda root: client.get("/v1/models/a/b"), 404),
        ("routing: wrong method", lambda root: client.get("/v1/models/pull"), 405),
    ]

    failures = []
    for index, (label, send, expected) in enumerate(cases):
        root = tmp_path / f"repo{index}"
        root.mkdir()
        monkeypatch.setattr(settings, "model_repo_path", str(root))
        response = send(root)
        try:
            assert (
                response.status_code == expected
            ), f"expected {expected}, got {response.status_code}"
            assert_enveloped(response)
        except AssertionError as exc:
            failures.append(f"  {label}: {exc}")

    assert not failures, "non-2xx responses that are not ErrorEnvelopes:\n" + "\n".join(failures)


# ------------------------------------------------------- the reported failure
def test_a_model_repo_the_service_cannot_write_is_enveloped_over_http(client, repo, tmp_path):
    """Finding C, at the wire: the first write of a pull is the mkdir.

    On a shared mount the Model Manager was never granted write access to --
    a fresh ``docker volume create`` is root:root 0755, and the container runs
    as a non-root uid -- the ``mkdir`` fails before any guarded block is
    reached, so the guard on the download never renders. The caller must still
    get an envelope, must be told not to retry, and must be handed the remedy.
    """
    if os.getuid() == 0:
        pytest.skip("running as root: a 0o555 directory does not deny root")
    os.chmod(repo, 0o555)
    try:
        response = client.post("/v1/models/pull", json=_pull_body(tmp_path))
    finally:
        os.chmod(repo, 0o755)

    assert response.status_code == 500
    error = assert_enveloped(response)
    assert error["retryable"] is False, (
        "a mount that cannot be written will not become writable; a retryable "
        "verdict here is what turns a misconfigured volume into an endless "
        "Celery re-drive"
    )
    assert "must be writable by both" in error["message"], "remedy dropped"


def test_a_bug_in_the_service_is_not_reported_as_a_staging_failure(
    client, repo, tmp_path, monkeypatch
):
    """The envelope must not lie about what went wrong.

    Guards the fix against its own worst version: broadening the staging
    translation to ``except Exception`` would make every ``TypeError`` in the
    module report as an IO failure, hand the operator a mount remedy that does
    not apply, and (for a ``PermissionError``-shaped message) mark a real bug
    non-retryable for the wrong reason.
    """
    monkeypatch.setattr(activation, "_artifact_filename", lambda uri: 1 / 0)

    response = client.post("/v1/models/pull", json=_pull_body(tmp_path))

    assert response.status_code == 500
    error = assert_enveloped(response)
    assert error["code"] == "internal"
    assert "Failed to stage" not in error["message"], "a ZeroDivisionError is not a staging failure"
    assert (
        "writable by the serving process" not in error["message"]
    ), "a bug must not be handed the mount remedy"
    assert "ZeroDivisionError" in error["message"], "the class the operator must grep for"


def test_a_validation_failure_is_enveloped_and_keeps_its_field_report(client, tmp_path):
    """422 is a non-2xx too, so the contract covers it.

    FastAPI's built-in ``{"detail": [...]}`` is valid JSON but not the envelope,
    so a caller reading ``error.code`` finds nothing. The per-field report is
    kept under ``details`` so nothing is lost by the reshape.
    """
    response = client.post("/v1/models/pull", json=_pull_body(tmp_path, format="pytorch"))

    assert response.status_code == 422
    error = assert_enveloped(response)
    assert error["code"] == "bad_request"
    assert error["retryable"] is False
    locs = [tuple(item["loc"]) for item in error["details"]["errors"]]
    assert ("body", "format") in locs


def test_a_405_still_carries_the_allow_header():
    """Enveloping the body must not discard the headers Starlette attached.

    RFC 9110 s15.5.6: a 405 MUST generate an ``Allow`` header. The app-level
    handler builds a fresh JSONResponse, so unless ``exc.headers`` is forwarded
    the header is silently lost -- a spec-compliant response turned
    non-compliant by a change that was only meant to alter the body. The same
    forwarding covers ``WWW-Authenticate`` on a 401 and ``Retry-After`` on a 503.
    """
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/v1/models/apply")  # POST-only route

    assert response.status_code == 405
    assert response.headers.get(
        "allow"
    ), "405 lost its Allow header; RFC 9110 requires it and Starlette had supplied it"
    assert "POST" in response.headers["allow"]
    # and it is still enveloped -- the header fix must not undo the body fix
    assert response.json()["error"]["code"] == "bad_request"


def test_a_staging_failure_is_logged_with_its_traceback(tmp_path, monkeypatch, caplog):
    """Converting the exception must not make the server side silent.

    Before staging IO was enveloped, an OSError reached Starlette and the
    operator got a full traceback for free. Enveloping it is what makes the
    RESPONSE good, and it is also what removes that traceback -- so the classifier
    logs before it raises. The caller gets a name and a remedy; the operator
    needs the frame.
    """
    import logging

    from src.adapters.activation import _staging_errors

    caplog.set_level(logging.ERROR)
    with pytest.raises(ModelManagerError):
        with _staging_errors("m", "1", "file:///nope"):
            raise PermissionError(13, "Permission denied", "/repo/m/1")

    assert caplog.records, "the staging failure left no server-side record at all"
    record = caplog.records[-1]
    assert record.exc_info is not None, (
        "logged without exc_info, so the traceback is still lost -- an operator "
        "debugging a download-site failure has neither a path nor a frame"
    )
    assert "m@1" in record.getMessage()
