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
import logging
from typing import Any

from nat.llm.nim_llm import NIMModelConfig
from nat.llm.openai_llm import OpenAIModelConfig
from pydantic import BaseModel
import pytest

from vss_agents.utils.llm_endpoint import LLMEndpointConfigurationError
from vss_agents.utils.llm_endpoint import validate_chat_llm_endpoints


class _Workflow(BaseModel):
    """Stands in for top_agent; llm_name is the only field that is read."""

    llm_name: str = "nim_llm"


class _Config(BaseModel):
    """Minimal stand-in for nat Config; only workflow and llms are read."""

    workflow: Any = None
    llms: dict[str, Any] = {}


def _config(base_url: str | None) -> _Config:
    return _Config(
        workflow=_Workflow(),
        llms={"nim_llm": NIMModelConfig(model_name="nvidia/nemotron", base_url=base_url)},
    )


def test_in_cluster_nim_passes() -> None:
    validate_chat_llm_endpoints(_config("http://vss-llm-nim:8000/v1"))


def test_gateway_route_passes() -> None:
    validate_chat_llm_endpoints(_config("http://vss-haproxy-ingress:7777/llm/v1"))


def test_hosted_endpoint_passes() -> None:
    validate_chat_llm_endpoints(_config("https://integrate.api.nvidia.com/v1"))


def test_omitted_base_url_passes() -> None:
    # `openai_vlm` omits base_url on purpose so the client uses its own endpoint.
    validate_chat_llm_endpoints(_config(None))


def test_empty_llm_base_url_resolves_to_relative_path_and_refuses() -> None:
    # `base_url: ${LLM_BASE_URL}/v1` with the variable empty.
    with pytest.raises(LLMEndpointConfigurationError) as excinfo:
        validate_chat_llm_endpoints(_config("/v1"))

    message = str(excinfo.value)
    assert "llms.nim_llm" in message
    assert "'/v1'" in message
    assert "LLM_BASE_URL" in message
    assert "${VSS_GATEWAY_ORIGIN}/llm" in message
    assert "https://integrate.api.nvidia.com" in message


def test_empty_string_base_url_refuses() -> None:
    with pytest.raises(LLMEndpointConfigurationError, match="it is empty"):
        validate_chat_llm_endpoints(_config("   "))


def test_scheme_less_host_refuses() -> None:
    with pytest.raises(LLMEndpointConfigurationError, match="no scheme and no host"):
        validate_chat_llm_endpoints(_config("vss-llm-nim:8000/v1"))


def test_non_workflow_entry_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    # rtvi_vlm resolves to /v1 on every shipping Compose profile because
    # RTVI_VLM_BASE_URL is not in the agent's interpolation chain. Refusing on it
    # would refuse to start every profile as it ships.
    config = _Config(
        workflow=_Workflow(llm_name="nim_llm"),
        llms={
            "nim_llm": NIMModelConfig(model_name="nvidia/nemotron", base_url="http://vss-llm-nim:8000/v1"),
            "rtvi_vlm": OpenAIModelConfig(model_name="cosmos", base_url="/v1"),
        },
    )
    with caplog.at_level(logging.WARNING):
        validate_chat_llm_endpoints(config)

    assert "llms.rtvi_vlm" in caplog.text
    assert "nim_llm" not in caplog.text


def test_healthy_config_warns_about_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        validate_chat_llm_endpoints(_config("http://vss-llm-nim:8000/v1"))

    assert caplog.text == ""
