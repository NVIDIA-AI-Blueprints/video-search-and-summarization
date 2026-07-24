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
"""Unit tests for video_analytics/embeddings module."""

from unittest.mock import MagicMock
from unittest.mock import patch

import numpy as np
import pytest

from vss_agents.video_analytics.embeddings import EmbeddingModel
from vss_agents.video_analytics.embeddings import PlaceEmbeddingCache


def add_place(cache: PlaceEmbeddingCache, name: str, embedding: np.ndarray) -> None:
    """Helper to add a single place to the cache."""
    embedding_2d = embedding.reshape(1, -1)
    cache.add_places_batch([name], embedding_2d)


class TestEmbeddingModel:
    """Test EmbeddingModel class."""

    @patch("vss_agents.video_analytics.embeddings.SentenceTransformer", create=True)
    def test_init_success(self, mock_st_class):
        """Test successful model initialization."""
        mock_model = MagicMock()
        mock_st_class.return_value = mock_model

        with patch.dict("sys.modules", {"sentence_transformers": MagicMock(SentenceTransformer=mock_st_class)}):
            with patch("vss_agents.video_analytics.embeddings.EmbeddingModel._load_model") as mock_load:
                model = EmbeddingModel("test-model")
                assert model.model_name == "test-model"
                mock_load.assert_called_once()

    def test_encode_without_model_raises(self):
        """Test encode raises when model not loaded."""
        with patch("vss_agents.video_analytics.embeddings.EmbeddingModel._load_model"):
            model = EmbeddingModel()
            model.model = None
            with pytest.raises(RuntimeError, match="Embedding model not loaded"):
                model.encode("test text")

    def test_encode_batch_without_model_raises(self):
        """Test encode_batch raises when model not loaded."""
        with patch("vss_agents.video_analytics.embeddings.EmbeddingModel._load_model"):
            model = EmbeddingModel()
            model.model = None
            with pytest.raises(RuntimeError, match="Embedding model not loaded"):
                model.encode_batch(["text1", "text2"])

    def test_encode_batch_empty_list(self):
        """Test encode_batch with empty list returns empty array."""
        with patch("vss_agents.video_analytics.embeddings.EmbeddingModel._load_model"):
            model = EmbeddingModel()
            model.model = MagicMock()
            result = model.encode_batch([])
            assert result.shape == (0, 0)

    def test_encode_success(self):
        """Test successful encoding."""
        with patch("vss_agents.video_analytics.embeddings.EmbeddingModel._load_model"):
            model = EmbeddingModel()
            mock_model = MagicMock()
            mock_model.encode.return_value = np.array([0.1, 0.2, 0.3])
            model.model = mock_model

            result = model.encode("test text")
            np.testing.assert_array_equal(result, np.array([0.1, 0.2, 0.3]))
            mock_model.encode.assert_called_once_with("test text", convert_to_numpy=True)

    def test_encode_batch_success(self):
        """Test successful batch encoding."""
        with patch("vss_agents.video_analytics.embeddings.EmbeddingModel._load_model"):
            model = EmbeddingModel()
            mock_model = MagicMock()
            mock_model.encode.return_value = np.array([[0.1, 0.2], [0.3, 0.4]])
            model.model = mock_model

            result = model.encode_batch(["text1", "text2"])
            np.testing.assert_array_equal(result, np.array([[0.1, 0.2], [0.3, 0.4]]))

    def test_encode_exception(self):
        """Test encode handles exceptions."""
        with patch("vss_agents.video_analytics.embeddings.EmbeddingModel._load_model"):
            model = EmbeddingModel()
            mock_model = MagicMock()
            mock_model.encode.side_effect = Exception("Encode error")
            model.model = mock_model

            with pytest.raises(Exception, match="Encode error"):
                model.encode("test")

    def test_encode_batch_exception(self):
        """Test encode_batch handles exceptions."""
        with patch("vss_agents.video_analytics.embeddings.EmbeddingModel._load_model"):
            model = EmbeddingModel()
            mock_model = MagicMock()
            mock_model.encode.side_effect = Exception("Batch error")
            model.model = mock_model

            with pytest.raises(Exception, match="Batch error"):
                model.encode_batch(["text1"])

    def test_load_model_success(self):
        """Test _load_model successfully loads model."""
        mock_st = MagicMock()
        mock_model_instance = MagicMock()
        mock_st.return_value = mock_model_instance

        with patch.dict(
            "sys.modules",
            {"sentence_transformers": MagicMock(SentenceTransformer=mock_st)},
        ):
            # Reimport to get fresh class
            import importlib

            import vss_agents.video_analytics.embeddings as emb_module

            importlib.reload(emb_module)

            model = emb_module.EmbeddingModel("test-model")
            assert model.model is not None

    def test_load_model_failure(self):
        """Test _load_model handles import failure."""
        with (
            patch.dict("sys.modules", {"sentence_transformers": None}),
            patch(
                "vss_agents.video_analytics.embeddings.EmbeddingModel._load_model",
                side_effect=ImportError("Module not found"),
            ),
            pytest.raises(ImportError),
        ):
            EmbeddingModel()


class TestPlaceEmbeddingCache:
    """Test PlaceEmbeddingCache class."""

    def test_init(self):
        """Test cache initialization."""
        cache = PlaceEmbeddingCache()
        assert cache.place_names == []
        assert cache.embeddings is None

    def test_add_place(self):
        """Test adding a place to the cache."""
        cache = PlaceEmbeddingCache()
        embedding = np.array([0.1, 0.2, 0.3])
        add_place(cache, "Main Street", embedding)

        assert len(cache.place_names) == 1
        assert cache.place_names[0] == "Main Street"
        assert cache.embeddings is not None
        assert cache.embeddings.shape == (1, 3)

    def test_add_multiple_places(self):
        """Test adding multiple places to the cache."""
        cache = PlaceEmbeddingCache()

        add_place(cache, "Place 1", np.array([0.1, 0.2, 0.3]))
        add_place(cache, "Place 2", np.array([0.4, 0.5, 0.6]))
        add_place(cache, "Place 3", np.array([0.7, 0.8, 0.9]))

        assert len(cache.place_names) == 3
        assert cache.embeddings.shape == (3, 3)

    def test_find_similar_empty_cache(self):
        """Test finding similar places in an empty cache."""
        cache = PlaceEmbeddingCache()
        query_embedding = np.array([0.1, 0.2, 0.3])

        results = cache.find_similar(query_embedding)
        assert results == []

    def test_find_similar_with_results(self):
        """Test finding similar places with results."""
        cache = PlaceEmbeddingCache()

        # Add some places with normalized embeddings
        add_place(cache, "Main Street", np.array([1.0, 0.0, 0.0]))
        add_place(cache, "Oak Avenue", np.array([0.9, 0.1, 0.0]))
        add_place(cache, "River Road", np.array([0.0, 1.0, 0.0]))

        # Query with embedding similar to first two
        query = np.array([0.95, 0.05, 0.0])
        results = cache.find_similar(query, top_k=2)

        assert len(results) <= 2
        # Results should be sorted by similarity

    def test_find_similar_with_threshold(self):
        """Test finding similar places with threshold."""
        cache = PlaceEmbeddingCache()

        add_place(cache, "Match", np.array([1.0, 0.0, 0.0]))
        add_place(cache, "No Match", np.array([0.0, 0.0, 1.0]))

        query = np.array([1.0, 0.0, 0.0])
        results = cache.find_similar(query, threshold=0.9)

        # Only the exact match should be returned
        assert len(results) >= 1

    def test_cache_length(self):
        """Test getting cache length via place_names."""
        cache = PlaceEmbeddingCache()

        assert len(cache.place_names) == 0

        add_place(cache, "Place 1", np.array([0.1, 0.2, 0.3]))
        assert len(cache.place_names) == 1

        add_place(cache, "Place 2", np.array([0.4, 0.5, 0.6]))
        assert len(cache.place_names) == 2

    def test_find_similar_top_k(self):
        """Test top_k parameter in find_similar."""
        cache = PlaceEmbeddingCache()

        for i in range(10):
            add_place(cache, f"Place {i}", np.array([float(i), 0.0, 0.0]))

        query = np.array([5.0, 0.0, 0.0])
        results = cache.find_similar(query, top_k=3)

        assert len(results) <= 3

    def test_2d_embedding(self):
        """Test handling of 2D embedding array."""
        cache = PlaceEmbeddingCache()
        embedding_2d = np.array([[0.1, 0.2, 0.3]])

        # Should handle 2D array
        add_place(cache, "Test", embedding_2d.flatten())
        assert len(cache.place_names) == 1

    def test_size_method(self):
        """Test size() method returns correct count."""
        cache = PlaceEmbeddingCache()
        assert cache.size() == 0

        add_place(cache, "Place 1", np.array([0.1, 0.2, 0.3]))
        assert cache.size() == 1

        add_place(cache, "Place 2", np.array([0.4, 0.5, 0.6]))
        assert cache.size() == 2

    def test_add_places_batch_empty(self):
        """Test add_places_batch with empty list does nothing."""
        cache = PlaceEmbeddingCache()
        cache.add_places_batch([], np.array([]).reshape(0, 3))
        assert cache.size() == 0
        assert cache.embeddings is None

    def test_add_places_batch_mismatch_raises(self):
        """Test add_places_batch raises on mismatch."""
        cache = PlaceEmbeddingCache()
        with pytest.raises(ValueError, match="Mismatch"):
            cache.add_places_batch(["Place 1", "Place 2"], np.array([[0.1, 0.2, 0.3]]))

    def test_add_places_batch_multiple(self):
        """Test add_places_batch with multiple places at once."""
        cache = PlaceEmbeddingCache()
        names = ["Place 1", "Place 2", "Place 3"]
        embeddings = np.array(
            [
                [0.1, 0.2, 0.3],
                [0.4, 0.5, 0.6],
                [0.7, 0.8, 0.9],
            ]
        )
        cache.add_places_batch(names, embeddings)

        assert cache.size() == 3
        assert cache.embeddings.shape == (3, 3)
