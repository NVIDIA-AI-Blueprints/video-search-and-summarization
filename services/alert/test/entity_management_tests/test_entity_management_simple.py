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
Simple working tests for entity management system.
"""
import pytest
import tempfile
import os
from pathlib import Path

# Test just the basic imports and validator
def test_imports_work():
    """Test that all entity management components can be imported."""
    from entity_management import (
        AlertRequestEntity,
        AlertResponseEntity, 
        EntityValidator,
        EntityBuilder
    )
    from entity_management.shared import AlertSeverity, AlertStatus
    
    # Basic verification that imports work
    assert AlertRequestEntity is not None
    assert AlertResponseEntity is not None
    assert EntityValidator is not None
    assert EntityBuilder is not None
    assert AlertSeverity.HIGH is not None
    assert AlertStatus.ACTIVE is not None
    print("✅ All imports successful")


def test_entity_validator_creation():
    """Test that EntityValidator can be created."""
    from entity_management import EntityValidator
    
    validator = EntityValidator()
    assert validator is not None
    print("✅ EntityValidator created successfully")


def test_entity_builder_creation():
    """Test that EntityBuilder can be created.""" 
    from entity_management import EntityBuilder
    
    builder = EntityBuilder()
    assert builder is not None
    print("✅ EntityBuilder created successfully")


def test_config_loading():
    """Test that the configuration system loads properly."""
    from entity_management.config.defaults_loader import AlertsDefaultsConfigLoader
    
    # This should work with the existing alert_request_defaults.yaml
    loader = AlertsDefaultsConfigLoader()
    config = loader.load_defaults()
    
    assert config is not None
    assert hasattr(config, 'vlm_params')
    assert hasattr(config, 'vss_params')
    print("✅ Configuration loaded successfully")


@pytest.mark.skip(reason="EntityBuilder.create_error_response removed in API rewrite (commit 65d17be).")
def test_create_error_response():
    """Test creating error responses."""
    from entity_management import EntityBuilder

    builder = EntityBuilder()
    error_response = builder.create_error_response(
        "Test error message",
        request_id="test_123"
    )

    assert error_response is not None
    assert error_response.request_id == "test_123"
    assert error_response.error_message == "Test error message"
    assert not error_response.is_successful
    print("✅ Error response created successfully")


def test_alert_severity_enum():
    """Test AlertSeverity enum values."""
    from entity_management.shared import AlertSeverity

    assert AlertSeverity.LOW.value == "LOW"
    assert AlertSeverity.MEDIUM.value == "MEDIUM"
    assert AlertSeverity.HIGH.value == "HIGH"
    assert AlertSeverity.CRITICAL.value == "CRITICAL"
    print("✅ AlertSeverity enum works correctly")


def test_alert_status_enum():
    """Test AlertStatus enum values."""
    from entity_management.shared import AlertStatus

    assert AlertStatus.ACTIVE.value == "ACTIVE"
    assert AlertStatus.RESOLVED.value == "RESOLVED"
    assert AlertStatus.ACKNOWLEDGED.value == "ACKNOWLEDGED"
    assert AlertStatus.SUPPRESSED.value == "SUPPRESSED"
    assert AlertStatus.REVIEW_PENDING.value == "REVIEW_PENDING"
    assert AlertStatus.REVIEWED.value == "REVIEWED"
    assert AlertStatus.REVIEW_FAILED.value == "REVIEW_FAILED"
    print("✅ AlertStatus enum works correctly")


def test_basic_alert_info_creation():
    """Test creating AlertInfo directly."""
    from entity_management.request_entity.models import AlertInfo
    from entity_management.shared import AlertSeverity, AlertStatus

    alert_info = AlertInfo(
        severity=AlertSeverity.HIGH,
        status=AlertStatus.ACTIVE,
        type="motion_detection",
        description="Motion detected in restricted area"
    )

    # AlertInfo Config has use_enum_values=True, so severity/status are stored as strings.
    assert alert_info.severity == "HIGH"
    assert alert_info.status == "ACTIVE"
    assert alert_info.type == "motion_detection"
    assert alert_info.description == "Motion detected in restricted area"
    print("✅ AlertInfo created successfully")


def test_basic_event_info_creation():
    """Test creating EventInfo directly."""
    from entity_management.request_entity.models import EventInfo
    
    event_info = EventInfo(
        type="person_detected",
        description="Person detected crossing boundary"
    )
    
    assert event_info.type == "person_detected"
    assert event_info.description == "Person detected crossing boundary"
    print("✅ EventInfo created successfully")


if __name__ == "__main__":
    # Run tests manually
    test_imports_work()
    test_entity_validator_creation()
    test_entity_builder_creation()
    test_config_loading()
    test_create_error_response()
    test_alert_severity_enum()
    test_alert_status_enum()
    test_basic_alert_info_creation()
    test_basic_event_info_creation()
    print("\n🎉 All tests passed!") 