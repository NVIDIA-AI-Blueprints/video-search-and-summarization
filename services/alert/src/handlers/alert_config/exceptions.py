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

"""Exceptions for the alert-config store layer."""


class AlertConfigStoreError(Exception):
    """Raised when a store backend rejects a request for infrastructure
    reasons (as opposed to an existence conflict).

    Distinct from existence conflicts so callers can map infrastructure
    failures to 5xx responses instead of 4xx. The Elasticsearch-backed
    store raises ``persistence.exceptions.PersistenceError`` instead; this
    type is retained for the store contract and for callers that catch it.
    """
