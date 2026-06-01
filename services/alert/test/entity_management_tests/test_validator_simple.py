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
Simple tests for EntityValidator using the actual API.
"""
from entity_management import EntityValidator, AlertRequestEntity
from entity_management.shared import AlertSeverity


def test_validator_with_valid_requests():
    """Test EntityValidator with valid alert requests."""
    validator = EntityValidator()
    
    # Create a list of valid alert requests
    valid_requests = [
        {
            "id": "test_001",
            "@timestamp": "2024-01-01T12:00:00Z",
            "sensor_id": "camera_01",
            "video_path": "/recordings/test_001.mp4",
            "alert": {
                "severity": "HIGH",
                "status": "ACTIVE",
                "type": "security_breach",
                "description": "Unauthorized access detected"
            },
            "event": {
                "type": "person_detected",
                "description": "Person detected in restricted area",
                "confidence": 0.95
            },
            "vss_params": {
                "vlm_params": {
                    "prompt": "Analyze this security footage for unauthorized access."
                }
            }
        },
        {
            "id": "test_002",
            "@timestamp": "2024-01-01T13:00:00Z",
            "sensor_id": "door_sensor_02",
            "video_path": "/recordings/test_002.mp4",
            "alert": {
                "severity": "MEDIUM",
                "status": "REVIEW_PENDING",
                "type": "door_anomaly",
                "description": "Door left open unexpectedly"
            },
            "event": {
                "type": "door_opened",
                "description": "Security door remained open",
                "confidence": 0.88
            },
            "vss_params": { "vlm_params": { "prompt": "Check if this door activity is normal." } }
        }
    ]
    
    # Validate the requests
    entities = validator.validate_and_build(valid_requests)
    
    # Should have processed both requests successfully
    assert len(entities) == 2
    assert all(isinstance(entity, AlertRequestEntity) for entity in entities)
    
    # AlertInfo Config has use_enum_values=True, so severity is stored as string.
    entity1 = entities[0]
    assert entity1.id == "test_001"
    assert entity1.alert.severity == "HIGH"
    assert entity1.event.confidence == 0.95

    entity2 = entities[1]
    assert entity2.id == "test_002"
    assert entity2.alert.severity == "MEDIUM"
    assert entity2.event.confidence == 0.88
    
    print("✅ Valid requests processed successfully")


def test_validator_with_invalid_requests():
    """Test EntityValidator behavior with invalid requests."""
    validator = EntityValidator()
    
    # Create a mix of valid and invalid requests
    mixed_requests = [
        # Valid request
        {
            "id": "valid_001",
            "@timestamp": "2024-01-01T12:00:00Z",
            "sensor_id": "camera_valid",
            "video_path": "/recordings/valid.mp4",
            "alert": {
                "severity": "HIGH",
                "status": "ACTIVE",
                "type": "test_alert",
                "description": "Valid test alert"
            },
            "event": {
                "type": "test_event",
                "description": "Valid test event"
            },
            "vss_params": { "vlm_params": { "prompt": "Valid test prompt" } }
        },
        
        # Invalid request (missing required fields)
        {
            "id": "invalid_001"
            # Missing most required fields
        },
        
        # Another valid request
        {
            "id": "valid_002",
            "@timestamp": "2024-01-01T13:00:00Z",
            "sensor_id": "camera_valid_2",
            "video_path": "/recordings/valid2.mp4",
            "alert": {
                "severity": "LOW",
                "status": "RESOLVED",
                "type": "test_alert_2",
                "description": "Another valid alert"
            },
            "event": {
                "type": "test_event_2",
                "description": "Another valid event"
            },
            "vss_params": { "vlm_params": { "prompt": "Another valid prompt" } }
        }
    ]
    
    # Validate the mixed requests
    entities = validator.validate_and_build(mixed_requests)
    
    # Should only return valid entities (invalid ones are filtered out)
    assert len(entities) == 2  # Only the 2 valid ones
    assert all(isinstance(entity, AlertRequestEntity) for entity in entities)
    
    # Check that we got the valid entities
    entity_ids = [entity.id for entity in entities]
    assert "valid_001" in entity_ids
    assert "valid_002" in entity_ids
    assert "invalid_001" not in entity_ids
    
    print("✅ Mixed valid/invalid requests handled correctly")


def test_validator_statistics():
    """Test that validator tracks statistics correctly."""
    validator = EntityValidator()
    
    # Start with clean stats
    initial_stats = validator.get_validation_stats()
    
    # Process some requests
    requests = [
        {
            "id": "stats_test",
            "@timestamp": "2024-01-01T12:00:00Z",
            "sensor_id": "camera_stats",
            "video_path": "/recordings/stats.mp4",
            "alert": {
                "severity": "MEDIUM",
                "status": "ACTIVE",
                "type": "stats_test",
                "description": "Statistics test alert"
            },
            "event": {
                "type": "stats_event",
                "description": "Statistics test event"
            },
            "vss_params": { "vlm_params": { "prompt": "Statistics test prompt" } }
        }
    ]
    
    entities = validator.validate_and_build(requests)
    
    # Check updated stats
    final_stats = validator.get_validation_stats()
    
    assert final_stats['total_requests'] > initial_stats['total_requests']
    assert final_stats['successful_validations'] > initial_stats['successful_validations']
    assert len(entities) == 1
    
    print("✅ Validator statistics working correctly")


def test_empty_request_list():
    """Test validator with empty request list."""
    validator = EntityValidator()
    
    entities = validator.validate_and_build([])
    
    assert entities == []
    print("✅ Empty request list handled correctly")


def test_batch_processing_performance():
    """Test validator performance with larger batch."""
    validator = EntityValidator()
    
    # Create a batch of 20 requests
    batch_requests = []
    for i in range(20):
        request = {
            "id": f"batch_{i:03d}",
            "@timestamp": f"2024-01-01T{12 + i//10}:{(i*3)%60:02d}:00Z",
            "sensor_id": f"camera_batch_{i%5}",
            "video_path": f"/recordings/batch_{i}.mp4",
            "alert": {
                "severity": ["LOW", "MEDIUM", "HIGH"][i % 3],
                "status": "ACTIVE",
                "type": f"batch_type_{i%3}",
                "description": f"Batch alert {i}"
            },
            "event": {
                "type": f"batch_event_{i%4}",
                "description": f"Batch event {i}",
                "confidence": min(0.99, 0.5 + (i * 0.02))
            },
            "vss_params": {
                "vlm_params": {
                    "prompt": f"Analyze batch item {i}"
                }
            }
        }
        batch_requests.append(request)

    entities = validator.validate_and_build(batch_requests)

    assert len(entities) == 20
    assert entities[0].id == "batch_000"
    assert entities[19].id == "batch_019"
    # AlertInfo Config has use_enum_values=True, so severity is stored as string.
    assert entities[5].alert.severity in ["LOW", "MEDIUM", "HIGH"]
    
    print(f"✅ Batch processing: {len(entities)}/20 requests processed successfully")


if __name__ == "__main__":
    test_validator_with_valid_requests()
    test_validator_with_invalid_requests()
    test_validator_statistics()
    test_empty_request_list()
    test_batch_processing_performance()
    print("\n🎉 All EntityValidator tests passed!") 