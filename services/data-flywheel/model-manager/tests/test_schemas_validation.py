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

"""The canary-weight validators, which nothing else exercises.

Both ``ApplyRequest`` and ``Applied`` reject a canary carrying no weight, and
until these tests existed BOTH could be deleted with the whole suite still
green. That is worth pinning rather than trusting, because of what an absent
weight does downstream::

    # _control_reflected
    return canary_weight is None or abs(entry.weight - canary_weight) <= 1e-6

A ``None`` weight makes ANY observed split satisfy the check, so a 5% canary
would report converged against a 50% rollout. ``Applied`` matters more than
``ApplyRequest`` here: it is new in this change and arrives FROM THE WIRE on
every convergence poll, where before this the weight was server-held state the
caller could not influence.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas import Applied, ApplyRequest, CleanupResponse, PullRequest


def test_an_apply_request_for_a_canary_requires_a_weight():
    with pytest.raises(ValidationError, match="canary_weight"):
        ApplyRequest(model_name="m", version="2", control="canary")


def test_an_applied_expectation_for_a_canary_requires_a_weight():
    """The read side. Without this, a forged expectation converges on any split."""
    with pytest.raises(ValidationError, match="canary_weight"):
        Applied(model_name="m", to_version="2", control="canary", key="k")


@pytest.mark.parametrize("control", ["shadow", "full_switch", "unload"])
def test_a_weightless_non_canary_is_fine(control):
    """The rule is scoped to canary; it must not reject the other controls."""
    assert ApplyRequest(model_name="m", version="2", control=control).canary_weight is None
    assert Applied(model_name="m", to_version="2", control=control, key="k").canary_weight is None


def test_a_canary_with_a_weight_is_accepted_on_both_sides():
    request = ApplyRequest(model_name="m", version="2", control="canary", canary_weight=0.05)
    expectation = Applied(
        model_name="m", to_version="2", control="canary", canary_weight=0.05, key="k"
    )

    assert request.canary_weight == 0.05
    assert expectation.canary_weight == 0.05


# ---------------------------------------------------- pull locators + threshold
def _pull(**overrides):
    body = {
        "model_name": "m",
        "version": "1",
        "artifact_uri": "file:///tmp/model.onnx",
        "format": "onnx",
        "sha256": "0" * 64,
    }
    body.update(overrides)
    return PullRequest(**body)


@pytest.mark.parametrize("field", ["artifact_uri", "spec_uri"])
def test_a_locator_urlparse_cannot_read_is_rejected_at_the_boundary(field):
    """``urlparse`` raises on a malformed authority, and activation calls it twice.

    Once to derive the artifact filename and once to pick the download scheme,
    the first of them before any staging guard opens. Rejecting here keeps a
    caller's typo a 422 instead of a server error.
    """
    with pytest.raises(ValidationError, match="parseable URI"):
        _pull(**{field: "http://[::1"})


def test_a_bare_filesystem_path_is_still_a_valid_locator():
    """The check is parseability, not a scheme allowlist: bare paths are supported."""
    assert _pull(artifact_uri="/models/model.onnx").artifact_uri == "/models/model.onnx"


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_a_non_finite_threshold_is_rejected(value):
    """A non-finite threshold publishes a metadata.json no strict reader accepts.

    ``json.dumps`` writes bare ``NaN``/``Infinity``, which is not JSON. The pull
    answers 201, and the serving stack that cannot read the sidecar falls back to
    a hardcoded default threshold without logging it -- a moved decision boundary
    on live traffic, reported to the caller as a success.
    """
    with pytest.raises(ValidationError, match="finite"):
        _pull(threshold=value)


def test_a_finite_threshold_is_still_accepted():
    assert _pull(threshold=0.42).threshold == 0.42


def test_a_cleanup_response_must_state_what_happened_to_the_directory():
    """``status`` must carry no default. It was ``Literal["removed"]`` defaulting
    to "removed", so a dry run and a refusal both reported the directory removed.
    """
    with pytest.raises(ValidationError, match="status"):
        CleanupResponse(version="1.4.0", disk_reclaimed=False)


def test_a_cleanup_response_can_say_the_directory_was_kept():
    """The value the wire had no way to express before this release."""
    kept = CleanupResponse(version="1.4.0", status="kept", disk_reclaimed=False)

    assert kept.model_dump() == {"version": "1.4.0", "status": "kept", "disk_reclaimed": False}
