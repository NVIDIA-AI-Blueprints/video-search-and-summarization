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

"""Task handlers for the flywheel workflow, grouped per backend.

``nmp`` — LLM + embedding pipeline handlers; ``deft`` — AOI (DEFT-loop)
handlers. This facade re-exports every handler so existing
``from src.tasks.handlers import X`` importers keep working regardless of which
sub-package a handler lives in. ``BaseTaskHandler`` stays shared at
``src/tasks/base.py``.
"""

from src.tasks.handlers.deft import (
    AOIStageError,
    FinalizeAOIRunHandler,
    InitializeDEFTWorkflowHandler,
    RegisterCandidateHandler,
    RunDeftTrainingHandler,
    RunOfflineEvalGateHandler,
    SelectAndSnapshotHandler,
    StartPromotionHandler,
)
from src.tasks.handlers.nmp import (
    CreateDatasetsHandler,
    CreateDPODatasetsHandler,
    CreateEmbeddingDatasetsHandler,
    CustomizationHandler,
    EmbeddingCustomizationHandler,
    EmbeddingEvaluationHandler,
    EvaluationHandler,
    FinalizeFlywheelRunHandler,
    InitializeWorkflowHandler,
    PrepareCustomizedNIMHandler,
    ShutdownCustomizedNIMHandler,
    ShutdownDeploymentHandler,
    SpinUpCustomizedNIMHandler,
    SpinUpNIMHandler,
    WaitForLLMJudgeHandler,
)

__all__ = [
    "AOIStageError",
    "CreateDPODatasetsHandler",
    "CreateDatasetsHandler",
    "CreateEmbeddingDatasetsHandler",
    "CustomizationHandler",
    "EmbeddingCustomizationHandler",
    "EmbeddingEvaluationHandler",
    "EvaluationHandler",
    "FinalizeAOIRunHandler",
    "FinalizeFlywheelRunHandler",
    "InitializeDEFTWorkflowHandler",
    "InitializeWorkflowHandler",
    "PrepareCustomizedNIMHandler",
    "RegisterCandidateHandler",
    "RunDeftTrainingHandler",
    "RunOfflineEvalGateHandler",
    "SelectAndSnapshotHandler",
    "ShutdownCustomizedNIMHandler",
    "ShutdownDeploymentHandler",
    "SpinUpCustomizedNIMHandler",
    "SpinUpNIMHandler",
    "StartPromotionHandler",
    "WaitForLLMJudgeHandler",
]
