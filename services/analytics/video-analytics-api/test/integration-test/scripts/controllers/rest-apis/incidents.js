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
 * Integration tests for /incidents (mirrors app/controllers/rest-apis/incidents.js).
 */

'use strict';

function getTests(c) {
    const qs = c.qs;
    const S = c.SENSOR_ID;
    const P = c.PLACE;
    const F = c.FROM_TS;
    const T = c.TO_TS;

    return [
        { name: 'GET /incidents (sensorId+timestamps)', path: `/incidents?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, validate: (b) => (Array.isArray(JSON.parse(b).incidents) ? null : 'missing incidents array') },
        { name: 'GET /incidents (place only)', path: `/incidents?${qs({ place: P })}`, method: 'GET', expectedStatus: 200 },
        { name: 'GET /incidents (no sensorId/place) returns 200', path: `/incidents?${qs({ fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, validate: (b) => (Array.isArray(JSON.parse(b).incidents) ? null : 'missing incidents array') },
        { name: 'GET /incidents/severe (sensorId+timestamps)', path: `/incidents/severe?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true },
        { name: 'GET /incidents/severe (no sensorId/place) -> 400', path: '/incidents/severe', method: 'GET', expectedStatus: 400 },
    ];
}

module.exports = { getTests };
