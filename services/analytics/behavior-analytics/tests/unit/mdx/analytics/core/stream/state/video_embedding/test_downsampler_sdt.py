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
from mdx.analytics.core.stream.state.video_embedding.downsampling.downsampler_sdt import SDTEmbeddingDownsampler


class TestSDTEmbeddingDownsampler(unittest.TestCase):
    """
    Unit tests for SDT (Swinging Door Trending) embedding downsampler.
    
    Test Coverage:
    1. Basic resample and force_save functionality (lean)
    2. _should_save method algorithm tests (main focus):
       - Smooth trends
       - Sharp transitions
       - Constant values
       - Oscillating patterns
       - Edge cases (identical timestamps, zero vectors)
    3. Both tolerance modes (distance and cosine)
    4. Max interval override
    """

    def setUp(self):
        """Setup test configuration using EmbedFilteringConfig."""
        self.config_distance = VideoEmbeddingConfig(
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.15,
            downsample_max_interval_sec=300,
        )
        self.config_cosine = VideoEmbeddingConfig(
            downsample_tolerance_mode="cosine",
            downsample_similarity_threshold=0.91,
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

    def test_resample_first_point_always_saved(self):
        """Test that the first embedding is always saved as anchor."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
        ]
        
        results = downsampler.resample(embeddings)
        
        self.assertEqual(len(results), 1)
        self.assertIsNotNone(downsampler._anchor)
        self.assertIsNone(downsampler._candidate)

    def test_resample_second_point_becomes_candidate(self):
        """Test that the second point becomes candidate (not saved yet)."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
            self.create_embedding(2000, [0.9, 0.1, 0.0, 0.0]),
        ]
        
        results = downsampler.resample(embeddings)
        
        self.assertEqual(len(results), 1)  # Only first point saved
        self.assertIsNotNone(downsampler._anchor)
        self.assertIsNotNone(downsampler._candidate)

    def test_force_save_returns_candidate(self):
        """Test force_save returns pending candidate."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
            self.create_embedding(2000, [0.9, 0.1, 0.0, 0.0]),
        ]
        
        downsampler.resample(embeddings)
        forced = downsampler.force_save()
        
        self.assertIsNotNone(forced)
        self.assertIsNone(downsampler._candidate)  # Candidate cleared

    def test_force_save_returns_none_when_no_candidate(self):
        """Test force_save returns None when no pending candidate."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        forced = downsampler.force_save()
        self.assertIsNone(forced)

    # ==================== _should_save Algorithm Tests (Main Focus) ====================

    def test_smooth_linear_trend_distance_mode(self):
        """Test SDT skips points on smooth linear trend (distance mode)."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # Create linear progression: (1,0,0) -> (0.8, 0.2, 0) -> (0.6, 0.4, 0) -> (0.4, 0.6, 0)
        embeddings = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.9, 0.1, 0.0, 0.0])),
            self.create_embedding(3000, self.create_unit_vector([0.8, 0.2, 0.0, 0.0])),
            self.create_embedding(4000, self.create_unit_vector([0.7, 0.3, 0.0, 0.0])),
            self.create_embedding(5000, self.create_unit_vector([0.6, 0.4, 0.0, 0.0])),
        ]
        
        results = downsampler.resample(embeddings)
        
        # Should save: first point, potentially some intermediate due to interpolation check
        # Smooth trend should achieve good compression
        self.assertLess(len(results), len(embeddings))
        self.assertEqual(results[0], embeddings[0])  # First always saved

    def test_smooth_linear_trend_cosine_mode(self):
        """Test SDT skips points on smooth linear trend (cosine mode)."""
        downsampler = SDTEmbeddingDownsampler(self.config_cosine)
        
        embeddings = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.9, 0.1, 0.0, 0.0])),
            self.create_embedding(3000, self.create_unit_vector([0.8, 0.2, 0.0, 0.0])),
            self.create_embedding(4000, self.create_unit_vector([0.7, 0.3, 0.0, 0.0])),
            self.create_embedding(5000, self.create_unit_vector([0.6, 0.4, 0.0, 0.0])),
        ]
        
        results = downsampler.resample(embeddings)
        
        self.assertLess(len(results), len(embeddings))
        self.assertEqual(results[0], embeddings[0])

    def test_sharp_direction_change_detected(self):
        """Test SDT detects and saves sharp direction changes."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # Sharp turn: X-axis -> sudden jump to Y-axis
        embeddings = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.95, 0.05, 0.0, 0.0])),
            self.create_embedding(3000, self.create_unit_vector([0.0, 1.0, 0.0, 0.0])),  # Sharp turn
            self.create_embedding(4000, self.create_unit_vector([0.0, 0.95, 0.05, 0.0])),
        ]
        
        results = downsampler.resample(embeddings)
        
        # Should save the transition point
        self.assertGreaterEqual(len(results), 2)

    def test_constant_values_compressed(self):
        """Test SDT aggressively compresses constant/identical values."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # All same vector
        same_vector = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        embeddings = [
            self.create_embedding(1000 + i * 1000, same_vector) 
            for i in range(10)
        ]
        
        results = downsampler.resample(embeddings)
        
        # Should achieve maximum compression (only first point)
        self.assertEqual(len(results), 1)
        
        # Force save should return the pending candidate
        forced = downsampler.force_save()
        self.assertIsNotNone(forced)

    def test_oscillating_pattern_multiple_saves(self):
        """Test SDT detects oscillating pattern and saves transition points."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # Oscillate between two directions
        vec_a = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        vec_b = self.create_unit_vector([0.0, 1.0, 0.0, 0.0])
        
        embeddings = [
            self.create_embedding(1000, vec_a),
            self.create_embedding(2000, vec_b),
            self.create_embedding(3000, vec_a),
            self.create_embedding(4000, vec_b),
            self.create_embedding(5000, vec_a),
        ]
        
        results = downsampler.resample(embeddings)
        
        # Should detect transitions
        self.assertGreater(len(results), 1)

    def test_gradual_rotation_in_vector_space(self):
        """Test SDT handles gradual rotation with appropriate compression."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # Simulate circular motion (rotation in 2D plane of 4D space)
        angles = np.linspace(0, np.pi / 2, 20)  # 0 to 90 degrees
        embeddings = [
            self.create_embedding(
                1000 + i * 1000,
                self.create_unit_vector([np.cos(angle), np.sin(angle), 0.0, 0.0])
            )
            for i, angle in enumerate(angles)
        ]
        
        results = downsampler.resample(embeddings)
        
        # Should compress smooth rotation
        self.assertLess(len(results), len(embeddings))
        # Smooth rotation should achieve good compression (may only save start point)
        self.assertGreaterEqual(len(results), 1)

    def test_zigzag_pattern_saves_peaks(self):
        """Test SDT captures peak/valley points in zigzag pattern."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # Zigzag: up, down, up, down
        embeddings = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.7, 0.3, 0.0, 0.0])),
            self.create_embedding(3000, self.create_unit_vector([0.4, 0.6, 0.0, 0.0])),  # Peak
            self.create_embedding(4000, self.create_unit_vector([0.7, 0.3, 0.0, 0.0])),  # Valley
            self.create_embedding(5000, self.create_unit_vector([0.4, 0.6, 0.0, 0.0])),  # Peak
        ]
        
        results = downsampler.resample(embeddings)
        
        # Should detect direction changes at peaks/valleys
        self.assertGreater(len(results), 1)

    def test_within_tolerance_band_skipped_distance(self):
        """Test points within distance tolerance are skipped."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # Points that should be within tolerance (small deviations from line)
        embeddings = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.95, 0.05, 0.0, 0.0])),
            self.create_embedding(3000, self.create_unit_vector([0.90, 0.10, 0.0, 0.0])),
            self.create_embedding(4000, self.create_unit_vector([0.85, 0.15, 0.0, 0.0])),
        ]
        
        results = downsampler.resample(embeddings)
        
        # Middle points should be skipped if within tolerance
        self.assertLessEqual(len(results), 2)

    def test_outside_tolerance_triggers_save_distance(self):
        """Test points outside distance tolerance trigger save."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # Create points where middle deviates significantly from interpolated line
        embeddings = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.0, 1.0, 0.0, 0.0])),  # Perpendicular
            self.create_embedding(3000, self.create_unit_vector([0.0, 0.0, 1.0, 0.0])),  # Another direction
        ]
        
        results = downsampler.resample(embeddings)
        
        # Large deviations should trigger saves
        self.assertGreaterEqual(len(results), 2)

    def test_within_tolerance_band_skipped_cosine(self):
        """Test points within cosine tolerance are skipped."""
        downsampler = SDTEmbeddingDownsampler(self.config_cosine)
        
        embeddings = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.95, 0.05, 0.0, 0.0])),
            self.create_embedding(3000, self.create_unit_vector([0.90, 0.10, 0.0, 0.0])),
        ]
        
        results = downsampler.resample(embeddings)
        
        self.assertLessEqual(len(results), 2)

    def test_outside_tolerance_triggers_save_cosine(self):
        """Test points outside cosine tolerance trigger save."""
        downsampler = SDTEmbeddingDownsampler(self.config_cosine)
        
        embeddings = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.0, 1.0, 0.0, 0.0])),
            self.create_embedding(3000, self.create_unit_vector([0.0, 0.0, 1.0, 0.0])),
        ]
        
        results = downsampler.resample(embeddings)
        
        self.assertGreaterEqual(len(results), 2)

    # ==================== Edge Cases ====================

    def test_identical_timestamps_handled(self):
        """Test handling of identical timestamps (edge case in interpolation)."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # Same timestamp for multiple embeddings
        embeddings = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.9, 0.1, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.8, 0.2, 0.0, 0.0])),  # Same timestamp
        ]
        
        # Should not crash
        results = downsampler.resample(embeddings)
        self.assertGreaterEqual(len(results), 1)

    def test_zero_vector_handled(self):
        """Test handling of zero vectors (normalization edge case)."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
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
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # Create 512-dimensional vectors with smooth changes
        dim = 512
        base_vec = np.zeros(dim)
        base_vec[0] = 1.0
        
        embeddings = []
        for i in range(10):
            vec = base_vec.copy()
            vec[0] = 1.0 - i * 0.05
            vec[1] = i * 0.05
            embeddings.append(
                self.create_embedding(1000 + i * 1000, self.create_unit_vector(vec.tolist()))
            )
        
        results = downsampler.resample(embeddings)
        
        # Should compress high-dim smooth trend
        self.assertLess(len(results), len(embeddings))

    def test_very_small_time_intervals(self):
        """Test with very small time intervals between embeddings."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # Embeddings 10ms apart
        embeddings = [
            self.create_embedding(1000 + i * 10, self.create_unit_vector([1.0 - i * 0.1, i * 0.1, 0.0, 0.0]))
            for i in range(10)
        ]
        
        results = downsampler.resample(embeddings)
        
        self.assertGreaterEqual(len(results), 1)

    # ==================== Max Interval Override Tests ====================

    def test_max_interval_forces_save(self):
        """Test max interval override forces save regardless of tolerance."""
        config = VideoEmbeddingConfig(
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.15,
            downsample_max_interval_sec=2,
        )
        downsampler = SDTEmbeddingDownsampler(config)
        
        # Constant values with large time gap
        same_vector = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        embeddings = [
            self.create_embedding(1000, same_vector),
            self.create_embedding(2000, same_vector),
            self.create_embedding(5000, same_vector),  # 3 seconds from anchor (exceeds 2s limit)
        ]
        
        results = downsampler.resample(embeddings)
        
        # Should force save at max interval
        self.assertEqual(len(results), 2)  # First + forced save at 2000ms

    def test_max_interval_resets_anchor(self):
        """Test max interval save updates anchor correctly."""
        config = VideoEmbeddingConfig(
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.15,
            downsample_max_interval_sec=2,
        )
        downsampler = SDTEmbeddingDownsampler(config)
        
        same_vector = self.create_unit_vector([1.0, 0.0, 0.0, 0.0])
        embeddings = [
            self.create_embedding(1000, same_vector),
            self.create_embedding(2000, same_vector),
            self.create_embedding(5000, same_vector),  # Triggers max interval
            self.create_embedding(6000, same_vector),
        ]
        
        results = downsampler.resample(embeddings)
        
        # When max interval triggers at t=5000, it saves candidate (t=2000) and makes it anchor.
        # Then t=5000 becomes candidate. When t=6000 arrives, t=5000 gets saved too
        # (since all identical vectors don't deviate from interpolation).
        # So final state: anchor=5000, candidate=6000
        self.assertIsNotNone(downsampler._anchor)
        # Final anchor will be at 5000 after processing all embeddings
        self.assertEqual(downsampler._anchor[0], 5000)

    # ==================== State Persistence Tests ====================

    def test_state_persists_across_resample_calls(self):
        """Test that state (anchor, candidate) persists across multiple resample calls."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # First batch
        batch1 = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.9, 0.1, 0.0, 0.0])),
        ]
        
        results1 = downsampler.resample(batch1)
        self.assertEqual(len(results1), 1)
        
        # Second batch - state should persist
        batch2 = [
            self.create_embedding(3000, self.create_unit_vector([0.8, 0.2, 0.0, 0.0])),
            self.create_embedding(4000, self.create_unit_vector([0.7, 0.3, 0.0, 0.0])),
        ]
        
        results2 = downsampler.resample(batch2)
        
        # Should continue from previous state
        self.assertIsNotNone(downsampler._anchor)
        self.assertIsNotNone(downsampler._candidate)

    def test_empty_input_preserves_state(self):
        """Test empty input doesn't crash and preserves state."""
        downsampler = SDTEmbeddingDownsampler(self.config_distance)
        
        # Setup state
        embeddings = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.9, 0.1, 0.0, 0.0])),
        ]
        downsampler.resample(embeddings)
        
        anchor_before = downsampler._anchor
        candidate_before = downsampler._candidate
        
        # Process empty batch
        results = downsampler.resample([])
        
        # State should be preserved
        self.assertEqual(len(results), 0)
        self.assertEqual(downsampler._anchor, anchor_before)
        self.assertEqual(downsampler._candidate, candidate_before)


if __name__ == "__main__":
    unittest.main()
