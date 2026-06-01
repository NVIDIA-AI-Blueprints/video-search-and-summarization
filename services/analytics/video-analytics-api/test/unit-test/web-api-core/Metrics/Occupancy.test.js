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
const Occupancy = require('../../../../src/web-api-core/Metrics/Occupancy');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');

describe('Occupancy', () => {
    let occupancy;
    let elasticDb;
    let searchStub;
    let mockEsClient;

    beforeEach(() => {
        occupancy = new Occupancy();
        mockEsClient = {
            index: sinon.stub().resolves({ result: 'created' })
        };
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => mockEsClient,
            getConfigs: () => new Map([
                ['indexPrefix', 'mdx-']
            ])
        };
        searchStub = sinon.stub(Elasticsearch, 'getSearchResults');
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('getOccupancyBasedOnTripwireEvents', () => {
        it('should return occupancy with no reset value', async () => {
            const input = {
                place: 'building=abc/room=xyz',
                timestamp: '2023-01-12T11:20:10.000Z'
            };

            // First call - occupancy reset details (no reset)
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objectTypes: {
                            buckets: []
                        }
                    }
                }
            });

            // Second call - object types from Events.getObjectTypesOfTripwireEvents
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objectTypes: {
                            buckets: [{ key: 'Person' }]
                        }
                    }
                }
            });

            // Third call - tripwire events with object type aggregations
            searchStub.onCall(2).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objectTypes: {
                            buckets: [
                                {
                                    key: 'Person',
                                    eventTypes: {
                                        buckets: [
                                            { key: 'IN', doc_count: 10 },
                                            { key: 'OUT', doc_count: 5 }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await occupancy.getOccupancyBasedOnTripwireEvents(elasticDb, input);

            expect(result).to.have.property('occupancy').that.is.an('array');
            expect(result.occupancy).to.have.length(1);
            expect(result.occupancy[0]).to.deep.include({
                objectType: 'Person',
                count: 5
            });
            expect(result.occupancy[0].details.occupancyReset).to.be.null;
        });

        it('should return occupancy with reset value', async () => {
            const input = {
                place: 'building=abc/room=xyz',
                timestamp: '2023-01-12T11:20:10.000Z'
            };

            // First call - occupancy reset details with reset value
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objectTypes: {
                            buckets: [{
                                key: 'Person',
                                latestReset: {
                                    hits: {
                                        hits: [{
                                            _source: {
                                                occupancyReset: 10,
                                                timestamp: '2023-01-12T10:00:00.000Z'
                                            }
                                        }]
                                    }
                                }
                            }]
                        }
                    }
                }
            });

            // Second call - object types from Events.getObjectTypesOfTripwireEvents
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objectTypes: {
                            buckets: [{ key: 'Person' }]
                        }
                    }
                }
            });

            // Third call - tripwire events with object type aggregations
            searchStub.onCall(2).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objectTypes: {
                            buckets: [
                                {
                                    key: 'Person',
                                    eventTypes: {
                                        buckets: [
                                            { key: 'IN', doc_count: 5 },
                                            { key: 'OUT', doc_count: 3 }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await occupancy.getOccupancyBasedOnTripwireEvents(elasticDb, input);

            expect(result).to.have.property('occupancy').that.is.an('array');
            expect(result.occupancy).to.have.length(1);
            expect(result.occupancy[0]).to.deep.include({
                objectType: 'Person',
                count: 12 // 10 (reset) + 5 (IN) - 3 (OUT) = 12
            });
            expect(result.occupancy[0].details.occupancyReset).to.not.be.null;
        });

        it('should return 0 when OUT > IN', async () => {
            const input = {
                place: 'building=abc/room=xyz',
                timestamp: '2023-01-12T11:20:10.000Z'
            };

            // First call - no occupancy reset
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objectTypes: { buckets: [] }
                    }
                }
            });

            // Second call - object types
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objectTypes: { buckets: [{ key: 'Person' }] }
                    }
                }
            });

            // Third call - tripwire events
            searchStub.onCall(2).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objectTypes: {
                            buckets: [{
                                key: 'Person',
                                eventTypes: {
                                    buckets: [
                                        { key: 'IN', doc_count: 5 },
                                        { key: 'OUT', doc_count: 10 }
                                    ]
                                }
                            }]
                        }
                    }
                }
            });

            const result = await occupancy.getOccupancyBasedOnTripwireEvents(elasticDb, input);

            expect(result).to.have.property('occupancy').that.is.an('array');
            expect(result.occupancy).to.have.length(1);
            expect(result.occupancy[0].count).to.equal(0);
        });

        it('should use custom objectType', async () => {
            const input = {
                place: 'building=abc/room=xyz',
                timestamp: '2023-01-12T11:20:10.000Z',
                objectType: 'Vehicle'
            };

            // First call - occupancy reset details for custom objectType
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objectTypes: { buckets: [] }
                    }
                }
            });

            // Second call - tripwire events for custom objectType
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objectTypes: {
                            buckets: [{
                                key: 'Vehicle',
                                eventTypes: {
                                    buckets: [
                                        { key: 'IN', doc_count: 5 },
                                        { key: 'OUT', doc_count: 3 }
                                    ]
                                }
                            }]
                        }
                    }
                }
            });

            const result = await occupancy.getOccupancyBasedOnTripwireEvents(elasticDb, input);

            expect(result).to.have.property('occupancy').that.is.an('array');
            expect(result.occupancy[0].objectType).to.equal('Vehicle');
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                place: 'building=abc/room=xyz'
            };

            try {
                await occupancy.getOccupancyBasedOnTripwireEvents(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('timestamp');
            }
        });

        it('should throw InvalidInputError for invalid timestamp', async () => {
            const input = {
                place: 'building=abc/room=xyz',
                timestamp: 'invalid-timestamp'
            };

            try {
                await occupancy.getOccupancyBasedOnTripwireEvents(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('timestamp');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const input = {
                place: 'building=abc/room=xyz',
                timestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await occupancy.getOccupancyBasedOnTripwireEvents(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });
    });

    describe('resetOccupancy', () => {
        it('should reset occupancy successfully', async () => {
            const input = {
                place: 'building=abc/room=xyz',
                timestamp: '2023-01-12T11:20:10.000Z',
                occupancyReset: 10,
                objectType: 'Person'
            };

            const result = await occupancy.resetOccupancy(elasticDb, input);

            expect(result).to.deep.equal({ success: true });
            expect(mockEsClient.index.calledOnce).to.be.true;
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                place: 'building=abc/room=xyz',
                timestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await occupancy.resetOccupancy(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('occupancyReset');
            }
        });

        it('should throw InvalidInputError for invalid timestamp', async () => {
            const input = {
                place: 'building=abc/room=xyz',
                timestamp: 'invalid-timestamp',
                occupancyReset: 10,
                objectType: 'Person'
            };

            try {
                await occupancy.resetOccupancy(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('timestamp');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const input = {
                place: 'building=abc/room=xyz',
                timestamp: '2023-01-12T11:20:10.000Z',
                occupancyReset: 10,
                objectType: 'Person'
            };

            try {
                await occupancy.resetOccupancy(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should throw BadRequestError when occupancyReset is Infinity (schema validation)', async () => {
            const input = {
                place: 'building=abc/room=xyz',
                timestamp: '2023-01-12T11:20:10.000Z',
                occupancyReset: Infinity,
                objectType: 'Person'
            };

            try {
                await occupancy.resetOccupancy(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                // Schema validation catches Infinity before Number.isFinite check
                expect(error).to.be.instanceOf(BadRequestError);
            }
        });
    });

    describe('getAverageFovOccupancy', () => {
        it('should return average fov occupancy', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        fov: {
                            searchAggFilter: {
                                objectType: {
                                    buckets: [
                                        { key: 'Person', avgCount: { value: 5.5 } },
                                        { key: 'Vehicle', avgCount: { value: 2.3 } }
                                    ]
                                }
                            }
                        }
                    }
                }
            });

            const result = await occupancy.getAverageFovOccupancy(elasticDb, input);

            expect(result).to.have.property('fovOccupancy');
            expect(result.fovOccupancy).to.be.an('array').with.length(2);
        });

        it('should filter by objectType when provided', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                objectType: 'Person'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        fov: {
                            searchAggFilter: {
                                objectType: {
                                    buckets: [
                                        { key: 'Person', avgCount: { value: 5.5 } }
                                    ]
                                }
                            }
                        }
                    }
                }
            });

            const result = await occupancy.getAverageFovOccupancy(elasticDb, input);

            expect(result.fovOccupancy).to.have.length(1);
        });

        it('should return empty array when index is absent', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await occupancy.getAverageFovOccupancy(elasticDb, input);

            expect(result.fovOccupancy).to.be.an('array').that.is.empty;
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await occupancy.getAverageFovOccupancy(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T14:20:10.000Z',
                toTimestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await occupancy.getAverageFovOccupancy(elasticDb, input);
                throw new Error('Expected InvalidInputError');
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
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await occupancy.getAverageFovOccupancy(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });
    });

    describe('getHistogramOfAverageFovOccupancy', () => {
        it('should return histogram with time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: [
                                {
                                    key_as_string: '2023-01-12T11:00:00.000Z',
                                    fov: {
                                        searchAggFilter: {
                                            objectType: {
                                                buckets: [
                                                    { key: 'Person', avgCount: { value: 5.5 } }
                                                ]
                                            }
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await occupancy.getHistogramOfAverageFovOccupancy(elasticDb, input);

            expect(result).to.have.property('bucketSizeInSec');
            expect(result).to.have.property('histogram');
        });

        it('should return histogram with minutesAgo', async () => {
            const input = {
                sensorId: 'sensor123',
                minutesAgo: 60
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await occupancy.getHistogramOfAverageFovOccupancy(elasticDb, input);

            expect(result).to.have.property('bucketSizeInSec');
            expect(result).to.have.property('histogram');
        });

        it('should throw BadRequestError when neither time range nor minutesAgo provided', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await occupancy.getHistogramOfAverageFovOccupancy(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T12:00:00.000Z',
                toTimestamp: '2023-01-12T11:00:00.000Z'
            };

            try {
                await occupancy.getHistogramOfAverageFovOccupancy(elasticDb, input);
                throw new Error('Expected InvalidInputError');
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
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            try {
                await occupancy.getHistogramOfAverageFovOccupancy(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should filter by objectType when provided', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z',
                objectType: 'Person'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: [
                                {
                                    key_as_string: '2023-01-12T11:00:00.000Z',
                                    fov: {
                                        searchAggFilter: {
                                            objectType: {
                                                buckets: [
                                                    { key: 'Person', avgCount: { value: 5.5 } }
                                                ]
                                            }
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await occupancy.getHistogramOfAverageFovOccupancy(elasticDb, input);

            expect(result.histogram).to.be.an('array');
        });

        it('should throw BadRequestError when minutesAgo is Infinity (schema validation)', async () => {
            const input = {
                sensorId: 'sensor123',
                minutesAgo: Infinity
            };

            try {
                await occupancy.getHistogramOfAverageFovOccupancy(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                // Schema validation catches Infinity before Number.isFinite check
                expect(error).to.be.instanceOf(BadRequestError);
            }
        });

        it('should throw BadRequestError when bucketCount is Infinity (schema validation)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z',
                bucketCount: Infinity
            };

            try {
                await occupancy.getHistogramOfAverageFovOccupancy(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
            }
        });
    });

    describe('getRoiOccupancy', () => {
        it('should return roi occupancy with data', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        rois: {
                            searchAggFilter: {
                                roiIds: {
                                    buckets: [
                                        {
                                            key: 'roi1',
                                            objectType: {
                                                buckets: [
                                                    { key: 'Person', avgCount: { value: 5.5 }, uniqueObjectCount: { value: 10 } },
                                                    { key: 'Vehicle', avgCount: { value: 2.3 }, uniqueObjectCount: { value: 5 } }
                                                ]
                                            }
                                        },
                                        {
                                            key: 'roi2',
                                            objectType: {
                                                buckets: [
                                                    { key: 'Person', avgCount: { value: 3.0 }, uniqueObjectCount: { value: 8 } }
                                                ]
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            });

            const result = await occupancy.getRoiOccupancy(elasticDb, input);

            expect(result.roiOccupancy).to.be.an('array').with.lengthOf(2);
            expect(result.roiOccupancy[0]).to.deep.include({ roiId: 'roi1' });
            expect(result.roiOccupancy[0].objects).to.have.lengthOf(2);
            expect(result.roiOccupancy[1]).to.deep.include({ roiId: 'roi2' });
        });

        it('should return empty array when index is absent', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await occupancy.getRoiOccupancy(elasticDb, input);

            expect(result.roiOccupancy).to.be.an('array').that.is.empty;
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await occupancy.getRoiOccupancy(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T14:20:10.000Z',
                toTimestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await occupancy.getRoiOccupancy(elasticDb, input);
                throw new Error('Expected InvalidInputError');
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
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await occupancy.getRoiOccupancy(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should filter by roiId (line 697)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                roiId: 'roi1'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        rois: {
                            searchAggFilter: {
                                roiIds: {
                                    buckets: [
                                        {
                                            key: 'roi1',
                                            objectType: {
                                                buckets: [
                                                    { key: 'Person', avgCount: { value: 5.5 }, uniqueObjectCount: { value: 10 } }
                                                ]
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            });

            const result = await occupancy.getRoiOccupancy(elasticDb, input);

            expect(result.roiOccupancy).to.be.an('array');
        });

        it('should filter by objectType (line 757)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                objectType: 'Person'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        rois: {
                            searchAggFilter: {
                                roiIds: {
                                    buckets: [
                                        {
                                            key: 'roi1',
                                            objectType: {
                                                buckets: [
                                                    { key: 'Person', avgCount: { value: 5.5 }, uniqueObjectCount: { value: 10 } }
                                                ]
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            });

            const result = await occupancy.getRoiOccupancy(elasticDb, input);

            expect(result.roiOccupancy).to.be.an('array');
        });

        it('should filter by both roiId and objectType (lines 753, 758)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                roiId: 'roi1',
                objectType: 'Vehicle'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        rois: {
                            searchAggFilter: {
                                roiIds: {
                                    buckets: [
                                        {
                                            key: 'roi1',
                                            objectType: {
                                                buckets: [
                                                    { key: 'Vehicle', avgCount: { value: 3.0 }, uniqueObjectCount: { value: 6 } }
                                                ]
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            });

            const result = await occupancy.getRoiOccupancy(elasticDb, input);

            expect(result.roiOccupancy).to.be.an('array');
        });
    });

    describe('getHistogramOfRoiOccupancy', () => {
        it('should return histogram with roi data', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: [
                                {
                                    key_as_string: '2023-01-12T11:00:00.000Z',
                                    rois: {
                                        searchAggFilter: {
                                            roiIds: {
                                                buckets: [
                                                    {
                                                        key: 'roi1',
                                                        objectType: {
                                                            buckets: [
                                                                { key: 'Person', avgCount: { value: 5.5 }, uniqueObjectCount: { value: 10 } }
                                                            ]
                                                        }
                                                    }
                                                ]
                                            }
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await occupancy.getHistogramOfRoiOccupancy(elasticDb, input);

            expect(result).to.have.property('bucketSizeInSec');
            expect(result).to.have.property('rois');
            expect(result.rois).to.be.an('array');
        });

        it('should return rois with minutesAgo and roiId filter', async () => {
            const input = {
                sensorId: 'sensor123',
                minutesAgo: 60,
                roiId: 'roi1'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await occupancy.getHistogramOfRoiOccupancy(elasticDb, input);

            expect(result).to.have.property('rois');
        });

        it('should throw BadRequestError when neither time range nor minutesAgo provided', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await occupancy.getHistogramOfRoiOccupancy(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T12:00:00.000Z',
                toTimestamp: '2023-01-12T11:00:00.000Z'
            };

            try {
                await occupancy.getHistogramOfRoiOccupancy(elasticDb, input);
                throw new Error('Expected InvalidInputError');
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
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            try {
                await occupancy.getHistogramOfRoiOccupancy(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should filter by objectType (line 938)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z',
                objectType: 'Person'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: [
                                {
                                    key_as_string: '2023-01-12T11:00:00.000Z',
                                    rois: {
                                        searchAggFilter: {
                                            roiIds: {
                                                buckets: [
                                                    {
                                                        key: 'roi1',
                                                        objectType: {
                                                            buckets: [
                                                                { key: 'Person', avgCount: { value: 5.5 }, uniqueObjectCount: { value: 10 } }
                                                            ]
                                                        }
                                                    }
                                                ]
                                            }
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await occupancy.getHistogramOfRoiOccupancy(elasticDb, input);

            expect(result).to.have.property('rois');
        });

        it('should handle missing ROIs in buckets and fill with empty buckets (lines 1105, 1113-1118)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            // First bucket has roi1, second bucket is empty (missing roi1)
            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: [
                                {
                                    key_as_string: '2023-01-12T11:00:00.000Z',
                                    rois: {
                                        searchAggFilter: {
                                            roiIds: {
                                                buckets: [
                                                    {
                                                        key: 'roi1',
                                                        objectType: {
                                                            buckets: [
                                                                { key: 'Person', avgCount: { value: 5.5 }, uniqueObjectCount: { value: 10 } }
                                                            ]
                                                        }
                                                    }
                                                ]
                                            }
                                        }
                                    }
                                },
                                {
                                    key_as_string: '2023-01-12T11:30:00.000Z',
                                    rois: {
                                        searchAggFilter: {
                                            roiIds: {
                                                buckets: [] // roi1 missing in this bucket
                                            }
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await occupancy.getHistogramOfRoiOccupancy(elasticDb, input);

            expect(result).to.have.property('rois');
            // Should have roi1 with 2 buckets (one with data, one empty)
            if (result.rois.length > 0) {
                expect(result.rois[0].histogram.length).to.be.greaterThan(0);
            }
        });

        it('should trim histogram timestamps (lines 1138, 1141)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:30:00.000Z',
                toTimestamp: '2023-01-12T11:45:00.000Z',
                bucketCount: 2
            };

            // ES returns buckets that start before fromTimestamp and end after toTimestamp
            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: [
                                {
                                    key_as_string: '2023-01-12T11:00:00.000Z',
                                    rois: {
                                        searchAggFilter: {
                                            roiIds: {
                                                buckets: [
                                                    {
                                                        key: 'roi1',
                                                        objectType: {
                                                            buckets: [
                                                                { key: 'Person', avgCount: { value: 3 }, uniqueObjectCount: { value: 5 } }
                                                            ]
                                                        }
                                                    }
                                                ]
                                            }
                                        }
                                    }
                                },
                                {
                                    key_as_string: '2023-01-12T11:30:00.000Z',
                                    rois: {
                                        searchAggFilter: {
                                            roiIds: {
                                                buckets: [
                                                    {
                                                        key: 'roi1',
                                                        objectType: {
                                                            buckets: [
                                                                { key: 'Person', avgCount: { value: 4 }, uniqueObjectCount: { value: 6 } }
                                                            ]
                                                        }
                                                    }
                                                ]
                                            }
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await occupancy.getHistogramOfRoiOccupancy(elasticDb, input);

            expect(result).to.have.property('rois');
            if (result.rois.length > 0 && result.rois[0].histogram.length > 0) {
                // First bucket should be trimmed to fromTimestamp
                expect(result.rois[0].histogram[0].start).to.equal('2023-01-12T11:30:00.000Z');
                // Last bucket should be trimmed to toTimestamp
                const lastBucket = result.rois[0].histogram[result.rois[0].histogram.length - 1];
                expect(lastBucket.end).to.equal('2023-01-12T11:45:00.000Z');
            }
        });
    });

    describe('getUniqueObjectCountInMutuallyExclusiveRois', () => {
        it('should return unique object count with place', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                timestamp: '2023-01-12T11:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        sensorIds: {
                            buckets: [
                                {
                                    key: 'sensor1',
                                    lastProcessedRecord: {
                                        hits: {
                                            hits: [{
                                                _source: {
                                                    rois: [
                                                        { id: 'roi1', type: 'Person', count: 5, objectIds: [], coordinates: [] }
                                                    ]
                                                }
                                            }]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await occupancy.getUniqueObjectCountInMutuallyExclusiveRois(elasticDb, input);

            expect(result).to.have.property('occupancy');
            expect(result.occupancy).to.equal(5);
        });

        it('should return result without timestamp (uses timestampDelayInSec)', async () => {
            const input = {
                place: 'city=abc/building=xyz'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        sensorIds: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await occupancy.getUniqueObjectCountInMutuallyExclusiveRois(elasticDb, input);

            expect(result).to.have.property('place', 'city=abc/building=xyz');
            expect(result).to.have.property('occupancy');
        });

        it('should return 0 occupancy when index is absent', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                timestamp: '2023-01-12T11:20:10.000Z'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await occupancy.getUniqueObjectCountInMutuallyExclusiveRois(elasticDb, input);

            expect(result.occupancy).to.equal(0);
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {};

            try {
                await occupancy.getUniqueObjectCountInMutuallyExclusiveRois(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('place');
            }
        });

        it('should throw InvalidInputError for invalid timestamp', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                timestamp: 'invalid-timestamp'
            };

            try {
                await occupancy.getUniqueObjectCountInMutuallyExclusiveRois(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('timestamp');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const input = {
                place: 'city=abc/building=xyz',
                timestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await occupancy.getUniqueObjectCountInMutuallyExclusiveRois(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should return objectDetails when requested (lines 1305, 1318-1322)', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                timestamp: '2023-01-12T11:20:10.000Z',
                objectDetails: true
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        sensorIds: {
                            buckets: [
                                {
                                    key: 'sensor1',
                                    lastProcessedRecord: {
                                        hits: {
                                            hits: [{
                                                _source: {
                                                    rois: [
                                                        {
                                                            id: 'roi1',
                                                            type: 'Person',
                                                            count: 3,
                                                            objectIds: ['obj1', 'obj2', 'obj3'],
                                                            coordinates: [
                                                                { x: 1, y: 2 },
                                                                { x: 3, y: 4 },
                                                                { x: 5, y: 6 }
                                                            ]
                                                        }
                                                    ]
                                                }
                                            }]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await occupancy.getUniqueObjectCountInMutuallyExclusiveRois(elasticDb, input);

            expect(result).to.have.property('objectDetails');
            expect(result.objectDetails).to.be.an('array').with.lengthOf(1);
            expect(result.objectDetails[0]).to.deep.include({
                sensorId: 'sensor1',
                roiId: 'roi1'
            });
            expect(result.objectDetails[0].objects).to.have.length(3);
            expect(result.objectDetails[0].objects[0]).to.deep.include({
                id: 'obj1',
                coordinates: { x: 1, y: 2 }
            });
        });

        it('should filter ROIs by objectType (line 1315)', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                timestamp: '2023-01-12T11:20:10.000Z',
                objectType: 'Vehicle'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        sensorIds: {
                            buckets: [
                                {
                                    key: 'sensor1',
                                    lastProcessedRecord: {
                                        hits: {
                                            hits: [{
                                                _source: {
                                                    rois: [
                                                        { id: 'roi1', type: 'Person', count: 5, objectIds: [], coordinates: [] },
                                                        { id: 'roi2', type: 'Vehicle', count: 3, objectIds: [], coordinates: [] }
                                                    ]
                                                }
                                            }]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await occupancy.getUniqueObjectCountInMutuallyExclusiveRois(elasticDb, input);

            // Should only count Vehicle types
            expect(result.occupancy).to.equal(3);
        });
    });

    describe('getHistogramOfAverageOccupancyOfAPlace', () => {
        it('should return histogram with time range', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            // Two calls: RTLS and AMR
            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: [
                                {
                                    key_as_string: '2023-01-12T11:00:00.000Z',
                                    objectCounts: {
                                        objectType: {
                                            buckets: [
                                                { key: 'Person', avgCount: { value: 5.5 } }
                                            ]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await occupancy.getHistogramOfAverageOccupancyOfAPlace(elasticDb, input);

            expect(result).to.have.property('bucketSizeInSec');
            expect(result).to.have.property('histogram');
        });

        it('should return histogram with minutesAgo', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                minutesAgo: 60
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventsOverTime: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await occupancy.getHistogramOfAverageOccupancyOfAPlace(elasticDb, input);

            expect(result).to.have.property('histogram');
        });

        it('should return histogram with empty buckets when indices are absent', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            // Both RTLS and AMR indices are absent
            searchStub.resolves({ indexAbsent: true });

            const result = await occupancy.getHistogramOfAverageOccupancyOfAPlace(elasticDb, input);

            // When both indices are absent, returns empty histogram buckets
            expect(result.histogram).to.be.an('array');
        });

        it('should throw BadRequestError when neither time range nor minutesAgo provided', async () => {
            const input = {
                place: 'city=abc/building=xyz'
            };

            try {
                await occupancy.getHistogramOfAverageOccupancyOfAPlace(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                place: 'city=abc/building=xyz',
                fromTimestamp: '2023-01-12T12:00:00.000Z',
                toTimestamp: '2023-01-12T11:00:00.000Z'
            };

            try {
                await occupancy.getHistogramOfAverageOccupancyOfAPlace(elasticDb, input);
                throw new Error('Expected InvalidInputError');
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
                place: 'city=abc/building=xyz',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            try {
                await occupancy.getHistogramOfAverageOccupancyOfAPlace(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });
    });
});
