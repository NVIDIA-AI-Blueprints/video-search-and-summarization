# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Ask an OpenAI-compatible VLM (RT-VLM, vLLM, Cosmos Reason NIM, plain OpenAI) about a video."""

from .client import analyze
from .client import list_models
from .models import Endpoint
from .models import ModelsResponse
from .models import VlmBackend
from .models import VlmRequest
from .models import VlmResponse

__all__ = [
    "Endpoint",
    "ModelsResponse",
    "VlmBackend",
    "VlmRequest",
    "VlmResponse",
    "analyze",
    "list_models",
]
