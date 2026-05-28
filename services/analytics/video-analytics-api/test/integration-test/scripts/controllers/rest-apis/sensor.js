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
 * Integration tests for /sensor (mirrors app/controllers/rest-apis/sensor.js).
 */

'use strict';

function getTests(c) {
    const qs = c.qs;
    const P = c.PLACE;

    return [
        { name: 'GET /sensor/lookup (place+x,y,z)', path: `/sensor/lookup?${qs({ place: P, x: '1', y: '-10', z: '0' })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (JSON.parse(b).sensorIds !== undefined ? null : 'missing sensorIds') },
        { name: 'GET /sensor/lookup (missing place) -> 400', path: `/sensor/lookup?${qs({ x: '1', y: '-10', z: '0' })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /sensor/lookup (missing x,y,z) -> 400', path: `/sensor/lookup?${qs({ place: P })}`, method: 'GET', expectedStatus: 400 },
    ];
}

module.exports = { getTests };
