# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for Brev's copy-rate-limit response."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock


def _load_brev_env():
    """Import the transport helper without needing Harbor for this unit test."""
    harbor = types.ModuleType("harbor")
    environments = types.ModuleType("harbor.environments")
    base = types.ModuleType("harbor.environments.base")
    base.BaseEnvironment = type("BaseEnvironment", (), {})
    base.ExecResult = lambda **kwargs: types.SimpleNamespace(**kwargs)
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


def test_ssh_port_is_discovered_once_from_brev_ports():
    module = _load_brev_env()
    response = types.SimpleNamespace(
        return_code=0, stderr="",
        stdout='[{"destination_port":22,"public_port":14872,"endpoint":"global.prd.ga.run.brev.nvidia.com:14872"}]',
    )
    with mock.patch.object(module, "_run_brev", new_callable=mock.AsyncMock, return_value=response) as brev:
        assert asyncio.run(module._get_brev_ssh_endpoint("vss-eval-rtx-2g-4")) == (
            "global.prd.ga.run.brev.nvidia.com", 14872,
        )
        assert asyncio.run(module._get_brev_ssh_endpoint("vss-eval-rtx-2g-4")) == (
            "global.prd.ga.run.brev.nvidia.com", 14872,
        )
    brev.assert_awaited_once_with("ports", "ls", "vss-eval-rtx-2g-4", "--json", timeout=30)


def test_invalid_ssh_endpoint_is_rejected():
    module = _load_brev_env()
    response = types.SimpleNamespace(
        return_code=0, stderr="",
        stdout='[{"destination_port":22,"public_port":14872,"endpoint":"evil.example:14873"}]',
    )
    with mock.patch.object(module, "_run_brev", new_callable=mock.AsyncMock, return_value=response):
        try:
            asyncio.run(module._get_brev_ssh_endpoint("vss-eval-rtx-2g-4"))
        except RuntimeError as exc:
            assert "Invalid SSH endpoint" in str(exc)
        else:
            assert False, "mismatched public port was accepted"


def test_managed_copy_uses_rsync_and_discovered_port():
    module = _load_brev_env()
    calls = []

    async def launch(*cmd, **kwargs):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0)

    async def communicate(proc, **kwargs):
        return b"", b""

    with tempfile.TemporaryDirectory() as td:
        key = Path(td) / ".brev" / "brev.pem"
        key.parent.mkdir()
        key.write_text("test key")
        with mock.patch.object(module, "_is_registered_node", new_callable=mock.AsyncMock, return_value=False), \
             mock.patch.object(module, "_get_brev_ssh_endpoint", new_callable=mock.AsyncMock, return_value=("global.prd.ga.run.brev.nvidia.com", 14872)), \
             mock.patch.object(module.Path, "home", return_value=Path(td)), \
             mock.patch.object(module.asyncio, "create_subprocess_exec", side_effect=launch), \
             mock.patch.object(module, "_communicate_with_cancellation_cleanup", side_effect=communicate), \
             mock.patch.object(module, "_register_transport_process"):
            result = asyncio.run(module._run_brev_copy_once("/tmp/archive.tar.gz", "vss-eval-rtx-2g-4:/tmp/archive.tar.gz"))
    assert result.return_code == 0
    assert calls[0][0] == "rsync"
    assert "-p 14872" in calls[0][calls[0].index("-e") + 1]
    assert calls[0][-1] == "ubuntu@global.prd.ga.run.brev.nvidia.com:/tmp/archive.tar.gz"


def test_legacy_copy_requires_explicit_switch():
    module = _load_brev_env()
    success = types.SimpleNamespace(return_code=0, stdout="", stderr="")
    with mock.patch.dict(module.os.environ, {"BREV_COPY_TRANSPORT": "legacy"}), \
         mock.patch.object(module, "_is_registered_node", new_callable=mock.AsyncMock, return_value=False), \
         mock.patch.object(module, "_run_brev_copy_legacy_once", new_callable=mock.AsyncMock, return_value=success) as legacy:
        assert asyncio.run(module._run_brev_copy_once("src", "instance:/dst")) is success
    legacy.assert_awaited_once()
