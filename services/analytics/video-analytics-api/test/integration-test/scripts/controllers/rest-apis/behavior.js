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
 * Integration tests for /behavior (mirrors app/controllers/rest-apis/behavior.js).
 */

'use strict';

function getTests(c) {
    const qs = c.qs;
    const S = c.SENSOR_ID;
    const S_ALT = c.SENSOR_ID_ALT;
    const P = c.PLACE;
    const OID = c.OBJECT_ID;
    const OT = c.OBJECT_TYPE;
    const F = c.FROM_TS;
    const T = c.TO_TS;

    return [
        { name: 'GET /behavior?sensorId only', path: `/behavior?${qs({ sensorId: S })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (Array.isArray(JSON.parse(b).behaviors) ? null : 'response missing behaviors array') },
        { name: 'GET /behavior?sensorId+fromTimestamp+toTimestamp', path: `/behavior?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => { const j = JSON.parse(b); if (!Array.isArray(j.behaviors)) return 'missing behaviors'; if (j.behaviors.length === 0) return 'expected at least one behavior'; return null; } },
        { name: 'GET /behavior?sensorId+timestamps+objectId', path: `/behavior?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T, objectId: OID })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => { const j = JSON.parse(b); if (!Array.isArray(j.behaviors)) return 'missing behaviors'; if (j.behaviors.length > 1) return 'objectId should return at most 1'; return null; } },
        { name: 'GET /behavior?sensorId+timestamps+objectType', path: `/behavior?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T, objectType: OT })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => { const j = JSON.parse(b); if (!Array.isArray(j.behaviors)) return 'missing behaviors'; for (const x of j.behaviors) { if (x.object && x.object.type !== OT) return `expected objectType=${OT}`; } return null; } },
        { name: 'GET /behavior?sensorId+timestamps+queryString', path: `/behavior?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T, queryString: 'object.type:Person' })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (Array.isArray(JSON.parse(b).behaviors) ? null : 'missing behaviors') },
        { name: 'GET /behavior?sensorId+maxResultSize=1', path: `/behavior?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T, maxResultSize: '1' })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => { const j = JSON.parse(b); return (Array.isArray(j.behaviors) && j.behaviors.length <= 1) ? null : 'maxResultSize=1 violated'; } },
        { name: 'GET /behavior?sensorId+maxResultSize=10000', path: `/behavior?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T, maxResultSize: '10000' })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (Array.isArray(JSON.parse(b).behaviors) ? null : 'missing behaviors') },
        { name: 'GET /behavior?place only', path: `/behavior?${qs({ place: P })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (Array.isArray(JSON.parse(b).behaviors) ? null : 'missing behaviors') },
        { name: 'GET /behavior?place+fromTimestamp+toTimestamp', path: `/behavior?${qs({ place: P, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => { const j = JSON.parse(b); if (!Array.isArray(j.behaviors) || j.behaviors.length === 0) return 'expected at least one behavior'; return null; } },
        { name: 'GET /behavior?place+objectType', path: `/behavior?${qs({ place: P, objectType: OT })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (Array.isArray(JSON.parse(b).behaviors) ? null : 'missing behaviors') },
        { name: 'GET /behavior?sensorId+timestamps+objectType+queryString', path: `/behavior?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T, objectType: OT, queryString: 'object.type:Person' })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (Array.isArray(JSON.parse(b).behaviors) ? null : 'missing behaviors') },
        { name: 'GET /behavior?sensorId (nonexistent) returns empty', path: `/behavior?${qs({ sensorId: 'nonexistent-sensor-xyz' })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => { const j = JSON.parse(b); return (Array.isArray(j.behaviors) && j.behaviors.length === 0) ? null : 'expected empty'; } },
        { name: 'GET /behavior?sensorId+timestamps+objectId (nonexistent)', path: `/behavior?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T, objectId: 'nonexistent-object' })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => { const j = JSON.parse(b); return (Array.isArray(j.behaviors) && j.behaviors.length === 0) ? null : 'expected empty'; } },
        { name: 'GET /behavior?place+timestamps+maxResultSize', path: `/behavior?${qs({ place: P, fromTimestamp: F, toTimestamp: T, maxResultSize: '2' })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => { const j = JSON.parse(b); return (Array.isArray(j.behaviors) && j.behaviors.length <= 2) ? null : 'maxResultSize=2 violated'; } },
        { name: 'GET /behavior?sensorId (alt Camera_01)', path: `/behavior?${qs({ sensorId: S_ALT, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => { const j = JSON.parse(b); if (!Array.isArray(j.behaviors) || j.behaviors.length === 0) return 'expected at least one'; return null; } },
        { name: 'GET /behavior?place+queryString', path: `/behavior?${qs({ place: P, queryString: 'object.type:Forklift' })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (Array.isArray(JSON.parse(b).behaviors) ? null : 'missing behaviors') },
        { name: 'GET /behavior (no params) -> 400', path: '/behavior', method: 'GET', expectedStatus: 400 },
        { name: 'GET /behavior?sensorId+place (both) -> 400', path: `/behavior?${qs({ sensorId: S, place: P })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /behavior?place+objectId (API allows place+objectId)', path: `/behavior?${qs({ place: P, objectId: OID, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (Array.isArray(JSON.parse(b).behaviors) ? null : 'missing behaviors') },
        { name: 'GET /behavior?sensorId+objectId without timestamps (API returns 200)', path: `/behavior?${qs({ sensorId: S, objectId: OID })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (Array.isArray(JSON.parse(b).behaviors) ? null : 'missing behaviors') },
        { name: 'GET /behavior?sensorId+objectId+queryString -> 400', path: `/behavior?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T, objectId: OID, queryString: 'x' })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /behavior?sensorId+objectId+maxResultSize -> 400', path: `/behavior?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T, objectId: OID, maxResultSize: '5' })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /behavior?sensorId+fromTimestamp only (API returns 200)', path: `/behavior?${qs({ sensorId: S, fromTimestamp: F })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (Array.isArray(JSON.parse(b).behaviors) ? null : 'missing behaviors') },
        { name: 'GET /behavior?sensorId+toTimestamp only (API returns 200)', path: `/behavior?${qs({ sensorId: S, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true, validate: (b) => (Array.isArray(JSON.parse(b).behaviors) ? null : 'missing behaviors') },
        { name: 'GET /behavior?sensorId+maxResultSize=0 -> 400', path: `/behavior?${qs({ sensorId: S, maxResultSize: '0' })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /behavior?sensorId+maxResultSize=10001 -> 400', path: `/behavior?${qs({ sensorId: S, maxResultSize: '10001' })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /behavior?sensorId+unknownParam -> 400', path: `/behavior?${qs({ sensorId: S, unknownParam: 'value' })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /behavior fromTimestamp > toTimestamp -> 422', path: `/behavior?${qs({ sensorId: S, fromTimestamp: T, toTimestamp: F })}`, method: 'GET', expectedStatus: 422 },
        { name: 'GET /behavior invalid fromTimestamp -> 422', path: `/behavior?${qs({ sensorId: S, fromTimestamp: 'not-a-timestamp', toTimestamp: T })}`, method: 'GET', expectedStatus: 422 },
        { name: 'GET /behavior invalid toTimestamp -> 422', path: `/behavior?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: 'not-a-timestamp' })}`, method: 'GET', expectedStatus: 422 },
        { name: 'GET /behavior/pts (no params) -> 400', path: '/behavior/pts', method: 'GET', expectedStatus: 400 },
        { name: 'GET /behavior/pts missing sensorId -> 400', path: `/behavior/pts?${qs({ endFrameId: '100', behaviorTimeInterval: '2.5' })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /behavior/pts missing endFrameId -> 400', path: `/behavior/pts?${qs({ sensorId: S, behaviorTimeInterval: '2.5' })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /behavior/pts missing behaviorTimeInterval -> 400', path: `/behavior/pts?${qs({ sensorId: S, endFrameId: '100' })}`, method: 'GET', expectedStatus: 400 },
        { name: 'GET /behavior/pts unknown param -> 400', path: `/behavior/pts?${qs({ sensorId: S, endFrameId: '100', behaviorTimeInterval: '2.5', extra: 'value' })}`, method: 'GET', expectedStatus: 400 },
    ];
}

module.exports = { getTests };
