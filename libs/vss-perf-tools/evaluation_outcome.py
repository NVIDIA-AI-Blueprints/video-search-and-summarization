# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recognize completed latency evaluations without weakening native KPI checks."""
from collections.abc import Mapping
import re

_NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
_LATENCY_LIMIT = re.compile(r'[\w.-]+\.(?:avg_s|p50_s|p95_s|p99_s|max_s) ' + _NUMBER + r' > ' + _NUMBER)


def latency_kpi_failure(payload, measurement_key, return_code=1):
    """Only measured latency-limit breaches are nonfatal; missing/error data is fatal.

    The native runner still decides thresholds and test results. This function
    changes only the canonical entrypoint's process exit status after publication.
    """
    if return_code not in (0, 1) or payload.get('success') is not False or payload.get('failure_stage'):
        return False
    measurements = payload.get(measurement_key)
    kpis = payload.get('kpis')
    if not isinstance(measurements, Mapping) or not measurements or not isinstance(kpis, Mapping):
        return False
    violations = kpis.get('violations')
    if kpis.get('passed') is not False or not isinstance(violations, list) or not violations:
        return False
    if not all(isinstance(item, str) and _LATENCY_LIMIT.fullmatch(item) for item in violations):
        return False
    if payload.get('error') != 'KPI violations: ' + '; '.join(violations):
        return False
    for measured in measurements.values():
        if not isinstance(measured, Mapping) or measured.get('present') is not True:
            return False
        values = measured.get('corrected', measured)
        if not isinstance(values, Mapping) or not isinstance(values.get('count'), (int, float)) or values['count'] <= 0:
            return False
    if measurement_key == 'topics' and (payload.get('raw') or {}).get('count', 0) <= 0:
        return False
    return True
