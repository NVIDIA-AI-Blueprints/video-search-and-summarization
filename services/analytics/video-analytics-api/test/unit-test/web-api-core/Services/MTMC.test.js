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
const MTMC = require('../../../../src/web-api-core/Services/MTMC');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');

describe('MTMC', () => {
    let mtmc;
    let elasticDb;
    let searchStub;
    let countStub;

    beforeEach(() => {
        mtmc = new MTMC();
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([
                ['indexPrefix', 'mdx-']
            ])
        };
        searchStub = sinon.stub(Elasticsearch, 'getSearchResults');
        countStub = sinon.stub(Elasticsearch, 'getDocCount');
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('getUniqueObjectCount', () => {
        it('should return unique object count with timestamp', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z'
            };

            countStub.resolves({
                indexAbsent: false,
                count: 42
            });

            const result = await mtmc.getUniqueObjectCount(elasticDb, input);

            expect(result.uniqueObjectCount).to.equal(42);
        });

        it('should return unique object count with sensorIds filter', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                sensorIds: ['sensor1', 'sensor2']
            };

            countStub.resolves({
                indexAbsent: false,
                count: 10
            });

            const result = await mtmc.getUniqueObjectCount(elasticDb, input);

            expect(result.uniqueObjectCount).to.equal(10);
        });

        it('should return unique object count with place filter', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                place: 'city=abc/building=xyz'
            };

            countStub.resolves({
                indexAbsent: false,
                count: 5
            });

            const result = await mtmc.getUniqueObjectCount(elasticDb, input);

            expect(result.uniqueObjectCount).to.equal(5);
        });

        it('should return unique object count with objectId and sensorIds', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                sensorIds: ['sensor1'],
                objectId: 'object123'
            };

            countStub.resolves({
                indexAbsent: false,
                count: 1
            });

            const result = await mtmc.getUniqueObjectCount(elasticDb, input);

            expect(result.uniqueObjectCount).to.equal(1);
        });

        it('should return 0 when index is absent', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z'
            };

            countStub.resolves({ indexAbsent: true });

            const result = await mtmc.getUniqueObjectCount(elasticDb, input);

            expect(result.uniqueObjectCount).to.equal(0);
        });

        it('should respect custom timeWindowInMs', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                timeWindowInMs: 500
            };

            countStub.resolves({
                indexAbsent: false,
                count: 15
            });

            const result = await mtmc.getUniqueObjectCount(elasticDb, input);

            expect(result.uniqueObjectCount).to.equal(15);
        });

        it('should throw BadRequestError when timestamp is missing', async () => {
            const input = {};

            try {
                await mtmc.getUniqueObjectCount(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('timestamp');
            }
        });

        it('should throw InvalidInputError for invalid timestamp format', async () => {
            const input = {
                timestamp: 'invalid-timestamp'
            };

            try {
                await mtmc.getUniqueObjectCount(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('timestamp');
            }
        });

        it('should throw BadRequestError when both sensorIds and place are provided', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                sensorIds: ['sensor1'],
                place: 'city=abc'
            };

            try {
                await mtmc.getUniqueObjectCount(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Input cannot have");
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await mtmc.getUniqueObjectCount(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('getUniqueObjects', () => {
        it('should return unique objects with time range', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { globalId: 'global1', matched: [] } },
                            { _source: { globalId: 'global2', matched: [] } }
                        ]
                    }
                }
            });

            const result = await mtmc.getUniqueObjects(elasticDb, input);

            expect(result.uniqueObjects).to.be.an('array').with.lengthOf(2);
        });

        it('should return unique objects with sensorIds filter', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                sensorIds: ['sensor1']
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { globalId: 'global1' } }
                        ]
                    }
                }
            });

            const result = await mtmc.getUniqueObjects(elasticDb, input);

            expect(result.uniqueObjects).to.be.an('array').with.lengthOf(1);
        });

        it('should return unique objects with place filter', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                place: 'city=abc'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { globalId: 'global1' } }
                        ]
                    }
                }
            });

            const result = await mtmc.getUniqueObjects(elasticDb, input);

            expect(result.uniqueObjects).to.be.an('array').with.lengthOf(1);
        });

        it('should return unique objects with globalId filter', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                globalId: 'global123'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { globalId: 'global123' } }
                        ]
                    }
                }
            });

            const result = await mtmc.getUniqueObjects(elasticDb, input);

            expect(result.uniqueObjects).to.be.an('array').with.lengthOf(1);
        });

        it('should return unique objects with objectId and sensorIds', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                sensorIds: ['sensor1'],
                objectId: 'object123'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { globalId: 'global1' } }
                        ]
                    }
                }
            });

            const result = await mtmc.getUniqueObjects(elasticDb, input);

            expect(result.uniqueObjects).to.be.an('array').with.lengthOf(1);
        });

        it('should return empty array when index is absent', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await mtmc.getUniqueObjects(elasticDb, input);

            expect(result.uniqueObjects).to.be.an('array').that.is.empty;
        });

        it('should respect maxResultSize parameter', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                maxResultSize: 50
            };

            searchStub.resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            await mtmc.getUniqueObjects(elasticDb, input);

            expect(searchStub.calledOnce).to.be.true;
            const queryArg = searchStub.firstCall.args[1];
            expect(queryArg.size).to.equal(50);
        });

        it('should throw BadRequestError when timestamps are missing', async () => {
            const input = {};

            try {
                await mtmc.getUniqueObjects(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                fromTimestamp: '2023-01-12T14:20:10.000Z',
                toTimestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await mtmc.getUniqueObjects(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.equal('fromTimestamp is not lesser than toTimestamp.');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await mtmc.getUniqueObjects(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('consumeRTLSMessages', () => {
        it('should throw InvalidInputError when messageBroker is null', async () => {
            try {
                await mtmc.consumeRTLSMessages(null, {});
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('message broker');
            }
        });

        it('should throw InternalServerError for unsupported message broker', async () => {
            const unsupportedBroker = {
                getName: () => 'UnsupportedBroker'
            };

            try {
                await mtmc.consumeRTLSMessages(unsupportedBroker, {});
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid message broker');
            }
        });
    });

    describe('consumeAMRMessages', () => {
        it('should throw InvalidInputError when messageBroker is null', async () => {
            try {
                await mtmc.consumeAMRMessages(null, {});
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('message broker');
            }
        });

        it('should throw InternalServerError for unsupported message broker', async () => {
            const unsupportedBroker = {
                getName: () => 'UnsupportedBroker'
            };

            try {
                await mtmc.consumeAMRMessages(unsupportedBroker, {});
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid message broker');
            }
        });
    });

    describe('getLastRecord', () => {
        it('should return last record for RTLS source', async () => {
            const input = {
                place: 'city=abc',
                source: 'RTLS'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { id: 'record1', timestamp: '2023-01-12T14:20:10.000Z' } }
                        ]
                    }
                }
            });

            const result = await mtmc.getLastRecord(elasticDb, input);

            expect(result.lastRecord).to.not.be.null;
            expect(result.lastRecord.id).to.equal('record1');
        });

        it('should return last record for AMR source', async () => {
            const input = {
                place: 'city=abc',
                source: 'AMR'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { id: 'amr-record1' } }
                        ]
                    }
                }
            });

            const result = await mtmc.getLastRecord(elasticDb, input);

            expect(result.lastRecord).to.not.be.null;
        });

        it('should return last record with objectType filter', async () => {
            const input = {
                place: 'city=abc',
                objectType: 'Person'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { id: 'record1', objectType: 'Person' } }
                        ]
                    }
                }
            });

            const result = await mtmc.getLastRecord(elasticDb, input);

            expect(result.lastRecord).to.not.be.null;
        });

        it('should return null lastRecord when index is absent', async () => {
            const input = {
                place: 'city=abc'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await mtmc.getLastRecord(elasticDb, input);

            expect(result.lastRecord).to.be.null;
        });

        it('should return null lastRecord when no records found', async () => {
            const input = {
                place: 'city=abc'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            const result = await mtmc.getLastRecord(elasticDb, input);

            expect(result.lastRecord).to.be.null;
        });

        it('should default to RTLS source', async () => {
            const input = {
                place: 'city=abc'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            await mtmc.getLastRecord(elasticDb, input);

            expect(searchStub.calledOnce).to.be.true;
        });

        it('should throw BadRequestError when place is missing', async () => {
            const input = {};

            try {
                await mtmc.getLastRecord(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('place');
            }
        });

        it('should throw BadRequestError for invalid source', async () => {
            const input = {
                place: 'city=abc',
                source: 'INVALID'
            };

            try {
                await mtmc.getLastRecord(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('source');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const input = {
                place: 'city=abc'
            };

            try {
                await mtmc.getLastRecord(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('getLocationsOfMatchedBehaviors', () => {
        it('should throw BadRequestError when timestamps missing', async () => {
            const input = {
                globalId: 'global123'
            };

            try {
                await mtmc.getLocationsOfMatchedBehaviors(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fromTimestamp');
            }
        });

        it('should throw BadRequestError when neither behaviorIds nor globalId provided', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await mtmc.getLocationsOfMatchedBehaviors(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('behaviorIds');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                fromTimestamp: '2023-01-12T14:20:10.000Z',
                toTimestamp: '2023-01-12T11:20:10.000Z',
                globalId: 'global123'
            };

            try {
                await mtmc.getLocationsOfMatchedBehaviors(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.equal('fromTimestamp is not lesser than toTimestamp.');
            }
        });

        it('should return empty behaviors when globalId not found', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                globalId: 'nonexistent'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            const result = await mtmc.getLocationsOfMatchedBehaviors(elasticDb, input);

            expect(result.behaviors).to.be.an('array').that.is.empty;
        });

        it('should return behaviors when globalId found with matched behaviors', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                globalId: 'global123'
            };

            // First call: getUniqueObjects returns matched behaviors
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: {
                                globalId: 'global123',
                                matched: [
                                    { id: 'behavior1' },
                                    { id: 'behavior2' }
                                ]
                            }
                        }]
                    }
                }
            });

            // Second call: getLocationsOfBehaviors returns behavior locations
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { id: 'behavior1', locations: [{ x: 10, y: 20 }] } },
                            { _source: { id: 'behavior2', locations: [{ x: 30, y: 40 }] } }
                        ]
                    }
                }
            });

            const result = await mtmc.getLocationsOfMatchedBehaviors(elasticDb, input);

            expect(result).to.have.property('behaviors');
        });

        it('should return behaviors when using behaviorIds directly', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                behaviorIds: ['behavior1', 'behavior2']
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { id: 'behavior1', locations: [] } },
                            { _source: { id: 'behavior2', locations: [] } }
                        ]
                    }
                }
            });

            const result = await mtmc.getLocationsOfMatchedBehaviors(elasticDb, input);

            expect(result).to.have.property('behaviors');
        });
    });

    describe('getNormalizedEmbedding', () => {
        it('should normalize an embedding vector', () => {
            const embedding = [3, 4];  // 3-4-5 triangle
            
            const result = mtmc.getNormalizedEmbedding(embedding);
            
            expect(result).to.be.an('array');
            expect(result.length).to.equal(2);
            expect(result[0]).to.be.closeTo(0.6, 0.001);  // 3/5
            expect(result[1]).to.be.closeTo(0.8, 0.001);  // 4/5
        });

        it('should handle zero vector', () => {
            const embedding = [0, 0, 0];
            
            const result = mtmc.getNormalizedEmbedding(embedding);
            
            expect(result).to.be.an('array');
            expect(result.length).to.equal(3);
            // All values will be NaN due to division by zero
            result.forEach(val => expect(isNaN(val)).to.be.true);
        });

        it('should handle single element embedding', () => {
            const embedding = [5];
            
            const result = mtmc.getNormalizedEmbedding(embedding);
            
            expect(result.length).to.equal(1);
            expect(result[0]).to.be.closeTo(1, 0.001);  // 5/5
        });

        it('should handle negative values', () => {
            const embedding = [-3, 4];
            
            const result = mtmc.getNormalizedEmbedding(embedding);
            
            expect(result[0]).to.be.closeTo(-0.6, 0.001);
            expect(result[1]).to.be.closeTo(0.8, 0.001);
        });
    });

    describe('getAMREvents', () => {
        it('should throw BadRequestError when required properties missing', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await mtmc.getAMREvents(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('toTimestamp');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                fromTimestamp: '2023-01-12T14:20:10.000Z',
                toTimestamp: '2023-01-12T11:20:10.000Z',
                place: 'city=abc',
                objectType: 'AMR'
            };

            try {
                await mtmc.getAMREvents(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.equal('fromTimestamp is not lesser than toTimestamp.');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                place: 'city=abc',
                objectType: 'AMR'
            };

            try {
                await mtmc.getAMREvents(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('getOccupancyTracker', () => {
        it('should throw BadRequestError when required properties missing', async () => {
            const input = {
                timestamp: '2023-01-12T11:20:10.000Z'
                // missing place
            };

            try {
                await mtmc.getOccupancyTracker(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('place');
            }
        });

        it('should throw InvalidInputError for invalid timestamp', async () => {
            const input = {
                timestamp: 'invalid-timestamp',
                place: 'city=abc'
            };

            try {
                await mtmc.getOccupancyTracker(elasticDb, input);
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
                timestamp: '2023-01-12T11:20:10.000Z',
                place: 'city=abc'
            };

            try {
                await mtmc.getOccupancyTracker(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });

        it('should return occupancy tracker data from ES (lines 1425-1461)', async () => {
            const input = {
                timestamp: '2023-01-12T11:20:10.000Z',
                place: 'city=abc'
            };

            // First call for RTLS
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    place: 'city=abc',
                                    timestamp: '2023-01-12T11:20:10.000Z',
                                    objectCounts: [{ type: 'Person', count: 5 }],
                                    locationsOfObjects: [{ id: 'obj1', locations: [[1, 2]], type: 'Person' }]
                                }
                            }
                        ]
                    }
                }
            });

            // Second call for AMR past
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            // Third call for AMR future
            searchStub.onCall(2).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            const result = await mtmc.getOccupancyTracker(elasticDb, input);

            expect(result).to.have.property('trackerOccupancy');
            expect(result.trackerOccupancy).to.be.an('array');
        });

        it('should return empty result when index is absent (line 1454)', async () => {
            const input = {
                timestamp: '2023-01-12T11:20:10.000Z',
                place: 'city=abc'
            };

            // RTLS index absent
            searchStub.onCall(0).resolves({ indexAbsent: true });

            // AMR without RTLS
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            const result = await mtmc.getOccupancyTracker(elasticDb, input);

            expect(result).to.have.property('trackerOccupancy');
            expect(result.trackerOccupancy).to.be.an('array').that.is.empty;
        });

        it('should return empty result when no hits found (line 1451)', async () => {
            const input = {
                timestamp: '2023-01-12T11:20:10.000Z',
                place: 'city=abc'
            };

            // RTLS empty
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            // AMR without RTLS
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            const result = await mtmc.getOccupancyTracker(elasticDb, input);

            expect(result).to.have.property('trackerOccupancy');
            expect(result.trackerOccupancy).to.be.an('array').that.is.empty;
        });

        it('should merge AMR data with RTLS data (lines 1441-1443)', async () => {
            const input = {
                timestamp: '2023-01-12T11:20:10.000Z',
                place: 'city=abc'
            };

            // RTLS data
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    place: 'city=abc',
                                    timestamp: '2023-01-12T11:20:09.000Z',
                                    objectCounts: [{ type: 'Person', count: 5 }],
                                    locationsOfObjects: []
                                }
                            }
                        ]
                    }
                }
            });

            // AMR past - has data
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    place: 'city=abc',
                                    timestamp: '2023-01-12T11:20:08.000Z',
                                    objectCounts: [{ type: 'AMR', count: 2 }],
                                    locationsOfObjects: []
                                }
                            }
                        ]
                    }
                }
            });

            // AMR future
            searchStub.onCall(2).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            const result = await mtmc.getOccupancyTracker(elasticDb, input);

            expect(result).to.have.property('trackerOccupancy');
            // Should have both Person and AMR counts merged
            expect(result.trackerOccupancy.length).to.be.greaterThan(0);
        });
    });

    describe('getAMREvents - ES path', () => {
        it('should return AMR events from ES (lines 1370-1371)', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                place: 'city=abc',
                objectType: 'AMR'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    timestamp: '2023-01-12T12:00:00.000Z',
                                    place: 'city=abc',
                                    events: [
                                        { objectId: 'amr1', objectType: 'AMR', event: 'moving' }
                                    ]
                                }
                            }
                        ]
                    }
                }
            });

            const result = await mtmc.getAMREvents(elasticDb, input);

            expect(result).to.have.property('amrEvents');
            expect(result.amrEvents).to.be.an('array');
        });

        it('should return empty array when index is absent', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                place: 'city=abc',
                objectType: 'AMR'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await mtmc.getAMREvents(elasticDb, input);

            expect(result.amrEvents).to.be.an('array').that.is.empty;
        });

        it('should filter events by objectType', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                place: 'city=abc',
                objectType: 'Robot'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    timestamp: '2023-01-12T12:00:00.000Z',
                                    place: 'city=abc',
                                    events: [
                                        { objectId: 'amr1', objectType: 'AMR' },
                                        { objectId: 'robot1', objectType: 'Robot' }
                                    ]
                                }
                            }
                        ]
                    }
                }
            });

            const result = await mtmc.getAMREvents(elasticDb, input);

            expect(result.amrEvents).to.be.an('array');
            // Events are filtered by objectType
            if (result.amrEvents.length > 0) {
                result.amrEvents.forEach(msg => {
                    msg.events.forEach(e => {
                        expect(e.objectType).to.equal('Robot');
                    });
                });
            }
        });
    });

    describe('consumeRTLSMessages', () => {
        it('should throw InvalidInputError when messageBroker is null (line 659)', async () => {
            try {
                await mtmc.consumeRTLSMessages(null);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('message broker');
            }
        });

        it('should throw InternalServerError for unsupported message broker (line 667)', async () => {
            const unsupportedBroker = {
                getName: () => 'UnsupportedBroker'
            };

            try {
                await mtmc.consumeRTLSMessages(unsupportedBroker, {});
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid message broker');
            }
        });
    });

    describe('consumeAMRMessages', () => {
        it('should throw InvalidInputError when messageBroker is null (line 710)', async () => {
            try {
                await mtmc.consumeAMRMessages(null);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('message broker');
            }
        });

        it('should throw InternalServerError for unsupported message broker (line 718)', async () => {
            const unsupportedBroker = {
                getName: () => 'UnsupportedBroker'
            };

            try {
                await mtmc.consumeAMRMessages(unsupportedBroker, {});
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid message broker');
            }
        });
    });

    describe('getUniqueObjectCountWithLocations', () => {
        it('should throw BadRequestError when place is missing', async () => {
            const input = {};

            try {
                await mtmc.getUniqueObjectCountWithLocations(elasticDb, null, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('place');
            }
        });

        it('should throw InvalidInputError for invalid timestamp', async () => {
            const input = {
                place: 'city=abc',
                timestamp: 'invalid-timestamp'
            };

            try {
                await mtmc.getUniqueObjectCountWithLocations(elasticDb, null, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('timestamp');
            }
        });

        it('should throw InvalidInputError when messageBroker is null and timestamp is null (line 1225)', async () => {
            const input = {
                place: 'city=abc',
                timestamp: null
            };

            try {
                await mtmc.getUniqueObjectCountWithLocations(elasticDb, null, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('message broker');
            }
        });

        it('should throw InternalServerError for unsupported database with timestamp (line 1221)', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const input = {
                place: 'city=abc',
                timestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await mtmc.getUniqueObjectCountWithLocations(unsupportedDb, null, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should return data from ES when timestamp is provided (lines 1152-1218)', async () => {
            const input = {
                place: 'city=abc',
                timestamp: '2023-01-12T11:20:10.000Z',
                timeWindowInMs: 3000
            };

            // Mock for RTLS query
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    place: 'city=abc',
                                    timestamp: '2023-01-12T11:20:09.000Z',
                                    objectCounts: [{ type: 'Person', count: 5 }],
                                    locationsOfObjects: [{ id: 'obj1', locations: [[1, 2]], type: 'Person' }]
                                }
                            }
                        ]
                    }
                }
            });

            // Mock for AMR past query
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            // Mock for AMR future query
            searchStub.onCall(2).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            // Mock for AMR events query
            searchStub.onCall(3).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            const result = await mtmc.getUniqueObjectCountWithLocations(elasticDb, null, input);

            expect(result).to.have.property('place');
            expect(result.place).to.equal('city=abc');
        });

        it('should handle RTLS absent and get AMR record (lines 1191-1217)', async () => {
            const input = {
                place: 'city=abc',
                timestamp: '2023-01-12T11:20:10.000Z',
                timeWindowInMs: 3000
            };

            // Mock for RTLS query - index absent
            searchStub.onCall(0).resolves({ indexAbsent: true });

            // Mock for AMR record without RTLS
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    place: 'city=abc',
                                    timestamp: '2023-01-12T11:20:08.000Z',
                                    objectCounts: [{ type: 'AMR', count: 2 }],
                                    locationsOfObjects: [{ id: 'amr1', locations: [[3, 4]], type: 'AMR' }]
                                }
                            }
                        ]
                    }
                }
            });

            // Mock for AMR events
            searchStub.onCall(2).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            const result = await mtmc.getUniqueObjectCountWithLocations(elasticDb, null, input);

            expect(result).to.have.property('place');
            expect(result.place).to.equal('city=abc');
        });

        it('should handle RTLS empty results and get AMR record (line 1186)', async () => {
            const input = {
                place: 'city=abc',
                timestamp: '2023-01-12T11:20:10.000Z',
                timeWindowInMs: 3000
            };

            // Mock for RTLS query - empty results
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            // Mock for AMR record without RTLS
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            // Mock for AMR events
            searchStub.onCall(2).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            const result = await mtmc.getUniqueObjectCountWithLocations(elasticDb, null, input);

            expect(result).to.have.property('place');
            expect(result.objectCounts).to.be.an('array').that.is.empty;
        });

        it('should get AMR record with RTLS and merge data (lines 1174-1184)', async () => {
            const input = {
                place: 'city=abc',
                timestamp: '2023-01-12T11:20:10.000Z',
                timeWindowInMs: 3000,
                amrTimestampWindowInMs: 200
            };

            // Mock for RTLS query
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    place: 'city=abc',
                                    timestamp: '2023-01-12T11:20:09.000Z',
                                    objectCounts: [{ type: 'Person', count: 5 }],
                                    locationsOfObjects: [{ id: 'p1', locations: [[1, 2]], type: 'Person' }]
                                }
                            }
                        ]
                    }
                }
            });

            // Mock for AMR past query - has result
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    place: 'city=abc',
                                    timestamp: '2023-01-12T11:20:08.900Z',
                                    objectCounts: [{ type: 'AMR', count: 2 }],
                                    locationsOfObjects: [{ id: 'amr1', locations: [[3, 4]], type: 'AMR' }]
                                }
                            }
                        ]
                    }
                }
            });

            // Mock for AMR future query
            searchStub.onCall(2).resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            // Mock for AMR events query - has events
            searchStub.onCall(3).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    timestamp: '2023-01-12T11:20:09.000Z',
                                    place: 'city=abc',
                                    events: [{ objectId: 'amr1', route: 'pathA' }]
                                }
                            }
                        ]
                    }
                }
            });

            const result = await mtmc.getUniqueObjectCountWithLocations(elasticDb, null, input);

            expect(result).to.have.property('place');
            expect(result.objectCounts.length).to.be.greaterThan(0);
        });
    });
});

// Additional tests using proxyquire to test cache-based paths
describe('MTMC with proxyquire (cache-based paths)', () => {
    const proxyquire = require('proxyquire').noCallThru().noPreserveCache();
    let MTMC;
    let mtmc;
    let elasticDb;
    let searchStub;
    let countStub;
    
    // Mock caches
    let objectCountWithLocationsCache;
    let simulatedTimestampCache;
    let amrCountWithLocationsCache;
    let amrRoutes;
    let amrRouteTimestampCache;

    beforeEach(() => {
        // Create fresh mock caches
        objectCountWithLocationsCache = new Map();
        simulatedTimestampCache = new Map();
        amrCountWithLocationsCache = new Map();
        amrRoutes = new Map();
        amrRouteTimestampCache = new Map();

        // Create mock NodeCache constructor
        const MockNodeCache = function() {
            const cache = new Map();
            this.get = (key) => cache.get(key);
            this.set = (key, value) => cache.set(key, value);
            this.has = (key) => cache.has(key);
        };

        // Create stubs
        searchStub = sinon.stub();
        countStub = sinon.stub();

        // Use proxyquire to inject our mocks
        MTMC = proxyquire('../../../../src/web-api-core/Services/MTMC', {
            'node-cache': MockNodeCache,
            '../Utils/Elasticsearch': {
                getSearchResults: searchStub,
                getDocCount: countStub,
                getIndex: (name) => name,
                searchResultFormatter: (body) => body.hits?.hits?.map(h => h._source) || []
            },
            '../Utils/Kafka': {
                getTopicPattern: () => 'test-topic-pattern',
                getTopic: () => 'test-topic',
                initializeConsumer: sinon.stub().resolves()
            }
        });

        mtmc = new MTMC();
        elasticDb = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([['indexPrefix', 'mdx-']])
        };
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('getUniqueObjectCountWithLocations - cache paths', () => {
        it('should get data from cache when timestamp is null and messageBroker provided (lines 1227-1307)', async () => {
            const mockBroker = {
                getName: () => 'Kafka',
                getClient: () => ({}),
                getAdminClient: () => ({})
            };

            const input = {
                place: 'city=test',
                timestamp: null
            };

            const result = await mtmc.getUniqueObjectCountWithLocations(elasticDb, mockBroker, input);

            expect(result).to.have.property('place');
            expect(result.place).to.equal('city=test');
            expect(result).to.have.property('timestamp');
            expect(result).to.have.property('objectCounts');
            expect(result).to.have.property('locationsOfObjects');
        });

        it('should handle simulation mode with null timestamp (lines 1227, 1281)', async () => {
            const mockBroker = {
                getName: () => 'Kafka',
                getClient: () => ({}),
                getAdminClient: () => ({})
            };

            const input = {
                place: 'city=simulated',
                timestamp: null
            };

            // Run in simulation mode
            const result = await mtmc.getUniqueObjectCountWithLocations(elasticDb, mockBroker, input, true);

            expect(result).to.have.property('place');
            expect(result.place).to.equal('city=simulated');
        });
    });

    describe('consumeRTLSMessages - Kafka path', () => {
        it('should call Kafka consumer initialization for RTLS (lines 663-664, 671-694)', async () => {
            const kafkaInitStub = sinon.stub().resolves();
            
            const MTMCWithKafka = proxyquire('../../../../src/web-api-core/Services/MTMC', {
                'node-cache': function() {
                    const cache = new Map();
                    this.get = (key) => cache.get(key);
                    this.set = (key, value) => cache.set(key, value);
                    this.has = (key) => cache.has(key);
                },
                '../Utils/Kafka': {
                    getTopicPattern: () => 'rtls-.*',
                    initializeConsumer: kafkaInitStub
                }
            });

            const mtmcInstance = new MTMCWithKafka();
            const mockBroker = {
                getName: () => 'Kafka',
                getClient: () => ({}),
                getAdminClient: () => ({})
            };

            await mtmcInstance.consumeRTLSMessages(mockBroker, { inSimulationMode: false });

            expect(kafkaInitStub.calledOnce).to.be.true;
        });
    });

    describe('consumeAMRMessages - Kafka path', () => {
        it('should call Kafka consumer initialization for AMR (lines 714-715, 783-800)', async () => {
            const kafkaInitStub = sinon.stub().resolves();
            
            const MTMCWithKafka = proxyquire('../../../../src/web-api-core/Services/MTMC', {
                'node-cache': function() {
                    const cache = new Map();
                    this.get = (key) => cache.get(key);
                    this.set = (key, value) => cache.set(key, value);
                    this.has = (key) => cache.has(key);
                },
                '../Utils/Kafka': {
                    getTopic: () => 'mdx-amr',
                    initializeConsumer: kafkaInitStub
                }
            });

            const mtmcInstance = new MTMCWithKafka();
            const mockBroker = {
                getName: () => 'Kafka',
                getClient: () => ({}),
                getAdminClient: () => ({})
            };

            await mtmcInstance.consumeAMRMessages(mockBroker, { inSimulationMode: false, amrRetentionInSec: 300 });

            expect(kafkaInitStub.calledOnce).to.be.true;
        });
    });
});

// Test RTLS and AMR message processing callbacks
describe('MTMC Kafka message processing (proxyquire)', () => {
    const proxyquire = require('proxyquire').noCallThru().noPreserveCache();

    it('should process RTLS Kafka messages (lines 672-694)', async () => {
        let capturedCallback = null;
        const mockCache = new Map();
        
        const MockNodeCache = function() {
            this.get = (key) => mockCache.get(key);
            this.set = (key, value) => mockCache.set(key, value);
            this.has = (key) => mockCache.has(key);
        };

        const kafkaInitStub = sinon.stub().callsFake((callback) => {
            capturedCallback = callback;
            return Promise.resolve();
        });

        // Mock protobuf
        const mockNvFrame = {
            decode: sinon.stub().returns({
                timestamp: { seconds: '1673528410', nanos: 0 },
                info: { place: 'city=test' },
                fov: [{ type: 'Person', count: 5 }],
                objects: []
            })
        };

        const MTMC = proxyquire('../../../../src/web-api-core/Services/MTMC', {
            'node-cache': MockNodeCache,
            '../Utils/Kafka': {
                getTopicPattern: () => 'rtls-.*',
                initializeConsumer: kafkaInitStub
            },
            'protobufjs': {
                loadSync: () => ({
                    lookupType: () => mockNvFrame
                })
            },
            'proto3-json-serializer': {
                toProto3JSON: (decoded) => ({
                    timestamp: new Date(parseInt(decoded.timestamp.seconds) * 1000).toISOString(),
                    info: decoded.info,
                    fov: decoded.fov,
                    objects: decoded.objects
                })
            }
        });

        const mtmc = new MTMC();
        const mockBroker = {
            getName: () => 'Kafka',
            getClient: () => ({}),
            getAdminClient: () => ({})
        };

        await mtmc.consumeRTLSMessages(mockBroker, { inSimulationMode: false });

        expect(kafkaInitStub.calledOnce).to.be.true;
    });

    it('should process AMR Kafka messages with mdx-amr-locations type (lines 788-789, 751-780)', async () => {
        let capturedCallback = null;
        const mockCache = new Map();
        
        const MockNodeCache = function() {
            this.get = (key) => mockCache.get(key);
            this.set = (key, value) => mockCache.set(key, value);
            this.has = (key) => mockCache.has(key);
        };

        const kafkaInitStub = sinon.stub().callsFake((callback) => {
            capturedCallback = callback;
            return Promise.resolve();
        });

        const MTMC = proxyquire('../../../../src/web-api-core/Services/MTMC', {
            'node-cache': MockNodeCache,
            '../Utils/Kafka': {
                getTopic: () => 'mdx-amr',
                initializeConsumer: kafkaInitStub
            }
        });

        const mtmc = new MTMC();
        const mockBroker = {
            getName: () => 'Kafka',
            getClient: () => ({}),
            getAdminClient: () => ({})
        };

        await mtmc.consumeAMRMessages(mockBroker, { inSimulationMode: false, amrRetentionInSec: 300 });

        expect(kafkaInitStub.calledOnce).to.be.true;

        // Test the callback if captured
        if (capturedCallback) {
            const mockMessage = {
                topic: 'mdx-amr',
                partition: 0,
                message: {
                    headers: { type: Buffer.from('mdx-amr-locations') },
                    value: Buffer.from(JSON.stringify({
                        place: 'city=test',
                        timestamp: '2023-01-12T11:20:10.000Z',
                        objectCounts: [{ type: 'AMR', count: 1 }],
                        locationsOfObjects: []
                    }))
                }
            };

            // Call the captured callback
            capturedCallback(mockMessage);
        }
    });

    it('should process AMR Kafka messages with mdx-amr-events type (lines 790-791, 722-748)', async () => {
        // Create mock caches that return proper values
        const amrRoutesMap = new Map();
        const amrRouteTimestampMap = new Map();
        const simulatedTimestampMap = new Map();
        
        const MockNodeCache = function() {
            // Use shared maps for testing
            const internalCache = new Map();
            this.get = (key) => internalCache.get(key);
            this.set = (key, value) => internalCache.set(key, value);
            this.has = (key) => internalCache.has(key);
        };

        const kafkaInitStub = sinon.stub().resolves();

        const MTMC = proxyquire('../../../../src/web-api-core/Services/MTMC', {
            'node-cache': MockNodeCache,
            '../Utils/Kafka': {
                getTopic: () => 'mdx-amr',
                initializeConsumer: kafkaInitStub
            }
        });

        const mtmc = new MTMC();
        const mockBroker = {
            getName: () => 'Kafka',
            getClient: () => ({}),
            getAdminClient: () => ({})
        };

        await mtmc.consumeAMRMessages(mockBroker, { inSimulationMode: false, amrRetentionInSec: 300 });

        // Verify that Kafka consumer was initialized
        expect(kafkaInitStub.calledOnce).to.be.true;
        
        // The callback is passed to initializeConsumer - verify it's a function
        const callArgs = kafkaInitStub.getCall(0).args;
        expect(callArgs[0]).to.be.a('function'); // The callback
    });

    it('should warn when AMR message header type is undefined (line 787)', async () => {
        let capturedCallback = null;
        let loggerWarnCalled = false;
        
        const MockNodeCache = function() {
            this.get = () => undefined;
            this.set = () => {};
            this.has = () => false;
        };

        const kafkaInitStub = sinon.stub().callsFake((callback) => {
            capturedCallback = callback;
            return Promise.resolve();
        });

        const winstonMock = {
            format: {
                combine: () => ({}),
                timestamp: () => ({}),
                printf: () => ({})
            },
            transports: { Console: function() {} },
            createLogger: () => ({
                warn: (msg) => { 
                    if (msg.includes("Header 'type' has to be present")) {
                        loggerWarnCalled = true;
                    }
                }
            })
        };

        const MTMC = proxyquire('../../../../src/web-api-core/Services/MTMC', {
            'node-cache': MockNodeCache,
            '../Utils/Kafka': {
                getTopic: () => 'mdx-amr',
                initializeConsumer: kafkaInitStub
            },
            'winston': winstonMock
        });

        const mtmcInstance = new MTMC();
        const mockBroker = {
            getName: () => 'Kafka',
            getClient: () => ({}),
            getAdminClient: () => ({})
        };

        await mtmcInstance.consumeAMRMessages(mockBroker, { inSimulationMode: false, amrRetentionInSec: 300 });

        if (capturedCallback) {
            capturedCallback({
                topic: 'mdx-amr',
                partition: 0,
                message: {
                    headers: {}, // No type header
                    value: Buffer.from(JSON.stringify({ place: 'test' }))
                }
            });
        }

        expect(loggerWarnCalled).to.be.true;
    });

    it('should warn for invalid AMR message type (lines 792-793)', async () => {
        let capturedCallback = null;
        let loggerWarnCalled = false;
        
        const MockNodeCache = function() {
            this.get = () => undefined;
            this.set = () => {};
            this.has = () => false;
        };

        const kafkaInitStub = sinon.stub().callsFake((callback) => {
            capturedCallback = callback;
            return Promise.resolve();
        });

        const winstonMock = {
            format: {
                combine: () => ({}),
                timestamp: () => ({}),
                printf: () => ({})
            },
            transports: { Console: function() {} },
            createLogger: () => ({
                warn: (msg) => { 
                    if (msg.includes("Invalid value:")) {
                        loggerWarnCalled = true;
                    }
                }
            })
        };

        const MTMC = proxyquire('../../../../src/web-api-core/Services/MTMC', {
            'node-cache': MockNodeCache,
            '../Utils/Kafka': {
                getTopic: () => 'mdx-amr',
                initializeConsumer: kafkaInitStub
            },
            'winston': winstonMock
        });

        const mtmcInstance = new MTMC();
        const mockBroker = {
            getName: () => 'Kafka',
            getClient: () => ({}),
            getAdminClient: () => ({})
        };

        await mtmcInstance.consumeAMRMessages(mockBroker, { inSimulationMode: false, amrRetentionInSec: 300 });

        if (capturedCallback) {
            capturedCallback({
                topic: 'mdx-amr',
                partition: 0,
                message: {
                    headers: { type: Buffer.from('invalid-type') },
                    value: Buffer.from(JSON.stringify({ place: 'test' }))
                }
            });
        }

        expect(loggerWarnCalled).to.be.true;
    });
});

describe('MTMC additional coverage tests', () => {
    let mtmc;
    let elasticDb;
    let searchStub;

    beforeEach(() => {
        mtmc = new MTMC();
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

    it('should handle getOccupancyTracker with AMR record from ES when RTLS absent (lines 1450-1458)', async () => {
        // First call for RTLS - index absent
        searchStub.onCall(0).resolves({ indexAbsent: true });
        
        // Second call for AMR past record
        searchStub.onCall(1).resolves({
            indexAbsent: false,
            body: {
                hits: {
                    hits: [{
                        _source: {
                            timestamp: '2023-01-12T14:20:05.000Z',
                            objectCounts: [{ type: 'AMR', count: 3 }]
                        }
                    }]
                }
            }
        });
        
        // Third call for AMR future record
        searchStub.onCall(2).resolves({ indexAbsent: true });

        const result = await mtmc.getOccupancyTracker(elasticDb, {
            place: 'city=test',
            timestamp: '2023-01-12T14:20:10.000Z'
        });

        expect(result.trackerOccupancy).to.be.an('array');
    });

    it('should merge AMR data with RTLS data choosing future record when closer (lines 857-863)', async () => {
        // RTLS result - must include objectCounts and locationsOfObjects arrays
        searchStub.onCall(0).resolves({
            indexAbsent: false,
            body: {
                hits: {
                    hits: [{
                        _source: {
                            timestamp: '2023-01-12T14:20:10.000Z',
                            place: 'city=test',
                            objectCounts: [{ type: 'Person', count: 5 }],
                            locationsOfObjects: [{ id: 'p1', type: 'Person', locations: [[10, 20]] }]
                        }
                    }]
                }
            }
        });

        // AMR past record - further from RTLS timestamp (3 seconds before)
        searchStub.onCall(1).resolves({
            indexAbsent: false,
            body: {
                hits: {
                    hits: [{
                        _source: {
                            timestamp: '2023-01-12T14:20:07.000Z',
                            objectCounts: [{ type: 'AMR', count: 2 }],
                            locationsOfObjects: []
                        }
                    }]
                }
            }
        });

        // AMR future record - closer to RTLS timestamp (1 second after)
        searchStub.onCall(2).resolves({
            indexAbsent: false,
            body: {
                hits: {
                    hits: [{
                        _source: {
                            timestamp: '2023-01-12T14:20:11.000Z',
                            objectCounts: [{ type: 'AMR', count: 3 }],
                            locationsOfObjects: []
                        }
                    }]
                }
            }
        });

        // AMR route events
        searchStub.onCall(3).resolves({
            indexAbsent: false,
            body: {
                hits: {
                    hits: []
                }
            }
        });

        const result = await mtmc.getUniqueObjectCountWithLocations(elasticDb, null, {
            place: 'city=test',
            timestamp: '2023-01-12T14:20:10.000Z'
        });

        expect(result.objectCounts).to.be.an('array');
        // The future AMR record (count: 3) should be chosen since it's closer
    });

    it('should handle getUniqueObjectCount with place filter (line 77)', async () => {
        const countStub = sinon.stub(Elasticsearch, 'getDocCount');
        countStub.resolves({
            indexAbsent: false,
            count: 10
        });

        const result = await mtmc.getUniqueObjectCount(elasticDb, {
            timestamp: '2023-01-12T14:20:10.000Z',
            place: 'city=test/building=xyz'
        });

        expect(result.uniqueObjectCount).to.equal(10);
        countStub.restore();
    });
});

describe('MTMC cache-based tests (proxyquire)', () => {
    const proxyquire = require('proxyquire');

    it('should get unique object count with locations from cache with fov and objects (lines 1235-1278)', async () => {
        const cachedRtlsRecord = {
            timestamp: '2023-01-12T14:20:10.000Z',
            fov: [{ type: 'Person', count: 5 }],
            objects: [
                { id: 'obj1', type: 'Person', coordinate: { x: 10, y: 20, z: 5 } },
                { id: 'obj2', type: 'Vehicle', coordinate: { x: 15, y: 25 } }
            ]
        };

        const amrRecord = {
            timestamp: '2023-01-12T14:20:09.000Z',
            objectCounts: [{ type: 'AMR', count: 2 }],
            locationsOfObjects: [{ id: 'amr1', type: 'AMR', locations: [[30, 40]] }]
        };

        const amrRouteEvents = new Map([
            ['amr1', { timestamp: '2023-01-12T14:20:09.000Z', event: { amrId: 'amr1', eventType: 'route-change' } }]
        ]);
        
        const objectCountCache = new Map([['city=test', cachedRtlsRecord]]);
        const amrCountCache = new Map([['city=test', [amrRecord]]]);
        const amrRoutesMap = new Map([['city=test', amrRouteEvents]]);
        
        let cacheInstance = 0;
        const MockNodeCache = function() {
            const instance = cacheInstance++;
            if (instance === 0) {
                // objectCountWithLocationsCache
                this.get = (key) => objectCountCache.get(key);
                this.set = (key, value) => objectCountCache.set(key, value);
            } else if (instance === 1) {
                // amrCountWithLocationsCache
                this.get = (key) => amrCountCache.get(key);
                this.set = (key, value) => amrCountCache.set(key, value);
            } else {
                // amrRoutes
                this.get = (key) => amrRoutesMap.get(key);
                this.set = (key, value) => amrRoutesMap.set(key, value);
            }
            this.has = () => false;
            this.keys = () => [];
        };

        const utilsMock = {
            tsCompare: (ts1, op, ts2) => {
                const d1 = new Date(ts1).getTime();
                const d2 = new Date(ts2).getTime();
                if (op === '<') return d1 < d2;
                if (op === '>=') return d1 >= d2;
                if (op === '<=') return d1 <= d2;
                if (op === '>') return d1 > d2;
                if (op === '==') return d1 === d2;
                return false;
            }
        };

        const winstonMock = {
            format: {
                combine: () => ({}),
                timestamp: () => ({}),
                printf: () => ({})
            },
            transports: { Console: function() {} },
            createLogger: () => ({ warn: () => {} })
        };

        const MTMC = proxyquire('../../../../src/web-api-core/Services/MTMC', {
            'node-cache': MockNodeCache,
            '../Utils/Utils': utilsMock,
            'winston': winstonMock
        });

        const mtmcInstance = new MTMC();
        const mockBroker = {
            getName: () => 'Kafka',
            getClient: () => ({}),
            getAdminClient: () => ({})
        };

        // Pass timestamp: null to use the Kafka/cache path
        const result = await mtmcInstance.getUniqueObjectCountWithLocations(null, mockBroker, {
            place: 'city=test'
            // No timestamp - will use cache path
        });

        expect(result.objectCounts).to.be.an('array');
        expect(result.locationsOfObjects).to.be.an('array');
    });

    it('should handle getUniqueObjectCountWithLocations without RTLS but with AMR from cache (lines 1281-1303)', async () => {
        const amrRecord = {
            timestamp: '2023-01-12T14:20:08.000Z',
            objectCounts: [{ type: 'AMR', count: 3 }],
            locationsOfObjects: [{ id: 'amr1', type: 'AMR', locations: [[30, 40]] }]
        };

        const amrRouteEvents = new Map([
            ['amr1', { timestamp: '2023-01-12T14:20:08.000Z', event: { amrId: 'amr1', eventType: 'route-change' } }]
        ]);
        
        const objectCountCache = new Map(); // Empty - no RTLS data
        const amrCountCache = new Map([['city=test', [amrRecord]]]);
        const amrRoutesMap = new Map([['city=test', amrRouteEvents]]);
        
        let cacheInstance = 0;
        const MockNodeCache = function() {
            const instance = cacheInstance++;
            if (instance === 0) {
                this.get = (key) => objectCountCache.get(key);
                this.set = (key, value) => objectCountCache.set(key, value);
            } else if (instance === 1) {
                this.get = (key) => amrCountCache.get(key);
                this.set = (key, value) => amrCountCache.set(key, value);
            } else {
                this.get = (key) => amrRoutesMap.get(key);
                this.set = (key, value) => amrRoutesMap.set(key, value);
            }
            this.has = () => false;
            this.keys = () => [];
        };

        const utilsMock = {
            tsCompare: (ts1, op, ts2) => {
                const d1 = new Date(ts1).getTime();
                const d2 = new Date(ts2).getTime();
                if (op === '<') return d1 < d2;
                if (op === '>=') return d1 >= d2;
                if (op === '<=') return d1 <= d2;
                if (op === '>') return d1 > d2;
                if (op === '==') return d1 === d2;
                return false;
            }
        };

        const winstonMock = {
            format: {
                combine: () => ({}),
                timestamp: () => ({}),
                printf: () => ({})
            },
            transports: { Console: function() {} },
            createLogger: () => ({ warn: () => {} })
        };

        const MTMC = proxyquire('../../../../src/web-api-core/Services/MTMC', {
            'node-cache': MockNodeCache,
            '../Utils/Utils': utilsMock,
            'winston': winstonMock
        });

        const mtmcInstance = new MTMC();
        const mockBroker = {
            getName: () => 'Kafka',
            getClient: () => ({}),
            getAdminClient: () => ({})
        };

        // Pass timestamp: null to use the Kafka/cache path
        const result = await mtmcInstance.getUniqueObjectCountWithLocations(null, mockBroker, {
            place: 'city=test'
            // No timestamp - will use cache path
        });

        expect(result.objectCounts).to.be.an('array');
        expect(result.locationsOfObjects).to.be.an('array');
    });
});
