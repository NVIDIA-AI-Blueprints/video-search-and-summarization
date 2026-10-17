#!/usr/bin/env python3
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

"""
Sample embeddings from a dumped CSV.

This script performs the following:
1. Reads cluster data from a CSV file
2. Uses FAISS + NumPy to:
   - Remove extreme outliers (distance to centroid)
   - Cosine prune @ 0.999-0.9995
   - FPS (Farthest Point Sampling) with K=(max(3, min(max_k, 4th_root(N))))
3. Writes the sampled embeddings to CSV

Usage:
    python sample_and_extract_patches.py \
        --embeddings embeddings.csv \
        --output-dir sampled/
        --object-id 25
"""

import argparse
import csv
import logging
import numpy as np
import faiss
import os
from typing import Dict, List, Tuple, Optional
import sys

# Running this file directly puts cluster_sampling/ on sys.path rather than the
# source root (src/), so ``config`` would not resolve; main.py-driven imports
# already have it. Adding it here keeps both entry points working.
_src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

from config import COMPRESSION_MERGE_MAX_DISTANCE  # noqa: E402
from cluster_stats.moments import combine_cluster_moments, moments_from_vectors  # noqa: E402

logger = logging.getLogger(__name__)

# Largest squared L2 distance between two unit vectors: ||a - b||^2 = 2 - 2cos,
# maximized at cos = -1 (antipodal). A merge threshold at or above this admits
# every pair by definition, which makes it an exact off switch rather than a
# magic sentinel -- see _gated_representative_groups.
MAX_UNIT_SQUARED_L2 = 4.0


def check_if_normalized(vectors: np.ndarray, tolerance: float = 1e-5) -> Tuple[bool, np.ndarray]:
    """
    Check if vectors are already L2-normalized

    Args:
        vectors: NxD array of vectors
        tolerance: Tolerance for checking if norm equals 1.0

    Returns:
        Tuple of (is_normalized, norms)
    """
    norms = np.linalg.norm(vectors, axis=1)
    is_normalized = np.allclose(norms, 1.0, atol=tolerance)
    return is_normalized, norms


def _normalize_vectors(vectors: np.ndarray) -> np.ndarray:
    """Return L2-normalized vectors as float32."""
    is_normalized, norms = check_if_normalized(vectors)
    if is_normalized:
        return vectors.astype(np.float32, copy=False)
    return (vectors / (norms.reshape(-1, 1) + 1e-8)).astype(np.float32)


def _assign_to_representatives(representatives: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    """Index of the nearest representative for every row of ``vectors``."""
    index = faiss.IndexFlatL2(representatives.shape[1])
    index.add(np.ascontiguousarray(representatives, dtype=np.float32))
    _, nearest = index.search(np.ascontiguousarray(vectors, dtype=np.float32), 1)
    return nearest.flatten()


def _gated_representative_groups(
    normalized: np.ndarray, seed_indices: np.ndarray, weights: np.ndarray, max_distance: float
) -> Tuple[np.ndarray, np.ndarray]:
    """Group representatives around ``seed_indices`` without merging across
    ``max_distance``.

    Folding one representative into another is only meaningful while the two
    describe the same appearance; past some distance the merged summary
    describes neither, and compression has already deleted the raw vectors
    that would be needed to undo it. So a representative joins a group only
    when its *mean* distance to the group's current members is within
    ``max_distance`` (squared L2, the scale Milvus reports).

    Averaging over the members, rather than testing only the group's seed, is
    what keeps a representative out of a group it sits at the edge of: the
    seed-only test admits anything close to the seed, which lets two
    representatives 2x ``max_distance`` apart meet inside one group. Averaging
    rather than taking the worst member in turn lets one object's gradual
    appearance drift consolidate -- a single identity's embeddings routinely
    span more than ``max_distance`` end to end, so requiring every member to
    be within the gate would fragment it. What the average still refuses is a
    representative far from the bulk of the group, which is the case that
    fuses unrelated appearances.

    A representative that no group can accept becomes a group of its own, so
    the retained count is bounded by how many distinct appearance modes the
    object actually holds rather than by the sample budget. Candidates are
    considered heaviest-first so the densest modes anchor the result.

    ``max_distance`` reads monotonically: at 0 only exact duplicates merge,
    and raising it admits progressively more. At ``MAX_UNIT_SQUARED_L2`` the
    gate is provably off -- no two unit vectors are further apart than that, so
    the mean over any group cannot exceed it either and no candidate can ever
    be rejected. The pass then reduces exactly to nearest-seed assignment, so
    it short-circuits to that instead of building the distance matrix. In
    practice the switch happens well before 4.0: ReID embeddings sit in a
    narrow cone, measured at most 0.95 apart on this deployment, so any
    threshold from 1.0 up already admits everything.

    Returns:
        ``(retained_indices, assignments)`` -- ascending indices into
        ``normalized`` of the surviving representatives, and for every row of
        ``normalized`` the position within ``retained_indices`` of the group
        it belongs to. Every group contains its own representative, so no
        group is ever empty, and every group's mean pairwise distance is at
        most ``max_distance``.
    """
    retained_indices = np.array(sorted({int(i) for i in seed_indices}), dtype=int)

    if max_distance >= MAX_UNIT_SQUARED_L2:
        return retained_indices, _assign_to_representatives(normalized[retained_indices], normalized)

    # _cdist is Euclidean; square it to compare on the squared-L2 scale the
    # threshold is expressed in.
    distances = _cdist(normalized, normalized) ** 2

    n = len(normalized)
    # A group's mean distance to a candidate is a sum divided by a count, and
    # the sum only ever grows by one row per join -- so carry it rather than
    # re-gathering the group's members and averaging them once per
    # (candidate, group) pair. group_sums[g, i] is the summed distance from row
    # i to every member of group g, so the means for a candidate against all
    # groups at once are group_sums[:n_groups, candidate] / counts. That turns
    # the O(n^2) tiny-numpy-call scan into O(n) vector operations, which is the
    # whole cost of this function at realistic representative counts.
    #
    # Every row lands in exactly one group, so n groups is the ceiling -- but
    # only [:n_groups] is ever read, and the gate exists to keep the group count
    # below n, so reserving all n rows up front would commit 8n^2 bytes to hold
    # a fraction of that. Start at the groups that exist and double on demand,
    # capped at the ceiling. counts and seeds are O(n) bytes, so they stay full
    # length. Sums are accumulated over the whole pass and so are kept in
    # float64; the per-pair means they replace were computed by numpy's pairwise
    # summation over float32, which is a comparable error either way.
    n_groups = len(retained_indices)
    group_sums = np.zeros((max(n_groups, 1), n), dtype=np.float64)
    counts = np.zeros(n, dtype=np.int64)
    # Group seeds in insertion order: the initial representatives ascending,
    # then any candidate promoted to stand for itself.
    seeds = np.zeros(n, dtype=np.int64)
    group_sums[:n_groups] = distances[retained_indices]
    counts[:n_groups] = 1
    seeds[:n_groups] = retained_indices

    group_of = {int(i): int(i) for i in retained_indices}
    for candidate in np.argsort(-weights, kind="stable"):
        candidate = int(candidate)
        if candidate in group_of:
            continue
        row = distances[candidate]
        means = group_sums[:n_groups, candidate] / counts[:n_groups]
        # Ineligible groups are pushed to infinity so one argmin does both the
        # gate and the nearest-seed choice. argmin returns the earliest
        # minimum, which keeps the insertion-order tie-break of the scan this
        # replaces.
        seed_distances = np.where(means <= max_distance, row[seeds[:n_groups]], np.inf)
        best = int(np.argmin(seed_distances))
        if not np.isfinite(seed_distances[best]):
            # Nothing can describe it; it stands for itself. Later candidates
            # can still join it, which keeps near-duplicates from each
            # claiming a row of their own.
            if n_groups == len(group_sums):
                grown = np.zeros((min(n, 2 * len(group_sums)), n), dtype=np.float64)
                grown[:n_groups] = group_sums[:n_groups]
                group_sums = grown
            group_sums[n_groups] = row
            counts[n_groups] = 1
            seeds[n_groups] = candidate
            n_groups += 1
            group_of[candidate] = candidate
        else:
            group_sums[best] += row
            counts[best] += 1
            group_of[candidate] = int(seeds[best])

    retained_indices = np.array(sorted(int(s) for s in seeds[:n_groups]), dtype=int)
    position = {int(group): i for i, group in enumerate(retained_indices)}
    assignments = np.array([position[group_of[i]] for i in range(n)], dtype=np.int64)
    return retained_indices, assignments


def _group_moments(vectors: np.ndarray, assignments: np.ndarray, num_groups: int) -> Tuple[np.ndarray, np.ndarray]:
    """Per-group ``(counts, means)`` of the assigned vectors.

    ``counts[j]`` is the weight of representative ``j`` and ``means[j]`` is
    the per-dimension mean of the vectors assigned to it -- the summary
    stored alongside the representative so later stages can reason about its
    spread after the raw vectors are gone.
    """
    d = vectors.shape[1]
    counts = np.zeros(num_groups, dtype=np.int64)
    means = np.zeros((num_groups, d), dtype=np.float32)
    for group in range(num_groups):
        count, mean = moments_from_vectors(vectors[assignments == group])
        counts[group] = count
        means[group] = mean
    return counts, means


def _cdist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Euclidean distances between rows of ``a`` [m, d] and ``b`` [n, d] -> [m, n].

    Uses the Gram-matrix identity ||x - y||^2 = ||x||^2 + ||y||^2 - 2 x.y so the
    work runs as a single BLAS matmul producing an [m, n] matrix, rather than
    materializing an [m, n, d] difference tensor. At d=1280 the tensor form is
    both ~3x slower than a plain Python loop and prone to OOM; this stays flat.
    """
    a = np.ascontiguousarray(a, dtype=np.float32)
    b = np.ascontiguousarray(b, dtype=np.float32)
    sq_a = np.einsum("ij,ij->i", a, a)
    sq_b = np.einsum("ij,ij->i", b, b)
    d2 = sq_a[:, None] + sq_b[None, :] - 2.0 * (a @ b.T)
    np.maximum(d2, 0, out=d2)  # clamp tiny negatives from float cancellation
    return np.sqrt(d2, out=d2)


def _intra_cluster_medoid_costs(cluster_vectors: np.ndarray, block_size: int = 512) -> np.ndarray:
    """
    Total distance from each cluster point to all other cluster points.

    Equivalent to ``_cdist(cluster_vectors, cluster_vectors).sum(axis=0)`` but
    accumulates in row blocks so peak memory is O(block_size * cluster_size)
    instead of O(cluster_size^2).
    """
    m = len(cluster_vectors)
    costs = np.zeros(m, dtype=np.float64)
    for start in range(0, m, block_size):
        end = min(start + block_size, m)
        costs += _cdist(cluster_vectors[start:end], cluster_vectors).sum(axis=0)
    return costs


def _weighted_medoid_costs(
    cluster_vectors: np.ndarray, cluster_weights: np.ndarray, block_size: int = 512
) -> np.ndarray:
    """Weight-scaled total distance from each cluster point to all the others.

    The weighted counterpart of :func:`_intra_cluster_medoid_costs`:
    ``costs[i] == sum_j dist(i, j) * cluster_weights[j]``, so a candidate is
    pulled toward whichever points carry the most mass. Equivalent to
    ``_cdist(cv, cv) @ cluster_weights``, blocked by rows so peak memory is
    O(block_size * cluster_size) rather than O(cluster_size^2).
    """
    m = len(cluster_vectors)
    costs = np.empty(m, dtype=np.float64)
    for start in range(0, m, block_size):
        end = min(start + block_size, m)
        block = _cdist(cluster_vectors[start:end], cluster_vectors)
        costs[start:end] = block @ cluster_weights
    return costs


def load_embeddings_from_csv(csv_path: str, object_id: Optional[str] = None) -> Dict:
    """
    Load embeddings from CSV file dumped by /dump/file endpoint
    Groups embeddings by object_id for per-object processing

    Returns:
        Dict mapping object_id -> {
            'entry_ids', 'object_ids', 'sensor_ids', 'frame_ids', 'vectors'
        }
    """
    logger.info(f"Loading embeddings from {csv_path}...")

    # Group by object_id
    grouped_data = {}

    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            obj_id = str(row["object_id"])

            # Filter by object_id if specified
            if object_id is not None and obj_id != object_id:
                continue

            if obj_id not in grouped_data:
                grouped_data[obj_id] = {
                    "entry_ids": [],
                    "object_ids": [],
                    "sensor_ids": [],
                    "frame_ids": [],
                    "vectors": [],
                }

            grouped_data[obj_id]["entry_ids"].append(int(row["entry_id"]))
            grouped_data[obj_id]["object_ids"].append(obj_id)
            grouped_data[obj_id]["sensor_ids"].append(row["sensor_id"])
            grouped_data[obj_id]["frame_ids"].append(int(row["frame_id"]))

            # Parse vector from comma-separated string
            vector = [float(x) for x in row["vector"].split(",")]
            grouped_data[obj_id]["vectors"].append(vector)

    # Convert lists to numpy arrays
    for obj_id in grouped_data:
        grouped_data[obj_id]["entry_ids"] = np.array(grouped_data[obj_id]["entry_ids"])
        grouped_data[obj_id]["object_ids"] = np.array(grouped_data[obj_id]["object_ids"])
        grouped_data[obj_id]["sensor_ids"] = np.array(grouped_data[obj_id]["sensor_ids"])
        grouped_data[obj_id]["frame_ids"] = np.array(grouped_data[obj_id]["frame_ids"])
        grouped_data[obj_id]["vectors"] = np.array(grouped_data[obj_id]["vectors"], dtype=np.float32)

    total_embeddings = sum(len(data["vectors"]) for data in grouped_data.values())
    logger.info(f"Loaded {total_embeddings} embeddings across " f"{len(grouped_data)} object(s)")

    return grouped_data


def load_compressed_csv(csv_path: str, object_id: Optional[str] = None) -> Dict[str, List[Dict]]:
    """
    Load previously compressed embeddings from CSV (with weights).

    Returns:
        Dict mapping object_id -> list of dicts, each with keys:
        entry_id, object_id, sensor_id, frame_id, weight, mean, vector

    Dumps written before compression started carrying per-row moments have no
    mean column; those rows fall back to a degenerate summary (mean = the
    representative itself, carrying zero spread), which is the best available
    reconstruction once the raw vectors are gone. A variance column, written
    while the spread model still needed one, is ignored.
    """
    print(f"Loading compressed embeddings from {csv_path}...")

    grouped: Dict[str, List[Dict]] = {}
    rows_without_moments = 0

    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            obj_id = str(row["object_id"])

            if object_id is not None and obj_id != object_id:
                continue

            vector = [float(x) for x in row["vector"].split(",")]
            if row.get("mean"):
                mean = [float(x) for x in row["mean"].split(",")]
            else:
                rows_without_moments += 1
                mean = _normalize_vectors(np.array([vector], dtype=np.float32))[0].tolist()

            item = {
                "entry_id": int(row["entry_id"]),
                "object_id": obj_id,
                "sensor_id": row["sensor_id"],
                "frame_id": int(row["frame_id"]),
                "weight": int(row["weight"]),
                "vector": vector,
                "mean": mean,
            }
            grouped.setdefault(obj_id, []).append(item)

    total = sum(len(v) for v in grouped.values())
    print(f"Loaded {total} compressed embeddings across {len(grouped)} object(s)")
    if rows_without_moments:
        print(f"  {rows_without_moments} row(s) had no mean column; " f"substituted mean=vector")

    return grouped


def remove_outliers(vectors: np.ndarray, threshold_std: float = 3.0) -> Tuple[np.ndarray, np.ndarray]:
    """
    Remove extreme outliers based on distance to centroid

    Args:
        vectors: NxD array of vectors
        threshold_std: Number of standard deviations for outlier threshold

    Returns:
        Tuple of (filtered_vectors, mask)
    """
    logger.debug(f"Removing outliers (threshold: {threshold_std} std)...")

    # Calculate centroid
    centroid = np.mean(vectors, axis=0)

    # Calculate distances to centroid
    distances = np.linalg.norm(vectors - centroid, axis=1)

    # Calculate threshold
    mean_dist = np.mean(distances)
    std_dist = np.std(distances)
    threshold = mean_dist + threshold_std * std_dist

    # Create mask for non-outliers
    mask = distances <= threshold

    logger.debug(f"  Original count: {len(vectors)}")
    logger.debug(f"  Mean distance: {mean_dist:.4f}, Std distance: {std_dist:.4f}, Threshold: {threshold:.4f}")
    logger.debug(f"  Removed: {(~mask).sum()} outliers, Remaining: {mask.sum()} vectors")

    return vectors[mask], mask


def cosine_prune(
    vectors: np.ndarray, similarity_threshold: float = 0.999, normalized: Optional[np.ndarray] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Remove vectors that are too similar (cosine similarity > threshold)

    Args:
        vectors: NxD array of vectors (preferably already normalized)
        similarity_threshold: Cosine similarity threshold (0.999-0.9995)
        normalized: Optional pre-normalized float32 array aligned with ``vectors``

    Returns:
        Tuple of (pruned_vectors, mask)
    """
    logger.debug(f"Cosine pruning (threshold: {similarity_threshold})...")

    if normalized is None:
        normalized = _normalize_vectors(vectors)
    else:
        normalized = normalized.astype(np.float32, copy=False)

    d = normalized.shape[1]
    index = faiss.IndexFlatIP(d)  # Inner product = cosine for normalized vectors
    index.add(normalized)

    # Greedy dedup: keep first index, drop later points with cos >= threshold.
    n = len(vectors)
    keep_mask = np.ones(n, dtype=bool)

    for i in range(n):
        if not keep_mask[i]:
            continue

        # FAISS range_search uses a strict lower bound; nudge slightly below
        # threshold so inner products equal to the threshold match old >= logic.
        # At threshold=1.0 this also catches exact duplicates.
        radius = np.nextafter(np.float32(similarity_threshold), np.float32(0.0))
        lims, _D, ids = index.range_search(normalized[i : i + 1], radius)
        later = ids[lims[0] : lims[1]]
        later = later[later > i]
        if later.size:
            keep_mask[later] = False

    logger.debug(f"  Cosine prune: {len(vectors)} -> {keep_mask.sum()} (removed {(~keep_mask).sum()})")

    return vectors[keep_mask], keep_mask


def farthest_point_sampling(
    vectors: np.ndarray, max_k: int = 100, recompression: bool = False, normalized: Optional[np.ndarray] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Perform Farthest Point Sampling (FPS) using FAISS

    Args:
        vectors: NxD array of vectors (preferably already normalized)
        max_k: Maximum number of samples
        normalized: Optional pre-normalized float32 array aligned with ``vectors``

    Returns:
        Tuple of (sampled_vectors, indices)
    """
    n = len(vectors)
    k = min(max_k, n)

    logger.debug(f"Farthest Point Sampling: N={n}, K={k} (max_k={max_k})")

    if k >= n:
        logger.debug("  K >= N, returning all vectors")
        return vectors, np.arange(n)

    if normalized is None:
        normalized = _normalize_vectors(vectors)
    else:
        normalized = normalized.astype(np.float32, copy=False)

    d = normalized.shape[1]

    # Build FAISS index
    index = faiss.IndexFlatL2(d)
    index.add(normalized)

    # FPS algorithm
    selected_indices = []

    # Start with the point closest to centroid (more representative)
    centroid = np.mean(normalized, axis=0, keepdims=True)
    distances_to_centroid = np.linalg.norm(normalized - centroid, axis=1)
    current_idx = np.argmin(distances_to_centroid)
    logger.debug(
        f"  Starting with point closest to centroid: index {current_idx} "
        f"(distance: {distances_to_centroid[current_idx]:.6f})"
    )
    selected_indices.append(current_idx)

    # Track minimum distances to selected set
    min_distances = np.full(n, np.inf)

    for _ in range(k - 1):
        # Update distances to the newly added point
        query = normalized[current_idx : current_idx + 1]
        D, ids = index.search(query, n)

        # Update minimum distances
        distances_by_index = np.empty(n, dtype=D.dtype)
        distances_by_index[ids[0]] = D[0]
        min_distances = np.minimum(min_distances, distances_by_index)

        # Set distance of already selected points to -inf
        for idx in selected_indices:
            min_distances[idx] = -np.inf

        # Select the farthest point
        current_idx = np.argmax(min_distances)
        selected_indices.append(current_idx)

    selected_indices = np.array(selected_indices)

    logger.debug(f"  FPS: selected {len(selected_indices)} from {n}")

    return vectors[selected_indices], selected_indices


def kmedoids_sampling(
    vectors: np.ndarray, max_k: int = 100, max_iter: int = 10, seed: int = 100, normalized: Optional[np.ndarray] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Perform K-medoids clustering and return the medoid indices

    K-medoids selects actual data points as cluster centers (unlike K-means
    which uses mean centroids). Uses PAM-style algorithm with FAISS for
    efficient distance computation.

    Args:
        vectors: NxD array of vectors (preferably already normalized)
        max_k: Maximum number of clusters/samples
        max_iter: Maximum iterations for refinement
        seed: Random seed for FAISS K-means initialization
        normalized: Optional pre-normalized float32 array aligned with ``vectors``

    Returns:
        Tuple of (sampled_vectors, indices)
    """
    n = len(vectors)
    k = min(max_k, n)

    logger.debug(f"K-medoids Clustering: N={n}, K={k} (max_k={max_k})")

    if k >= n:
        logger.debug("  K >= N, returning all vectors")
        return vectors, np.arange(n)

    if normalized is None:
        normalized = _normalize_vectors(vectors)
    else:
        normalized = normalized.astype(np.float32, copy=False)

    d = normalized.shape[1]

    # Build FAISS index for distance lookups
    index = faiss.IndexFlatL2(d)
    index.add(normalized)

    # Initialize medoids using K-means: run K-means, then find nearest
    # actual point to each centroid. This gives different initial points
    # than FPS, making the two methods complementary.
    kmeans = faiss.Kmeans(d, k, niter=20, verbose=False, seed=seed)
    kmeans.train(normalized)

    # Find nearest actual data point to each K-means centroid
    D, ids = index.search(kmeans.centroids, 1)
    medoid_indices = ids.flatten()

    # Remove duplicates (if multiple centroids map to same point)
    medoid_indices = np.unique(medoid_indices)
    medoid_indices = medoid_indices.astype(int)

    # PAM-style refinement: swap medoids to reduce total cost
    logger.debug(f"  Refining medoids (max {max_iter} iterations)...")
    for iteration in range(max_iter):
        # Assign each point to its nearest medoid (BLAS matmul, [n, current_k])
        medoid_vectors = normalized[medoid_indices]
        current_k = len(medoid_indices)

        point_to_medoid = np.argmin(_cdist(normalized, medoid_vectors), axis=1)

        improved = False
        new_medoids = medoid_indices.copy()

        for cluster_id in range(current_k):
            cluster_points = np.where(point_to_medoid == cluster_id)[0]

            if len(cluster_points) == 0:
                continue

            # Best medoid = point minimizing total intra-cluster distance
            cluster_vectors = normalized[cluster_points]
            costs = _intra_cluster_medoid_costs(cluster_vectors)
            best_medoid = int(cluster_points[int(np.argmin(costs))])

            if best_medoid != medoid_indices[cluster_id]:
                new_medoids[cluster_id] = best_medoid
                improved = True

        medoid_indices = new_medoids

        if not improved:
            logger.debug(f"    Converged at iteration {iteration + 1}")
            break

    logger.debug(f"  K-medoids: selected {len(medoid_indices)} from {n}")

    return vectors[medoid_indices], medoid_indices


def weighted_kmedoids_sampling(
    vectors: np.ndarray, weights: np.ndarray, max_k: int = 100, max_iter: int = 10, seed: int = 100
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Perform weighted K-medoids clustering and return the medoid indices.

    Each vector has an associated weight (e.g. cluster size from a prior
    sampling stage).  Weights influence both the assignment cost and the
    medoid selection so that representatives are pulled toward denser
    regions of the embedding space.

    Args:
        vectors: NxD array of vectors (preferably already normalized)
        weights: N-length array of non-negative weights per vector
        max_k: Maximum number of clusters/samples
        max_iter: Maximum iterations for PAM refinement
        seed: Random seed for seed-point sampling and FAISS K-means
            initialization

    Returns:
        Tuple of (sampled_vectors, indices)
    """
    n = len(vectors)
    k = min(max_k, n)

    logger.debug(f"Weighted K-medoids: N={n}, K={k} (max_k={max_k})")

    if k >= n:
        logger.debug("  K >= N, returning all vectors")
        return vectors, np.arange(n)

    w = weights.astype(np.float64)
    if np.any(w == 0):
        zero_indices = np.where(w == 0)[0]
        raise ValueError(
            f"Weighted K-medoids: {len(zero_indices)} vector(s) have zero weight "
            f"(indices: {zero_indices.tolist()}). All weights must be positive."
        )

    # perform log2 compression on weights
    w = np.log2(2 * w)
    # %s rather than an f-string: rendering an n-element array costs a
    # per-element float format, and an f-string pays it even with DEBUG off.
    logger.debug("  Weights after log2 compression: %s", w)

    normalized = vectors.astype(np.float32)
    d = vectors.shape[1]

    index = faiss.IndexFlatL2(d)
    index.add(normalized)

    # Weighted K-means initialization: sample k seed points with
    # probability proportional to weights, then snap to nearest data point.
    probs = w / w.sum()
    seed_indices = np.random.default_rng(seed).choice(n, size=min(k * 4, n), replace=False, p=probs)
    seed_vectors = normalized[seed_indices]

    kmeans = faiss.Kmeans(d, k, niter=20, verbose=False, seed=seed)
    kmeans.train(seed_vectors)

    D_init, I_init = index.search(kmeans.centroids, 1)
    medoid_indices = np.unique(I_init.flatten()).astype(int)

    # PAM-style refinement with weights
    logger.debug(f"  Refining medoids (max {max_iter} iterations)...")
    for iteration in range(max_iter):
        medoid_vectors = normalized[medoid_indices]
        current_k = len(medoid_indices)

        # Assign each point to its nearest medoid (BLAS matmul, [n, current_k])
        point_to_medoid = np.argmin(_cdist(normalized, medoid_vectors), axis=1)
        # _cdist works from the Gram identity, so in float32 a point's distance
        # to itself is only near zero, not zero (up to ~1.2e-3 at d=1280). A
        # medoid whose near-duplicate is also a medoid can therefore measure
        # closer to the duplicate than to itself and be assigned away, leaving
        # its own cluster empty and tripping the guard below. Repeated frames of
        # a stationary person are exactly that input, and this runs on raw
        # vectors before any merging, so pin each medoid to its own cluster and
        # keep the guard an invariant rather than a hope.
        point_to_medoid[medoid_indices] = np.arange(current_k)

        improved = False
        new_medoids = medoid_indices.copy()

        for cluster_id in range(current_k):
            cluster_points = np.where(point_to_medoid == cluster_id)[0]

            if len(cluster_points) == 0:
                raise RuntimeError(
                    f"Weighted K-medoids: cluster {cluster_id} has no assigned points "
                    f"(medoid index={medoid_indices[cluster_id]}). This should never "
                    f"happen since the medoid itself must be assigned to its own cluster."
                )

            # Best medoid = point minimizing weight-scaled intra-cluster
            # distance. One blocked matmul per cluster rather than a distance
            # call per candidate.
            cluster_vectors = normalized[cluster_points]
            cluster_weights = w[cluster_points]
            costs = _weighted_medoid_costs(cluster_vectors, cluster_weights)
            best_medoid = int(cluster_points[int(np.argmin(costs))])

            if best_medoid != medoid_indices[cluster_id]:
                new_medoids[cluster_id] = best_medoid
                improved = True

        medoid_indices = new_medoids

        if not improved:
            logger.debug(f"    Converged at iteration {iteration + 1}")
            break

    logger.debug(f"  Weighted K-medoids: selected {len(medoid_indices)} from {n}")

    return vectors[medoid_indices], medoid_indices


def recompress_single_object(items: List[Dict], args) -> List[Dict]:
    """
    Recompress a single object from a list of items

    Each item carries the summary of the vectors it represents (``weight`` and,
    when ``args.with_moments`` is set, ``mean``). Representatives dropped by
    this pass have their summary folded into the surviving representative that
    absorbs them, so the total vector count -- and the mean, when carried --
    stay exact without ever revisiting the raw vectors.

    ``args.with_moments`` defaults to True. Set it False and the pass neither
    reads nor emits ``mean``: only the weights are summed, which is all the
    surviving row needs when nothing downstream consumes the moments.
    """
    with_moments = getattr(args, "with_moments", True)
    entry_ids_list = []
    object_ids_list = []
    sensor_ids_list = []
    frame_ids_list = []
    weights_list = []
    vectors_list = []
    means_list = []
    for it in items:
        entry_ids_list.append(it["entry_id"])
        object_ids_list.append(it["object_id"])
        sensor_ids_list.append(it["sensor_id"])
        frame_ids_list.append(it["frame_id"])
        weights_list.append(it["weight"])
        vectors_list.append(it["vector"])
        if with_moments:
            means_list.append(it["mean"])
    vectors = np.array(vectors_list, dtype=np.float32)
    entry_ids = np.array(entry_ids_list)
    obj_ids = np.array(object_ids_list)
    sensor_ids = np.array(sensor_ids_list)
    frame_ids = np.array(frame_ids_list)
    weights = np.array(weights_list)
    means = np.array(means_list, dtype=np.float64) if with_moments else None

    object_id = obj_ids[0]
    logger.debug(f"{'='*60}")
    logger.info(f"Recompressing Object ID: {object_id}")
    logger.debug(f"{'='*60}")

    logger.debug(f"Initial vectors: {len(vectors)}")

    # Step 4a: Farthest Point Sampling
    logger.debug(f"[STEP 4a] Farthest Point Sampling for object {object_id}...")
    _, fps_indices = farthest_point_sampling(vectors, max_k=args.max_samples, recompression=True)

    # Step 4b: weighted K-medoids Clustering with weights
    logger.debug(f"[STEP 4b] K-medoids Clustering for object {object_id}...")
    _, kmedoids_indices = weighted_kmedoids_sampling(vectors, weights=weights, max_k=args.max_samples, seed=args.seed)

    # Combine indices from both methods (union, no duplicates)
    combined_indices = np.unique(np.concatenate([fps_indices, kmedoids_indices]))
    logger.debug("  Combined sampling results:")
    logger.debug(f"  FPS selected: {len(fps_indices)} {fps_indices}")
    logger.debug(f"  K-medoids selected: {len(kmedoids_indices)} {kmedoids_indices}")
    logger.debug(f"  Combined (unique): {len(combined_indices)}")

    # Group the input representatives around the samples and merge the
    # summaries of everything that landed in the same group. Representatives
    # no sample can describe within merge_max_distance survive the pass
    # instead, so the budget above is a target rather than a cap.
    merge_max_distance = getattr(args, "merge_max_distance", COMPRESSION_MERGE_MAX_DISTANCE)
    retained_indices, assignments = _gated_representative_groups(
        _normalize_vectors(vectors),
        combined_indices,
        weights,
        merge_max_distance,
    )
    promoted = len(retained_indices) - len(combined_indices)
    if promoted:
        # Logged with the total so a steadily rising row count -- an object
        # whose representatives are spread wider than the gate, so nothing can
        # be merged -- is visible without extra instrumentation.
        logger.info(
            f"  Retained {len(retained_indices)} of {len(vectors)} "
            f"representative(s): {promoted} beyond the "
            f"{len(combined_indices)} sampled, further than "
            f"{merge_max_distance} (squared L2) from some member of every "
            f"other group"
        )

    entry_ids = entry_ids[retained_indices]
    obj_ids = obj_ids[retained_indices]
    sensor_ids = sensor_ids[retained_indices]
    frame_ids = frame_ids[retained_indices]
    sampled_vectors = vectors[retained_indices]

    new_weights = np.zeros(len(sampled_vectors), dtype=np.int64)
    new_means = np.zeros((len(sampled_vectors), means.shape[1]), dtype=np.float64) if with_moments else None
    for group in range(len(sampled_vectors)):
        members = np.flatnonzero(assignments == group)
        if members.size == 0:
            # Only reachable with the gate off, where assignment is pure
            # nearest-representative: two representatives holding identical
            # vectors have the tie broken toward one of them, so this one's
            # mass is already accounted for and the row must be dropped rather
            # than kept at weight 0 (which would fail the next weighted
            # k-medoids pass). While the gate is on, every group holds at
            # least its own representative.
            logger.error(
                f"  Dropping duplicate representative " f"entry_id={entry_ids[group]} absorbed by another sample"
            )
            continue
        if with_moments:
            count, mean = combine_cluster_moments(weights[members], means[members])
            new_means[group] = mean
        else:
            count = weights[members].sum()
        new_weights[group] = count
    logger.debug(f"  Weights: {new_weights}")

    # Convert back to items list
    items = []
    for i in range(len(entry_ids)):
        if new_weights[i] == 0:
            continue
        item = {
            "entry_id": entry_ids[i],
            "object_id": obj_ids[i],
            "sensor_id": sensor_ids[i],
            "frame_id": frame_ids[i],
            "weight": new_weights[i],
            "vector": sampled_vectors[i].tolist(),
        }
        if with_moments:
            item["mean"] = new_means[i].astype(np.float32).tolist()
        items.append(item)

    return items


def _empty_sampling_result(outlier_data: Dict, stats: Dict) -> Tuple:
    """Zero-sample return for :func:`process_single_object`, shaped like the
    success path so callers can unpack either unconditionally."""
    empty = np.array([])
    return (empty, empty, empty, empty, empty, empty, empty, outlier_data, stats)


def process_single_object(
    object_id: str, embeddings_data: Dict, args
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, Dict, Dict]:
    """
    Process a single object_id through the sampling pipeline.

    Returns:
        Tuple of (entry_ids, object_ids, sensor_ids, frame_ids, weights,
                  vectors, means, outlier_data, stats)
        where weights/means summarize the vectors assigned to each sampled
        representative, outlier_data contains metadata for distance-based
        outliers and stats is {"after_outlier_removal": int,
        "after_cosine_prune": int, "final_sampled": int}.

        ``means`` is ``None`` when ``args.with_moments`` is False (it defaults
        to True); the position is kept so callers can unpack unconditionally.
    """
    logger.debug(f"{'='*60}")
    logger.info(f"Processing Object ID: {object_id}")
    logger.debug(f"{'='*60}")

    vectors = embeddings_data["vectors"]
    entry_ids = embeddings_data["entry_ids"]
    obj_ids = embeddings_data["object_ids"]
    sensor_ids = embeddings_data["sensor_ids"]
    frame_ids = embeddings_data["frame_ids"]

    logger.debug(f"Initial vectors: {len(vectors)}")

    # Step 2: Remove outliers
    logger.debug(f"[STEP 2] Removing outliers for object {object_id}...")
    filtered_vectors, outlier_mask = remove_outliers(vectors, threshold_std=args.outlier_threshold)
    is_outlier = ~outlier_mask
    outlier_data = {
        "entry_ids": entry_ids[is_outlier],
        "object_ids": obj_ids[is_outlier],
        "sensor_ids": sensor_ids[is_outlier],
        "frame_ids": frame_ids[is_outlier],
    }
    logger.debug(f"  Outliers captured for potential extraction: {is_outlier.sum()}")

    # Apply mask to all metadata
    entry_ids = entry_ids[outlier_mask]
    obj_ids = obj_ids[outlier_mask]
    sensor_ids = sensor_ids[outlier_mask]
    frame_ids = frame_ids[outlier_mask]
    after_outlier = len(entry_ids)

    if len(filtered_vectors) == 0:
        logger.debug(f"  Warning: No vectors remaining after outlier removal for " f"object {object_id}")
        return _empty_sampling_result(
            outlier_data,
            {"after_outlier_removal": 0, "after_cosine_prune": 0, "final_sampled": 0},
        )

    filtered_normalized = _normalize_vectors(filtered_vectors)

    # Step 3: Cosine pruning
    logger.debug(f"[STEP 3] Cosine pruning for object {object_id}...")
    pruned_vectors, prune_mask = cosine_prune(
        filtered_vectors,
        similarity_threshold=args.cosine_threshold,
        normalized=filtered_normalized,
    )

    # Apply mask
    entry_ids = entry_ids[prune_mask]
    obj_ids = obj_ids[prune_mask]
    sensor_ids = sensor_ids[prune_mask]
    frame_ids = frame_ids[prune_mask]
    pruned_normalized = filtered_normalized[prune_mask]
    after_prune = len(entry_ids)

    if len(pruned_vectors) == 0:
        logger.debug(f"  Warning: No vectors remaining after cosine pruning for " f"object {object_id}")
        return _empty_sampling_result(
            outlier_data,
            {"after_outlier_removal": after_outlier, "after_cosine_prune": 0, "final_sampled": 0},
        )

    # Step 4a: Farthest Point Sampling
    logger.debug(f"[STEP 4a] Farthest Point Sampling for object {object_id}...")
    _, fps_indices = farthest_point_sampling(
        pruned_vectors,
        max_k=args.max_samples,
        normalized=pruned_normalized,
    )

    # Step 4b: K-medoids Clustering
    logger.debug(f"[STEP 4b] K-medoids Clustering for object {object_id}...")
    _, kmedoids_indices = kmedoids_sampling(
        pruned_vectors,
        max_k=args.max_samples,
        seed=args.seed,
        normalized=pruned_normalized,
    )

    # Combine indices from both methods (union, no duplicates)
    combined_indices = np.unique(np.concatenate([fps_indices, kmedoids_indices]))
    logger.debug("    Combined sampling results:")
    logger.debug(f"    FPS selected: {len(fps_indices)} {fps_indices}")
    logger.debug(f"    K-medoids selected: {len(kmedoids_indices)} {kmedoids_indices}")
    logger.debug(f"    Combined (unique): {len(combined_indices)}")

    # Apply combined indices
    entry_ids = entry_ids[combined_indices]
    obj_ids = obj_ids[combined_indices]
    sensor_ids = sensor_ids[combined_indices]
    frame_ids = frame_ids[combined_indices]
    sampled_vectors = pruned_vectors[combined_indices]
    sampled_normalized = pruned_normalized[combined_indices]

    # Now that we have all the sampled vectors, summarize the cluster each one
    # represents: weight = number of vectors closest to the sampled vector, and
    # mean = per-dimension mean of those vectors. Moments are taken over the
    # normalized vectors because downstream distance estimation assumes
    # unit-norm embeddings -- it reads the group's spread back out of the
    # mean's norm, which only holds for unit-norm members.
    assignments = _assign_to_representatives(sampled_normalized, filtered_normalized)
    if getattr(args, "with_moments", True):
        weights, means = _group_moments(filtered_normalized, assignments, len(sampled_vectors))
    else:
        # Only the group sizes are wanted; skip the per-group mean entirely
        # rather than computing a [k, d] array nothing will read.
        weights = np.bincount(assignments, minlength=len(sampled_vectors))
        means = None
    logger.debug(f"  Weights: {weights}")

    stats = {"after_outlier_removal": after_outlier, "after_cosine_prune": after_prune, "final_sampled": len(entry_ids)}
    return (entry_ids, obj_ids, sensor_ids, frame_ids, weights, sampled_vectors, means, outlier_data, stats)


def main():
    parser = argparse.ArgumentParser(description="Sample embeddings")
    parser.add_argument("--embeddings", required=True, help="Path to embeddings CSV file")
    parser.add_argument("--output-dir", required=True, help="Directory to save the sampled embeddings CSV")
    parser.add_argument(
        "--object-id", type=str, default=None, help="Filter by specific object_id (processes all if " "not specified)"
    )
    parser.add_argument(
        "--outlier-threshold", type=float, default=3.0, help="Std dev threshold for outlier removal " "(default: 3.0)"
    )
    parser.add_argument(
        "--cosine-threshold", type=float, default=0.999, help="Cosine similarity threshold (default: 0.999)"
    )
    parser.add_argument(
        "--max-samples", type=int, default=7, help="Maximum number of samples per object " "(default: 7)"
    )
    parser.add_argument(
        "--merge-max-distance",
        type=float,
        default=COMPRESSION_MERGE_MAX_DISTANCE,
        help="Largest squared L2 distance at which one "
        "representative may be folded into another during "
        "recompression; anything further away is kept as "
        "its own representative (default: "
        f"{COMPRESSION_MERGE_MAX_DISTANCE}). 0 merges "
        "unconditionally.",
    )
    parser.add_argument(
        "--prev-compressed",
        type=str,
        default=None,
        help="Path to a previously compressed CSV file. "
        "If provided, new samples are merged with previous "
        "compressed data per object and recompressed.",
    )
    parser.add_argument("--seed", type=int, default=100, help="Random seed for reproducible results (default: 100)")
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity (default: INFO). Use DEBUG for " "per-step internals from the sampling pipeline.",
    )

    args = parser.parse_args()

    # Configure logging so the INFO/DEBUG messages emitted throughout this
    # module (process_single_object, kmedoids_sampling, etc.) actually reach
    # the console. Without this, the root logger defaults to WARNING and the
    # script appears to do nothing.
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )

    np.random.seed(args.seed)

    os.makedirs(args.output_dir, exist_ok=True)

    logger.info("=" * 60)
    logger.info("SAMPLE EMBEDDINGS")
    logger.info("=" * 60)

    # Step 1: Load data
    logger.info("[STEP 1] Loading data...")
    embeddings_by_object = load_embeddings_from_csv(args.embeddings, args.object_id)

    if len(embeddings_by_object) == 0:
        logger.error("No embeddings found!")
        sys.exit(1)

    # Per-object processing: capture sampling stats from process_single_object
    # so the summary can show the full pipeline progression per object.
    all_sampled_data = []
    all_outlier_data = []
    per_object_stats: Dict[str, Dict[str, int]] = {}

    for object_id, embeddings_data in embeddings_by_object.items():
        (entry_ids, obj_ids, sensor_ids, frame_ids, weights, vectors, means, outlier_data, stats) = (
            process_single_object(object_id, embeddings_data, args)
        )

        per_object_stats[str(object_id)] = {
            "initial": int(len(embeddings_data["vectors"])),
            "after_outlier_removal": int(stats.get("after_outlier_removal", 0)),
            "after_cosine_prune": int(stats.get("after_cosine_prune", 0)),
            "final_sampled": int(stats.get("final_sampled", 0)),
        }

        if len(entry_ids) > 0:
            all_sampled_data.append(
                {
                    "entry_ids": entry_ids,
                    "object_ids": obj_ids,
                    "sensor_ids": sensor_ids,
                    "frame_ids": frame_ids,
                    "weights": weights,
                    "vectors": vectors,
                    "means": means,
                }
            )

        if len(outlier_data["entry_ids"]) > 0:
            all_outlier_data.append(outlier_data)

    if len(all_sampled_data) == 0:
        logger.error("No samples generated from any object!")
        sys.exit(1)

    # Convert sampled arrays to list-of-dicts (same format as main.py)
    all_sampled_items: List[Dict] = []
    for d in all_sampled_data:
        for i in range(len(d["entry_ids"])):
            vec = d["vectors"][i]
            all_sampled_items.append(
                {
                    "entry_id": int(d["entry_ids"][i]),
                    "object_id": str(d["object_ids"][i]),
                    "sensor_id": str(d["sensor_ids"][i]),
                    "frame_id": int(d["frame_ids"][i]),
                    "weight": int(d["weights"][i]),
                    "vector": vec.tolist() if hasattr(vec, "tolist") else list(vec),
                    "mean": d["means"][i].tolist(),
                }
            )

    # Recompression: merge with previous compressed data if provided
    total_prev_compressed = 0
    if args.prev_compressed and os.path.exists(args.prev_compressed):
        logger.info("\n" + "=" * 60)
        logger.info("[RECOMPRESSION] Merging with previous compressed data...")
        logger.info("=" * 60)

        prev_compressed_by_object = load_compressed_csv(args.prev_compressed, args.object_id)
        total_prev_compressed = sum(len(v) for v in prev_compressed_by_object.values())

        new_by_object: Dict[str, List[Dict]] = {}
        for item in all_sampled_items:
            new_by_object.setdefault(item["object_id"], []).append(item)

        final_items: List[Dict] = []
        all_object_ids_set = set(new_by_object.keys()) | set(prev_compressed_by_object.keys())

        for obj_id in sorted(all_object_ids_set):
            new_items = new_by_object.get(obj_id, [])
            prev_items = prev_compressed_by_object.get(obj_id, [])

            if prev_items and new_items:
                prev_entry_ids = {item["entry_id"] for item in prev_items}
                merged = new_items + prev_items
                logger.info(
                    f"  Object {obj_id}: {len(new_items)} new + " f"{len(prev_items)} prev = {len(merged)} merged"
                )
                recompressed = recompress_single_object(merged, args)
                new_entry_ids = {item["entry_id"] for item in recompressed}
                kept_prev = new_entry_ids & prev_entry_ids
                added_new = new_entry_ids - prev_entry_ids
                dropped_prev = prev_entry_ids - new_entry_ids
                logger.info(
                    f"    -> {len(recompressed)} after recompression "
                    f"(kept {len(kept_prev)} prev: {sorted(kept_prev)}, "
                    f"added {len(added_new)} new: {sorted(added_new)}, "
                    f"dropped {len(dropped_prev)} prev: {sorted(dropped_prev)})"
                )
                final_items.extend(recompressed)
            elif prev_items:
                # Nothing new for this object: keep prev compressed as-is.
                logger.info(f"  Object {obj_id}: 0 new + {len(prev_items)} " f"prev (kept unchanged)")
                final_items.extend(prev_items)
            else:
                logger.info(f"  Object {obj_id}: {len(new_items)} new " f"(no previous)")
                final_items.extend(new_items)

        all_sampled_items = final_items
        logger.info(f"\nTotal after recompression: {len(all_sampled_items)}")
    elif args.prev_compressed:
        logger.warning(f"--prev-compressed file not found: " f"{args.prev_compressed}")
        logger.warning("Proceeding without recompression.")

    # Dump compressed embeddings to CSV
    sampled_csv_path = os.path.join(args.output_dir, "sampled_embeddings.csv")
    logger.info(f"\nDumping compressed embeddings to {sampled_csv_path}...")
    with open(sampled_csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["entry_id", "object_id", "sensor_id", "frame_id", "weight", "vector", "mean"])
        for item in all_sampled_items:
            writer.writerow(
                [
                    item["entry_id"],
                    item["object_id"],
                    item["sensor_id"],
                    item["frame_id"],
                    item["weight"],
                    ",".join(str(v) for v in item["vector"]),
                    ",".join(str(v) for v in item["mean"]),
                ]
            )
    logger.info(f"  Wrote {len(all_sampled_items)} compressed embeddings")

    all_weights = np.array([item["weight"] for item in all_sampled_items])

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)

    total_original = sum(s["initial"] for s in per_object_stats.values())

    logger.info(f"Objects processed: {len(per_object_stats)}")
    logger.info(f"Total original vectors: {total_original}")
    if total_prev_compressed:
        logger.info(f"Previously compressed vectors merged: " f"{total_prev_compressed}")
    logger.info(f"Total sampled points (final): {len(all_sampled_items)}")
    if len(all_weights) > 0:
        logger.info(
            f"Sample weight stats: min={int(all_weights.min())}, "
            f"max={int(all_weights.max())}, "
            f"mean={float(all_weights.mean()):.2f}, "
            f"sum={int(all_weights.sum())}"
        )
    logger.info(f"Output directory: {args.output_dir}")

    if all_outlier_data:
        total_outliers = sum(len(d["entry_ids"]) for d in all_outlier_data)
        logger.info(f"Outliers found: {total_outliers}")

    # Per-object breakdown: combine per_object_stats (pipeline progression)
    # with the per-object counts in the final output (post recompression).
    final_counts_by_object: Dict[str, int] = {}
    for item in all_sampled_items:
        oid = item["object_id"]
        final_counts_by_object[oid] = final_counts_by_object.get(oid, 0) + 1

    all_obj_ids_for_report = sorted(set(per_object_stats.keys()) | set(final_counts_by_object.keys()))

    logger.info("Per-object breakdown:")
    for obj_id in all_obj_ids_for_report:
        stats = per_object_stats.get(obj_id, {})
        initial = stats.get("initial", 0)
        after_out = stats.get("after_outlier_removal", 0)
        after_pr = stats.get("after_cosine_prune", 0)
        sampled = stats.get("final_sampled", 0)
        final = final_counts_by_object.get(obj_id, 0)
        logger.info(
            f"  Object {obj_id}: initial={initial}, "
            f"after_outlier_removal={after_out}, "
            f"after_cosine_prune={after_pr}, "
            f"sampled_pre_merge={sampled}, "
            f"final_in_output={final}"
        )

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
