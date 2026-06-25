# SPDX-FileCopyrightText: Copyright (c) 2023-2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from prometheus_client import generate_latest

from common.logger import logger

# Global state
_otel_enabled = False
_tracer = None
_meter_provider = None
_prometheus_reader = None


def init_otel(service_name: str = "rtvi", service_version: str = "1.0.0", metric_views=None):
    """Initialize OpenTelemetry if enabled and available.

    The SDK automatically reads and configures itself from standard OpenTelemetry environment variables:
    - ENABLE_OTEL_MONITORING: Set to 'true' to enable OTLP exporters (default: false, only Prometheus /metrics enabled)  # noqa: E501
    - OTEL_SERVICE_NAME: Service name for traces (overrides service_name parameter)
    - OTEL_RESOURCE_ATTRIBUTES: Additional resource attributes (key1=value1,key2=value2)
    - OTEL_TRACES_EXPORTER: Exporter type - 'otlp', 'console', 'none' (default: 'otlp')
    - OTEL_METRICS_EXPORTER: Exporter type - 'otlp', 'console', 'none' (default: 'otlp')
    - OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint (default: http://localhost:4318)
    - OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: Traces-specific endpoint (overrides OTEL_EXPORTER_OTLP_ENDPOINT)
    - OTEL_EXPORTER_OTLP_METRICS_ENDPOINT: Metrics-specific endpoint (overrides OTEL_EXPORTER_OTLP_ENDPOINT)
    - OTEL_EXPORTER_OTLP_PROTOCOL: Protocol - 'http/protobuf', 'grpc' (default: http/protobuf)
    - OTEL_METRIC_EXPORT_INTERVAL: Metrics export interval in milliseconds (default: 60000 = 60 seconds)

    See: https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/

    Args:
        service_name: Default service name (overridden by OTEL_SERVICE_NAME env var if set)
        service_version: Service version for resource attributes
        metric_views: Optional list of View objects for configuring metric aggregations (e.g., histogram buckets)  # noqa: E501

    Returns:
        bool: True if OTEL was successfully initialized, False otherwise
    """
    global _otel_enabled, _tracer

    # Check if OTEL monitoring is enabled (standard variable)
    # When disabled (default), we still initialize Prometheus metrics for /v1/metrics endpoint
    # but skip OTLP exporters
    otel_monitoring_enabled = os.getenv("ENABLE_OTEL_MONITORING", "false").lower() in ("true", "1")
    if not otel_monitoring_enabled:
        logger.info(
            "OpenTelemetry OTLP exporters disabled via ENABLE_OTEL_MONITORING=false, initializing Prometheus metrics only"  # noqa: E501
        )
        try:
            # Create resource for metrics
            resource = Resource.create(
                {
                    "service.name": service_name,
                    "service.version": service_version,
                }
            )

            # Setup metrics with only Prometheus reader (no OTLP exporters)
            _init_metrics(resource, metric_views, skip_otlp=True)
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Prometheus metrics: {e}")
            return False

    try:

        # Create resource - automatically reads OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": service_version,
            }
        )

        # Setup tracing
        provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(provider)

        # Auto-configure exporter based on OTEL_TRACES_EXPORTER environment variable
        exporter_type = os.getenv("OTEL_TRACES_EXPORTER", "otlp").lower()

        if exporter_type == "console":

            processor = BatchSpanProcessor(
                ConsoleSpanExporter(formatter=lambda span: span.to_json(indent=None))
            )
            provider.add_span_processor(processor)
            logger.info("OTEL tracing initialized with console exporter")

        elif exporter_type == "otlp":

            # OTLPSpanExporter automatically reads OTEL_EXPORTER_OTLP_ENDPOINT and
            # OTEL_EXPORTER_OTLP_TRACES_ENDPOINT from environment
            exporter = OTLPSpanExporter()
            processor = BatchSpanProcessor(exporter)
            provider.add_span_processor(processor)

            # Log the endpoint being used (read from env or default)
            endpoint = (
                os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
                or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
                or "http://localhost:4318"
            )

            logger.info(f"OTEL tracing initialized with OTLP exporter (endpoint: {endpoint})")

        else:
            # No external exporter
            logger.info("OTEL tracing disabled")
            return False

        # Get tracer
        _tracer = trace.get_tracer(__name__)
        _otel_enabled = True

        # Setup metrics with optional views
        _init_metrics(resource, metric_views)

        return True

    except ImportError as e:
        logger.info(f"OpenTelemetry dependencies not available: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to initialize OpenTelemetry: {e}")
        return False


def _init_metrics(resource, metric_views=None, skip_otlp=False):
    """Initialize OpenTelemetry metrics with the given resource.

    Sets up both OTLP/Console exporter for remote collection AND a Prometheus
    exporter for the local /metrics endpoint.

    Args:
        resource: OpenTelemetry Resource object
        metric_views: Optional list of View objects for configuring metric aggregations
        skip_otlp: If True, only initialize Prometheus reader, skip OTLP/Console exporters
    """
    global _meter_provider, _prometheus_reader

    try:
        metric_readers = []

        # Use provided views or empty list
        views = metric_views if metric_views is not None else []

        # Setup Prometheus exporter for /metrics endpoint
        try:
            prometheus_reader = PrometheusMetricReader()
            metric_readers.append(prometheus_reader)
            _prometheus_reader = prometheus_reader

            logger.info("OTEL Prometheus metric reader initialized for /metrics endpoint")
        except Exception as e:
            logger.error(f"Failed to initialize Prometheus metric reader: {e}", exc_info=True)

        # Auto-configure additional exporter based on OTEL_METRICS_EXPORTER environment variable
        # Skip OTLP/Console exporters if skip_otlp is True
        if not skip_otlp:
            exporter_type = os.getenv("OTEL_METRICS_EXPORTER", "otlp").lower()

            if exporter_type == "console":

                # Get export interval from environment or use default (60 seconds)
                export_interval_ms = int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "60000"))

                metric_reader = PeriodicExportingMetricReader(
                    ConsoleMetricExporter(), export_interval_millis=export_interval_ms
                )
                metric_readers.append(metric_reader)
                logger.info(
                    f"OTEL metrics initialized with console exporter "
                    f"(export interval: {export_interval_ms}ms)"
                )

            elif exporter_type == "otlp":

                # OTLPMetricExporter automatically reads OTEL_EXPORTER_OTLP_ENDPOINT and
                # OTEL_EXPORTER_OTLP_METRICS_ENDPOINT from environment
                exporter = OTLPMetricExporter()

                # Get export interval from environment or use default (60 seconds)
                export_interval_ms = int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "60000"))

                metric_reader = PeriodicExportingMetricReader(
                    exporter, export_interval_millis=export_interval_ms
                )
                metric_readers.append(metric_reader)

                # Log the endpoint and interval being used
                endpoint = (
                    os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "")
                    or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
                    or "http://localhost:4318"
                )

                logger.info(
                    f"OTEL metrics initialized with OTLP exporter "
                    f"(endpoint: {endpoint}, export interval: {export_interval_ms}ms)"
                )

            elif exporter_type != "none":
                logger.info(
                    f"Unknown OTEL_METRICS_EXPORTER: {exporter_type}, metrics export disabled"
                )
        else:
            logger.info("OTLP/Console metric exporters skipped (ENABLE_OTEL_MONITORING=false)")

        # Initialize MeterProvider with all configured readers and views
        if metric_readers:
            if views:
                _meter_provider = MeterProvider(
                    resource=resource,
                    metric_readers=metric_readers,
                    views=views,
                )
                logger.info(f"MeterProvider initialized with {len(views)} histogram views")
            else:
                # Initialize without views if they couldn't be configured
                _meter_provider = MeterProvider(
                    resource=resource,
                    metric_readers=metric_readers,
                )
                logger.warning(
                    "MeterProvider initialized without custom histogram views (using defaults)"
                )
            metrics.set_meter_provider(_meter_provider)
        else:
            logger.warning("No metric readers configured")

    except ImportError as e:
        logger.info(f"OpenTelemetry metrics dependencies not available: {e}")
    except Exception as e:
        logger.error(f"Failed to initialize OpenTelemetry metrics: {e}")


def get_tracer():
    """Get the current tracer instance."""
    return _tracer


def get_meter_provider():
    """Get the current meter provider instance."""
    return _meter_provider


def get_prometheus_metrics():
    """Get the Prometheus metrics for OpenTelemetry metrics.

    Returns:
        str: Prometheus metrics in text format, or empty string if Prometheus exporter is not initialized
    """
    if _prometheus_reader is None:
        return "# Prometheus metrics exporter not initialized\n"
    try:
        # PrometheusMetricReader exposes metrics via its collector
        return generate_latest(_prometheus_reader._collector)
    except Exception as e:
        logger.error(f"Failed to generate Prometheus metrics: {e}", exc_info=True)
        return f"# Error generating Prometheus metrics: {e}\n"


def is_tracing_enabled():
    """Check if OTEL is enabled."""
    return _otel_enabled


def create_historical_span(
    span_name: str, start_time: float, end_time: float, attributes: dict, parent_span=None
):
    """Create an OTEL span with explicit start and end times for completed operations.

    Args:
        span_name: Name of the span
        start_time: Start time in seconds (Unix timestamp)
        end_time: End time in seconds (Unix timestamp)
        attributes: Dictionary of attributes to attach to the span
        parent_span: Optional parent span to set as the parent context
    """
    try:

        if not is_tracing_enabled():
            return

        tracer = get_tracer()
        if not tracer:
            return

        # Convert timestamps from seconds to nanoseconds (OTEL requirement)
        start_time_ns = int(start_time * 1_000_000_000)
        end_time_ns = int(end_time * 1_000_000_000)

        # Create span with explicit start time and optional parent
        context = None
        if parent_span is not None:
            context = trace.set_span_in_context(parent_span)

        span = tracer.start_span(span_name, context=context, start_time=start_time_ns)

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
