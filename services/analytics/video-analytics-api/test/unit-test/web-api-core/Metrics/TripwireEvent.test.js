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
const TripwireEvent = require('../../../../src/web-api-core/Metrics/TripwireEvent');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');

describe('TripwireEvent', () => {
    let tripwireEvent;
    let elasticDb;
    let searchStub;

    beforeEach(() => {
        tripwireEvent = new TripwireEvent();
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([
                ['indexPrefix', 'mdx-']
            ])
        };
        searchStub = sinon.stub(Elasticsearch, 'getSearchResults');
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('getTripwireCounts', () => {
        it('should return tripwire counts', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            // First call - detailed counts with objectTypes nested structure
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventIds: {
                            buckets: [
                                {
                                    key: 'tripwire1',
                                    objectTypes: {
                                        buckets: [
                                            {
                                                key: 'Person',
                                                eventTypes: {
                                                    buckets: [
                                                        { key: 'IN', doc_count: 10 },
                                                        { key: 'OUT', doc_count: 8 }
                                                    ]
                                                }
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            // Second call - effective counts (composite aggregation) with objectTypes
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: [
                                {
                                    key: { eventId: 'tripwire1', objectId: 'obj1', objectType: 'Person' },
                                    eventTypes: {
                                        buckets: [
                                            { key: 'IN', doc_count: 5 },
                                            { key: 'OUT', doc_count: 4 }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireCounts(elasticDb, input);

            expect(result).to.have.property('tripwireMetrics');
            expect(result).to.have.property('aggregatedMetrics');
            expect(result.tripwireMetrics).to.be.an('array');
            expect(result.aggregatedMetrics).to.have.property('events');
        });

        it('should return tripwire counts with tripwireId filter', async () => {
            const input = {
                sensorId: 'sensor123',
                tripwireId: 'tripwire1',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventIds: {
                            buckets: []
                        },
                        groupedBuckets: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireCounts(elasticDb, input);

            expect(result).to.have.property('tripwireMetrics');
        });

        it('should return tripwire counts with custom objectType', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z',
                objectType: 'Vehicle'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventIds: {
                            buckets: []
                        },
                        groupedBuckets: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireCounts(elasticDb, input);

            expect(result).to.have.property('tripwireMetrics');
        });

        it('should handle missing IN/OUT counts', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            // First call - detailed counts - only IN with objectTypes
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventIds: {
                            buckets: [
                                {
                                    key: 'tripwire1',
                                    objectTypes: {
                                        buckets: [
                                            {
                                                key: 'Person',
                                                eventTypes: {
                                                    buckets: [{ key: 'IN', doc_count: 10 }]
                                                }
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            // Second call - effective counts with objectType
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: [
                                {
                                    key: { eventId: 'tripwire1', objectId: 'obj1', objectType: 'Person' },
                                    eventTypes: {
                                        buckets: [{ key: 'IN', doc_count: 5 }]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireCounts(elasticDb, input);

            expect(result.tripwireMetrics).to.be.an('array');
        });

        it('should return empty results when index is absent', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await tripwireEvent.getTripwireCounts(elasticDb, input);

            expect(result.tripwireMetrics).to.be.an('array').that.is.empty;
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await tripwireEvent.getTripwireCounts(elasticDb, input);
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
                await tripwireEvent.getTripwireCounts(elasticDb, input);
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
                await tripwireEvent.getTripwireCounts(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should aggregate counts across multiple tripwires', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            // First call - detailed counts with multiple tripwires and objectTypes
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventIds: {
                            buckets: [
                                {
                                    key: 'tripwire1',
                                    objectTypes: {
                                        buckets: [{
                                            key: 'Person',
                                            eventTypes: {
                                                buckets: [
                                                    { key: 'IN', doc_count: 10 },
                                                    { key: 'OUT', doc_count: 8 }
                                                ]
                                            }
                                        }]
                                    }
                                },
                                {
                                    key: 'tripwire2',
                                    objectTypes: {
                                        buckets: [{
                                            key: 'Person',
                                            eventTypes: {
                                                buckets: [
                                                    { key: 'IN', doc_count: 5 },
                                                    { key: 'OUT', doc_count: 6 }
                                                ]
                                            }
                                        }]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            // Second call - effective counts (composite aggregation)
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: [
                                {
                                    key: { eventId: 'tripwire1', objectId: 'obj1', objectType: 'Person' },
                                    eventTypes: {
                                        buckets: [
                                            { key: 'IN', doc_count: 5 },
                                            { key: 'OUT', doc_count: 4 }
                                        ]
                                    }
                                },
                                {
                                    key: { eventId: 'tripwire2', objectId: 'obj2', objectType: 'Person' },
                                    eventTypes: {
                                        buckets: [
                                            { key: 'IN', doc_count: 2 },
                                            { key: 'OUT', doc_count: 3 }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireCounts(elasticDb, input);

            expect(result).to.have.property('tripwireMetrics');
            expect(result).to.have.property('aggregatedMetrics');
            expect(result.tripwireMetrics).to.be.an('array').with.length(2);
            expect(result.aggregatedMetrics).to.have.property('events');
        });

        it('should handle pagination in composite aggregation', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            // First call - detailed counts with objectTypes
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventIds: {
                            buckets: [{
                                key: 'tripwire1',
                                objectTypes: {
                                    buckets: [{
                                        key: 'Person',
                                        eventTypes: {
                                            buckets: [
                                                { key: 'IN', doc_count: 10 },
                                                { key: 'OUT', doc_count: 8 }
                                            ]
                                        }
                                    }]
                                }
                            }]
                        }
                    }
                }
            });

            // First page of composite aggregation
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: [{
                                key: { eventId: 'tripwire1', objectId: 'obj1', objectType: 'Person' },
                                eventTypes: {
                                    buckets: [
                                        { key: 'IN', doc_count: 3 },
                                        { key: 'OUT', doc_count: 2 }
                                    ]
                                }
                            }],
                            after_key: { eventId: 'tripwire1', objectId: 'obj1', objectType: 'Person' }
                        }
                    }
                }
            });

            // Second page of composite aggregation (last page)
            searchStub.onCall(2).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: [{
                                key: { eventId: 'tripwire1', objectId: 'obj2', objectType: 'Person' },
                                eventTypes: {
                                    buckets: [
                                        { key: 'IN', doc_count: 2 },
                                        { key: 'OUT', doc_count: 1 }
                                    ]
                                }
                            }]
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireCounts(elasticDb, input);

            expect(result).to.have.property('tripwireMetrics');
            expect(searchStub.callCount).to.equal(3);
        });

        it('should handle missing IN counts in detailed counts', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            // First call - detailed counts without IN, with objectTypes
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventIds: {
                            buckets: [{
                                key: 'tripwire1',
                                objectTypes: {
                                    buckets: [{
                                        key: 'Person',
                                        eventTypes: {
                                            buckets: [{ key: 'OUT', doc_count: 8 }]
                                        }
                                    }]
                                }
                            }]
                        }
                    }
                }
            });

            // Second call - effective counts
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: [{
                                key: { eventId: 'tripwire1', objectId: 'obj1', objectType: 'Person' },
                                eventTypes: {
                                    buckets: [{ key: 'OUT', doc_count: 2 }]
                                }
                            }]
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireCounts(elasticDb, input);

            expect(result).to.have.property('tripwireMetrics');
        });

        it('should warn when IN count exceeds OUT by more than 1', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            // First call - detailed counts with objectTypes
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventIds: {
                            buckets: [{
                                key: 'tripwire1',
                                objectTypes: {
                                    buckets: [{
                                        key: 'Person',
                                        eventTypes: {
                                            buckets: [
                                                { key: 'IN', doc_count: 10 },
                                                { key: 'OUT', doc_count: 5 }
                                            ]
                                        }
                                    }]
                                }
                            }]
                        }
                    }
                }
            });

            // Second call - effective counts with IN > OUT
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: [{
                                key: { eventId: 'tripwire1', objectId: 'obj1', objectType: 'Person' },
                                eventTypes: {
                                    buckets: [
                                        { key: 'IN', doc_count: 5 },
                                        { key: 'OUT', doc_count: 2 }
                                    ]
                                }
                            }]
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireCounts(elasticDb, input);

            expect(result).to.have.property('tripwireMetrics');
        });

        it('should warn when OUT count exceeds IN by more than 1', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            // First call - detailed counts with objectTypes
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        eventIds: {
                            buckets: [{
                                key: 'tripwire1',
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
                            }]
                        }
                    }
                }
            });

            // Second call - effective counts with OUT > IN
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: [{
                                key: { eventId: 'tripwire1', objectId: 'obj1', objectType: 'Person' },
                                eventTypes: {
                                    buckets: [
                                        { key: 'IN', doc_count: 2 },
                                        { key: 'OUT', doc_count: 5 }
                                    ]
                                }
                            }]
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireCounts(elasticDb, input);

            expect(result).to.have.property('tripwireMetrics');
        });
    });

    describe('getTripwireHistogram', () => {
        it('should return tripwire histogram with time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: [
                                {
                                    key: {
                                        eventId: 'tripwire1',
                                        bucketStartTime: new Date('2023-01-12T11:00:00.000Z').valueOf(),
                                        objectId: 'obj1'
                                    },
                                    eventTypes: {
                                        buckets: [
                                            { key: 'IN', doc_count: 3 },
                                            { key: 'OUT', doc_count: 2 }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireHistogram(elasticDb, input);

            expect(result).to.have.property('bucketSizeInSec');
            expect(result.bucketSizeInSec).to.be.a('number');
            expect(result).to.have.property('tripwires');
            expect(result.tripwires).to.be.an('array');
        });

        it('should return tripwire histogram with minutesAgo', async () => {
            const input = {
                sensorId: 'sensor123',
                minutesAgo: 60
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireHistogram(elasticDb, input);

            expect(result).to.have.property('bucketSizeInSec');
            expect(result).to.have.property('tripwires');
        });

        it('should return tripwire histogram with tripwireId filter', async () => {
            const input = {
                sensorId: 'sensor123',
                tripwireId: 'tripwire1',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireHistogram(elasticDb, input);

            expect(result).to.have.property('tripwires');
        });

        it('should return empty tripwires when index is absent', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await tripwireEvent.getTripwireHistogram(elasticDb, input);

            expect(result.tripwires).to.be.an('array').that.is.empty;
        });

        it('should handle pagination with after_key', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            // First call with after_key
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: [
                                {
                                    key: {
                                        eventId: 'tripwire1',
                                        bucketStartTime: new Date('2023-01-12T11:00:00.000Z').valueOf(),
                                        objectId: 'obj1'
                                    },
                                    eventTypes: {
                                        buckets: [{ key: 'IN', doc_count: 1 }]
                                    }
                                }
                            ],
                            after_key: { eventId: 'tripwire1', bucketStartTime: 123, objectId: 'obj1' }
                        }
                    }
                }
            });

            // Second call without after_key (end of pagination)
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireHistogram(elasticDb, input);

            expect(result).to.have.property('tripwires');
        });

        it('should throw BadRequestError when neither time range nor minutesAgo provided', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await tripwireEvent.getTripwireHistogram(elasticDb, input);
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
                await tripwireEvent.getTripwireHistogram(elasticDb, input);
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
                await tripwireEvent.getTripwireHistogram(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should use custom bucketCount', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z',
                bucketCount: 12
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: []
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireHistogram(elasticDb, input);

            expect(result).to.have.property('bucketSizeInSec');
        });

        it('should handle net count greater than 0 (more IN than OUT)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: [
                                {
                                    key: {
                                        eventId: 'tripwire1',
                                        bucketStartTime: new Date('2023-01-12T11:00:00.000Z').valueOf(),
                                        objectId: 'obj1'
                                    },
                                    eventTypes: {
                                        buckets: [
                                            { key: 'IN', doc_count: 5 },
                                            { key: 'OUT', doc_count: 2 }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireHistogram(elasticDb, input);

            expect(result.tripwires).to.be.an('array');
        });

        it('should handle net count less than 0 (more OUT than IN)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        groupedBuckets: {
                            buckets: [
                                {
                                    key: {
                                        eventId: 'tripwire1',
                                        bucketStartTime: new Date('2023-01-12T11:00:00.000Z').valueOf(),
                                        objectId: 'obj1'
                                    },
                                    eventTypes: {
                                        buckets: [
                                            { key: 'IN', doc_count: 2 },
                                            { key: 'OUT', doc_count: 5 }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await tripwireEvent.getTripwireHistogram(elasticDb, input);

            expect(result.tripwires).to.be.an('array');
        });

        it('should throw BadRequestError for Infinity minutesAgo', async () => {
            const input = {
                sensorId: 'sensor123',
                minutesAgo: Infinity
            };

            try {
                await tripwireEvent.getTripwireHistogram(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
            }
        });

        it('should throw BadRequestError for Infinity bucketCount', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:00:00.000Z',
                toTimestamp: '2023-01-12T12:00:00.000Z',
                bucketCount: Infinity
            };

            try {
                await tripwireEvent.getTripwireHistogram(elasticDb, input);
                throw new Error('Expected error');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
            }
        });
    });

});
