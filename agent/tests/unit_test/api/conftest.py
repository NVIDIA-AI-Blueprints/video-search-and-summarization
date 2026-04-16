# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""API unit-test guards and shared fixtures."""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_outbound_network(monkeypatch):
    """Fail fast if a unit test attempts a real network connection."""

    def _deny_network(*args, **kwargs):
        raise AssertionError("API unit tests must not depend on remote endpoints. Mock the network boundary instead.")

    monkeypatch.setattr(socket, "create_connection", _deny_network)
    monkeypatch.setattr(socket.socket, "connect", _deny_network, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", _deny_network, raising=True)
