# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Unit tests for src/otel_helper.py

Tests OpenTelemetry initialization, span collection, trace dumping, and decorators.
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

import otel_helper


@pytest.fixture(autouse=True)
def reset_otel_state():
    """Reset global OTEL state before each test."""
    otel_helper._otel_enabled = False
    otel_helper._tracer = None
    otel_helper._collected_spans.clear()
    yield
    otel_helper._otel_enabled = False
    otel_helper._tracer = None
    otel_helper._collected_spans.clear()


# =============================================================================
# init_otel Tests
# =============================================================================


@pytest.mark.unit
class TestInitOtel:
    @patch.dict(os.environ, {"VIA_ENABLE_OTEL": "false"}, clear=False)
    def test_disabled_via_env(self):
        result = otel_helper.init_otel()
        assert result is False
        assert otel_helper._otel_enabled is False

    @patch.dict(os.environ, {"VIA_ENABLE_OTEL": ""}, clear=False)
    def test_disabled_when_empty(self):
        result = otel_helper.init_otel()
        assert result is False

    @patch.dict(
        os.environ,
        {"VIA_ENABLE_OTEL": "true", "VIA_OTEL_EXPORTER": "console"},
        clear=False,
    )
    def test_console_exporter(self):
        mock_trace = MagicMock()
        mock_provider = MagicMock()
        mock_tracer = MagicMock()
        mock_trace.get_tracer.return_value = mock_tracer

        mock_resource = MagicMock()
        mock_resource_cls = MagicMock()
        mock_resource_cls.create.return_value = mock_resource

        mock_provider_cls = MagicMock(return_value=mock_provider)
        mock_batch = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.trace": mock_trace,
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
                "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
                "opentelemetry.sdk.trace.export": MagicMock(
                    BatchSpanProcessor=mock_batch,
                    ConsoleSpanExporter=MagicMock(),
                    SpanExporter=MagicMock,
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            result = otel_helper.init_otel(exporter_type="console")

        assert result is True
        assert otel_helper._otel_enabled is True

    @patch.dict(
        os.environ,
        {"VIA_ENABLE_OTEL": "true"},
        clear=False,
    )
    def test_otlp_exporter(self):
        mock_trace = MagicMock()
        mock_provider = MagicMock()
        mock_tracer = MagicMock()
        mock_trace.get_tracer.return_value = mock_tracer

        mock_resource_cls = MagicMock()
        mock_resource_cls.create.return_value = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider)
        mock_batch = MagicMock()
        mock_otlp_exporter = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.trace": mock_trace,
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
                "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
                "opentelemetry.sdk.trace.export": MagicMock(
                    BatchSpanProcessor=mock_batch,
                    SpanExporter=MagicMock,
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
                "opentelemetry.exporter": MagicMock(),
                "opentelemetry.exporter.otlp": MagicMock(),
                "opentelemetry.exporter.otlp.proto": MagicMock(),
                "opentelemetry.exporter.otlp.proto.http": MagicMock(),
                "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(
                    OTLPSpanExporter=mock_otlp_exporter
                ),
            },
        ):
            result = otel_helper.init_otel(exporter_type="otlp", endpoint="http://localhost:4318")

        assert result is True

    @patch.dict(os.environ, {"VIA_ENABLE_OTEL": "true"}, clear=False)
    def test_invalid_exporter_type(self):
        mock_trace = MagicMock()
        mock_provider = MagicMock()
        mock_resource_cls = MagicMock()
        mock_resource_cls.create.return_value = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider)
        mock_batch = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.trace": mock_trace,
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
                "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
                "opentelemetry.sdk.trace.export": MagicMock(
                    BatchSpanProcessor=mock_batch,
                    SpanExporter=MagicMock,
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            result = otel_helper.init_otel(exporter_type="invalid_type")

        assert result is False

    @patch.dict(os.environ, {"VIA_ENABLE_OTEL": "true"}, clear=False)
    def test_import_error(self):
        with patch.dict("sys.modules", {"opentelemetry": None}):
            with patch("builtins.__import__", side_effect=ImportError("no otel")):
                result = otel_helper.init_otel()
        assert result is False

    @patch.dict(os.environ, {"VIA_ENABLE_OTEL": "true"}, clear=False)
    def test_general_exception(self):
        with patch.dict("sys.modules", {"opentelemetry": None}):
            with patch("builtins.__import__", side_effect=RuntimeError("unexpected")):
                result = otel_helper.init_otel()
        assert result is False

    @patch.dict(
        os.environ,
        {
            "VIA_ENABLE_OTEL": "true",
            "VIA_OTEL_EXPORTER": "otlp",
            "VIA_OTEL_ENDPOINT": "http://custom:4318",
        },
        clear=False,
    )
    def test_env_defaults_for_exporter_and_endpoint(self):
        mock_trace = MagicMock()
        mock_provider = MagicMock()
        mock_trace.get_tracer.return_value = MagicMock()
        mock_resource_cls = MagicMock()
        mock_resource_cls.create.return_value = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider)

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.trace": mock_trace,
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
                "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
                "opentelemetry.sdk.trace.export": MagicMock(
                    BatchSpanProcessor=MagicMock(),
                    SpanExporter=MagicMock,
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
                "opentelemetry.exporter": MagicMock(),
                "opentelemetry.exporter.otlp": MagicMock(),
                "opentelemetry.exporter.otlp.proto": MagicMock(),
                "opentelemetry.exporter.otlp.proto.http": MagicMock(),
                "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(),
            },
        ):
            result = otel_helper.init_otel()
        assert result is True


# =============================================================================
# SpanCollector Tests
# =============================================================================


@pytest.mark.unit
class TestSpanCollector:
    def _make_mock_span(self, name="test_span", has_parent=False, has_end=True, has_attrs=True):
        span = MagicMock()
        span.context.trace_id = 0x1234567890ABCDEF
        span.context.span_id = 0xFEDCBA0987654321
        span.name = name
        span.start_time = 1000000000000
        span.end_time = 2000000000000 if has_end else None
        span.status.status_code.name = "OK"
        span.status.description = None
        if has_attrs:
            span.attributes = {"key": "value"}
        else:
            span.attributes = None
        if has_parent:
            span.parent = MagicMock()
            span.parent.span_id = 0xAAAABBBBCCCCDDDD
        else:
            span.parent = None
        return span

    def test_export_success(self):
        mock_export_result = MagicMock()
        mock_export_result.SUCCESS = "SUCCESS"
        mock_export_result.FAILURE = "FAILURE"

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.trace": MagicMock(),
                "opentelemetry.sdk.trace.export": MagicMock(
                    SpanExporter=type("SpanExporter", (), {}),
                    SpanExportResult=mock_export_result,
                ),
            },
        ):
            collector = otel_helper._create_span_collector()
            span = self._make_mock_span()
            result = collector.export([span])
            assert result == "SUCCESS"
            assert len(otel_helper._collected_spans) == 1

    def test_export_with_parent(self):
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.trace": MagicMock(),
                "opentelemetry.sdk.trace.export": MagicMock(
                    SpanExporter=type("SpanExporter", (), {}),
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            collector = otel_helper._create_span_collector()
            span = self._make_mock_span(has_parent=True)
            collector.export([span])
            assert otel_helper._collected_spans[0]["parent_span_id"] is not None

    def test_export_no_end_time(self):
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.trace": MagicMock(),
                "opentelemetry.sdk.trace.export": MagicMock(
                    SpanExporter=type("SpanExporter", (), {}),
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            collector = otel_helper._create_span_collector()
            span = self._make_mock_span(has_end=False)
            collector.export([span])
            assert otel_helper._collected_spans[0]["duration_ns"] is None
            assert otel_helper._collected_spans[0]["duration_ms"] is None

    def test_export_no_attributes(self):
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.trace": MagicMock(),
                "opentelemetry.sdk.trace.export": MagicMock(
                    SpanExporter=type("SpanExporter", (), {}),
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            collector = otel_helper._create_span_collector()
            span = self._make_mock_span(has_attrs=False)
            collector.export([span])
            assert otel_helper._collected_spans[0]["attributes"] == {}

    def test_export_failure(self):
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.trace": MagicMock(),
                "opentelemetry.sdk.trace.export": MagicMock(
                    SpanExporter=type("SpanExporter", (), {}),
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            collector = otel_helper._create_span_collector()
            bad_span = MagicMock()
            bad_span.context = MagicMock(side_effect=Exception("fail"))
            type(bad_span.context).trace_id = property(
                lambda s: (_ for _ in ()).throw(Exception("bad"))
            )
            result = collector.export([bad_span])
            assert result == "FAILURE"

    def test_shutdown(self):
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.trace": MagicMock(),
                "opentelemetry.sdk.trace.export": MagicMock(
                    SpanExporter=type("SpanExporter", (), {}),
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            collector = otel_helper._create_span_collector()
            collector.shutdown()


# =============================================================================
# dump_traces_to_file Tests
# =============================================================================


@pytest.mark.unit
class TestDumpTracesToFile:
    def test_disabled_otel(self):
        result = otel_helper.dump_traces_to_file("req-123")
        assert result == {"json_file": None, "text_file": None}

    def test_no_spans(self):
        otel_helper._otel_enabled = True
        result = otel_helper.dump_traces_to_file("req-123")
        assert result == {"json_file": None, "text_file": None}

    def test_successful_dump(self, tmp_path):
        otel_helper._otel_enabled = True
        otel_helper._collected_spans.append(
            {
                "trace_id": "00000001234567890abcdef0",
                "span_id": "00fedcba09876543",
                "name": "test_span",
                "start_time": 1700000000000000000,
                "end_time": 1700000001000000000,
                "duration_ns": 1000000000,
                "duration_ms": 1000.0,
                "attributes": {"key": "value"},
                "status": {"status_code": "OK", "description": None},
                "parent_span_id": None,
            }
        )

        result = otel_helper.dump_traces_to_file("test-req", output_dir=str(tmp_path))
        assert result["json_file"] is not None
        assert result["text_file"] is not None
        assert os.path.exists(result["json_file"])
        assert os.path.exists(result["text_file"])

        with open(result["json_file"]) as f:
            data = json.loads(f.readline())
            assert data["name"] == "test_span"

        with open(result["text_file"]) as f:
            content = f.read()
            assert "test_span" in content
            assert "test-req" in content
            assert "key: value" in content

    def test_dump_with_no_start_time(self, tmp_path):
        otel_helper._otel_enabled = True
        otel_helper._collected_spans.append(
            {
                "trace_id": "abc",
                "span_id": "def",
                "name": "no_start",
                "start_time": None,
                "end_time": None,
                "duration_ns": None,
                "duration_ms": 0,
                "attributes": {},
                "status": {},
                "parent_span_id": None,
            }
        )

        result = otel_helper.dump_traces_to_file("no-start", output_dir=str(tmp_path))
        assert result["json_file"] is not None

        with open(result["text_file"]) as f:
            content = f.read()
            assert "Unknown" in content

    def test_dump_exception(self):
        otel_helper._otel_enabled = True
        otel_helper._collected_spans.append({"name": "span"})

        with patch("os.makedirs", side_effect=OSError("permission denied")):
            result = otel_helper.dump_traces_to_file("fail-req")
        assert result == {"json_file": None, "text_file": None}


# =============================================================================
# Utility Function Tests
# =============================================================================


@pytest.mark.unit
class TestClearCollectedSpans:
    def test_clears_spans(self):
        otel_helper._collected_spans.append({"name": "span1"})
        otel_helper._collected_spans.append({"name": "span2"})
        assert len(otel_helper._collected_spans) == 2

        otel_helper.clear_collected_spans()
        assert len(otel_helper._collected_spans) == 0


@pytest.mark.unit
class TestGetSpanCount:
    def test_empty(self):
        assert otel_helper.get_span_count() == 0

    def test_with_spans(self):
        otel_helper._collected_spans.append({"name": "s1"})
        otel_helper._collected_spans.append({"name": "s2"})
        assert otel_helper.get_span_count() == 2


@pytest.mark.unit
class TestTraceOperation:
    def test_disabled(self):
        with otel_helper.trace_operation("test_op") as span:
            assert span is None

    def test_enabled_no_error(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
        otel_helper._tracer = mock_tracer

        with otel_helper.trace_operation("test_op", attr1="val1") as span:
            assert span is mock_span

    def test_enabled_with_exception(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_span = MagicMock()

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_span)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = ctx

        otel_helper._tracer = mock_tracer

        with pytest.raises(ValueError, match="test error"):
            with otel_helper.trace_operation("fail_op"):
                raise ValueError("test error")

        mock_span.set_attribute.assert_any_call("error", True)
        mock_span.set_attribute.assert_any_call("error.type", "ValueError")
        mock_span.set_attribute.assert_any_call("error.message", "test error")


@pytest.mark.unit
class TestAddSpanAttribute:
    def test_with_valid_span(self):
        mock_span = MagicMock()
        otel_helper.add_span_attribute(mock_span, "key", "value")
        mock_span.set_attribute.assert_called_once_with("key", "value")

    def test_with_none_span(self):
        otel_helper.add_span_attribute(None, "key", "value")

    def test_with_span_without_set_attribute(self):
        otel_helper.add_span_attribute("not_a_span", "key", "value")


@pytest.mark.unit
class TestGetTracer:
    def test_returns_none_by_default(self):
        assert otel_helper.get_tracer() is None

    def test_returns_tracer_when_set(self):
        mock_tracer = MagicMock()
        otel_helper._tracer = mock_tracer
        assert otel_helper.get_tracer() is mock_tracer


@pytest.mark.unit
class TestIsTracingEnabled:
    def test_disabled_by_default(self):
        assert otel_helper.is_tracing_enabled() is False

    def test_enabled(self):
        otel_helper._otel_enabled = True
        assert otel_helper.is_tracing_enabled() is True


# =============================================================================
# trace_function Decorator Tests
# =============================================================================


@pytest.mark.unit
class TestTraceFunction:
    def test_decorator_disabled(self):
        @otel_helper.trace_function("test.func")
        def my_func(x, y):
            return x + y

        assert my_func(1, 2) == 3

    def test_decorator_auto_name(self):
        @otel_helper.trace_function()
        def another_func():
            return 42

        assert another_func() == 42

    def test_decorator_enabled(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_span)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = ctx
        otel_helper._tracer = mock_tracer

        @otel_helper.trace_function("my.traced.func")
        def traced_func(x):
            return x * 2

        result = traced_func(5)
        assert result == 10


# =============================================================================
# create_historical_span Tests
# =============================================================================


@pytest.mark.unit
class TestCreateHistoricalSpan:
    def test_tracing_disabled(self):
        result = otel_helper.create_historical_span("test_span", 1000.0, 2000.0, {"key": "value"})
        assert result is None

    def test_no_tracer(self):
        otel_helper._otel_enabled = True
        otel_helper._tracer = None
        result = otel_helper.create_historical_span("test_span", 1000.0, 2000.0, {"key": "value"})
        assert result is None

    def _setup_otel_mock(self):
        mock_trace_module = MagicMock()
        mock_trace_module.set_span_in_context.return_value = "mock_context"
        mock_otel = MagicMock()
        mock_otel.trace = mock_trace_module
        return mock_otel, mock_trace_module

    def test_successful_creation(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span
        otel_helper._tracer = mock_tracer

        mock_otel, mock_trace_module = self._setup_otel_mock()

        with patch.dict(
            "sys.modules", {"opentelemetry": mock_otel, "opentelemetry.trace": mock_trace_module}
        ):
            result = otel_helper.create_historical_span(
                "test_span",
                1000.0,
                2000.0,
                {"key": "value", "request_id": "abc"},
            )
        assert result == "mock_context"
        mock_span.set_attribute.assert_any_call("key", "value")
        mock_span.set_attribute.assert_any_call("execution_time_ms", 1000000.0)
        mock_span.end.assert_called_once()

    def test_with_parent_context(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span
        otel_helper._tracer = mock_tracer

        mock_otel, mock_trace_module = self._setup_otel_mock()
        mock_trace_module.set_span_in_context.return_value = "child_context"
        parent_ctx = MagicMock()

        with patch.dict(
            "sys.modules", {"opentelemetry": mock_otel, "opentelemetry.trace": mock_trace_module}
        ):
            otel_helper.create_historical_span(
                "child_span",
                1000.0,
                2000.0,
                {"key": "value"},
                parent_context=parent_ctx,
            )

        mock_tracer.start_span.assert_called_once()
        call_kwargs = mock_tracer.start_span.call_args
        assert call_kwargs[1].get("context") is parent_ctx

    def test_exception_handling(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_tracer.start_span.side_effect = RuntimeError("span creation failed")
        otel_helper._tracer = mock_tracer

        result = otel_helper.create_historical_span("fail_span", 1000.0, 2000.0, {"key": "value"})
        assert result is None


# =============================================================================
# Additional edge-case tests
# =============================================================================


@pytest.mark.unit
class TestInitOtelEdgeCases:
    @patch.dict(os.environ, {"VIA_ENABLE_OTEL": "1"}, clear=False)
    def test_enabled_via_env_1(self):
        mock_trace = MagicMock()
        mock_provider = MagicMock()
        mock_trace.get_tracer.return_value = MagicMock()
        mock_resource_cls = MagicMock()
        mock_resource_cls.create.return_value = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider)

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.trace": mock_trace,
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
                "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
                "opentelemetry.sdk.trace.export": MagicMock(
                    BatchSpanProcessor=MagicMock(),
                    ConsoleSpanExporter=MagicMock(),
                    SpanExporter=MagicMock,
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            result = otel_helper.init_otel(exporter_type="console")
        assert result is True

    @patch.dict(os.environ, {"VIA_ENABLE_OTEL": "true"}, clear=False)
    def test_otlp_without_endpoint(self):
        mock_trace = MagicMock()
        mock_provider = MagicMock()
        mock_resource_cls = MagicMock()
        mock_resource_cls.create.return_value = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider)

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.trace": mock_trace,
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
                "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
                "opentelemetry.sdk.trace.export": MagicMock(
                    BatchSpanProcessor=MagicMock(),
                    SpanExporter=MagicMock,
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            result = otel_helper.init_otel(exporter_type="otlp", endpoint="")
        assert result is False

    @patch.dict(os.environ, {"VIA_ENABLE_OTEL": "true"}, clear=False)
    def test_custom_service_name(self):
        mock_trace = MagicMock()
        mock_provider = MagicMock()
        mock_trace.get_tracer.return_value = MagicMock()
        mock_resource_cls = MagicMock()
        mock_resource_cls.create.return_value = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider)

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.trace": mock_trace,
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
                "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
                "opentelemetry.sdk.trace.export": MagicMock(
                    BatchSpanProcessor=MagicMock(),
                    ConsoleSpanExporter=MagicMock(),
                    SpanExporter=MagicMock,
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            result = otel_helper.init_otel(service_name="custom-service", exporter_type="console")
        assert result is True
        mock_resource_cls.create.assert_called_once()
        call_args = mock_resource_cls.create.call_args[0][0]
        assert call_args["service.name"] == "custom-service"


@pytest.mark.unit
class TestSpanCollectorEdgeCases:
    def test_export_no_status(self):
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.trace": MagicMock(),
                "opentelemetry.sdk.trace.export": MagicMock(
                    SpanExporter=type("SpanExporter", (), {}),
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            collector = otel_helper._create_span_collector()
            span = MagicMock()
            span.context.trace_id = 0xABCD
            span.context.span_id = 0x1234
            span.name = "no_status_span"
            span.start_time = 1000
            span.end_time = 2000
            span.attributes = {"a": 1}
            span.status = None
            span.parent = None

            result = collector.export([span])
            assert result == "SUCCESS"
            data = otel_helper._collected_spans[0]
            assert data["status"]["status_code"] is None
            assert data["status"]["description"] is None

    def test_export_multiple_spans(self):
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.trace": MagicMock(),
                "opentelemetry.sdk.trace.export": MagicMock(
                    SpanExporter=type("SpanExporter", (), {}),
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            collector = otel_helper._create_span_collector()
            spans = []
            for i in range(5):
                s = MagicMock()
                s.context.trace_id = i
                s.context.span_id = i + 100
                s.name = f"span_{i}"
                s.start_time = 1000 * i
                s.end_time = 1000 * (i + 1)
                s.attributes = {}
                s.status.status_code.name = "OK"
                s.status.description = None
                s.parent = None
                spans.append(s)
            result = collector.export(spans)
            assert result == "SUCCESS"
            assert len(otel_helper._collected_spans) == 5


@pytest.mark.unit
class TestDumpTracesToFileEdgeCases:
    def test_dump_with_attributes_and_parent(self, tmp_path):
        otel_helper._otel_enabled = True
        otel_helper._collected_spans.append(
            {
                "trace_id": "aaa",
                "span_id": "bbb",
                "name": "span_with_attrs",
                "start_time": 1700000000000000000,
                "end_time": 1700000001000000000,
                "duration_ns": 1000000000,
                "duration_ms": 1000.0,
                "attributes": {"request_id": "r1", "model": "gpt-4"},
                "status": {"status_code": "OK", "description": "success"},
                "parent_span_id": "ccc",
            }
        )
        result = otel_helper.dump_traces_to_file("attr-req", output_dir=str(tmp_path))
        assert result["text_file"] is not None

        with open(result["text_file"]) as f:
            content = f.read()
            assert "request_id: r1" in content
            assert "model: gpt-4" in content
            assert "Parent Span ID: ccc" in content

    def test_dump_with_empty_attributes(self, tmp_path):
        otel_helper._otel_enabled = True
        otel_helper._collected_spans.append(
            {
                "trace_id": "t1",
                "span_id": "s1",
                "name": "no_attrs",
                "start_time": 1700000000000000000,
                "end_time": 1700000001000000000,
                "duration_ns": 1000000000,
                "duration_ms": 1000.0,
                "attributes": {},
                "status": {"status_code": "UNSET"},
                "parent_span_id": None,
            }
        )
        result = otel_helper.dump_traces_to_file("empty-attr", output_dir=str(tmp_path))
        with open(result["text_file"]) as f:
            content = f.read()
            assert "Attributes:" not in content


@pytest.mark.unit
class TestTraceFunctionEdgeCases:
    def test_decorator_preserves_return_value_when_enabled(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_span)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = ctx
        otel_helper._tracer = mock_tracer

        @otel_helper.trace_function()
        def returns_dict():
            return {"a": 1}

        result = returns_dict()
        assert result == {"a": 1}

    def test_decorator_with_kwargs(self):
        @otel_helper.trace_function("kw.func")
        def func_with_kwargs(a, b=10):
            return a + b

        assert func_with_kwargs(5, b=20) == 25


# =============================================================================
# Additional comprehensive tests for full coverage
# =============================================================================


@pytest.mark.unit
class TestInitOtelFullPaths:
    @patch.dict(os.environ, {"VIA_ENABLE_OTEL": "true"}, clear=False)
    def test_console_exporter_full_execution(self):
        import importlib

        importlib.reload(otel_helper)
        otel_helper._otel_enabled = False
        otel_helper._tracer = None
        otel_helper._collected_spans.clear()

        try:
            result = otel_helper.init_otel(service_name="test-service", exporter_type="console")
            assert result is True
            assert otel_helper._otel_enabled is True
            assert otel_helper._tracer is not None
        except ImportError:
            mock_trace = MagicMock()
            mock_provider = MagicMock()
            mock_trace.get_tracer.return_value = MagicMock()
            mock_resource_cls = MagicMock()
            mock_resource_cls.create.return_value = MagicMock()
            mock_provider_cls = MagicMock(return_value=mock_provider)

            with patch.dict(
                "sys.modules",
                {
                    "opentelemetry": MagicMock(),
                    "opentelemetry.trace": mock_trace,
                    "opentelemetry.sdk": MagicMock(),
                    "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
                    "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
                    "opentelemetry.sdk.trace.export": MagicMock(
                        BatchSpanProcessor=MagicMock(),
                        ConsoleSpanExporter=MagicMock(),
                        SpanExporter=MagicMock,
                        SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                    ),
                },
            ):
                result = otel_helper.init_otel(service_name="test-service", exporter_type="console")
            assert result is True
        finally:
            otel_helper._otel_enabled = False
            otel_helper._tracer = None

    @patch.dict(
        os.environ,
        {"VIA_ENABLE_OTEL": "true"},
        clear=False,
    )
    def test_otlp_exporter_with_endpoint(self):
        mock_trace = MagicMock()
        mock_provider = MagicMock()
        mock_tracer = MagicMock()
        mock_trace.get_tracer.return_value = mock_tracer
        mock_resource_cls = MagicMock()
        mock_resource_cls.create.return_value = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider)
        mock_batch = MagicMock()
        mock_otlp = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.trace": mock_trace,
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
                "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
                "opentelemetry.sdk.trace.export": MagicMock(
                    BatchSpanProcessor=mock_batch,
                    SpanExporter=MagicMock,
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
                "opentelemetry.exporter": MagicMock(),
                "opentelemetry.exporter.otlp": MagicMock(),
                "opentelemetry.exporter.otlp.proto": MagicMock(),
                "opentelemetry.exporter.otlp.proto.http": MagicMock(),
                "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(
                    OTLPSpanExporter=mock_otlp
                ),
            },
        ):
            result = otel_helper.init_otel(exporter_type="otlp", endpoint="http://localhost:4318")
        assert result is True
        assert otel_helper._otel_enabled is True


@pytest.mark.unit
class TestDumpTracesToFileFullPaths:
    def test_dump_multiple_spans_with_various_data(self, tmp_path):
        otel_helper._otel_enabled = True
        otel_helper._collected_spans.extend(
            [
                {
                    "trace_id": "trace1",
                    "span_id": "span1",
                    "name": "span_A",
                    "start_time": 1700000000000000000,
                    "end_time": 1700000001000000000,
                    "duration_ns": 1000000000,
                    "duration_ms": 1000.0,
                    "attributes": {"req": "1", "model": "gpt"},
                    "status": {"status_code": "OK", "description": "done"},
                    "parent_span_id": "parent1",
                },
                {
                    "trace_id": "trace2",
                    "span_id": "span2",
                    "name": "span_B",
                    "start_time": None,
                    "end_time": None,
                    "duration_ns": None,
                    "duration_ms": 0,
                    "attributes": {},
                    "status": {"status_code": "ERROR"},
                    "parent_span_id": None,
                },
                {
                    "trace_id": "trace3",
                    "span_id": "span3",
                    "name": "span_C",
                    "start_time": 1700000002000000000,
                    "end_time": 1700000003000000000,
                    "duration_ns": 1000000000,
                    "duration_ms": 1000.0,
                    "attributes": {"key1": "val1"},
                    "status": {},
                    "parent_span_id": None,
                },
            ]
        )

        result = otel_helper.dump_traces_to_file("multi-req", output_dir=str(tmp_path))
        assert result["json_file"] is not None
        assert result["text_file"] is not None

        with open(result["json_file"]) as f:
            lines = f.readlines()
            assert len(lines) == 3
            data0 = json.loads(lines[0])
            assert data0["name"] == "span_A"

        with open(result["text_file"]) as f:
            content = f.read()
            assert "span_A" in content
            assert "span_B" in content
            assert "span_C" in content
            assert "Unknown" in content
            assert "Total Spans: 3" in content
            assert "req: 1" in content

    def test_enabled_but_empty_spans(self):
        otel_helper._otel_enabled = True
        otel_helper._collected_spans.clear()
        result = otel_helper.dump_traces_to_file("empty-req")
        assert result == {"json_file": None, "text_file": None}


@pytest.mark.unit
class TestTraceOperationFullPaths:
    def test_enabled_with_attributes(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_span)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = ctx
        otel_helper._tracer = mock_tracer

        with otel_helper.trace_operation("my_op", request_id="abc", model="gpt") as span:
            assert span is mock_span

        mock_tracer.start_as_current_span.assert_called_once_with(
            "my_op", context=None, attributes={"request_id": "abc", "model": "gpt"}
        )

    def test_disabled_yields_none_and_runs_body(self):
        otel_helper._otel_enabled = False
        otel_helper._tracer = None
        executed = False

        with otel_helper.trace_operation("noop") as span:
            assert span is None
            executed = True

        assert executed is True

    def test_enabled_exception_sets_error_attributes(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_span)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = ctx
        otel_helper._tracer = mock_tracer

        with pytest.raises(RuntimeError, match="boom"):
            with otel_helper.trace_operation("fail"):
                raise RuntimeError("boom")

        mock_span.set_attribute.assert_any_call("error", True)
        mock_span.set_attribute.assert_any_call("error.type", "RuntimeError")
        mock_span.set_attribute.assert_any_call("error.message", "boom")


@pytest.mark.unit
class TestCreateHistoricalSpanFullPaths:
    def test_successful_with_multiple_attributes(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span
        otel_helper._tracer = mock_tracer

        mock_trace_module = MagicMock()
        mock_trace_module.set_span_in_context.return_value = "ctx"

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(trace=mock_trace_module),
                "opentelemetry.trace": mock_trace_module,
            },
        ):
            result = otel_helper.create_historical_span(
                "inference",
                100.0,
                200.5,
                {"model": "gpt-4", "tokens": 512, "request_id": "req-1"},
            )
        assert result == "ctx"
        mock_span.set_attribute.assert_any_call("model", "gpt-4")
        mock_span.set_attribute.assert_any_call("tokens", 512)
        mock_span.set_attribute.assert_any_call("execution_time_ms", 100500.0)
        mock_span.end.assert_called_once()
        start_ns = mock_tracer.start_span.call_args[1]["start_time"]
        assert start_ns == 100000000000

    def test_with_parent_context_passed(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_span.return_value = mock_span
        otel_helper._tracer = mock_tracer

        mock_trace_module = MagicMock()
        mock_trace_module.set_span_in_context.return_value = "child_ctx"
        parent = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(trace=mock_trace_module),
                "opentelemetry.trace": mock_trace_module,
            },
        ):
            result = otel_helper.create_historical_span(
                "child_span", 50.0, 60.0, {"a": "b"}, parent_context=parent
            )
        assert result == "child_ctx"
        mock_tracer.start_span.assert_called_once_with(
            "child_span", context=parent, start_time=50000000000
        )

    def test_tracing_disabled_returns_none(self):
        otel_helper._otel_enabled = False
        result = otel_helper.create_historical_span("x", 0, 1, {})
        assert result is None

    def test_tracer_none_returns_none(self):
        otel_helper._otel_enabled = True
        otel_helper._tracer = None
        result = otel_helper.create_historical_span("x", 0, 1, {})
        assert result is None

    def test_exception_during_span_creation(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_tracer.start_span.side_effect = Exception("otel failure")
        otel_helper._tracer = mock_tracer

        result = otel_helper.create_historical_span("fail", 1.0, 2.0, {"k": "v"})
        assert result is None


@pytest.mark.unit
class TestSpanCollectorFullPaths:
    def test_collector_export_collects_all_fields(self):
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.trace": MagicMock(),
                "opentelemetry.sdk.trace.export": MagicMock(
                    SpanExporter=type("SpanExporter", (), {}),
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            collector = otel_helper._create_span_collector()
            span = MagicMock()
            span.context.trace_id = 0xABCDEF1234567890
            span.context.span_id = 0x1234567890ABCDEF
            span.name = "full_span"
            span.start_time = 5000000000
            span.end_time = 6000000000
            span.attributes = {"key1": "val1", "key2": 42}
            span.status.status_code.name = "OK"
            span.status.description = "success"
            span.parent = MagicMock()
            span.parent.span_id = 0xFEDCBA9876543210

            result = collector.export([span])
            assert result == "SUCCESS"
            assert len(otel_helper._collected_spans) == 1

            data = otel_helper._collected_spans[0]
            assert data["name"] == "full_span"
            assert data["duration_ns"] == 1000000000
            assert data["duration_ms"] == 1000.0
            assert data["attributes"] == {"key1": "val1", "key2": 42}
            assert data["status"]["status_code"] == "OK"
            assert data["status"]["description"] == "success"
            assert data["parent_span_id"] is not None

    def test_collector_shutdown_noop(self):
        with patch.dict(
            "sys.modules",
            {
                "opentelemetry": MagicMock(),
                "opentelemetry.sdk": MagicMock(),
                "opentelemetry.sdk.trace": MagicMock(),
                "opentelemetry.sdk.trace.export": MagicMock(
                    SpanExporter=type("SpanExporter", (), {}),
                    SpanExportResult=MagicMock(SUCCESS="SUCCESS", FAILURE="FAILURE"),
                ),
            },
        ):
            collector = otel_helper._create_span_collector()
            collector.shutdown()


@pytest.mark.unit
class TestAddSpanAttributeFullPaths:
    def test_span_with_set_attribute(self):
        mock_span = MagicMock()
        otel_helper.add_span_attribute(mock_span, "model", "gpt-4")
        mock_span.set_attribute.assert_called_once_with("model", "gpt-4")

    def test_none_span(self):
        otel_helper.add_span_attribute(None, "key", "value")

    def test_object_without_set_attribute(self):
        otel_helper.add_span_attribute(42, "key", "value")


@pytest.mark.unit
class TestHelperFunctions:
    def test_get_tracer_returns_none_initially(self):
        assert otel_helper.get_tracer() is None

    def test_get_tracer_returns_set_tracer(self):
        mock = MagicMock()
        otel_helper._tracer = mock
        assert otel_helper.get_tracer() is mock

    def test_is_tracing_enabled_false(self):
        assert otel_helper.is_tracing_enabled() is False

    def test_is_tracing_enabled_true(self):
        otel_helper._otel_enabled = True
        assert otel_helper.is_tracing_enabled() is True

    def test_clear_collected_spans(self):
        otel_helper._collected_spans.append({"a": 1})
        otel_helper._collected_spans.append({"b": 2})
        otel_helper.clear_collected_spans()
        assert len(otel_helper._collected_spans) == 0

    def test_get_span_count(self):
        assert otel_helper.get_span_count() == 0
        otel_helper._collected_spans.append({"x": 1})
        assert otel_helper.get_span_count() == 1


@pytest.mark.unit
class TestTraceFunctionDecorator:
    def test_explicit_name(self):
        @otel_helper.trace_function("my.module.func")
        def my_func():
            return 99

        assert my_func() == 99

    def test_auto_name(self):
        @otel_helper.trace_function()
        def auto_named():
            return "auto"

        assert auto_named() == "auto"

    def test_enabled_with_auto_name(self):
        otel_helper._otel_enabled = True
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_span)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = ctx
        otel_helper._tracer = mock_tracer

        @otel_helper.trace_function()
        def traced_auto():
            return 42

        result = traced_auto()
        assert result == 42
        call_args = mock_tracer.start_as_current_span.call_args[0]
        assert "traced_auto" in call_args[0]


# =============================================================================
# Inbound W3C traceparent propagation (NVBug 6537736)
# =============================================================================

# Fixed ids so assertions can compare against the exact values a client sent.
REMOTE_TRACE_ID_HEX = "7947efe02129245f8b5033e0b3a82bb4"
REMOTE_SPAN_ID_HEX = "bd41b2a934561ac9"
REMOTE_TRACEPARENT = f"00-{REMOTE_TRACE_ID_HEX}-{REMOTE_SPAN_ID_HEX}-01"


@pytest.fixture
def in_memory_tracer():
    """Enable tracing against a local provider whose spans land in memory.

    init_otel() is deliberately not used: it calls trace.set_tracer_provider(),
    which OTEL only honours once per process, so a second call would silently
    no-op and make these tests depend on execution order.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    otel_helper._otel_enabled = True
    otel_helper._tracer = provider.get_tracer(__name__)
    yield exporter
    provider.shutdown()


@pytest.mark.unit
class TestExtractContextFromHeaders:
    def test_returns_none_when_otel_disabled(self):
        otel_helper._otel_enabled = False
        assert otel_helper.extract_context_from_headers({"traceparent": REMOTE_TRACEPARENT}) is None

    def test_extracts_trace_and_span_id(self, in_memory_tracer):
        from opentelemetry import trace

        context = otel_helper.extract_context_from_headers({"traceparent": REMOTE_TRACEPARENT})

        assert context is not None
        span_context = trace.get_current_span(context).get_span_context()
        assert format(span_context.trace_id, "032x") == REMOTE_TRACE_ID_HEX
        assert format(span_context.span_id, "016x") == REMOTE_SPAN_ID_HEX
        assert span_context.is_remote is True

    def test_header_name_is_case_insensitive(self, in_memory_tracer):
        from opentelemetry import trace

        context = otel_helper.extract_context_from_headers({"TraceParent": REMOTE_TRACEPARENT})

        assert context is not None
        span_context = trace.get_current_span(context).get_span_context()
        assert format(span_context.trace_id, "032x") == REMOTE_TRACE_ID_HEX

    def test_returns_none_without_traceparent(self, in_memory_tracer):
        assert (
            otel_helper.extract_context_from_headers({"content-type": "application/json"}) is None
        )

    def test_returns_none_for_empty_headers(self, in_memory_tracer):
        assert otel_helper.extract_context_from_headers({}) is None

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "not-a-traceparent",
            "00-tooshort-bd41b2a934561ac9-01",
            # Version ff is forbidden by the W3C spec. Higher versions such as
            # 99 are intentionally not listed: the spec requires parsers to
            # accept them for forward compatibility.
            "ff-7947efe02129245f8b5033e0b3a82bb4-bd41b2a934561ac9-01",
            "00-zzz7efe02129245f8b5033e0b3a82bb4-bd41b2a934561ac9-01",
            "00-7947efe02129245f8b5033e0b3a82bb4-bd41b2a934561ac9",
            "00-7947efe02129245f8b5033e0b3a82bb4-0000000000000000-01",
        ],
    )
    def test_returns_none_for_malformed_traceparent(self, in_memory_tracer, value):
        assert otel_helper.extract_context_from_headers({"traceparent": value}) is None

    def test_returns_none_for_all_zero_trace_id(self, in_memory_tracer):
        # A zeroed trace id is syntactically valid but semantically invalid;
        # accepting it would produce spans under a bogus parent.
        zeroed = "00-00000000000000000000000000000000-bd41b2a934561ac9-01"
        assert otel_helper.extract_context_from_headers({"traceparent": zeroed}) is None

    def test_does_not_raise_on_unusable_headers(self, in_memory_tracer):
        class Exploding:
            def keys(self):
                raise RuntimeError("boom")

        assert otel_helper.extract_context_from_headers(Exploding()) is None
        assert otel_helper.extract_context_from_headers(None) is None


@pytest.mark.unit
class TestRemoteParentSpanWiring:
    """The E2E span must adopt the caller's traceparent (NVBug 6537736).

    Mirrors how _trigger_query builds its span tree: an E2E root span, then
    children created from that span's context.
    """

    def test_e2e_span_adopts_incoming_trace_id(self, in_memory_tracer):
        context = otel_helper.extract_context_from_headers({"traceparent": REMOTE_TRACEPARENT})

        otel_helper.get_tracer().start_span("Summarization E2E Latency", context=context).end()

        (span,) = in_memory_tracer.get_finished_spans()
        assert format(span.context.trace_id, "032x") == REMOTE_TRACE_ID_HEX
        assert span.parent is not None
        assert format(span.parent.span_id, "016x") == REMOTE_SPAN_ID_HEX

    def test_e2e_span_is_root_without_traceparent(self, in_memory_tracer):
        context = otel_helper.extract_context_from_headers({})

        otel_helper.get_tracer().start_span("Summarization E2E Latency", context=context).end()

        (span,) = in_memory_tracer.get_finished_spans()
        assert span.parent is None
        assert span.context.trace_id != 0
        assert format(span.context.trace_id, "032x") != REMOTE_TRACE_ID_HEX

    def test_trace_operation_child_shares_incoming_trace_id(self, in_memory_tracer):
        from opentelemetry import trace

        context = otel_helper.extract_context_from_headers({"traceparent": REMOTE_TRACEPARENT})
        e2e_span = otel_helper.get_tracer().start_span("Summarization E2E Latency", context=context)
        e2e_context = trace.set_span_in_context(e2e_span)

        with otel_helper.trace_operation("CTX-RAG Call - Summarize", parent_context=e2e_context):
            pass
        e2e_span.end()

        by_name = {s.name: s for s in in_memory_tracer.get_finished_spans()}
        child = by_name["CTX-RAG Call - Summarize"]
        assert format(child.context.trace_id, "032x") == REMOTE_TRACE_ID_HEX
        assert child.parent.span_id == by_name["Summarization E2E Latency"].context.span_id

    def test_historical_child_spans_share_incoming_trace_id(self, in_memory_tracer):
        from opentelemetry import trace

        context = otel_helper.extract_context_from_headers({"traceparent": REMOTE_TRACEPARENT})
        e2e_span = otel_helper.get_tracer().start_span("Summarization E2E Latency", context=context)
        e2e_context = trace.set_span_in_context(e2e_span)

        chunk_context = otel_helper.create_historical_span(
            "Chunk 0", 1000.0, 1002.0, {"chunk_idx": 0}, parent_context=e2e_context
        )
        otel_helper.create_historical_span(
            "Decode - Chunk 0", 1000.0, 1001.0, {"chunk_idx": 0}, parent_context=chunk_context
        )
        e2e_span.end()

        spans = in_memory_tracer.get_finished_spans()
        assert {s.name for s in spans} == {
            "Summarization E2E Latency",
            "Chunk 0",
            "Decode - Chunk 0",
        }
        assert {format(s.context.trace_id, "032x") for s in spans} == {REMOTE_TRACE_ID_HEX}

        by_name = {s.name: s for s in spans}
        assert (
            by_name["Chunk 0"].parent.span_id
            == by_name["Summarization E2E Latency"].context.span_id
        )
        assert by_name["Decode - Chunk 0"].parent.span_id == by_name["Chunk 0"].context.span_id
