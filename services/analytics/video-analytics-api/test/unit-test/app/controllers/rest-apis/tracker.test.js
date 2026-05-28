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

describe('Tracker Controller', () => {
    let app;
    let trackerController;
    let mtmcStub;
    let mockElastic;
    let mockKafka;
    let mockCache;

    beforeEach(() => {
        app = express();
        app.use(express.json());

        mockElastic = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([['indexPrefix', 'mdx-']])
        };

        mockKafka = {
            getName: () => 'Kafka',
            getClient: () => ({}),
            getAdminClient: () => ({})
        };

        mockCache = {
            get: sinon.stub().returns(false)
        };

        mtmcStub = {
            getUniqueObjectCount: sinon.stub(),
            getUniqueObjects: sinon.stub(),
            getLocationsOfMatchedBehaviors: sinon.stub(),
            getUniqueObjectCountWithLocations: sinon.stub(),
            getLastRecord: sinon.stub()
        };

        const mockMdx = {
            Services: {
                MTMC: sinon.stub().returns(mtmcStub)
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

        trackerController = proxyquire('../../../../../src/app/controllers/rest-apis/tracker', {
            '@nvidia-mdx/web-api-core': mockMdx,
            '../../initializers/elastic': mockElastic,
            '../../initializers/kafka': mockKafka,
            '../../initializers/cache': mockCache
        });

        const router = express.Router();
        trackerController(router);
        app.use('/tracker', router);

        app.use((err, req, res, next) => {
            res.status(500).json({ error: err.message });
        });
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('GET /tracker/unique-object-count', () => {
        it('should return unique object count with 200 status', async () => {
            const mockResult = { uniqueObjectCount: 42 };
            mtmcStub.getUniqueObjectCount.resolves(mockResult);

            const response = await request(app)
                .get('/tracker/unique-object-count')
                .query({ timestamp: '2023-01-12T14:20:10.000Z' });

            expect(response.status).to.equal(200);
            expect(response.body.uniqueObjectCount).to.equal(42);
            expect(mtmcStub.getUniqueObjectCount.calledOnce).to.be.true;
        });
    });

    describe('GET /tracker/unique-object-count-with-locations', () => {
        it('should return unique object count with locations', async () => {
            const mockResult = {
                place: 'city=test',
                objectCounts: [{ type: 'Person', count: 5 }],
                locationsOfObjects: [{ id: 'obj1', locations: [[10, 20]] }]
            };
            mtmcStub.getUniqueObjectCountWithLocations.resolves(mockResult);

            const response = await request(app)
                .get('/tracker/unique-object-count-with-locations')
                .query({ 
                    timestamp: '2023-01-12T14:20:10.000Z',
                    place: 'city=test'
                });

            expect(response.status).to.equal(200);
            expect(response.body.place).to.equal('city=test');
            expect(response.body.objectCounts).to.be.an('array');
            expect(mtmcStub.getUniqueObjectCountWithLocations.calledOnce).to.be.true;
        });
    });

    describe('GET /tracker/unique-objects', () => {
        it('should return unique objects with 200 status', async () => {
            const mockResult = { uniqueObjects: [{ id: 'obj1', type: 'Person' }] };
            mtmcStub.getUniqueObjects.resolves(mockResult);

            const response = await request(app)
                .get('/tracker/unique-objects')
                .query({ timestamp: '2023-01-12T14:20:10.000Z' });

            expect(response.status).to.equal(200);
            expect(response.body.uniqueObjects).to.be.an('array');
            expect(mtmcStub.getUniqueObjects.calledOnce).to.be.true;
        });
    });

    describe('GET /tracker/behavior-locations', () => {
        it('should return behavior locations with 200 status', async () => {
            const mockResult = { behaviors: [{ objectId: 'obj1', locations: [[1, 2]] }] };
            mtmcStub.getLocationsOfMatchedBehaviors.resolves(mockResult);

            const response = await request(app)
                .get('/tracker/behavior-locations')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z'
                });

            expect(response.status).to.equal(200);
            expect(response.body.behaviors).to.be.an('array');
            expect(mtmcStub.getLocationsOfMatchedBehaviors.calledOnce).to.be.true;
        });
    });

    describe('GET /tracker/last-record', () => {
        it('should return last record with 200 status', async () => {
            const mockResult = { lastRecord: { timestamp: '2023-01-12T14:20:10.000Z' } };
            mtmcStub.getLastRecord.resolves(mockResult);

            const response = await request(app)
                .get('/tracker/last-record')
                .query({ place: 'city=test' });

            expect(response.status).to.equal(200);
            expect(response.body.lastRecord).to.have.property('timestamp');
            expect(mtmcStub.getLastRecord.calledOnce).to.be.true;
        });
    });
});
