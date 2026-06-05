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

from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.transform.detection.proximity_detection import ProximityDetection


def test_cluster_empty_inputs():
    """Test clustering with empty inputs."""
    clusters, groups = ProximityDetection.cluster([], [], 1.0)
    assert clusters == []
    assert groups == []


def test_cluster_single_object():
    """Test clustering with a single object."""
    obj_coord = nvSchema.Coordinate(x=0.0, y=0.0)
    protected_objs = [(obj_coord, "obj1")]
    monitored_objs = []

    clusters, groups = ProximityDetection.cluster(protected_objs, monitored_objs, 1.0)

    assert len(clusters) == 0
    assert len(groups) == 0


def test_cluster_with_threshold():
    """Test clustering with objects within threshold distance."""
    obj1 = nvSchema.Coordinate(x=0.0, y=0.0)
    obj2 = nvSchema.Coordinate(x=0.5, y=0.5)  # Within threshold of 1.0
    obj3 = nvSchema.Coordinate(x=2.0, y=2.0)  # Outside threshold

    protected_objs = [(obj1, "obj1")]
    monitored_objs = [(obj2, "obj2"), (obj3, "obj3")]

    clusters, groups = ProximityDetection.cluster(protected_objs, monitored_objs, 1.0)

    assert len(clusters) == 1
    assert len(groups) == 1
    assert len(clusters[0].points) == 2  # obj1 and obj2 should be clustered
    assert "obj1" in groups[0]
    assert "obj2" in groups[0]
    assert "obj3" not in groups[0]


def test_cluster_duplicate_removal():
    """Test that duplicate clusters are removed."""
    obj1 = nvSchema.Coordinate(x=0.0, y=0.0)
    obj2 = nvSchema.Coordinate(x=0.5, y=0.5)

    protected_objs = [(obj1, "obj1"), (obj2, "obj2")]
    monitored_objs = [(obj1, "obj1"), (obj2, "obj2")]

    clusters, groups = ProximityDetection.cluster(protected_objs, monitored_objs, 1.0)

    # Should only have one unique cluster
    assert len(clusters) == 1
    assert len(groups) == 1
    assert len(clusters[0].points) == 2
    assert "obj1" in groups[0]
    assert "obj2" in groups[0]


def test_cluster_subset_removal():
    """Test that subset clusters are removed."""
    obj1 = nvSchema.Coordinate(x=0.0, y=0.2)
    obj2 = nvSchema.Coordinate(x=0.0, y=1)
    obj3 = nvSchema.Coordinate(x=0.0, y=1.8)

    protected_objs = [(obj1, "obj1"), (obj2, "obj2"), (obj3, "obj3")]
    monitored_objs = [(obj1, "obj1"), (obj2, "obj2"), (obj3, "obj3")]

    clusters, groups = ProximityDetection.cluster(protected_objs, monitored_objs, 1.0)

    # Should only keep the larger cluster
    print(groups)
    assert len(clusters) == 1
    assert len(groups) == 1
    assert len(clusters[0].points) == 3
    assert "obj2" == groups[0][0]
    assert "obj1" in groups[0]
    assert "obj3" in groups[0]
