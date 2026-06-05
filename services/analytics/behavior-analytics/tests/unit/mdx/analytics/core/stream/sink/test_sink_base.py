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

import pytest
import json
from datetime import datetime, timezone
from google.protobuf.timestamp_pb2 import Timestamp

from mdx.analytics.core.stream.sink.sink_base import (
    StrBytesSerializer,
    ProtoBytesSerializer,
    JsonStrSerializer,
    JsonBytesSerializer,
    _datetime_json_serializer
)
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.proto import ext_pb2 as extSchema


class TestStrBytesSerializer:
    """Test StrBytesSerializer functionality"""
    
    def test_serialize_simple_string(self):
        """Test serializing a simple string"""
        input_str = "hello world"
        result = StrBytesSerializer(input_str)
        
        assert isinstance(result, bytes)
        assert result == b"hello world"
    
    def test_serialize_empty_string(self):
        """Test serializing an empty string"""
        input_str = ""
        result = StrBytesSerializer(input_str)
        
        assert isinstance(result, bytes)
        assert result == b""
    
    def test_serialize_string_with_special_characters(self):
        """Test serializing string with special characters"""
        input_str = "Line 1\nLine 2\tTabbed\r\nWindows line ending"
        result = StrBytesSerializer(input_str)
        
        assert isinstance(result, bytes)
        assert result == input_str.encode('utf-8')
    
    def test_serialize_json_string(self):
        """Test serializing a JSON string"""
        input_str = '{"key": "value", "number": 42}'
        result = StrBytesSerializer(input_str)
        
        assert isinstance(result, bytes)
        assert result == input_str.encode('utf-8')


class TestProtoBytesSerializer:
    """Test ProtoBytesSerializer functionality"""
    
    def test_serialize_frame_protobuf(self):
        """Test serializing a Frame protobuf object"""
        # Create a Frame protobuf
        frame = nvSchema.Frame()
        frame.id = "frame_001"
        frame.version = "1.0"
        frame.sensorId = "sensor_001"
        
        # Add timestamp
        timestamp = Timestamp()
        timestamp.FromDatetime(datetime.now(timezone.utc))
        frame.timestamp.CopyFrom(timestamp)
        
        # Add an object
        obj = nvSchema.Object()
        obj.id = "obj_001"
        obj.type = "person"
        obj.confidence = 0.95
        frame.objects.append(obj)
        
        result = ProtoBytesSerializer(frame)
        
        assert isinstance(result, bytes)
        assert len(result) > 0
        
        # Verify we can deserialize it back
        deserialized_frame = nvSchema.Frame()
        deserialized_frame.ParseFromString(result)
        assert deserialized_frame.id == "frame_001"
        assert deserialized_frame.sensorId == "sensor_001"
        assert len(deserialized_frame.objects) == 1
        assert deserialized_frame.objects[0].id == "obj_001"
    
    def test_serialize_behavior_protobuf(self):
        """Test serializing a Behavior protobuf object"""
        # Create a Behavior protobuf
        behavior = extSchema.Behavior()
        behavior.id = "behavior_001"
        behavior.distance = 10.5
        behavior.speed = 2.3
        behavior.bearing = 45.0
        behavior.direction = "north"
        behavior.length = 5
        
        # Add sensor
        behavior.sensor.id = "sensor_002"
        
        # Add timestamps
        start_time = Timestamp()
        start_time.FromDatetime(datetime.now(timezone.utc))
        behavior.timestamp.CopyFrom(start_time)
        
        end_time = Timestamp()
        end_time.FromDatetime(datetime.now(timezone.utc))
        behavior.end.CopyFrom(end_time)
        
        result = ProtoBytesSerializer(behavior)
        
        assert isinstance(result, bytes)
        assert len(result) > 0
        
        # Verify we can deserialize it back
        deserialized_behavior = extSchema.Behavior()
        deserialized_behavior.ParseFromString(result)
        assert deserialized_behavior.id == "behavior_001"
        assert deserialized_behavior.sensor.id == "sensor_002"
        assert deserialized_behavior.distance == 10.5
        assert deserialized_behavior.speed == 2.3
    
    def test_serialize_sensor_protobuf(self):
        """Test serializing a Sensor protobuf object"""
        # Create a Sensor protobuf
        sensor = nvSchema.Sensor()
        sensor.id = "sensor_003"
        sensor.type = "camera"
        sensor.description = "Front-facing camera"
        
        # Add location
        sensor.location.lat = 37.7749
        sensor.location.lon = -122.4194
        sensor.location.alt = 0.0
        
        # Add coordinate
        sensor.coordinate.x = 100.0
        sensor.coordinate.y = 200.0
        sensor.coordinate.z = 10.0
        
        result = ProtoBytesSerializer(sensor)
        
        assert isinstance(result, bytes)
        assert len(result) > 0
        
        # Verify we can deserialize it back
        deserialized_sensor = nvSchema.Sensor()
        deserialized_sensor.ParseFromString(result)
        assert deserialized_sensor.id == "sensor_003"
        assert deserialized_sensor.type == "camera"
        assert deserialized_sensor.location.lat == 37.7749
        assert deserialized_sensor.coordinate.x == 100.0
    
    def test_serialize_empty_protobuf(self):
        """Test serializing an empty protobuf object"""
        frame = nvSchema.Frame()
        result = ProtoBytesSerializer(frame)
        
        assert isinstance(result, bytes)
        # Empty protobuf should still have some bytes
        assert len(result) >= 0


class TestDatetimeJsonSerializer:
    """Test _datetime_json_serializer functionality"""
    
    def test_serialize_datetime_object(self):
        """Test serializing a datetime object"""
        dt = datetime(2024, 1, 15, 12, 30, 45, tzinfo=timezone.utc)
        result = _datetime_json_serializer(dt)
        
        assert isinstance(result, str)
        assert result == "2024-01-15T12:30:45.000Z"
    
    def test_serialize_datetime_with_microseconds(self):
        """Test serializing datetime with microseconds"""
        dt = datetime(2024, 1, 15, 12, 30, 45, 123456, tzinfo=timezone.utc)
        result = _datetime_json_serializer(dt)
        
        assert isinstance(result, str)
        assert result == "2024-01-15T12:30:45.123Z"
    
    def test_serialize_non_datetime_object_raises_error(self):
        """Test that non-datetime objects raise TypeError"""
        with pytest.raises(TypeError) as excinfo:
            _datetime_json_serializer("not a datetime")
        
        assert "Type not serializable" in str(excinfo.value)


class TestJsonStrSerializer:
    """Test JsonStrSerializer functionality"""
    
    def test_serialize_simple_dict(self):
        """Test serializing a simple dictionary"""
        data = {"key": "value", "number": 42, "boolean": True}
        result = JsonStrSerializer(data)
        
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed == data
    
    def test_serialize_dict_with_datetime(self):
        """Test serializing nested dictionary with datetime"""
        dt1 = datetime(2024, 1, 15, 12, 30, 45, tzinfo=timezone.utc)
        dt2 = datetime(2024, 1, 16, 10, 15, 30, tzinfo=timezone.utc)
        
        data = {
            "event": "complex_event",
            "metadata": {
                "start_time": dt1,
                "end_time": dt2,
                "duration": 3600
            },
            "participants": ["user1", "user2"]
        }
        result = JsonStrSerializer(data)
        
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed["event"] == "complex_event"
        assert parsed["metadata"]["start_time"] == "2024-01-15T12:30:45.000Z"
        assert parsed["metadata"]["end_time"] == "2024-01-16T10:15:30.000Z"
        assert parsed["participants"] == ["user1", "user2"]
    
    def test_serialize_list_with_datetime(self):
        """Test serializing list containing datetime objects"""
        dt = datetime(2024, 1, 15, 12, 30, 45, tzinfo=timezone.utc)
        data = [
            {"id": 1, "timestamp": dt},
            {"id": 2, "timestamp": dt},
            "string_item"
        ]
        result = JsonStrSerializer(data)
        
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert len(parsed) == 3
        assert parsed[0]["timestamp"] == "2024-01-15T12:30:45.000Z"
        assert parsed[1]["timestamp"] == "2024-01-15T12:30:45.000Z"
        assert parsed[2] == "string_item"
    
    def test_serialize_empty_dict(self):
        """Test serializing empty dictionary"""
        data = {}
        result = JsonStrSerializer(data)
        
        assert isinstance(result, str)
        assert result == "{}"
    
    def test_serialize_empty_list(self):
        """Test serializing empty list"""
        data = []
        result = JsonStrSerializer(data)
        
        assert isinstance(result, str)
        assert result == "[]"


class TestJsonBytesSerializer:
    """Test JsonBytesSerializer functionality"""
    
    def test_serialize_simple_dict_to_bytes(self):
        """Test serializing dictionary to bytes"""
        data = {"key": "value", "number": 42}
        result = JsonBytesSerializer(data)
        
        assert isinstance(result, bytes)
        # Should be able to decode and parse back to original data
        decoded = result.decode('utf-8')
        parsed = json.loads(decoded)
        assert parsed == data
    
    def test_serialize_dict_with_datetime_to_bytes(self):
        """Test serializing dict with datetime to bytes"""
        dt = datetime(2024, 1, 15, 12, 30, 45, tzinfo=timezone.utc)
        data = {
            "event": "test_event",
            "timestamp": dt,
            "count": 5
        }
        result = JsonBytesSerializer(data)
        
        assert isinstance(result, bytes)
        # Should be able to decode and parse back
        decoded = result.decode('utf-8')
        parsed = json.loads(decoded)
        assert parsed["event"] == "test_event"
        assert parsed["timestamp"] == "2024-01-15T12:30:45.000Z"
        assert parsed["count"] == 5
    
    def test_serialize_complex_nested_structure_to_bytes(self):
        """Test serializing complex nested structure to bytes"""
        dt = datetime(2024, 1, 15, 12, 30, 45, tzinfo=timezone.utc)
        data = {
            "sensor_data": {
                "sensor_id": "cam_001",
                "location": {
                    "lat": 37.7749,
                    "lon": -122.4194,
                    "alt": 0.0
                },
                "readings": [
                    {"timestamp": dt, "value": 23.5},
                    {"timestamp": dt, "value": 24.1}
                ]
            },
            "metadata": {
                "version": "1.0",
                "processed": True
            }
        }
        result = JsonBytesSerializer(data)
        
        assert isinstance(result, bytes)
        # Verify complete round-trip
        decoded = result.decode('utf-8')
        parsed = json.loads(decoded)
        assert parsed["sensor_data"]["sensor_id"] == "cam_001"
        assert parsed["sensor_data"]["location"]["lat"] == 37.7749
        assert len(parsed["sensor_data"]["readings"]) == 2
        assert parsed["sensor_data"]["readings"][0]["timestamp"] == "2024-01-15T12:30:45.000Z"
        assert parsed["metadata"]["processed"] is True
    
    def test_serialize_empty_dict_to_bytes(self):
        """Test serializing empty dictionary to bytes"""
        data = {}
        result = JsonBytesSerializer(data)
        
        assert isinstance(result, bytes)
        assert result == b"{}"
