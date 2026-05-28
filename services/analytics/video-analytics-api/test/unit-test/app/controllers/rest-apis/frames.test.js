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

describe('Frames Controller', () => {
    let app;
    let framesController;
    let framesStub;
    let mockElastic;

    beforeEach(() => {
        app = express();
        app.use(express.json());

        mockElastic = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([['indexPrefix', 'mdx-']])
        };

        framesStub = {
            getFrames: sinon.stub(),
            getEnhancedFrames: sinon.stub(),
            getBevFrames: sinon.stub(),
            getMaxConfidenceDetectionOfObjects: sinon.stub(),
            getProximityClusters: sinon.stub(),
            getPts: sinon.stub(),
            getAlerts: sinon.stub()
        };

        const mockMdx = {
            Services: {
                Frames: sinon.stub().returns(framesStub)
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

        framesController = proxyquire('../../../../../src/app/controllers/rest-apis/frames', {
            '@nvidia-mdx/web-api-core': mockMdx,
            '../../initializers/elastic': mockElastic
        });

        const router = express.Router();
        framesController(router);
        app.use('/frames', router);

        app.use((err, req, res, next) => {
            res.status(500).json({ error: err.message });
        });
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('GET /frames/', () => {
        it('should return frames with 200 status', async () => {
            const mockResult = {
                frames: [
                    { timestamp: '2023-01-12T14:20:10.000Z', objects: [] }
                ]
            };
            framesStub.getFrames.resolves(mockResult);

            const response = await request(app)
                .get('/frames/')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.frames).to.be.an('array');
            expect(framesStub.getFrames.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            framesStub.getFrames.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/frames/')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });

    describe('GET /frames/enhanced', () => {
        it('should return enhanced frames with 200 status', async () => {
            const mockResult = {
                enhancedFrames: [
                    { timestamp: '2023-01-12T14:20:10.000Z', objects: [] }
                ]
            };
            framesStub.getEnhancedFrames.resolves(mockResult);

            const response = await request(app)
                .get('/frames/enhanced')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.enhancedFrames).to.be.an('array');
            expect(framesStub.getEnhancedFrames.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            framesStub.getEnhancedFrames.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/frames/enhanced')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });

    describe('GET /frames/bev', () => {
        it('should return bev frames with 200 status', async () => {
            const mockResult = {
                bevFrames: [
                    { timestamp: '2023-01-12T14:20:10.000Z', objects: [] }
                ]
            };
            framesStub.getBevFrames.resolves(mockResult);

            const response = await request(app)
                .get('/frames/bev')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    place: 'city=test'
                });

            expect(response.status).to.equal(200);
            expect(response.body.bevFrames).to.be.an('array');
            expect(framesStub.getBevFrames.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            framesStub.getBevFrames.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/frames/bev')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    place: 'city=test'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });

    describe('GET /frames/high-confidence-objects', () => {
        it('should return high confidence objects with 200 status', async () => {
            const mockResult = {
                objects: [
                    { objectId: 'obj1', confidence: 0.95 }
                ]
            };
            framesStub.getMaxConfidenceDetectionOfObjects.resolves(mockResult);

            const response = await request(app)
                .get('/frames/high-confidence-objects')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.objects).to.be.an('array');
            expect(framesStub.getMaxConfidenceDetectionOfObjects.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            framesStub.getMaxConfidenceDetectionOfObjects.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/frames/high-confidence-objects')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });

    describe('GET /frames/proximity-detection', () => {
        it('should return proximity clusters with 200 status', async () => {
            const mockResult = {
                proximityClusters: [
                    { clusterId: 'cluster1', objects: ['obj1', 'obj2'] }
                ]
            };
            framesStub.getProximityClusters.resolves(mockResult);

            const response = await request(app)
                .get('/frames/proximity-detection')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.proximityClusters).to.be.an('array');
            expect(framesStub.getProximityClusters.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            framesStub.getProximityClusters.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/frames/proximity-detection')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });

    describe('GET /frames/pts', () => {
        it('should return pts with 200 status', async () => {
            const mockResult = { pts: [1000, 2000, 3000] };
            framesStub.getPts.resolves(mockResult);

            const response = await request(app)
                .get('/frames/pts')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.pts).to.be.an('array');
            expect(framesStub.getPts.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            framesStub.getPts.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/frames/pts')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });

    describe('GET /frames/alerts', () => {
        it('should return alerts with 200 status', async () => {
            const mockResult = {
                sensorId: 'sensor123',
                objects: [
                    { objectId: 'obj1', type: 'Person' }
                ]
            };
            framesStub.getAlerts.resolves(mockResult);

            const response = await request(app)
                .get('/frames/alerts')
                .query({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.sensorId).to.equal('sensor123');
            expect(response.body.objects).to.be.an('array');
            expect(framesStub.getAlerts.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            framesStub.getAlerts.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/frames/alerts')
                .query({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });
});
