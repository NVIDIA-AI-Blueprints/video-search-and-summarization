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
"""Apply the Search profile's prompt policy without rebuilding the released image."""

from nat.data_models.config import Config
from vss_agents.api.custom_fastapi_worker import CustomFastApiFrontEndWorker
from vss_agents.tools import search

TEMPORAL_GROUNDING = """Time filters must be grounded in the user's request.
If the user query does not request a time or date, omit timestamp_start and timestamp_end.
Never invent a time interval or copy timestamps from the examples into an untimed query.
When the user explicitly requests a time interval, extract that interval using the normal date conventions.
Untimed example: "person carrying a parcel" has time fields
{{"timestamp_start": null, "timestamp_end": null}}."""
_USER_QUERY = "User query: {user_query}"


class SearchPromptWorker(CustomFastApiFrontEndWorker):
    """Retain the standard worker and ground its search decomposition prompt."""

    def __init__(self, config: Config) -> None:
        # shortcut: the 3.3 image ignores agent_mode_prompt; remove when the image honors that setting.
        prompt = search.QUERY_DECOMPOSITION_PROMPT
        if TEMPORAL_GROUNDING not in prompt:
            if _USER_QUERY not in prompt:
                raise RuntimeError(
                    "Search prompt policy requires the VSS 3.3 query template"
                )
            search.QUERY_DECOMPOSITION_PROMPT = prompt.replace(
                _USER_QUERY, f"{TEMPORAL_GROUNDING}\n\n{_USER_QUERY}"
            )
        super().__init__(config)
