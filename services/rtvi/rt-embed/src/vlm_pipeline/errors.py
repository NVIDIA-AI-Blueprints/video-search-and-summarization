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
"""Shared pipeline error helpers."""

import torch

CUDA_OOM_STATUS_CODE = 503
CUDA_OOM_ERROR_PREFIX = "CUDA out of memory"


def is_cuda_oom_error(error: object) -> bool:
    """Return True when an exception/message represents a CUDA OOM."""
    oom_error_type = getattr(torch, "OutOfMemoryError", RuntimeError)
    return isinstance(error, oom_error_type) or CUDA_OOM_ERROR_PREFIX in str(error)


def format_cuda_oom_error(error: object, context: str) -> str:
    """Build an operator-facing CUDA OOM message.

    Bug 6138167: CUDA OOMs from frame copy/preprocess callbacks must become
    controlled pipeline errors so server-side Kafka/Redis error propagation sees
    the failure instead of a raw callback traceback.
    """
    detail = str(error)
    return (
        f"{CUDA_OOM_ERROR_PREFIX} while {context}. "
        "Reduce frame sampling rate, chunk duration, input resolution, or concurrent streams. "
        f"Details: {detail}"
    )
