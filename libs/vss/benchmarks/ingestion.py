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

"""Validate the single-caption-backend boundary used by M8 workloads."""

from vss.ingestion import VlmChunkResponse


def caption_result(results: object, backend: str) -> list[VlmChunkResponse]:
    """Reject missing/extra backends and non-caption outputs before accounting."""
    if not isinstance(results, dict) or set(results) != {backend}:
        raise ValueError("caption_backend_result_identity")
    chunks = results[backend]
    if not isinstance(chunks, list) or any(
        not isinstance(chunk, VlmChunkResponse) for chunk in chunks
    ):
        raise ValueError("caption_backend_result_type")
    return chunks
