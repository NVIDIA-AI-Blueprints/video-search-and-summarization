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

"""Online tracklet-based cluster manager.

Public API::

    from cluster_manager import ClusterManager, ClusterParams

The core pipeline steps live in dedicated modules and can be imported
individually for testing or custom orchestration:

    - :mod:`cluster_manager.splitting`         — tracklet splitting
    - :mod:`cluster_manager.representatives`   — representative extraction
    - :mod:`cluster_manager.coexistence`       — coexistence conflict detection
    - :mod:`cluster_manager.affinity`          — affinity matrix
    - :mod:`cluster_manager.clustering`        — constrained clustering
    - :mod:`cluster_manager.archive`           — archive update
"""

from cluster_manager.online_manager import ClusterManager, DEFAULT_STATE_PATH
from cluster_manager.types import (
    Archive,
    BatchResult,
    BatchStats,
    ClusterGroup,
    ClusterInfo,
    ClusterNode,
    ClusterParams,
    EmbeddingRecord,
    MemberHistoryEntry,
    SubTracklet,
)

__all__ = [
    "Archive",
    "BatchResult",
    "BatchStats",
    "ClusterGroup",
    "ClusterInfo",
    "ClusterManager",
    "ClusterNode",
    "ClusterParams",
    "DEFAULT_STATE_PATH",
    "EmbeddingRecord",
    "MemberHistoryEntry",
    "SubTracklet",
]
