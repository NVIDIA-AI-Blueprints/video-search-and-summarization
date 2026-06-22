#!/usr/bin/env python3
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
Shared status constants for Alert Bridge HTTP responses.

Use these instead of hardcoded literals so the response envelope stays
consistent across alert / incident / realtime / validation handlers.
"""


class ResponseStatus:
    SUCCESS = "success"
    ERROR = "error"
    ACCEPTED = "accepted"


class ErrorCode:
    VALIDATION_FAILED = "validation_failed"
    NOT_FOUND = "not_found"
    INTERNAL_ERROR = "internal_error"
    INVALID_PAYLOAD = "invalid_payload"

    # Feature disabled by configuration
    PERSISTENCE_DISABLED = "persistence_disabled"

    # Service-specific upstream failures
    RTVI_VLM_UNAVAILABLE = "rtvi_vlm_unavailable"
    RTVI_STREAM_NOT_READABLE = "rtvi_stream_not_readable"
    RTVI_INVALID_RESPONSE = "rtvi_invalid_response"
    RTVI_STREAM_CONFLICT = "rtvi_stream_conflict"
    ELASTICSEARCH_UNAVAILABLE = "elasticsearch_unavailable"
    ELASTICSEARCH_QUERY_FAILED = "elasticsearch_query_failed"
    ELASTICSEARCH_WRITE_FAILED = "elasticsearch_write_failed"
