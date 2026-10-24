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
"""Unit tests for the idempotent NMP 26.03 workspace bootstrap helper."""

from unittest.mock import Mock

import pytest
from nemo_platform import APIStatusError

from src.backends.nmp.workspace import ensure_secret, ensure_workspace


def _api_error(status_code: int, message: str = "boom") -> APIStatusError:
    err = APIStatusError(message, response=Mock(), body=None)
    err.status_code = status_code
    err.message = message
    return err


def test_ensure_workspace_creates_with_exist_ok():
    """Happy path: calls workspaces.create with exist_ok and role propagation."""
    client = Mock()
    ensure_workspace(client, "dfwbp")
    client.workspaces.create.assert_called_once_with(
        name="dfwbp",
        exist_ok=True,
        description="DFW workspace: dfwbp",
        wait_role_propagation=True,
    )


@pytest.mark.parametrize(
    "status_code, message",
    [
        (409, "boom"),  # 409 is unambiguous already-exists, regardless of message
        (422, "Workspace already exists"),  # 422 only when it actually says so
        (422, "duplicate workspace name"),
    ],
)
def test_ensure_workspace_swallows_already_exists(status_code, message):
    """409, or a 422 that reports already-exists/duplicate, is idempotent (no raise)."""
    client = Mock()
    client.workspaces.create.side_effect = _api_error(status_code, message)
    ensure_workspace(client, "dfwbp")  # should not raise


@pytest.mark.parametrize(
    "status_code, message",
    [
        (422, "invalid workspace name"),  # a 422 that is NOT already-exists must surface
        (500, "boom"),
    ],
)
def test_ensure_workspace_propagates_other_api_errors(status_code, message):
    """Non-already-exists errors propagate — including a generic 422."""
    client = Mock()
    client.workspaces.create.side_effect = _api_error(status_code, message)
    with pytest.raises(APIStatusError):
        ensure_workspace(client, "dfwbp")


def test_ensure_secret_creates_and_returns_name():
    """Happy path: creates the secret and returns its name."""
    client = Mock()
    name = ensure_secret(client, "dfwbp", "dfw-judge-x", "the-key")
    assert name == "dfw-judge-x"
    client.secrets.create.assert_called_once_with(
        workspace="dfwbp",
        name="dfw-judge-x",
        data="the-key",
        description="DFW managed secret: dfw-judge-x",
    )
    client.secrets.update.assert_not_called()


def test_ensure_secret_updates_when_exists():
    """If the secret already exists (409), update it in place so a rotated key sticks."""
    client = Mock()
    client.secrets.create.side_effect = _api_error(409, "already exists")
    name = ensure_secret(client, "dfwbp", "dfw-judge-x", "new-key")
    assert name == "dfw-judge-x"
    client.secrets.update.assert_called_once_with(
        name="dfw-judge-x", workspace="dfwbp", data="new-key"
    )


def test_ensure_secret_propagates_other_errors():
    """A non-conflict error (e.g. 500) surfaces."""
    client = Mock()
    client.secrets.create.side_effect = _api_error(500, "boom")
    with pytest.raises(APIStatusError):
        ensure_secret(client, "dfwbp", "dfw-judge-x", "k")
