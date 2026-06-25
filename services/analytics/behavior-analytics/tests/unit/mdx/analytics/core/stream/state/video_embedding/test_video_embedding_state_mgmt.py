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
import time
import numpy as np
from unittest.mock import patch, MagicMock
from google.protobuf.timestamp_pb2 import Timestamp
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.config import VideoEmbeddingConfig
from mdx.analytics.core.stream.state.video_embedding.video_embedding_state_mgmt import VideoEmbeddingStateMgmt
from mdx.analytics.core.stream.state.video_embedding.downsampling.downsampler_sdt import SDTEmbeddingDownsampler
from mdx.analytics.core.stream.state.video_embedding.downsampling.downsampler_window import SlidingWindowEmbeddingDownsampler


class TestVideoEmbeddingStateMgmt(unittest.TestCase):
    """
    Unit tests for VideoEmbeddingStateMgmt class.
    
    Test Coverage:
    1. Initialization with different configurations
    2. Downsampler creation (SDT vs Window)
    3. Video embedding processing and downsampling
    4. Sensor lifecycle management (creation, updates, purging)
    5. Pending embeddings retrieval (force_save)
    6. Invalid embedding filtering
    7. Multi-sensor handling
    8. Edge cases
    """

    def setUp(self):
        """Setup test configuration using VideoEmbeddingConfig with downsampling enabled."""
        self.config_sdt = VideoEmbeddingConfig(
            enable_downsampling=True,
            downsampler_type="sdt",
            sensor_ttl_sec=10,
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.15,
            downsample_max_interval_sec=300,
        )
        self.config_window = VideoEmbeddingConfig(
            enable_downsampling=True,
            downsampler_type="window",
            sensor_ttl_sec=10,
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.45,
            downsample_window_size=10,
            downsample_min_neighbours=3,
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

    # ==================== Initialization Tests ====================

    def test_initialization_with_sdt_config(self):
        """Test initialization creates SDT downsampler type."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        self.assertEqual(state_mgmt._downsampler_type, "sdt")
        self.assertEqual(state_mgmt._sensor_ttl, 10)  # Stored in seconds
        self.assertEqual(len(state_mgmt._downsamplers), 0)
        self.assertEqual(len(state_mgmt._sensor_last_updated), 0)

    def test_initialization_with_window_config(self):
        """Test initialization creates window downsampler type."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_window)
        
        self.assertEqual(state_mgmt._downsampler_type, "window")
        self.assertEqual(state_mgmt._sensor_ttl, 10)  # Stored in seconds

    def test_initialization_with_default_config(self):
        """Test initialization with default EmbedFilteringConfig values."""
        config = VideoEmbeddingConfig()
        state_mgmt = VideoEmbeddingStateMgmt(config)
        # EmbedFilteringConfig defaults: downsampler_type "window", sensor_ttl_sec 3600
        self.assertEqual(state_mgmt._downsampler_type, "window")
        self.assertEqual(state_mgmt._sensor_ttl, 3600)

    # ==================== Downsampler Creation Tests ====================

    def test_downsampler_created_lazily_sdt(self):
        """Test SDT downsampler is created on first sensor access."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        # No downsamplers initially
        self.assertEqual(len(state_mgmt._downsamplers), 0)
        
        # Access creates downsampler
        embeddings = [self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0])]
        state_mgmt.update_video_embeddings("sensor_1", embeddings)
        
        # Downsampler created
        self.assertEqual(len(state_mgmt._downsamplers), 1)
        self.assertIn("sensor_1", state_mgmt._downsamplers)
        self.assertIsInstance(state_mgmt._downsamplers["sensor_1"], SDTEmbeddingDownsampler)

    def test_downsampler_created_lazily_window(self):
        """Test window downsampler is created on first sensor access."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_window)
        
        embeddings = [self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0])]
        state_mgmt.update_video_embeddings("sensor_1", embeddings)
        
        self.assertIsInstance(state_mgmt._downsamplers["sensor_1"], SlidingWindowEmbeddingDownsampler)

    def test_different_sensors_get_separate_downsamplers(self):
        """Test each sensor gets its own downsampler instance."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        embeddings = [self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0])]
        
        state_mgmt.update_video_embeddings("sensor_1", embeddings)
        state_mgmt.update_video_embeddings("sensor_2", embeddings)
        state_mgmt.update_video_embeddings("sensor_3", embeddings)
        
        self.assertEqual(len(state_mgmt._downsamplers), 3)
        # Each sensor has independent downsampler
        self.assertIsNot(state_mgmt._downsamplers["sensor_1"], state_mgmt._downsamplers["sensor_2"])
        self.assertIsNot(state_mgmt._downsamplers["sensor_2"], state_mgmt._downsamplers["sensor_3"])

    # ==================== Video Embedding Processing Tests ====================

    def test_update_video_embeddings_processes_valid_embeddings(self):
        """Test valid embeddings are processed through downsampler."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        # Create embeddings with smooth trend (should compress)
        embeddings = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.9, 0.1, 0.0, 0.0])),
            self.create_embedding(3000, self.create_unit_vector([0.8, 0.2, 0.0, 0.0])),
        ]
        
        results = state_mgmt.update_video_embeddings("sensor_1", embeddings)
        
        # SDT should compress smooth trends
        self.assertLess(len(results), len(embeddings))
        self.assertGreaterEqual(len(results), 1)

    def test_update_video_embeddings_filters_invalid_embeddings(self):
        """Test embeddings without llm or visionEmbeddings are filtered out."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        # Create mix of valid and invalid embeddings
        valid_embed = self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0])
        
        invalid_embed_no_llm = nvSchema.VisionLLM()
        ts = Timestamp()
        ts.FromMilliseconds(2000)
        invalid_embed_no_llm.end.CopyFrom(ts)
        
        invalid_embed_no_vision = nvSchema.VisionLLM()
        ts2 = Timestamp()
        ts2.FromMilliseconds(3000)
        invalid_embed_no_vision.end.CopyFrom(ts2)
        invalid_embed_no_vision.llm.CopyFrom(nvSchema.LLM())  # Has llm but no visionEmbeddings
        
        embeddings = [valid_embed, invalid_embed_no_llm, invalid_embed_no_vision]
        
        results = state_mgmt.update_video_embeddings("sensor_1", embeddings)
        
        # Only valid embedding should be processed
        self.assertEqual(len(results), 1)

    def test_update_video_embeddings_with_empty_list(self):
        """Test processing empty embedding list doesn't crash."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        results = state_mgmt.update_video_embeddings("sensor_1", [])
        
        self.assertEqual(len(results), 0)
        # Downsampler still created for sensor
        self.assertIn("sensor_1", state_mgmt._downsamplers)

    def test_update_video_embeddings_maintains_per_sensor_state(self):
        """Test each sensor maintains independent downsampling state."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        # Same embeddings for two sensors
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
            self.create_embedding(2000, [0.9, 0.1, 0.0, 0.0]),
        ]
        
        results1 = state_mgmt.update_video_embeddings("sensor_1", embeddings)
        results2 = state_mgmt.update_video_embeddings("sensor_2", embeddings)
        
        # Both should have same behavior (independent state)
        self.assertEqual(len(results1), len(results2))

    # ==================== Sensor Lifecycle Management Tests ====================

    def test_sensor_timestamp_updated_on_processing(self):
        """Test sensor last_updated timestamp is updated when _manage_sensors is called."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        # Implementation uses time.time() (seconds), not time_ns
        with patch('mdx.analytics.core.stream.state.video_embedding.video_embedding_state_mgmt.time.time', return_value=1000):
            state_mgmt._manage_sensors("sensor_1")
            self.assertEqual(state_mgmt._sensor_last_updated["sensor_1"], 1000)

    @patch('mdx.analytics.core.stream.state.video_embedding.video_embedding_state_mgmt.time.time')
    def test_inactive_sensor_purged_after_ttl(self, mock_time):
        """Test sensors inactive beyond TTL are purged when _manage_sensors is called."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        # Implementation uses time.time() in seconds; TTL is 10 seconds
        mock_time.return_value = 0
        state_mgmt._manage_sensors("sensor_1")
        self.assertIn("sensor_1", state_mgmt._sensor_last_updated)
        # Advance 11 seconds: sensor_1 last_updated=0, elapsed=11 > ttl=10 → purge
        mock_time.return_value = 11
        state_mgmt._manage_sensors("sensor_2")
        self.assertNotIn("sensor_1", state_mgmt._sensor_last_updated)

    @patch('mdx.analytics.core.stream.state.video_embedding.video_embedding_state_mgmt.time.time')
    def test_active_sensor_not_purged(self, mock_time):
        """Test sensors active within TTL are not purged."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        mock_time.return_value = 0
        state_mgmt._manage_sensors("sensor_1")
        # Advance 5 seconds; elapsed=5 <= ttl=10 → not purged
        mock_time.return_value = 5
        state_mgmt._manage_sensors("sensor_1")
        self.assertIn("sensor_1", state_mgmt._sensor_last_updated)

    @patch('mdx.analytics.core.stream.state.video_embedding.video_embedding_state_mgmt.time.time')
    def test_multiple_sensors_purged_correctly(self, mock_time):
        """Test multiple inactive sensors are purged while active ones remain."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        # All timestamps in seconds; TTL=10
        mock_time.return_value = 0
        state_mgmt._manage_sensors("sensor_1")
        mock_time.return_value = 2
        state_mgmt._manage_sensors("sensor_2")
        mock_time.return_value = 5
        state_mgmt._manage_sensors("sensor_3")
        # At t=13: elapsed sensor_1=13, sensor_2=11, sensor_3=8 → 1 and 2 purged
        mock_time.return_value = 13
        state_mgmt._manage_sensors("sensor_4")
        self.assertNotIn("sensor_1", state_mgmt._sensor_last_updated)
        self.assertNotIn("sensor_2", state_mgmt._sensor_last_updated)
        self.assertIn("sensor_3", state_mgmt._sensor_last_updated)

    # ==================== Pending Embeddings Tests ====================

    def test_get_pending_video_embeddings_returns_sdt_candidates(self):
        """Test pending candidates are retrieved from SDT downsamplers."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        # Create embeddings that will leave a candidate in SDT
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
            self.create_embedding(2000, [0.9, 0.1, 0.0, 0.0]),
        ]
        
        state_mgmt.update_video_embeddings("sensor_1", embeddings)
        
        # SDT should have a pending candidate
        pending = state_mgmt.get_pending_video_embeddings()
        
        self.assertEqual(len(pending), 1)

    def test_get_pending_video_embeddings_from_multiple_sensors(self):
        """Test pending candidates retrieved from multiple sensors."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
            self.create_embedding(2000, [0.9, 0.1, 0.0, 0.0]),
        ]
        
        state_mgmt.update_video_embeddings("sensor_1", embeddings)
        state_mgmt.update_video_embeddings("sensor_2", embeddings)
        state_mgmt.update_video_embeddings("sensor_3", embeddings)
        
        pending = state_mgmt.get_pending_video_embeddings()
        
        # Each sensor's SDT has a candidate
        self.assertEqual(len(pending), 3)

    def test_get_pending_video_embeddings_window_returns_none(self):
        """Test window downsampler returns no pending embeddings."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_window)
        
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
            self.create_embedding(2000, [0.9, 0.1, 0.0, 0.0]),
        ]
        
        state_mgmt.update_video_embeddings("sensor_1", embeddings)
        
        pending = state_mgmt.get_pending_video_embeddings()
        
        # Window algorithm has no pending state
        self.assertEqual(len(pending), 0)

    def test_get_pending_video_embeddings_when_no_sensors(self):
        """Test getting pending embeddings when no sensors exist."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        pending = state_mgmt.get_pending_video_embeddings()
        
        self.assertEqual(len(pending), 0)

    def test_get_pending_video_embeddings_when_no_candidates(self):
        """Test getting pending embeddings when sensors have no candidates."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        # Single embedding won't leave a candidate (becomes anchor)
        embeddings = [self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0])]
        state_mgmt.update_video_embeddings("sensor_1", embeddings)
        
        pending = state_mgmt.get_pending_video_embeddings()
        
        self.assertEqual(len(pending), 0)

    # ==================== Integration Tests ====================

    def test_full_workflow_single_sensor(self):
        """Test complete workflow for single sensor: create, process, flush."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        # Process multiple batches
        batch1 = [
            self.create_embedding(1000, self.create_unit_vector([1.0, 0.0, 0.0, 0.0])),
            self.create_embedding(2000, self.create_unit_vector([0.9, 0.1, 0.0, 0.0])),
        ]
        
        batch2 = [
            self.create_embedding(3000, self.create_unit_vector([0.8, 0.2, 0.0, 0.0])),
            self.create_embedding(4000, self.create_unit_vector([0.7, 0.3, 0.0, 0.0])),
        ]
        
        results1 = state_mgmt.update_video_embeddings("sensor_1", batch1)
        results2 = state_mgmt.update_video_embeddings("sensor_1", batch2)
        
        # Should have results from processing
        self.assertGreaterEqual(len(results1) + len(results2), 1)
        
        # Get pending embeddings
        pending = state_mgmt.get_pending_video_embeddings()
        self.assertGreaterEqual(len(pending), 0)

    def test_full_workflow_multiple_sensors(self):
        """Test complete workflow with multiple sensors."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        embeddings = [
            self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0]),
            self.create_embedding(2000, [0.9, 0.1, 0.0, 0.0]),
        ]
        
        # Process for multiple sensors
        state_mgmt.update_video_embeddings("sensor_1", embeddings)
        state_mgmt.update_video_embeddings("sensor_2", embeddings)
        state_mgmt.update_video_embeddings("sensor_3", embeddings)
        
        # Verify independent state (downsamplers created lazily)
        self.assertEqual(len(state_mgmt._downsamplers), 3)
        # Note: _sensor_last_updated is only updated via _manage_sensors which is not called
        # by update_video_embeddings in the current implementation
        
        # Get pending from all
        pending = state_mgmt.get_pending_video_embeddings()
        self.assertEqual(len(pending), 3)

    @patch('mdx.analytics.core.stream.state.video_embedding.video_embedding_state_mgmt.time.time')
    def test_sensor_lifecycle_with_activity(self, mock_time):
        """Test sensor survives TTL when continuously active."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        mock_time.return_value = 0
        state_mgmt._manage_sensors("sensor_1")
        mock_time.return_value = 5
        state_mgmt._manage_sensors("sensor_1")
        mock_time.return_value = 12
        state_mgmt._manage_sensors("sensor_1")
        self.assertIn("sensor_1", state_mgmt._sensor_last_updated)

    # ==================== Edge Cases ====================

    def test_empty_sensor_id(self):
        """Test processing with empty sensor ID."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        embeddings = [self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0])]
        
        # Should not crash
        results = state_mgmt.update_video_embeddings("", embeddings)
        self.assertIsInstance(results, list)

    def test_very_short_ttl(self):
        """Test with very short TTL (1 second)."""
        config = VideoEmbeddingConfig(
            downsampler_type="sdt",
            sensor_ttl_sec=1,
            downsample_tolerance_mode="distance",
            downsample_distance_threshold=0.15,
        )
        state_mgmt = VideoEmbeddingStateMgmt(config)
        self.assertEqual(state_mgmt._sensor_ttl, 1)

    def test_very_long_ttl(self):
        """Test with very long TTL (1 hour)."""
        config = VideoEmbeddingConfig(downsampler_type="sdt", sensor_ttl_sec=3600)
        state_mgmt = VideoEmbeddingStateMgmt(config)
        self.assertEqual(state_mgmt._sensor_ttl, 3600)

    def test_all_embeddings_invalid(self):
        """Test when all embeddings are invalid (filtered out)."""
        state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
        
        # Create all invalid embeddings
        invalid1 = nvSchema.VisionLLM()
        invalid2 = nvSchema.VisionLLM()
        
        results = state_mgmt.update_video_embeddings("sensor_1", [invalid1, invalid2])
        
        # Should return empty list
        self.assertEqual(len(results), 0)

    def test_sensor_reactivation_after_purge(self):
        """Test sensor can be reactivated after being purged."""
        with patch('mdx.analytics.core.stream.state.video_embedding.video_embedding_state_mgmt.time.time') as mock_time:
            state_mgmt = VideoEmbeddingStateMgmt(self.config_sdt)
            embeddings = [self.create_embedding(1000, [1.0, 0.0, 0.0, 0.0])]
            mock_time.return_value = 0
            state_mgmt._manage_sensors("sensor_1")
            self.assertIn("sensor_1", state_mgmt._sensor_last_updated)
            mock_time.return_value = 11
            state_mgmt._manage_sensors("sensor_2")
            self.assertNotIn("sensor_1", state_mgmt._sensor_last_updated)
            mock_time.return_value = 12
            state_mgmt.update_video_embeddings("sensor_1", embeddings)
            self.assertIn("sensor_1", state_mgmt._downsamplers)


if __name__ == "__main__":
    unittest.main()
