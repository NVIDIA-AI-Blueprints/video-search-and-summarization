/*
 * SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
 * Regression checks for the synced warehouse contracts and integration runner.
 */

'use strict';

const { expect } = require('chai');
const sinon = require('sinon');
const proxyquire = require('proxyquire').noCallThru();
const { EventEmitter } = require('events');
const incidents = require('../../../integration-test/scripts/controllers/rest-apis/incidents');
const behavior = require('../../../integration-test/scripts/controllers/rest-apis/behavior');
const config = require('../../../integration-test/scripts/controllers/rest-apis/config');
const runner = require('../../../integration-test/scripts/run_integration_tests');
const fs = require('fs');
const path = require('path');

const PROFILE_VARIABLES = ['COMPOSE_PROFILE', 'BP_PROFILE', 'MODE', 'DEPLOY_PROFILE', 'WAREHOUSE_VLM_ALERTS_VERIFICATION'];
const CONSTANTS = {
    ...runner.getControllerConstants('http://example.test'),
    FROM_TS: '2026-02-14T10:18:00.000Z',
    TO_TS: '2026-02-14T10:19:00.000Z'
};

function incident(overrides = {}) {
    return {
        Id: 'incident-1', sensorId: 'Camera_02', category: 'Proximity Violation',
        timestamp: CONSTANTS.FROM_TS, end: '2026-02-14T10:18:10.000Z',
        objectIds: ['1'], frameIds: [], info: { isComplete: 'true' }, ...overrides
    };
}

function body(records) {
    return JSON.stringify({ incidents: records });
}

function loadDump(name) {
    return fs.readFileSync(path.join(__dirname, '../../../integration-test/elasticsearch_data_dump', name), 'utf8')
        .trim().split('\n').map((line) => JSON.parse(line)._source);
}

function findTest(tests, name) {
    return tests.find((test) => test.name === name);
}

describe('Warehouse integration contracts', () => {
    let savedEnvironment;
    beforeEach(() => {
        savedEnvironment = Object.fromEntries(PROFILE_VARIABLES.map((key) => [key, process.env[key]]));
        PROFILE_VARIABLES.forEach((key) => { delete process.env[key]; });
    });
    afterEach(() => {
        for (const [key, value] of Object.entries(savedEnvironment)) {
            if (value === undefined) delete process.env[key];
            else process.env[key] = value;
        }
        sinon.restore();
    });

    it('requires exactly one behavior for the primary sensor, place and alternate sensor', () => {
        const tests = behavior.getTests(CONSTANTS).filter((test) => test.expectedStatus === 200 && new URL(test.path, 'http://example.test').searchParams.get('maxResultSize') === '1');
        expect(tests).to.have.length(3);
        for (const test of tests) {
            expect(test.validate(JSON.stringify({ behaviors: [] }))).to.be.a('string');
            expect(test.validate(JSON.stringify({ behaviors: [{}] }))).to.equal(null);
            expect(test.validate(JSON.stringify({ behaviors: [{}, {}] }))).to.be.a('string');
        }
    });

    it('rejects empty calibration while retaining local config and mutation tests', () => {
        const tests = config.getTests(CONSTANTS);
        const calibration = findTest(tests, 'GET /config/calibration');
        expect(calibration.validate('{}')).to.equal('calibrationType is empty');
        expect(calibration.validate('{"calibrationType":"2d","sensors":[]}')).to.include('at least one sensor');
        expect(calibration.validate('{"calibrationType":"2d","sensors":[{"id":"Camera"}]}')).to.equal(null);
        expect(calibration.failRemainingControllerTestsOnFailure).to.be.a('string');
        expect(tests.some((test) => test.path === '/config/road-network')).to.equal(true);
        expect(tests.some((test) => test.method === 'POST')).to.equal(true);
    });

    it('validates protobuf fields and camelCase info keys', () => {
        const test = incidents.getTests(CONSTANTS)[0];
        expect(test.validate(body([incident()]))).to.equal(null);
        for (const badIncident of [
            incident({ unknownField: true }), incident({ objectIds: [1] }),
            incident({ timestamp: 'invalid' }), incident({ info: { primary_object_id: '1' } })
        ]) {
            expect(() => test.validate(body([badIncident]))).to.throw();
        }
    });

    it('keeps VLM verification gated and supports explicit opt-in', () => {
        expect(incidents.getTests(CONSTANTS)).to.have.length(5);
        process.env.WAREHOUSE_VLM_ALERTS_VERIFICATION = 'true';
        expect(incidents.getTests(CONSTANTS)).to.have.length(10);
    });

    it('passes the paired source/VLM dump through every warehouse incident validator', async () => {
        process.env.COMPOSE_PROFILE = 'bp_wh_2d';
        const records = loadDump('warehouse-verification-incidents.json');
        const source = records.filter((record) => record.type === 'mdx-incidents');
        const verified = records.filter((record) => record.type === 'mdx-vlm-incidents');
        for (const test of incidents.getTests(CONSTANTS)) {
            if (!test.validate) continue;
            const query = new URL(test.path, 'http://example.test').searchParams;
            let records = query.get('vlmVerified') === 'true' ? verified : source;
            if (query.has('category')) records = records.filter((record) => record.category === query.get('category'));
            const result = runner.interpretCustomValidation(await test.validate(body(records)));
            expect(result.error, test.name).to.equal(null);
        }
    });

    it('reports missing verification IDs and mismatched proximity IDs', async () => {
        process.env.COMPOSE_PROFILE = 'bp_wh_2d';
        const tests = incidents.getTests(CONSTANTS);
        const nearMiss = incident({ Id: 'near-miss', category: 'Near Miss Violation' });
        await findTest(tests, 'GET /incidents (no sensorId/place) returns 200')
            .validate(body([incident({ Id: 'missed' })]));
        const coverage = await findTest(tests, 'GET /incidents (VLM verification coverage)').validate(body([nearMiss]));
        expect(coverage.error).to.include('missed');
        await findTest(tests, 'GET /incidents (unverified proximity violations)').validate(body([incident()]));
        const mismatch = await findTest(tests, 'GET /incidents (VLM Near Miss IDs match active proximity violations)')
            .validate(body([nearMiss]));
        expect(mismatch).to.include('without Near Miss match');
    });

    it('deduplicates incidents crossing the pagination boundary', async () => {
        process.env.COMPOSE_PROFILE = 'bp_wh_2d';
        const records = Array.from({ length: 10000 }, (_, index) => incident({ Id: String(index) }));
        const request = sinon.stub();
        request.onFirstCall().resolves({ statusCode: 200, body: body(records.slice(0, 5001)) });
        request.onSecondCall().resolves({ statusCode: 200, body: body(records.slice(5000)) });
        const test = findTest(incidents.getTests({ ...CONSTANTS, request }), 'GET /incidents (no sensorId/place) returns 200');
        const result = await test.validate(body(records));
        expect(result.details).to.equal('10000 incident(s) match protobuf Incident schema');
        expect(request.callCount).to.equal(2);
    });

    it('fails when a saturated incident window cannot be split', async () => {
        process.env.COMPOSE_PROFILE = 'bp_wh_2d';
        const test = findTest(incidents.getTests({ ...CONSTANTS, TO_TS: CONSTANTS.FROM_TS }), 'GET /incidents (no sensorId/place) returns 200');
        try {
            await test.validate(body(Array.from({ length: 10000 }, () => incident())));
            throw new Error('expected saturated-window failure');
        } catch (error) {
            expect(error.message).to.include('cannot split saturated incident window');
        }
    });
});

describe('Integration controller runner', () => {
    function stubRunner(statusCode, responseBody) {
        const http = {
            request: (_options, callback) => {
                const req = new EventEmitter();
                req.setTimeout = () => {};
                req.end = () => {
                    const res = new EventEmitter();
                    res.statusCode = statusCode;
                    callback(res);
                    res.emit('data', responseBody);
                    res.emit('end');
                };
                return req;
            }
        };
        return proxyquire('../../../integration-test/scripts/run_integration_tests', { http });
    }

    it('awaits custom validators and preserves diagnostic details', async () => {
        const localRunner = stubRunner(200, '{}');
        const validateOutput = sinon.stub().returns({ valid: true });
        const result = await localRunner.runControllerTest('http://example.test', {
            name: 'async validator', path: '/incidents?maxResultSize=10000', method: 'GET', expectedStatus: 200,
            validate: async () => ({ details: '4 incidents checked' })
        }, validateOutput);
        expect(result.error).to.equal(null);
        expect(result.details).to.equal('4 incidents checked');
        expect(validateOutput.firstCall.args[2]).to.equal('/incidents');
    });

    it('reports structured and string validation failures', async () => {
        const localRunner = stubRunner(200, '{}');
        for (const validation of ['invalid incident', { error: 'missing verification', details: '1 missed' }]) {
            const result = await localRunner.runControllerTest('http://example.test', {
                name: 'failure', path: '/incidents', method: 'GET', expectedStatus: 200,
                validate: async () => validation
            }, () => { throw new Error('schema validation must not run after custom failure'); });
            expect(result.error).to.equal(typeof validation === 'string' ? validation : validation.error);
        }
    });

    it('rejects unexpected HTTP statuses before invoking validators', async () => {
        const localRunner = stubRunner(500, '{}');
        const validate = sinon.stub();
        const result = await localRunner.runControllerTest('http://example.test', {
            name: 'wrong status', path: '/incidents', method: 'GET', expectedStatuses: [200, 204], validate
        }, sinon.stub());
        expect(result.error).to.equal('expected 200 or 204, got 500');
        expect(validate.called).to.equal(false);
    });
});
