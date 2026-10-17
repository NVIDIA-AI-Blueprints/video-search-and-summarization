# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Harness handoff failures must not be classified from quoted agent output."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def codex(monkeypatch):
    # Only exercise our adapter boundary; CI's contract suite needs no Harbor
    # installation. Delegation to the unchanged upstream classifier is explicit.
    base = ModuleType('harbor.agents.installed.base')
    class NonZeroAgentExitCodeError(Exception):
        pass
    base.NonZeroAgentExitCodeError = NonZeroAgentExitCodeError
    upstream = ModuleType('harbor.agents.installed.codex')
    class Codex:
        _classify_exec_error = Mock()
    upstream.Codex = Codex
    monkeypatch.setitem(sys.modules, base.__name__, base)
    monkeypatch.setitem(sys.modules, upstream.__name__, upstream)
    spec = importlib.util.spec_from_file_location(
        'nv_codex_classification_test', Path(__file__).resolve().parents[1] / 'agents/nv_codex.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, Codex, NonZeroAgentExitCodeError


@pytest.mark.parametrize('transcript', [
    'Read config: rate_limit: 5. Agent completed; NIM uses 42940 MiB.',
    'Tool docs: HTTP 429 Too Many Requests. Agent completed; NIM uses 42,940 MiB.',
])
def test_completed_coding_turn_keeps_real_handoff_failure(codex, transcript):
    module, upstream, error_class = codex
    result = SimpleNamespace(return_code=1, stdout=transcript,
                             stderr='NemoClaw setup handoff failed: sandbox_phase failed')
    error = object.__new__(module.NvCodex)._classify_exec_error('codex exec', result)
    assert type(error) is error_class
    assert result.stderr in str(error)
    assert transcript in str(error)
    upstream._classify_exec_error.assert_not_called()


def test_handoff_exception_retains_bounded_coding_blocker_without_reclassification(codex):
    module, upstream, error_class = codex
    final = 'FINAL: RT-CV CUDA out of memory; evaluation NIM reserves 42,940 MiB. Sandbox not onboarded.'
    transcript = 'old-prompt-must-not-be-repeated' + ('old tool output\n' * 1000) + 'rate_limit: 5\n' + final
    stderr = 'NemoClaw setup handoff failed: sandbox_phase failed'
    result = SimpleNamespace(return_code=1, stdout=transcript, stderr=stderr)
    error = object.__new__(module.NvCodex)._classify_exec_error('codex exec', result)
    assert type(error) is error_class
    assert final in str(error)
    assert 'old-prompt-must-not-be-repeated' not in str(error)
    assert len(str(error)) < len(stderr) + 6100
    upstream._classify_exec_error.assert_not_called()


@pytest.mark.parametrize('stdout,stderr', [
    ('', 'HTTP 429 Too Many Requests; rate limit exceeded'),
    ('rate_limit_exceeded', ''),
    ('NIM uses 42940 MiB', 'command failed'),
])
def test_real_agent_errors_use_unmodified_upstream_classification(codex, stdout, stderr):
    module, upstream, _ = codex
    expected = RuntimeError('upstream classification')
    upstream._classify_exec_error.return_value = expected
    result = SimpleNamespace(return_code=1, stdout=stdout, stderr=stderr)
    error = object.__new__(module.NvCodex)._classify_exec_error('codex exec', result)
    assert error is expected
    upstream._classify_exec_error.assert_called_once_with('codex exec', result)
