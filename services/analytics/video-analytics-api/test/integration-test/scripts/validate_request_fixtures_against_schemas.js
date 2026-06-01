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
 * Validates integration-test request payloads (fixtures) against AJV schemas from web-api-core/schemas/ajv.
 * Ensures the data we send in POST /config/upload-file/* conforms to the schema before upload.
 * Usage: node validate_request_fixtures_against_schemas.js <FIXTURES_DIR> [WEB_APIS_ROOT]
 * Example: node validate_request_fixtures_against_schemas.js /path/to/test/integration-test/fixtures /path/to/web-apis
 */

'use strict';

const path = require('path');
const fs = require('fs');

const SCRIPT_DIR = __dirname;
const TEST_DIR = path.join(SCRIPT_DIR, '..', '..');
const DEFAULT_WEB_APIS_ROOT = path.join(TEST_DIR, '..');

// Fixture file (upload request body) -> schema file
const FIXTURES = [
    { fixtureFile: 'calibration.json', schemaFile: 'calibration.json', name: 'POST /config/upload-file/calibration (fixture)' },
    { fixtureFile: 'road-network.json', schemaFile: 'roadNetwork.json', name: 'POST /config/upload-file/road-network (fixture)' },
    { fixtureFile: 'usd-assets.json', schemaFile: 'usdAssets.json', name: 'POST /config/upload-file/usd-assets (fixture)' },
];

function loadAjv() {
    const ajvPath = path.join(TEST_DIR, 'node_modules', 'ajv');
    const ajvErrorsPath = path.join(TEST_DIR, 'node_modules', 'ajv-errors');
    if (!fs.existsSync(path.join(ajvPath, 'package.json'))) {
        console.error('AJV not found. Run "npm install" in the test/ directory.');
        return null;
    }
    const Ajv2019 = require(path.join(ajvPath, 'dist', '2019'));
    const ajvErrors = require(ajvErrorsPath);
    const ajv = new Ajv2019({ allErrors: true, useDefaults: true, coerceTypes: 'array' });
    ajvErrors(ajv);
    return ajv;
}

function main() {
    const fixturesDir = process.argv[2];
    const webApisRoot = process.argv[3] || DEFAULT_WEB_APIS_ROOT;
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
        console.log('⚠ Skipping request fixture schema validation (AJV not found). Run "npm install" in test/ to enable.');
        process.exit(0);
    }
    let failed = 0;

    console.log(`Validating request fixtures against AJV schemas (${schemasDir})`);
    console.log('----------------------------------------');

    for (const { fixtureFile, schemaFile, name } of FIXTURES) {
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
            if (valid) {
                console.log(`✓ ${name} -> valid (AJV)`);
            } else {
                const msg = (validate.errors || []).map(e => e.message || JSON.stringify(e)).join('; ');
                console.log(`✗ ${name} -> schema validation failed: ${msg}`);
                failed++;
            }
        } catch (err) {
            console.log(`✗ ${name} -> ${err.message}`);
            failed++;
        }
    }

    console.log('----------------------------------------');
    if (failed === 0) {
        console.log('✅ All request fixture schema checks passed');
        process.exit(0);
    } else {
        console.log(`❌ ${failed} request fixture schema check(s) failed`);
        process.exit(1);
    }
}

main();
