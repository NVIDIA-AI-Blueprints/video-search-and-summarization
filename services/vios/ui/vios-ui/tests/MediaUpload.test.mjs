// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

// Execute the component's upload callbacks with controlled transport and React state.
// No copied upload implementation or live backend is needed for these UI regressions.
// Run from vios-ui: node --test tests/MediaUpload.test.mjs
const source = readFileSync(new URL('../src/pages/nvstreamer/MediaUpload.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('MediaUpload.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = [
    'isNumeric',
    'buildMetadata',
    'applyTranscodeHeaders',
    'isChunkSizeValid',
    'handleChunkSuccess',
    'handleChunkUploadError',
    'buildChunkHeaders',
    'uploadChunk',
    'uploadFileInChunks',
    'handleCancelAllUploads',
];
const declarations = new Map();
function visit(node) {
    if (ts.isVariableDeclaration(node) && names.includes(node.name.getText(ast))) {
        declarations.set(node.name.getText(ast), `const ${node.getText(ast)};`);
    }
    ts.forEachChild(node, visit);
}
visit(ast);
assert.equal(declarations.size, names.length);
const compiled = ts.transpileModule(
    names.map(name => declarations.get(name)).join('\n') + '\nglobalThis.uploads = { uploadFileInChunks, handleCancelAllUploads };',
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } }
).outputText;

const MiB = 1024 * 1024;
const file = (name, size) => ({ name, size, slice: () => new Blob(['video']) });
const options = file => ({
    file,
    successes: 0,
    errors: [],
    progress: [],
});

function harness(post) {
    const tokens = new Map();
    const state = { completed: 0, cancelled: [], requests: [] };
    class AxiosError extends Error {}
    const context = {
        Blob,
        FormData,
        AxiosError,
        VIDEO_UPLOAD_TIMEOUT: 999999999,
        stateRef: {
            current: {
                chunkSize: 20,
                sensorId: 'fresh-sensor',
                eventInfo: '',
                streamName: '',
                metadataTag: '',
                checksum: '',
                timestampInput: '',
            },
        },
        config: { storageManagementEndpoint: '/test' },
        cancelTokensRef: { current: tokens },
        tags: '',
        generateUUID: () => 'test-upload-id',
        useCallback: fn => fn,
        setFileTag: () => {},
        enqueueSnackbar: () => {},
        handleRefresh: () => {},
        LOG: { info() {}, error() {} },
        setCancelledFiles: update => (state.cancelled = update(state.cancelled)),
        setUploadedFiles: update => (state.completed = update(state.completed)),
        axios: {
            isCancel: error => error.cancelled === true,
            CancelToken: {
                source: () => {
                    const token = {};
                    return { token, cancel: () => token.abort?.() };
                },
            },
        },
        nvAxios: {
            post: async (url, body, config) => {
                state.requests.push({ body, config });
                return post(body, config);
            },
        },
    };
    runInNewContext(compiled, context);
    const upload = item => {
        const callbacks = {
            ...item,
            onSuccess: () => {
                item.successes += 1;
                state.completed += 1;
            },
            onError: ({ error }) => {
                item.errors.push(error);
                state.completed += 1;
            },
            onProgress: ({ percent }) => item.progress.push(percent),
        };
        return context.uploads.uploadFileInChunks(callbacks);
    };
    return { ...context.uploads, upload, state, tokens };
}

test('Cancel All only cancels unfinished files after a chunked upload succeeds', async () => {
    let received;
    const pending = new Promise(resolve => (received = resolve));
    const h = harness((body, config) => {
        if (body.get('filename') === 'done.mp4') return { data: { filename: 'done.mp4', id: 'done' } };
        return new Promise((resolve, reject) => {
            config.cancelToken.abort = () => reject(Object.assign(new Error('Cancelled'), { cancelled: true }));
            received();
        });
    });
    const done = options(file('done.mp4', 25 * MiB));
    await h.upload(done);
    assert.equal(done.successes, 1);
    assert.equal(h.tokens.has('done.mp4'), false);
    const uploading = h.upload(options(file('pending.mp4', 25 * MiB)));
    await pending;
    h.handleCancelAllUploads();
    await uploading;
    assert.deepEqual([...h.state.cancelled], ['pending.mp4']);
    assert.equal(h.state.completed, 2);
    assert.equal(h.tokens.size, 0);
});

test('multipart overhead is clamped to 100 while finalization is still pending', async () => {
    let finish;
    let received;
    const pending = new Promise(resolve => (received = resolve));
    const h = harness((body, config) => {
        config.onUploadProgress({ loaded: 5 * MiB + 1024 });
        return new Promise(resolve => {
            finish = () => resolve({ data: { filename: 'small.mp4', id: 'small' } });
            received();
        });
    });
    const item = options(file('small.mp4', 5 * MiB));
    const uploading = h.upload(item);
    await pending;
    assert.deepEqual(item.progress, [100]);
    assert.equal(item.successes, 0);
    assert.equal(h.tokens.has('small.mp4'), true);
    finish();
    await uploading;
    assert.equal(item.successes, 1);
    assert.equal(h.tokens.size, 0);
});

test('multi-chunk progress stays bounded and final metadata preserves the Sensor ID without a routing header', async () => {
    const h = harness((body, config) => {
        config.onUploadProgress({ loaded: 20 * MiB + 1024 });
        return { data: { filename: 'large.mp4', id: 'large' } };
    });
    const item = options(file('large.mp4', 45 * MiB));
    await h.upload(item);
    assert.equal(h.state.requests.length, 3);
    assert.equal(item.successes, 1);
    assert.ok(item.progress.every(percent => percent >= 0 && percent <= 100));
    assert.equal(item.progress.at(-1), 100);
    const [first, middle, final] = h.state.requests;
    assert.equal(JSON.parse(first.body.get('metadata')).sensorId, 'fresh-sensor');
    assert.equal(middle.body.get('metadata'), null);
    assert.equal(JSON.parse(final.body.get('metadata')).sensorId, 'fresh-sensor');
    assert.ok(h.state.requests.every(request => !('streamId' in request.config.headers)));
});

test('empty files report failure exactly once without sending a request or retaining a cancel token', async () => {
    const h = harness(() => assert.fail('Empty file sent to backend'));
    const item = options(file('empty.mp4', 0));
    await h.upload(item);
    assert.equal(item.errors.length, 1);
    assert.match(item.errors[0].message, /empty/i);
    assert.equal(item.successes, 0);
    assert.equal(h.state.requests.length, 0);
    assert.equal(h.tokens.size, 0);
    assert.equal(h.state.completed, 1);
});

test('a failed chunk stops subsequent requests and removes its cancel token', async () => {
    const h = harness(() => {
        if (h.state.requests.length === 2) throw new Error('Transport failed');
        return { data: { filename: 'failed.mp4', id: 'failed' } };
    });
    const item = options(file('failed.mp4', 45 * MiB));
    await h.upload(item);
    assert.equal(h.state.requests.length, 2);
    assert.equal(item.errors.length, 1);
    assert.equal(item.successes, 0);
    assert.equal(h.tokens.size, 0);
});
