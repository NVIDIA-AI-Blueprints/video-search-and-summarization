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

"""Exceptions raised by VSS ingestion."""


class IngestionError(Exception):
    """Base exception for ingestion failures."""


class IngestionBackendError(IngestionError):
    """An ingestion backend could not complete a request."""


class VLMBackendError(IngestionBackendError):
    """A frame-level VLM backend could not complete inference."""


class IngestionQueueFullError(IngestionError):
    """The bounded ingestion queue cannot accept more work."""


class RTVIBackendError(IngestionBackendError):
    """The RTVI backend could not complete ingestion."""


class RTEmbedBackendError(IngestionBackendError):
    """The RTVI-Embed backend could not complete ingestion."""


class RTVICvBackendError(IngestionBackendError):
    """The RTVI-CV backend could not complete ingestion."""
