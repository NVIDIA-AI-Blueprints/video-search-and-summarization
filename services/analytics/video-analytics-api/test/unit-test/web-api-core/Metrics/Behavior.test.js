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
const Behavior = require('../../../../src/web-api-core/Metrics/Behavior');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const Calibration = require('../../../../src/web-api-core/Services/Calibration');
const RoadNetwork = require('../../../../src/web-api-core/Services/RoadNetwork');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');

describe('Metrics.Behavior', () => {

    describe('getSpeedUnit', () => {
        it('should return "mph" for cartesian calibration type', () => {
            const result = Behavior.getSpeedUnit('cartesian');
            expect(result).to.equal('mph');
        });

        it('should return "mph" for geo calibration type', () => {
            const result = Behavior.getSpeedUnit('geo');
            expect(result).to.equal('mph');
        });

        it('should return "pixels/sec" for image calibration type', () => {
            const result = Behavior.getSpeedUnit('image');
            expect(result).to.equal('pixels/sec');
        });

        it('should throw InvalidInputError for invalid calibration type', () => {
            expect(() => {
                Behavior.getSpeedUnit('invalid');
            }).to.throw(InvalidInputError, 'Invalid calibration type: invalid.');
        });
    });

    describe('getAverageSpeedPerDirection', () => {
        let behavior;
        let elasticDb;
        let elasticsearchStub;
        let calibrationStub;

        beforeEach(() => {
            behavior = new Behavior();
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => ({}),
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
            calibrationStub = sinon.stub(Calibration.prototype, 'getCalibrationType');
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should return metrics when valid input with sensorId is provided', async () => {
            const input = {
                fromTimestamp: '2025-01-10T11:05:10.000Z',
                toTimestamp: '2025-01-10T11:10:10.000Z',
                sensorId: 'sensor123'
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        directions: {
                            buckets: [
                                { key: 'North', averageSpeed: { value: 25 }, doc_count: 10 },
                                { key: 'South', averageSpeed: { value: 30 }, doc_count: 15 }
                            ]
                        }
                    }
                }
            });
            calibrationStub.resolves('cartesian');

            const result = await behavior.getAverageSpeedPerDirection(elasticDb, input);

            expect(result).to.have.property('metrics');
            expect(result.metrics).to.be.an('array').with.length(2);
            expect(result.metrics[0]).to.deep.include({ direction: 'North' });
            expect(result.metrics[0].averageSpeed).to.include('mph');
            expect(result.metrics[1]).to.deep.include({ direction: 'South' });
        });

        it('should return metrics when valid input with place is provided', async () => {
            const input = {
                fromTimestamp: '2025-01-10T11:05:10.000Z',
                toTimestamp: '2025-01-10T11:10:10.000Z',
                place: 'city=abc/intersection=xyz'
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        directions: {
                            buckets: []
                        }
                    }
                }
            });
            calibrationStub.resolves('cartesian');

            const result = await behavior.getAverageSpeedPerDirection(elasticDb, input);

            expect(result).to.have.property('metrics');
        });

        it('should throw BadRequestError when required timestamps are missing', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await behavior.getAverageSpeedPerDirection(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw BadRequestError when neither sensorId nor place is provided', async () => {
            const input = {
                fromTimestamp: '2025-01-10T11:05:10.000Z',
                toTimestamp: '2025-01-10T11:10:10.000Z'
            };

            try {
                await behavior.getAverageSpeedPerDirection(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Input should have either 'sensorId' or 'place'");
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                fromTimestamp: '2025-01-10T12:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                sensorId: 'sensor123'
            };

            try {
                await behavior.getAverageSpeedPerDirection(elasticDb, input);
                throw new Error('Expected InvalidInputError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };

            const input = {
                fromTimestamp: '2025-01-10T11:05:10.000Z',
                toTimestamp: '2025-01-10T11:10:10.000Z',
                sensorId: 'sensor123'
            };

            try {
                await behavior.getAverageSpeedPerDirection(unsupportedDb, input);
                throw new Error('Expected InternalServerError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should return empty metrics when index is absent', async () => {
            const input = {
                fromTimestamp: '2025-01-10T11:05:10.000Z',
                toTimestamp: '2025-01-10T11:10:10.000Z',
                sensorId: 'sensor123'
            };

            elasticsearchStub.resolves({ indexAbsent: true });
            calibrationStub.resolves('cartesian');

            const result = await behavior.getAverageSpeedPerDirection(elasticDb, input);

            expect(result).to.have.property('metrics');
            expect(result.metrics).to.be.an('array').that.is.empty;
        });

        it('should use default calibration type when none is returned', async () => {
            const input = {
                fromTimestamp: '2025-01-10T11:05:10.000Z',
                toTimestamp: '2025-01-10T11:10:10.000Z',
                sensorId: 'sensor123'
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        directions: {
                            buckets: [{ key: 'North', averageSpeed: { value: 25 }, doc_count: 10 }]
                        }
                    }
                }
            });
            calibrationStub.resolves(null);

            const result = await behavior.getAverageSpeedPerDirection(elasticDb, input);

            expect(result.metrics[0].averageSpeed).to.include('mph');
        });
    });

    describe('getFlowratePerDirection', () => {
        let behavior;
        let elasticDb;
        let elasticsearchStub;

        beforeEach(() => {
            behavior = new Behavior();
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => ({}),
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should return flowrate metrics when valid input with sensorId is provided', async () => {
            const input = {
                toTimestamp: '2025-01-10T11:10:05.000Z',
                sensorId: 'sensor123',
                flowrateUnit: '/min'
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        sensors: {
                            buckets: [
                                {
                                    key: 'sensor123',
                                    directions: {
                                        buckets: [
                                            { key: 'North', uniqueObjectCount: { value: 10 } },
                                            { key: 'South', uniqueObjectCount: { value: 15 } }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await behavior.getFlowratePerDirection(elasticDb, input);

            expect(result).to.have.property('metrics');
            expect(result.metrics).to.be.an('array').with.length(2);
            expect(result.metrics[0]).to.have.property('direction', 'North');
            expect(result.metrics[1]).to.have.property('direction', 'South');
        });

        it('should return flowrate metrics when valid input with place is provided', async () => {
            const input = {
                toTimestamp: '2025-01-10T11:10:05.000Z',
                place: 'city=abc/intersection=xyz',
                flowrateUnit: '/sec'
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        sensors: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await behavior.getFlowratePerDirection(elasticDb, input);

            expect(result).to.have.property('metrics');
        });

        it('should throw BadRequestError when toTimestamp is missing', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await behavior.getFlowratePerDirection(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('toTimestamp');
            }
        });

        it('should throw BadRequestError when neither sensorId nor place is provided', async () => {
            const input = {
                toTimestamp: '2025-01-10T11:10:05.000Z'
            };

            try {
                await behavior.getFlowratePerDirection(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Input should have either 'sensorId' or 'place'");
            }
        });

        it('should throw InvalidInputError for invalid toTimestamp', async () => {
            const input = {
                toTimestamp: 'invalid-timestamp',
                sensorId: 'sensor123'
            };

            try {
                await behavior.getFlowratePerDirection(elasticDb, input);
                throw new Error('Expected InvalidInputError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('Invalid toTimestamp');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };

            const input = {
                toTimestamp: '2025-01-10T11:10:05.000Z',
                sensorId: 'sensor123'
            };

            try {
                await behavior.getFlowratePerDirection(unsupportedDb, input);
                throw new Error('Expected InternalServerError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should calculate flowrate for /hr unit', async () => {
            const input = {
                toTimestamp: '2025-01-10T11:10:05.000Z',
                sensorId: 'sensor123',
                flowrateUnit: '/hr',
                timeWindowSizeInSec: 60
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        sensors: {
                            buckets: [
                                {
                                    key: 'sensor123',
                                    directions: {
                                        buckets: [
                                            { key: 'North', uniqueObjectCount: { value: 10 } }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await behavior.getFlowratePerDirection(elasticDb, input);

            expect(result.metrics[0].flowrate).to.include('/hr');
        });

        it('should calculate flowrate for /sec unit', async () => {
            const input = {
                toTimestamp: '2025-01-10T11:10:05.000Z',
                sensorId: 'sensor123',
                flowrateUnit: '/sec',
                timeWindowSizeInSec: 60
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        sensors: {
                            buckets: [
                                {
                                    key: 'sensor123',
                                    directions: {
                                        buckets: [
                                            { key: 'North', uniqueObjectCount: { value: 10 } }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await behavior.getFlowratePerDirection(elasticDb, input);

            expect(result.metrics[0].flowrate).to.include('/sec');
        });

        it('should return empty metrics when index is absent', async () => {
            const input = {
                toTimestamp: '2025-01-10T11:10:05.000Z',
                sensorId: 'sensor123'
            };

            elasticsearchStub.resolves({ indexAbsent: true });

            const result = await behavior.getFlowratePerDirection(elasticDb, input);

            expect(result).to.have.property('metrics');
            expect(result.metrics).to.be.an('array').that.is.empty;
        });

        it('should handle multiple sensors with directions', async () => {
            const input = {
                toTimestamp: '2025-01-10T11:10:05.000Z',
                place: 'city=abc',
                flowrateUnit: '/min',
                timeWindowSizeInSec: 60
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        sensors: {
                            buckets: [
                                {
                                    key: 'sensor1',
                                    directions: {
                                        buckets: [
                                            { key: 'North', uniqueObjectCount: { value: 10 } },
                                            { key: 'South', uniqueObjectCount: { value: 5 } }
                                        ]
                                    }
                                },
                                {
                                    key: 'sensor2',
                                    directions: {
                                        buckets: [
                                            { key: 'North', uniqueObjectCount: { value: 8 } }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await behavior.getFlowratePerDirection(elasticDb, input);

            expect(result).to.have.property('metrics');
            expect(result.metrics).to.be.an('array');
        });
    });

    describe('getAverageSpeedWithFlowrate', () => {
        let behavior;
        let elasticDb;
        let elasticsearchStub;
        let calibrationStub;

        beforeEach(() => {
            behavior = new Behavior();
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => ({}),
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
            calibrationStub = sinon.stub(Calibration.prototype, 'getCalibrationType');
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should throw BadRequestError when required timestamps are missing', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await behavior.getAverageSpeedWithFlowrate(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw BadRequestError when neither sensorId nor place is provided', async () => {
            const input = {
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z'
            };

            try {
                await behavior.getAverageSpeedWithFlowrate(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('sensorId');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                fromTimestamp: '2025-01-10T12:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                sensorId: 'sensor123'
            };

            try {
                await behavior.getAverageSpeedWithFlowrate(elasticDb, input);
                throw new Error('Expected InvalidInputError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };

            const input = {
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                sensorId: 'sensor123'
            };

            try {
                await behavior.getAverageSpeedWithFlowrate(unsupportedDb, input);
                throw new Error('Expected InternalServerError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should return combined metrics when valid input is provided', async () => {
            const input = {
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                sensorId: 'sensor123',
                flowrateUnit: '/min'
            };

            // First call for average speed
            elasticsearchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        directions: {
                            buckets: [
                                { key: 'North', averageSpeed: { value: 25 }, doc_count: 10 }
                            ]
                        }
                    }
                }
            });

            // Second call for flowrate
            elasticsearchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        sensors: {
                            buckets: [
                                {
                                    key: 'sensor123',
                                    directions: {
                                        buckets: [
                                            { key: 'North', uniqueObjectCount: { value: 10 } }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            calibrationStub.resolves('cartesian');

            const result = await behavior.getAverageSpeedWithFlowrate(elasticDb, input);

            expect(result).to.have.property('metrics');
        });

        it('should handle directions only in avgSpeed (avgSpeedExclusiveDir)', async () => {
            const input = {
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                sensorId: 'sensor123',
                flowrateUnit: '/min'
            };

            // Average speed has North and South
            elasticsearchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        directions: {
                            buckets: [
                                { key: 'North', averageSpeed: { value: 25 }, doc_count: 10 },
                                { key: 'South', averageSpeed: { value: 30 }, doc_count: 15 }
                            ]
                        }
                    }
                }
            });

            // Flowrate only has North (South is exclusive to avgSpeed)
            elasticsearchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        sensors: {
                            buckets: [
                                {
                                    key: 'sensor123',
                                    directions: {
                                        buckets: [
                                            { key: 'North', uniqueObjectCount: { value: 10 } }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            calibrationStub.resolves('cartesian');

            const result = await behavior.getAverageSpeedWithFlowrate(elasticDb, input);

            expect(result).to.have.property('metrics');
            expect(result.metrics).to.be.an('array');
            // Should have both North (common) and South (avgSpeed exclusive with flowrate=0)
        });

        it('should handle directions only in flowrate (flowrateExclusiveDir)', async () => {
            const input = {
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                sensorId: 'sensor123',
                flowrateUnit: '/min'
            };

            // Average speed only has North
            elasticsearchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        directions: {
                            buckets: [
                                { key: 'North', averageSpeed: { value: 25 }, doc_count: 10 }
                            ]
                        }
                    }
                }
            });

            // Flowrate has North and East (East is exclusive to flowrate)
            elasticsearchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        sensors: {
                            buckets: [
                                {
                                    key: 'sensor123',
                                    directions: {
                                        buckets: [
                                            { key: 'North', uniqueObjectCount: { value: 10 } },
                                            { key: 'East', uniqueObjectCount: { value: 5 } }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            calibrationStub.resolves('cartesian');

            const result = await behavior.getAverageSpeedWithFlowrate(elasticDb, input);

            expect(result).to.have.property('metrics');
            expect(result.metrics).to.be.an('array');
            // Should have both North (common) and East (flowrate exclusive with averageSpeed=0)
        });
    });

    describe('getAverageSpeedWithTravelTime', () => {
        let behavior;
        let elasticDb;
        let elasticsearchStub;
        let calibrationGetStub;
        let calibrationTypeStub;
        let calibrationMapsStub;

        beforeEach(() => {
            behavior = new Behavior();
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => ({}),
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
            calibrationGetStub = sinon.stub(Calibration.prototype, 'getCalibration');
            calibrationTypeStub = sinon.stub(Calibration.prototype, 'getCalibrationType');
            calibrationMapsStub = sinon.stub(Calibration.prototype, 'getCalibrationMaps');
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should throw BadRequestError when required properties are missing', async () => {
            const input = {
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z'
            };

            try {
                await behavior.getAverageSpeedWithTravelTime(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('place');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                fromTimestamp: '2025-01-10T12:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                place: 'city=abc/corridor=xyz'
            };

            try {
                await behavior.getAverageSpeedWithTravelTime(elasticDb, input);
                throw new Error('Expected InvalidInputError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InvalidInputError for unsupported place hierarchy', async () => {
            const input = {
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                place: 'city=abc/building=xyz'
            };

            calibrationGetStub.resolves({
                timestamp: '2025-01-10T10:00:00.000Z',
                calibration: {}
            });
            calibrationMapsStub.returns({
                placeHierarchyMap: new Map(),
                sensorPlaceMap: new Map(),
                corridorInfoMap: new Map()
            });

            try {
                await behavior.getAverageSpeedWithTravelTime(elasticDb, input);
                throw new Error('Expected InvalidInputError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('Unsupported place hierarchy');
            }
        });
    });

    describe('getCurrentBehaviorSpeed', () => {
        let behavior;
        let elasticDb;
        let elasticsearchStub;

        beforeEach(() => {
            behavior = new Behavior();
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => ({}),
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should throw BadRequestError when required properties are missing', async () => {
            const input = {
                place: 'city=abc'
            };

            try {
                await behavior.getCurrentBehaviorSpeed(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T12:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z'
            };

            try {
                await behavior.getCurrentBehaviorSpeed(elasticDb, input);
                throw new Error('Expected InvalidInputError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };

            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z'
            };

            try {
                await behavior.getCurrentBehaviorSpeed(unsupportedDb, input);
                throw new Error('Expected InternalServerError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should return behaviors with currentAvgSpeed when valid input is provided', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z'
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    Id: 'behavior1',
                                    timestamp: '2025-01-10T10:30:00.000Z',
                                    end: '2025-01-10T10:35:00.000Z',
                                    timeInterval: 300,
                                    speedOverTime: [20, 25, 30, 35, 40],
                                    speed: 30,
                                    sensor: { id: 'sensor1' },
                                    place: { name: 'city=abc' },
                                    object: { id: 'obj1', type: 'Vehicle' }
                                }
                            }
                        ]
                    }
                }
            });

            const result = await behavior.getCurrentBehaviorSpeed(elasticDb, input);

            expect(result).to.be.an('array');
        });

        it('should return empty array when index is absent', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z'
            };

            elasticsearchStub.resolves({ indexAbsent: true });

            const result = await behavior.getCurrentBehaviorSpeed(elasticDb, input);

            expect(result).to.be.an('array').that.is.empty;
        });

        it('should calculate currentAvgSpeed from speedOverTime when toTimestamp is before behavior end', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T10:30:00.000Z',
                toTimestamp: '2025-01-10T10:32:30.000Z' // 2.5 minutes after behavior start, before end
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    Id: 'behavior1',
                                    timestamp: '2025-01-10T10:30:00.000Z',
                                    end: '2025-01-10T10:35:00.000Z',
                                    timeInterval: 300, // 5 minutes
                                    speedOverTime: [20, 25, 30, 35, 40],
                                    speed: 30,
                                    sensor: { id: 'sensor1' },
                                    place: { name: 'city=abc' },
                                    object: { id: 'obj1', type: 'Vehicle' }
                                }
                            }
                        ]
                    }
                }
            });

            const result = await behavior.getCurrentBehaviorSpeed(elasticDb, input);

            expect(result).to.be.an('array').with.lengthOf(1);
            expect(result[0]).to.have.property('currentAvgSpeed').that.is.a('number');
        });

        it('should cap currentAvgSpeed when it exceeds 2x behavior speed for fast objects', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T10:30:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                expectedAvgSpeedOfObjects: 30 // behavior.speed (50) > expectedAvgSpeedOfObjects
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    Id: 'behavior1',
                                    timestamp: '2025-01-10T10:30:00.000Z',
                                    end: '2025-01-10T10:35:00.000Z',
                                    timeInterval: 300,
                                    speedOverTime: [20, 25, 150, 35, 40], // 150 >= 2 * 50 = 100
                                    speed: 50, // speed > expectedAvgSpeedOfObjects (30)
                                    sensor: { id: 'sensor1' },
                                    place: { name: 'city=abc' },
                                    object: { id: 'obj1', type: 'Vehicle' }
                                }
                            }
                        ]
                    }
                }
            });

            const result = await behavior.getCurrentBehaviorSpeed(elasticDb, input);

            expect(result).to.be.an('array').with.lengthOf(1);
            expect(result[0]).to.have.property('currentAvgSpeed').that.is.a('number');
        });
    });

    describe('getRoadSegmentSpeed', () => {
        let behavior;
        let elasticDb;
        let elasticsearchStub;
        let calibrationGetStub;
        let calibrationMapsStub;
        let roadNetworkGetStub;
        let roadNetworkIntersectionStub;
        let roadNetworkSegmentStub;
        let roadNetworkMinimalStub;
        let currentBehaviorStub;

        beforeEach(() => {
            behavior = new Behavior();
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => ({}),
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
            calibrationGetStub = sinon.stub(Calibration.prototype, 'getCalibration');
            calibrationMapsStub = sinon.stub(Calibration.prototype, 'getCalibrationMaps');
            roadNetworkGetStub = sinon.stub(RoadNetwork.prototype, 'getRoadNetwork');
            roadNetworkIntersectionStub = sinon.stub(RoadNetwork.prototype, 'getIntersectionInfoMap');
            roadNetworkSegmentStub = sinon.stub(RoadNetwork.prototype, 'getSegmentMap');
            roadNetworkMinimalStub = sinon.stub(RoadNetwork.prototype, 'getMinimalSegmentMap');
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should throw BadRequestError when required properties are missing', async () => {
            const input = {
                place: 'city=abc'
            };

            try {
                await behavior.getRoadSegmentSpeed(elasticDb, input);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T12:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z'
            };

            try {
                await behavior.getRoadSegmentSpeed(elasticDb, input);
                throw new Error('Expected InvalidInputError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InvalidInputError for unsupported place hierarchy', async () => {
            const input = {
                place: 'city=abc/building=xyz/room=123',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z'
            };

            try {
                await behavior.getRoadSegmentSpeed(elasticDb, input);
                throw new Error('Expected InvalidInputError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('Unsupported place hierarchy');
            }
        });
    });

    describe('getAverageSpeedWithTravelTime', () => {
        let behavior;
        let elasticDb;
        let elasticsearchStub;
        let calibrationGetStub;
        let calibrationMapsStub;
        let calibrationTypeStub;

        beforeEach(() => {
            behavior = new Behavior();
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => ({}),
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
            calibrationGetStub = sinon.stub(Calibration.prototype, 'getCalibration');
            calibrationMapsStub = sinon.stub(Calibration.prototype, 'getCalibrationMaps');
            calibrationTypeStub = sinon.stub(Calibration.prototype, 'getCalibrationType');
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should throw BadRequestError for Infinity stationaryObjectMaxTimeIntervalThresholdInSec', async () => {
            const input = {
                place: 'city=abc/corridor=xyz',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                stationaryObjectMaxTimeIntervalThresholdInSec: Infinity
            };

            try {
                await behavior.getAverageSpeedWithTravelTime(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('stationaryObjectMaxTimeIntervalThresholdInSec');
            }
        });

        it('should throw BadRequestError for Infinity stationaryObjectMinDistanceThresholdInMeters', async () => {
            const input = {
                place: 'city=abc/corridor=xyz',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                stationaryObjectMinDistanceThresholdInMeters: Infinity
            };

            try {
                await behavior.getAverageSpeedWithTravelTime(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('stationaryObjectMinDistanceThresholdInMeters');
            }
        });

        it('should throw BadRequestError for Infinity shortLivedBehaviorMinTimeIntervalInSec', async () => {
            const input = {
                place: 'city=abc/corridor=xyz',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                shortLivedBehaviorMinTimeIntervalInSec: Infinity
            };

            try {
                await behavior.getAverageSpeedWithTravelTime(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('shortLivedBehaviorMinTimeIntervalInSec');
            }
        });

        it('should throw InvalidInputError for unsupported place hierarchy', async () => {
            const input = {
                place: 'city=abc/intersection=xyz',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z'
            };

            calibrationGetStub.resolves({
                timestamp: '2025-01-10T09:00:00.000Z',
                calibration: { sensors: [] }
            });

            try {
                await behavior.getAverageSpeedWithTravelTime(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('Unsupported place hierarchy');
            }
        });

        it('should return corridor travel time with hr unit', async () => {
            const input = {
                place: 'city=abc/corridor=xyz',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                corridorTravelTimeUnit: 'hr'
            };

            calibrationGetStub.resolves({
                timestamp: '2025-01-10T09:00:00.000Z',
                calibration: {
                    sensors: [{
                        id: 'sensor1',
                        place: [{ name: 'city=abc/corridor=xyz/sensor=s1' }]
                    }]
                }
            });

            calibrationMapsStub.returns({
                placeHierarchyMap: new Map([['city=abc/corridor=xyz', { leafPlace: false }]]),
                sensorPlaceMap: new Map([['sensor1', 'city=abc/corridor=xyz/sensor=s1']]),
                corridorInfoMap: new Map([['city=abc/corridor=xyz', { sensors: ['sensor1'], directions: ['North', 'South'], length: 10 }]])
            });

            calibrationTypeStub.resolves('cartesian');

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        directions: {
                            buckets: [
                                { key: 'North', averageSpeed: { value: 25 }, doc_count: 10 },
                                { key: 'South', averageSpeed: { value: 30 }, doc_count: 15 }
                            ]
                        }
                    }
                }
            });

            const result = await behavior.getAverageSpeedWithTravelTime(elasticDb, input);

            expect(result).to.have.property('metrics');
            expect(result.metrics).to.be.an('array');
            expect(result.metrics[0].corridorTravelTime).to.include('hr');
        });

        it('should return corridor travel time with min unit', async () => {
            const input = {
                place: 'city=abc/corridor=xyz',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                corridorTravelTimeUnit: 'min'
            };

            calibrationGetStub.resolves({
                timestamp: '2025-01-10T09:00:00.000Z',
                calibration: {
                    sensors: [{
                        id: 'sensor1',
                        place: [{ name: 'city=abc/corridor=xyz/sensor=s1' }]
                    }]
                }
            });

            calibrationMapsStub.returns({
                placeHierarchyMap: new Map([['city=abc/corridor=xyz', { leafPlace: false }]]),
                sensorPlaceMap: new Map([['sensor1', 'city=abc/corridor=xyz/sensor=s1']]),
                corridorInfoMap: new Map([['city=abc/corridor=xyz', { sensors: ['sensor1'], directions: ['North'], length: 10 }]])
            });

            calibrationTypeStub.resolves('cartesian');

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        directions: {
                            buckets: [
                                { key: 'North', averageSpeed: { value: 25 }, doc_count: 10 }
                            ]
                        }
                    }
                }
            });

            const result = await behavior.getAverageSpeedWithTravelTime(elasticDb, input);

            expect(result).to.have.property('metrics');
            expect(result.metrics[0].corridorTravelTime).to.include('min');
        });

        it('should return corridor travel time with sec unit', async () => {
            const input = {
                place: 'city=abc/corridor=xyz',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                corridorTravelTimeUnit: 'sec'
            };

            calibrationGetStub.resolves({
                timestamp: '2025-01-10T09:00:00.000Z',
                calibration: {
                    sensors: [{
                        id: 'sensor1',
                        place: [{ name: 'city=abc/corridor=xyz/sensor=s1' }]
                    }]
                }
            });

            calibrationMapsStub.returns({
                placeHierarchyMap: new Map([['city=abc/corridor=xyz', { leafPlace: false }]]),
                sensorPlaceMap: new Map([['sensor1', 'city=abc/corridor=xyz/sensor=s1']]),
                corridorInfoMap: new Map([['city=abc/corridor=xyz', { sensors: ['sensor1'], directions: ['North'], length: 10 }]])
            });

            calibrationTypeStub.resolves('cartesian');

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        directions: {
                            buckets: [
                                { key: 'North', averageSpeed: { value: 25 }, doc_count: 10 }
                            ]
                        }
                    }
                }
            });

            const result = await behavior.getAverageSpeedWithTravelTime(elasticDb, input);

            expect(result).to.have.property('metrics');
            expect(result.metrics[0].corridorTravelTime).to.include('sec');
        });
    });

    describe('getCurrentBehaviorSpeed additional tests', () => {
        let behavior;
        let elasticDb;
        let elasticsearchStub;

        beforeEach(() => {
            behavior = new Behavior();
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => ({}),
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should throw BadRequestError for Infinity expectedAvgSpeedOfObjects', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                expectedAvgSpeedOfObjects: Infinity,
                maxResultSize: 100
            };

            try {
                await behavior.getCurrentBehaviorSpeed(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('must be number');
            }
        });

        it('should throw BadRequestError for Infinity maxResultSize', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                expectedAvgSpeedOfObjects: 25,
                maxResultSize: Infinity
            };

            try {
                await behavior.getCurrentBehaviorSpeed(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('maxResultSize');
            }
        });

        it('should throw BadRequestError for Infinity stationaryObjectMaxTimeIntervalThresholdInSec', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                expectedAvgSpeedOfObjects: 25,
                maxResultSize: 100,
                stationaryObjectMaxTimeIntervalThresholdInSec: Infinity
            };

            try {
                await behavior.getCurrentBehaviorSpeed(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('stationaryObjectMaxTimeIntervalThresholdInSec');
            }
        });

        it('should throw BadRequestError for Infinity stationaryObjectMinDistanceThresholdInMeters', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                expectedAvgSpeedOfObjects: 25,
                maxResultSize: 100,
                stationaryObjectMinDistanceThresholdInMeters: Infinity
            };

            try {
                await behavior.getCurrentBehaviorSpeed(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('stationaryObjectMinDistanceThresholdInMeters');
            }
        });

        it('should throw BadRequestError for Infinity shortLivedBehaviorMinTimeIntervalInSec', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                expectedAvgSpeedOfObjects: 25,
                maxResultSize: 100,
                shortLivedBehaviorMinTimeIntervalInSec: Infinity
            };

            try {
                await behavior.getCurrentBehaviorSpeed(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('shortLivedBehaviorMinTimeIntervalInSec');
            }
        });

        it('should cap currentAvgSpeed when behavior speed exceeds expected and current >= 2x behavior speed', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T11:00:00.000Z',
                expectedAvgSpeedOfObjects: 10,
                maxResultSize: 100
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        total: { value: 1 },
                        hits: [{
                            _source: {
                                id: 'behavior1',
                                timestamp: '2025-01-10T10:00:00.000Z',
                                end: '2025-01-10T10:30:00.000Z',
                                speed: 20,
                                timeInterval: 1800,
                                speedOverTime: [10, 15, 20, 50, 100]
                            }
                        }]
                    }
                }
            });

            const result = await behavior.getCurrentBehaviorSpeed(elasticDb, input);

            expect(result).to.be.an('array');
            expect(result.length).to.equal(1);
            expect(result[0].currentAvgSpeed).to.equal(20);
        });

        it('should calculate currentAvgSpeed using speedIndex when toTimestamp < behavior.end', async () => {
            const input = {
                place: 'city=abc',
                fromTimestamp: '2025-01-10T10:00:00.000Z',
                toTimestamp: '2025-01-10T10:15:00.000Z',
                expectedAvgSpeedOfObjects: 50,
                maxResultSize: 100
            };

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        total: { value: 1 },
                        hits: [{
                            _source: {
                                id: 'behavior1',
                                timestamp: '2025-01-10T10:00:00.000Z',
                                end: '2025-01-10T10:30:00.000Z',
                                speed: 20,
                                timeInterval: 1800,
                                speedOverTime: [10, 15, 20, 25, 30]
                            }
                        }]
                    }
                }
            });

            const result = await behavior.getCurrentBehaviorSpeed(elasticDb, input);

            expect(result).to.be.an('array').with.lengthOf(1);
            expect(result[0]).to.have.property('currentAvgSpeed').that.is.a('number');
        });
    });

});

// Tests for getRoadSegmentSpeed using proxyquire to mock cache
describe('Metrics.Behavior.getRoadSegmentSpeed with proxyquire', () => {
    const proxyquire = require('proxyquire').noCallThru().noPreserveCache();
    let Behavior;
    let behavior;
    let elasticDb;
    let searchStub;
    let calibrationGetStub;
    let calibrationMapsStub;
    let roadNetworkGetStub;
    let roadNetworkIntersectionStub;
    let roadNetworkSegmentStub;
    let roadNetworkMinimalStub;
    let mockCache;

    beforeEach(() => {
        mockCache = new Map();
        const MockNodeCache = function() {
            this.get = (key) => mockCache.get(key);
            this.set = (key, value) => mockCache.set(key, value);
            this.has = (key) => mockCache.has(key);
            this.keys = () => Array.from(mockCache.keys());
            this.del = (keys) => keys.forEach(k => mockCache.delete(k));
        };

        searchStub = sinon.stub();
        calibrationGetStub = sinon.stub();
        calibrationMapsStub = sinon.stub();
        roadNetworkGetStub = sinon.stub();
        roadNetworkIntersectionStub = sinon.stub();
        roadNetworkSegmentStub = sinon.stub();
        roadNetworkMinimalStub = sinon.stub();

        Behavior = proxyquire('../../../../src/web-api-core/Metrics/Behavior', {
            'node-cache': MockNodeCache,
            '../Utils/Elasticsearch': {
                getSearchResults: searchStub,
                getIndex: (name) => name,
                searchResultFormatter: (body) => body.hits?.hits?.map(h => h._source) || []
            },
            '../Services/Calibration': function() {
                this.getCalibration = calibrationGetStub;
                this.getCalibrationMaps = calibrationMapsStub;
            },
            '../Services/RoadNetwork': function() {
                this.getRoadNetwork = roadNetworkGetStub;
                this.getIntersectionInfoMap = roadNetworkIntersectionStub;
                this.getSegmentMap = roadNetworkSegmentStub;
                this.getMinimalSegmentMap = roadNetworkMinimalStub;
            }
        });

        behavior = new Behavior();
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([['indexPrefix', 'mdx-']])
        };
    });

    afterEach(() => {
        sinon.restore();
        mockCache.clear();
    });

    it('should return empty roadSegments when place not in hierarchy (line 1247)', async () => {
        const input = {
            place: 'city=unknown',
            fromTimestamp: '2025-01-10T10:00:00.000Z',
            toTimestamp: '2025-01-10T11:00:00.000Z'
        };

        calibrationGetStub.resolves({
            timestamp: '2025-01-10T09:00:00.000Z',
            calibration: { calibrationType: 'geo', sensors: [] }
        });

        calibrationMapsStub.returns({
            placeHierarchyMap: new Map(), // Empty - place not found
            sensorPlaceMap: new Map(),
            corridorInfoMap: new Map()
        });

        roadNetworkGetStub.resolves({
            timestamp: '2025-01-10T08:00:00.000Z',
            roadNetwork: { intersections: [] }
        });

        searchStub.resolves({
            indexAbsent: false,
            body: { hits: { hits: [] } }
        });

        const result = await behavior.getRoadSegmentSpeed(elasticDb, input);

        expect(result).to.have.property('roadSegments');
        expect(result.roadSegments).to.be.an('array').that.is.empty;
    });

    it('should process city-level place with behaviors (lines 1273-1275, 1293-1329)', async () => {
        const input = {
            place: 'city=testCity',
            fromTimestamp: '2025-01-10T10:00:00.000Z',
            toTimestamp: '2025-01-10T11:00:00.000Z'
        };

        calibrationGetStub.resolves({
            timestamp: '2025-01-10T09:00:00.000Z',
            calibration: { calibrationType: 'geo', sensors: [] }
        });

        calibrationMapsStub.returns({
            placeHierarchyMap: new Map([
                ['city=testCity', { places: ['city=testCity/intersection=i1'], sensors: null }]
            ]),
            sensorPlaceMap: new Map(),
            corridorInfoMap: new Map()
        });

        roadNetworkGetStub.resolves({
            timestamp: '2025-01-10T08:00:00.000Z',
            roadNetwork: { intersections: [{ id: 'i1', edges: [{ id: 'edge1' }] }] }
        });

        // Create segment map
        const citySegmentMap = new Map([
            ['edge1', { speed: 0, objectCount: 0, from: 'a', to: 'b' }]
        ]);
        const minimalCitySegmentMap = new Map([
            ['edge1', { speed: 0, objectCount: 0 }]
        ]);

        roadNetworkIntersectionStub.returns(new Map());
        roadNetworkSegmentStub.returns(citySegmentMap);
        roadNetworkMinimalStub.returns(minimalCitySegmentMap);

        // Return behaviors with edges
        searchStub.resolves({
            indexAbsent: false,
            body: {
                hits: {
                    hits: [{
                        _source: {
                            id: 'b1',
                            timestamp: '2025-01-10T10:30:00.000Z',
                            end: '2025-01-10T10:35:00.000Z',
                            timeInterval: 300,
                            speedOverTime: [20, 25, 30],
                            speed: 25,
                            edges: ['edge1'],
                            place: { name: 'city=testCity' },
                            sensor: { id: 's1' },
                            object: { id: 'o1', type: 'Vehicle' }
                        }
                    }]
                }
            }
        });

        const result = await behavior.getRoadSegmentSpeed(elasticDb, input);

        expect(result).to.have.property('roadSegments');
        expect(result.roadSegments).to.be.an('array');
    });

    it('should process intersection-level place (lines 1276-1291)', async () => {
        const input = {
            place: 'city=testCity/intersection=i1',
            fromTimestamp: '2025-01-10T10:00:00.000Z',
            toTimestamp: '2025-01-10T11:00:00.000Z'
        };

        calibrationGetStub.resolves({
            timestamp: '2025-01-10T09:00:00.000Z',
            calibration: { calibrationType: 'geo', sensors: [] }
        });

        calibrationMapsStub.returns({
            placeHierarchyMap: new Map([
                ['city=testCity/intersection=i1', { places: null, sensors: ['sensor1'] }]
            ]),
            sensorPlaceMap: new Map([['sensor1', 'city=testCity/intersection=i1']]),
            corridorInfoMap: new Map()
        });

        const intersectionInfo = { id: 'i1', edges: [{ id: 'edge1' }] };
        const intersectionInfoMap = new Map([
            ['city=testCity/intersection=i1', intersectionInfo]
        ]);

        roadNetworkGetStub.resolves({
            timestamp: '2025-01-10T08:00:00.000Z',
            roadNetwork: { intersections: [intersectionInfo] }
        });

        const citySegmentMap = new Map([['edge1', { speed: 0, objectCount: 0 }]]);
        const minimalSegmentMap = new Map([['edge1', { speed: 0, objectCount: 0 }]]);

        roadNetworkIntersectionStub.returns(intersectionInfoMap);
        roadNetworkSegmentStub.returns(citySegmentMap);
        roadNetworkMinimalStub.returns(minimalSegmentMap);

        searchStub.resolves({
            indexAbsent: false,
            body: {
                hits: {
                    hits: [{
                        _source: {
                            id: 'b1',
                            timestamp: '2025-01-10T10:30:00.000Z',
                            end: '2025-01-10T10:35:00.000Z',
                            timeInterval: 300,
                            speedOverTime: [20, 25, 30],
                            speed: 25,
                            edges: ['edge1'],
                            place: { name: 'city=testCity/intersection=i1' },
                            sensor: { id: 'sensor1' },
                            object: { id: 'o1', type: 'Vehicle' }
                        }
                    }]
                }
            }
        });

        const result = await behavior.getRoadSegmentSpeed(elasticDb, input);

        expect(result).to.have.property('roadSegments');
    });

    it('should update segment speed when behavior has more objects (lines 1313-1320)', async () => {
        const input = {
            place: 'city=testCity',
            fromTimestamp: '2025-01-10T10:00:00.000Z',
            toTimestamp: '2025-01-10T11:00:00.000Z'
        };

        calibrationGetStub.resolves({
            timestamp: '2025-01-10T09:00:00.000Z',
            calibration: { calibrationType: 'geo', sensors: [] }
        });

        calibrationMapsStub.returns({
            placeHierarchyMap: new Map([
                ['city=testCity', { places: ['city=testCity/intersection=i1'], sensors: null }]
            ]),
            sensorPlaceMap: new Map(),
            corridorInfoMap: new Map()
        });

        roadNetworkGetStub.resolves({
            timestamp: '2025-01-10T08:00:00.000Z',
            roadNetwork: { intersections: [] }
        });

        const citySegmentMap = new Map([
            ['edge1', { speed: 10, objectCount: 1, from: 'a', to: 'b' }]
        ]);
        const minimalCitySegmentMap = new Map([
            ['edge1', { speed: 10, objectCount: 1 }]
        ]);

        roadNetworkIntersectionStub.returns(new Map());
        roadNetworkSegmentStub.returns(citySegmentMap);
        roadNetworkMinimalStub.returns(minimalCitySegmentMap);

        // Return multiple behaviors for same edge
        searchStub.resolves({
            indexAbsent: false,
            body: {
                hits: {
                    hits: [
                        {
                            _source: {
                                id: 'b1',
                                timestamp: '2025-01-10T10:30:00.000Z',
                                end: '2025-01-10T10:35:00.000Z',
                                timeInterval: 300,
                                speedOverTime: [30, 35, 40],
                                speed: 35,
                                edges: ['edge1'],
                                place: { name: 'city=testCity' },
                                sensor: { id: 's1' },
                                object: { id: 'o1', type: 'Vehicle' }
                            }
                        },
                        {
                            _source: {
                                id: 'b2',
                                timestamp: '2025-01-10T10:31:00.000Z',
                                end: '2025-01-10T10:36:00.000Z',
                                timeInterval: 300,
                                speedOverTime: [25, 30, 35],
                                speed: 30,
                                edges: ['edge1'],
                                place: { name: 'city=testCity' },
                                sensor: { id: 's1' },
                                object: { id: 'o2', type: 'Vehicle' }
                            }
                        }
                    ]
                }
            }
        });

        const result = await behavior.getRoadSegmentSpeed(elasticDb, input);

        expect(result).to.have.property('roadSegments');
        expect(result.roadSegments).to.be.an('array');
        // Should have updated speed based on 2 behaviors
    });

    it('should use cached intersection segment map (line 1279)', async () => {
        const input = {
            place: 'city=testCity/intersection=i1',
            fromTimestamp: '2025-01-10T10:00:00.000Z',
            toTimestamp: '2025-01-10T11:00:00.000Z'
        };

        // Pre-populate cache with intersection segment map
        const cachedSegmentMap = new Map([['edge1', { speed: 20, objectCount: 5 }]]);
        mockCache.set(`intersectionSegmentMap-city=testCity/intersection=i1`, {
            segmentMap: cachedSegmentMap,
            minimalSegmentMap: cachedSegmentMap
        });
        mockCache.set('placeHierarchyMap', new Map([
            ['city=testCity/intersection=i1', { places: null, sensors: ['sensor1'] }]
        ]));
        mockCache.set('calibration-timestamp', '2025-01-10T09:00:00.000Z');
        mockCache.set('road-network-timestamp', '2025-01-10T08:00:00.000Z');
        mockCache.set('roadNetworkCitySegmentMaps', {
            citySegmentMap: new Map(),
            minimalCitySegmentMap: new Map()
        });

        calibrationGetStub.resolves({
            timestamp: '2025-01-10T08:00:00.000Z', // Older than cached
            calibration: { calibrationType: 'geo', sensors: [] }
        });

        calibrationMapsStub.returns({
            placeHierarchyMap: new Map([
                ['city=testCity/intersection=i1', { places: null, sensors: ['sensor1'] }]
            ]),
            sensorPlaceMap: new Map(),
            corridorInfoMap: new Map()
        });

        roadNetworkGetStub.resolves({
            timestamp: '2025-01-10T07:00:00.000Z', // Older than cached
            roadNetwork: { intersections: [] }
        });

        roadNetworkIntersectionStub.returns(new Map());
        roadNetworkSegmentStub.returns(new Map());
        roadNetworkMinimalStub.returns(new Map());

        searchStub.resolves({
            indexAbsent: false,
            body: { hits: { hits: [] } }
        });

        const result = await behavior.getRoadSegmentSpeed(elasticDb, input);

        expect(result).to.have.property('roadSegments');
    });

    it('should delete old intersection cache keys when road network updates (lines 1262-1269)', async () => {
        const input = {
            place: 'city=testCity',
            fromTimestamp: '2025-01-10T10:00:00.000Z',
            toTimestamp: '2025-01-10T11:00:00.000Z'
        };

        // Pre-populate cache with old data
        mockCache.set('road-network-timestamp', '2025-01-01T00:00:00.000Z');
        mockCache.set('intersectionSegmentMap-city=testCity/intersection=old', { old: true });

        calibrationGetStub.resolves({
            timestamp: '2025-01-10T09:00:00.000Z',
            calibration: { calibrationType: 'geo', sensors: [] }
        });

        calibrationMapsStub.returns({
            placeHierarchyMap: new Map([
                ['city=testCity', { places: null, sensors: null }]
            ]),
            sensorPlaceMap: new Map(),
            corridorInfoMap: new Map()
        });

        roadNetworkGetStub.resolves({
            timestamp: '2025-01-10T08:00:00.000Z', // Newer than cached
            roadNetwork: { intersections: [] }
        });

        roadNetworkIntersectionStub.returns(new Map());
        roadNetworkSegmentStub.returns(new Map());
        roadNetworkMinimalStub.returns(new Map());

        searchStub.resolves({
            indexAbsent: false,
            body: { hits: { hits: [] } }
        });

        const result = await behavior.getRoadSegmentSpeed(elasticDb, input);

        expect(result).to.have.property('roadSegments');
        // Old intersection cache should be deleted
        expect(mockCache.has('intersectionSegmentMap-city=testCity/intersection=old')).to.be.false;
    });

    it('should use segmentInfo=true to get full segment map (line 1275)', async () => {
        const input = {
            place: 'city=testCity',
            fromTimestamp: '2025-01-10T10:00:00.000Z',
            toTimestamp: '2025-01-10T11:00:00.000Z',
            segmentInfo: true
        };

        calibrationGetStub.resolves({
            timestamp: '2025-01-10T09:00:00.000Z',
            calibration: { calibrationType: 'geo', sensors: [] }
        });

        calibrationMapsStub.returns({
            placeHierarchyMap: new Map([
                ['city=testCity', { places: null, sensors: null }]
            ]),
            sensorPlaceMap: new Map(),
            corridorInfoMap: new Map()
        });

        roadNetworkGetStub.resolves({
            timestamp: '2025-01-10T08:00:00.000Z',
            roadNetwork: { intersections: [] }
        });

        const fullSegmentMap = new Map([
            ['edge1', { speed: 0, objectCount: 0, from: 'a', to: 'b', lanes: 2 }]
        ]);
        const minimalSegmentMap = new Map([
            ['edge1', { speed: 0, objectCount: 0 }]
        ]);

        roadNetworkIntersectionStub.returns(new Map());
        roadNetworkSegmentStub.returns(fullSegmentMap);
        roadNetworkMinimalStub.returns(minimalSegmentMap);

        searchStub.resolves({
            indexAbsent: false,
            body: { hits: { hits: [] } }
        });

        const result = await behavior.getRoadSegmentSpeed(elasticDb, input);

        expect(result).to.have.property('roadSegments');
    });
});
