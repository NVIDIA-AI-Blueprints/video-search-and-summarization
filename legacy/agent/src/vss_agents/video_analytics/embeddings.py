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
Embedding utilities for semantic place search.

Provides functionality to encode text into embeddings and perform
similarity-based search over cached place embeddings.
"""

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingModel:
    """
    Wrapper around sentence-transformers for generating embeddings.

    Loads model once at initialization and caches in memory for fast inference.
    """

    model: Any  # SentenceTransformer instance

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        """
        Initialize embedding model.

        Args:
            model_name: Name of the sentence-transformers model to use
        """
        self.model_name = model_name
        self._load_model()

    def _load_model(self) -> None:
        """Load the sentence-transformers model."""
        try:
            from sentence_transformers import SentenceTransformer

            logger.info(f"Loading embedding model: {self.model_name}")
            self.model = SentenceTransformer(self.model_name)
            logger.info(f"Successfully loaded embedding model: {self.model_name}")
        except Exception as e:
            logger.error(f"Failed to load embedding model {self.model_name}: {e}")
            raise

    def encode(self, text: str) -> np.ndarray:
        """
        Encode text into embedding vector.

        Args:
            text: Text to encode

        Returns:
            Embedding vector as numpy array
        """
        if self.model is None:
            raise RuntimeError("Video Analytics: Embedding model not loaded")

        try:
            embedding: np.ndarray = self.model.encode(text, convert_to_numpy=True)
            return embedding
        except Exception as e:
            logger.error(f"Failed to encode text '{text}': {e}")
            raise

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        """
        Encode multiple texts into embedding vectors in a single batch.

        Args:
            texts: List of texts to encode

        Returns:
            2D numpy array of shape (len(texts), embedding_dim)
        """
        if self.model is None:
            raise RuntimeError("Video Analytics: Embedding model not loaded")

        if not texts:
            return np.array([]).reshape(0, 0)

        try:
            logger.info(f"Batch encoding {len(texts)} texts...")
            embeddings: np.ndarray = self.model.encode(texts, convert_to_numpy=True, show_progress_bar=True)
            logger.info(f"Successfully encoded {len(texts)} texts")
            return embeddings
        except Exception as e:
            logger.error(f"Failed to batch encode {len(texts)} texts: {e}")
            raise


class PlaceEmbeddingCache:
    """
    In-memory cache of place name embeddings for fast similarity search.

    Stores embeddings as numpy arrays and performs cosine similarity
    search to find semantically similar places.
    """

    def __init__(self) -> None:
        """Initialize empty cache."""
        self.place_names: list[str] = []
        self.embeddings: np.ndarray | None = None  # Shape: (N, embedding_dim)

    def add_places_batch(self, names: list[str], embeddings: np.ndarray) -> None:
        """
        Add multiple places and their embeddings to the cache at once.

        Args:
            names: List of place names
            embeddings: 2D array of embeddings, shape (len(names), embedding_dim)
        """
        if len(names) != len(embeddings):
            raise ValueError(f"Video Analytics: Mismatch: {len(names)} names vs {len(embeddings)} embeddings")

        if len(names) == 0:
            return

        self.place_names.extend(names)

        if self.embeddings is None:
            self.embeddings = embeddings
        else:
            self.embeddings = np.vstack([self.embeddings, embeddings])

    def find_similar(
        self, query_embedding: np.ndarray, top_k: int = 5, threshold: float = 0.5
    ) -> list[tuple[str, float]]:
        """
        Find places similar to the query embedding.

        Uses cosine similarity to rank places by semantic similarity.

        Args:
            query_embedding: Query embedding vector
            top_k: Maximum number of results to return
            threshold: Minimum similarity score (0.0-1.0) to include in results

        Returns:
            List of (place_name, similarity_score) tuples, sorted by score descending
        """
        if self.embeddings is None or len(self.place_names) == 0:
            return []

        # Compute cosine similarity between query and all cached embeddings
        # Cosine similarity = dot(A, B) / (norm(A) * norm(B))
        query_norm = query_embedding / np.linalg.norm(query_embedding)
        embeddings_norm = self.embeddings / np.linalg.norm(self.embeddings, axis=1, keepdims=True)

        # Shape: (N,) - similarity score for each place
        similarities = np.dot(embeddings_norm, query_norm)

        # Get top-k indices sorted by similarity descending
        top_indices = np.argsort(similarities)[::-1][:top_k]

        # Filter by threshold and build results
        results = []
        for idx in top_indices:
            score = float(similarities[idx])
            if score >= threshold:
                place_name = self.place_names[idx]
                results.append((place_name, score))

        return results

    def size(self) -> int:
        """Return number of places in cache."""
        return len(self.place_names)
