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
"""NMP alias for the neutral retry helper.

The implementation lives in ``src/lib/retry.py`` (backend-neutral, no
``nemo_platform`` import) so generic code and the DEFT backend share the same
backoff policy without pulling in the NMP SDK. This module re-exports the names
for existing NMP importers.
"""

from src.lib.retry import (
    API_MAX_RETRIES,
    API_RETRY_BACKOFF_FACTOR,
    is_transient_error,
    retry_on_transient_error,
)

__all__ = [
    "API_MAX_RETRIES",
    "API_RETRY_BACKOFF_FACTOR",
    "is_transient_error",
    "retry_on_transient_error",
]
