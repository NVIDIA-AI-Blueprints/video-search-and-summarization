# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for Brev's copy-rate-limit response."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock


def _load_brev_env():
    """Import the transport helper without needing Harbor for this unit test."""
    harbor = types.ModuleType("harbor")
    environments = types.ModuleType("harbor.environments")
    base = types.ModuleType("harbor.environments.base")
    base.BaseEnvironment = type("BaseEnvironment", (), {})
    base.ExecResult = type("ExecResult", (), {})
    path = Path(__file__).resolve().parents[1] / "envs/brev_env.py"
    spec = importlib.util.spec_from_file_location("brev_env_copy_backoff_test", path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {
        "harbor": harbor,
        "harbor.environments": environments,
        "harbor.environments.base": base,
    }):
        spec.loader.exec_module(module)
    return module


def test_resource_exhausted_is_retryable_but_auth_error_is_not():
    module = _load_brev_env()
    response = types.SimpleNamespace(return_code=1, stdout="", stderr="resource exhausted")
    assert module._transient_transport_failure(response)
    response.stderr = "RESOURCE_EXHAUSTED"
    assert module._transient_transport_failure(response)
    response.stderr = "Permission denied: resource exhausted"
    assert not module._transient_transport_failure(response)


def test_brev_copy_backs_off_then_succeeds_after_resource_exhausted():
    module = _load_brev_env()
    exhausted = types.SimpleNamespace(return_code=1, stdout="", stderr="resource exhausted")
    success = types.SimpleNamespace(return_code=0, stdout="", stderr="")
    with mock.patch.object(module, "_run_brev_copy_once", new_callable=mock.AsyncMock, side_effect=[exhausted, success]) as copy, \
         mock.patch.object(module, "_transport_backoff", new_callable=mock.AsyncMock) as backoff:
        result = asyncio.run(module._run_brev_copy("source", "worker:/target"))
    assert result is success
    assert copy.await_count == 2
    backoff.assert_awaited_once_with(0)
