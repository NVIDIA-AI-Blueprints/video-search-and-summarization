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

"""vLLM plugin registration for Cosmos3 checkpoints."""

import logging

logger = logging.getLogger(__name__)


def register():
    from transformers import AutoConfig, AutoProcessor
    from vllm import ModelRegistry
    from vllm.v1.attention.backends.registry import (
        AttentionBackendEnum,
        register_backend,
    )

    from vllm_cosmos3.edge_config import Cosmos3EdgeConfig
    from vllm_cosmos3.edge_processor import Cosmos3EdgeProcessor

    # vLLM 0.17.1 predates the public Cosmos3-Edge config and processor.
    # Register the vendored upstream implementations before ModelConfig reads
    # the checkpoint's model_type.
    AutoConfig.register("cosmos3_edge", Cosmos3EdgeConfig, exist_ok=True)
    AutoProcessor.register(
        Cosmos3EdgeConfig,
        Cosmos3EdgeProcessor,
        exist_ok=True,
    )
    register_backend(
        AttentionBackendEnum.CUSTOM,
        "vllm_cosmos3.edge_attention_backend.EdgeFlashAttentionBackend",
    )

    registrations = {
        "Cosmos3ForConditionalGeneration": "vllm_cosmos3.model:Cosmos3ForConditionalGeneration",
        "Cosmos3EdgeForConditionalGeneration": (
            "vllm_cosmos3.edge_native:Cosmos3EdgeForConditionalGeneration"
        ),
    }
    supported_archs = ModelRegistry.get_supported_archs()
    for arch, model_cls in registrations.items():
        if arch not in supported_archs:
            logger.info("Registering architecture %s", arch)
            ModelRegistry.register_model(arch, model_cls)
