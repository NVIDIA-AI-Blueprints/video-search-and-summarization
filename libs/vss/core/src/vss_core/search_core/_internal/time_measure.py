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
"""Re-export of the shared timing helper.

``TimeMeasure`` moved to ``vss_core._foundation`` when the critic and VLM paths
needed it too: it is a cross-cutting utility, and reaching into another
package's ``_internal`` for it would have been the first such breach. Existing
``search_core._internal.time_measure`` imports keep working through here.
"""

from vss_core._foundation.time_measure import LOG_PERF_LEVEL
from vss_core._foundation.time_measure import LOG_STATUS_LEVEL
from vss_core._foundation.time_measure import TimeMeasure
from vss_core._foundation.time_measure import collect_timings

__all__ = ["LOG_PERF_LEVEL", "LOG_STATUS_LEVEL", "TimeMeasure", "collect_timings"]
