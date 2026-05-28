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
 * Integration tests for /tracker (mirrors app/controllers/rest-apis/tracker.js).
 */

'use strict';

function getTests(c) {
    const qs = c.qs;
    const P = c.PLACE;
    const F = c.FROM_TS;
    const T = c.TO_TS;
    const BID = c.BEHAVIOR_ID;

    return [
        { name: 'GET /tracker/unique-object-count (place+timestamp)', path: `/tracker/unique-object-count?${qs({ place: P, timestamp: T, timeWindowInMs: '100' })}`, method: 'GET', expectedStatus: 200, validate: (b) => (JSON.parse(b).uniqueObjectCount !== undefined ? null : 'missing uniqueObjectCount') },
        { name: 'GET /tracker/unique-object-count (no place/sensorIds) returns 200 with 0', path: `/tracker/unique-object-count?${qs({ timestamp: T })}`, method: 'GET', expectedStatus: 200, validate: (b) => (JSON.parse(b).uniqueObjectCount === 0 ? null : 'expected uniqueObjectCount 0') },
        { name: 'GET /tracker/unique-objects (place+timestamps)', path: `/tracker/unique-objects?${qs({ place: P, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, validate: (b) => (Array.isArray(JSON.parse(b).uniqueObjects) ? null : 'missing uniqueObjects array') },
        { name: 'GET /tracker/unique-objects (missing timestamps) -> 400', path: `/tracker/unique-objects?${qs({ place: P })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /tracker/behavior-locations (behaviorIds)', path: `/tracker/behavior-locations?${qs({ fromTimestamp: F, toTimestamp: T, behaviorIds: BID })}`, method: 'GET', expectedStatus: 200, validate: (b) => (Array.isArray(JSON.parse(b).behaviors) ? null : 'missing behaviors array') },
        { name: 'GET /tracker/behavior-locations (missing behaviorIds/globalId) -> 400', path: `/tracker/behavior-locations?${qs({ fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /tracker/last-record (place)', path: `/tracker/last-record?${qs({ place: P })}`, method: 'GET', expectedStatus: 200, validate: (b) => (JSON.parse(b).lastRecord !== undefined ? null : 'missing lastRecord') },
        { name: 'GET /tracker/last-record (no place) -> 400', path: '/tracker/last-record', method: 'GET', expectedStatus: 400 },
    ];
}

module.exports = { getTests };
