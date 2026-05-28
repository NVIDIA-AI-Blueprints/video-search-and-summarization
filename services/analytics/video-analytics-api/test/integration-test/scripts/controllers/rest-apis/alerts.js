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
 * Integration tests for /alerts (mirrors app/controllers/rest-apis/alerts.js).
 */

'use strict';

function getTests(c) {
    const qs = c.qs;
    const S = c.SENSOR_ID;
    const P = c.PLACE;
    const F = c.FROM_TS;
    const T = c.TO_TS;

    return [
        { name: 'GET /alerts (sensorId+timestamps)', path: `/alerts?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, validate: (b) => (Array.isArray(JSON.parse(b).alerts) ? null : 'missing alerts array') },
        { name: 'GET /alerts (place only)', path: `/alerts?${qs({ place: P })}`, method: 'GET', expectedStatus: 200 },
        { name: 'GET /alerts (no sensorId/place) returns 200 with empty', path: `/alerts?${qs({ fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, validate: (b) => (Array.isArray(JSON.parse(b).alerts) ? null : 'missing alerts array') },
        { name: 'GET /alerts/severe (sensorId+timestamps)', path: `/alerts/severe?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200 },
        { name: 'GET /alerts/severe (no sensorId/place) -> 400', path: '/alerts/severe', method: 'GET', expectedStatus: 400 },
    ];
}

module.exports = { getTests };
