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

"""
Real-time VLM alert domain package.

Pure-Python service layer for managing real-time VLM (RTVI) alert rules,
orchestrating the always-on fan-out across camera lifecycle events, and
querying incidents from Elasticsearch. Has no FastAPI dependency, so it
can be invoked from REST routes, CLI, workers, agent flows, or tests
without going through HTTP.

Package structure:
    realtime/
    ├── config/          # Configuration and constants
    ├── schemas/         # Data models and typed configs
    └── services/        # Service classes
"""

from .config import ErrorCode, ResponseStatus, RuleStatus, load_config
from .schemas import (
    AlertRuleConfig,
    AlwaysOnRuleEntry,
    AlwaysOnRuleParams,
    AlwaysOnRulesFile,
)
from .services import (
    AlwaysOnReason,
    AlwaysOnResult,
    AlwaysOnRulesConfigError,
    AlwaysOnService,
    ESRuleStore,
    IncidentService,
    RealtimeAlertService,
    RTVIVLMClient,
    RuleStore,
)

__all__ = [
    "AlertRuleConfig",
    "AlwaysOnReason",
    "AlwaysOnResult",
    "AlwaysOnRuleEntry",
    "AlwaysOnRuleParams",
    "AlwaysOnRulesConfigError",
    "AlwaysOnRulesFile",
    "AlwaysOnService",
    "ESRuleStore",
    "ErrorCode",
    "IncidentService",
    "RTVIVLMClient",
    "RealtimeAlertService",
    "ResponseStatus",
    "RuleStatus",
    "RuleStore",
    "load_config",
]
