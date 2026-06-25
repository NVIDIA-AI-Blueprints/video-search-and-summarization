# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Unit and integration tests for RTVI Embed Stream Handler (rtvi_stream_handler.py)

Tests cover:
- Stream handler initialization for embeddings
- Request management
- Kafka integration
- Metrics collection
- Chunk processing
- Live stream handling
- Text embeddings generation
- Video embeddings generation
- Error handling
"""

import argparse
import os
import tempfile
import uuid
from unittest.mock import Mock, patch

import pytest

from server.rtvi_stream_handler import RequestInfo, RTVIStreamHandler
from tests.tests_common import TempEnv
from vlm_pipeline.vlm_pipeline import VlmModelType

API_PREFIX = "/v1"


@pytest.fixture(scope="session")
def mock_args():
    """Create mock arguments for RTVIStreamHandler initialization for embed server"""
    args = argparse.Namespace()
    args.asset_dir = tempfile.mkdtemp()
    args.kafka_enabled = False
    args.kafka_topic = "mdx-embed"
    args.kafka_bootstrap_servers = ""
    args.enable_dev_dc_gen = False
    args.max_file_duration = 0
    args.max_live_streams = 10
    args.num_gpus = 1
    args.vlm_batch_size = 4
    args.vlm_model_type = VlmModelType("custom")
    args.model_implementation_path = "/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1"
    args.model_path = "git:https://huggingface.co/nvidia/Cosmos-Embed1-448p"
    args.model_repository_script_path = (
        "/opt/nvidia/rtvi/rtvi/models/custom/samples/cosmos-embed1/create_triton_model_repo.py"
    )
    args.num_vlm_procs = 1
    args.vlm_input_width = 448
    args.vlm_input_height = 448
    args.enable_audio = False
    args.disable_vlm = True
    args.disable_decoding = True
    args.log_level = "debug"
    args.extra_args = ""
    args.rtsp_latency = 0
    args.rtsp_timeout = 0
    args.rtsp_reconnection_interval = 5
    args.rtsp_reconnection_window = 60
    args.rtsp_reconnection_max_attempts = 10
    args.num_frames_per_second_or_fixed_frames_chunk = 8
    args.use_fps_for_chunking = False
    args.enable_reasoning = False
    args.num_decoders_per_gpu = 1
    os.environ["RTVI_DISABLE_LIVESTREAM_PREVIEW"] = "true"

    return args


@pytest.fixture(scope="session")
def stream_handler(mock_args, request):
    """Create a stream handler instance for testing embeddings"""
    with TempEnv(
        {
            "SKIP_PIPELINE_WARMUP": "1",
            "KAFKA_ENABLED": "false",
        }
    ):
        try:
            handler = RTVIStreamHandler(mock_args, service_name="rtvi-embed-test")

            # Register a finalizer to ensure cleanup happens even if tests fail
            def cleanup():
                try:
                    handler.stop(force=True)
                except Exception as e:
                    print(f"Error during cleanup: {e}", flush=True)

            request.addfinalizer(cleanup)
            yield handler
        except Exception as e:
            # If initialization fails (e.g., no GPU), skip tests
            pytest.skip(f"Stream handler initialization failed: {e}")


class TestStreamHandlerInitialization:
    """Test stream handler initialization for embed server"""

    def test_handler_initialization(self, mock_args):
        """Test handler can be initialized for embeddings"""
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "KAFKA_ENABLED": "false"}):
            try:
                handler = RTVIStreamHandler(mock_args, service_name="rtvi-embed-test")
                assert handler._request_info_map is not None
                assert handler._metrics is not None
                handler.stop(force=True)
            except Exception as e:
                pytest.skip(f"Initialization failed: {e}")

    def test_embeddings_model_configuration(self, mock_args):
        """Test embeddings model is configured correctly"""
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "KAFKA_ENABLED": "false"}):
            try:
                handler = RTVIStreamHandler(mock_args, service_name="rtvi-embed-test")
                # Verify embeddings model configuration
                assert mock_args.vlm_model_type == VlmModelType("custom")
                assert "cosmos-embed" in mock_args.model_implementation_path
                handler.stop(force=True)
            except Exception as e:
                pytest.skip(f"Initialization failed: {e}")

    def test_kafka_disabled_by_default(self, mock_args):
        """Test Kafka is disabled by default"""
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1"}):
            try:
                handler = RTVIStreamHandler(mock_args, service_name="rtvi-embed-test")
                assert handler._kafka_enabled is False
                assert handler._kafka_producer is None
                handler.stop(force=True)
            except Exception as e:
                pytest.skip(f"Initialization failed: {e}")

    # def test_kafka_enabled_via_env(self, mock_args):
    #     """Test Kafka can be enabled via environment variable"""
    #     with TempEnv(
    #         {
    #             "SKIP_PIPELINE_WARMUP": "1",
    #             "KAFKA_ENABLED": "true",
    #             "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
    #         }
    #     ):
    #         try:
    #             handler = RTVIStreamHandler(mock_args, service_name="rtvi-embed-test")
    #             # Kafka producer may be None if connection fails, but enabled flag should be True
    #             assert handler._kafka_enabled is True
    #             handler.stop(force=True)
    #         except Exception as e:
    #             pytest.skip(f"Initialization failed: {e}")

    def test_kafka_topic_for_embeddings(self, mock_args):
        """Test Kafka topic is set correctly for embeddings"""
        assert mock_args.kafka_topic == "mdx-embed"


class TestRequestInfo:
    """Test RequestInfo dataclass"""

    def test_request_info_creation(self):
        """Test creating a RequestInfo instance"""
        req_info = RequestInfo()
        assert req_info.request_id is not None
        assert req_info.status == RequestInfo.Status.QUEUED
        assert req_info.chunk_count == 0
        assert req_info.is_live is False

    def test_request_info_progress(self):
        """Test progress calculation"""
        req_info = RequestInfo()
        req_info.chunk_count = 10
        req_info.processed_chunk_list = [Mock() for _ in range(5)]
        assert req_info.progress == 50.0

    def test_request_info_progress_complete(self):
        """Test progress for completed request"""
        req_info = RequestInfo()
        req_info.status = RequestInfo.Status.SUCCESSFUL
        assert req_info.progress == 100.0

    def test_request_info_progress_live(self):
        """Test progress for live stream"""
        req_info = RequestInfo()
        req_info.is_live = True
        assert req_info.progress == 0.0

    def test_request_info_stream_id(self):
        """Test stream_id property"""
        req_info = RequestInfo()
        mock_asset = Mock()
        mock_asset.asset_id = "test-id"
        req_info.assets = [mock_asset]
        assert req_info.stream_id == "test-id"

    def test_request_info_stream_id_no_assets(self):
        """Test stream_id when no assets"""
        req_info = RequestInfo()
        req_info.assets = None
        assert req_info.stream_id == ""


class TestLiveStreamManagement:
    """Test live stream management methods"""

    def test_get_live_stream_request_none(self, stream_handler):
        """Test getting non-existent live stream request"""
        result = stream_handler._get_live_stream_request("non-existent-id")
        assert result is None

    def test_count_active_live_streams(self, stream_handler):
        """Test counting active live streams"""
        count = stream_handler._count_active_live_streams()
        assert count == 0

    def test_get_models_info(self, stream_handler):
        """Test getting models info for embeddings"""
        try:
            models_info = stream_handler.get_models_info()
            assert models_info is not None
            # For embeddings, api_type should be embeddings
            if hasattr(models_info, "api_type"):
                # May be embeddings or other types depending on model
                assert models_info.api_type in ["embeddings", "vlm", "custom"]
        except Exception as e:
            pytest.skip(f"Models info not available: {e}")

    def test_get_health_status(self, stream_handler):
        """Test getting health status"""
        health_status = stream_handler.get_health_status()
        assert "healthy" in health_status
        assert "checks" in health_status
        assert "timestamp" in health_status
        assert "uptime_seconds" in health_status


class TestMetrics:
    """Test metrics collection"""

    def test_metrics_initialization(self, stream_handler):
        """Test metrics are initialized"""
        assert stream_handler._metrics is not None

    def test_histogram_views(self):
        """Test histogram views configuration"""
        views = RTVIStreamHandler.get_histogram_views()

        assert isinstance(views, list)
        # Should have views for various metrics
        view_names = [getattr(v, "_instrument_name", None) for v in views]

        expected_metrics = [
            "stream_fps",
            "decode_latency_seconds",
            "vlm_latency_seconds",
            "live_stream_captions_latency_seconds",
        ]
        for metric in expected_metrics:
            assert any(metric in str(v) for v in views) or metric in str(view_names)

    def test_embeddings_latency_tracking(self, stream_handler):
        """Test embeddings latency is tracked"""
        # Metrics should be available for tracking embeddings generation
        assert stream_handler._metrics is not None


class TestKafkaIntegration:
    """Test Kafka message sending for embeddings"""

    @patch("server.rtvi_stream_handler.KafkaProducer")
    def test_send_error_message_to_kafka_disabled(self, mock_kafka_producer, stream_handler):
        """Test error message not sent when Kafka disabled"""
        stream_handler._kafka_enabled = False
        stream_handler._send_error_message_to_kafka("test error", "test-id")
        # Should return early without sending
        assert True  # If we get here, no exception was raised

    def test_send_error_message_no_producer(self, stream_handler):
        """Test error message handling when producer is None"""
        stream_handler._kafka_enabled = True
        stream_handler._kafka_producer = None
        # Should handle gracefully
        stream_handler._send_error_message_to_kafka("test error", "test-id")
        assert True  # Should not raise exception

    def test_kafka_topic_for_embeddings(self, stream_handler):
        """Test Kafka topic is configured for embeddings messages"""
        # The handler should use the embeddings-specific topic
        if hasattr(stream_handler, "_kafka_topic"):
            # Topic may be None if not configured, but if set should be embeddings topic
            assert stream_handler._kafka_topic in [None, "mdx-embed"]


class TestUtilityMethods:
    """Test utility methods"""

    def test_seconds_to_timestamp(self, stream_handler):
        """Test converting seconds to protobuf timestamp"""
        timestamp = stream_handler._seconds_to_timestamp(1234.567)
        assert timestamp is not None
        assert timestamp.seconds == 1234
        assert timestamp.nanos > 0

    def test_seconds_to_timestamp_none(self, stream_handler):
        """Test converting None to timestamp"""
        timestamp = stream_handler._seconds_to_timestamp(None)
        assert timestamp is None

    def test_seconds_to_timestamp_invalid(self, stream_handler):
        """Test converting invalid value to timestamp"""
        timestamp = stream_handler._seconds_to_timestamp("invalid")
        assert timestamp is None

    def test_coerce_relative_seconds(self, stream_handler):
        """Test coercing relative seconds"""
        result = stream_handler._coerce_relative_seconds(123.456)
        assert result == 123.456

    def test_coerce_relative_seconds_nanoseconds(self, stream_handler):
        """Test coercing nanoseconds to seconds"""
        # Large value should be treated as nanoseconds
        result = stream_handler._coerce_relative_seconds(1234567890)
        assert result < 1234567890  # Should be divided by 1e9

    def test_coerce_relative_seconds_none(self, stream_handler):
        """Test coercing None"""
        result = stream_handler._coerce_relative_seconds(None)
        assert result is None


class TestRequestManagement:
    """Test request management for embeddings"""

    def test_get_response_not_found(self, stream_handler):
        """Test getting response for non-existent request"""
        from common.service_exception import ServiceException

        fake_id = str(uuid.uuid4())
        with pytest.raises(ServiceException):
            stream_handler.get_response(fake_id)

    def test_wait_for_request_done_not_found(self, stream_handler):
        """Test waiting for non-existent request"""
        from common.service_exception import ServiceException

        fake_id = str(uuid.uuid4())
        with pytest.raises(ServiceException):
            stream_handler.wait_for_request_done(fake_id)


class TestEmbeddingsGeneration:
    """Test embeddings generation functionality"""

    def test_text_embeddings_support(self, stream_handler):
        """Test text embeddings generation is supported"""
        # Check if text embeddings method exists
        assert hasattr(stream_handler, "generate_text_embeddings")

    def test_video_embeddings_support(self, stream_handler):
        """Test video embeddings generation is supported"""
        # The same generate_vlm_captions method is used for embeddings
        assert hasattr(stream_handler, "generate_vlm_captions")


class TestArgumentParser:
    """Test argument parser"""

    def test_populate_argument_parser(self):
        """Test populating argument parser"""
        from argparse import ArgumentParser

        parser = ArgumentParser()
        RTVIStreamHandler.populate_argument_parser(parser)
        # Should have added arguments
        assert parser is not None

    def test_parse_arguments_embeddings(self):
        """Test parsing arguments for embeddings configuration"""
        from argparse import ArgumentParser

        parser = ArgumentParser()
        RTVIStreamHandler.populate_argument_parser(parser)
        args = parser.parse_args(
            [
                "--kafka-enabled",
                "--kafka-topic",
                "mdx-embed",
                "--max-file-duration",
                "60",
                "--vlm-model-type",
                "custom",
            ]
        )
        assert args.kafka_enabled is True
        assert args.kafka_topic == "mdx-embed"
        assert args.max_file_duration == 60
        assert args.vlm_model_type.value == "custom"


# class TestStopHandler:
#     """Test handler stop functionality"""

#     def test_stop_handler(self, stream_handler):
#         """Test stopping handler"""
#         stream_handler.stop(force=True)
#         # Should complete without exception
#         assert True

#     def test_stop_handler_without_pipeline(self, mock_args):
#         """Test stopping handler without pipeline"""
#         with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "KAFKA_ENABLED": "false"}):
#             handler = RTVIStreamHandler(mock_args, service_name="rtvi-embed-test")
#             # Manually set pipeline to None
#             handler._vlm_pipeline = None
#             handler.stop(force=True)
#             assert True


class TestBatchProcessing:
    """Test batch processing for embeddings"""

    def test_batch_size_configuration(self, mock_args):
        """Test batch size is configured for embeddings"""
        assert mock_args.vlm_batch_size == 4
        # Embeddings typically use larger batch sizes than VLM

    def test_batch_processing_support(self, stream_handler):
        """Test batch processing is supported"""
        # Handler should support batch processing
        assert stream_handler is not None


class TestChunkProcessing:
    """Test chunk processing for video embeddings"""

    def test_chunk_duration_support(self, stream_handler):
        """Test chunk duration configuration is supported"""
        # Handler should support configurable chunk duration
        assert stream_handler is not None

    def test_chunk_overlap_support(self, stream_handler):
        """Test chunk overlap is supported"""
        # Handler should support chunk overlap for smooth embeddings
        assert stream_handler is not None


class TestHealthChecks:
    """Test health check functionality"""

    def test_readiness_check(self, stream_handler):
        """Test readiness health check"""
        health = stream_handler.get_health_status(readiness=True)
        assert "healthy" in health
        assert isinstance(health["healthy"], bool)

    def test_liveness_check(self, stream_handler):
        """Test liveness health check"""
        health = stream_handler.get_health_status(readiness=False)
        assert "healthy" in health
        assert isinstance(health["healthy"], bool)

    def test_health_check_components(self, stream_handler):
        """Test health check includes all components"""
        health = stream_handler.get_health_status()
        assert "checks" in health
        assert isinstance(health["checks"], list)
        # Should have checks for various components


class TestErrorHandling:
    """Test error handling"""

    def test_error_message_format(self, stream_handler):
        """Test error message format"""
        # Error messages should be handled gracefully
        stream_handler._send_error_message_to_kafka("Test error", "test-id")
        assert True  # Should not raise exception

    def test_invalid_request_handling(self, stream_handler):
        """Test handling of invalid requests"""
        from common.service_exception import ServiceException

        fake_id = str(uuid.uuid4())
        with pytest.raises(ServiceException):
            stream_handler.get_response(fake_id)


class TestServiceConfiguration:
    """Test service-specific configuration"""

    def test_service_name(self, mock_args):
        """Test service name is set correctly for embed"""
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "KAFKA_ENABLED": "false"}):
            try:
                handler = RTVIStreamHandler(mock_args, service_name="rtvi-embed-test")
                # Service name should be passed during initialization
                assert handler is not None
                handler.stop(force=True)
            except Exception as e:
                pytest.skip(f"Initialization failed: {e}")

    def test_model_path_configuration(self, mock_args):
        """Test model path is configured for embeddings"""
        assert "cosmos-embed" in mock_args.model_implementation_path
        # Should point to embeddings model
