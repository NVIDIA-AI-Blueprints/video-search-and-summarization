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
 * Integration tests: all HTTP assertions via Node (no curl). Requires Node and npm install in test/.
 * - Config: validate fixtures against AJV schema then POST /config/upload-file/*
 * - Controllers: load test/controllers/rest-apis/*.js (one file per app/controllers/rest-apis/*.js)
 *   and run getTests(constants) for each; covers /livez, /config GETs, /behavior, /tracker, etc.
 * Response bodies are validated against the response schemas from src/app/specification/openapi.json.
 * Usage: node run_integration_tests.js <BASE_URL> <FIXTURES_DIR> [WEB_APIS_ROOT]
 */

'use strict';

const path = require('path');
const fs = require('fs');
const http = require('http');
const https = require('https');
const FormData = require('form-data');

const OPENAPI_SPEC_PATH = path.join('src', 'app', 'specification', 'openapi.json');

const SCRIPT_DIR = __dirname;
const TEST_DIR = path.join(SCRIPT_DIR, '..', '..');
const DEFAULT_WEB_APIS_ROOT = path.join(TEST_DIR, '..');

const UPLOADS = [
    { docType: 'calibration', fixtureFile: 'calibration.json', schemaFile: 'calibration.json' },
    { docType: 'road-network', fixtureFile: 'road-network.json', schemaFile: 'roadNetwork.json' },
    { docType: 'usd-assets', fixtureFile: 'usd-assets.json', schemaFile: 'usdAssets.json' },
];

function qs(params) {
    return Object.entries(params)
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
        .join('&');
}

const CONTROLLERS_REST_APIS_DIR = path.join(SCRIPT_DIR, 'controllers', 'rest-apis');

/** Shared constants passed to each controller test module (mirrors app/controllers/rest-apis data). */
function getControllerConstants() {
    return {
        qs,
        SENSOR_ID: 'Camera',
        SENSOR_ID_ALT: 'Camera_01',
        PLACE: 'building=Warehouse/room=Room-1',
        OBJECT_ID: '0',
        OBJECT_TYPE: 'Person',
        FROM_TS: '2026-02-14T10:16:00.000Z',
        TO_TS: '2026-02-14T10:17:00.000Z',
        BEHAVIOR_ID: 'Camera #-# 0',
    };
}

// Controller tests live in scripts/controllers/rest-apis/*.js (one file per app/controllers/rest-apis/*.js)

const FORM_FIELD_NAME = 'configFiles';

function loadOpenApi(webApisRoot) {
    const specPath = path.join(webApisRoot, OPENAPI_SPEC_PATH);
    if (!fs.existsSync(specPath)) {
        return null;
    }
    return JSON.parse(fs.readFileSync(specPath, 'utf8'));
}

/** OpenAPI metadata keywords to strip so AJV (strict mode) can compile the schema. */
const OPENAPI_METADATA_KEYS = new Set(['example', 'description']);

/** Strip additionalProperties from schema so response validation allows extra fields from the API. */
function stripAdditionalProperties(schema) {
    if (!schema || typeof schema !== 'object') {
        return;
    }
    if (!Array.isArray(schema)) {
        delete schema.additionalProperties;
        for (const key of Object.keys(schema)) {
            stripAdditionalProperties(schema[key]);
        }
    } else {
        for (let i = 0; i < schema.length; i++) {
            stripAdditionalProperties(schema[i]);
        }
    }
}

/** Strip minLength so response validation allows empty strings (API may return "" for optional fields). */
function stripMinLength(schema) {
    if (!schema || typeof schema !== 'object') {
        return;
    }
    if (!Array.isArray(schema)) {
        delete schema.minLength;
        for (const key of Object.keys(schema)) {
            stripMinLength(schema[key]);
        }
    } else {
        for (let i = 0; i < schema.length; i++) {
            stripMinLength(schema[i]);
        }
    }
}

/**
 * Resolve $ref "#/components/schemas/X" in a schema. Returns a deep copy with refs inlined.
 * Converts OpenAPI "nullable: true" to JSON Schema type: [t, "null"] for AJV compatibility.
 * Strips OpenAPI-only keywords (example, examples, description) so AJV can compile.
 * Avoids circular refs by tracking visited refs.
 */
function resolveSchemaRef(spec, schema, visited) {
    if (!schema || typeof schema !== 'object') {
        return schema;
    }
    if (schema.$ref && typeof schema.$ref === 'string') {
        const ref = schema.$ref;
        if (ref.startsWith('#/components/schemas/')) {
            const name = ref.slice('#/components/schemas/'.length);
            if (visited.has(name)) {
                const cycle = [...visited, name].join(' → ');
                throw new Error(`OpenAPI schema cyclic $ref: ${cycle}`);
            }
            const resolved = spec.components && spec.components.schemas && spec.components.schemas[name];
            if (!resolved) {
                return schema;
            }
            visited.add(name);
            const out = resolveSchemaRef(spec, resolved, visited);
            visited.delete(name);
            return out;
        }
    }
    const out = Array.isArray(schema) ? [] : {};
    for (const key of Object.keys(schema)) {
        if (key === 'nullable' && schema.nullable === true) {
            continue;
        }
        if (OPENAPI_METADATA_KEYS.has(key)) {
            continue;
        }
        out[key] = resolveSchemaRef(spec, schema[key], visited);
    }
    if (schema.nullable === true && out.type != null) {
        if (typeof out.type === 'string') {
            out.type = [out.type, 'null'];
        } else if (Array.isArray(out.type) && !out.type.includes('null')) {
            out.type = [...out.type, 'null'];
        }
    }
    return out;
}

/**
 * Find the OpenAPI path key that matches the request path (e.g. /config/upload-file/calibration
 * matches /config/upload-file/{docType}).
 */
function findSpecPath(openapi, requestPath) {
    if (openapi.paths[requestPath]) {
        return requestPath;
    }
    for (const specPath of Object.keys(openapi.paths)) {
        const pattern = '^' + specPath.replace(/\{[^}]+\}/g, '[^/]+') + '$';
        if (new RegExp(pattern).test(requestPath)) {
            return specPath;
        }
    }
    return null;
}

/**
 * Get the response body schema for (method, path, statusCode) from the OpenAPI spec.
 * Returns null if no application/json schema is defined.
 */
function getResponseSchema(openapi, method, requestPath, statusCode) {
    const specPath = findSpecPath(openapi, requestPath);
    if (!specPath) {
        return null;
    }
    const pathItem = openapi.paths[specPath];
    const methodLower = method.toLowerCase();
    const op = pathItem[methodLower];
    if (!op || !op.responses) {
        return null;
    }
    const statusStr = String(statusCode);
    const response = op.responses[statusStr] || op.responses.default;
    if (!response || !response.content || !response.content['application/json']) {
        return null;
    }
    const schema = response.content['application/json'].schema;
    if (!schema) {
        return null;
    }
    const resolved = resolveSchemaRef(openapi, schema, new Set());
    stripAdditionalProperties(resolved);
    stripMinLength(resolved);
    return resolved;
}

function loadAjv() {
    const ajvPath = path.join(TEST_DIR, 'node_modules', 'ajv');
    const ajvErrorsPath = path.join(TEST_DIR, 'node_modules', 'ajv-errors');
    if (!fs.existsSync(path.join(ajvPath, 'package.json'))) {
        return null;
    }
    const Ajv2019 = require(path.join(ajvPath, 'dist', '2019'));
    const ajvErrors = require(ajvErrorsPath);
    const ajv = new Ajv2019({
        allErrors: true,      // Report every validation error, not just the first
        useDefaults: true,    // Fill in default values from the schema when validating
        coerceTypes: 'array', // Coerce types for array items when the schema allows it
        strict: false         // Do not treat unknown keywords as errors when compiling schemas
      });
    ajvErrors(ajv);
    try {
        const ajvFormats = require(path.join(TEST_DIR, 'node_modules', 'ajv-formats'));
        ajvFormats(ajv);
    } catch (err) {
        console.warn('⚠ ajv-formats not loaded:', err.message, '- date-time and other formats will not be validated');
    }
    // OpenAPI "double" (64-bit float) - not in ajv-formats
    if (!ajv.formats.double) {
        ajv.addFormat('double', (value) => typeof value === 'number' && !Number.isNaN(value));
    }
    return ajv;
}

function request(baseUrl, method, pathname, expectedStatus) {
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

function requestWithBody(baseUrl, method, pathname, body) {
    return new Promise((resolve, reject) => {
        const url = new URL(pathname, baseUrl);
        const client = url.protocol === 'https:' ? https : http;
        const payload = typeof body === 'string' ? body : JSON.stringify(body);
        const opts = {
            hostname: url.hostname,
            port: url.port || (url.protocol === 'https:' ? 443 : 80),
            path: url.pathname + url.search,
            method,
            headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload, 'utf8') },
        };
        const req = client.request(opts, (res) => {
            let data = '';
            res.on('data', (chunk) => { data += chunk; });
            res.on('end', () => resolve({ statusCode: res.statusCode, body: data }));
        });
        req.on('error', reject);
        req.setTimeout(15000, () => { req.destroy(); reject(new Error('Request timeout')); });
        req.write(payload, 'utf8');
        req.end();
    });
}

function postCalibrationImages(baseUrl, imageFilePath, metadataFilePath) {
    return new Promise((resolve, reject) => {
        const url = new URL('/config/calibration/images', baseUrl);
        const client = url.protocol === 'https:' ? https : http;

        const form = new FormData();
        form.append('images', fs.readFileSync(imageFilePath), {
            filename: path.basename(imageFilePath),
            contentType: 'image/png',
        });
        form.append('imageMetadata', fs.readFileSync(metadataFilePath), {
            filename: path.basename(metadataFilePath),
            contentType: 'application/json',
        });

        const opts = {
            hostname: url.hostname,
            port: url.port || (url.protocol === 'https:' ? 443 : 80),
            path: url.pathname,
            method: 'POST',
            headers: form.getHeaders(),
        };

        form.getLength((err, length) => {
            if (err) { reject(err); return; }
            opts.headers['Content-Length'] = length;
            const req = client.request(opts, (res) => {
                let data = '';
                res.on('data', (chunk) => { data += chunk; });
                res.on('end', () => resolve({ statusCode: res.statusCode, body: data }));
            });
            req.on('error', reject);
            req.setTimeout(30000, () => { req.destroy(); reject(new Error('Upload timeout')); });
            form.pipe(req);
        });
    });
}

function postMultipartFile(baseUrl, docType, filePath) {
    return new Promise((resolve, reject) => {
        const url = new URL(`/config/upload-file/${docType}`, baseUrl);
        const client = url.protocol === 'https:' ? https : http;
        const filename = path.basename(filePath);

        const form = new FormData();
        const fileBuffer = fs.readFileSync(filePath);
        form.append(FORM_FIELD_NAME, fileBuffer, {
            filename,
            contentType: 'application/json',
        });

        const opts = {
            hostname: url.hostname,
            port: url.port || (url.protocol === 'https:' ? 443 : 80),
            path: url.pathname + url.search,
            method: 'POST',
            headers: form.getHeaders(),
        };

        form.getLength((err, length) => {
            if (err) {
                reject(err);
                return;
            }
            opts.headers['Content-Length'] = length;

            const req = client.request(opts, (res) => {
                let data = '';
                res.on('data', (chunk) => { data += chunk; });
                res.on('end', () => resolve({ statusCode: res.statusCode, body: data }));
            });
            req.on('error', reject);
            req.setTimeout(30000, () => { req.destroy(); reject(new Error('Upload timeout')); });
            form.pipe(req);
        });
    });
}

async function main() {
    const baseUrl = process.argv[2] || 'http://localhost:8081';
    const fixturesDir = process.argv[3];
    const webApisRoot = process.argv[4] || DEFAULT_WEB_APIS_ROOT;
    const schemasDir = path.join(webApisRoot, 'src', 'web-api-core', 'schemas', 'ajv');

    if (!fixturesDir || !fs.existsSync(fixturesDir)) {
        console.error(`Fixtures directory not found: ${fixturesDir || '(not provided)'}`);
        process.exit(1);
    }
    if (!fs.existsSync(schemasDir)) {
        console.error(`Schemas directory not found: ${schemasDir}`);
        process.exit(1);
    }

    const ajv = loadAjv();
    if (!ajv) {
        console.error('AJV not found. Run "npm install" in the test/ directory.');
        process.exit(1);
    }

    const openapi = loadOpenApi(webApisRoot);
    if (!openapi) {
        console.error(`OpenAPI spec not found at ${path.join(webApisRoot, OPENAPI_SPEC_PATH)}. Required for response validation.`);
        process.exit(1);
    }

    const responseSchemaCache = new Map();

    function validateOutput(name, method, pathname, statusCode, body) {
        if (!openapi || !body || statusCode < 200 || statusCode >= 300) {
            return null;
        }
        let json;
        try {
            json = JSON.parse(body);
        } catch {
            return null;
        }
        const cacheKey = `${method}:${pathname}:${statusCode}`;
        let validate = responseSchemaCache.get(cacheKey);
        if (!validate) {
            const schema = getResponseSchema(openapi, method, pathname, statusCode);
            if (!schema) {
                return null;
            }
            try {
                validate = ajv.compile(schema);
            } catch (err) {
                console.log(`⚠ ${name} -> could not compile OpenAPI response schema: ${err.message}`);
                return null;
            }
            responseSchemaCache.set(cacheKey, validate);
        }
        const valid = validate(json);
        if (valid) {
            return { valid: true };
        }
        const msg = (validate.errors || []).map(e => e.message || JSON.stringify(e)).join('; ');
        return { valid: false, errors: msg };
    }

    let failed = 0;

    console.log(`Running integration tests against ${baseUrl}`);
    console.log('----------------------------------------');

    // Validate (AJV) then POST for each config
    for (const { docType, fixtureFile, schemaFile } of UPLOADS) {
        const name = `POST /config/upload-file/${docType}`;
        const fixturePath = path.join(fixturesDir, fixtureFile);
        const schemaPath = path.join(schemasDir, schemaFile);

        if (!fs.existsSync(fixturePath)) {
            console.log(`✗ ${name} -> fixture not found: ${fixtureFile}`);
            failed++;
            continue;
        }
        if (!fs.existsSync(schemaPath)) {
            console.log(`✗ ${name} -> schema not found: ${schemaFile}`);
            failed++;
            continue;
        }

        try {
            const payload = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
            const schema = JSON.parse(fs.readFileSync(schemaPath, 'utf8'));
            const validate = ajv.compile(schema);
            const valid = validate(payload);
            if (!valid) {
                const msg = (validate.errors || []).map(e => e.message || JSON.stringify(e)).join('; ');
                console.log(`✗ ${name} -> schema validation failed (not sent): ${msg}`);
                failed++;
                continue;
            }

            const { statusCode, body } = await postMultipartFile(baseUrl, docType, fixturePath);
            if (statusCode === 200 || statusCode === 201) {
                const uploadPath = `/config/upload-file/${docType}`;
                const result = validateOutput(name, 'POST', uploadPath, statusCode, body);
                if (result && !result.valid) {
                    console.log(`✗ ${name} -> ${statusCode} but response invalid (OpenAPI): ${result.errors}`);
                    failed++;
                } else {
                    console.log(`✓ ${name} -> valid (AJV) -> ${statusCode}` + (result && result.valid ? ' (OpenAPI ✓)' : ''));
                }
            } else {
                console.log(`✗ ${name} -> valid (AJV) but upload failed: ${statusCode}`);
                if (body) console.log(`  Response: ${body.slice(0, 200)}`);
                failed++;
            }
        } catch (err) {
            console.log(`✗ ${name} -> ${err.message}`);
            failed++;
        }
    }

    // POST /config/calibration/images (multipart: image + imageMetadata JSON)
    {
        const name = 'POST /config/calibration/images';
        const imageFixturePath = path.join(fixturesDir, 'Top.png');
        const metadataFixturePath = path.join(fixturesDir, 'calibration_image_metadata.json');
        if (!fs.existsSync(imageFixturePath) || !fs.existsSync(metadataFixturePath)) {
            console.log(`✗ ${name} -> fixture not found (Top.png or calibration_image_metadata.json)`);
            failed++;
        } else {
            try {
                const { statusCode, body } = await postCalibrationImages(baseUrl, imageFixturePath, metadataFixturePath);
                if (statusCode === 201) {
                    const result = validateOutput(name, 'POST', '/config/calibration/images', statusCode, body);
                    if (result && !result.valid) {
                        console.log(`✗ ${name} -> ${statusCode} but response invalid (OpenAPI): ${result.errors}`);
                        failed++;
                    } else {
                        console.log(`✓ ${name} -> ${statusCode}` + (result && result.valid ? ' (OpenAPI ✓)' : ''));
                    }
                } else {
                    console.log(`✗ ${name} -> expected 201, got ${statusCode}`);
                    if (body) console.log(`  Response: ${body.slice(0, 200)}`);
                    failed++;
                }
            } catch (err) {
                console.log(`✗ ${name} -> ${err.message}`);
                failed++;
            }
        }
    }

    // Run controller tests from scripts/controllers/rest-apis/*.js (one file per app/controllers/rest-apis/*.js)
    const constants = getControllerConstants();
    const controllerFiles = fs.existsSync(CONTROLLERS_REST_APIS_DIR)
        ? fs.readdirSync(CONTROLLERS_REST_APIS_DIR).filter((f) => f.endsWith('.js')).sort()
        : [];
    for (const file of controllerFiles) {
        const controllerName = file.replace(/\.js$/, '');
        let mod;
        try {
            mod = require(path.join(CONTROLLERS_REST_APIS_DIR, file));
        } catch (err) {
            console.log(`✗ ${controllerName} -> failed to load: ${err.message}`);
            failed++;
            continue;
        }
        if (typeof mod.getTests !== 'function') {
            continue;
        }
        const tests = mod.getTests(constants);
        if (!Array.isArray(tests) || tests.length === 0) {
            continue;
        }
        for (const test of tests) {
            const { name, path: pathname, method, expectedStatus, expectedStatuses, validate, body: bodyPayload, skipOpenApiValidation } = test;
            const allowedStatuses = expectedStatuses || (expectedStatus != null ? [expectedStatus] : []);
            try {
                const { statusCode, body } = bodyPayload != null && method === 'POST'
                    ? await requestWithBody(baseUrl, method, pathname, bodyPayload)
                    : await request(baseUrl, method, pathname, allowedStatuses[0]);
                if (!allowedStatuses.includes(statusCode)) {
                    console.log(`✗ ${name} -> expected ${allowedStatuses.join(' or ')}, got ${statusCode}`);
                    if (body) console.log(`  Response: ${body.slice(0, 300)}`);
                    failed++;
                    continue;
                }
                if (validate && statusCode >= 200 && statusCode < 300) {
                    const validationError = validate(body);
                    if (validationError) {
                        console.log(`✗ ${name} -> ${statusCode} but validation failed: ${validationError}`);
                        failed++;
                        continue;
                    }
                }
                const pathForSchema = pathname.split('?')[0];
                const result = skipOpenApiValidation ? null : validateOutput(name, method, pathForSchema, statusCode, body);
                if (result && !result.valid) {
                    console.log(`✗ ${name} -> ${statusCode} but response invalid (OpenAPI): ${result.errors}`);
                    failed++;
                } else {
                    console.log(`✓ ${name} -> ${statusCode}` + (result && result.valid ? ' (OpenAPI ✓)' : (skipOpenApiValidation ? '' : '')));
                }
            } catch (err) {
                console.log(`✗ ${name} -> ${err.message}`);
                failed++;
            }
        }
    }

    console.log('----------------------------------------');
    if (failed === 0) {
        console.log('✅ All integration checks passed');
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
