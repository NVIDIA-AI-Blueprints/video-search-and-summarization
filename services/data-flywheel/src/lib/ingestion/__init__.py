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

"""
Ingestion module for the Data Flywheel.

Key Components:
- IngestionPlugin: Abstract base class for plugins
- IngestionContext: Context passed to plugins
- IngestionRecord: Standardized record format
- IngestionResult: Result summary
- IngestionConfig: Configuration model (used for API input/output and storage)
- IngestionState: Watermark state model (state is managed by ingestion-plugins service)
- IngestionPluginClient: HTTP client for plugin container
"""

from src.lib.ingestion.models import (
    DEFAULT_INGESTION_START_DATE,
    INGESTION_WATERMARKS_COLLECTION,
    DatasetType,
    EmbeddingDatasetType,
    IngestionConfig,
    IngestionContext,
    IngestionPlugin,
    IngestionRecord,
    IngestionResult,
    IngestionState,
    LLMDatasetType,
)
from src.lib.ingestion.plugin_client import IngestionPluginClient, PluginClientError

__all__ = [
    "DEFAULT_INGESTION_START_DATE",
    # Constants
    "INGESTION_WATERMARKS_COLLECTION",
    "DatasetType",
    # Dataset type enums
    "EmbeddingDatasetType",
    # Configuration and state models
    "IngestionConfig",
    "IngestionContext",
    # Plugin contracts
    "IngestionPlugin",
    # Plugin client
    "IngestionPluginClient",
    "IngestionRecord",
    "IngestionResult",
    "IngestionState",
    "LLMDatasetType",
    "PluginClientError",
]
