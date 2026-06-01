#!/usr/bin/env node

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
 * Integration tests for warehouse_2d_app: mdx-behavior, mdx-frames, mdx-incidents, mdx-events
 * (mdx-raw has no GET API; data lives in Elasticsearch dump only).
 * Time range from dump: 2026-02-14T10:13:51.292Z to 2026-02-14T10:17:33.492Z.
 * - Validates each request input (query params) against OpenAPI parameter schemas (AJV).
 * - Hits the API, validates response against OpenAPI response schema.
 * - Writes fixtures (input.json + output.json) per test under fixtures/warehouse_2d_app/.
 * Usage: node run_warehouse_2d_app_tests.js <BASE_URL> [WEB_APIS_ROOT] [FIXTURES_OUT_DIR]
 */

'use strict';

const path = require('path');
const fs = require('fs');
const http = require('http');
const https = require('https');

const OPENAPI_SPEC_PATH = path.join('src', 'app', 'specification', 'openapi-governance.json');
const SCRIPT_DIR = __dirname;
const TEST_DIR = path.join(SCRIPT_DIR, '..', '..');
const DEFAULT_WEB_APIS_ROOT = path.join(TEST_DIR, '..');
const WAREHOUSE_FIXTURES_SUBDIR = 'warehouse_2d_app';

// Time range from Elasticsearch dump (warehouse_2d_app)
const FROM_TS = '2026-02-14T10:13:51.292Z';
const TO_TS = '2026-02-14T10:17:33.492Z';
const PLACE = 'building=Warehouse/room=Room-1';
const SENSOR_IDS = ['Camera', 'Camera_01', 'Camera_02'];
const OBJECT_ID = '0';
const OBJECT_TYPE_PERSON = 'Person';
const OBJECT_TYPE_PALLET = 'Pallet';
const FRAME_ID = '0';

function qs(params) {
    return Object.entries(params)
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
        .join('&');
}

function loadOpenApi(webApisRoot) {
    const specPath = path.join(webApisRoot, OPENAPI_SPEC_PATH);
    if (!fs.existsSync(specPath)) return null;
    return JSON.parse(fs.readFileSync(specPath, 'utf8'));
}

function findSpecPath(openapi, requestPath) {
    if (openapi.paths[requestPath]) return requestPath;
    for (const specPath of Object.keys(openapi.paths)) {
        const pattern = '^' + specPath.replace(/\{[^}]+\}/g, '[^/]+') + '$';
        if (new RegExp(pattern).test(requestPath)) return specPath;
    }
    return null;
}


/** Build a JSON schema from OpenAPI operation query parameters for AJV input validation. */
function getInputSchemaFromOpenApi(openapi, method, requestPath) {
    const specPath = findSpecPath(openapi, requestPath);
    if (!specPath) return null;
    const op = openapi.paths[specPath] && openapi.paths[specPath][method.toLowerCase()];
    if (!op || !Array.isArray(op.parameters)) return null;
    const properties = {};
    const required = [];
    for (const p of op.parameters) {
        if (p.in !== 'query') continue;
        const name = p.name;
        const schema = p.schema ? { ...p.schema } : { type: 'string' };
        delete schema.description;
        delete schema.example;
        delete schema.examples;
        properties[name] = schema;
        if (p.required) required.push(name);
    }
    if (Object.keys(properties).length === 0) return null;
    return { type: 'object', properties, required };
}

const OPENAPI_METADATA_KEYS = new Set(['example', 'description']);
function resolveSchemaRef(spec, schema, visited) {
    if (!schema || typeof schema !== 'object') return schema;
    if (schema.$ref && typeof schema.$ref === 'string') {
        const ref = schema.$ref;
        if (ref.startsWith('#/components/schemas/')) {
            const name = ref.slice('#/components/schemas/'.length);
            if (visited.has(name)) return schema;
            const resolved = spec.components?.schemas?.[name];
            if (!resolved) return schema;
            visited.add(name);
            const out = resolveSchemaRef(spec, resolved, visited);
            visited.delete(name);
            return out;
        }
    }
    const out = Array.isArray(schema) ? [] : {};
    for (const key of Object.keys(schema)) {
        if (OPENAPI_METADATA_KEYS.has(key)) continue;
        out[key] = resolveSchemaRef(spec, schema[key], visited);
    }
    return out;
}

function stripAdditionalProperties(schema) {
    if (!schema || typeof schema !== 'object') return;
    if (!Array.isArray(schema)) {
        delete schema.additionalProperties;
        for (const k of Object.keys(schema)) stripAdditionalProperties(schema[k]);
    } else {
        schema.forEach((s) => stripAdditionalProperties(s));
    }
}

function stripMinLength(schema) {
    if (!schema || typeof schema !== 'object') return;
    if (!Array.isArray(schema)) {
        delete schema.minLength;
        for (const k of Object.keys(schema)) stripMinLength(schema[k]);
    } else {
        schema.forEach((s) => stripMinLength(s));
    }
}

function getResponseSchema(openapi, method, requestPath, statusCode) {
    const specPath = findSpecPath(openapi, requestPath);
    if (!specPath) return null;
    const op = openapi.paths[specPath][method.toLowerCase()];
    if (!op?.responses) return null;
    const response = op.responses[String(statusCode)] || op.responses.default;
    const schema = response?.content?.['application/json']?.schema;
    if (!schema) return null;
    const resolved = resolveSchemaRef(openapi, schema, new Set());
    stripAdditionalProperties(resolved);
    stripMinLength(resolved);
    return resolved;
}

function loadAjv() {
    const ajvPath = path.join(TEST_DIR, 'node_modules', 'ajv');
    const ajvErrorsPath = path.join(TEST_DIR, 'node_modules', 'ajv-errors');
    if (!fs.existsSync(path.join(ajvPath, 'package.json'))) return null;
    const Ajv2019 = require(path.join(ajvPath, 'dist', '2019'));
    const ajvErrors = require(ajvErrorsPath);
    const ajv = new Ajv2019({ allErrors: true, useDefaults: true, coerceTypes: 'array', strict: false });
    ajvErrors(ajv);
    try {
        require(path.join(TEST_DIR, 'node_modules', 'ajv-formats'))(ajv);
    } catch (_) {}
    if (!ajv.formats.double) ajv.addFormat('double', (v) => typeof v === 'number' && !Number.isNaN(v));
    return ajv;
}

function request(baseUrl, method, pathname) {
    return new Promise((resolve, reject) => {
        const url = new URL(pathname, baseUrl);
        const client = url.protocol === 'https:' ? https : http;
        const opts = {
            hostname: url.hostname,
            port: url.port || (url.protocol === 'https:' ? 443 : 80),
            path: url.pathname + url.search,
            method,
        };
        const req = client.request(opts, (res) => {
            let data = '';
            res.on('data', (chunk) => { data += chunk; });
            res.on('end', () => resolve({ statusCode: res.statusCode, body: data }));
        });
        req.on('error', reject);
        req.setTimeout(15000, () => { req.destroy(); reject(new Error('Request timeout')); });
        req.end();
    });
}

/**
 * Test case: { path, method, queryParams, name, slug }.
 * slug used for fixture subdir (safe filename).
 */
function getWarehouse2dAppTestCases() {
    const cases = [];
    const add = (pathName, queryParams, name, slug) => {
        cases.push({ path: pathName, method: 'GET', queryParams: queryParams || {}, name, slug });
    };

    // --- mdx-behavior (GET /behavior) ---
    for (const sensorId of SENSOR_IDS) {
        add(`/behavior?${qs({ sensorId })}`, { sensorId }, `GET /behavior sensorId=${sensorId}`, `behavior_sensorId_${sensorId}`);
    }
    add(`/behavior?${qs({ place: PLACE })}`, { place: PLACE }, 'GET /behavior place only', 'behavior_place_only');
    for (const sensorId of SENSOR_IDS) {
        const q = { sensorId, fromTimestamp: FROM_TS, toTimestamp: TO_TS };
        add(`/behavior?${qs(q)}`, q, `GET /behavior sensorId=${sensorId}+timestamps`, `behavior_sensorId_${sensorId}_timestamps`);
    }
    add(
        `/behavior?${qs({ place: PLACE, fromTimestamp: FROM_TS, toTimestamp: TO_TS })}`,
        { place: PLACE, fromTimestamp: FROM_TS, toTimestamp: TO_TS },
        'GET /behavior place+timestamps',
        'behavior_place_timestamps'
    );
    const qBehObj = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS, objectId: OBJECT_ID };
    add(`/behavior?${qs(qBehObj)}`, qBehObj, 'GET /behavior sensorId+timestamps+objectId', 'behavior_sensorId_timestamps_objectId');
    const qBehType = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS, objectType: OBJECT_TYPE_PERSON };
    add(`/behavior?${qs(qBehType)}`, qBehType, 'GET /behavior sensorId+timestamps+objectType', 'behavior_sensorId_timestamps_objectType');
    const qBehQuery = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS, queryString: 'object.type:Person' };
    add(`/behavior?${qs(qBehQuery)}`, qBehQuery, 'GET /behavior sensorId+timestamps+queryString', 'behavior_sensorId_timestamps_queryString');
    const qBehMax = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS, maxResultSize: 5 };
    add(`/behavior?${qs(qBehMax)}`, qBehMax, 'GET /behavior sensorId+timestamps+maxResultSize', 'behavior_sensorId_timestamps_maxResultSize');
    add(
        `/behavior?${qs({ place: PLACE, objectType: OBJECT_TYPE_PALLET })}`,
        { place: PLACE, objectType: OBJECT_TYPE_PALLET },
        'GET /behavior place+objectType',
        'behavior_place_objectType'
    );

    // --- mdx-frames (GET /frames) ---
    for (const sensorId of SENSOR_IDS) {
        const qFrameId = { sensorId, frameId: FRAME_ID };
        add(`/frames?${qs(qFrameId)}`, qFrameId, `GET /frames sensorId=${sensorId}+frameId`, `frames_sensorId_${sensorId}_frameId`);
    }
    const qFramesTs = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/frames?${qs(qFramesTs)}`, qFramesTs, 'GET /frames sensorId+timestamps', 'frames_sensorId_timestamps');
    const qFramesMax = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS, maxResultSize: 10 };
    add(`/frames?${qs(qFramesMax)}`, qFramesMax, 'GET /frames sensorId+timestamps+maxResultSize', 'frames_sensorId_timestamps_maxResultSize');

    // --- mdx-incidents (GET /incidents) ---
    for (const sensorId of SENSOR_IDS) {
        const q = { sensorId, fromTimestamp: FROM_TS, toTimestamp: TO_TS };
        add(`/incidents?${qs(q)}`, q, `GET /incidents sensorId=${sensorId}+timestamps`, `incidents_sensorId_${sensorId}_timestamps`);
    }
    add(`/incidents?${qs({ place: PLACE })}`, { place: PLACE }, 'GET /incidents place only', 'incidents_place_only');
    add(
        `/incidents?${qs({ place: PLACE, fromTimestamp: FROM_TS, toTimestamp: TO_TS })}`,
        { place: PLACE, fromTimestamp: FROM_TS, toTimestamp: TO_TS },
        'GET /incidents place+timestamps',
        'incidents_place_timestamps'
    );
    const qIncMax = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS, maxResultSize: 10 };
    add(`/incidents?${qs(qIncMax)}`, qIncMax, 'GET /incidents sensorId+timestamps+maxResultSize', 'incidents_sensorId_timestamps_maxResultSize');

    // --- mdx-events: tripwire (GET /events/tripwire) ---
    const qTripwireSensor = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/events/tripwire?${qs(qTripwireSensor)}`, qTripwireSensor, 'GET /events/tripwire sensorId+timestamps', 'events_tripwire_sensorId_timestamps');
    const qTripwirePlace = { place: PLACE, fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/events/tripwire?${qs(qTripwirePlace)}`, qTripwirePlace, 'GET /events/tripwire place+timestamps', 'events_tripwire_place_timestamps');

    // --- mdx-events: roi (GET /events/roi) ---
    const qRoiSensor = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/events/roi?${qs(qRoiSensor)}`, qRoiSensor, 'GET /events/roi sensorId+timestamps', 'events_roi_sensorId_timestamps');
    const qRoiPlace = { place: PLACE, fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/events/roi?${qs(qRoiPlace)}`, qRoiPlace, 'GET /events/roi place+timestamps', 'events_roi_place_timestamps');

    // --- mdx-frames: alerts (GET /frames/alerts) ---
    const qFramesAlerts = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/frames/alerts?${qs(qFramesAlerts)}`, qFramesAlerts, 'GET /frames/alerts sensorId+timestamps', 'frames_alerts_sensorId_timestamps');
    const qFramesAlertsBlank = { fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/frames/alerts?${qs(qFramesAlertsBlank)}`, qFramesAlertsBlank, 'GET /frames/alerts timestamps only', 'frames_alerts_timestamps_only');
    const qFramesAlertsProximity = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS, type: 'proximity' };
    add(`/frames/alerts?${qs(qFramesAlertsProximity)}`, qFramesAlertsProximity, 'GET /frames/alerts type=proximity', 'frames_alerts_type_proximity');

    // --- mdx-frames: proximity-detection (GET /frames/proximity-detection) ---
    const qProximity = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/frames/proximity-detection?${qs(qProximity)}`, qProximity, 'GET /frames/proximity-detection sensorId+timestamps', 'frames_proximity_detection_sensorId_timestamps');

    // --- mdx-frames: enhanced (GET /frames/enhanced) ---
    const qFramesEnhanced = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/frames/enhanced?${qs(qFramesEnhanced)}`, qFramesEnhanced, 'GET /frames/enhanced sensorId+timestamps', 'frames_enhanced_sensorId_timestamps');

    // --- metrics: occupancy/fov (GET /metrics/occupancy/fov) ---
    const qFov = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/metrics/occupancy/fov?${qs(qFov)}`, qFov, 'GET /metrics/occupancy/fov sensorId+timestamps', 'metrics_occupancy_fov_sensorId_timestamps');

    // --- metrics: occupancy/fov/histogram (GET /metrics/occupancy/fov/histogram) ---
    const qFovHistogram = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/metrics/occupancy/fov/histogram?${qs(qFovHistogram)}`, qFovHistogram, 'GET /metrics/occupancy/fov/histogram sensorId+timestamps', 'metrics_occupancy_fov_histogram_sensorId_timestamps');

    // --- metrics: occupancy/roi (GET /metrics/occupancy/roi) ---
    const qRoiOccupancy = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/metrics/occupancy/roi?${qs(qRoiOccupancy)}`, qRoiOccupancy, 'GET /metrics/occupancy/roi sensorId+timestamps', 'metrics_occupancy_roi_sensorId_timestamps');

    // --- metrics: occupancy/roi/histogram (GET /metrics/occupancy/roi/histogram) ---
    const qRoiHistogram = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/metrics/occupancy/roi/histogram?${qs(qRoiHistogram)}`, qRoiHistogram, 'GET /metrics/occupancy/roi/histogram sensorId+timestamps', 'metrics_occupancy_roi_histogram_sensorId_timestamps');

    // --- metrics: occupancy/tripwire (GET /metrics/occupancy/tripwire) ---
    const qTripwireOccupancy = { place: PLACE, timestamp: TO_TS };
    add(`/metrics/occupancy/tripwire?${qs(qTripwireOccupancy)}`, qTripwireOccupancy, 'GET /metrics/occupancy/tripwire place+timestamp', 'metrics_occupancy_tripwire_place_timestamp');

    // --- metrics: tripwire/histogram (GET /metrics/tripwire/histogram) ---
    const qTripwireHistogram = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/metrics/tripwire/histogram?${qs(qTripwireHistogram)}`, qTripwireHistogram, 'GET /metrics/tripwire/histogram sensorId+timestamps', 'metrics_tripwire_histogram_sensorId_timestamps');

    // --- metrics: tripwire/counts (GET /metrics/tripwire/counts) ---
    const qTripwireCounts = { sensorId: SENSOR_IDS[0], fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/metrics/tripwire/counts?${qs(qTripwireCounts)}`, qTripwireCounts, 'GET /metrics/tripwire/counts sensorId+timestamps', 'metrics_tripwire_counts_sensorId_timestamps');

    return cases;
}

function safeSlug(s) {
    return s.replace(/[^a-zA-Z0-9_-]/g, '_');
}

async function main() {
    const baseUrl = process.argv[2] || 'http://localhost:8081';
    const webApisRoot = process.argv[3] || DEFAULT_WEB_APIS_ROOT;
    const INTEGRATION_TEST_DIR = path.join(SCRIPT_DIR, '..');
    const fixturesRoot = process.argv[4] || path.join(INTEGRATION_TEST_DIR, 'fixtures');
    const outDir = path.join(fixturesRoot, WAREHOUSE_FIXTURES_SUBDIR);

    const openapi = loadOpenApi(webApisRoot);
    if (!openapi) {
        console.error(`OpenAPI spec not found at ${path.join(webApisRoot, OPENAPI_SPEC_PATH)}`);
        process.exit(1);
    }

    const ajv = loadAjv();
    if (!ajv) {
        console.error('AJV not found. Run "npm install" in test/');
        process.exit(1);
    }

    const inputSchemaCache = new Map();
    const responseSchemaCache = new Map();

    function validateInput(pathname, method, queryParams) {
        const pathOnly = pathname.split('?')[0];
        const key = `${method}:${pathOnly}`;
        let validate = inputSchemaCache.get(key);
        if (!validate) {
            const schema = getInputSchemaFromOpenApi(openapi, method, pathOnly);
            if (!schema) return null;
            try {
                validate = ajv.compile(schema);
            } catch (err) {
                console.warn(`⚠ Could not compile input schema for ${pathOnly}: ${err.message}`);
                return null;
            }
            inputSchemaCache.set(key, validate);
        }
        const valid = validate(queryParams);
        return valid ? null : (validate.errors || []).map((e) => e.message || JSON.stringify(e)).join('; ');
    }

    function validateOutput(pathname, method, statusCode, body) {
        if (!body || statusCode < 200 || statusCode >= 300) return null;
        let json;
        try {
            json = JSON.parse(body);
        } catch {
            return null;
        }
        const pathOnly = pathname.split('?')[0];
        const cacheKey = `${method}:${pathOnly}:${statusCode}`;
        let validate = responseSchemaCache.get(cacheKey);
        if (!validate) {
            const schema = getResponseSchema(openapi, method, pathOnly, statusCode);
            if (!schema) return null;
            try {
                validate = ajv.compile(schema);
            } catch (err) {
                return null;
            }
            responseSchemaCache.set(cacheKey, validate);
        }
        const valid = validate(json);
        return valid ? null : (validate.errors || []);
    }

    function formatValidationErrors(errors) {
        const missing = [];
        const mismatched = [];
        for (const e of errors) {
            if (e.keyword === 'required') {
                const parent = e.instancePath || '(root)';
                missing.push(`${parent}/${e.params.missingProperty}`);
            } else {
                const key = e.instancePath || '(root)';
                mismatched.push(`${key} (${e.message})`);
            }
        }
        const parts = [];
        if (missing.length) parts.push(`missing keys: ${missing.join(', ')}`);
        if (mismatched.length) parts.push(`mismatched keys: ${mismatched.join(', ')}`);
        return parts.join('; ');
    }

    const testCases = getWarehouse2dAppTestCases();
    let failed = 0;

    console.log(`Running warehouse_2d_app tests against ${baseUrl}`);
    console.log(`Time range: ${FROM_TS} → ${TO_TS}, place: ${PLACE}`);
    console.log('----------------------------------------');

    for (const test of testCases) {
        const pathname = test.path.startsWith('/') ? test.path : `/${test.path}`;
        const pathOnly = pathname.split('?')[0];
        const inputError = validateInput(pathname, test.method, test.queryParams);
        if (inputError) {
            console.log(`✗ ${test.name} -> input invalid (AJV): ${inputError}`);
            failed++;
            continue;
        }
        try {
            const { statusCode, body } = await request(baseUrl, test.method, pathname);
            if (statusCode !== 200) {
                console.log(`✗ ${test.name} -> expected 200, got ${statusCode}`);
                if (body) console.log(`  Response: ${body.slice(0, 200)}`);
                failed++;
                continue;
            }
            const outputErrors = validateOutput(pathname, test.method, statusCode, body);
            if (outputErrors) {
                console.log(`✗ ${test.name} -> response invalid (OpenAPI): ${formatValidationErrors(outputErrors)}`);
                failed++;
                continue;
            }
            const dir = path.join(outDir, safeSlug(test.slug));
            fs.mkdirSync(dir, { recursive: true });
            fs.writeFileSync(path.join(dir, 'input.json'), JSON.stringify(test.queryParams, null, 2), 'utf8');
            let outBody = body;
            try {
                outBody = JSON.stringify(JSON.parse(body), null, 2);
            } catch (_) {}
            fs.writeFileSync(path.join(dir, 'output.json'), outBody, 'utf8');
            console.log(`✓ ${test.name} -> 200 (AJV ✓, OpenAPI ✓) -> ${path.relative(fixturesRoot, dir)}`);
        } catch (err) {
            console.log(`✗ ${test.name} -> ${err.message}`);
            failed++;
        }
    }

    console.log('----------------------------------------');
    if (failed === 0) {
        console.log(`✅ All warehouse_2d_app checks passed; fixtures written to ${outDir}`);
        process.exit(0);
    } else {
        console.log(`❌ ${failed} check(s) failed`);
        process.exit(1);
    }
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
