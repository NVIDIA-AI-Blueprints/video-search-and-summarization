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
"""Startup check that the workflow's LLM has an endpoint a request can reach.

Every profile writes its LLM endpoint as ``base_url: ${LLM_BASE_URL}/v1`` with no
``:-`` fallback. When that variable arrives empty, nothing downstream objects: the
NAT YAML loader substitutes the empty string rather than failing, so ``base_url``
becomes the literal ``/v1``; ``NIMModelConfig.base_url`` is ``str | None`` with no
validation, so Pydantic accepts it; and the LangChain NVIDIA client only *warns*
about a URL with no scheme and no host. The agent then starts, passes its health
check, and fails on the first token with ``InvalidUrlClientError`` naming
``/v1/chat/completions`` -- which the top agent renders as ``Error:
/v1/chat/completions`` followed by the canned apology, with nothing pointing at
the variable that was missing.

Note that the client's ``https://integrate.api.nvidia.com/v1`` default only
applies when ``base_url`` is *omitted*. Setting it to ``/v1`` defeats it, so an
empty variable is strictly worse than an absent one.

Scope is deliberately narrow:

* The entry named by ``workflow.llm_name`` is fatal. It is the agent's single
  reasoning LLM, so an unusable endpoint there means no prompt can be answered
  and there is nothing to be gained by starting.
* Any other ``llms:`` entry only warns. ``llms.rtvi_vlm.base_url`` resolves to
  ``/v1`` on base, search, lvs and warehouse as they ship, because
  ``RTVI_VLM_BASE_URL`` is only defined in ``deploy/docker/services/rtvi/rtvi.env``
  -- an ``env_file:`` for the RT-VLM containers, not part of the agent's
  interpolation chain. Refusing on those would refuse every current profile.
* An entry that omits ``base_url`` is left alone: ``openai_vlm`` does that on
  purpose so the client uses its own endpoint.
"""

import logging

from nat.data_models.config import Config

logger = logging.getLogger(__name__)

_ABSOLUTE_PREFIXES = ("http://", "https://")

_VALID_SHAPES = (
    "an in-deployment LLM (for example 'http://vss-llm-nim:8000'), the gateway "
    "route ('${VSS_GATEWAY_ORIGIN}/llm'), or a hosted endpoint (for example "
    "'https://integrate.api.nvidia.com')"
)


class LLMEndpointConfigurationError(ValueError):
    """The workflow's LLM is configured with a base URL no request can reach."""


def _endpoint_problem(base_url: object) -> str | None:
    """Describe why ``base_url`` is unusable, or return ``None`` when it is fine."""

    # Omitted entirely: the client falls back to its own endpoint, which is a
    # deliberate configuration rather than a missing one.
    if base_url is None:
        return None
    if not isinstance(base_url, str) or not base_url.strip():
        return "it is empty"
    if not base_url.strip().lower().startswith(_ABSOLUTE_PREFIXES):
        return "it has no scheme and no host"
    return None


def validate_chat_llm_endpoints(config: Config) -> None:
    """Refuse to serve when the workflow's LLM endpoint is unusable; warn on the rest.

    Raises:
        LLMEndpointConfigurationError: naming the ``llms:`` entry, the value it
            resolved to, and the endpoint shapes that are valid.
    """

    workflow_llm = getattr(config.workflow, "llm_name", None)

    for name, llm_config in (config.llms or {}).items():
        base_url = getattr(llm_config, "base_url", None)
        problem = _endpoint_problem(base_url)
        if problem is None:
            continue

        if name == workflow_llm:
            raise LLMEndpointConfigurationError(
                f"The workflow's LLM 'llms.{name}' has base_url={base_url!r}, and {problem}. "
                "This is what an empty LLM_BASE_URL interpolates to, and it would send every "
                "chat completion to a bare '/v1/chat/completions' with no host: the agent would "
                "start, pass its health check, and fail on the first prompt. Set LLM_BASE_URL to "
                f"an absolute origin with no trailing '/v1' -- {_VALID_SHAPES}."
            )

        logger.warning(
            "LLM 'llms.%s' has base_url=%r, and %s. The workflow does not use it, so the agent "
            "will start, but anything that resolves this entry will fail on its first request. "
            "Set the endpoint variable this entry reads to an absolute origin -- %s.",
            name,
            base_url,
            problem,
            _VALID_SHAPES,
        )
