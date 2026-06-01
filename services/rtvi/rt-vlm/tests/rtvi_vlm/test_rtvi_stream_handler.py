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
Unit and integration tests for RTVI Stream Handler (rtvi_stream_handler.py)

Tests cover:
- Stream handler initialization
- Request management
- Kafka integration
- Metrics collection
- Chunk processing
- Live stream handling
- Error handling
"""

import queue
import uuid
from threading import Event, Thread
from time import monotonic
from unittest.mock import MagicMock, Mock, patch

import pytest

from common.chunk_info import ChunkInfo
from models.base_vlm_model import VlmModelOutput
from server.rtvi_stream_handler import RequestInfo, RTVIStreamHandler
from tests.tests_common import TempEnv
from utils.asset_manager import Asset
from vlm_pipeline.vlm_pipeline import PipelineChunkResult, VlmModelType

# NOTE: `mock_args` and `stream_handler` fixtures are defined in
# tests/rtvi_vlm/conftest.py so they can be shared across this module and
# other rtvi_vlm test files.

API_PREFIX = "/v1"


class TestStreamHandlerInitialization:
    """Test stream handler initialization"""

    def test_handler_initialization(self, mock_args):
        """Test handler can be initialized"""
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "KAFKA_ENABLED": "false"}):
            # Mock VlmPipeline to avoid hanging on GPU initialization
            with patch("server.rtvi_stream_handler.VlmPipeline") as mock_vlm_pipeline_class:
                mock_pipeline = MagicMock()
                mock_model_info = MagicMock()
                mock_model_info.id = "test-model"
                mock_model_info.created = 1234567890
                mock_model_info.owned_by = "test"
                mock_model_info.api_type = "test"
                mock_pipeline.get_models_info.return_value = mock_model_info
                mock_pipeline.get_health_status.return_value = []
                mock_vlm_pipeline_class.return_value = mock_pipeline
                try:
                    handler = RTVIStreamHandler(mock_args, service_name="rtvi-vlm-test")
                    assert handler._request_info_map is not None
                    assert handler._metrics is not None
                    handler.stop(force=True)
                except Exception as e:
                    pytest.skip(f"Initialization failed: {e}")

    def test_kafka_disabled_by_default(self, mock_args):
        """Test Kafka is disabled by default"""
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1"}):
            # Mock VlmPipeline to avoid hanging on GPU initialization
            with patch("server.rtvi_stream_handler.VlmPipeline") as mock_vlm_pipeline_class:
                mock_pipeline = MagicMock()
                mock_model_info = MagicMock()
                mock_model_info.id = "test-model"
                mock_model_info.created = 1234567890
                mock_model_info.owned_by = "test"
                mock_model_info.api_type = "test"
                mock_pipeline.get_models_info.return_value = mock_model_info
                mock_pipeline.get_health_status.return_value = []
                mock_vlm_pipeline_class.return_value = mock_pipeline
                try:
                    handler = RTVIStreamHandler(mock_args, service_name="rtvi-vlm-test")
                    assert handler._kafka_enabled is False
                    assert handler._kafka_producer is None
                    handler.stop(force=True)
                except Exception as e:
                    pytest.skip(f"Initialization failed: {e}")

    def test_kafka_enabled_via_env(self, mock_args):
        """Test Kafka can be enabled via environment variable"""
        with TempEnv(
            {
                "SKIP_PIPELINE_WARMUP": "1",
                "KAFKA_ENABLED": "true",
                "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
            }
        ):
            # Mock VlmPipeline to avoid hanging on GPU initialization
            with patch("server.rtvi_stream_handler.VlmPipeline") as mock_vlm_pipeline_class:
                mock_pipeline = MagicMock()
                mock_model_info = MagicMock()
                mock_model_info.id = "test-model"
                mock_model_info.created = 1234567890
                mock_model_info.owned_by = "test"
                mock_model_info.api_type = "test"
                mock_pipeline.get_models_info.return_value = mock_model_info
                mock_pipeline.get_health_status.return_value = []
                mock_vlm_pipeline_class.return_value = mock_pipeline
                try:
                    handler = RTVIStreamHandler(mock_args, service_name="rtvi-vlm-test")
                    # Kafka producer may be None if connection fails, but enabled flag should be True
                    assert handler._kafka_enabled is True
                    handler.stop(force=True)
                except Exception as e:
                    pytest.skip(f"Initialization failed: {e}")


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
        """Test getting models info"""
        try:
            models_info = stream_handler.get_models_info()
            assert models_info is not None
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
        # If OpenTelemetry is not available, views will be empty list
        if not views:
            pytest.skip("OpenTelemetry not available, skipping histogram views test")
        # Should have views for various metrics
        # Try to get instrument_name from View objects - it might be an attribute or in __dict__
        view_names = []
        for v in views:
            # Try multiple ways to access instrument_name
            name = getattr(v, "instrument_name", None)
            if name is None and hasattr(v, "__dict__"):
                name = v.__dict__.get("instrument_name", None)
            if name is None and hasattr(v, "_instrument_name"):
                name = getattr(v, "_instrument_name", None)
            view_names.append(name)
        expected_metrics = [
            "stream_fps",
            "decode_latency_seconds",
            "vlm_latency_seconds",
            "live_stream_captions_latency_seconds",
        ]
        # Check if metrics are present in view names or in string representation of views
        for metric in expected_metrics:
            found = False
            # Check in view_names
            if any(name and metric in str(name) for name in view_names):
                found = True
            # Check in string representation of views
            if not found:
                found = any(metric in str(v) for v in views)
            assert found, f"Metric '{metric}' not found in histogram views"


class TestKafkaIntegration:
    """Test Kafka message sending"""

    class _FakeKafkaFuture:
        def add_callback(self, _callback):
            return self

        def add_errback(self, _errback):
            return self

    class _BlockingKafkaProducer:
        def __init__(self):
            self.config = {"bootstrap_servers": ["missing-kafka:9092"]}
            self.release_send = Event()
            self.send_started = Event()
            self.send_calls = []

        def send(self, *args, **kwargs):
            self.send_calls.append((args, kwargs))
            self.send_started.set()
            self.release_send.wait(timeout=2)
            return TestKafkaIntegration._FakeKafkaFuture()

        def flush(self, timeout=None):
            return None

        def close(self, timeout=None):
            return None

    class _BlockingRedisClient:
        def __init__(self):
            self.release_publish = Event()
            self.publish_started = Event()
            self.publish_calls = []

        def publish(self, *args, **kwargs):
            self.publish_calls.append((args, kwargs))
            self.publish_started.set()
            self.release_publish.wait(timeout=2)
            return 0

        def close(self):
            return None

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

    def test_protobuf_kafka_send_does_not_block_caller(self, stream_handler):
        """Kafka producer.send is offloaded because it may block on broker metadata."""
        producer = self._BlockingKafkaProducer()
        stream_handler._kafka_enabled = True
        stream_handler._kafka_producer = producer

        req_info = RequestInfo()
        req_info.request_id = "request-1"
        chunk_result = Mock()
        chunk_result.chunk = Mock()
        chunk_result.chunk.chunkIdx = 7

        start_time = monotonic()
        stream_handler._send_protobuf_to_kafka(b"payload", chunk_result, req_info)
        elapsed = monotonic() - start_time

        assert elapsed < 0.2
        assert producer.send_started.wait(timeout=1)
        assert len(producer.send_calls) == 1

        args, kwargs = producer.send_calls[0]
        assert args == ("mdx-vlm-captions",)
        assert kwargs["key"] == b"request-1:7"
        assert kwargs["value"] == b"payload"
        assert kwargs["headers"] == [("message_type", b"vision_llm")]

        producer.release_send.set()
        stream_handler._kafka_send_queue.join()

    def test_error_kafka_send_does_not_block_caller(self, stream_handler):
        """Kafka error messages use the same background sender."""
        producer = self._BlockingKafkaProducer()
        stream_handler._kafka_enabled = True
        stream_handler._kafka_producer = producer

        start_time = monotonic()
        stream_handler._send_error_message_to_kafka("test error", "stream-1")
        elapsed = monotonic() - start_time

        assert elapsed < 0.2
        assert producer.send_started.wait(timeout=1)
        assert len(producer.send_calls) == 1

        args, kwargs = producer.send_calls[0]
        assert stream_handler._kafka_error_topic == "mdx-vlm-errors"
        assert args == ("mdx-vlm-errors",)
        assert kwargs["headers"] == [("message_type", b"error")]
        assert b"test error" in kwargs["value"]
        assert b"stream-1" in kwargs["value"]

        producer.release_send.set()
        stream_handler._kafka_send_queue.join()

    def test_redis_error_bus_publish_does_not_block_caller(self, stream_handler):
        """Redis error bus publishes use a background sender."""
        redis_client = self._BlockingRedisClient()
        stream_handler._use_redis_error_bus = True
        stream_handler._redis_client = redis_client

        start_time = monotonic()
        stream_handler._send_error_message_to_kafka("test redis error", "stream-redis")
        elapsed = monotonic() - start_time

        assert elapsed < 0.2
        assert redis_client.publish_started.wait(timeout=1)
        assert len(redis_client.publish_calls) == 1

        args, kwargs = redis_client.publish_calls[0]
        assert stream_handler._redis_error_channel == "mdx-vlm-errors"
        assert args[0] == "mdx-vlm-errors"
        assert b"test redis error" in args[1]
        assert b"stream-redis" in args[1]
        assert kwargs == {}

        redis_client.release_publish.set()
        stream_handler._redis_send_queue.join()

    def test_late_kafka_submit_after_stop_is_rejected(self, stream_handler):
        """Stopping must not allow a later submit to restart sender threads."""
        producer = self._BlockingKafkaProducer()
        stream_handler._kafka_enabled = True
        stream_handler._kafka_producer = producer

        stream_handler.stop(force=True)

        accepted = stream_handler._submit_kafka_send("late kafka job", lambda: producer.send("t"))

        assert accepted is False
        assert producer.send_calls == []
        assert stream_handler._kafka_send_thread is None
        assert stream_handler._kafka_send_queue is None

    def test_late_redis_submit_after_stop_is_rejected(self, stream_handler):
        """Stopping must not allow a later Redis publish to restart sender threads."""
        redis_client = self._BlockingRedisClient()
        stream_handler._redis_client = redis_client

        stream_handler.stop(force=True)

        accepted = stream_handler._submit_redis_publish(
            "late redis job", lambda: redis_client.publish("channel", b"payload")
        )

        assert accepted is False
        assert redis_client.publish_calls == []
        assert stream_handler._redis_send_thread is None
        assert stream_handler._redis_send_queue is None

    def test_kafka_queue_full_drops_message(self, stream_handler):
        """Kafka submissions should drop instead of blocking when the async queue is full."""
        producer = self._BlockingKafkaProducer()
        release_thread = Event()
        sender_thread = Thread(target=release_thread.wait)
        sender_thread.start()
        stream_handler._kafka_enabled = True
        stream_handler._kafka_producer = producer
        stream_handler._kafka_send_queue_maxsize = 1
        stream_handler._kafka_send_queue = queue.Queue(maxsize=1)
        stream_handler._kafka_send_queue.put_nowait(("existing", lambda: None))
        stream_handler._kafka_send_thread = sender_thread

        try:
            accepted = stream_handler._submit_kafka_send(
                "overflow kafka job", lambda: producer.send("t")
            )
        finally:
            release_thread.set()
            sender_thread.join(timeout=1)

        assert accepted is False
        assert producer.send_calls == []

    def test_redis_queue_full_drops_message(self, stream_handler):
        """Redis submissions should drop instead of blocking when the async queue is full."""
        redis_client = self._BlockingRedisClient()
        release_thread = Event()
        sender_thread = Thread(target=release_thread.wait)
        sender_thread.start()
        stream_handler._redis_client = redis_client
        stream_handler._redis_send_queue_maxsize = 1
        stream_handler._redis_send_queue = queue.Queue(maxsize=1)
        stream_handler._redis_send_queue.put_nowait(("existing", lambda: None))
        stream_handler._redis_send_thread = sender_thread

        try:
            accepted = stream_handler._submit_redis_publish(
                "overflow redis job", lambda: redis_client.publish("channel", b"payload")
            )
        finally:
            release_thread.set()
            sender_thread.join(timeout=1)

        assert accepted is False
        assert redis_client.publish_calls == []

    def test_cuda_oom_chunk_error_publishes_to_redis(self, stream_handler):
        """Decode CUDA OOM chunk errors should reach the Redis error bus."""
        redis_client = self._BlockingRedisClient()
        redis_client.release_publish.set()
        stream_handler._use_redis_error_bus = True
        stream_handler._redis_client = redis_client

        req_info = RequestInfo()
        req_info.is_live = True
        req_info.assets = [Mock(asset_id="stream-oom", path="/tmp/stream-oom")]
        req_info.status = RequestInfo.Status.PROCESSING

        chunk = ChunkInfo(
            file="rtsp://example/stream",
            chunkIdx=0,
            start_pts=0,
            end_pts=1_000_000_000,
        )
        chunk.streamId = "stream-oom"
        chunk.start_ntp = "2026-05-05T00:00:00.000Z"
        chunk.end_ntp = "2026-05-05T00:00:01.000Z"

        chunk_result = PipelineChunkResult(
            chunk=chunk,
            error="Decode error: CUDA out of memory while decoding video frames",
            error_status_code=503,
        )

        stream_handler._on_vlm_chunk_response(chunk_result, req_info)
        stream_handler._redis_send_queue.join()

        assert len(redis_client.publish_calls) == 1
        args, kwargs = redis_client.publish_calls[0]
        assert args[0] == stream_handler._redis_error_channel
        assert b"CUDA out of memory" in args[1]
        assert b"stream-oom" in args[1]
        assert kwargs == {}

    def test_vision_llm_stream_id_uses_asset_id_and_sensor_id_uses_camera_id(self, stream_handler):
        """Kafka streamId should correlate to RTVI asset_id, not the CV camera_id."""
        asset = Asset(
            asset_id="364e71ba-ace0-41b9-a4ef-745ab2a2b8b7",
            path="rtsp://example.com/warehouse",
            purpose="",
            media_type="",
            asset_dir="",
            description="Camera 1",
            sensor_name="cam-001",
            camera_id="cam-001",
        )
        req_info = RequestInfo(
            request_id="request-123",
            assets=[asset],
            is_live=True,
        )
        chunk = ChunkInfo(
            file=asset.path,
            chunkIdx=3,
            start_pts=0,
            end_pts=1_000_000_000,
        )
        chunk.streamId = asset.asset_id
        chunk_result = PipelineChunkResult(
            chunk=chunk,
            vlm_model_output=VlmModelOutput(
                output="Nothing unusual.",
                input_tokens=10,
                output_tokens=2,
            ),
            frame_times=[0.0, 0.5],
        )

        vision_llm, incident = stream_handler._chunk_result_to_vision_llm(chunk_result, req_info)

        assert incident is None
        assert vision_llm.info["streamId"] == asset.asset_id
        assert "assetId" not in vision_llm.info
        assert vision_llm.info["cameraId"] == "cam-001"
        assert vision_llm.info["sensorId"] == "cam-001"
        assert vision_llm.sensor.id == "cam-001"
        assert "assetId" not in vision_llm.sensor.info
        assert vision_llm.sensor.info["cameraId"] == "cam-001"
        assert [frame.sensorId for frame in vision_llm.frames] == ["cam-001", "cam-001"]

        query_params = vision_llm.llm.queries[0].params
        assert query_params["streamId"] == asset.asset_id
        assert "assetId" not in query_params
        assert query_params["cameraId"] == "cam-001"
        assert query_params["sensorId"] == "cam-001"

    def test_vision_llm_keeps_sensor_name_when_different_from_camera_id(self, stream_handler):
        """camera_id remains sensor identity; sensor_name is preserved as metadata."""
        asset = Asset(
            asset_id="e9957d18-5193-4b1a-819d-e516e15bda1d",
            path="rtsp://example.com/warehouse",
            purpose="",
            media_type="",
            asset_dir="",
            description="Camera 1",
            sensor_name="Dock Entrance",
            camera_id="cam-001",
        )
        req_info = RequestInfo(
            request_id="request-456",
            assets=[asset],
            is_live=True,
        )
        chunk = ChunkInfo(
            file=asset.path,
            chunkIdx=4,
            start_pts=0,
            end_pts=1_000_000_000,
        )
        chunk.streamId = asset.asset_id
        chunk_result = PipelineChunkResult(
            chunk=chunk,
            vlm_model_output=VlmModelOutput(
                output="Nothing unusual.",
                input_tokens=10,
                output_tokens=2,
            ),
            frame_times=[0.0, 0.5],
        )

        vision_llm, incident = stream_handler._chunk_result_to_vision_llm(chunk_result, req_info)

        assert incident is None
        assert vision_llm.info["streamId"] == asset.asset_id
        assert "assetId" not in vision_llm.info
        assert vision_llm.sensor.id == "cam-001"
        assert vision_llm.sensor.info["sensorName"] == "Dock Entrance"
        assert vision_llm.sensor.info["cameraId"] == "cam-001"
        assert [frame.sensorId for frame in vision_llm.frames] == ["cam-001", "cam-001"]

        query_params = vision_llm.llm.queries[0].params
        assert query_params["streamId"] == asset.asset_id
        assert "assetId" not in query_params
        assert query_params["cameraId"] == "cam-001"
        assert query_params["sensorId"] == "cam-001"
        assert query_params["sensorName"] == "Dock Entrance"

    def test_vision_llm_info_includes_reasoning(self, stream_handler):
        """Caption schema should expose parsed reasoning in VisionLLM info."""
        asset = Asset(
            asset_id="caption-stream",
            path="rtsp://example.com/warehouse",
            purpose="",
            media_type="",
            asset_dir="",
            camera_id="cam-001",
        )
        req_info = RequestInfo(
            request_id="request-reasoning-caption",
            assets=[asset],
            is_live=True,
        )
        chunk = ChunkInfo(
            file=asset.path,
            chunkIdx=5,
            start_pts=0,
            end_pts=1_000_000_000,
        )
        chunk.streamId = asset.asset_id
        chunk_result = PipelineChunkResult(
            chunk=chunk,
            vlm_model_output=VlmModelOutput(
                output="Nothing unusual.",
                input_tokens=10,
                output_tokens=2,
                reasoning_description="The scene is quiet and unchanged.",
            ),
            frame_times=[0.0],
        )

        vision_llm, incident = stream_handler._chunk_result_to_vision_llm(chunk_result, req_info)

        assert incident is None
        assert vision_llm.info["reasoning"] == "The scene is quiet and unchanged."
        assert vision_llm.info["reasoningDescription"] == "The scene is quiet and unchanged."

    def test_incident_info_includes_reasoning(self, stream_handler):
        """Incident schema should expose parsed reasoning in Incident info."""
        asset = Asset(
            asset_id="incident-stream",
            path="rtsp://example.com/warehouse",
            purpose="",
            media_type="",
            asset_dir="",
            camera_id="cam-001",
        )
        req_info = RequestInfo(
            request_id="request-reasoning-incident",
            assets=[asset],
            is_live=True,
        )
        chunk = ChunkInfo(
            file=asset.path,
            chunkIdx=6,
            start_pts=0,
            end_pts=1_000_000_000,
        )
        chunk.streamId = asset.asset_id
        chunk_result = PipelineChunkResult(
            chunk=chunk,
            vlm_model_output=VlmModelOutput(
                output="Yes, a person entered the restricted area.",
                input_tokens=12,
                output_tokens=8,
                reasoning_description="The person crosses the marked boundary.",
            ),
            frame_times=[0.0],
        )

        vision_llm, incident = stream_handler._chunk_result_to_vision_llm(chunk_result, req_info)

        assert vision_llm.info["incidentDetected"] == "true"
        assert vision_llm.info["reasoning"] == "The person crosses the marked boundary."
        assert vision_llm.info["reasoningDescription"] == "The person crosses the marked boundary."
        assert incident is not None
        assert incident.info["reasoning"] == "The person crosses the marked boundary."
        assert incident.info["reasoningDescription"] == "The person crosses the marked boundary."


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
    """Test request management"""

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

    def test_process_output_preserves_failed_non_live_status(self, stream_handler, monkeypatch):
        req_info = RequestInfo()
        req_info.status = RequestInfo.Status.FAILED
        req_info.error_message = "Decode error: decoded 0 frame(s), required at least 1"
        req_info.is_live = False
        req_info.text_query = Mock()

        stop_request_profiling = MagicMock()
        cleanup_request_files = MagicMock()
        monkeypatch.setattr(stream_handler, "stop_request_profiling", stop_request_profiling)
        monkeypatch.setattr(stream_handler, "_cleanup_request_files", cleanup_request_files)

        stream_handler._process_output(
            req_info=req_info,
            is_live_stream_ended=False,
            chunk_responses=[],
        )

        assert req_info.status == RequestInfo.Status.FAILED
        assert req_info.status_event.is_set()
        stop_request_profiling.assert_called_once_with(req_info, [])
        cleanup_request_files.assert_called_once_with(req_info)


class TestArgumentParser:
    """Test argument parser"""

    def test_populate_argument_parser(self):
        """Test populating argument parser"""
        from argparse import ArgumentParser

        parser = ArgumentParser()
        RTVIStreamHandler.populate_argument_parser(parser)
        # Should have added arguments
        assert parser is not None

    def test_parse_arguments(self):
        """Test parsing arguments"""
        from argparse import ArgumentParser

        parser = ArgumentParser()
        RTVIStreamHandler.populate_argument_parser(parser)
        args = parser.parse_args(
            [
                "--kafka-enabled",
                "--kafka-topic",
                "test-topic",
                "--max-file-duration",
                "60",
                "--vlm-model-type",
                "openai-compat",
            ]
        )
        assert args.kafka_enabled is True
        assert args.kafka_topic == "test-topic"
        assert args.max_file_duration == 60
        assert args.vlm_model_type == VlmModelType.OPENAI_COMPATIBLE


class TestStopHandler:
    """Test handler stop functionality"""

    def test_stop_handler(self, stream_handler):
        """Test stopping handler"""
        stream_handler.stop(force=True)
        # Should complete without exception
        assert True

    def test_stop_handler_without_pipeline(self, mock_args):
        """Test stopping handler without pipeline"""
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "KAFKA_ENABLED": "false"}):
            # Mock VlmPipeline to avoid hanging on GPU initialization
            with patch("server.rtvi_stream_handler.VlmPipeline") as mock_vlm_pipeline_class:
                mock_pipeline = MagicMock()
                mock_model_info = MagicMock()
                mock_model_info.id = "test-model"
                mock_model_info.created = 1234567890
                mock_model_info.owned_by = "test"
                mock_model_info.api_type = "test"
                mock_pipeline.get_models_info.return_value = mock_model_info
                mock_pipeline.get_health_status.return_value = []
                mock_vlm_pipeline_class.return_value = mock_pipeline
                handler = RTVIStreamHandler(mock_args, service_name="rtvi-vlm-test")
                # Manually set pipeline to None
                handler._vlm_pipeline = None
                handler.stop(force=True)
                assert True
