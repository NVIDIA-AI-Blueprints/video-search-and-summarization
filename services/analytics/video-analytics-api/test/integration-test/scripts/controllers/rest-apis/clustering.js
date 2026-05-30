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
 * Integration tests for /clustering (mirrors app/controllers/rest-apis/clustering.js).
 */

'use strict';

function getTests(c) {
    const qs = c.qs;
    const S = c.SENSOR_ID;
    const F = c.FROM_TS;
    const T = c.TO_TS;

    return [
        { name: 'GET /clustering/behavior (sensorId+timestamps)', path: `/clustering/behavior?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, validate: (b) => (Array.isArray(JSON.parse(b).clusters) ? null : 'missing clusters array') },
        { name: 'GET /clustering/behavior (missing sensorId) -> 400', path: `/clustering/behavior?${qs({ fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /clustering/behavior (missing timestamps) -> 400', path: `/clustering/behavior?${qs({ sensorId: S })}`, method: 'GET', expectedStatus: 400 },
        { name: 'POST /clustering/add-label (valid body)', path: '/clustering/add-label', method: 'POST', expectedStatus: 201, body: { sensorId: S, modelVersion: 'directionBasedModel', clusterIndex: '0', label: 'integration-test-label' }, validate: (b) => { try { JSON.parse(b); return null; } catch { return 'invalid JSON'; } } },
        { name: 'POST /clustering/add-label (missing required) -> 400', path: '/clustering/add-label', method: 'POST', expectedStatus: 400, body: { sensorId: S } },
    ];
}

module.exports = { getTests };
