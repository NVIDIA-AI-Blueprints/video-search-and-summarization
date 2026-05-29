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

"""Single source of truth for alert_type normalization.

All callers (Pydantic validators, Redis key generators, route handlers,
prompt loaders) must use this helper so the rule lives in exactly one
place. Changing it once propagates everywhere.
"""


def normalize_alert_type(value: str) -> str:
    """Lower-case, strip surrounding whitespace.

    Returns empty string for None / empty input rather than raising so
    callers can decide how to handle the missing case.
    """
    if not value:
        return ""
    return value.lower().strip()
