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
Simple tests for AlertRequestEntity to verify basic functionality.
"""
import pytest
from pydantic import ValidationError

from entity_management.request_entity.models import AlertRequestEntity, AlertInfo, EventInfo
from entity_management.shared import AlertSeverity


class TestAlertRequestEntitySimple:
    """Simple tests for AlertRequestEntity using actual field structure."""

    def test_create_minimal_alert_request(self):
        """Test creating AlertRequestEntity with minimal required fields."""
        # Use the actual field structure from the model
        alert_data = {
            "id": "test_123",
            "@timestamp": "2024-01-01T12:00:00Z",
            "sensor_id": "camera_01",
            "video_path": "/path/to/video.mp4",
            "alert": {
                "severity": "HIGH",
                "status": "REVIEW_PENDING",
                "type": "MOTION_DETECTED",
                "description": "Motion detected in restricted area"
            },
            "event": {
                "type": "Person Detected",
                "description": "Person detected crossing boundary"
            },
            "vss_params": {
                "vlm_params": {
                    "prompt": "Analyze this video"
                },
                "enable_reasoning": True,
                "do_verification": None
            }
        }
        
        # Create the entity
        alert = AlertRequestEntity(**alert_data)
        
        # AlertInfo Config has use_enum_values=True, so severity/status are stored as strings.
        assert alert.id == "test_123"
        assert alert.sensor_id == "camera_01"
        assert alert.video_path == "/path/to/video.mp4"
        assert alert.alert.severity == "HIGH"
        assert alert.alert.status == "REVIEW_PENDING"
        assert alert.event.type in ["Person Detected", "person_detected"]

    def test_create_alert_with_optional_fields(self):
        """Test creating AlertRequestEntity with optional fields."""
        alert_data = {
            "id": "test_456", 
            "@timestamp": "2024-01-01T12:00:00Z",
            "sensor_id": "camera_02",
            "video_path": "/path/to/video2.mp4",
            "alert": {
                "severity": "MEDIUM",
                "status": "REVIEW_PENDING",
                "type": "VEHICLE_DETECTED", 
                "description": "Vehicle detected in parking area"
            },
            "event": {
                "type": "Vehicle Detected",
                "description": "Car entered parking zone",
                "confidence": 0.92
            },
            "vss_params": {
                "vlm_params": {
                    "prompt": "Are there vehicles in a restricted area?"
                },
                "enable_reasoning": True,
                "do_verification": True
            },
            # Optional fields
            "confidence": 0.85,
            "cv_metadata_path": "/path/to/metadata.json",
        }

        alert = AlertRequestEntity(**alert_data)

        assert alert.confidence == 0.85
        assert alert.cv_metadata_path == "/path/to/metadata.json"
        assert alert.event.confidence == 0.92

    def test_validation_errors_for_missing_required_fields(self):
        """Test that missing required fields raise ValidationError."""
        incomplete_data = {
            "id": "test_incomplete"
            # Missing everything else
        }
        
        with pytest.raises(ValidationError) as exc_info:
            AlertRequestEntity(**incomplete_data)
        
        errors = exc_info.value.errors()
        # Should have multiple validation errors for missing fields
        assert len(errors) > 3
        
        # Check some expected missing fields. vss_params is optional, so excluded.
        error_fields = {error["loc"][0] for error in errors}
        expected_missing = {"@timestamp", "sensor_id", "video_path", "alert", "event"}
        assert expected_missing.issubset(error_fields)

    def test_invalid_severity_enum_raises_error(self):
        """Test that invalid severity values raise ValidationError."""
        alert_data = {
            "id": "test_invalid",
            "@timestamp": "2024-01-01T12:00:00Z", 
            "sensor_id": "camera_01",
            "video_path": "/path/to/video.mp4",
            "alert": {
                "severity": "INVALID_SEVERITY",  # Invalid enum value
                "status": "REVIEW_PENDING",
                "type": "test",
                "description": "Test alert"
            },
            "event": {
                "type": "test_event",
                "description": "Test event"
            },
            "vss_params": {
                "vlm_params": {"prompt": "Test"}
            }
        }
        
        with pytest.raises(ValidationError) as exc_info:
            AlertRequestEntity(**alert_data)
        
        # Should have validation error for severity field
        errors = exc_info.value.errors()
        severity_errors = [e for e in errors if "severity" in str(e["loc"])]
        assert len(severity_errors) > 0

    def test_invalid_confidence_range_raises_error(self):
        """Test that confidence scores outside 0.0-1.0 raise ValidationError."""
        alert_data = {
            "id": "test_confidence",
            "@timestamp": "2024-01-01T12:00:00Z",
            "sensor_id": "camera_01", 
            "video_path": "/path/to/video.mp4",
            "alert": {
                "severity": "HIGH",
                "status": "REVIEW_PENDING",
                "type": "test",
                "description": "Test alert"
            },
            "event": {
                "type": "test_event",
                "description": "Test event",
                "confidence": 1.5  # Invalid - outside 0.0-1.0 range
            },
            "vss_params": {
                "vlm_params": {"prompt": "Test"}
            }
        }
        
        with pytest.raises(ValidationError) as exc_info:
            AlertRequestEntity(**alert_data)
        
        errors = exc_info.value.errors()
        confidence_errors = [e for e in errors if "confidence" in str(e["loc"])]
        assert len(confidence_errors) > 0

    def test_json_serialization_works(self):
        """Test that AlertRequestEntity can be serialized to JSON."""
        alert_data = {
            "id": "test_json",
            "@timestamp": "2024-01-01T12:00:00Z",
            "sensor_id": "camera_01",
            "video_path": "/path/to/video.mp4", 
            "alert": {
                "severity": "LOW",
                "status": "REVIEW_PENDING",
                "type": "test_alert",
                "description": "Test alert for JSON"
            },
            "event": {
                "type": "test_event", 
                "description": "Test event for JSON"
            },
            "vss_params": {
                "vlm_params": {"prompt": "Test"}
            }
        }
        
        alert = AlertRequestEntity(**alert_data)
        
        # Should be serializable to JSON
        json_str = alert.json()
        assert "test_json" in json_str
        assert "camera_01" in json_str
        assert "LOW" in json_str
        
        # Should be deserializable from JSON
        reconstructed = AlertRequestEntity.parse_raw(json_str)
        assert reconstructed.id == alert.id
        assert reconstructed.alert.severity == alert.alert.severity


if __name__ == "__main__":
    pytest.main([__file__, "-v"]) 