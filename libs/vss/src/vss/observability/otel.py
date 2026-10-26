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

"""Explicit bridge to application-owned OpenTelemetry providers."""

from __future__ import annotations

import threading
from typing import Any

from .contracts import METRIC_CATALOG, Observation


class OpenTelemetrySink:
    """Sanitized event lineage and fixed-cardinality aggregate histograms.

    Construct explicitly and pass as collect_vss_metrics(sink=...). No exporter
    or global provider is installed. Application providers own export behavior.
    """

    def __init__(self, *, tracer_provider: Any = None, meter_provider: Any = None):
        from opentelemetry import metrics, trace

        self._tracer = trace.get_tracer(
            "vss.observability", "1.0", tracer_provider=tracer_provider
        )
        self._meter = metrics.get_meter(
            "vss.observability", "1.0", meter_provider=meter_provider
        )
        self._histograms: dict[str, Any] = {}
        self._lock = threading.Lock()

    def __call__(self, observation: Observation) -> None:
        attributes = observation.model_dump(exclude_none=True)
        with self._tracer.start_as_current_span(
            observation.name, attributes=attributes
        ):
            if observation.value is None:
                return
            with self._lock:
                instrument = self._histograms.get(observation.name)
                if instrument is None:
                    instrument = self._meter.create_histogram(
                        "vss." + observation.name,
                        unit={"seconds": "s", "count": "1", "bytes": "By"}[
                            METRIC_CATALOG[observation.name]
                        ],
                    )
                    self._histograms[observation.name] = instrument
            instrument.record(
                observation.value,
                {
                    "library": observation.library,
                    "status": observation.status,
                    "origin": observation.origin,
                    "schema_version": observation.schema_version,
                },
            )
