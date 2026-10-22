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

"""NMP (LLM + embedding) pipeline task handlers."""

from src.tasks.handlers.nmp.customization import (
    CustomizationHandler,
    EmbeddingCustomizationHandler,
    PrepareCustomizedNIMHandler,
)
from src.tasks.handlers.nmp.dataset import (
    CreateDatasetsHandler,
    CreateDPODatasetsHandler,
    CreateEmbeddingDatasetsHandler,
)
from src.tasks.handlers.nmp.deployment import (
    ShutdownCustomizedNIMHandler,
    ShutdownDeploymentHandler,
    SpinUpCustomizedNIMHandler,
    SpinUpNIMHandler,
    WaitForLLMJudgeHandler,
)
from src.tasks.handlers.nmp.evaluation import (
    EmbeddingEvaluationHandler,
    EvaluationHandler,
)
from src.tasks.handlers.nmp.finalization import FinalizeFlywheelRunHandler
from src.tasks.handlers.nmp.initialization import InitializeWorkflowHandler

__all__ = [
    "CreateDPODatasetsHandler",
    "CreateDatasetsHandler",
    "CreateEmbeddingDatasetsHandler",
    "CustomizationHandler",
    "EmbeddingCustomizationHandler",
    "EmbeddingEvaluationHandler",
    "EvaluationHandler",
    "FinalizeFlywheelRunHandler",
    "InitializeWorkflowHandler",
    "PrepareCustomizedNIMHandler",
    "ShutdownCustomizedNIMHandler",
    "ShutdownDeploymentHandler",
    "SpinUpCustomizedNIMHandler",
    "SpinUpNIMHandler",
    "WaitForLLMJudgeHandler",
]
