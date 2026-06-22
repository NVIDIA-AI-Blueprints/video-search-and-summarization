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

from .base import AlertConfigStoreABC
from .cached_store import CachedAlertConfigStore
from .es_store import ALERT_CONFIG_COLLECTION, ESAlertConfigStore
from .factory import build_alert_config_store
from .hydration import hydrate_cache
from .normalize import normalize_alert_type
from .service import AlertConfigAlreadyExists, AlertConfigNotFound, AlertConfigService
from .store import AlertConfigStoreError, RedisAlertConfigStore

# Backward-compatibility alias so pre-rename callers keep working while the
# wire-up in the route layer migrates to the cached composite.
AlertConfigStore = RedisAlertConfigStore

__all__ = [
    "ALERT_CONFIG_COLLECTION",
    "AlertConfigAlreadyExists",
    "AlertConfigNotFound",
    "AlertConfigService",
    "AlertConfigStore",
    "AlertConfigStoreABC",
    "AlertConfigStoreError",
    "CachedAlertConfigStore",
    "ESAlertConfigStore",
    "RedisAlertConfigStore",
    "build_alert_config_store",
    "hydrate_cache",
    "normalize_alert_type",
]
