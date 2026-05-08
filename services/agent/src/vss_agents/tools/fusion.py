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
"""NAT registration shim for the pure fusion engine."""

from collections.abc import AsyncGenerator
import logging

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import Field

from lib.fusion.algorithms import run_fusion
from lib.fusion.fusion_models import FiniteNonNegFloat
from lib.fusion.fusion_models import FusionInput
from lib.fusion.fusion_models import FusionOutput
from lib.fusion.fusion_models import _SharedFusionParams

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NAT tool registration
# ---------------------------------------------------------------------------


class FusionConfig(FunctionBaseConfig, _SharedFusionParams, name="fusion"):
    """YAML-configured defaults for the fusion tool.

    Any inherited field the caller did not explicitly set in the request
    falls through to the config value here (see :func:`_merge_config_defaults`).
    """

    # -- Fields set once upon service startup (not overridable per request) --
    space_weights_default: FiniteNonNegFloat = Field(
        default=1.0,
        description=(
            "Safety-net fallback weight used by the fusion NAT wrapper to fill in "
            "the input space_weights for any missing space."
            "Default 1.0 (neutral)."
        ),
    )

    # -- Fields overridable per request --
    # Included from ``_SharedFusionParams``


def _merge_config_defaults(inp: FusionInput, config: FusionConfig) -> FusionInput:
    """Overlay :class:`FusionConfig` defaults onto a :class:`FusionInput`.

    Approach: take the shared knobs from ``config`` as the base layer, then
    layer the caller's explicitly-set fields on top. Caller wins for any
    field they sent, everything else falls through to deployment defaults.

    Example: caller posts ``{"lists": [...], "rrf_k": 30}``.
    - ``rrf_k`` was set in the request -> stays 30 (caller wins).
    - ``method`` was not set -> falls through to ``config.method`` (e.g. "rrf").
    - all other knobs -> fall through to config defaults.
    """
    # Fast path: caller already set every shared knob -> nothing to overlay
    if _SharedFusionParams.model_fields.keys() <= inp.model_fields_set:
        return inp

    shared_defaults = {name: getattr(config, name) for name in _SharedFusionParams.model_fields}
    caller_set = inp.model_dump(exclude_unset=True)
    return FusionInput.model_validate({**shared_defaults, **caller_set})


@register_function(config_type=FusionConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def fusion(config: FusionConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """Register the fusion ranker as a NAT tool / FastAPI endpoint."""

    async def _fusion(inp: FusionInput) -> FusionOutput:
        """Fuse N ranked lists of 5s chunks. Pure ranker. No I/O, no searches.

        Overlay config defaults onto unset request fields/knobs.
        """
        merged_params = _merge_config_defaults(inp, config)

        # Fill up any missing weight for spaces (via new copy)
        weights = {**merged_params.space_weights}
        for rl in merged_params.lists:
            weights.setdefault(rl.space, config.space_weights_default)
        merged_params_with_weights = merged_params.model_copy(update={"space_weights": weights})

        logger.debug(
            "fusion: method=%s spaces=%s weights=%s",
            merged_params.method,
            [rl.space for rl in merged_params.lists],
            weights,
        )
        return run_fusion(merged_params_with_weights)

    yield FunctionInfo.create(
        single_fn=_fusion,
        description=_fusion.__doc__,
        input_schema=FusionInput,
        single_output_schema=FusionOutput,
    )
