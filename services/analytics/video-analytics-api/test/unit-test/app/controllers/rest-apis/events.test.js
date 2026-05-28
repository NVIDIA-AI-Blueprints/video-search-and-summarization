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

describe('Events Controller', () => {
    let app;
    let eventsController;
    let eventsStub;
    let mtmcStub;
    let mockElastic;

    beforeEach(() => {
        app = express();
        app.use(express.json());

        mockElastic = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([['indexPrefix', 'mdx-']])
        };

        eventsStub = {
            getTripwireEvents: sinon.stub(),
            getRoiEvents: sinon.stub()
        };

        mtmcStub = {
            getAMREvents: sinon.stub()
        };

        const mockMdx = {
            Services: {
                Events: sinon.stub().returns(eventsStub),
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

        eventsController = proxyquire('../../../../../src/app/controllers/rest-apis/events', {
            '@nvidia-mdx/web-api-core': mockMdx,
            '../../initializers/elastic': mockElastic
        });

        const router = express.Router();
        eventsController(router);
        app.use('/events', router);

        app.use((err, req, res, next) => {
            res.status(500).json({ error: err.message });
        });
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('GET /events/tripwire', () => {
        it('should return tripwire events with 200 status', async () => {
            const mockResult = {
                tripwireEvents: [
                    { eventId: 'event1', tripwireId: 'tw1', timestamp: '2023-01-12T14:20:10.000Z' }
                ]
            };
            eventsStub.getTripwireEvents.resolves(mockResult);

            const response = await request(app)
                .get('/events/tripwire')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.tripwireEvents).to.be.an('array');
            expect(eventsStub.getTripwireEvents.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            eventsStub.getTripwireEvents.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/events/tripwire')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });

    describe('GET /events/roi', () => {
        it('should return ROI events with 200 status', async () => {
            const mockResult = {
                roiEvents: [
                    { eventId: 'event1', roiId: 'roi1', timestamp: '2023-01-12T14:20:10.000Z' }
                ]
            };
            eventsStub.getRoiEvents.resolves(mockResult);

            const response = await request(app)
                .get('/events/roi')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.roiEvents).to.be.an('array');
            expect(eventsStub.getRoiEvents.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            eventsStub.getRoiEvents.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/events/roi')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });

    describe('GET /events/amr', () => {
        it('should return AMR events with 200 status', async () => {
            const mockResult = {
                amrEvents: [
                    { eventId: 'event1', amrId: 'amr1', timestamp: '2023-01-12T14:20:10.000Z' }
                ]
            };
            mtmcStub.getAMREvents.resolves(mockResult);

            const response = await request(app)
                .get('/events/amr')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    place: 'city=test'
                });

            expect(response.status).to.equal(200);
            expect(response.body.amrEvents).to.be.an('array');
            expect(mtmcStub.getAMREvents.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            mtmcStub.getAMREvents.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/events/amr')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    place: 'city=test'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });
});
