# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
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
