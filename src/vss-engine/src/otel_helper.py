######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import json
import logging
import os
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from threading import Lock
from typing import Dict

logger = logging.getLogger(__name__)

# Global state
_otel_enabled = False
_tracer = None
_collected_spans = deque(maxlen=10000)  # Keep last 10k spans in memory
_spans_lock = Lock()


def init_otel(service_name: str = "via-engine", exporter_type: str = None, endpoint: str = None):
    """Initialize OpenTelemetry if enabled and available.

    Args:
        service_name: Name of the service for OTEL resource
        exporter_type: Type of exporter ('console', 'otlp'). If None, determined from env vars
        endpoint: OTLP endpoint. If None, uses default from environment
    """
    global _otel_enabled, _tracer

    # Check if OTEL should be enabled
    if not os.getenv("VIA_ENABLE_OTEL", "false").lower() in ("true", "1"):
        logger.info("OpenTelemetry disabled via VIA_ENABLE_OTEL")
        return False

    # Determine exporter type if not specified
    if exporter_type is None:
        exporter_type = os.getenv("VIA_OTEL_EXPORTER", "console")

    # Determine endpoint if not specified
    if endpoint is None:
        endpoint = os.getenv("VIA_OTEL_ENDPOINT", "http://otel-collector:4318")

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Create resource
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": "1.0.0",
            }
        )

        # Setup tracing
        provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(provider)

        # Always add our custom span collector to collect spans in memory
        collector_processor = BatchSpanProcessor(_create_span_collector())
        provider.add_span_processor(collector_processor)

        # Add appropriate exporter
        if exporter_type == "console":
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter

            processor = BatchSpanProcessor(
                ConsoleSpanExporter(formatter=lambda span: span.to_json(indent=None))
            )
            provider.add_span_processor(processor)
        elif exporter_type == "otlp" and endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            traces_endpoint = endpoint + "/v1/traces"
            processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint))
            provider.add_span_processor(processor)
        else:
            raise ValueError(
                f"Invalid exporter type: {exporter_type}. Valid types are: console, otlp. "
                f"Check if endpoint is provided for otlp exporter."
            )

        # Get tracer
        _tracer = trace.get_tracer(__name__)

        _otel_enabled = True
        logger.info(
            f"[VIA-ENGINE] OTEL Initialized Successfully. Exporter Type: {exporter_type}, Endpoint: {endpoint}"  # noqa: E501
        )
        return True

    except ImportError as e:
        logger.info(f"OpenTelemetry dependencies not available: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to initialize OpenTelemetry: {e}")
        return False


def _create_span_collector():
    """Create a custom span collector that stores spans in memory."""
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    class SpanCollector(SpanExporter):
        def export(self, spans):
            try:
                with _spans_lock:
                    for span in spans:
                        span_data = {
                            "trace_id": format(span.context.trace_id, "032x"),
                            "span_id": format(span.context.span_id, "016x"),
                            "name": span.name,
                            "start_time": span.start_time,
                            "end_time": span.end_time,
                            "duration_ns": (
                                span.end_time - span.start_time if span.end_time else None
                            ),
                            "duration_ms": (
                                (span.end_time - span.start_time) / 1_000_000
                                if span.end_time
                                else None
                            ),
                            "attributes": dict(span.attributes) if span.attributes else {},
                            "status": {
                                "status_code": (
                                    span.status.status_code.name if span.status else None
                                ),
                                "description": span.status.description if span.status else None,
                            },
                            "parent_span_id": (
                                format(span.parent.span_id, "016x") if span.parent else None
                            ),
                        }
                        _collected_spans.append(span_data)
                return SpanExportResult.SUCCESS
            except Exception as e:
                logger.error(f"Failed to collect spans: {e}")
                return SpanExportResult.FAILURE

        def shutdown(self):
            pass

    return SpanCollector()


def dump_traces_to_file(request_id: str, output_dir: str = "/tmp/via-logs") -> Dict[str, str]:
    """Dump collected OTEL traces to JSON and text files.

    Args:
        request_id: The request ID to include in filename
        output_dir: Directory to save the files (default: /tmp/via-logs)

    Returns:
        Dict with 'json_file' and 'text_file' paths
    """
    if not _otel_enabled or not _collected_spans:
        logger.debug("OTEL not enabled or no spans collected")
        return {"json_file": None, "text_file": None}

    try:
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Create filenames with request ID
        json_file = os.path.join(output_dir, f"via_otel_traces_{request_id}.json")
        text_file = os.path.join(output_dir, f"via_otel_traces_{request_id}.txt")

        # Copy current spans to avoid locking for too long
        with _spans_lock:
            spans_to_dump = list(_collected_spans)

        # Write JSON file (machine readable)
        with open(json_file, "w") as f:
            for span_data in spans_to_dump:
                f.write(json.dumps(span_data) + "\n")

        # Write text file (human readable)
        with open(text_file, "w") as f:
            f.write("=== VIA OTEL Trace Dump ===\n")
            f.write(f"Request ID: {request_id}\n")
            f.write(f"Dump Time: {datetime.now().isoformat()}\n")
            f.write(f"Total Spans: {len(spans_to_dump)}\n")
            f.write("=" * 50 + "\n\n")

            for span_data in spans_to_dump:
                duration_ms = span_data.get("duration_ms", 0)
                start_time_str = (
                    datetime.fromtimestamp(span_data["start_time"] / 1_000_000_000).strftime(
                        "%Y-%m-%d %H:%M:%S.%f"
                    )[:-3]
                    if span_data.get("start_time")
                    else "Unknown"
                )

                f.write("=== OTEL SPAN ===\n")
                f.write(f"Trace ID: {span_data.get('trace_id', 'Unknown')}\n")
                f.write(f"Span ID: {span_data.get('span_id', 'Unknown')}\n")
                f.write(f"Parent Span ID: {span_data.get('parent_span_id', 'None')}\n")
                f.write(f"Name: {span_data.get('name', 'Unknown')}\n")
                f.write(f"Start Time: {start_time_str}\n")
                f.write(f"Duration: {duration_ms:.2f} ms\n")
                f.write(f"Status: {span_data.get('status', {}).get('status_code', 'Unknown')}\n")

                attributes = span_data.get("attributes", {})
                if attributes:
                    f.write("Attributes:\n")
                    for key, value in attributes.items():
                        f.write(f"  {key}: {value}\n")
                f.write("\n")

        logger.info(f"OTEL traces dumped to {json_file} and {text_file}")
        return {"json_file": json_file, "text_file": text_file}

    except Exception as e:
        logger.error(f"Failed to dump OTEL traces: {e}")
        return {"json_file": None, "text_file": None}


def clear_collected_spans():
    """Clear all collected spans from memory."""
    with _spans_lock:
        _collected_spans.clear()
    logger.debug("Cleared collected OTEL spans")


def get_span_count() -> int:
    """Get the number of spans currently collected."""
    with _spans_lock:
        return len(_collected_spans)


@contextmanager
def trace_operation(name: str, **attributes):
    """Create a trace span if OTEL is enabled, otherwise do nothing."""
    if _otel_enabled and _tracer:
        with _tracer.start_as_current_span(name, attributes=attributes) as span:
            try:
                yield span
            except Exception as e:
                span.set_attribute("error", True)
                span.set_attribute("error.type", type(e).__name__)
                span.set_attribute("error.message", str(e))
                raise
    else:
        # No-op context manager
        yield None


def add_span_attribute(span, key: str, value):
    """Add attribute to span if it exists."""
    if span and hasattr(span, "set_attribute"):
        span.set_attribute(key, value)


def get_tracer():
    """Get the current tracer instance."""
    return _tracer


def is_tracing_enabled():
    """Check if OTEL is enabled."""
    return _otel_enabled


def trace_function(func_name: str = None):
    """Decorator to trace function execution."""

    def decorator(func):
        nonlocal func_name
        if func_name is None:
            func_name = f"{func.__module__}.{func.__name__}"

        def wrapper(*args, **kwargs):
            with trace_operation(func_name):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def create_historical_span(span_name: str, start_time: float, end_time: float, attributes: dict):
    """Create an OTEL span with explicit start and end times for completed operations."""
    try:

        if not is_tracing_enabled():
            return

        tracer = get_tracer()
        if not tracer:
            return

        # Convert timestamps from seconds to nanoseconds (OTEL requirement)
        start_time_ns = int(start_time * 1_000_000_000)
        end_time_ns = int(end_time * 1_000_000_000)

        # Create span with explicit start time
        span = tracer.start_span(span_name, start_time=start_time_ns)

        # Set all attributes
        for key, value in attributes.items():
            span.set_attribute(key, value)

        # Add execution time for consistency
        execution_time_seconds = end_time - start_time
        execution_time_ms = execution_time_seconds * 1000
        span.set_attribute("execution_time_ms", execution_time_ms)

        # End span with explicit end time
        span.end(end_time=end_time_ns)

        logger.debug(
            f"Created historical OTEL span '{span_name}' with duration {execution_time_ms:.2f}ms"
        )

    except Exception as e:
        logger.debug(f"Failed to create historical OTEL span: {e}")
