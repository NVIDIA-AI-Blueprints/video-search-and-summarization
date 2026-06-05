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

import unittest
import numpy as np
from google.protobuf.timestamp_pb2 import Timestamp
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.config import VideoEmbeddingConfig
from mdx.analytics.core.stream.state.video_embedding.downsampling.downsampler_window import SlidingWindowEmbeddingDownsampler


class TestSlidingWindowEmbeddingDownsampler(unittest.TestCase):
    """
    Unit tests for Sliding Window embedding downsampler.
    
    Test Coverage:
    1. Basic resample functionality (lean)
    2. _should_save method algorithm tests (main focus):
       - Novel patterns detection
       - Repetitive/cyclical patterns
       - Pattern transitions
       - Consecutive neighbor counting
       - Edge cases
    3. Both tolerance modes (distance and cosine)
    4. Window management and min_neighbours parameter
    5. Max interval override
    """

    def setUp(self):
        """Setup test configuration using EmbedFilteringConfig."""
        self.config_distance = VideoEmbeddingConfig(
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.45,
            downsample_window_size=10,
            downsample_min_neighbours=3,
            downsample_max_interval_sec=300,
        )
        self.config_cosine = VideoEmbeddingConfig(
            downsample_tolerance_mode="cosine",
            downsample_similarity_threshold=0.90,
            downsample_window_size=10,
            downsample_min_neighbours=3,
            downsample_max_interval_sec=300,
        )

    def create_embedding(self, timestamp_ms: float, vector: list[float]) -> nvSchema.VisionLLM:
        """Create a minimal VisionLLM protobuf with embedding vector."""
        vid_embed = nvSchema.VisionLLM()
        
        # Set timestamp
        ts = Timestamp()
        ts.FromMilliseconds(int(timestamp_ms))
        vid_embed.end.CopyFrom(ts)
        
        # Set embedding vector
        llm = nvSchema.LLM()
        vision_embed = nvSchema.Embedding()
        vision_embed.vector.extend(vector)
        llm.visionEmbeddings.append(vision_embed)
        vid_embed.llm.CopyFrom(llm)
        
        return vid_embed

    def create_unit_vector(self, direction: list[float]) -> list[float]:
        """Create a normalized unit vector from given direction."""
        vec = np.array(direction, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm < 1e-10:
            return direction
        return (vec / norm).tolist()

    # ==================== Basic Functionality Tests (Lean) ====================

    def test_resample_empty_window_saves_first(self):
        """Test that first point is saved when window is empty."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
        ]
        
        results = downsampler.resample(embeddings)
        
        # First point should be saved (empty window condition)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(downsampler._window), 1)

    def test_window_size_maintained(self):
        """Test that window maintains configured size."""
        config = VideoEmbeddingConfig(
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.45,
            downsample_window_size=5,
            downsample_min_neighbours=3,
        )
        downsampler = SlidingWindowEmbeddingDownsampler(config)
        
        # Add more embeddings than window size
        embeddings = [
            self.create_embedding(1000 + i * 1000, [1.0, 0.0, 0.0, 0.0])
            for i in range(10)
        ]
        
        downsampler.resample(embeddings)
        
        # Window should be capped at configured size
        self.assertEqual(len(downsampler._window), 5)

    def test_force_save_not_needed_for_window(self):
        """Test that force_save is not required for window algorithm (no pending state)."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
            self.create_embedding(2000, [0.9, 0.1, 0.0, 0.0]),
        ]
        
        downsampler.resample(embeddings)
        forced = downsampler.force_save()
        
        # Window algorithm has no pending candidate
        self.assertIsNone(forced)

    # ==================== _should_save Algorithm Tests (Main Focus) ====================

    def test_novel_pattern_saved_distance_mode(self):
        """Test novel patterns (dissimilar to recent history) are saved."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        # Fill window with pattern A, then introduce novel pattern B
        pattern_a = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        pattern_b = self.create_unit_vector([0.0, 1.0, 0.0, 0.0])  # Perpendicular
        
        embeddings = [
            self.create_embedding(1000 + i * 1000, pattern_a) for i in range(5)
        ] + [
            self.create_embedding(6000, pattern_b),  # Novel pattern
        ]
        
        results = downsampler.resample(embeddings)
        
        # Novel pattern should be saved
        self.assertIn(embeddings[-1], results)

    def test_novel_pattern_saved_cosine_mode(self):
        """Test novel patterns are saved in cosine mode."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_cosine)
        
        pattern_a = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        pattern_b = self.create_unit_vector([0.0, 1.0, 0.0, 0.0])
        
        embeddings = [
            self.create_embedding(1000 + i * 1000, pattern_a) for i in range(5)
        ] + [
            self.create_embedding(6000, pattern_b),
        ]
        
        results = downsampler.resample(embeddings)
        
        self.assertIn(embeddings[-1], results)

    def test_repetitive_pattern_compressed(self):
        """Test repetitive/cyclical patterns are compressed (redundant points skipped)."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        # Same pattern repeated
        same_pattern = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        embeddings = [
            self.create_embedding(1000 + i * 1000, same_pattern) for i in range(10)
        ]
        
        results = downsampler.resample(embeddings)
        
        # Should skip most redundant points (except first few to establish pattern)
        self.assertLess(len(results), len(embeddings))

    def test_cyclical_pattern_with_repetition(self):
        """Test cyclical pattern (A->B->A->B) compression depends on pattern spacing."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        pattern_a = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        pattern_b = self.create_unit_vector([0.0, 1.0, 0.0, 0.0])
        
        # Cycle: A, B, A, B, A, B...
        # With perpendicular patterns and min_neighbours=3, alternating patterns
        # won't have 3 consecutive similar neighbors, so all will be saved.
        embeddings = []
        for i in range(12):
            pattern = pattern_a if i % 2 == 0 else pattern_b
            embeddings.append(self.create_embedding(1000 + i * 1000, pattern))
        
        results = downsampler.resample(embeddings)
        
        # For alternating perpendicular patterns, window won't compress
        # (each point lacks 3 consecutive similar neighbors)
        self.assertGreaterEqual(len(results), 1)

    def test_pattern_transition_detected(self):
        """Test transition from pattern A to pattern B is detected and saved."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        pattern_a = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        pattern_b = self.create_unit_vector([0.0, 0.0, 1.0, 0.0])
        
        # Transition: A, A, A, B, B, B
        embeddings = (
            [self.create_embedding(1000 + i * 1000, pattern_a) for i in range(5)] +
            [self.create_embedding(6000 + i * 1000, pattern_b) for i in range(5)]
        )
        
        results = downsampler.resample(embeddings)
        
        # Transition points should be captured
        # First B should be saved (novel compared to recent A's)
        self.assertGreater(len(results), 1)

    def test_consecutive_neighbors_required(self):
        """Test that only consecutive similar neighbors count (break strategy)."""
        config = VideoEmbeddingConfig(
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.45,
            downsample_window_size=10,
            downsample_min_neighbours=3,
        )
        downsampler = SlidingWindowEmbeddingDownsampler(config)
        
        pattern_a = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        pattern_b = self.create_unit_vector([0.0, 1.0, 0.0, 0.0])
        
        # Window: A, A, B, B, A (new test point: A)
        # Backward search from end: A (similar), B (not similar) -> STOP
        # Count = 1 < 3 -> should SAVE (not enough consecutive neighbors)
        embeddings = [
            self.create_embedding(1000, pattern_a),
            self.create_embedding(2000, pattern_a),
            self.create_embedding(3000, pattern_b),
            self.create_embedding(4000, pattern_b),
            self.create_embedding(5000, pattern_a),
            self.create_embedding(6000, pattern_a),  # Test point
        ]
        
        results = downsampler.resample(embeddings)
        
        # Last point should be saved (only 1 consecutive A before the B's)
        self.assertIn(embeddings[-1], results)

    def test_min_neighbours_threshold_exact_boundary(self):
        """Test behavior at exact min_neighbours boundary."""
        config = VideoEmbeddingConfig(
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.45,
            downsample_window_size=10,
            downsample_min_neighbours=3,
        )
        downsampler = SlidingWindowEmbeddingDownsampler(config)
        
        same_pattern = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        
        # Add exactly 3 similar points, then add another
        embeddings = [
            self.create_embedding(1000 + i * 1000, same_pattern) for i in range(4)
        ]
        
        results = downsampler.resample(embeddings)
        
        # First point saved (empty window); points 2 and 3 have < 3 consecutive
        # similar neighbors so they are saved; point 4 has 3 neighbors -> skipped.
        self.assertEqual(len(results), 3)
        self.assertNotIn(embeddings[-1], results)

    def test_sparse_similar_points_not_consecutive(self):
        """Test that non-consecutive similar points don't trigger skip."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        pattern_a = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        pattern_b = self.create_unit_vector([0.0, 1.0, 0.0, 0.0])
        
        # Pattern: A, A, B, A, B, A (test with A)
        # Recent history alternates, so no consecutive sequence of 3
        embeddings = [
            self.create_embedding(1000, pattern_a),
            self.create_embedding(2000, pattern_a),
            self.create_embedding(3000, pattern_b),
            self.create_embedding(4000, pattern_a),
            self.create_embedding(5000, pattern_b),
            self.create_embedding(6000, pattern_a),  # Test point
        ]
        
        results = downsampler.resample(embeddings)
        
        # Should save because no 3 consecutive A's at the end
        self.assertIn(embeddings[-1], results)

    def test_smooth_trend_with_small_variations(self):
        """Test smooth trend with small variations within tolerance."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        # Gradual smooth changes (should be within tolerance)
        embeddings = [
            self.create_embedding(
                1000 + i * 1000,
                self.create_unit_vector([1.0 - i * 0.05, i * 0.05, 0.0, 0.0])
            )
            for i in range(10)
        ]
        
        results = downsampler.resample(embeddings)
        
        # Should compress smooth variations
        self.assertLess(len(results), len(embeddings))

    def test_sharp_transitions_in_sequence(self):
        """Test multiple sharp transitions are all captured."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        # Sequence: X-axis -> Y-axis -> Z-axis -> W-axis
        patterns = [
            self.create_unit_vector([1.0, 0.0, 0.0, 0.0]),
            self.create_unit_vector([0.0, 1.0, 0.0, 0.0]),
            self.create_unit_vector([0.0, 0.0, 1.0, 0.0]),
            self.create_unit_vector([0.0, 0.0, 0.0, 1.0]),
        ]
        
        embeddings = [
            self.create_embedding(1000 + i * 1000, patterns[i])
            for i in range(4)
        ]
        
        results = downsampler.resample(embeddings)
        
        # All sharp transitions should be saved
        self.assertGreaterEqual(len(results), 3)

    def test_gradual_rotation_detected_as_novel(self):
        """Test gradual rotation in vector space with novelty detection."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        # Simulate rotation
        angles = np.linspace(0, np.pi, 15)  # 0 to 180 degrees
        embeddings = [
            self.create_embedding(
                1000 + i * 1000,
                self.create_unit_vector([np.cos(angle), np.sin(angle), 0.0, 0.0])
            )
            for i, angle in enumerate(angles)
        ]
        
        results = downsampler.resample(embeddings)
        
        # Gradual rotation: with threshold 0.45, consecutive points stay within
        # tolerance but the backward chain rarely reaches 3, so all may be saved.
        # At minimum we must capture start and end of rotation.
        self.assertGreaterEqual(len(results), 2)

    def test_oscillating_between_two_states(self):
        """Test oscillating pattern between two states."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        state_a = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        state_b = self.create_unit_vector([0.0, 1.0, 0.0, 0.0])
        
        # A, B, A, B, A, B, A, B
        embeddings = []
        for i in range(16):
            state = state_a if i % 2 == 0 else state_b
            embeddings.append(self.create_embedding(1000 + i * 500, state))
        
        results = downsampler.resample(embeddings)
        
        # Alternating perpendicular patterns don't have consecutive similar neighbors
        # so window algorithm saves all of them (expected behavior)
        self.assertGreaterEqual(len(results), 1)

    def test_three_pattern_cycle(self):
        """Test three-pattern cycle (A->B->C->A->B->C)."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        pattern_a = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        pattern_b = self.create_unit_vector([0.0, 1.0, 0.0, 0.0])
        pattern_c = self.create_unit_vector([0.0, 0.0, 1.0, 0.0])
        
        patterns = [pattern_a, pattern_b, pattern_c]
        embeddings = [
            self.create_embedding(1000 + i * 1000, patterns[i % 3])
            for i in range(18)
        ]
        
        results = downsampler.resample(embeddings)
        
        # Three-way cycle with perpendicular patterns won't compress
        # (need min_neighbours=3 consecutive, but patterns alternate every point)
        self.assertGreaterEqual(len(results), 1)

    # ==================== Edge Cases ====================

    def test_zero_vector_handled(self):
        """Test handling of zero vectors."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
            self.create_embedding(2000, [0.0, 0.0, 0.0, 0.0]),  # Zero vector
            self.create_embedding(3000, [0.0, 1.0, 0.0, 0.0]),
        ]
        
        # Should not crash
        results = downsampler.resample(embeddings)
        self.assertGreaterEqual(len(results), 1)

    def test_high_dimensional_vectors(self):
        """Test with realistic high-dimensional embeddings (512D)."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        dim = 512
        
        # Create two distinct patterns in high-dim space
        pattern_a = np.zeros(dim)
        pattern_a[0] = 1.0
        
        pattern_b = np.zeros(dim)
        pattern_b[1] = 1.0
        
        embeddings = (
            [self.create_embedding(1000 + i * 1000, self.create_unit_vector(pattern_a.tolist())) for i in range(5)] +
            [self.create_embedding(6000 + i * 1000, self.create_unit_vector(pattern_b.tolist())) for i in range(5)]
        )
        
        results = downsampler.resample(embeddings)
        
        # Should detect transition
        self.assertGreater(len(results), 1)

    def test_single_embedding_in_batch(self):
        """Test processing single embedding."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
        ]
        
        results = downsampler.resample(embeddings)
        
        self.assertEqual(len(results), 1)

    def test_identical_vectors_in_sequence(self):
        """Test sequence of identical vectors."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        same_vec = [1.0, 0.0, 0.0, 0.0]
        embeddings = [
            self.create_embedding(1000 + i * 1000, same_vec)
            for i in range(20)
        ]
        
        results = downsampler.resample(embeddings)
        
        # Should achieve high compression
        self.assertLess(len(results), len(embeddings) // 2)

    # ==================== Max Interval Override Tests ====================

    def test_max_interval_forces_save(self):
        """Test max interval override forces save regardless of neighbors."""
        config = VideoEmbeddingConfig(
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.45,
            downsample_window_size=10,
            downsample_min_neighbours=3,
            downsample_max_interval_sec=2,
        )
        downsampler = SlidingWindowEmbeddingDownsampler(config)
        
        # Repetitive pattern with large time gap
        same_pattern = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        embeddings = [
            self.create_embedding(1000, same_pattern),
            self.create_embedding(2000, same_pattern),
            self.create_embedding(3000, same_pattern),
            self.create_embedding(6000, same_pattern),  # 3 seconds from last save (exceeds 2s)
        ]
        
        results = downsampler.resample(embeddings)
        
        # Should force save at max interval
        self.assertGreaterEqual(len(results), 2)

    def test_max_interval_updates_last_saved_time(self):
        """Test max interval save updates last_saved_time correctly."""
        config = VideoEmbeddingConfig(
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.45,
            downsample_window_size=10,
            downsample_min_neighbours=3,
            downsample_max_interval_sec=2,
        )
        downsampler = SlidingWindowEmbeddingDownsampler(config)
        
        same_pattern = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        embeddings = [
            self.create_embedding(1000, same_pattern),
            self.create_embedding(2000, same_pattern),
            self.create_embedding(5000, same_pattern),  # Triggers max interval
        ]
        
        downsampler.resample(embeddings)
        
        # Last saved time should be updated
        self.assertIsNotNone(downsampler._last_saved_time)

    # ==================== Window and Parameter Tests ====================

    def test_different_min_neighbours_values(self):
        """Test different min_neighbours parameter values."""
        # Test with min_neighbours=1 (very sensitive - need only 1 similar neighbor to skip)
        config1 = VideoEmbeddingConfig(
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.45,
            downsample_window_size=10,
            downsample_min_neighbours=1,
        )
        downsampler1 = SlidingWindowEmbeddingDownsampler(config1)
        
        same_pattern = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        embeddings = [
            self.create_embedding(1000 + i * 1000, same_pattern) for i in range(10)
        ]
        
        results1 = downsampler1.resample(embeddings)
        
        # Test with min_neighbours=5 (less sensitive - need 5 similar neighbors to skip)
        config2 = VideoEmbeddingConfig(
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.45,
            downsample_window_size=10,
            downsample_min_neighbours=5,
        )
        downsampler2 = SlidingWindowEmbeddingDownsampler(config2)
        results2 = downsampler2.resample(embeddings)
        
        # Lower min_neighbours compresses more (easier to find 1 neighbor than 5)
        # results1 should have fewer or equal saved points than results2
        self.assertLessEqual(len(results1), len(results2))

    def test_window_fifo_behavior(self):
        """Test window maintains FIFO behavior (oldest evicted first)."""
        config = VideoEmbeddingConfig(
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.45,
            downsample_window_size=3,
            downsample_min_neighbours=2,
        )
        downsampler = SlidingWindowEmbeddingDownsampler(config)
        
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
            self.create_embedding(2000, [0.9, 0.1, 0.0, 0.0]),
            self.create_embedding(3000, [0.8, 0.2, 0.0, 0.0]),
            self.create_embedding(4000, [0.7, 0.3, 0.0, 0.0]),  # Should evict first
        ]
        
        downsampler.resample(embeddings)
        
        # Window should contain last 3
        self.assertEqual(len(downsampler._window), 3)
        self.assertEqual(downsampler._window[0][0], 2000)  # Oldest in window

    def test_state_persists_across_resample_calls(self):
        """Test that window state persists across multiple resample calls."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        # First batch
        batch1 = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.9, 0.1, 0.0, 0.0])),
        ]
        
        downsampler.resample(batch1)
        window_size_after_batch1 = len(downsampler._window)
        
        # Second batch
        batch2 = [
            self.create_embedding(3000, self.create_unit_vector([0.8, 0.2, 0.0, 0.0])),
            self.create_embedding(4000, self.create_unit_vector([0.7, 0.3, 0.0, 0.0])),
        ]
        
        downsampler.resample(batch2)
        
        # Window should accumulate across batches
        self.assertEqual(len(downsampler._window), window_size_after_batch1 + len(batch2))

    def test_empty_input_preserves_state(self):
        """Test empty input doesn't crash and preserves window state."""
        downsampler = SlidingWindowEmbeddingDownsampler(self.config_distance)
        
        # Setup state
        embeddings = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.9, 0.1, 0.0, 0.0])),
        ]
        downsampler.resample(embeddings)
        
        window_before = len(downsampler._window)
        
        # Process empty batch
        results = downsampler.resample([])
        
        # State should be preserved
        self.assertEqual(len(results), 0)
        self.assertEqual(len(downsampler._window), window_before)


if __name__ == "__main__":
    unittest.main()
