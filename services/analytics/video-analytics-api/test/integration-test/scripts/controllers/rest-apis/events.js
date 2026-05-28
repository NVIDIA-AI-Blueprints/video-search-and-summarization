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
 * Integration tests for /events (mirrors app/controllers/rest-apis/events.js).
 */

'use strict';

function getTests(c) {
    const qs = c.qs;
    const S = c.SENSOR_ID;
    const P = c.PLACE;
    const F = c.FROM_TS;
    const T = c.TO_TS;

    return [
        { name: 'GET /events/tripwire (sensorId+timestamps)', path: `/events/tripwire?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (Array.isArray(JSON.parse(b).tripwireEvents) ? null : 'missing tripwireEvents array') },
        { name: 'GET /events/tripwire (no sensorId/place) -> 400', path: `/events/tripwire?${qs({ fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /events/roi (sensorId+timestamps)', path: `/events/roi?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true },
        { name: 'GET /events/roi (no sensorId/place) -> 400', path: `/events/roi?${qs({ fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /events/amr (place+timestamps)', path: `/events/amr?${qs({ place: P, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200 },
    ];
}

module.exports = { getTests };
