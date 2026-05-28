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
const LastProcessedTimestamp = require('../../../../src/web-api-core/Metrics/LastProcessedTimestamp');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const Calibration = require('../../../../src/web-api-core/Services/Calibration');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');

describe('LastProcessedTimestamp', () => {
    let lastProcessedTimestamp;
    let elasticDb;
    let searchStub;

    beforeEach(() => {
        lastProcessedTimestamp = new LastProcessedTimestamp();
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([
                ['indexPrefix', 'mdx-'],
                ['rawIndex', 'mdx-raw-*']
            ])
        };
        searchStub = sinon.stub(Elasticsearch, 'getSearchResults');
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('getLastProcessedTimestamp', () => {
        describe('with sensorId', () => {
            it('should return last processed timestamp for sensor', async () => {
                const input = { sensorId: 'sensor123' };

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        hits: {
                            hits: [{ _source: { timestamp: '2023-01-12T14:20:10.000Z' } }]
                        }
                    }
                });

                const result = await lastProcessedTimestamp.getLastProcessedTimestamp(elasticDb, input);

                expect(result).to.have.property('latestTimestamp');
                expect(result.latestTimestamp).to.deep.include({
                    sensorId: 'sensor123',
                    timestamp: '2023-01-12T14:20:10.000Z'
                });
            });

            it('should return null timestamp when no data found for sensor', async () => {
                const input = { sensorId: 'sensor123' };

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        hits: {
                            hits: []
                        }
                    }
                });

                const result = await lastProcessedTimestamp.getLastProcessedTimestamp(elasticDb, input);

                expect(result.latestTimestamp).to.be.null;
            });

            it('should return null timestamp when index is absent', async () => {
                const input = { sensorId: 'sensor123' };

                searchStub.resolves({ indexAbsent: true });

                const result = await lastProcessedTimestamp.getLastProcessedTimestamp(elasticDb, input);

                expect(result.latestTimestamp).to.be.null;
            });
        });

        describe('with place', () => {
            let calibrationStub;
            let calibrationMapsStub;

            beforeEach(() => {
                calibrationStub = sinon.stub(Calibration.prototype, 'getCalibration');
                calibrationMapsStub = sinon.stub(Calibration.prototype, 'getCalibrationMaps');
            });

            it('should return null timestamp when place not found in calibration', async () => {
                const input = { place: 'nonexistent' };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                calibrationMapsStub.returns({
                    placeHierarchyMap: new Map([
                        ['city=abc', { places: ['city=abc/building=xyz'], sensors: ['sensor1'] }]
                    ]),
                    sensorPlaceMap: new Map([
                        ['sensor1', 'city=abc/building=xyz']
                    ])
                });

                const result = await lastProcessedTimestamp.getLastProcessedTimestamp(elasticDb, input);

                expect(result.latestTimestamp).to.be.null;
            });

            it('should return null for leaf place when no data found', async () => {
                const input = { place: 'city=abc/building=xyz' };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                calibrationMapsStub.returns({
                    placeHierarchyMap: new Map([
                        ['city=abc/building=xyz', { places: null, sensors: ['sensor1', 'sensor2'] }]
                    ]),
                    sensorPlaceMap: new Map([
                        ['sensor1', 'city=abc/building=xyz'],
                        ['sensor2', 'city=abc/building=xyz']
                    ])
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        hits: { hits: [] },
                        aggregations: {
                            sensorIds: { buckets: [] }
                        }
                    }
                });

                const result = await lastProcessedTimestamp.getLastProcessedTimestamp(elasticDb, input);

                expect(result.latestTimestamp).to.be.null;
            });

            it('should return result for non-leaf place without sensors', async () => {
                const input = { place: 'city=def' };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                calibrationMapsStub.returns({
                    placeHierarchyMap: new Map([
                        ['city=def', { places: ['city=def/building=uvw'], sensors: null }]
                    ]),
                    sensorPlaceMap: new Map()
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        hits: {
                            hits: [{
                                _source: {
                                    place: { name: 'city=def/building=uvw' },
                                    end: '2023-01-12T14:20:10.000Z'
                                }
                            }]
                        },
                        aggregations: {
                            placeSuccessor: {
                                buckets: [
                                    {
                                        key: 'city=def/building=uvw',
                                        lastProcessedTimestamp: {
                                            hits: {
                                                hits: [{ _source: { end: '2023-01-12T14:20:10.000Z' } }]
                                            }
                                        }
                                    }
                                ]
                            }
                        }
                    }
                });

                const result = await lastProcessedTimestamp.getLastProcessedTimestamp(elasticDb, input);

                expect(result).to.have.property('latestTimestamp');
            });

            it('should return result for non-leaf place with sensors', async () => {
                const input = { place: 'city=abc' };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                calibrationMapsStub.returns({
                    placeHierarchyMap: new Map([
                        ['city=abc', { places: ['city=abc/building=xyz'], sensors: ['sensor1'] }]
                    ]),
                    sensorPlaceMap: new Map([
                        ['sensor1', 'city=abc/building=xyz']
                    ])
                });

                // Both queries return data
                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        hits: {
                            hits: [{
                                _source: {
                                    sensor: { id: 'sensor1' },
                                    place: { name: 'city=abc/building=xyz' },
                                    end: '2023-01-12T14:20:10.000Z'
                                }
                            }]
                        },
                        aggregations: {
                            sensorIds: {
                                buckets: [{
                                    key: 'sensor1',
                                    lastProcessedTimestamp: {
                                        hits: {
                                            hits: [{ _source: { end: '2023-01-12T14:00:00.000Z' } }]
                                        }
                                    }
                                }]
                            },
                            placeSuccessor: {
                                buckets: [{
                                    key: 'city=abc/building=xyz',
                                    lastProcessedTimestamp: {
                                        hits: {
                                            hits: [{ _source: { end: '2023-01-12T14:20:10.000Z' } }]
                                        }
                                    }
                                }]
                            }
                        }
                    }
                });

                const result = await lastProcessedTimestamp.getLastProcessedTimestamp(elasticDb, input);

                expect(result).to.have.property('latestTimestamp');
                expect(result).to.have.property('details');
            });

            it('should handle empty calibration gracefully', async () => {
                // Use a unique place name to avoid cache interference from other tests
                const input = { place: 'empty-calibration-test-place' };

                calibrationStub.resolves({
                    // Use a future timestamp to ensure cache is updated with stubbed values
                    timestamp: '2099-12-31T23:59:59.000Z',
                    calibration: { calibrationType: '', sensors: [] }
                });

                calibrationMapsStub.returns({
                    placeHierarchyMap: new Map(),
                    sensorPlaceMap: new Map()
                });

                // Stub searchStub in case the code reaches ES (shouldn't happen but for safety)
                searchStub.resolves({
                    indexAbsent: false,
                    body: { hits: { hits: [] } }
                });

                const result = await lastProcessedTimestamp.getLastProcessedTimestamp(elasticDb, input);

                expect(result.latestTimestamp).to.be.null;
            });

            it('should return null details when no data found', async () => {
                const input = { place: 'city=abc/building=xyz' };

                calibrationStub.resolves({
                    timestamp: '2023-01-12T14:20:10.000Z',
                    calibration: { calibrationType: 'geo', sensors: [] }
                });

                calibrationMapsStub.returns({
                    placeHierarchyMap: new Map([
                        ['city=abc/building=xyz', { places: null, sensors: ['sensor1'] }]
                    ]),
                    sensorPlaceMap: new Map([
                        ['sensor1', 'city=abc/building=xyz']
                    ])
                });

                searchStub.resolves({
                    indexAbsent: false,
                    body: {
                        hits: { hits: [] },
                        aggregations: {
                            sensorIds: { buckets: [] }
                        }
                    }
                });

                const result = await lastProcessedTimestamp.getLastProcessedTimestamp(elasticDb, input);

                expect(result.latestTimestamp).to.be.null;
            });
        });

        describe('validation', () => {
            it('should throw BadRequestError when neither sensorId nor place provided', async () => {
                const input = {};

                try {
                    await lastProcessedTimestamp.getLastProcessedTimestamp(elasticDb, input);
                    throw new Error('Expected BadRequestError');
                } catch (error) {
                    expect(error).to.be.instanceOf(BadRequestError);
                    expect(error.message).to.include('sensorId');
                }
            });

            it('should throw BadRequestError for empty sensorId', async () => {
                const input = { sensorId: '' };

                try {
                    await lastProcessedTimestamp.getLastProcessedTimestamp(elasticDb, input);
                    throw new Error('Expected BadRequestError');
                } catch (error) {
                    expect(error).to.be.instanceOf(BadRequestError);
                    expect(error.message).to.include('sensorId');
                }
            });

            it('should throw BadRequestError for empty place', async () => {
                const input = { place: '' };

                try {
                    await lastProcessedTimestamp.getLastProcessedTimestamp(elasticDb, input);
                    throw new Error('Expected BadRequestError');
                } catch (error) {
                    expect(error).to.be.instanceOf(BadRequestError);
                    expect(error.message).to.include('place');
                }
            });

            it('should throw InternalServerError for unsupported database with sensorId', async () => {
                const unsupportedDb = {
                    getName: () => 'UnsupportedDB',
                    getClient: () => ({}),
                    getConfigs: () => new Map()
                };
                const input = { sensorId: 'sensor123' };

                try {
                    await lastProcessedTimestamp.getLastProcessedTimestamp(unsupportedDb, input);
                    throw new Error('Expected InternalServerError');
                } catch (error) {
                    expect(error).to.be.instanceOf(InternalServerError);
                    expect(error.message).to.include('Invalid database');
                }
            });
        });
    });
});
