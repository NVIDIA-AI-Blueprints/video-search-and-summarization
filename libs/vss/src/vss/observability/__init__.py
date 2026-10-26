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

"""Opt-in performance observations; no exporter or native imports on import."""

from .context import collect_vss_metrics, measure, metric_scope, record
from .contracts import METRIC_CATALOG, Observation
from .otel import OpenTelemetrySink

__all__ = [
    "METRIC_CATALOG",
    "Observation",
    "OpenTelemetrySink",
    "collect_vss_metrics",
    "measure",
    "metric_scope",
    "record",
]
