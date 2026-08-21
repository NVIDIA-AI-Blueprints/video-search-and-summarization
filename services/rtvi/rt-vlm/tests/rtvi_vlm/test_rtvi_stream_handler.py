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
from time import monotonic, sleep
from unittest.mock import MagicMock, Mock, patch

import pytest
import torch

from api_models.captions import VlmQuery
from common.chunk_info import ChunkInfo
from common.service_exception import ServiceException
from models.base_vlm_model import VlmModelOutput
from server.rtvi_stream_handler import RequestInfo, RTVIStreamHandler, _get_bool_env
from tests.tests_common import TempEnv
from utils.asset_manager import Asset
from vlm_pipeline.vlm_pipeline import PipelineChunkResult, VlmModelType

# NOTE: `mock_args` and `stream_handler` fixtures are defined in
# tests/rtvi_vlm/conftest.py so they can be shared across this module and
# other rtvi_vlm test files.

API_PREFIX = "/v1"


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        ("true", False, True),
        ("off", True, False),
        ("", True, True),
        ("treu", True, True),
        ("treu", False, False),
    ],
)
def test_get_bool_env_rejects_ambiguous_values(monkeypatch, value, default, expected):
    monkeypatch.setenv("RTVI_TEST_BOOL", value)
    assert _get_bool_env("RTVI_TEST_BOOL", default) is expected


class TestStreamHandlerInitialization:
    """Test stream handler initialization"""

    def test_handler_initialization(self, mock_args, monkeypatch):
        """Test handler can be initialized"""
        monkeypatch.delenv("RTVI_VLM_ADMISSION_MODE", raising=False)
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "MESSAGE_BUS": ""}):
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
                handler = RTVIStreamHandler(mock_args, service_name="rtvi-vlm")
                assert handler._request_info_map is not None
                assert handler._metrics is not None
                assert handler._vlm_admission_mode == "off"
                handler.stop(force=True)

    def test_kafka_disabled_by_default(self, mock_args):
        """Test generated-message output is disabled by default."""
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "MESSAGE_BUS": ""}):
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
                assert handler._kafka_enabled is False
                assert handler._kafka_producer is None
                handler.stop(force=True)

    def test_kafka_message_bus_enabled_via_env(self, mock_args):
        """Test Kafka can be selected via MESSAGE_BUS."""
        with TempEnv(
            {
                "SKIP_PIPELINE_WARMUP": "1",
                "MESSAGE_BUS": "kafka",
                "MESSAGE_BUS_TOPIC": "mdx-init-kafka",
                "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
            }
        ):
            # Mock VlmPipeline to avoid hanging on GPU initialization
            with patch("server.rtvi_stream_handler.KafkaProducer") as mock_kafka_class:
                kafka_producer = MagicMock()
                kafka_producer.config = {"bootstrap_servers": ["localhost:9092"]}
                mock_kafka_class.return_value = kafka_producer
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
                    assert handler.get_message_bus_config()["messagingbus"] == "kafka"
                    assert handler.get_message_bus_config()["kafka_topic"] == "mdx-init-kafka"
                    assert handler._kafka_enabled is True
                    assert handler._kafka_producer is kafka_producer
                    handler.stop(force=True)

    def test_redis_message_bus_enabled_via_env(self, mock_args):
        """Test Redis Streams can be selected as the startup output bus."""
        with TempEnv(
            {
                "SKIP_PIPELINE_WARMUP": "1",
                "MESSAGE_BUS": "redis",
                "MESSAGE_BUS_TOPIC": "mdx-init-redis",
            }
        ):
            with patch("server.rtvi_stream_handler.redis.Redis") as mock_redis_class:
                redis_client = MagicMock()
                redis_client.ping.return_value = True
                mock_redis_class.return_value = redis_client
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
                    assert handler.get_message_bus_config()["messagingbus"] == "redis"
                    assert handler.get_message_bus_config()["redis_stream"] == "mdx-init-redis"
                    assert handler._redis_client is redis_client
                    handler.stop(force=True)

    def test_kafka_message_bus_topic_enabled_via_env(self, mock_args):
        """Test the generic startup topic env can select the Kafka topic."""
        with TempEnv(
            {
                "SKIP_PIPELINE_WARMUP": "1",
                "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
                "MESSAGE_BUS": "kafka",
                "MESSAGE_BUS_TOPIC": "mdx-init-kafka",
            }
        ):
            with patch("server.rtvi_stream_handler.KafkaProducer") as mock_kafka_class:
                kafka_producer = MagicMock()
                kafka_producer.config = {"bootstrap_servers": ["localhost:9092"]}
                mock_kafka_class.return_value = kafka_producer
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
                    assert handler.get_message_bus_config()["messagingbus"] == "kafka"
                    assert handler.get_message_bus_config()["kafka_topic"] == "mdx-init-kafka"
                    assert handler._kafka_producer is kafka_producer
                    handler.stop(force=True)

    def test_kafka_error_bus_enabled_via_env(self, mock_args):
        """Test Kafka can be selected via ERROR_BUS without generated output."""
        with TempEnv(
            {
                "SKIP_PIPELINE_WARMUP": "1",
                "MESSAGE_BUS": "",
                "ERROR_BUS": "kafka",
                "ERROR_MESSAGE_TOPIC": "mdx-init-errors",
                "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
            }
        ):
            with patch("server.rtvi_stream_handler.KafkaProducer") as mock_kafka_class:
                kafka_producer = MagicMock()
                kafka_producer.config = {"bootstrap_servers": ["localhost:9092"]}
                mock_kafka_class.return_value = kafka_producer
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
                    assert handler.get_message_bus_config()["messagingbus"] is None
                    assert handler.get_error_bus_config()["errorbus"] == "kafka"
                    assert handler.get_error_bus_config()["kafka_topic"] == "mdx-init-errors"
                    assert handler._kafka_producer is kafka_producer
                    handler.stop(force=True)

    def test_redis_error_bus_enabled_via_env(self, mock_args):
        """Test Redis can be selected via ERROR_BUS without generated output."""
        with TempEnv(
            {
                "SKIP_PIPELINE_WARMUP": "1",
                "MESSAGE_BUS": "",
                "ERROR_BUS": "redis",
                "ERROR_MESSAGE_TOPIC": "mdx-init-errors",
            }
        ):
            with patch("server.rtvi_stream_handler.redis.Redis") as mock_redis_class:
                redis_client = MagicMock()
                redis_client.ping.return_value = True
                mock_redis_class.return_value = redis_client
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
                    assert handler.get_message_bus_config()["messagingbus"] is None
                    assert handler.get_error_bus_config()["errorbus"] == "redis"
                    assert handler.get_error_bus_config()["redis_channel"] == "mdx-init-errors"
                    assert handler._redis_client is redis_client
                    assert handler._use_redis_error_bus is True
                    handler.stop(force=True)

    def test_error_bus_empty_disables_inherited_kafka_errors(self, mock_args):
        """ERROR_BUS can explicitly disable error output while MESSAGE_BUS uses Kafka."""
        with TempEnv(
            {
                "SKIP_PIPELINE_WARMUP": "1",
                "MESSAGE_BUS": "kafka",
                "ERROR_BUS": "",
                "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
            }
        ):
            with patch("server.rtvi_stream_handler.KafkaProducer") as mock_kafka_class:
                kafka_producer = MagicMock()
                kafka_producer.config = {"bootstrap_servers": ["localhost:9092"]}
                mock_kafka_class.return_value = kafka_producer
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
                    assert handler.get_message_bus_config()["messagingbus"] == "kafka"
                    assert handler.get_error_bus_config()["errorbus"] is None
                    handler._send_error_message_to_kafka("test error", "test-id")
                    kafka_producer.send.assert_not_called()
                    handler.stop(force=True)

    def test_kafka_message_bus_without_bootstrap_is_disabled(self, mock_args):
        """Startup Kafka selection must not mark Kafka active without bootstrap servers."""
        with TempEnv(
            {
                "SKIP_PIPELINE_WARMUP": "1",
                "KAFKA_BOOTSTRAP_SERVERS": "",
                "MESSAGE_BUS": "kafka",
                "MESSAGE_BUS_TOPIC": "mdx-init-kafka",
            }
        ):
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
                assert handler.get_message_bus_config()["messagingbus"] is None
                assert handler._kafka_enabled is False
                assert handler._kafka_producer is None
                handler.stop(force=True)


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

    def test_live_request_setup_failure_ends_otel_spans(self, stream_handler, monkeypatch):
        """Live setup failures must unwind request state and close started spans."""
        asset = Asset(
            asset_id=str(uuid.uuid4()),
            path="rtsp://example.com/live",
            purpose="",
            media_type="",
            asset_dir="",
        )
        query = VlmQuery(
            id=uuid.UUID(asset.asset_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        e2e_span = MagicMock()
        pipeline_span = MagicMock()
        tracer = MagicMock()
        tracer.start_span.side_effect = [e2e_span, pipeline_span]
        stream_handler._vlm_pipeline.add_live_stream.side_effect = ServiceException(
            "decode settings mismatch",
            "BadParameters",
            400,
        )
        monkeypatch.setattr("server.rtvi_stream_handler.get_tracer", lambda: tracer)

        with pytest.raises(ServiceException):
            stream_handler.generate_vlm_captions([asset], query, is_rtsp=True)

        assert stream_handler._request_info_map == {}
        assert asset.use_count == 0
        pipeline_span.end.assert_called_once()
        e2e_span.end.assert_called_once()
        e2e_span.set_attribute.assert_any_call("error_message", "decode settings mismatch")

    def test_new_live_stream_rejected_when_gpu_memory_headroom_is_low(self, stream_handler):
        """Reject only when current free memory has crossed the hard watermark."""
        asset = Asset(
            asset_id=str(uuid.uuid4()),
            path="rtsp://example.com/live-low-memory",
            purpose="",
            media_type="",
            asset_dir="",
        )
        query = VlmQuery(
            id=uuid.UUID(asset.asset_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        gib = 1024 * 1024 * 1024
        stream_handler._live_stream_gpu_memory_guard_enabled = True
        stream_handler._live_stream_gpu_memory_headroom_bytes = 2 * gib
        stream_handler._get_gpu_memory_info_bytes = MagicMock(return_value=(1 * gib, 80 * gib))

        with pytest.raises(ServiceException) as exc_info:
            stream_handler.generate_vlm_captions([asset], query, is_rtsp=True)

        assert exc_info.value.status_code == 503
        assert exc_info.value.code == "ServerBusy"
        assert "Insufficient GPU memory" in exc_info.value.message
        assert stream_handler._request_info_map == {}
        assert asset.use_count == 0
        stream_handler._vlm_pipeline.add_live_stream.assert_not_called()

    def test_retained_gpu_memory_does_not_inflate_live_stream_admission(
        self,
        stream_handler,
    ):
        """Allocator history must not be interpreted as the cost of the next stream."""
        asset = Asset(
            asset_id=str(uuid.uuid4()),
            path="rtsp://example.com/live-retained-memory",
            purpose="",
            media_type="",
            asset_dir="",
        )
        query = VlmQuery(
            id=uuid.UUID(asset.asset_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        gib = 1024 * 1024 * 1024
        existing_asset = Asset(
            asset_id=str(uuid.uuid4()),
            path="rtsp://example.com/already-active",
            purpose="",
            media_type="",
            asset_dir="",
        )
        existing = RequestInfo()
        existing.assets = [existing_asset]
        existing.is_live = True
        existing.status = RequestInfo.Status.PROCESSING
        stream_handler._request_info_map[existing.request_id] = existing
        stream_handler._live_stream_gpu_memory_guard_enabled = True
        stream_handler._live_stream_gpu_memory_headroom_bytes = 1 * gib
        stream_handler._gpu_memory_guard_baseline_used_bytes = 60 * gib
        stream_handler._get_gpu_memory_info_bytes = MagicMock(return_value=(9 * gib, 80 * gib))

        request_id = stream_handler.generate_vlm_captions([asset], query, is_rtsp=True)

        assert request_id in stream_handler._request_info_map
        stream_handler._vlm_pipeline.add_live_stream.assert_called_once()

    def test_cuda_oom_during_live_stream_setup_returns_server_busy(self, stream_handler):
        """A real setup OOM should clean request state and become a retriable 503."""
        asset = Asset(
            asset_id=str(uuid.uuid4()),
            path="rtsp://example.com/live-setup-oom",
            purpose="",
            media_type="",
            asset_dir="",
        )
        query = VlmQuery(
            id=uuid.UUID(asset.asset_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        gib = 1024 * 1024 * 1024
        stream_handler._live_stream_gpu_memory_guard_enabled = True
        stream_handler._live_stream_gpu_memory_headroom_bytes = 1 * gib
        stream_handler._get_gpu_memory_info_bytes = MagicMock(return_value=(2 * gib, 80 * gib))
        stream_handler._vlm_pipeline.add_live_stream.side_effect = torch.OutOfMemoryError(
            "CUDA out of memory while starting decoder"
        )

        with pytest.raises(ServiceException) as exc_info:
            stream_handler.generate_vlm_captions([asset], query, is_rtsp=True)

        assert exc_info.value.status_code == 503
        assert exc_info.value.code == "ServerBusy"
        assert "GPU memory was exhausted while starting live stream" in exc_info.value.message
        assert stream_handler._request_info_map == {}
        assert asset.use_count == 0

    def test_additional_live_caption_prompt_reuses_stream_without_memory_gate(
        self,
        stream_handler,
    ):
        """A second prompt on the same RTSP asset should not consume a new-stream admission slot."""
        asset = Asset(
            asset_id=str(uuid.uuid4()),
            path="rtsp://example.com/live-shared",
            purpose="",
            media_type="",
            asset_dir="",
        )
        existing = RequestInfo()
        existing.assets = [asset]
        existing.is_live = True
        existing.status = RequestInfo.Status.PROCESSING
        asset.lock()
        stream_handler._request_info_map[existing.request_id] = existing

        query = VlmQuery(
            id=uuid.UUID(asset.asset_id),
            model="test-model",
            prompt="Describe the stream.",
            stream=True,
            chunk_duration=10,
        )
        stream_handler._raise_if_insufficient_gpu_memory_for_new_live_stream = MagicMock()

        request_id = stream_handler.generate_vlm_captions([asset], query, is_rtsp=True)

        assert request_id in stream_handler._request_info_map
        stream_handler._raise_if_insufficient_gpu_memory_for_new_live_stream.assert_not_called()
        stream_handler._vlm_pipeline.add_live_stream.assert_called_once()
        assert asset.use_count == 2

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

    def test_average_chunk_latency_tracking(self, stream_handler):
        """Test average chunk latency gauges are maintained with latest values."""
        metrics = stream_handler._metrics

        metrics.record_chunk_latency(2.0)
        metrics.record_chunk_latency(4.0)
        assert metrics._chunk_latency_latest_value == 4.0
        assert metrics._chunk_latency_count_value == 2
        assert metrics._chunk_latency_avg_value == pytest.approx(3.0)

        metrics.record_live_stream_chunk_latency(1.0)
        metrics.record_live_stream_chunk_latency(3.0)
        assert metrics._live_stream_chunk_latency_latest_value == 3.0
        assert metrics._live_stream_chunk_latency_count_value == 2
        assert metrics._live_stream_chunk_latency_avg_value == pytest.approx(2.0)

    def test_live_chunk_response_records_live_chunk_latency(self, stream_handler):
        """Live VLM chunk completion should populate live-stream chunk metrics."""
        asset = Asset(
            asset_id="live-metrics-stream",
            path="rtsp://example.com/live",
            purpose="",
            media_type="",
            asset_dir="",
            camera_id="cam-live-metrics",
        )
        req_info = RequestInfo(
            request_id="request-live-metrics",
            assets=[asset],
            is_live=True,
        )
        req_info.status = RequestInfo.Status.PROCESSING

        chunk = ChunkInfo(
            file=asset.path,
            chunkIdx=1,
            start_pts=0,
            end_pts=1_000_000_000,
        )
        chunk.streamId = asset.asset_id
        chunk.start_ntp = "2026-05-05T00:00:00.000Z"
        chunk.end_ntp = "2026-05-05T00:00:01.000Z"
        chunk_result = PipelineChunkResult(
            chunk=chunk,
            vlm_model_output=VlmModelOutput(
                output="The scene is active.",
                input_tokens=10,
                output_tokens=4,
            ),
            decode_start_time=1.0,
            decode_end_time=1.5,
            vlm_start_time=1.5,
            vlm_end_time=4.0,
            frame_times=[0.0, 0.5],
        )

        stream_handler._on_vlm_chunk_response(chunk_result, req_info)

        assert stream_handler._metrics._chunk_latency_latest_value == pytest.approx(3.0)
        assert stream_handler._metrics._live_stream_chunk_latency_latest_value == pytest.approx(3.0)
        assert stream_handler._metrics._live_stream_chunk_latency_count_value == 1
        assert stream_handler._metrics._live_stream_chunk_latency_avg_value == pytest.approx(3.0)

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
            self.release_xadd = Event()
            self.xadd_started = Event()
            self.xadd_calls = []

        def publish(self, *args, **kwargs):
            self.publish_calls.append((args, kwargs))
            self.publish_started.set()
            self.release_publish.wait(timeout=2)
            return 0

        def xadd(self, *args, **kwargs):
            self.xadd_calls.append((args, kwargs))
            self.xadd_started.set()
            self.release_xadd.wait(timeout=2)
            return b"1-0"

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

    def test_chat_completion_messages_disabled_by_default(self, stream_handler):
        stream_handler._kafka_enabled = True
        req_info = RequestInfo(is_chat_completion=True)

        with patch.dict("os.environ", {}, clear=True):
            assert stream_handler._messages_enabled_for_request(req_info) is False

    def test_chat_completion_messages_can_be_disabled(self, stream_handler):
        stream_handler._kafka_enabled = True
        req_info = RequestInfo(is_chat_completion=True)

        with TempEnv({"ENABLE_MESSAGES_FOR_CHAT_COMPLETIONS": "false"}):
            assert stream_handler._messages_enabled_for_request(req_info) is False

    def test_non_chat_messages_ignore_chat_completion_switch(self, stream_handler):
        stream_handler._kafka_enabled = True
        req_info = RequestInfo(is_chat_completion=False)

        with TempEnv({"ENABLE_MESSAGES_FOR_CHAT_COMPLETIONS": "false"}):
            assert stream_handler._messages_enabled_for_request(req_info) is True

    def test_chat_completion_switch_disables_redis_output(self, stream_handler):
        stream_handler._message_bus = "redis"
        req_info = RequestInfo(is_chat_completion=True)

        with TempEnv({"ENABLE_MESSAGES_FOR_CHAT_COMPLETIONS": "false"}):
            assert stream_handler._normal_output_bus_enabled_for_request(req_info) is False

    def test_chat_file_without_creation_time_uses_queue_wall_clock_for_kafka(self, stream_handler):
        wall_clock_base = 1_767_225_600.0
        asset = Asset(
            asset_id="chat-file",
            path="/tmp/chat-file.mp4",
            purpose="vision",
            media_type="video",
            asset_dir="",
            creation_time=None,
        )
        req_info = RequestInfo(
            request_id="chat-request",
            assets=[asset],
            is_chat_completion=True,
            queue_time=wall_clock_base,
        )
        chunk = ChunkInfo(
            file=asset.path,
            chunkIdx=0,
            start_pts=60_000_000_000,
            end_pts=120_000_000_000,
            start_ntp="1970-01-01T00:01:00.000Z",
            end_ntp="1970-01-01T00:02:00.000Z",
        )
        chunk_result = PipelineChunkResult(
            chunk=chunk,
            vlm_model_output=VlmModelOutput(output="Description"),
        )

        vision_llm, _ = stream_handler._chunk_result_to_vision_llm(chunk_result, req_info)

        assert vision_llm.timestamp.seconds == int(wall_clock_base + 60)
        assert vision_llm.end.seconds == int(wall_clock_base + 120)
        assert vision_llm.llm.queries[0].params["startNtp"].startswith("2026-")
        assert vision_llm.llm.queries[0].params["endNtp"].startswith("2026-")

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

    def test_configure_message_bus_updates_redis_stream(self, stream_handler):
        """Config events can switch generated protobuf output to a Redis Stream."""
        redis_client = self._BlockingRedisClient()
        stream_handler._redis_client = redis_client

        result = stream_handler.configure_message_bus("redis", "mdx-bev")

        assert result == {"messagingbus": "redis", "topic": "mdx-bev"}
        assert stream_handler.get_message_bus_config()["messagingbus"] == "redis"
        assert stream_handler.get_message_bus_config()["redis_stream"] == "mdx-bev"

    def test_configure_message_bus_updates_kafka_topic(self, stream_handler):
        """Config events can switch generated protobuf output back to Kafka."""
        producer = self._BlockingKafkaProducer()
        stream_handler._kafka_producer = producer

        result = stream_handler.configure_message_bus("kafka", "mdx-configured")

        assert result == {"messagingbus": "kafka", "topic": "mdx-configured"}
        assert stream_handler.get_message_bus_config()["messagingbus"] == "kafka"
        assert stream_handler.get_message_bus_config()["kafka_topic"] == "mdx-configured"
        assert stream_handler._kafka_enabled is True

    def test_configure_error_bus_updates_redis_channel(self, stream_handler):
        """Config events can switch error output to a Redis channel."""
        redis_client = self._BlockingRedisClient()
        stream_handler._redis_client = redis_client

        result = stream_handler.configure_error_bus("redis", "mdx-errors")

        assert result == {"errorbus": "redis", "topic": "mdx-errors"}
        assert stream_handler.get_error_bus_config()["errorbus"] == "redis"
        assert stream_handler.get_error_bus_config()["redis_channel"] == "mdx-errors"
        assert stream_handler._use_redis_error_bus is True

    def test_configure_error_bus_updates_kafka_topic(self, stream_handler):
        """Config events can switch error output to Kafka."""
        producer = self._BlockingKafkaProducer()
        stream_handler._kafka_producer = producer

        result = stream_handler.configure_error_bus("kafka", "mdx-error-configured")

        assert result == {"errorbus": "kafka", "topic": "mdx-error-configured"}
        assert stream_handler.get_error_bus_config()["errorbus"] == "kafka"
        assert stream_handler.get_error_bus_config()["kafka_topic"] == "mdx-error-configured"
        assert stream_handler._use_redis_error_bus is False
        assert stream_handler._kafka_enabled is True

    def test_configure_message_bus_warns_when_media_generation_active(self, stream_handler):
        """Config changes warn when active file or RTSP requests may straddle routes."""
        redis_client = self._BlockingRedisClient()
        stream_handler._message_bus = "kafka"
        stream_handler._kafka_topic = "mdx-existing"
        stream_handler._redis_client = redis_client

        req_info = RequestInfo()
        req_info.status = RequestInfo.Status.PROCESSING
        req_info.assets = [Mock()]
        stream_handler._request_info_map[req_info.request_id] = req_info

        result = stream_handler.configure_message_bus("redis", "mdx-new")

        assert result["messagingbus"] == "redis"
        assert result["topic"] == "mdx-new"
        assert "warnings" in result
        assert "mdx-existing" in result["warnings"][0]
        assert "mdx-new" in result["warnings"][0]
        assert "subsequent chunk messages will use the updated route" in result["warnings"][0]

    def test_failed_redis_config_preserves_current_message_bus(self, stream_handler):
        """Failed Redis config must not partially switch the active output bus."""
        stream_handler._message_bus = "kafka"
        stream_handler._kafka_topic = "mdx-existing"

        with patch.object(stream_handler, "_ensure_redis_client", return_value=False):
            with pytest.raises(ServiceException):
                stream_handler.configure_message_bus("redis", "mdx-redis-fail")

        assert stream_handler.get_message_bus_config()["messagingbus"] == "kafka"
        assert stream_handler.get_message_bus_config()["kafka_topic"] == "mdx-existing"
        assert stream_handler.get_message_bus_config()["redis_stream"] == "mdx-vlm-captions"

    def test_failed_kafka_config_preserves_current_message_bus(self, stream_handler):
        """Failed Kafka config must not partially switch away from Redis."""
        stream_handler._message_bus = "redis"
        stream_handler._redis_stream = "mdx-existing-redis"
        stream_handler._kafka_topic = "mdx-existing-kafka"

        with patch.object(stream_handler, "_ensure_kafka_producer", return_value=False):
            with pytest.raises(ServiceException):
                stream_handler.configure_message_bus("kafka", "mdx-kafka-fail")

        assert stream_handler.get_message_bus_config()["messagingbus"] == "redis"
        assert stream_handler.get_message_bus_config()["redis_stream"] == "mdx-existing-redis"
        assert stream_handler.get_message_bus_config()["kafka_topic"] == "mdx-existing-kafka"

    def test_protobuf_redis_stream_send_does_not_block_caller(self, stream_handler):
        """Redis Stream xadd is offloaded like Kafka sends."""
        redis_client = self._BlockingRedisClient()
        stream_handler._message_bus = "redis"
        stream_handler._redis_stream = "mdx-bev"
        stream_handler._redis_client = redis_client
        stream_handler._redis_payload_key = "metadata"

        req_info = RequestInfo()
        req_info.request_id = "request-redis"
        chunk_result = Mock()
        chunk_result.chunk = Mock()
        chunk_result.chunk.chunkIdx = 9

        start_time = monotonic()
        stream_handler._send_protobuf_to_message_bus(b"payload", chunk_result, req_info)
        elapsed = monotonic() - start_time

        assert elapsed < 0.2
        assert redis_client.xadd_started.wait(timeout=1)
        assert len(redis_client.xadd_calls) == 1

        args, kwargs = redis_client.xadd_calls[0]
        assert args[0] == "mdx-bev"
        assert args[1] == {
            "metadata": b"payload",
            "message_type": b"vision_llm",
            "key": b"request-redis:9",
        }
        assert kwargs == {"maxlen": 10000, "approximate": True}

        redis_client.release_xadd.set()
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

    def test_failed_file_request_closes_evs_sessions(self, stream_handler):
        """A failed file chunk must release the request's EVS sessions.

        The success path closes them only when every chunk is accounted for
        (len(processed_chunk_list) == chunk_count). A chunk error marks the
        request FAILED and calls abort_chunks(), so the remaining chunks never
        arrive and that equality is never reached -- leaking the session for the
        life of the process until session creation fails with "max sessions
        reached".
        """
        stream_handler._vlm_pipeline.close_evs_sessions = Mock()

        asset = Asset(
            asset_id="file-asset-failed",
            path="/tmp/file-asset-failed.mp4",
            purpose="",
            media_type="",
            asset_dir="",
        )
        req_info = RequestInfo(
            request_id="request-file-failed",
            assets=[asset],
            is_live=False,
        )
        req_info.status = RequestInfo.Status.PROCESSING
        req_info.chunk_count = 4

        chunk = ChunkInfo(
            file=asset.path,
            chunkIdx=0,
            start_pts=0,
            end_pts=1_000_000_000,
        )
        chunk.streamId = req_info.stream_id

        chunk_result = PipelineChunkResult(
            chunk=chunk,
            error="EVS clip encode failed for chunk 0",
            error_status_code=500,
        )

        stream_handler._on_vlm_chunk_response(chunk_result, req_info)

        deadline = monotonic() + 5
        while (
            not stream_handler._vlm_pipeline.close_evs_sessions.call_args_list
            and monotonic() < deadline
        ):
            sleep(0.01)

        assert req_info.status == RequestInfo.Status.FAILED
        stream_handler._vlm_pipeline.close_evs_sessions.assert_called_once_with(req_info.stream_id)

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


def _admission_request(request_id: str, chunk_count: int = 2) -> RequestInfo:
    asset = MagicMock(asset_id=f"asset-{request_id}")
    query = VlmQuery(
        id=uuid.uuid4(),
        model="test-model",
        prompt="Describe.",
        chunk_duration=10,
        num_frames_per_second_or_fixed_frames_chunk=40,
        vlm_input_width=640,
        vlm_input_height=640,
    )
    req_info = RequestInfo(
        request_id=request_id,
        query=query,
        assets=[asset],
        status=RequestInfo.Status.PROCESSING,
    )
    for chunk_idx in range(chunk_count):
        chunk = ChunkInfo(
            chunkIdx=chunk_idx,
            start_pts=chunk_idx * 10_000_000_000,
            end_pts=(chunk_idx + 1) * 10_000_000_000,
        )
        req_info.pending_file_chunks.append((chunk, None, False, 1.0))
    return req_info


def _ready_admission_request(stream_handler, req_info: RequestInfo) -> None:
    stream_handler._request_info_map[req_info.request_id] = req_info
    stream_handler._vlm_admission_ready_requests.append(req_info.request_id)
    stream_handler._vlm_admission_ready_request_ids.add(req_info.request_id)


def test_file_admission_estimates_cost_from_frames_and_resolution(stream_handler):
    req_info = _admission_request("cost", chunk_count=0)
    chunk = ChunkInfo(chunkIdx=0, start_pts=0, end_pts=10_000_000_000)

    assert stream_handler._estimate_file_chunk_cost(req_info, chunk) == pytest.approx(1.0)
    req_info.query.num_frames_per_second_or_fixed_frames_chunk = 10
    assert stream_handler._estimate_file_chunk_cost(req_info, chunk) == pytest.approx(0.25)
    req_info.query.num_frames_per_second_or_fixed_frames_chunk = 40
    req_info.query.vlm_input_width = 1280
    req_info.query.vlm_input_height = 1280
    assert stream_handler._estimate_file_chunk_cost(req_info, chunk) == pytest.approx(4.0)

    req_info.query.num_frames_per_second_or_fixed_frames_chunk = -1
    req_info.query.vlm_input_width = 640
    req_info.query.vlm_input_height = 640
    req_info.video_fps = 4
    assert stream_handler._estimate_file_chunk_cost(req_info, chunk) == pytest.approx(1.0)


def test_file_admission_dispatches_requests_fairly(stream_handler):
    stream_handler._vlm_admission_mode = "bounded"
    stream_handler._vlm_admission_target_cost = 2.0
    stream_handler._vlm_admission_max_cost = 2.0
    request_a = _admission_request("a")
    request_b = _admission_request("b")
    _ready_admission_request(stream_handler, request_a)
    _ready_admission_request(stream_handler, request_b)

    stream_handler._dispatch_pending_file_chunks()

    calls = stream_handler._vlm_pipeline.enqueue_chunk.call_args_list
    assert [call.args[0].chunkIdx for call in calls] == [0, 0]
    assert calls[0].args[3] == "a"
    assert calls[1].args[3] == "b"
    assert stream_handler._vlm_admission_active_cost == pytest.approx(2.0)


def test_file_admission_completion_releases_credit_and_dispatches_next(stream_handler):
    stream_handler._vlm_admission_mode = "bounded"
    stream_handler._vlm_admission_target_cost = 1.0
    request = _admission_request("release")
    _ready_admission_request(stream_handler, request)
    stream_handler._dispatch_pending_file_chunks()
    first_chunk = stream_handler._vlm_pipeline.enqueue_chunk.call_args_list[0].args[0]

    result = PipelineChunkResult(chunk=first_chunk, queue_time=0.0, processing_latency=1.0)
    stream_handler._complete_admitted_chunk(result, request)

    calls = stream_handler._vlm_pipeline.enqueue_chunk.call_args_list
    assert [call.args[0].chunkIdx for call in calls] == [0, 1]
    assert stream_handler._vlm_admission_active_cost == pytest.approx(1.0)


def test_file_admission_enqueue_failure_releases_credit(stream_handler):
    stream_handler._vlm_admission_mode = "bounded"
    stream_handler._vlm_admission_target_cost = 1.0
    request = _admission_request("enqueue-error", chunk_count=1)
    _ready_admission_request(stream_handler, request)
    stream_handler._vlm_pipeline.enqueue_chunk.side_effect = RuntimeError("queue closed")

    stream_handler._dispatch_pending_file_chunks()

    assert stream_handler._vlm_admission_active_cost == pytest.approx(0.0)
    assert request.status == RequestInfo.Status.FAILED


def test_file_admission_error_releases_aborted_sibling_credit(stream_handler):
    stream_handler._vlm_admission_mode = "bounded"
    stream_handler._vlm_admission_target_cost = 2.0
    request = _admission_request("chunk-error")
    _ready_admission_request(stream_handler, request)
    stream_handler._dispatch_pending_file_chunks()
    first_chunk = stream_handler._vlm_pipeline.enqueue_chunk.call_args_list[0].args[0]

    result = PipelineChunkResult(chunk=first_chunk, error="decode failed")
    stream_handler._on_vlm_chunk_response(result, request)

    assert stream_handler._vlm_admission_active_cost == pytest.approx(0.0)
    assert request.active_file_chunk_costs == {}


def test_adaptive_admission_backs_off_and_recovers(stream_handler):
    stream_handler._vlm_admission_mode = "adaptive"
    stream_handler._vlm_admission_target_cost = 4.0
    stream_handler._vlm_admission_max_cost = 4.0
    req_info = _admission_request("adaptive", chunk_count=0)
    congested = PipelineChunkResult(queue_time=1.0, processing_latency=1.0)

    with stream_handler._lock:
        stream_handler._observe_admission_result_locked(congested, req_info)
    assert stream_handler._vlm_admission_target_cost == pytest.approx(2.0)

    clean = PipelineChunkResult(queue_time=0.0, processing_latency=1.0)
    with stream_handler._lock:
        stream_handler._observe_admission_result_locked(clean, req_info)
        stream_handler._observe_admission_result_locked(clean, req_info)
    assert stream_handler._vlm_admission_target_cost == pytest.approx(3.0)


def test_live_deadline_pressure_pauses_file_dispatch(stream_handler):
    stream_handler._vlm_admission_mode = "bounded"
    stream_handler._vlm_admission_target_cost = 2.0
    live_request = _admission_request("live", chunk_count=0)
    live_request.is_live = True
    file_request = _admission_request("file", chunk_count=1)
    stream_handler._request_info_map[live_request.request_id] = live_request
    _ready_admission_request(stream_handler, file_request)
    stream_handler._metrics._live_stream_chunk_latency_latest_value = 10.0

    stream_handler._dispatch_pending_file_chunks()
    stream_handler._vlm_pipeline.enqueue_chunk.assert_not_called()

    stream_handler._metrics._live_stream_chunk_latency_latest_value = 1.0
    stream_handler._dispatch_pending_file_chunks()
    stream_handler._vlm_pipeline.enqueue_chunk.assert_called_once()


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
                "--message-bus",
                "kafka",
                "--message-bus-topic",
                "test-topic",
                "--error-bus",
                "redis",
                "--max-file-duration",
                "60",
                "--vlm-model-type",
                "openai-compat",
            ]
        )
        assert args.message_bus == "kafka"
        assert args.message_bus_topic == "test-topic"
        assert args.error_bus == "redis"
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
        with TempEnv({"SKIP_PIPELINE_WARMUP": "1", "MESSAGE_BUS": ""}):
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
