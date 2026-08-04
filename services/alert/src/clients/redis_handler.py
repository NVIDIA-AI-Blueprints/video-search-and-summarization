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

"""Backward-compatible shim.

The dedup / filter / verdict-protection state that used to live in Redis
is now handled in-process (plus Elasticsearch for the one primitive that
must survive restarts). The implementation moved to
:mod:`clients.dedup_state`; ``RedisHandler`` is preserved here as an
alias so existing imports (``from clients.redis_handler import
RedisHandler``) keep working. New code should import
``DedupStateHandler`` directly.
"""

from clients.dedup_state import DedupStateHandler

# Backward-compatible alias.
RedisHandler = DedupStateHandler

__all__ = ["RedisHandler", "DedupStateHandler"]
