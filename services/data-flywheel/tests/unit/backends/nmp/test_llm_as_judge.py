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

"""Unit tests for ``LLMAsJudge.spin_up_llm_judge``.

These tests ensure the LLMAsJudge correctly orchestrates deployment of a *local*
LLM judge through the ``DMSClient``:

1. When the model is **not yet deployed** the LLMAsJudge should invoke
   ``deploy_model``.
2. When the model is **already deployed** ``deploy_model`` must *not* be
   called.
3. If ``deploy_model`` raises an exception the LLMAsJudge should propagate it
   unchanged so that the hosting process can fail fast.
"""

from unittest.mock import MagicMock

import pytest

from src.backends.nmp.llm_as_judge import LLMAsJudge, validate_llm_judge

# ---------------------------------------------------------------------------
# Helper patch builders
# ---------------------------------------------------------------------------


def _patch_llm_judge_config(monkeypatch):
    """Patch ``settings.system_config.llm_judge_config`` with a minimal stub that provides the
    ``get_local_nim_config`` helper used by ``spin_up_llm_judge``.
    """

    dummy_nim_cfg = MagicMock()
    dummy_nim_cfg.model_name = "stub-model"

    dummy_judge_cfg = MagicMock()
    dummy_judge_cfg.get_local_nim_config.return_value = dummy_nim_cfg
    dummy_judge_cfg.is_remote.return_value = False

    monkeypatch.setattr("src.config.settings.system_config.llm_judge_config", dummy_judge_cfg)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_spin_up_llm_judge_deploys_when_not_deployed(monkeypatch):
    """If the judge is *not* deployed, ``deploy_model`` should be triggered."""

    _patch_llm_judge_config(monkeypatch)

    # Build a dummy DMS client that reports *not* deployed
    dummy_client = MagicMock()
    dummy_client.is_deployed.return_value = False
    dummy_client.deploy_model = MagicMock()

    # Patch the DMSClient constructor inside the evaluator so every instantiation
    # returns our dummy instance
    monkeypatch.setattr("src.backends.nmp.llm_as_judge.DMSClient", lambda *_, **__: dummy_client)

    # Act
    result = LLMAsJudge().spin_up_llm_judge()

    # Assert
    dummy_client.is_deployed.assert_called_once()
    dummy_client.deploy_model.assert_called_once()
    assert result is True


def test_spin_up_llm_judge_skips_when_already_deployed(monkeypatch):
    """If the judge is already deployed the Evaluator must *not* redeploy."""

    _patch_llm_judge_config(monkeypatch)

    dummy_client = MagicMock()
    dummy_client.is_deployed.return_value = True
    dummy_client.deploy_model = MagicMock()

    monkeypatch.setattr("src.backends.nmp.llm_as_judge.DMSClient", lambda *_, **__: dummy_client)

    result = LLMAsJudge().spin_up_llm_judge()

    dummy_client.is_deployed.assert_called_once()
    dummy_client.deploy_model.assert_not_called()
    assert result is True


def test_spin_up_llm_judge_propagates_deployment_errors(monkeypatch):
    """Any exception raised by ``deploy_model`` must bubble up unchanged."""

    _patch_llm_judge_config(monkeypatch)

    dummy_client = MagicMock()
    dummy_client.is_deployed.return_value = False
    dummy_client.deploy_model.side_effect = RuntimeError("boom")

    monkeypatch.setattr("src.backends.nmp.llm_as_judge.DMSClient", lambda *_, **__: dummy_client)

    with pytest.raises(RuntimeError, match="boom"):
        LLMAsJudge().spin_up_llm_judge()

    dummy_client.is_deployed.assert_called_once()
    dummy_client.deploy_model.assert_called_once()


# ---------------------------------------------------------------------------
# Test cases for validate_llm_judge function
# ---------------------------------------------------------------------------


def test_validate_llm_judge_success(monkeypatch):
    """When LLM judge is available, function should complete successfully."""

    mock_llm_judge = MagicMock()
    mock_llm_judge.validate_llm_judge_availability.return_value = True

    monkeypatch.setattr(
        "src.backends.nmp.llm_as_judge.LLMAsJudge", MagicMock(return_value=mock_llm_judge)
    )

    # Should complete without raising exception
    validate_llm_judge()

    mock_llm_judge.validate_llm_judge_availability.assert_called_once()


def test_validate_llm_judge_failure_exits(monkeypatch):
    """When LLM judge is not available after 3 retries, function should exit with code 1."""

    mock_llm_judge = MagicMock()
    mock_llm_judge.validate_llm_judge_availability.return_value = False

    monkeypatch.setattr(
        "src.backends.nmp.llm_as_judge.LLMAsJudge", MagicMock(return_value=mock_llm_judge)
    )

    # Mock sys.exit to capture the exit call
    mock_exit = MagicMock()
    monkeypatch.setattr("sys.exit", mock_exit)

    # Mock time.sleep to avoid actual delays in tests
    mock_sleep = MagicMock()
    monkeypatch.setattr("src.backends.nmp.llm_as_judge.sleep", mock_sleep)

    # Should complete without raising exception

    validate_llm_judge()

    # Should be called 3 times due to retry logic
    assert mock_llm_judge.validate_llm_judge_availability.call_count == 3
    mock_exit.assert_called_once_with(1)
    # Should sleep twice (before 2nd and 3rd attempts)
    assert mock_sleep.call_count == 2
    # Verify sleep delays: 10s, then 20s
    mock_sleep.assert_any_call(10)
    mock_sleep.assert_any_call(20)


def test_validate_llm_judge_succeeds_on_retry(monkeypatch):
    """When LLM judge becomes available on retry, function should complete successfully."""

    mock_llm_judge = MagicMock()
    # First two calls return False, third call returns True
    mock_llm_judge.validate_llm_judge_availability.side_effect = [False, False, True]

    monkeypatch.setattr(
        "src.backends.nmp.llm_as_judge.LLMAsJudge", MagicMock(return_value=mock_llm_judge)
    )

    # Mock time.sleep to avoid actual delays in tests
    mock_sleep = MagicMock()
    monkeypatch.setattr("src.backends.nmp.llm_as_judge.sleep", mock_sleep)

    # Should complete without raising exception
    # Create mock configs
    validate_llm_judge()

    # Should be called 3 times (2 failures + 1 success)
    assert mock_llm_judge.validate_llm_judge_availability.call_count == 3
    # Should sleep twice (before 2nd and 3rd attempts)
    assert mock_sleep.call_count == 2


def test_validate_llm_judge_succeeds_on_first_attempt(monkeypatch):
    """When LLM judge is available on first attempt, function should complete without retries."""

    mock_llm_judge = MagicMock()
    mock_llm_judge.validate_llm_judge_availability.return_value = True

    monkeypatch.setattr(
        "src.backends.nmp.llm_as_judge.LLMAsJudge", MagicMock(return_value=mock_llm_judge)
    )

    # Mock time.sleep to verify it's not called
    mock_sleep = MagicMock()
    monkeypatch.setattr("src.backends.nmp.llm_as_judge.sleep", mock_sleep)

    # Should complete without raising exception
    # Create mock configs

    validate_llm_judge()

    # Should be called only once (no retries needed)
    mock_llm_judge.validate_llm_judge_availability.assert_called_once()
    # Should not sleep since success on first attempt
    mock_sleep.assert_not_called()


def test_validate_llm_judge_exception_handling(monkeypatch):
    """When LLM judge validation raises exceptions, function should retry and eventually exit."""

    mock_llm_judge = MagicMock()
    # First call raises exception, second and third return False
    mock_llm_judge.validate_llm_judge_availability.side_effect = [
        Exception("Connection error"),
        False,
        False,
    ]

    monkeypatch.setattr(
        "src.backends.nmp.llm_as_judge.LLMAsJudge", MagicMock(return_value=mock_llm_judge)
    )

    # Mock sys.exit to capture the exit call
    mock_exit = MagicMock()
    monkeypatch.setattr("sys.exit", mock_exit)

    # Mock time.sleep to avoid actual delays in tests
    mock_sleep = MagicMock()
    monkeypatch.setattr("src.backends.nmp.llm_as_judge.sleep", mock_sleep)

    validate_llm_judge()

    # Should be called 3 times despite exceptions
    assert mock_llm_judge.validate_llm_judge_availability.call_count == 3
    mock_exit.assert_called_once_with(1)
    # Should sleep twice (before 2nd and 3rd attempts)
    assert mock_sleep.call_count == 2
