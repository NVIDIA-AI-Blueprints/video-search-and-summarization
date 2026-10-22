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

"""Shared pytest fixtures and fakes for the Model Manager test suite."""

import pytest

from src.config import settings


@pytest.fixture(autouse=True)
def _fast_readiness(monkeypatch):
    """Make every poll instant so apply()/pull() tests never sleep.

    ``build_poll_timeout`` is shortened as well as zeroing the interval: a test
    that drives the build poll to exhaustion has to reach its deadline, and the
    production 600s budget would hang the suite rather than fail it.
    """
    monkeypatch.setattr(settings, "readiness_poll_interval", 0)
    monkeypatch.setattr(settings, "request_backoff_base", 0)
    monkeypatch.setattr(settings, "build_poll_interval", 0)
    monkeypatch.setattr(settings, "build_poll_timeout", 1.0)
    # The optional build step is OFF for every test that does not explicitly ask
    # for it, so a stray builder_url in the environment can never make the suite
    # reach for a builder that is not there.
    monkeypatch.setattr(settings, "builder_url", None)
