# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
from mdx.analytics.core.utils.distance_util import euclidean_distance


class ProximityDetection:
    """
    A class for detecting proximity violations between objects in a scene.

    This class provides functionality to detect potential proximity hazards by analyzing
    the distance between center objects and surrounding objects. It uses clustering to identify
    groups of objects that are too close to each other, which could indicate a proximity violation.

    The detection is based on:
    1. Distance threshold between objects
    2. Clustering of objects that violate the threshold
    3. Removal of duplicate and subset clusters
    """

    @staticmethod
    def cluster(
        center_objs: list[tuple[nvSchema.Coordinate, str]], surrounding_objs: list[tuple[nvSchema.Coordinate, str]], threshold: float
    ) -> tuple[list[nvSchema.Cluster], list[list[str]]]:
        """
        Cluster objects based on their proximity to detect potential proximity violations.

        This method:
        1. Anchors clusters on center objects
        2. Checks distances between center objects and surrounding objects
        3. Creates clusters of surrounding objects that are closer than the threshold
        4. Removes duplicate and subset clusters while preserving order
        5. Returns both the clusters and their associated object IDs

        :param list[tuple[nvSchema.Coordinate, str]] center_objs: List of tuples containing (Point2D, object_id) for center objects
        :param list[tuple[nvSchema.Coordinate, str]] surrounding_objs: List of tuples containing (Point2D, object_id) for surrounding objects
        :param float threshold: The minimum distance threshold for proximity violations
        :return tuple[list[nvSchema.Cluster], list[list[str]]]: Tuple containing:
            - List of protobuf Cluster objects representing the proximity violation clusters
            - List of lists containing object IDs in each cluster

        Examples::
            >>> center = [(nvSchema.Coordinate(x=1, y=1), "obj1"), (nvSchema.Coordinate(x=2, y=2), "obj2")]
            >>> surrounding = [(nvSchema.Coordinate(x=1.1, y=1.1), "obj3"), (nvSchema.Coordinate(x=5, y=5), "obj4")]
            >>> clusters, object_groups = ProximityDetection.cluster(center, surrounding, threshold=0.5)
            >>> print(f"Found {len(clusters)} proximity violation clusters")
            >>> for cluster, group in zip(clusters, object_groups):
            ...     print(f"Cluster with objects: {group}")
        """
        clusters = []
        object_id_groups = []

        if not center_objs or not surrounding_objs:
            return [], []

        # Compute distances and check threshold
        for obj_coord, obj_id in center_objs:
            cluster = [nvSchema.Point2D(x=obj_coord.x, y=obj_coord.y)]
            object_ids = [obj_id]
            for surrounding_coord, surrounding_id in surrounding_objs:
                if surrounding_id == obj_id:
                    continue
                distance = euclidean_distance(surrounding_coord, obj_coord)
                if distance < threshold:
                    cluster.append(nvSchema.Point2D(x=surrounding_coord.x, y=surrounding_coord.y))
                    object_ids.append(surrounding_id)
            clusters.append(nvSchema.Cluster(points=cluster))
            object_id_groups.append(object_ids)

        # Remove duplicates and subsets while preserving original order
        unique_groups = []
        unique_clusters = []
        seen_sets = set()

        # Sort groups by size in descending order to process larger groups first
        sorted_indices = sorted(range(len(object_id_groups)), key=lambda i: len(object_id_groups[i]), reverse=True)

        for i in sorted_indices:
            group = object_id_groups[i]
            group_set = frozenset(group)

            # Skip if this group is a duplicate or subset of any existing group
            if group_set in seen_sets or any(group_set.issubset(existing_set) for existing_set in seen_sets):
                continue

            seen_sets.add(group_set)
            unique_groups.append(group)
            unique_clusters.append(clusters[i])

        return unique_clusters, unique_groups
