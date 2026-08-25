# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Effective static memory policy tests."""

from __future__ import annotations

import pytest

from vss_cli.config import Deployment
from vss_cli.config import MemoryConfig
from vss_cli.memory_policy import effective_persist


@pytest.mark.parametrize(
    ("memory_config", "no_persist", "expected"),
    [
        (None, False, False),
        (MemoryConfig(enabled=False, persist_by_default=False), False, False),
        (MemoryConfig(enabled=True, persist_by_default=False), False, False),
        (MemoryConfig(), False, True),
        (MemoryConfig(), True, False),
    ],
)
def test_effective_persist(
    memory_config: MemoryConfig | None,
    no_persist: bool,
    expected: bool,
) -> None:
    deployment = Deployment(base_url="http://h", memory=memory_config)
    assert effective_persist(deployment, no_persist=no_persist) is expected


def test_no_deployment_never_persists() -> None:
    assert effective_persist(None, no_persist=False) is False
