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
Schema definitions for real-time VLM alert domain.
"""

from .alert_config import (
    AlertRuleConfig,
    EXTENDED_OPTIONAL_FIELDS,
    STREAM_IDENTITY_OPTIONAL_FIELDS,
)
from .always_on_config import (
    AlwaysOnRuleEntry,
    AlwaysOnRuleParams,
    AlwaysOnRulesFile,
)

__all__ = [
    "AlertRuleConfig",
    "EXTENDED_OPTIONAL_FIELDS",
    "STREAM_IDENTITY_OPTIONAL_FIELDS",
    "AlwaysOnRuleEntry",
    "AlwaysOnRuleParams",
    "AlwaysOnRulesFile",
]
