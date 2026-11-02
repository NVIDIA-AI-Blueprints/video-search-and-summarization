# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Routing-only extra_body for openai_compat subjects: it never changes the prompt, schema or settings."""

from __future__ import annotations

import pytest

from evalv2.adapters.base import AdapterConfigError
from evalv2.adapters.openai_compat import ENDPOINTS, with_routing
from evalv2.adapters.registry import build_adapter


class Subject:
    def __init__(self, request=None, endpoint="openrouter"):
        self.id, self.adapter, self.model, self.endpoint = "s", "openai_compat", "google/gemma-4-31b-it", endpoint
        self.request = request or {}


def test_routing_is_added_and_nothing_else_may_be():
    body = with_routing({"model": "m", "temperature": 0.0}, {"provider": {"order": ["Crusoe"]}})
    assert body == {"model": "m", "temperature": 0.0, "provider": {"order": ["Crusoe"]}}
    with pytest.raises(AdapterConfigError, match="routing keys"):
        with_routing({"model": "m"}, {"temperature": 1.0})
    with pytest.raises(AdapterConfigError, match="replace request keys"):
        with_routing({"model": "m", "provider": {}}, {"provider": {"order": ["x"]}})


def test_an_openrouter_subject_builds_with_its_routing_and_the_prepaid_endpoint():
    adapter = build_adapter(Subject({"extra_body": {"provider": {"order": ["Crusoe"], "allow_fallbacks": False}}}))
    assert adapter.endpoint is ENDPOINTS["openrouter"] and adapter.extra_body["provider"]["order"] == ["Crusoe"]
    with pytest.raises(ValueError, match="takes builder, prompt and extra_body"):
        build_adapter(Subject({"temperature": 1.0}))
    with pytest.raises(AdapterConfigError):
        build_adapter(Subject({"extra_body": {"response_format": {}}}))
