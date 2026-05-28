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

describe('Metrics Controller', () => {
    let app;
    let metricsController;
    let mockElastic;
    let lastProcessedTimestampStub;
    let tripwireEventStub;
    let occupancyStub;
    let spaceUtilizationStub;
    let behaviorStub;
    let mtmcStub;

    beforeEach(() => {
        app = express();
        app.use(express.json());

        mockElastic = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([['indexPrefix', 'mdx-']])
        };

        lastProcessedTimestampStub = {
            getLastProcessedTimestamp: sinon.stub()
        };

        tripwireEventStub = {
            getTripwireCounts: sinon.stub(),
            getTripwireHistogram: sinon.stub()
        };

        occupancyStub = {
            getOccupancyBasedOnTripwireEvents: sinon.stub(),
            resetOccupancy: sinon.stub(),
            getAverageFovOccupancy: sinon.stub(),
            getHistogramOfAverageFovOccupancy: sinon.stub(),
            getRoiOccupancy: sinon.stub(),
            getHistogramOfRoiOccupancy: sinon.stub(),
            getUniqueObjectCountInMutuallyExclusiveRois: sinon.stub(),
            getHistogramOfAverageOccupancyOfAPlace: sinon.stub()
        };

        spaceUtilizationStub = {
            getHistogramOfSpaceUtilizationMetrics: sinon.stub()
        };

        behaviorStub = {
            getAverageSpeedPerDirection: sinon.stub(),
            getFlowratePerDirection: sinon.stub(),
            getAverageSpeedWithFlowrate: sinon.stub(),
            getAverageSpeedWithTravelTime: sinon.stub(),
            getRoadSegmentSpeed: sinon.stub()
        };

        mtmcStub = {
            getOccupancyTracker: sinon.stub()
        };

        const mockMdx = {
            Metrics: {
                LastProcessedTimestamp: sinon.stub().returns(lastProcessedTimestampStub),
                TripwireEvent: sinon.stub().returns(tripwireEventStub),
                Occupancy: sinon.stub().returns(occupancyStub),
                SpaceUtilization: sinon.stub().returns(spaceUtilizationStub),
                Behavior: sinon.stub().returns(behaviorStub)
            },
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

        metricsController = proxyquire('../../../../../src/app/controllers/rest-apis/metrics', {
            '@nvidia-mdx/web-api-core': mockMdx,
            '../../initializers/elastic': mockElastic
        });

        const router = express.Router();
        metricsController(router);
        app.use('/metrics', router);

        app.use((err, req, res, next) => {
            res.status(500).json({ error: err.message });
        });
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('GET /metrics/last-processed-timestamp', () => {
        it('should return last processed timestamp with 200 status', async () => {
            const mockResult = { latestTimestamp: { sensorId: 'sensor1', timestamp: '2023-01-12T14:20:10.000Z' } };
            lastProcessedTimestampStub.getLastProcessedTimestamp.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/last-processed-timestamp')
                .query({ sensorId: 'sensor1' });

            expect(response.status).to.equal(200);
            expect(response.body.latestTimestamp).to.have.property('timestamp');
            expect(lastProcessedTimestampStub.getLastProcessedTimestamp.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/tripwire/counts', () => {
        it('should return tripwire counts with 200 status', async () => {
            const mockResult = {
                tripwireMetrics: [{ tripwireId: 'tw1', inCount: 10, outCount: 5 }]
            };
            tripwireEventStub.getTripwireCounts.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/tripwire/counts')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z'
                });

            expect(response.status).to.equal(200);
            expect(response.body.tripwireMetrics).to.be.an('array');
            expect(tripwireEventStub.getTripwireCounts.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/tripwire/histogram', () => {
        it('should return tripwire histogram with 200 status', async () => {
            const mockResult = {
                bucketSizeInSec: 600,
                tripwires: [{ tripwireId: 'tw1', histogram: [] }]
            };
            tripwireEventStub.getTripwireHistogram.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/tripwire/histogram')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z'
                });

            expect(response.status).to.equal(200);
            expect(response.body.bucketSizeInSec).to.equal(600);
            expect(tripwireEventStub.getTripwireHistogram.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/occupancy/tripwire', () => {
        it('should return occupancy based on tripwire events with 200 status', async () => {
            const mockResult = { occupancy: 15, details: [] };
            occupancyStub.getOccupancyBasedOnTripwireEvents.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/occupancy/tripwire')
                .query({ timestamp: '2023-01-12T14:20:10.000Z' });

            expect(response.status).to.equal(200);
            expect(response.body.occupancy).to.equal(15);
            expect(occupancyStub.getOccupancyBasedOnTripwireEvents.calledOnce).to.be.true;
        });
    });

    describe('POST /metrics/occupancy/reset', () => {
        it('should reset occupancy with 201 status', async () => {
            const mockResult = { success: true };
            occupancyStub.resetOccupancy.resolves(mockResult);

            const response = await request(app)
                .post('/metrics/occupancy/reset')
                .send({ tripwireId: 'tw1', occupancyReset: 0 });

            expect(response.status).to.equal(201);
            expect(response.body.success).to.be.true;
            expect(occupancyStub.resetOccupancy.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/occupancy/fov', () => {
        it('should return FOV occupancy with 200 status', async () => {
            const mockResult = { fovOccupancy: [{ sensorId: 'sensor1', averageCount: 5 }] };
            occupancyStub.getAverageFovOccupancy.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/occupancy/fov')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z'
                });

            expect(response.status).to.equal(200);
            expect(response.body.fovOccupancy).to.be.an('array');
            expect(occupancyStub.getAverageFovOccupancy.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/occupancy/fov/histogram', () => {
        it('should return FOV occupancy histogram with 200 status', async () => {
            const mockResult = { bucketSizeInSec: 600, histogram: [] };
            occupancyStub.getHistogramOfAverageFovOccupancy.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/occupancy/fov/histogram')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z'
                });

            expect(response.status).to.equal(200);
            expect(response.body.bucketSizeInSec).to.equal(600);
            expect(occupancyStub.getHistogramOfAverageFovOccupancy.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/occupancy/roi', () => {
        it('should return ROI occupancy with 200 status', async () => {
            const mockResult = { rois: [{ roiId: 'roi1', count: 5 }] };
            occupancyStub.getRoiOccupancy.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/occupancy/roi')
                .query({ timestamp: '2023-01-12T14:20:10.000Z' });

            expect(response.status).to.equal(200);
            expect(response.body.rois).to.be.an('array');
            expect(occupancyStub.getRoiOccupancy.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/occupancy/roi/histogram', () => {
        it('should return ROI occupancy histogram with 200 status', async () => {
            const mockResult = { bucketSizeInSec: 600, rois: [] };
            occupancyStub.getHistogramOfRoiOccupancy.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/occupancy/roi/histogram')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z'
                });

            expect(response.status).to.equal(200);
            expect(response.body.bucketSizeInSec).to.equal(600);
            expect(occupancyStub.getHistogramOfRoiOccupancy.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/occupancy/roi/mutually-exclusive', () => {
        it('should return unique object count in mutually exclusive ROIs with 200 status', async () => {
            const mockResult = { objectDetails: [] };
            occupancyStub.getUniqueObjectCountInMutuallyExclusiveRois.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/occupancy/roi/mutually-exclusive')
                .query({ timestamp: '2023-01-12T14:20:10.000Z' });

            expect(response.status).to.equal(200);
            expect(response.body.objectDetails).to.be.an('array');
            expect(occupancyStub.getUniqueObjectCountInMutuallyExclusiveRois.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/occupancy/tracker', () => {
        it('should return tracker occupancy with 200 status', async () => {
            const mockResult = { trackerOccupancy: [{ type: 'Person', count: 10 }] };
            mtmcStub.getOccupancyTracker.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/occupancy/tracker')
                .query({ timestamp: '2023-01-12T14:20:10.000Z' });

            expect(response.status).to.equal(200);
            expect(response.body.trackerOccupancy).to.be.an('array');
            expect(mtmcStub.getOccupancyTracker.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/occupancy/tracker/histogram', () => {
        it('should return tracker occupancy histogram with 200 status', async () => {
            const mockResult = { bucketSizeInSec: 600, histogram: [] };
            occupancyStub.getHistogramOfAverageOccupancyOfAPlace.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/occupancy/tracker/histogram')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z'
                });

            expect(response.status).to.equal(200);
            expect(response.body.bucketSizeInSec).to.equal(600);
            expect(occupancyStub.getHistogramOfAverageOccupancyOfAPlace.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/space-utilization/histogram', () => {
        it('should return space utilization histogram with 200 status', async () => {
            const mockResult = { bucketSizeInSec: 600, rois: [] };
            spaceUtilizationStub.getHistogramOfSpaceUtilizationMetrics.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/space-utilization/histogram')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z'
                });

            expect(response.status).to.equal(200);
            expect(response.body.bucketSizeInSec).to.equal(600);
            expect(spaceUtilizationStub.getHistogramOfSpaceUtilizationMetrics.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/average-speed', () => {
        it('should return average speed per direction with 200 status', async () => {
            const mockResult = { metrics: [{ direction: 'North', averageSpeed: '25.5 mph' }] };
            behaviorStub.getAverageSpeedPerDirection.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/average-speed')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z'
                });

            expect(response.status).to.equal(200);
            expect(response.body.metrics).to.be.an('array');
            expect(behaviorStub.getAverageSpeedPerDirection.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/flowrate', () => {
        it('should return flowrate per direction with 200 status', async () => {
            const mockResult = { metrics: [{ direction: 'North', flowrate: 100 }] };
            behaviorStub.getFlowratePerDirection.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/flowrate')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z'
                });

            expect(response.status).to.equal(200);
            expect(response.body.metrics).to.be.an('array');
            expect(behaviorStub.getFlowratePerDirection.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/average-speed-with-flowrate', () => {
        it('should return average speed with flowrate with 200 status', async () => {
            const mockResult = { metrics: [{ direction: 'North', averageSpeed: '25.5 mph', flowrate: 100 }] };
            behaviorStub.getAverageSpeedWithFlowrate.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/average-speed-with-flowrate')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z'
                });

            expect(response.status).to.equal(200);
            expect(response.body.metrics).to.be.an('array');
            expect(behaviorStub.getAverageSpeedWithFlowrate.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/average-speed-with-travel-time', () => {
        it('should return average speed with travel time with 200 status', async () => {
            const mockResult = { metrics: [{ averageSpeed: '25.5 mph', travelTime: '5 min' }] };
            behaviorStub.getAverageSpeedWithTravelTime.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/average-speed-with-travel-time')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z'
                });

            expect(response.status).to.equal(200);
            expect(response.body.metrics).to.be.an('array');
            expect(behaviorStub.getAverageSpeedWithTravelTime.calledOnce).to.be.true;
        });
    });

    describe('GET /metrics/road-network/segment-speed', () => {
        it('should return road segment speed with 200 status', async () => {
            const mockResult = { segments: [{ segmentId: 'seg1', speed: 30 }] };
            behaviorStub.getRoadSegmentSpeed.resolves(mockResult);

            const response = await request(app)
                .get('/metrics/road-network/segment-speed')
                .query({ timestamp: '2023-01-12T14:20:10.000Z' });

            expect(response.status).to.equal(200);
            expect(response.body.segments).to.be.an('array');
            expect(behaviorStub.getRoadSegmentSpeed.calledOnce).to.be.true;
        });
    });
});
