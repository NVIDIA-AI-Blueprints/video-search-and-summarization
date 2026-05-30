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
 * Integration tests for warehouse_3d_app dump-backed endpoints.
 * Time range from dump: 2026-02-23T10:09:22.753Z to 2026-02-23T10:13:29.453Z.
 * - Validates each request input (query params) against OpenAPI parameter schemas (AJV).
 * - Hits the API, validates response against OpenAPI response schema.
 * - Writes fixtures (input.json + output.json) per test under fixtures/warehouse_3d_app/.
 * Usage: node run_warehouse_3d_app_tests.js <BASE_URL> [WEB_APIS_ROOT] [FIXTURES_OUT_DIR]
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
const WAREHOUSE_FIXTURES_SUBDIR = 'warehouse_3d_app';

const FROM_TS = '2026-02-23T10:09:22.753Z';
const TO_TS = '2026-02-23T10:13:29.453Z';
const EVENTS_TO_TS = '2026-02-23T10:13:21.553Z';
const PLACE = 'room=SURF Booth/region=region-1';
const SENSOR_ID = 'bev-sensor-1';
const FRAME_ID = '896';
const OBJECT_ID = '5';
const OBJECT_TYPE_PERSON = 'Person';
const ALERT_TYPE_PROXIMITY = 'proximity';

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
    const ajvPackagePath = path.join(TEST_DIR, 'node_modules', 'ajv', 'package.json');
    let Ajv2019;
    if (fs.existsSync(ajvPackagePath)) {
        Ajv2019 = require(path.join(TEST_DIR, 'node_modules', 'ajv', 'dist', '2019'));
    } else {
        try {
            Ajv2019 = require('ajv/dist/2019');
        } catch (_) {
            return null;
        }
    }
    const ajv = new Ajv2019({ allErrors: true, useDefaults: true, coerceTypes: 'array', strict: false });
    try {
        const ajvErrors = fs.existsSync(path.join(TEST_DIR, 'node_modules', 'ajv-errors', 'package.json'))
            ? require(path.join(TEST_DIR, 'node_modules', 'ajv-errors'))
            : require('ajv-errors');
        ajvErrors(ajv);
    } catch (_) {}
    try {
        const ajvFormats = fs.existsSync(path.join(TEST_DIR, 'node_modules', 'ajv-formats', 'package.json'))
            ? require(path.join(TEST_DIR, 'node_modules', 'ajv-formats'))
            : require('ajv-formats');
        ajvFormats(ajv);
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

function getWarehouse3dAppTestCases() {
    const cases = [];
    const add = (pathName, queryParams, name, slug) => {
        cases.push({ path: pathName, method: 'GET', queryParams: queryParams || {}, name, slug });
    };

    const behaviorTimeRange = { sensorId: SENSOR_ID, fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    const eventTimeRange = { sensorId: SENSOR_ID, fromTimestamp: FROM_TS, toTimestamp: EVENTS_TO_TS };
    const placeEventTimeRange = { place: PLACE, fromTimestamp: FROM_TS, toTimestamp: EVENTS_TO_TS };
    const frameTimeRange = { sensorId: SENSOR_ID, fromTimestamp: FROM_TS, toTimestamp: TO_TS };

    add(`/behavior?${qs({ sensorId: SENSOR_ID })}`, { sensorId: SENSOR_ID }, 'GET /behavior sensorId', 'behavior_sensorId_bev_sensor_1');
    add(
        `/behavior?${qs(behaviorTimeRange)}`,
        behaviorTimeRange,
        'GET /behavior sensorId+timestamps',
        'behavior_sensorId_bev_sensor_1_timestamps'
    );
    const qBehaviorObjectId = { ...behaviorTimeRange, objectId: OBJECT_ID };
    add(`/behavior?${qs(qBehaviorObjectId)}`, qBehaviorObjectId, 'GET /behavior sensorId+timestamps+objectId', 'behavior_sensorId_timestamps_objectId');
    const qBehaviorObjectType = { ...behaviorTimeRange, objectType: OBJECT_TYPE_PERSON };
    add(`/behavior?${qs(qBehaviorObjectType)}`, qBehaviorObjectType, 'GET /behavior sensorId+timestamps+objectType', 'behavior_sensorId_timestamps_objectType');
    const qBehaviorMax = { ...behaviorTimeRange, maxResultSize: 5 };
    add(`/behavior?${qs(qBehaviorMax)}`, qBehaviorMax, 'GET /behavior sensorId+timestamps+maxResultSize', 'behavior_sensorId_timestamps_max_result_size');

    add(`/frames/enhanced?${qs(frameTimeRange)}`, frameTimeRange, 'GET /frames/enhanced sensorId+timestamps', 'frames_enhanced_sensorId_timestamps');
    const qBevFrames = { sensorId: SENSOR_ID, fromTimestamp: FROM_TS, toTimestamp: TO_TS };
    add(`/frames/bev?${qs(qBevFrames)}`, qBevFrames, 'GET /frames/bev sensorId+timestamps', 'frames_bev_sensorId_timestamps');
    add(`/frames/proximity-detection?${qs(frameTimeRange)}`, frameTimeRange, 'GET /frames/proximity-detection sensorId+timestamps', 'frames_proximity_detection_sensorId_timestamps');
    add(`/frames/alerts?${qs(frameTimeRange)}`, frameTimeRange, 'GET /frames/alerts sensorId+timestamps', 'frames_alerts_sensorId_timestamps');
    const qFramesAlertsProximity = { ...frameTimeRange, type: ALERT_TYPE_PROXIMITY };
    add(`/frames/alerts?${qs(qFramesAlertsProximity)}`, qFramesAlertsProximity, 'GET /frames/alerts type=proximity', 'frames_alerts_type_proximity');

    add(`/events/roi?${qs(eventTimeRange)}`, eventTimeRange, 'GET /events/roi sensorId+timestamps', 'events_roi_sensorId_timestamps');
    add(`/events/roi?${qs(placeEventTimeRange)}`, placeEventTimeRange, 'GET /events/roi place+timestamps', 'events_roi_place_timestamps');

    add(`/metrics/occupancy/fov?${qs(frameTimeRange)}`, frameTimeRange, 'GET /metrics/occupancy/fov sensorId+timestamps', 'metrics_occupancy_fov_sensorId_timestamps');
    add(`/metrics/occupancy/fov/histogram?${qs(frameTimeRange)}`, frameTimeRange, 'GET /metrics/occupancy/fov/histogram sensorId+timestamps', 'metrics_occupancy_fov_histogram_sensorId_timestamps');
    add(`/metrics/occupancy/roi?${qs(frameTimeRange)}`, frameTimeRange, 'GET /metrics/occupancy/roi sensorId+timestamps', 'metrics_occupancy_roi_sensorId_timestamps');
    add(`/metrics/occupancy/roi/histogram?${qs(frameTimeRange)}`, frameTimeRange, 'GET /metrics/occupancy/roi/histogram sensorId+timestamps', 'metrics_occupancy_roi_histogram_sensorId_timestamps');

    return cases;
}

function safeSlug(s) {
    return s.replace(/[^a-zA-Z0-9_-]/g, '_');
}

async function main() {
    const baseUrl = process.argv[2] || 'http://localhost:8081';
    const webApisRoot = process.argv[3] || DEFAULT_WEB_APIS_ROOT;
    const integrationTestDir = path.join(SCRIPT_DIR, '..');
    const fixturesRoot = process.argv[4] || path.join(integrationTestDir, 'fixtures');
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
            } catch (_) {
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

    const testCases = getWarehouse3dAppTestCases();
    let failed = 0;

    console.log(`Running warehouse_3d_app tests against ${baseUrl}`);
    console.log(`Time range: ${FROM_TS} -> ${TO_TS}, place: ${PLACE}, sensorId: ${SENSOR_ID}`);
    console.log('----------------------------------------');

    for (const test of testCases) {
        const pathname = test.path.startsWith('/') ? test.path : `/${test.path}`;
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
        console.log(`✅ All warehouse_3d_app checks passed; fixtures written to ${outDir}`);
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
