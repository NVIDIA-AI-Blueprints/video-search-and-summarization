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

import logging

import numpy as np

from mdx.analytics.core.utils.util import cosine_similarity, dot_product, normalize

logger = logging.getLogger(__name__)


class Model:
    """
    Model class to hold the clustering result and provide methods to work with the clusters.
    This class maintains cluster centers and provides functionality for cluster prediction,
    filtering, and distance calculations.

    The Model class is used to store and manipulate the results of the Chinese Restaurant Process
    clustering. It provides methods for:
    - Filtering clusters by size
    - Computing distances between clusters
    - Predicting cluster assignments for new vectors
    - Finding neighboring clusters

    :ivar list[np.ndarray] centersA: List of cluster centers (unnormalized vectors).
    :ivar list[int] center_idx: List of indices indicating the size of each cluster.
    :ivar list[np.ndarray] centers: List of normalized cluster centers (automatically computed).

    Examples::
        >>> centers = [np.array([1.0, 2.0]), np.array([3.0, 4.0])]
        >>> sizes = [5, 3]
        >>> model = Model(centers, sizes)
        >>> print(f"Created model with {len(model.centers)} clusters")
    """

    def __init__(self, centers: list[np.ndarray] | None = None, center_idx: list[int] | None = None) -> None:
        """
        Initialize the Model instance with cluster centers and their sizes.

        :param list[np.ndarray] centers: List of cluster centers (unnormalized vectors).
        :param list[int] center_idx: List of indices indicating the size of each cluster.
        :return: None

        Examples::
            >>> centers = [np.array([1.0, 2.0]), np.array([3.0, 4.0])]
            >>> sizes = [5, 3]
            >>> model = Model(centers, sizes)
            >>> print(f"Initialized model with {len(model.centers)} clusters")
        """
        self.centersA = centers if centers is not None else []
        self.center_idx = center_idx if center_idx is not None else []
        self.centers = [normalize(center) for center in self.centersA]

    def filter(self, threshold: int = 1) -> list[tuple[np.ndarray, int]]:
        """
        Filter clusters by size, removing clusters that are smaller than the threshold.
        This method is useful for removing noise or insignificant clusters from the model.

        :param int threshold: Minimum size of clusters to keep. Clusters with fewer elements
                            than this threshold will be excluded.
        :return list[tuple[np.ndarray, int]]: List of filtered clusters with their sizes as (center, size) tuples.

        Examples::
            >>> model = Model(centers, sizes)
            >>> # Keep only clusters with at least 3 elements
            >>> filtered_centers = model.filter(threshold=3)
            >>> # Get all non-empty clusters
            >>> filtered_centers = model.filter(threshold=1)
        """
        return [
            (self.centers[i], self.center_idx[i])
            for i in range(len(self.center_idx))
            if self.center_idx[i] >= threshold
        ]

    def distance(self, threshold: int = 1) -> np.ndarray:
        """
        Compute distance matrix for filtered clusters using dot product similarity.
        The matrix shows the similarity between each pair of cluster centers.

        :param int threshold: Minimum size of clusters to consider in the distance matrix.
        :return np.ndarray: Distance matrix where each element [i,j] represents the similarity
                between cluster i and cluster j.

        Examples::
            >>> model = Model(centers, sizes)
            >>> # Get distance matrix for all clusters
            >>> distance_matrix = model.distance()
            >>> # Get distance matrix only for clusters with at least 5 elements
            >>> distance_matrix = model.distance(threshold=5)
        """
        filtered_centers = [center for center, _ in self.filter(threshold)]
        return np.array([[dot_product(v, w) for w in filtered_centers] for v in filtered_centers])

    def predict(self, v: np.ndarray) -> int:
        """
        Predict the most similar cluster index for a given vector.
        Uses dot product similarity to find the closest cluster center.

        :param np.ndarray v: Vector to predict the cluster for. Should be normalized
                           for best results.
        :return int: Index of the predicted cluster (the most similar cluster center).

        Examples::
            >>> model = Model(centers, sizes)
            >>> vector = normalize(np.array([1.0, 2.0, 3.0]))
            >>> cluster_idx = model.predict(vector)
            >>> print(f"Vector assigned to cluster {cluster_idx}")
        """
        max_sim = 0.0
        max_idx = 0
        for i in range(len(self.centers)):
            sim = dot_product(self.centers[i], v)
            if max_sim < sim:
                max_sim = sim
                max_idx = i
        return max_idx

    def predict_next(self, v: np.ndarray) -> int:
        """
        Predict the second most similar cluster index for a given vector.
        Useful when you want to find alternative cluster assignments.

        :param np.ndarray v: Vector to predict the next closest cluster for.
                           Should be normalized for best results.
        :return int: Index of the second most similar cluster.

        Examples::
            >>> model = Model(centers, sizes)
            >>> vector = normalize(np.array([1.0, 2.0, 3.0]))
            >>> next_cluster_idx = model.predict_next(vector)
            >>> print(f"Second most similar cluster is {next_cluster_idx}")
        """
        max_sim = 0.0
        max_idx = 0
        next_sim = 0.0
        next_idx = 0
        for i in range(len(self.centers)):
            sim = dot_product(self.centers[i], v)
            if max_sim < sim:
                if next_sim < max_sim:
                    next_sim = max_sim
                    next_idx = max_idx
                max_sim = sim
                max_idx = i
            elif next_sim < sim:
                next_sim = sim
                next_idx = i
        return next_idx

    def predict_neighbour(self, v: np.ndarray, threshold: float = 0.4) -> list[int]:
        """
        Predict neighboring clusters for a given vector based on a similarity threshold.
        Returns all clusters that have similarity above the threshold, sorted by similarity.

        :param np.ndarray v: Vector to predict the neighboring clusters for.
                           Should be normalized for best results.
        :param float threshold: Similarity threshold (0.0 to 1.0) for considering
                              neighboring clusters. Higher values mean stricter matching.
        :return list[int]: List of indices of neighboring clusters, sorted by similarity.

        Examples::
            >>> model = Model(centers, sizes)
            >>> vector = normalize(np.array([1.0, 2.0, 3.0]))
            >>> # Find all similar clusters with similarity > 0.6
            >>> similar_clusters = model.predict_neighbour(vector, threshold=0.6)
            >>> # Find only very similar clusters
            >>> similar_clusters = model.predict_neighbour(vector, threshold=0.8)
        """
        nearest_cc = sorted(
            [
                (i, dot_product(center, v))
                for i, center in enumerate(self.centers)
                if dot_product(center, v) >= threshold
            ],
            key=lambda x: -x[1],
        )
        return [idx for idx, _ in nearest_cc] or [self.predict(v)]


class CRP:
    """
    Chinese Restaurant Process (CRP) clustering implementation.
    CRP is a non-parametric clustering method that can automatically determine
    the number of clusters. It's particularly useful for:
    - Streaming data where the number of clusters is unknown
    - Data where clusters may grow or merge over time
    - Applications requiring online clustering

    The process works by assigning each new data point to either:
    1. An existing cluster (if similar enough)
    2. A new cluster (if different enough from existing clusters)

    Examples::
        >>> crp = CRP()
        >>> # Cluster a batch of vectors
        >>> model = crp.cluster(vectors, pnew=0.9)
        >>> # Update the model with new vectors
        >>> updated_model = crp.update_model(model, new_vectors)
    """

    def cluster(self, vecs: list[list[float]] | None, pnew: float = 0.9) -> Model:
        """
        Cluster the given vectors using the Chinese Restaurant Process.
        Each vector is assigned to either an existing cluster or a new cluster
        based on similarity to existing cluster centers.

        :param list[list[float]] vecs: List of vectors to cluster. Each vector
                                     should be a list of floats.
        :param float pnew: Probability threshold (0.0 to 1.0) for creating a new cluster.
                         Higher values (e.g., 0.9) make it easier to create new clusters,
                         while lower values (e.g., 0.5) make it harder.
        :return Model: Model containing the clustering result with cluster centers and sizes.

        Examples::
            >>> crp = CRP()
            >>> # Cluster a batch of vectors with default settings
            >>> crp_model = crp.cluster(embeddings)
            >>> # Cluster with stricter similarity requirements
            >>> crp_model = crp.cluster(embeddings, pnew=0.7)
        """
        if not vecs or not vecs[0]:
            return Model([], [])
        centers = []
        center_ids = []
        n_cluster = 1
        N = len(vecs)

        centers.append(np.array(vecs[0]))
        center_ids.append([0])

        for i in range(1, N):
            max_sim = -np.inf
            max_idx = 0
            v = np.array(vecs[i])
            for j in range(n_cluster):
                sim = cosine_similarity(v, centers[j])
                if sim > max_sim:
                    max_idx = j
                    max_sim = sim

            if max_sim < pnew:
                centers.append(v)
                center_ids.append([i])
                n_cluster += 1
            else:
                centers[max_idx] = np.add(centers[max_idx], v)
                center_ids[max_idx].append(i)
        logger.debug(f"Clustered {len(centers)} center(s) from {len(vecs)} vectors")
        return Model(centers, [len(ids) for ids in center_ids])

    def update_model(self, model: Model, new_vecs: list[list[float]] | None, pnew: float = 0.9) -> Model:
        """
        Update an existing model with new vectors using the Chinese Restaurant Process.
        This method allows for incremental clustering, where new data points can be
        added to an existing clustering model without recomputing everything.

        :param Model model: The existing model to update. This model's clusters will
                          be used as the starting point.
        :param list[list[float]] new_vecs: List of new vectors to add to the model.
                                         Each vector should be a list of floats.
        :param float pnew: Probability threshold (0.0 to 1.0) for creating new clusters
                         when processing new vectors. Higher values make it easier to
                         create new clusters.
        :return Model: Updated Model containing the new clustering result, incorporating
                both old and new vectors.

        Examples::
            >>> crp = CRP()
            >>> # Update an existing model with new vectors
            >>> updated_model = crp.update_model(existing_model, new_embeddings)
            >>> # Update with stricter similarity requirements
            >>> updated_model = crp.update_model(existing_model, new_embeddings, pnew=0.7)
        """
        if not new_vecs or not new_vecs[0]:
            return model
        centers = model.centersA  # Use the original (unnormalized) centers for updating
        center_ids = [[i] for i in model.center_idx]  # Recreate center indices
        n_cluster = len(centers)

        for vec in new_vecs:
            max_sim = -np.inf
            max_idx = 0
            v = np.array(vec)
            for j in range(n_cluster):
                sim = cosine_similarity(v, centers[j])
                if sim > max_sim:
                    max_idx = j
                    max_sim = sim

            if max_sim < pnew:
                centers.append(v)
                center_ids.append([len(center_ids)])
                n_cluster += 1
            else:
                centers[max_idx] = np.add(centers[max_idx], v)
                center_ids[max_idx].append(len(center_ids))

        # Update the model's centers and cluster sizes
        updated_model = Model(centers, [len(ids) for ids in center_ids])
        logger.debug(
            f"Got new {len(centers)} center(s) from old {n_cluster} center(s) and new {len(new_vecs)} vectors"
        )
        return updated_model
