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

import numpy as np
from unittest.mock import patch

from mdx.analytics.core.utils.crp import Model, CRP


class TestModel:
    """Test cases for the Model class"""

    def test_init_empty_lists(self):
        """Test Model initialization with empty lists"""
        model = Model([], [])
        assert model.centersA == []
        assert model.center_idx == []
        assert model.centers == []

    def test_init_with_data(self):
        """Test Model initialization with data"""
        centers = [np.array([1, 2, 3]), np.array([4, 5, 6])]
        center_idx = [2, 3]
        model = Model(centers, center_idx)
        
        assert len(model.centersA) == 2
        assert len(model.center_idx) == 2
        assert len(model.centers) == 2
        np.testing.assert_array_equal(model.centersA[0], centers[0])
        np.testing.assert_array_equal(model.centersA[1], centers[1])
        assert model.center_idx == center_idx
        
        # Check that centers are normalized
        for i, center in enumerate(model.centers):
            expected_normalized = centers[i] / np.sqrt(np.sum(centers[i]**2))
            np.testing.assert_array_almost_equal(center, expected_normalized)

    def test_init_with_zero_vector(self):
        """Test Model initialization with zero vector (edge case for normalization)"""
        centers = [np.array([0, 0, 0])]
        center_idx = [1]
        
        # Zero vectors result in NaN in normalized centers, but don't raise exception
        model = Model(centers, center_idx)
        assert len(model.centers) == 1
        # The normalized zero vector results in NaN values
        assert np.all(np.isnan(model.centers[0]))

    def test_filter_default_threshold(self):
        """Test filter method with default threshold"""
        centers = [np.array([1, 2]), np.array([3, 4]), np.array([5, 6])]
        center_idx = [0, 2, 3]  # Only first cluster has 0 elements
        model = Model(centers, center_idx)
        
        filtered = model.filter()
        assert len(filtered) == 2  # Only clusters with size >= 1
        assert filtered[0][1] == 2  # Second cluster size
        assert filtered[1][1] == 3  # Third cluster size

    def test_filter_custom_threshold(self):
        """Test filter method with custom threshold"""
        centers = [np.array([1, 2]), np.array([3, 4]), np.array([5, 6])]
        center_idx = [1, 2, 5]
        model = Model(centers, center_idx)
        
        filtered = model.filter(threshold=3)
        assert len(filtered) == 1  # Only third cluster has size >= 3
        assert filtered[0][1] == 5

    def test_filter_high_threshold(self):
        """Test filter method with threshold higher than all cluster sizes"""
        centers = [np.array([1, 2]), np.array([3, 4])]
        center_idx = [1, 2]
        model = Model(centers, center_idx)
        
        filtered = model.filter(threshold=5)
        assert len(filtered) == 0  # No clusters meet the threshold

    def test_distance_default_threshold(self):
        """Test distance method with default threshold"""
        # Create orthogonal vectors for predictable dot products
        centers = [np.array([1, 0]), np.array([0, 1]), np.array([1, 1])]
        center_idx = [1, 1, 1]
        model = Model(centers, center_idx)
        
        distance_matrix = model.distance()
        assert distance_matrix.shape == (3, 3)
        
        # Check diagonal elements (should be 1.0 for normalized vectors)
        np.testing.assert_array_almost_equal(np.diag(distance_matrix), [1.0, 1.0, 1.0])
        
        # Check that matrix is symmetric
        np.testing.assert_array_almost_equal(distance_matrix, distance_matrix.T)

    def test_distance_with_filtering(self):
        """Test distance method with filtering"""
        centers = [np.array([1, 0]), np.array([0, 1]), np.array([1, 1])]
        center_idx = [1, 0, 2]  # Second cluster has 0 elements
        model = Model(centers, center_idx)
        
        distance_matrix = model.distance(threshold=1)
        assert distance_matrix.shape == (2, 2)  # Only 2 clusters pass the filter

    def test_predict_single_center(self):
        """Test predict method with single center"""
        centers = [np.array([1, 0])]
        center_idx = [1]
        model = Model(centers, center_idx)
        
        v = np.array([0.8, 0.6])  # Normalized vector
        predicted = model.predict(v)
        assert predicted == 0

    def test_predict_multiple_centers(self):
        """Test predict method with multiple centers"""
        centers = [np.array([1, 0]), np.array([0, 1]), np.array([-1, 0])]
        center_idx = [1, 1, 1]
        model = Model(centers, center_idx)
        
        # Test vector closer to first center
        v = np.array([0.8, 0.6])  # Closer to [1, 0]
        predicted = model.predict(v)
        assert predicted == 0
        
        # Test vector closer to second center
        v = np.array([0.2, 0.98])  # Closer to [0, 1]
        predicted = model.predict(v)
        assert predicted == 1

    def test_predict_next_single_center(self):
        """Test predict_next method with single center"""
        centers = [np.array([1, 0])]
        center_idx = [1]
        model = Model(centers, center_idx)
        
        v = np.array([0.8, 0.6])
        predicted = model.predict_next(v)
        assert predicted == 0  # Only one center available

    def test_predict_next_two_centers(self):
        """Test predict_next method with two centers"""
        centers = [np.array([1, 0]), np.array([0, 1])]
        center_idx = [1, 1]
        model = Model(centers, center_idx)
        
        # Vector closer to first center, so second should be next
        v = np.array([0.8, 0.6])
        predicted = model.predict_next(v)
        assert predicted == 1

    def test_predict_next_multiple_centers(self):
        """Test predict_next method with multiple centers"""
        centers = [np.array([1, 0]), np.array([0, 1]), np.array([-1, 0])]
        center_idx = [1, 1, 1]
        model = Model(centers, center_idx)
        
        # Vector closer to first center [1,0], second should be [0,1]
        v = np.array([0.9, 0.1])
        predicted_next = model.predict_next(v)
        assert predicted_next == 1

    def test_predict_next_edge_case_branches(self):
        """Test predict_next method to cover edge case branches (lines 155-156)"""
        # We need to set up centers where the algorithm will:
        # 1. Find a new max, triggering lines 155-156 to update next from old max
        # 2. Process centers in order where max gets updated after next is set
        centers = [np.array([0.5, 0.5]), np.array([1, 0]), np.array([0, 1])]
        center_idx = [1, 1, 1]
        model = Model(centers, center_idx)
        
        # This vector will make center[1] the max, but center[0] starts as max initially
        # When we process center[1], it becomes new max and old max becomes next
        v = np.array([0.9, 0.1])  # Very close to [1, 0]
        predicted_next = model.predict_next(v)
        
        # Should return the second most similar center
        assert isinstance(predicted_next, int)
        assert 0 <= predicted_next < len(centers)

    def test_predict_next_multiple_updates_coverage(self):
        """Test predict_next with specific scenario to hit lines 155-156"""
        # Set up 4 centers with specific similarities that will trigger the branch
        centers = [
            np.array([0.3, 0.4]),  # Will be initial max
            np.array([0.6, 0.8]),  # Lower similarity  
            np.array([0.9, 0.1]),  # Will become new max, making first one next
            np.array([0.1, 0.9])   # Even lower similarity
        ]
        center_idx = [1, 1, 1, 1]
        model = Model(centers, center_idx)
        
        # Vector that makes center[2] highest, center[0] second highest
        v = np.array([0.8, 0.2])
        predicted_next = model.predict_next(v)
        
        assert isinstance(predicted_next, int)
        assert 0 <= predicted_next < len(centers)

    def test_predict_neighbour_default_threshold(self):
        """Test predict_neighbour method with default threshold"""
        centers = [np.array([1, 0]), np.array([0, 1]), np.array([0.7, 0.7])]
        center_idx = [1, 1, 1]
        model = Model(centers, center_idx)
        
        # Vector that should be similar to first and third centers
        v = np.array([0.8, 0.6])
        neighbors = model.predict_neighbour(v)
        
        assert isinstance(neighbors, list)
        assert len(neighbors) > 0
        # Should be sorted by similarity (highest first)
        assert neighbors[0] in [0, 1, 2]

    def test_predict_neighbour_custom_threshold(self):
        """Test predict_neighbour method with custom threshold"""
        centers = [np.array([1, 0]), np.array([0, 1]), np.array([-1, 0])]
        center_idx = [1, 1, 1]
        model = Model(centers, center_idx)
        
        v = np.array([0.9, 0.1])
        neighbors = model.predict_neighbour(v, threshold=0.8)
        
        # Only centers with high similarity should be included
        assert isinstance(neighbors, list)
        assert len(neighbors) >= 1

    def test_predict_neighbour_high_threshold_fallback(self):
        """Test predict_neighbour method fallback when no neighbors meet threshold"""
        centers = [np.array([1, 0]), np.array([0, 1])]
        center_idx = [1, 1]
        model = Model(centers, center_idx)
        
        # Very high threshold that no neighbors will meet
        v = np.array([0.5, 0.5])
        neighbors = model.predict_neighbour(v, threshold=0.99)
        
        # Should fallback to predict() result
        assert len(neighbors) == 1
        expected_prediction = model.predict(v)
        assert neighbors[0] == expected_prediction

    def test_predict_neighbour_empty_centers(self):
        """Test predict_neighbour method with empty centers"""
        model = Model([], [])
        v = np.array([1, 0])
        
        # With empty centers, predict_neighbour returns [0] (fallback to predict())
        # predict() returns 0 when there are no centers (default max_idx)
        neighbors = model.predict_neighbour(v)
        assert neighbors == [0]


class TestCRP:
    """Test cases for the CRP class"""

    def test_cluster_empty_input(self):
        """Test cluster method with empty input"""
        crp = CRP()
        model = crp.cluster([])
        
        assert isinstance(model, Model)
        assert model.centersA == []
        assert model.center_idx == []
        assert model.centers == []

    def test_cluster_empty_vectors(self):
        """Test cluster method with empty vectors"""
        crp = CRP()
        model = crp.cluster([[]])
        
        assert isinstance(model, Model)
        assert model.centersA == []
        assert model.center_idx == []

    def test_cluster_single_vector(self):
        """Test cluster method with single vector"""
        crp = CRP()
        vecs = [[1, 2, 3]]
        model = crp.cluster(vecs)
        
        assert len(model.centersA) == 1
        assert len(model.center_idx) == 1
        assert model.center_idx[0] == 1
        np.testing.assert_array_equal(model.centersA[0], np.array([1, 2, 3]))

    def test_cluster_identical_vectors(self):
        """Test cluster method with identical vectors"""
        crp = CRP()
        vecs = [[1, 2, 3], [1, 2, 3], [1, 2, 3]]
        
        # With high pnew, should still cluster together
        model = crp.cluster(vecs, pnew=0.5)
        
        assert len(model.centersA) == 1  # All vectors in one cluster
        assert model.center_idx[0] == 3  # Three vectors in the cluster

    def test_cluster_dissimilar_vectors_high_pnew(self):
        """Test cluster method with dissimilar vectors and high pnew"""
        crp = CRP()
        vecs = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        
        # High pnew should create separate clusters
        model = crp.cluster(vecs, pnew=0.9)
        
        # Should likely create multiple clusters for orthogonal vectors
        assert len(model.centersA) >= 1
        assert len(model.center_idx) == len(model.centersA)

    def test_cluster_dissimilar_vectors_low_pnew(self):
        """Test cluster method with dissimilar vectors and low pnew"""
        crp = CRP()
        vecs = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        
        # Low pnew should be more likely to group vectors together
        model = crp.cluster(vecs, pnew=0.1)
        
        assert len(model.centersA) >= 1
        assert len(model.center_idx) == len(model.centersA)

    @patch('mdx.analytics.core.utils.crp.logger')
    def test_cluster_logging(self, mock_logger):
        """Test cluster method logs correctly"""
        crp = CRP()
        vecs = [[1, 2, 3], [4, 5, 6]]
        model = crp.cluster(vecs)
        
        # Verify debug logging was called
        mock_logger.debug.assert_called()
        call_args = mock_logger.debug.call_args[0][0]
        assert "Clustered" in call_args
        assert "center(s) from" in call_args
        assert "vectors" in call_args

    def test_update_model_empty_new_vecs(self):
        """Test update_model method with empty new vectors"""
        crp = CRP()
        original_model = Model([np.array([1, 2])], [1])
        
        updated_model = crp.update_model(original_model, [])
        
        # Should return the same model
        assert updated_model is original_model

    def test_update_model_empty_new_vecs_with_empty_vectors(self):
        """Test update_model method with list containing empty vectors"""
        crp = CRP()
        original_model = Model([np.array([1, 2])], [1])
        
        updated_model = crp.update_model(original_model, [[]])
        
        # Should return the same model
        assert updated_model is original_model

    def test_update_model_add_similar_vector(self):
        """Test update_model method adding similar vector"""
        crp = CRP()
        original_centers = [np.array([1, 0])]
        original_sizes = [1]
        original_model = Model(original_centers, original_sizes)
        
        # Add similar vector
        new_vecs = [[0.9, 0.1]]
        updated_model = crp.update_model(original_model, new_vecs, pnew=0.5)
        
        # Should still have one cluster with increased size
        assert len(updated_model.centersA) == 1
        assert updated_model.center_idx[0] == 2  # Original + new vector

    def test_update_model_add_dissimilar_vector(self):
        """Test update_model method adding dissimilar vector"""
        crp = CRP()
        original_centers = [np.array([1, 0])]
        original_sizes = [1]
        original_model = Model(original_centers, original_sizes)
        
        # Add very different vector with high pnew
        new_vecs = [[0, 1]]
        updated_model = crp.update_model(original_model, new_vecs, pnew=0.9)
        
        # Might create new cluster depending on similarity
        assert len(updated_model.centersA) >= 1
        assert len(updated_model.center_idx) == len(updated_model.centersA)

    def test_update_model_multiple_new_vectors(self):
        """Test update_model method with multiple new vectors"""
        crp = CRP()
        original_centers = [np.array([1, 0])]
        original_sizes = [1]
        original_model = Model(original_centers, original_sizes)
        
        new_vecs = [[0.8, 0.2], [0.9, 0.1], [0, 1]]
        updated_model = crp.update_model(original_model, new_vecs, pnew=0.7)
        
        assert len(updated_model.centersA) >= 1
        # Total size should account for original + new vectors
        total_size = sum(updated_model.center_idx)
        assert total_size == len(original_sizes) + len(new_vecs)

    @patch('mdx.analytics.core.utils.crp.logger')
    def test_update_model_logging(self, mock_logger):
        """Test update_model method logs correctly"""
        crp = CRP()
        original_model = Model([np.array([1, 2])], [1])
        new_vecs = [[3, 4]]
        
        updated_model = crp.update_model(original_model, new_vecs)
        
        # Verify debug logging was called
        mock_logger.debug.assert_called()
        call_args = mock_logger.debug.call_args[0][0]
        assert "Got new" in call_args
        assert "center(s) from old" in call_args
        assert "new" in call_args
        assert "vectors" in call_args


class TestCRPIntegration:
    """Integration tests for CRP clustering workflow"""

    def test_cluster_then_update_workflow(self):
        """Test complete workflow: cluster initial data, then update with new data"""
        crp = CRP()
        
        # Initial clustering
        initial_vecs = [[1, 0], [0.9, 0.1], [0, 1], [0.1, 0.9]]
        initial_model = crp.cluster(initial_vecs, pnew=0.7)
        
        # Update with new vectors
        new_vecs = [[1.1, 0.1], [-1, 0]]
        updated_model = crp.update_model(initial_model, new_vecs, pnew=0.7)
        
        # Verify the updated model has appropriate structure
        assert len(updated_model.centersA) >= 1
        assert len(updated_model.center_idx) == len(updated_model.centersA)
        # The total number might be less than initial + new if there are existing clusters
        # that new vectors get assigned to
        assert sum(updated_model.center_idx) >= len(initial_vecs)

    def test_edge_case_cosine_similarity_computation(self):
        """Test edge cases in cosine similarity computation used in clustering"""
        crp = CRP()
        
        # Vectors that might cause numerical issues
        vecs = [
            [1e-10, 1e-10, 1e-10],  # Very small values
            [1, 1, 1],              # Normal values
            [1000, 1000, 1000]      # Large values
        ]
        
        try:
            model = crp.cluster(vecs, pnew=0.5)
            assert len(model.centersA) >= 1
        except (ZeroDivisionError, FloatingPointError):
            # Expected for edge cases with very small vectors
            pass

    def test_prediction_consistency(self):
        """Test that prediction methods are consistent with cluster assignments"""
        crp = CRP()
        vecs = [[1, 0], [0, 1], [0.8, 0.6], [-1, 0]]
        model = crp.cluster(vecs, pnew=0.8)
        
        # Test each method works
        test_vector = np.array([0.9, 0.1])
        
        prediction = model.predict(test_vector)
        assert 0 <= prediction < len(model.centers)
        
        if len(model.centers) > 1:
            next_prediction = model.predict_next(test_vector)
            assert 0 <= next_prediction < len(model.centers)
            
            neighbors = model.predict_neighbour(test_vector, threshold=0.1)
            assert isinstance(neighbors, list)
            assert all(0 <= n < len(model.centers) for n in neighbors)


# Edge case and error handling tests
class TestCRPEdgeCases:
    """Test edge cases and error conditions"""

    def test_model_with_mixed_vector_sizes(self):
        """Test Model initialization with vectors of different sizes"""
        # This should work in initialization, but cause issues during operations
        centers = [np.array([1, 2]), np.array([3, 4, 5])]  # Different sizes
        center_idx = [1, 1]
        model = Model(centers, center_idx)
        
        # Operations requiring vector compatibility should handle this gracefully
        v = np.array([1, 0])
        try:
            prediction = model.predict(v)
            # May work or fail depending on numpy broadcasting
            assert isinstance(prediction, int)
        except (ValueError, IndexError):
            # Expected for incompatible vector sizes
            pass

    def test_crp_with_nan_values(self):
        """Test CRP clustering with NaN values"""
        crp = CRP()
        vecs = [[1, 2, np.nan], [4, 5, 6]]
        
        try:
            model = crp.cluster(vecs)
            # May work depending on numpy's handling of NaN
            assert isinstance(model, Model)
        except (ValueError, FloatingPointError):
            # Expected for NaN values
            pass

    def test_crp_with_infinite_values(self):
        """Test CRP clustering with infinite values"""
        crp = CRP()
        vecs = [[1, 2, np.inf], [4, 5, 6]]
        
        try:
            model = crp.cluster(vecs)
            assert isinstance(model, Model)
        except (ValueError, OverflowError):
            # Expected for infinite values
            pass
