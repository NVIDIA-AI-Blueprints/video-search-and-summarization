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
 * Load Elasticsearch data from elasticsearch_data_dump into a running ES instance.
 * Expects NDJSON files under DUMP_DIR: each line is an ES hit { _index, _id, _source }.
 * Converts to bulk format and POSTs to _bulk in chunks.
 * Usage: node load_elasticsearch_data_dump.js <ES_URL> <DUMP_DIR>
 * Example: node load_elasticsearch_data_dump.js http://localhost:9200 /path/to/integration-test/elasticsearch_data_dump
 */

'use strict';

const path = require('path');
const fs = require('fs');
const readline = require('readline');
const http = require('http');
const https = require('https');
const { URL } = require('url');

const BULK_CHUNK_SIZE = 500;

function parseEsUrl(esUrl) {
    const u = new URL(esUrl);
    const isHttps = u.protocol === 'https:';
    const port = u.port || (isHttps ? 443 : 80);
    return { hostname: u.hostname, port: Number(port), pathPrefix: u.pathname.replace(/\/$/, ''), isHttps };
}

function request(esUrlParsed, method, pathSegments, body) {
    const pathStr = pathSegments.join('/').replace(/\/+/g, '/');
    const payload = body ? Buffer.from(body, 'utf8') : null;
    const options = {
        hostname: esUrlParsed.hostname,
        port: esUrlParsed.port,
        path: `${esUrlParsed.pathPrefix}/${pathStr}`.replace(/\/+/g, '/'),
        method,
        headers: { 'Content-Type': 'application/json' },
    };
    if (payload) options.headers['Content-Length'] = payload.length;
    return new Promise((resolve, reject) => {
        const mod = esUrlParsed.isHttps ? https : http;
        const req = mod.request(options, (res) => {
            let data = '';
            res.on('data', (chunk) => { data += chunk; });
            res.on('end', () => resolve({ statusCode: res.statusCode, data }));
        });
        req.on('error', reject);
        req.setTimeout(15000, () => { req.destroy(); reject(new Error('Request timeout')); });
        if (payload) req.write(payload);
        req.end();
    });
}

const COORDINATES_FLOAT_MAPPING = {
    mappings: {
        properties: {
            locations: { properties: { coordinates: { type: 'float' } } },
            smoothLocations: { properties: { coordinates: { type: 'float' } } },
            object: {
                properties: {
                    bbox3d: { properties: { coordinates: { type: 'float' } } },
                },
            },
            objects: {
                properties: {
                    bbox3d: { properties: { coordinates: { type: 'float' } } },
                },
            },
        },
    },
};

/** Ensure index exists with coordinates as float so ES does not mix long/float. */
const createdIndices = new Set();
async function ensureIndexWithFloatCoords(esUrlParsed, indexName) {
    if (createdIndices.has(indexName)) return;
    const head = await request(esUrlParsed, 'HEAD', [indexName]);
    if (head.statusCode === 200) {
        createdIndices.add(indexName);
        return;
    }
    const { statusCode, data } = await request(esUrlParsed, 'PUT', [indexName], JSON.stringify(COORDINATES_FLOAT_MAPPING));
    if (statusCode === 200 || statusCode === 201) {
        createdIndices.add(indexName);
    } else {
        console.warn(`Create index ${indexName} returned ${statusCode}: ${data?.slice(0, 150)}`);
    }
}

function postBulk(esUrlParsed, body) {
    return new Promise((resolve, reject) => {
        const payload = Buffer.from(body, 'utf8');
        const options = {
            hostname: esUrlParsed.hostname,
            port: esUrlParsed.port,
            path: `${esUrlParsed.pathPrefix}/_bulk`,
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-ndjson',
                'Content-Length': payload.length,
            },
        };
        const mod = esUrlParsed.isHttps ? https : http;
        const req = mod.request(options, (res) => {
            let data = '';
            res.on('data', (chunk) => { data += chunk; });
            res.on('end', () => {
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    const json = JSON.parse(data);
                    if (json.errors) {
                        const first = (json.items || []).find((i) => i.index && i.index.error);
                        reject(new Error(first ? JSON.stringify(first.index.error) : 'Bulk had errors'));
                    } else {
                        resolve();
                    }
                } else {
                    reject(new Error(`_bulk returned ${res.statusCode}: ${data.slice(0, 300)}`));
                }
            });
        });
        req.on('error', reject);
        req.setTimeout(120000, () => { req.destroy(); reject(new Error('Bulk request timeout')); });
        req.write(payload);
        req.end();
    });
}

/**
 * Normalize _source so ES mapping is consistent (e.g. coordinates as float not long).
 * Recursively ensures any "coordinates" array is array of floats so ES does not
 * mix long/float and trigger "mapper [locations.coordinates] cannot be changed from type [float] to [long]".
 * Uses a custom stringify for the bulk body so coordinate numbers are emitted as "1.0" not "1".
 */
function normalizeSource(obj) {
    if (obj === null || typeof obj !== 'object') return obj;
    if (Array.isArray(obj)) {
        if (obj.every((v) => typeof v === 'number')) {
            return obj.map((n) => Number(n));
        }
        return obj.map((v) => (v !== null && typeof v === 'object' ? normalizeSource(v) : v));
    }
    const out = {};
    for (const key of Object.keys(obj)) {
        const val = obj[key];
        if (key === 'coordinates' && Array.isArray(val)) {
            out[key] = val.map((v) => (Array.isArray(v) && v.every((n) => typeof n === 'number') ? v.map((n) => Number(n)) : normalizeSource(v)));
        } else if (val !== null && typeof val === 'object') {
            out[key] = normalizeSource(val);
        } else {
            out[key] = val;
        }
    }
    return out;
}

function* findJsonFiles(dir) {
    if (!fs.existsSync(dir)) return;
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const e of entries) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) {
            yield* findJsonFiles(full);
        } else if (e.isFile() && e.name.endsWith('.json')) {
            yield full;
        }
    }
}

async function loadFile(esUrlParsed, filePath) {
    // Detect Git LFS pointer files (3-line text stub) so CI doesn't silently load 0 docs.
    const headBuf = Buffer.alloc(200);
    const fd = fs.openSync(filePath, 'r');
    let headBytes = 0;
    try {
        headBytes = fs.readSync(fd, headBuf, 0, 200, 0);
    } finally {
        fs.closeSync(fd);
    }
    if (headBuf.toString('utf8', 0, headBytes).startsWith('version https://git-lfs.github.com/spec/')) {
        throw new Error(`${filePath} is a Git LFS pointer, not the actual dump. Run "git lfs pull" (or enable GitLFSPull in the CI checkout).`);
    }
    const rl = readline.createInterface({ input: fs.createReadStream(filePath), crlfDelay: Infinity });
    let bulkLines = [];
    let count = 0;
    for await (const line of rl) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        let hit;
        try {
            hit = JSON.parse(trimmed);
        } catch {
            continue;
        }
        const idx = hit._index;
        const id = hit._id;
        const src = hit._source;
        if (!idx || src === undefined) continue;
        await ensureIndexWithFloatCoords(esUrlParsed, idx);
        bulkLines.push(JSON.stringify({ index: { _index: idx, _id: id || undefined } }));
        bulkLines.push(JSON.stringify(normalizeSource(src)));
        count++;
        if (bulkLines.length / 2 >= BULK_CHUNK_SIZE) {
            await postBulk(esUrlParsed, bulkLines.join('\n') + '\n');
            bulkLines = [];
        }
    }
    if (bulkLines.length > 0) {
        await postBulk(esUrlParsed, bulkLines.join('\n') + '\n');
    }
    return count;
}

async function main() {
    const esUrl = process.argv[2] || 'http://localhost:9200';
    const dumpDir = process.argv[3];
    if (!dumpDir || !fs.existsSync(dumpDir)) {
        console.log('Dump directory not found or not provided, skipping Elasticsearch data load.');
        process.exit(0);
    }
    const esUrlParsed = parseEsUrl(esUrl);
    let total = 0;
    let filesFound = 0;
    for (const filePath of findJsonFiles(dumpDir)) {
        filesFound++;
        const rel = path.relative(dumpDir, filePath);
        try {
            const n = await loadFile(esUrlParsed, filePath);
            total += n;
            if (n > 0) console.log(`  Loaded ${n} docs from ${rel}`);
        } catch (err) {
            console.error(`✗ Failed to load ${rel}: ${err.message}`);
            process.exit(1);
        }
    }
    if (total > 0) {
        console.log(`✓ Elasticsearch data dump loaded: ${total} documents`);
    } else if (filesFound > 0) {
        console.error(`✗ Dump directory ${dumpDir} contained files but no documents were loaded.`);
        process.exit(1);
    }
    process.exit(0);
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
