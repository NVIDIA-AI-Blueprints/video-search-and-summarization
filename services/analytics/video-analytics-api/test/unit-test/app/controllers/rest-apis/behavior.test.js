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

describe('Behavior Controller', () => {
    let app;
    let behaviorController;
    let behaviorStub;
    let mockElastic;

    beforeEach(() => {
        app = express();
        app.use(express.json());

        mockElastic = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([['indexPrefix', 'mdx-']])
        };

        behaviorStub = {
            getBehaviors: sinon.stub(),
            getPts: sinon.stub()
        };

        const mockMdx = {
            Services: {
                Behavior: sinon.stub().returns(behaviorStub)
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

        behaviorController = proxyquire('../../../../../src/app/controllers/rest-apis/behavior', {
            '@nvidia-mdx/web-api-core': mockMdx,
            '../../initializers/elastic': mockElastic
        });

        const router = express.Router();
        behaviorController(router);
        app.use('/behavior', router);

        app.use((err, req, res, next) => {
            res.status(500).json({ error: err.message });
        });
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('GET /behavior/', () => {
        it('should return behaviors with 200 status', async () => {
            const mockResult = {
                behaviors: [
                    { objectId: 'obj1', coordinates: [[1, 2], [3, 4]] }
                ]
            };
            behaviorStub.getBehaviors.resolves(mockResult);

            const response = await request(app)
                .get('/behavior/')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.behaviors).to.be.an('array');
            expect(behaviorStub.getBehaviors.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            behaviorStub.getBehaviors.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/behavior/')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });

    describe('GET /behavior/pts', () => {
        it('should return behavior pts with 200 status', async () => {
            const mockResult = { pts: [1000, 2000, 3000] };
            behaviorStub.getPts.resolves(mockResult);

            const response = await request(app)
                .get('/behavior/pts')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.pts).to.be.an('array');
            expect(behaviorStub.getPts.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            behaviorStub.getPts.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/behavior/pts')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });
});
