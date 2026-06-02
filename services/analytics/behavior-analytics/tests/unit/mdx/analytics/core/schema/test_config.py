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

import unittest
import os
from datetime import datetime
from pydantic import ValidationError
from mdx.analytics.core.schema.config import AppConfig, KeyValuePair, KafkaConsumerConfig, KafkaProducerConfig

class TestAppConfig(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.config = AppConfig()
    
    def clear_all_caches(self):
        """Clear all caches - both @cached_property and @cache."""
        # Clear caches
        if hasattr(self.config.get_app_config, 'cache_clear'):
            self.config.get_app_config.cache_clear()
        if hasattr(self.config.get_bool_app_config, 'cache_clear'):
            self.config.get_bool_app_config.cache_clear()
        if hasattr(self.config.get_sensor_config, 'cache_clear'):
            self.config.get_sensor_config.cache_clear()
        if hasattr(self.config.sensor_tripwire_min_points, 'cache_clear'):
            self.config.sensor_tripwire_min_points.cache_clear()
        
        # Clear specific @cached_property attributes if they exist
        cached_properties = [
            'in_3d_mode', 'state_mgmt_filter', 'behavior_time_threshold',
            'behavior_max_points', 'cluster_threshold', 'object_confidence_threshold'
        ]
        for prop in cached_properties:
            if hasattr(self.config, prop):
                delattr(self.config, prop)

    def test_app_config_basic_operations(self):
        """Test basic app configuration operations."""
        # Test setting and getting app config
        self.config.set_app_config("testKey", "testValue")
        self.assertEqual(self.config.get_app_config("testKey"), "testValue")
        
        # Test default value
        self.assertEqual(self.config.get_app_config("nonexistentKey", "default"), "default")

    def test_3d_mode_property(self):
        """Test 3D mode property."""
        # Test default value
        self.assertFalse(self.config.in_3d_mode)

        # Test setting to True
        self.clear_all_caches()
        self.config.set_app_config("in3dMode", "true")
        self.assertTrue(self.config.in_3d_mode)

        # Test setting to False
        self.clear_all_caches()
        self.config.set_app_config("in3dMode", "false")
        self.assertFalse(self.config.in_3d_mode)

        # Save original environment
        original_env = os.environ.get("IN_3D_MODE")
        os.environ["IN_3D_MODE"] = "true"
        
        # Test setting to $IN_3D_MODE
        self.clear_all_caches()
        self.config.set_app_config("in3dMode", "$IN_3D_MODE")
        self.assertTrue(self.config.in_3d_mode)

        self.clear_all_caches()
        self.config.set_app_config("in3dMode", "$IN_3D_MOD")
        self.assertFalse(self.config.in_3d_mode)

        if original_env:
            os.environ["IN_3D_MODE"] = original_env

    def test_sensor_config_operations(self):
        """Test sensor configuration operations."""
        # Test setting and getting sensor config
        self.config.set_sensor_config("resolution", "1920x1080", "camera1")
        self.assertEqual(self.config.get_sensor_config("resolution", sensor_id="camera1"), "1920x1080")

        # Test default value
        self.assertEqual(self.config.get_sensor_config("nonexistent", "default", "camera1"), "default")

        # Test default sensor config
        self.config.set_sensor_config("fps", "30")  # Using default sensor_id
        self.assertEqual(self.config.get_sensor_config("fps", sensor_id="camera1"), "30")

    def test_sensor_tripwire_min_points(self):
        """Test sensor tripwire minimum points."""
        # Test default value
        self.assertEqual(self.config.sensor_tripwire_min_points("camera1"), 5)

        # Test custom value
        self.clear_all_caches()
        self.config.set_sensor_config("tripwireMinPoints", "10", "camera1")
        self.assertEqual(self.config.sensor_tripwire_min_points("camera1"), 10)

    def test_state_management_filter(self):
        """Test state management filter property."""
        # Test custom filter
        self.assertEqual(self.config.state_mgmt_filter, set())
        self.clear_all_caches()
        self.config.set_app_config("stateManagementFilter", '["car", "truck"]')
        self.assertEqual(self.config.state_mgmt_filter, {"car", "truck"})

    def test_behavior_time_threshold(self):
        """Test behavior time threshold property."""
        # Test default value (1970-01-01)
        self.assertEqual(self.config.behavior_time_threshold, datetime.fromisoformat("1970-01-01T00:00:00+00:00"))
        
        # Test custom value
        self.clear_all_caches()
        custom_time = "2024-01-01T00:00:00.000Z"
        self.config.set_app_config("behaviorTimeThreshold", custom_time)
        expected_time = datetime.fromisoformat("2024-01-01T00:00:00+00:00")
        self.assertEqual(self.config.behavior_time_threshold, expected_time)

    def test_invalidate_caches_refreshes_cached_property(self):
        """invalidate_caches() should drop @cached_property values so the next read re-evaluates."""
        # Default in_3d_mode is False; reading it primes the cache.
        self.assertFalse(self.config.in_3d_mode)
        self.assertIn("in_3d_mode", self.config.__dict__)
        # Mutate without invalidating -> stale value still cached.
        self.config.app.append(KeyValuePair(name="in3dMode", value="true"))
        self.assertFalse(self.config.in_3d_mode)
        # Invalidate -> next read picks up the new value.
        self.config.invalidate_caches()
        self.assertNotIn("in_3d_mode", self.config.__dict__)
        self.assertTrue(self.config.in_3d_mode)

    def test_invalidate_caches_refreshes_cache_method(self):
        """invalidate_caches() should clear @cache-wrapped methods so the next call re-evaluates."""
        # Prime get_app_config cache with a default lookup.
        self.assertEqual(self.config.get_app_config("dynamicKey", "default"), "default")
        # Mutate without invalidating: @cache returns the stale 'default'.
        self.config.app.append(KeyValuePair(name="dynamicKey", value="newValue"))
        self.assertEqual(self.config.get_app_config("dynamicKey", "default"), "default")
        # Invalidate -> next call returns the new value.
        self.config.invalidate_caches()
        self.assertEqual(self.config.get_app_config("dynamicKey", "default"), "newValue")

    def test_invalidate_caches_handles_empty_state(self):
        """invalidate_caches() must be a no-op when nothing has been cached yet."""
        # Fresh config, no reads -> no cached values to clear. Should not raise.
        self.config.invalidate_caches()
        # And subsequent reads still work normally.
        self.assertFalse(self.config.in_3d_mode)

    def test_invalidate_caches_clears_sensor_config(self):
        """Sensor-scoped @cache helpers (e.g. get_sensor_config) should also reset."""
        # Prime get_sensor_config cache.
        self.assertEqual(
            self.config.get_sensor_config(key="proxKey", default_value="off", sensor_id="cam1"),
            "off",
        )
        # Add a sensor entry that would change the result.
        self.config.set_sensor_config("proxKey", "on", "cam1")
        # Without invalidating, @cache still returns 'off'.
        self.assertEqual(
            self.config.get_sensor_config(key="proxKey", default_value="off", sensor_id="cam1"),
            "off",
        )
        # After invalidating, the new value is returned.
        self.config.invalidate_caches()
        self.assertEqual(
            self.config.get_sensor_config(key="proxKey", default_value="off", sensor_id="cam1"),
            "on",
        )

    def test_kafka_topic_operations(self):
        """Test Kafka topic operations."""
        # Test default value
        self.assertEqual(self.config.get_kafka_topic("nonexistent", "default-topic"), "default-topic")

        # Test setting and getting topic
        self.config.kafka.topics.append(KeyValuePair(name="input", value="input-topic"))
        self.assertEqual(self.config.get_kafka_topic("input"), "input-topic")

    def test_to_mutable_snapshot_empty(self):
        """to_mutable_snapshot returns empty app/sensors when nothing is configured."""
        snap = self.config.to_mutable_snapshot()
        self.assertEqual(snap, {"app": [], "sensors": []})

    def test_to_mutable_snapshot_with_app_and_sensors(self):
        """to_mutable_snapshot serializes app + sensors into plain JSON-friendly dicts."""
        self.config.set_app_config("foo", "1")
        self.config.set_sensor_config(key="k", value="v", sensor_id="cam1")
        snap = self.config.to_mutable_snapshot()
        self.assertEqual(snap["app"], [{"name": "foo", "value": "1"}])
        self.assertEqual(
            snap["sensors"],
            [{"id": "cam1", "configs": [{"name": "k", "value": "v"}]}],
        )

    def test_to_mutable_snapshot_excludes_read_only_sections(self):
        """Read-only sections (kafka, redisStream, mqtt, CRS, inference) must not appear."""
        self.config.kafka.topics.append(KeyValuePair(name="t", value="x"))
        snap = self.config.to_mutable_snapshot()
        self.assertNotIn("kafka", snap)
        self.assertNotIn("redisStream", snap)
        self.assertNotIn("mqtt", snap)
        self.assertNotIn("inference", snap)
        self.assertNotIn("coordinateReferenceSystem", snap)


class TestConfigValidation(unittest.TestCase):
    """Test validation of configuration fields."""
    
    def test_kafka_consumer_max_partition_fetch_bytes_validation(self):
        """Test maxPartitionFetchBytes validation."""
        # Test valid maximum value
        config = KafkaConsumerConfig(maxPartitionFetchBytes=1000000000)
        self.assertEqual(config.maxPartitionFetchBytes, 1000000000)
        
        # Test valid minimum value
        config = KafkaConsumerConfig(maxPartitionFetchBytes=1)
        self.assertEqual(config.maxPartitionFetchBytes, 1)
        
        # Test too large value
        with self.assertRaises(ValidationError) as cm:
            KafkaConsumerConfig(maxPartitionFetchBytes=2147483647)
        errors = cm.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['loc'], ('maxPartitionFetchBytes',))
        self.assertIn('less than or equal to 1000000000', errors[0]['msg'])
        
        # Test too small value
        with self.assertRaises(ValidationError) as cm:
            KafkaConsumerConfig(maxPartitionFetchBytes=0)
        errors = cm.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['loc'], ('maxPartitionFetchBytes',))
        self.assertIn('greater than or equal to 1', errors[0]['msg'])
    
    def test_kafka_consumer_auto_offset_reset_validation(self):
        """Test autoOffsetReset validation."""
        # Test valid values
        for valid_value in ['latest', 'earliest', 'none']:
            config = KafkaConsumerConfig(autoOffsetReset=valid_value)
            self.assertEqual(config.autoOffsetReset, valid_value)

        config = KafkaConsumerConfig(autoOffsetReset='invalid')
        self.assertEqual(config.autoOffsetReset, 'invalid')
    
    def test_kafka_consumer_numeric_field_validation(self):
        """Test validation of numeric fields in KafkaConsumerConfig."""
        # Test that timeout can be -1 (special value) or >= 0
        config = KafkaConsumerConfig(timeout=0)
        self.assertEqual(config.timeout, 0)
        
        config = KafkaConsumerConfig(timeout=-1)
        self.assertEqual(config.timeout, -1)
        
        # Test timeout less than -1 is invalid
        with self.assertRaises(ValidationError) as cm:
            KafkaConsumerConfig(timeout=-2)
        errors = cm.exception.errors()
        self.assertEqual(errors[0]['loc'], ('timeout',))
        self.assertIn('greater than or equal to -1', errors[0]['msg'])
        
        # Test zero maxPollRecords
        with self.assertRaises(ValidationError) as cm:
            KafkaConsumerConfig(maxPollRecords=0)
        errors = cm.exception.errors()
        self.assertEqual(errors[0]['loc'], ('maxPollRecords',))
        self.assertIn('greater than or equal to 1', errors[0]['msg'])
        
        # Test negative maxPollIntervalMs
        with self.assertRaises(ValidationError) as cm:
            KafkaConsumerConfig(maxPollIntervalMs=0)
        errors = cm.exception.errors()
        self.assertEqual(errors[0]['loc'], ('maxPollIntervalMs',))
        self.assertIn('greater than or equal to 1', errors[0]['msg'])
    
    def test_kafka_producer_validation(self):
        """Test validation of KafkaProducerConfig fields."""
        # Test valid zero lingerMs
        config = KafkaProducerConfig(lingerMs=0)
        self.assertEqual(config.lingerMs, 0)
        
        # Test negative lingerMs
        with self.assertRaises(ValidationError) as cm:
            KafkaProducerConfig(lingerMs=-100)
        errors = cm.exception.errors()
        self.assertEqual(errors[0]['loc'], ('lingerMs',))
        self.assertIn('greater than or equal to 0', errors[0]['msg'])
        
        # Test messageMaxBytes below minimum (1000)
        with self.assertRaises(ValidationError) as cm:
            KafkaProducerConfig(messageMaxBytes=999)
        errors = cm.exception.errors()
        self.assertEqual(errors[0]['loc'], ('messageMaxBytes',))
        self.assertIn('greater than or equal to 1000', errors[0]['msg'])
    
    def test_app_config_unknown_fields_rejected(self):
        """Test that unknown fields are rejected in AppConfig."""
        # Test with unknown top-level field
        with self.assertRaises(ValidationError) as cm:
            AppConfig(unknownField="value")  # type: ignore
        errors = cm.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['type'], 'extra_forbidden')
        self.assertEqual(errors[0]['loc'], ('unknownField',))
        
        # Test with spaceAnalytics field (should be rejected)
        with self.assertRaises(ValidationError) as cm:
            AppConfig(
                spaceAnalytics={  # type: ignore
                    "invocationIntervalSec": 1.0,
                    "gridSize": 0.2
                }
            )
        errors = cm.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['type'], 'extra_forbidden')
        self.assertEqual(errors[0]['loc'], ('spaceAnalytics',))
        
        # Test with trajectory field (should be rejected)
        with self.assertRaises(ValidationError) as cm:
            AppConfig(trajectory={"geoCoordEnable": True})  # type: ignore
        errors = cm.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['type'], 'extra_forbidden')
        self.assertEqual(errors[0]['loc'], ('trajectory',))
    
    def test_app_config_with_invalid_kafka_consumer_config(self):
        """Test AppConfig with invalid Kafka consumer configuration."""
        with self.assertRaises(ValidationError) as cm:
            AppConfig(**{  # type: ignore
                "kafka": {
                    "brokers": "localhost:9092",
                    "consumer": {
                        "maxPartitionFetchBytes": 2147483647  # Too large
                    }
                }
            })
        errors = cm.exception.errors()
        self.assertEqual(len(errors), 1)
        # The error location will be nested
        self.assertEqual(errors[0]['loc'], ('kafka', 'consumer', 'maxPartitionFetchBytes'))
        self.assertIn('less than or equal to 1000000000', errors[0]['msg'])
    
    def test_app_config_with_corrected_values(self):
        """Test AppConfig with corrected values works properly."""
        config = AppConfig(**{  # type: ignore
            "kafka": {
                "brokers": "localhost:9092",
                "consumer": {
                    "maxPartitionFetchBytes": 1000000000,  # Maximum allowed
                    "autoOffsetReset": "earliest"
                },
                "producer": {
                    "lingerMs": 100,
                    "messageMaxBytes": 10485760
                }
            }
        })
        
        self.assertEqual(config.kafka.consumer.maxPartitionFetchBytes, 1000000000)
        self.assertEqual(config.kafka.consumer.autoOffsetReset, "earliest")
        self.assertEqual(config.kafka.producer.lingerMs, 100)
        self.assertEqual(config.kafka.producer.messageMaxBytes, 10485760)


if __name__ == '__main__':
    unittest.main()