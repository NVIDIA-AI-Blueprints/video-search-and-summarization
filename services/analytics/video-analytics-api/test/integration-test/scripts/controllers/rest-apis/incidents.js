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
    const runsBpWh2d = process.env.COMPOSE_PROFILE === 'bp_wh_2d'
        || (process.env.BP_PROFILE === 'bp_wh' && process.env.MODE === '2d')
        || process.env.DEPLOY_PROFILE === 'COMPOSE_PROFILES_WH_2D';
    const expectedVlmAlertTypes = new Set([
        'Load Quality Violation',
        'Near Miss Violation',
        'Pathway Obstruction Violation',
        'PPE Violation'
    ]);

    const tests = [
        { name: 'GET /incidents (sensorId+timestamps)', path: `/incidents?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, validate: (b) => (Array.isArray(JSON.parse(b).incidents) ? null : 'missing incidents array') },
        { name: 'GET /incidents (place only)', path: `/incidents?${qs({ place: P })}`, method: 'GET', expectedStatus: 200 },
        { name: 'GET /incidents (no sensorId/place) returns 200', path: `/incidents?${qs({ fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, validate: (b) => (Array.isArray(JSON.parse(b).incidents) ? null : 'missing incidents array') },
        { name: 'GET /incidents/severe (sensorId+timestamps)', path: `/incidents/severe?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true },
        { name: 'GET /incidents/severe (no sensorId/place) -> 400', path: '/incidents/severe', method: 'GET', expectedStatus: 400 },
    ];

    if (runsBpWh2d) {
        tests.splice(4, 0, {
            name: 'GET /incidents (VLM verified alert types)',
            path: `/incidents?${qs({ vlmVerified: true, maxResultSize: 10000 })}`,
            method: 'GET',
            expectedStatus: 200,
            validate: (b) => {
                const { incidents } = JSON.parse(b);
                if (!Array.isArray(incidents)) return 'missing incidents array';
                const alertTypes = new Set(incidents.map(({ category, info }) => (info && info.alertCategory) || category));
                const presentAlertTypes = [...expectedVlmAlertTypes].filter((alertType) => alertTypes.has(alertType));
                const missingAlertTypes = [...expectedVlmAlertTypes].filter((alertType) => !alertTypes.has(alertType));
                const unexpectedAlertTypes = [...alertTypes].filter((alertType) => alertType && !expectedVlmAlertTypes.has(alertType));
                if (!alertTypes.has('Near Miss Violation')) {
                    return `Near Miss Violation is required; not present: ${missingAlertTypes.join(', ') || 'none'}`;
                }
                if (presentAlertTypes.length < 3) {
                    return `expected Near Miss Violation and at least 2 more real time alert types (${expectedVlmAlertTypes.size} possible), got ${presentAlertTypes.length} (${presentAlertTypes.join(', ') || 'none'}); not present: ${missingAlertTypes.join(', ') || 'none'}`;
                }
                if (unexpectedAlertTypes.length > 0) {
                    return `unexpected alert types: ${unexpectedAlertTypes.join(', ')}; not present: ${missingAlertTypes.join(', ') || 'none'}`;
                }
                return {
                    details: missingAlertTypes.length === 0
                        ? `all ${presentAlertTypes.join(', ')} types found`
                        : `${presentAlertTypes.join(', ')} types found; ${missingAlertTypes.join(', ')} not found`
                };
            },
        });
    }

    return tests;
}

module.exports = { getTests };
