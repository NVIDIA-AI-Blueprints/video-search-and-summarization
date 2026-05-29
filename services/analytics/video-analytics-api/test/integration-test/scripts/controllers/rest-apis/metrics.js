/*
 * SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/*
 * Integration tests for /metrics (mirrors app/controllers/rest-apis/metrics.js).
 */

'use strict';

function getTests(c) {
    const qs = c.qs;
    const S = c.SENSOR_ID;
    const P = c.PLACE;
    const F = c.FROM_TS;
    const T = c.TO_TS;

    return [
        { name: 'GET /metrics/last-processed-timestamp (sensorId)', path: `/metrics/last-processed-timestamp?${qs({ sensorId: S })}`, method: 'GET', expectedStatus: 200 },
        { name: 'GET /metrics/last-processed-timestamp (no sensorId) -> 400', path: '/metrics/last-processed-timestamp', method: 'GET', expectedStatus: 400 },
        { name: 'GET /metrics/tripwire/counts (sensorId+timestamps)', path: `/metrics/tripwire/counts?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200 },
        { name: 'GET /metrics/average-speed (place+timestamps)', path: `/metrics/average-speed?${qs({ place: P, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true },
    ];
}

module.exports = { getTests };
