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
 * Integration tests for /frames (mirrors app/controllers/rest-apis/frames.js).
 */

'use strict';

function getTests(c) {
    const qs = c.qs;
    const S = c.SENSOR_ID;
    const F = c.FROM_TS;
    const T = c.TO_TS;

    return [
        { name: 'GET /frames (sensorId+frameId)', path: `/frames?${qs({ sensorId: S, frameId: '0' })}`, method: 'GET', expectedStatus: 200, validate: (b) => (Array.isArray(JSON.parse(b).frames) ? null : 'missing frames array') },
        { name: 'GET /frames (sensorId+timestamps)', path: `/frames?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200 },
        { name: 'GET /frames (no sensorId) -> 400', path: `/frames?${qs({ fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /frames/pts (sensorId+frameId)', path: `/frames/pts?${qs({ sensorId: S, frameId: '0' })}`, method: 'GET', expectedStatuses: [200, 422], validate: (b) => { const j = JSON.parse(b); if (j.error && j.error.includes('nvstreamer')) return null; return j.pts !== undefined ? null : 'missing pts'; } },
        { name: 'GET /frames/pts (no sensorId) -> 400', path: `/frames/pts?${qs({ frameId: '0' })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /frames/pts (no frameId/timestamp) -> 400', path: `/frames/pts?${qs({ sensorId: S })}`, method: 'GET', expectedStatus: 400 },
    ];
}

module.exports = { getTests };
