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

const sinon = require('sinon');
const { expect } = require('chai');
const express = require('express');
const request = require('supertest');
const proxyquire = require('proxyquire').noCallThru().noPreserveCache();

describe('Sensor Controller', () => {
    let app;
    let sensorController;
    let sensorStub;
    let mockElastic;

    beforeEach(() => {
        app = express();
        app.use(express.json());

        mockElastic = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([['indexPrefix', 'mdx-']])
        };

        sensorStub = {
            lookup: sinon.stub()
        };

        const mockMdx = {
            Services: {
                Sensor: sinon.stub().returns(sensorStub)
            },
            Utils: {
                Utils: {
                    expressAsyncWrapper: (fn) => async (req, res, next) => {
                        try {
                            await fn(req, res, next);
                        } catch (error) {
                            next(error);
                        }
                    }
                }
            }
        };

        sensorController = proxyquire('../../../../../src/app/controllers/rest-apis/sensor', {
            '@nvidia-mdx/web-api-core': mockMdx,
            '../../initializers/elastic': mockElastic
        });

        const router = express.Router();
        sensorController(router);
        app.use('/sensor', router);

        app.use((err, req, res, next) => {
            res.status(500).json({ error: err.message });
        });
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('GET /sensor/lookup', () => {
        it('should return sensor lookup result with 200 status', async () => {
            const mockResult = {
                sensors: [
                    { sensorId: 'sensor123', place: 'city=test' }
                ]
            };
            sensorStub.lookup.resolves(mockResult);

            const response = await request(app)
                .get('/sensor/lookup')
                .query({
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.sensors).to.be.an('array');
            expect(sensorStub.lookup.calledOnce).to.be.true;
        });

        it('should handle lookup with place query', async () => {
            const mockResult = {
                sensors: [
                    { sensorId: 'sensor123', place: 'city=test' },
                    { sensorId: 'sensor456', place: 'city=test' }
                ]
            };
            sensorStub.lookup.resolves(mockResult);

            const response = await request(app)
                .get('/sensor/lookup')
                .query({
                    place: 'city=test'
                });

            expect(response.status).to.equal(200);
            expect(response.body.sensors).to.be.an('array').with.length(2);
            expect(sensorStub.lookup.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            sensorStub.lookup.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/sensor/lookup')
                .query({
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });
});
