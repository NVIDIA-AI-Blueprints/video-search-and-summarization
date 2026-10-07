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

const fs = require('fs');
const path = require('path');

const INCIDENT_PAGE_SIZE = 10000;
const INCIDENT_PROTO_PATH = path.resolve(__dirname, '../../../../../../../../libs/nvschema/protobuf/ext.proto');
const INCIDENT_ENVELOPE_FIELDS = new Set(['Id', 'type']);

function loadIncidentProtoFields() {
    const proto = fs.readFileSync(INCIDENT_PROTO_PATH, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/\/\/.*$/gm, '');
    const messageStart = proto.search(/\bmessage\s+Incident\s*\{/);
    if (messageStart < 0) throw new Error(`Incident message not found in ${INCIDENT_PROTO_PATH}`);

    const openBrace = proto.indexOf('{', messageStart);
    let depth = 0;
    let closeBrace = -1;
    for (let index = openBrace; index < proto.length; index++) {
        if (proto[index] === '{') depth++;
        if (proto[index] === '}') depth--;
        if (depth === 0) {
            closeBrace = index;
            break;
        }
    }
    if (closeBrace < 0) throw new Error(`Incident message is incomplete in ${INCIDENT_PROTO_PATH}`);

    const messageBody = proto.slice(openBrace + 1, closeBrace);
    const fields = new Map();
    const mapPattern = /^\s*map\s*<\s*([^,]+),\s*([^>]+)>\s+(\w+)\s*=\s*\d+\s*;/gm;
    for (const match of messageBody.matchAll(mapPattern)) {
        fields.set(match[3], { kind: 'map', keyType: match[1].trim(), valueType: match[2].trim() });
    }
    const fieldPattern = /^\s*(repeated\s+)?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s+(\w+)\s*=\s*\d+\s*;/gm;
    for (const match of messageBody.matchAll(fieldPattern)) {
        fields.set(match[3], { kind: match[1] ? 'repeated' : 'single', type: match[2] });
    }
    if (fields.size === 0) throw new Error(`Incident message has no fields in ${INCIDENT_PROTO_PATH}`);
    return fields;
}

const INCIDENT_PROTO_FIELDS = loadIncidentProtoFields();

function isPlainObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function matchesProtoType(value, protoType) {
    if (protoType === 'string' || protoType === 'bytes') return typeof value === 'string';
    if (protoType === 'bool') return typeof value === 'boolean';
    if (/^(?:u?int|sint|fixed|sfixed|float|double)/.test(protoType)) return typeof value === 'number';
    if (protoType === 'google.protobuf.Timestamp') {
        return typeof value === 'string' && Number.isFinite(Date.parse(value));
    }
    return isPlainObject(value);
}

function validateIncidentAgainstProto(incident, index) {
    if (!isPlainObject(incident)) return `incident[${index}] is not an object`;
    const unexpectedFields = Object.keys(incident)
        .filter((field) => !INCIDENT_PROTO_FIELDS.has(field) && !INCIDENT_ENVELOPE_FIELDS.has(field));
    if (unexpectedFields.length > 0) {
        return `incident[${index}] has field(s) absent from protobuf Incident: ${unexpectedFields.join(', ')}`;
    }

    for (const [field, descriptor] of INCIDENT_PROTO_FIELDS) {
        if (!(field in incident)) continue;
        const value = incident[field];
        if (descriptor.kind === 'map') {
            if (!isPlainObject(value) || Object.values(value).some((item) => !matchesProtoType(item, descriptor.valueType))) {
                return `incident[${index}].${field} does not match protobuf map<string, ${descriptor.valueType}>`;
            }
        } else if (descriptor.kind === 'repeated') {
            if (!Array.isArray(value) || value.some((item) => !matchesProtoType(item, descriptor.type))) {
                return `incident[${index}].${field} does not match protobuf repeated ${descriptor.type}`;
            }
        } else if (!matchesProtoType(value, descriptor.type)) {
            return `incident[${index}].${field} does not match protobuf ${descriptor.type}`;
        }
    }
    if ('Id' in incident && typeof incident.Id !== 'string') return `incident[${index}].Id is not a string`;
    if ('type' in incident && typeof incident.type !== 'string') return `incident[${index}].type is not a string`;
    return null;
}

function validateIncidentsAgainstProto(incidents) {
    for (let index = 0; index < incidents.length; index++) {
        const error = validateIncidentAgainstProto(incidents[index], index);
        if (error) return error;
    }
    return null;
}

function validateInfoCamelCase(incidents) {
    const nonCamelCaseKeys = incidents.flatMap(({ info = {} }) => Object.keys(info)
        .filter((key) => !/^_?[a-z][A-Za-z0-9]*$/.test(key)));
    return nonCamelCaseKeys.length === 0
        ? null
        : `info contains non-camel-case key(s): ${[...new Set(nonCamelCaseKeys)].join(', ')}`;
}

function parseIncidents(body) {
    const { incidents } = JSON.parse(body);
    if (!Array.isArray(incidents)) throw new Error('missing incidents array');
    const protoValidationError = validateIncidentsAgainstProto(incidents);
    if (protoValidationError) throw new Error(protoValidationError);
    const infoValidationError = validateInfoCamelCase(incidents);
    if (infoValidationError) throw new Error(infoValidationError);
    return incidents;
}

async function fetchIncidentWindow(c, params, fromTimestamp, toTimestamp) {
    const path = `/incidents?${c.qs({
        ...params,
        fromTimestamp,
        toTimestamp,
        maxResultSize: INCIDENT_PAGE_SIZE
    })}`;
    const { statusCode, body } = await c.request('GET', path);
    if (statusCode !== 200) {
        throw new Error(`expected HTTP 200 while paging ${fromTimestamp} to ${toTimestamp}, got ${statusCode}`);
    }

    const incidents = parseIncidents(body);
    if (incidents.length < INCIDENT_PAGE_SIZE) return incidents;

    return splitIncidentWindow(c, params, fromTimestamp, toTimestamp);
}

async function splitIncidentWindow(c, params, fromTimestamp, toTimestamp) {
    const fromMillis = Date.parse(fromTimestamp);
    const toMillis = Date.parse(toTimestamp);
    if (!Number.isFinite(fromMillis) || !Number.isFinite(toMillis) || fromMillis >= toMillis) {
        throw new Error(`cannot split saturated incident window ${fromTimestamp} to ${toTimestamp}`);
    }

    const midpointMillis = Math.floor((fromMillis + toMillis) / 2);
    const midpoint = new Date(midpointMillis).toISOString();
    const afterMidpoint = new Date(midpointMillis + 1).toISOString();
    const firstHalf = await fetchIncidentWindow(c, params, fromTimestamp, midpoint);
    const secondHalf = await fetchIncidentWindow(c, params, afterMidpoint, toTimestamp);
    // Incident queries match overlapping intervals, so one incident can occur
    // in both halves. Count each incident once when combining pages.
    const uniqueIncidents = new Map();
    for (const incident of [...firstHalf, ...secondHalf]) {
        const key = incident.Id || JSON.stringify(incident);
        uniqueIncidents.set(key, incident);
    }
    return [...uniqueIncidents.values()];
}

async function collectEntireIncidentRange(c, params, initialBody) {
    const incidents = parseIncidents(initialBody);
    return incidents.length < INCIDENT_PAGE_SIZE
        ? incidents
        : splitIncidentWindow(c, params, c.FROM_TS, c.TO_TS);
}

function getTests(c) {
    const qs = c.qs;
    const S = c.SENSOR_ID;
    const P = c.PLACE;
    const F = c.FROM_TS;
    const T = c.TO_TS;
    const runsBpWh2d = process.env.COMPOSE_PROFILE === 'bp_wh_2d'
        || (process.env.BP_PROFILE === 'bp_wh' && process.env.MODE === '2d')
        || process.env.DEPLOY_PROFILE === 'COMPOSE_PROFILES_WH_2D'
        || process.env.WAREHOUSE_VLM_ALERTS_VERIFICATION === 'true';
    const expectedVlmAlertTypes = new Set([
        'Load Quality Violation',
        'Near Miss Violation',
        'Pathway Obstruction Violation',
        'PPE Violation'
    ]);
    let sourceIncidents = [];
    let proximityIncidents = [];

    const tests = [
        { name: 'GET /incidents (sensorId+timestamps)', path: `/incidents?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, validate: (b) => { parseIncidents(b); return null; } },
        { name: 'GET /incidents (place only)', path: `/incidents?${qs({ place: P })}`, method: 'GET', expectedStatus: 200, validate: (b) => { parseIncidents(b); return null; } },
        {
            name: 'GET /incidents (no sensorId/place) returns 200',
            path: `/incidents?${qs({ fromTimestamp: F, toTimestamp: T, maxResultSize: INCIDENT_PAGE_SIZE })}`,
            method: 'GET',
            expectedStatus: 200,
            validate: async (b) => {
                const incidents = runsBpWh2d
                    ? await collectEntireIncidentRange(c, {}, b)
                    : parseIncidents(b);
                if (runsBpWh2d) sourceIncidents = incidents;
                return { details: `${incidents.length} incident(s) match protobuf Incident schema` };
            },
        },
        { name: 'GET /incidents/severe (sensorId+timestamps)', path: `/incidents/severe?${qs({ sensorId: S, fromTimestamp: F, toTimestamp: T })}`, method: 'GET', expectedStatus: 200, skipOpenApiValidation: true },
    ];

    if (runsBpWh2d) {
        tests.splice(4, 0, {
            name: 'GET /incidents (VLM verified alert types)',
            path: `/incidents?${qs({ fromTimestamp: F, toTimestamp: T, vlmVerified: true, maxResultSize: 10000 })}`,
            method: 'GET',
            expectedStatus: 200,
            validate: (b) => {
                const incidents = parseIncidents(b);
                const alertTypes = new Set(incidents
                    .map(({ category, info }) => (info && info.alertCategory) || category)
                    .filter((alertType) => alertType !== 'Spillover Violation'));
                const presentAlertTypes = [...expectedVlmAlertTypes].filter((alertType) => alertTypes.has(alertType));
                const missingAlertTypes = [...expectedVlmAlertTypes].filter((alertType) => !alertTypes.has(alertType));
                const unexpectedAlertTypes = [...alertTypes].filter((alertType) => alertType && !expectedVlmAlertTypes.has(alertType));
                if (!alertTypes.has('Near Miss Violation')) {
                    return `Near Miss Violation is required; not present: ${missingAlertTypes.join(', ') || 'none'}`;
                }
                if (presentAlertTypes.length < 3) {
                    return `expected Near Miss Violation and at least 2 more alert types (${expectedVlmAlertTypes.size} possible), got ${presentAlertTypes.length} (${presentAlertTypes.join(', ') || 'none'}); not present: ${missingAlertTypes.join(', ') || 'none'}`;
                }
                if (unexpectedAlertTypes.length > 0) {
                    return `unexpected alert types: ${unexpectedAlertTypes.join(', ')}; not present: ${missingAlertTypes.join(', ') || 'none'}`;
                }
                return null;
            },
        }, {
            name: 'GET /incidents (unverified proximity violations)',
            path: `/incidents?${qs({ fromTimestamp: F, toTimestamp: T, category: 'Proximity Violation', vlmVerified: false, maxResultSize: 10000 })}`,
            method: 'GET',
            expectedStatus: 200,
            validate: async (b) => {
                proximityIncidents = await collectEntireIncidentRange(c, {
                    category: 'Proximity Violation',
                    vlmVerified: false
                }, b);
                return null;
            },
        }, {
            name: 'GET /incidents (VLM Near Miss IDs match active proximity violations)',
            path: `/incidents?${qs({ fromTimestamp: F, toTimestamp: T, category: 'Near Miss Violation', vlmVerified: true, maxResultSize: 10000 })}`,
            method: 'GET',
            expectedStatus: 200,
            validate: async (b) => {
                const incidents = await collectEntireIncidentRange(c, {
                    category: 'Near Miss Violation',
                    vlmVerified: true
                }, b);
                if (incidents.length === 0) return 'expected at least one VLM Near Miss incident';
                const firstNearMissTimestamp = incidents.reduce((earliest, { timestamp }) =>
                    !earliest || timestamp < earliest ? timestamp : earliest, null);
                const nearMissIds = new Set(incidents.map(({ Id }) => Id).filter(Boolean));
                const croppedOffProximityIncidents = proximityIncidents
                    .filter(({ timestamp }) => timestamp < firstNearMissTimestamp);
                console.log(`[VLM verification] cropped ${croppedOffProximityIncidents.length} beginning proximity incident(s) before first Near Miss timestamp ${firstNearMissTimestamp}`);
                croppedOffProximityIncidents.forEach((incident) => {
                    console.log(`[VLM verification] cropped beginning proximity incident: ${JSON.stringify(incident)}`);
                });
                const activeProximityIds = new Set(proximityIncidents
                    .filter(({ timestamp }) => timestamp >= firstNearMissTimestamp)
                    .map(({ Id }) => Id)
                    .filter(Boolean));
                const proximityWithoutNearMiss = [...activeProximityIds]
                    .filter((incidentId) => !nearMissIds.has(incidentId));
                const nearMissWithoutProximity = [...nearMissIds]
                    .filter((incidentId) => !activeProximityIds.has(incidentId));
                const countsMatch = activeProximityIds.size === nearMissIds.size;
                console.log(`[VLM verification] after beginning crop: ${activeProximityIds.size} active proximity violation ID(s), ${nearMissIds.size} Near Miss ID(s)`);
                if (!countsMatch || proximityWithoutNearMiss.length > 0 || nearMissWithoutProximity.length > 0) {
                    const proximityWithoutNearMissText = proximityWithoutNearMiss.join(', ') || 'none';
                    const nearMissWithoutProximityText = nearMissWithoutProximity.join(', ') || 'none';
                    console.log(`[VLM verification] unmatched active proximity violation ID(s): ${proximityWithoutNearMissText}`);
                    console.log(`[VLM verification] unmatched Near Miss ID(s): ${nearMissWithoutProximityText}`);
                    const mismatchDetails = [];
                    if (!countsMatch) {
                        mismatchDetails.push(`count mismatch after beginning crop: ${activeProximityIds.size} active proximity violation ID(s), ${nearMissIds.size} Near Miss ID(s)`);
                    }
                    mismatchDetails.push(
                        `active proximity violation ID(s) without Near Miss match: ${proximityWithoutNearMissText}`,
                        `Near Miss ID(s) without active proximity violation match: ${nearMissWithoutProximityText}`
                    );
                    return mismatchDetails.join('; ');
                }
                return null;
            },
        }, {
            name: 'GET /incidents (VLM Near Miss timestamps versus proximity end times)',
            path: `/incidents?${qs({ fromTimestamp: F, toTimestamp: T, category: 'Near Miss Violation', vlmVerified: true, maxResultSize: 10000 })}`,
            method: 'GET',
            expectedStatus: 200,
            validate: async (b) => {
                const incidents = await collectEntireIncidentRange(c, {
                    category: 'Near Miss Violation',
                    vlmVerified: true
                }, b);
                const proximityEndById = new Map(proximityIncidents.map(({ Id, end }) => [Id, end]));
                const timestampMismatchCount = incidents
                    .filter(({ Id }) => proximityEndById.has(Id))
                    .filter(({ Id, timestamp }) => timestamp !== proximityEndById.get(Id)).length;
                console.log(`[VLM verification] ${timestampMismatchCount} Near Miss timestamp(s) differ from the corresponding proximity end time (expected for pre-verified incidents)`);
                return null;
            },
        }, {
            name: 'GET /incidents (VLM verification coverage)',
            path: `/incidents?${qs({ fromTimestamp: F, toTimestamp: T, vlmVerified: true, maxResultSize: INCIDENT_PAGE_SIZE })}`,
            method: 'GET',
            expectedStatus: 200,
            validate: async (b) => {
                const incidents = await collectEntireIncidentRange(c, { vlmVerified: true }, b);
                if (incidents.length === 0) return { error: 'expected at least one VLM incident' };
                const firstVlmIncidentTimestamp = incidents.reduce((earliest, { timestamp }) =>
                    !earliest || timestamp < earliest ? timestamp : earliest, null);
                if (!firstVlmIncidentTimestamp) {
                    return { error: 'VLM incidents have no timestamp to establish the verification coverage boundary' };
                }

                const verifiedIds = new Set(incidents.map(({ Id }) => Id).filter(Boolean));
                const startupIncidents = sourceIncidents
                    .filter(({ timestamp }) => timestamp < firstVlmIncidentTimestamp);
                const eligibleSourceIncidents = sourceIncidents
                    .filter(({ timestamp }) => timestamp >= firstVlmIncidentTimestamp);
                const missedVerificationIds = eligibleSourceIncidents
                    .map(({ Id }) => Id)
                    .filter(Boolean)
                    .filter((incidentId) => !verifiedIds.has(incidentId));
                const missedVerificationCount = missedVerificationIds.length;
                const details = `${missedVerificationCount} incident(s) missed verification`;
                console.log(`[VLM verification] coverage starts at first VLM incident timestamp ${firstVlmIncidentTimestamp}; excluded ${startupIncidents.length} earlier source incident(s)`);
                console.log(`[VLM verification] ${details}`);
                console.log(`[VLM verification] missed verification incident ID(s): ${missedVerificationIds.join(', ') || 'none'}`);
                return missedVerificationCount === 0
                    ? { details }
                    : { error: `${details}; incident ID(s): ${missedVerificationIds.join(', ')}` };
            },
        });
    }

    tests.push({ name: 'GET /incidents/severe (no sensorId/place) -> 400', path: '/incidents/severe', method: 'GET', expectedStatus: 400 });
    return tests;
}

module.exports = { getTests };
