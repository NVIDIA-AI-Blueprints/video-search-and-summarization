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

from unittest.mock import Mock, patch

from mdx.analytics.core.app.app_base import BaseApp, Processor
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Behavior, Incident, Sensor
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.proto import ext_pb2 as extSchema


class MockBaseApp(BaseApp):
    """Mock concrete implementation of BaseApp for testing purposes."""
    
    def __init__(self, config: AppConfig, calibration_path: str):
        # Skip the parent constructor to avoid initialization issues
        self.config = config
        self.calibration_path = calibration_path
        self._processors = {}
        self._behavior_embedding = None
        self._behavior_clustering = None
        self.sink = Mock()
        self.source = Mock()
        self.calibration = Mock()
        # Dynamic-config infrastructure stub (real BaseApp.__init__ wires
        # the per-worker ConfigFileMonitor; the mock skips that, so leave it
        # as None to satisfy close()'s closeable iteration without
        # exercising the watchdog setup).
        self._config_monitor = None
        self._config_applier = None
        self.close_called = False
    
    def get_processors(self) -> list[Processor]:
        """Return mock processors for testing."""
        return list(self._processors.values())


class TestBaseAppFunctionality:
    """Test suite for BaseApp core functionality."""
    
    def test_register_processor(self):
        """Test that processors can be registered correctly."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock poller and handler
        mock_poller = Mock()
        mock_handler = Mock()
        mock_handler.__name__ = "test_handler"
        
        # Register processor
        app.register_processor(mock_poller, mock_handler, num_workers=3)
        
        # Verify processor was registered
        processors = app.get_processors()
        assert len(processors) == 1
        
        processor = processors[0]
        assert processor.poller == mock_poller
        assert processor.handler == mock_handler
        assert processor.num_workers == 3
        assert app._processors["test_handler"] == processor
    
    def test_register_processor_with_zero_workers(self):
        """Test that processor registration handles invalid worker count."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock poller and handler
        mock_poller = Mock()
        mock_handler = Mock()
        mock_handler.__name__ = "test_handler"
        
        # Register processor with 0 workers (invalid)
        app.register_processor(mock_poller, mock_handler, num_workers=0)
        
        # Verify processor was NOT registered
        processors = app.get_processors()
        assert len(processors) == 0
        assert "test_handler" not in app._processors
    
    @patch('mdx.analytics.core.app.app_base.convert_behavior_to_protobuf_behavior')
    @patch('mdx.analytics.core.app.app_base.StrBytesSerializer')
    @patch('mdx.analytics.core.app.app_base.ProtoBytesSerializer')
    def test_write_behaviors(self, mock_proto_serializer, mock_str_serializer, 
                            mock_convert_behavior):
        """Test that behaviors are written correctly with proper serialization."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        mock_config.get_bool_app_config.return_value = False
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock behaviors
        mock_sensor1 = Mock(spec=Sensor)
        mock_sensor1.id = "sensor1"
        mock_sensor2 = Mock(spec=Sensor)
        mock_sensor1.id = "sensor2"
        mock_behavior1 = Mock(spec=Behavior)
        mock_behavior2 = Mock(spec=Behavior)
        mock_behavior1.sensor = mock_sensor1
        mock_behavior2.sensor = mock_sensor2
        
        behaviors: list[Behavior] = [mock_behavior1, mock_behavior2]
        
        # Create mock protobuf behaviors
        mock_proto_behavior1 = Mock()
        mock_proto_behavior2 = Mock()
        mock_proto_behavior1.sensor.id = "sensor1"
        mock_proto_behavior2.sensor.id = "sensor2"
        
        mock_convert_behavior.side_effect = [mock_proto_behavior1, mock_proto_behavior2]
        
        # Write behaviors
        app.write_behaviors(behaviors)
        
        # Verify behaviors were converted to protobuf
        assert mock_convert_behavior.call_count == 2
        mock_convert_behavior.assert_any_call(mock_behavior1)
        mock_convert_behavior.assert_any_call(mock_behavior2)
        
        # Verify sink.write was called with correct parameters
        app.sink.write.assert_called_once_with(
            dest_key="behavior",
            messages=[mock_proto_behavior1, mock_proto_behavior2],
            value_serializer=mock_proto_serializer,
            key_extractor=app.sink.write.call_args[1]['key_extractor'],
            key_serializer=mock_str_serializer
        )
    

    @patch('mdx.analytics.core.app.app_base.convert_behavior_to_protobuf_behavior')
    @patch('mdx.analytics.core.app.app_base.StrBytesSerializer')
    @patch('mdx.analytics.core.app.app_base.ProtoBytesSerializer')
    def test_write_behaviors_filters_none_values(self, mock_proto_serializer, 
                                                 mock_str_serializer, mock_convert_behavior):
        """Test that None behaviors are filtered out."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        mock_config.get_bool_app_config.return_value = False
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create behaviors list with None values
        mock_sensor = Mock(spec=Sensor)
        mock_sensor.id = "sensor1"
        mock_behavior = Mock(spec=Behavior)
        mock_behavior.sensor = mock_sensor
        
        behaviors: list[Behavior] = [None, mock_behavior, None]
        
        # Create mock protobuf behavior
        mock_proto_behavior = Mock()
        mock_proto_behavior.sensor.id = "sensor1"
        
        mock_convert_behavior.return_value = mock_proto_behavior
        
        # Write behaviors
        app.write_behaviors(behaviors)
        
        # Verify only non-None behavior was converted
        mock_convert_behavior.assert_called_once_with(mock_behavior)
        
        # Verify sink.write was called with only the valid behavior
        app.sink.write.assert_called_once()
        messages_arg = app.sink.write.call_args[1]['messages']
        assert len(messages_arg) == 1
        assert messages_arg[0] == mock_proto_behavior
    
    @patch('mdx.analytics.core.app.app_base.StreamMessageProtoDeserializer')
    def test_read_raw_frames(self, mock_deserializer_cls):
        """Test that raw frames are read correctly from source."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock deserializer and frames
        mock_deserializer = Mock()
        mock_deserializer_cls.return_value = mock_deserializer
        
        mock_frame1 = Mock(spec=nvSchema.Frame)
        mock_frame2 = Mock(spec=nvSchema.Frame)
        mock_frames = [mock_frame1, mock_frame2]
        
        app.source.poll.return_value = mock_frames
        
        # Read raw frames
        result = app.read_raw(group_id_suffix="test_group")
        
        # Verify source.poll was called with correct parameters
        app.source.poll.assert_called_once_with(
            src_key="raw",
            msg_deserializer=mock_deserializer,
            group_id_suffix="test_group"
        )
        
        # Verify deserializer was created with correct schema
        mock_deserializer_cls.assert_called_once_with(nvSchema.Frame)
        
        # Verify result matches expected frames
        assert result == mock_frames
    
    @patch('mdx.analytics.core.app.app_base.StreamMessageProtoDeserializer')
    @patch('mdx.analytics.core.app.app_base.nv_behavior_to_behavior')
    def test_read_behavior(self, mock_nv_behavior_to_behavior, mock_deserializer_cls):
        """Test that behavior data is read correctly from source."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock behavior
        mock_behavior = Mock(spec=Behavior)
        
        # Mock the actual implementation - the _msg_deserializer function is created internally
        app.source.poll.return_value = [mock_behavior]
        
        # Read behaviors
        result = app.read_behavior(group_id_suffix="test_group")
        
        # Verify source.poll was called
        app.source.poll.assert_called_once()
        call_args = app.source.poll.call_args[1]
        assert call_args['src_key'] == "behavior"
        assert call_args['group_id_suffix'] == "test_group"
        
        # Verify result matches expected behaviors
        assert result == [mock_behavior]
    
    @patch('mdx.analytics.core.app.app_base.StreamMessageProtoDeserializer')
    @patch('mdx.analytics.core.app.app_base.nv_behavior_to_behavior')
    def test_read_events(self, mock_nv_behavior_to_behavior, mock_deserializer_cls):
        """Test that event data is read correctly from source."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock event
        mock_event = Mock(spec=Behavior)
        
        app.source.poll.return_value = [mock_event]
        
        # Read events
        result = app.read_events(group_id_suffix="test_group")
        
        # Verify source.poll was called with correct parameters
        app.source.poll.assert_called_once()
        call_args = app.source.poll.call_args[1]
        assert call_args['src_key'] == "events"
        assert call_args['group_id_suffix'] == "test_group"
        
        # Verify result matches expected events
        assert result == [mock_event]
    
    @patch('mdx.analytics.core.app.app_base.StreamMessageProtoDeserializer')
    @patch('mdx.analytics.core.app.app_base.nv_behavior_to_behavior')
    def test_read_anomaly(self, mock_nv_behavior_to_behavior, mock_deserializer_cls):
        """Test that anomaly data is read correctly from source."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock anomaly
        mock_anomaly = Mock(spec=Behavior)
        
        app.source.poll.return_value = [mock_anomaly]
        
        # Read anomalies
        result = app.read_anomaly(group_id_suffix="test_group")
        
        # Verify source.poll was called with correct parameters
        app.source.poll.assert_called_once()
        call_args = app.source.poll.call_args[1]
        assert call_args['src_key'] == "anomaly"
        assert call_args['group_id_suffix'] == "test_group"
        
        # Verify result matches expected anomalies
        assert result == [mock_anomaly]
    
    @patch('mdx.analytics.core.app.app_base.StrBytesSerializer')
    @patch('mdx.analytics.core.app.app_base.ProtoBytesSerializer')
    def test_write_frames(self, mock_proto_serializer, mock_str_serializer):
        """Test that frame data is written correctly."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock frames
        mock_frame1 = Mock(spec=nvSchema.Frame)
        mock_frame2 = Mock(spec=nvSchema.Frame)
        mock_frame1.sensorId = "sensor1"
        mock_frame2.sensorId = "sensor2"
        
        frames: list[nvSchema.Frame] = [mock_frame1, mock_frame2]
        
        # Write frames
        app.write_frames(frames)
        
        # Verify sink.write was called with correct parameters
        app.sink.write.assert_called_once_with(
            dest_key="frames",
            messages=frames,
            value_serializer=mock_proto_serializer,
            key_extractor=app.sink.write.call_args[1]['key_extractor'],
            key_serializer=mock_str_serializer
        )
    
    @patch('mdx.analytics.core.app.app_base.logger')
    def test_close_resources_with_none_values(self, mock_logger):
        """Test that close handles None resources gracefully."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Set some resources to None
        app._behavior_clustering = None
        app._behavior_embedding = None
        
        # Create mock resources with close methods
        mock_source = Mock()
        mock_sink = Mock()
        
        app.source = mock_source
        app.sink = mock_sink
        
        # Close app
        app.close()
        
        # Verify only existing resources were closed
        mock_source.close.assert_called_once()
        mock_sink.close.assert_called_once()
        
        # Verify close was logged for existing resources only
        assert mock_logger.info.call_count == 3
    
    @patch('mdx.analytics.core.app.app_base.logger')
    def test_close_resources_without_close_method(self, mock_logger):
        """Test that close handles resources without close method gracefully."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock resources without close methods
        mock_resource_no_close = Mock(spec=[])  # No close method
        del mock_resource_no_close.close  # Ensure no close attribute
        
        app.source = mock_resource_no_close
        app.sink = Mock()  # This one has close
        app._behavior_clustering = None
        app._behavior_embedding = None
        
        # Close app
        app.close()
        
        # Verify only sink was closed (source doesn't have close method)
        app.sink.close.assert_called_once()
        
        # Verify close was logged for sink and calibration
        assert mock_logger.info.call_count == 2

    @patch('mdx.analytics.core.app.app_base.convert_behavior_to_protobuf_behavior')
    @patch('mdx.analytics.core.app.app_base.StrBytesSerializer')
    @patch('mdx.analytics.core.app.app_base.ProtoBytesSerializer')
    def test_write_events(self, mock_proto_serializer, mock_str_serializer, mock_convert_behavior):
        """Test that events are written correctly."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock events
        mock_sensor1 = Mock(spec=Sensor)
        mock_sensor1.id = "sensor1"
        mock_sensor2 = Mock(spec=Sensor)
        mock_sensor1.id = "sensor2"
        mock_event1 = Mock(spec=Behavior)
        mock_event2 = Mock(spec=Behavior)
        mock_event1.sensor = mock_sensor1
        mock_event2.sensor = mock_sensor2
        
        events: list[Behavior] = [mock_event1, mock_event2]
        
        # Create mock protobuf events
        mock_proto_event1 = Mock()
        mock_proto_event2 = Mock()
        mock_proto_event1.sensor.id = "sensor1"
        mock_proto_event2.sensor.id = "sensor2"
        
        mock_convert_behavior.side_effect = [mock_proto_event1, mock_proto_event2]
        
        # Write events
        app.write_events(events)
        
        # Verify events were converted to protobuf
        assert mock_convert_behavior.call_count == 2
        mock_convert_behavior.assert_any_call(mock_event1)
        mock_convert_behavior.assert_any_call(mock_event2)
        
        # Verify sink.write was called with correct parameters
        app.sink.write.assert_called_once_with(
            dest_key="events",
            messages=[mock_proto_event1, mock_proto_event2],
            value_serializer=mock_proto_serializer,
            key_extractor=app.sink.write.call_args[1]['key_extractor'],
            key_serializer=mock_str_serializer
        )

    @patch('mdx.analytics.core.app.app_base.convert_incident_to_protobuf_incident')
    @patch('mdx.analytics.core.app.app_base.StrBytesSerializer')
    @patch('mdx.analytics.core.app.app_base.ProtoBytesSerializer')
    def test_write_incidents(self, mock_proto_serializer, mock_str_serializer, mock_convert_incident):
        """Test that incidents are written correctly."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock incidents
        mock_incident1 = Mock(spec=Incident)
        mock_incident2 = Mock(spec=Incident)
        mock_incident1.sensorId = "sensor1"
        mock_incident2.sensorId = "sensor2"
        
        incidents: list[Incident] = [mock_incident1, mock_incident2]
        
        # Create mock protobuf incidents
        mock_proto_incident1 = Mock()
        mock_proto_incident2 = Mock()
        mock_proto_incident1.sensorId = "sensor1"
        mock_proto_incident2.sensorId = "sensor2"
        
        mock_convert_incident.side_effect = [mock_proto_incident1, mock_proto_incident2]
        
        # Write incidents
        app.write_incidents(incidents)
        
        # Verify incidents were converted to protobuf
        assert mock_convert_incident.call_count == 2
        mock_convert_incident.assert_any_call(mock_incident1)
        mock_convert_incident.assert_any_call(mock_incident2)
        
        # Verify sink.write was called with correct parameters
        app.sink.write.assert_called_once_with(
            dest_key="incidents",
            messages=[mock_proto_incident1, mock_proto_incident2],
            value_serializer=mock_proto_serializer,
            key_extractor=app.sink.write.call_args[1]['key_extractor'],
            key_serializer=mock_str_serializer
        )
    
    @patch('mdx.analytics.core.app.app_base.convert_incident_to_protobuf_incident')
    @patch('mdx.analytics.core.app.app_base.StrBytesSerializer')
    @patch('mdx.analytics.core.app.app_base.ProtoBytesSerializer')
    def test_write_incidents_filters_none_values(self, mock_proto_serializer, 
                                                 mock_str_serializer, mock_convert_incident):
        """Test that None incidents are filtered out."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create incidents list with None values
        mock_incident = Mock(spec=Incident)
        mock_incident.sensorId = "sensor1"
        
        incidents: list[Incident] = [None, mock_incident, None]
        
        # Create mock protobuf incident
        mock_proto_incident = Mock()
        mock_proto_incident.sensorId = "sensor1"
        
        mock_convert_incident.return_value = mock_proto_incident
        
        # Write incidents
        app.write_incidents(incidents)
        
        # Verify only non-None incident was converted
        mock_convert_incident.assert_called_once_with(mock_incident)
        
        # Verify sink.write was called with only the valid incident
        app.sink.write.assert_called_once()
        messages_arg = app.sink.write.call_args[1]['messages']
        assert len(messages_arg) == 1
        assert messages_arg[0] == mock_proto_incident

    @patch('mdx.analytics.core.app.app_base.convert_behavior_to_protobuf_behavior')
    @patch('mdx.analytics.core.app.app_base.StrBytesSerializer')
    @patch('mdx.analytics.core.app.app_base.ProtoBytesSerializer')
    def test_write_anomalies(self, mock_proto_serializer, mock_str_serializer, mock_convert_behavior):
        """Test that anomalies are written correctly."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock anomalies
        mock_sensor1 = Mock(spec=Sensor)
        mock_sensor1.id = "sensor1"
        mock_sensor2 = Mock(spec=Sensor)
        mock_sensor1.id = "sensor2"
        mock_anomaly1 = Mock(spec=Behavior)
        mock_anomaly2 = Mock(spec=Behavior)
        mock_anomaly1.sensor = mock_sensor1
        mock_anomaly2.sensor = mock_sensor2

        anomalies: list[Behavior] = [mock_anomaly1, mock_anomaly2]
        
        # Create mock protobuf anomalies
        mock_proto_anomaly1 = Mock()
        mock_proto_anomaly2 = Mock()
        mock_proto_anomaly1.sensor.id = "sensor1"
        mock_proto_anomaly2.sensor.id = "sensor2"
        
        mock_convert_behavior.side_effect = [mock_proto_anomaly1, mock_proto_anomaly2]
        
        # Write anomalies
        app.write_anomalies(anomalies)
        
        # Verify anomalies were converted to protobuf
        assert mock_convert_behavior.call_count == 2
        mock_convert_behavior.assert_any_call(mock_anomaly1)
        mock_convert_behavior.assert_any_call(mock_anomaly2)
        
        # Verify sink.write was called with correct parameters
        app.sink.write.assert_called_once_with(
            dest_key="anomaly",
            messages=[mock_proto_anomaly1, mock_proto_anomaly2],
            value_serializer=mock_proto_serializer,
            key_extractor=app.sink.write.call_args[1]['key_extractor'],
            key_serializer=mock_str_serializer
        )

    @patch('mdx.analytics.core.app.app_base.StrBytesSerializer')
    @patch('mdx.analytics.core.app.app_base.ProtoBytesSerializer')
    def test_write_space_utilization(self, mock_proto_serializer, mock_str_serializer):
        """Test that space utilization data is written correctly."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock space utilization data
        mock_space_util1 = Mock(spec=extSchema.SpaceUtilization)
        mock_space_util2 = Mock(spec=extSchema.SpaceUtilization)
        
        space_utils: list[extSchema.SpaceUtilization] = [mock_space_util1, mock_space_util2]
        
        # Write space utilization
        app.write_space_utilization(space_utils)
        
        # Verify sink.write was called with correct parameters
        app.sink.write.assert_called_once_with(
            dest_key="spaceUtilization",
            messages=space_utils,
            value_serializer=mock_proto_serializer,
            key_extractor=app.sink.write.call_args[1]['key_extractor'],
            key_serializer=mock_str_serializer
        )

    @patch('mdx.analytics.core.app.app_base.InferenceClient')
    def test_behavior_clustering(self, mock_inference_client_cls):
        """Test behavior clustering functionality."""
        # Setup
        mock_inference_config = Mock()
        mock_inference_config.url = "localhost:80"
        mock_config = Mock(spec=AppConfig)
        mock_config.inference = mock_inference_config
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock behaviors
        mock_sensor = Mock(spec=Sensor)
        mock_sensor.id = "sensor1"
        mock_behavior1 = Mock(spec=Behavior)
        mock_behavior2 = Mock(spec=Behavior)
        mock_behavior1.sensor = mock_sensor
        mock_behavior2.sensor = mock_sensor

        behaviors: list[Behavior] = [mock_behavior1, mock_behavior2]
        
        # Create mock inference client
        mock_inference_client = Mock()
        mock_inference_client_cls.return_value = mock_inference_client
        mock_inference_client.server_ready.return_value = True
        mock_inference_client.infer_with_protobuf.return_value = ([0, 1], "v1.0")
        
        # Convert behaviors to protobuf for clustering
        mock_proto_behavior1 = Mock()
        mock_proto_behavior2 = Mock()
        mock_proto_behavior1.sensor.id = "sensor1"
        mock_proto_behavior2.sensor.id = "sensor1"
        mock_proto_behavior1.locations.coordinates = [1] * 20  # Min length for inference
        mock_proto_behavior2.locations.coordinates = [2] * 20
        
        with patch('mdx.analytics.core.app.app_base.convert_behavior_to_protobuf_behavior') as mock_convert:
            mock_convert.side_effect = [mock_proto_behavior1, mock_proto_behavior2]
            
            # Write behaviors with clustering
            app.write_behaviors_with_clustering(behaviors)
            
            # Verify behaviors were converted
            assert mock_convert.call_count == 2
            
            # Verify inference client was created and used
            mock_inference_client_cls.assert_called_once_with(mock_config.inference.url)
            mock_inference_client.server_ready.assert_called_once()
            mock_inference_client.infer_with_protobuf.assert_called_once_with(
                "sensor1", 
                [mock_proto_behavior1, mock_proto_behavior2]
            )
            
            # Verify cluster info was added to behaviors
            mock_proto_behavior2.info.update.assert_not_called()


class TestBaseAppInitialization:
    """Test suite for BaseApp initialization scenarios."""
    
    @patch('mdx.analytics.core.app.app_base.get_source')
    @patch('mdx.analytics.core.app.app_base.get_sink')
    @patch('mdx.analytics.core.app.app_base.DynamicCalibration')
    def test_init_with_geo_coordinate_system(self, mock_dynamic_calibration, mock_get_sink, mock_get_source):
        """Test initialization with GEO coordinate system."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        mock_config.get_bool_app_config.return_value = False
        
        mock_calibration_instance = Mock()
        mock_dynamic_calibration.return_value = mock_calibration_instance
        
        # Initialize BaseApp (using concrete subclass)
        class ConcreteApp(BaseApp):
            pass
        
        app = ConcreteApp(mock_config, "test_calibration.json")
        
        # Verify DynamicCalibration was used
        mock_dynamic_calibration.assert_called_once_with(mock_config, "test_calibration.json")
        mock_calibration_instance.start_listen.assert_called_once()
        
        # Verify other initialization
        mock_get_sink.assert_called_once_with(mock_config)
        mock_get_source.assert_called_once_with(mock_config)
    
    @patch('mdx.analytics.core.app.app_base.get_source')
    @patch('mdx.analytics.core.app.app_base.get_sink')
    @patch('mdx.analytics.core.app.app_base.DynamicCalibration')
    def test_init_with_image_coordinate_system(self, mock_dynamic_calibration, mock_get_sink, mock_get_source):
        """Test initialization with IMAGE coordinate system."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        mock_config.get_bool_app_config.return_value = True
        
        mock_calibration_instance = Mock()
        mock_dynamic_calibration.return_value = mock_calibration_instance
        
        # Initialize BaseApp (using concrete subclass)
        class ConcreteApp(BaseApp):
            pass
        
        app = ConcreteApp(mock_config, "test_calibration.json")
        
        # Verify DynamicCalibration was used
        mock_dynamic_calibration.assert_called_once_with(mock_config, "test_calibration.json")
        mock_calibration_instance.start_listen.assert_called_once()
    
    @patch('mdx.analytics.core.app.app_base.get_source')
    @patch('mdx.analytics.core.app.app_base.get_sink')
    @patch('mdx.analytics.core.app.app_base.DynamicCalibration')
    def test_init_with_none_calibration_path(self, mock_dynamic_calibration, mock_get_sink, mock_get_source):
        """Test initialization with None calibration path."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        
        mock_calibration_instance = Mock()
        mock_dynamic_calibration.return_value = mock_calibration_instance
        
        # Initialize BaseApp (using concrete subclass)
        class ConcreteApp(BaseApp):
            pass
        
        app = ConcreteApp(mock_config, None)  # type: ignore
        
        # Verify DynamicCalibration was used with None path
        mock_dynamic_calibration.assert_called_once_with(mock_config, None)
        mock_calibration_instance.start_listen.assert_called_once()


class TestBehaviorClusteringEdgeCases:
    """Test suite for BehaviorClustering edge cases."""
    
    @patch('mdx.analytics.core.app.app_base.InferenceClient')
    def test_behavior_clustering_empty_behaviors(self, mock_inference_client_cls):
        """Test behavior clustering with empty behavior list."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        mock_config.inference = Mock()
        mock_config.inference.url = "localhost:80"
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Write behaviors with clustering (empty list)
        app.write_behaviors_with_clustering([])
        
        # Verify write_proto was called with empty list
        app.sink.write.assert_called_once()
        messages_arg = app.sink.write.call_args[1]['messages']
        assert len(messages_arg) == 0
    
    @patch('mdx.analytics.core.app.app_base.InferenceClient')
    def test_behavior_clustering_short_trajectories(self, mock_inference_client_cls):
        """Test behavior clustering with short trajectories (below min length)."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        mock_config.inference = Mock()
        mock_config.inference.url = "localhost:80"
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock behaviors with short trajectories
        mock_sensor = Mock(spec=Sensor)
        mock_sensor.id = "sensor1"
        mock_behavior = Mock(spec=Behavior)
        mock_behavior.sensor = mock_sensor
        
        behaviors: list[Behavior] = [mock_behavior]
        
        # Convert behaviors to protobuf with short trajectory
        mock_proto_behavior = Mock()
        mock_proto_behavior.sensor.id = "sensor1"
        mock_proto_behavior.locations.coordinates = [1] * 10  # Below min length (20)
        
        with patch('mdx.analytics.core.app.app_base.convert_behavior_to_protobuf_behavior') as mock_convert:
            mock_convert.return_value = mock_proto_behavior
            
            # Write behaviors with clustering
            app.write_behaviors_with_clustering(behaviors)
            
            # Verify inference client was not created due to short trajectories
            mock_inference_client_cls.assert_not_called()
            
            # Verify write_proto was called with empty list
            app.sink.write.assert_called_once()
            messages_arg = app.sink.write.call_args[1]['messages']
            assert len(messages_arg) == 0
    
    @patch('mdx.analytics.core.app.app_base.InferenceClient')
    def test_behavior_clustering_server_not_ready(self, mock_inference_client_cls):
        """Test behavior clustering when inference server is not ready."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        mock_config.inference = Mock()
        mock_config.inference.url = "localhost:80"
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock behaviors
        mock_sensor = Mock(spec=Sensor)
        mock_sensor.id = "sensor1"
        mock_behavior = Mock(spec=Behavior)
        mock_behavior.sensor = mock_sensor
        
        behaviors: list[Behavior] = [mock_behavior]
        
        # Create mock inference client that's not ready
        mock_inference_client = Mock()
        mock_inference_client_cls.return_value = mock_inference_client
        mock_inference_client.server_ready.return_value = False  # Server not ready
        
        # Convert behaviors to protobuf
        mock_proto_behavior = Mock()
        mock_proto_behavior.sensor.id = "sensor1"
        mock_proto_behavior.locations.coordinates = [1] * 20  # Min length for inference
        
        with patch('mdx.analytics.core.app.app_base.convert_behavior_to_protobuf_behavior') as mock_convert:
            mock_convert.return_value = mock_proto_behavior
            
            # Write behaviors with clustering
            app.write_behaviors_with_clustering(behaviors)
            
            # Verify inference client was created but server_ready returned False
            mock_inference_client_cls.assert_called_once_with(mock_config.inference.url)
            mock_inference_client.server_ready.assert_called_once()
            mock_inference_client.infer_with_protobuf.assert_not_called()  # Should not be called
            
            # Verify write_proto was called with empty list
            app.sink.write.assert_called_once()
            messages_arg = app.sink.write.call_args[1]['messages']
            assert len(messages_arg) == 0
    
    @patch('mdx.analytics.core.app.app_base.InferenceClient')
    def test_behavior_clustering_different_cluster_index(self, mock_inference_client_cls):
        """Test behavior clustering when cluster index differs from original index."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        mock_config.inference = Mock()
        mock_config.inference.url = "localhost:80"
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock behaviors
        mock_sensor = Mock(spec=Sensor)
        mock_sensor.id = "sensor1"
        mock_behavior = Mock(spec=Behavior)
        mock_behavior.sensor = mock_sensor
        
        behaviors: list[Behavior] = [mock_behavior]
        
        # Create mock inference client
        mock_inference_client = Mock()
        mock_inference_client_cls.return_value = mock_inference_client
        mock_inference_client.server_ready.return_value = True
        mock_inference_client.infer_with_protobuf.return_value = ([5], "v1.0")  # Different cluster index
        
        # Convert behaviors to protobuf
        mock_proto_behavior = Mock()
        mock_proto_behavior.sensor.id = "sensor1"
        mock_proto_behavior.locations.coordinates = [1] * 20  # Min length for inference
        mock_proto_behavior.info.update = Mock()
        
        with patch('mdx.analytics.core.app.app_base.convert_behavior_to_protobuf_behavior') as mock_convert:
            mock_convert.return_value = mock_proto_behavior
            
            # Write behaviors with clustering
            app.write_behaviors_with_clustering(behaviors)
            
            # Verify cluster info was updated because cluster index (5) != behavior index (0)
            mock_proto_behavior.info.update.assert_called_once_with({
                "cluster.modelVersion": "v1.0",
                "cluster.index": "5",
            })
            
            # Verify write_proto was called with the behavior
            app.sink.write.assert_called_once()
            messages_arg = app.sink.write.call_args[1]['messages']
            assert len(messages_arg) == 1
            assert messages_arg[0] == mock_proto_behavior

    @patch('mdx.analytics.core.app.app_base.InferenceClient')
    def test_behavior_clustering_close(self, mock_inference_client_cls):
        """Test BehaviorClustering close method."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        mock_config.inference = Mock()
        mock_config.inference.url = "localhost:80"
        clustering = BaseApp.BehaviorClustering(mock_config)
        
        # Mock inference client
        mock_inference_client = Mock()
        mock_inference_client_cls.return_value = mock_inference_client
        
        # Initialize client by calling _get_inference_client
        clustering._get_inference_client()
        
        # Close clustering
        clustering.close()
        
        # Verify close was called
        mock_inference_client.close.assert_called_once()
    
    def test_behavior_clustering_close_no_client(self):
        """Test BehaviorClustering close method when no client exists."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        clustering = BaseApp.BehaviorClustering(mock_config)
        
        # Close clustering without initializing client
        clustering.close()
        
        # Should not raise any exceptions
        # No assertions needed as we're just testing it doesn't crash


class TestProcessorModel:
    """Test suite for the Processor model."""
    
    def test_processor_creation_with_defaults(self):
        """Test Processor creation with default values."""
        # Create mock functions
        mock_poller = Mock()
        mock_handler = Mock()
        
        # Create processor with defaults
        processor = Processor(poller=mock_poller, handler=mock_handler)
        
        # Verify attributes
        assert processor.poller == mock_poller
        assert processor.handler == mock_handler
        assert processor.num_workers == 1  # Default value
    
    def test_processor_creation_with_custom_workers(self):
        """Test Processor creation with custom worker count."""
        # Create mock functions
        mock_poller = Mock()
        mock_handler = Mock()
        
        # Create processor with custom worker count
        processor = Processor(poller=mock_poller, handler=mock_handler, num_workers=5)
        
        # Verify attributes
        assert processor.poller == mock_poller
        assert processor.handler == mock_handler
        assert processor.num_workers == 5


class TestEdgeCasesAndErrorHandling:
    """Test suite for edge cases and error handling."""
    
    def test_write_proto_with_none_key_extractor(self):
        """Test write_proto with None key_extractor."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create mock messages
        mock_messages = [Mock(), Mock()]
        
        # Write proto with None key_extractor
        app.write_proto("test_dest", mock_messages, key_extractor=None)
        
        # Verify sink.write was called with None key_extractor
        app.sink.write.assert_called_once()
        call_args = app.sink.write.call_args[1]
        assert call_args['dest_key'] == "test_dest"
        assert call_args['messages'] == mock_messages
        assert call_args['key_extractor'] is None
    
    def test_read_methods_with_none_group_id_suffix(self):
        """Test read methods with None group_id_suffix."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        app.source.poll.return_value = []
        
        # Test read_raw with None group_id_suffix
        app.read_raw(group_id_suffix=None)
        app.source.poll.assert_called()
        call_args = app.source.poll.call_args[1]
        assert call_args['group_id_suffix'] is None
        
        # Reset mock
        app.source.poll.reset_mock()
        
        # Test read_behavior with None group_id_suffix
        app.read_behavior(group_id_suffix=None)
        app.source.poll.assert_called()
        call_args = app.source.poll.call_args[1]
        assert call_args['group_id_suffix'] is None
    
    def test_write_behaviors_empty_list(self):
        """Test write_behaviors with empty list."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        mock_config.get_bool_app_config.return_value = False
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Write empty behaviors list
        app.write_behaviors([])
        
        # Verify sink.write was called with empty list
        app.sink.write.assert_called_once()
        messages_arg = app.sink.write.call_args[1]['messages']
        assert len(messages_arg) == 0
    
    def test_write_incidents_empty_list(self):
        """Test write_incidents with empty list."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Write empty incidents list
        app.write_incidents([])
        
        # Verify sink.write was called with empty list
        app.sink.write.assert_called_once()
        messages_arg = app.sink.write.call_args[1]['messages']
        assert len(messages_arg) == 0
    
    def test_write_anomalies_with_none_values(self):
        """Test write_anomalies filters None values correctly."""
        # Setup
        mock_config = Mock(spec=AppConfig)
        app = MockBaseApp(mock_config, "test_calibration.json")
        
        # Create anomalies list with None values
        mock_sensor = Mock(spec=Sensor)
        mock_sensor.id = "sensor1"
        mock_anomaly = Mock(spec=Behavior)
        mock_anomaly.sensor = mock_sensor
        
        anomalies: list[Behavior] = [None, mock_anomaly, None]
        
        # Create mock protobuf anomaly
        mock_proto_anomaly = Mock()
        mock_proto_anomaly.sensor.id = "sensor1"
        
        with patch('mdx.analytics.core.app.app_base.convert_behavior_to_protobuf_behavior') as mock_convert:
            mock_convert.return_value = mock_proto_anomaly
            
            # Write anomalies
            app.write_anomalies(anomalies)
            
            # Verify only non-None anomaly was converted
            mock_convert.assert_called_once_with(mock_anomaly)
            
            # Verify sink.write was called with only the valid anomaly
            app.sink.write.assert_called_once()
            messages_arg = app.sink.write.call_args[1]['messages']
            assert len(messages_arg) == 1
            assert messages_arg[0] == mock_proto_anomaly
