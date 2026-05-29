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
const Frames = require('../../../../src/web-api-core/Services/Frames');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const Calibration = require('../../../../src/web-api-core/Services/Calibration');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');

describe('Frames', () => {
    let frames;
    let elasticDb;
    let searchStub;

    beforeEach(() => {
        frames = new Frames();
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

    describe('getMaxConfidenceDetectionOfObjects', () => {
        it('should return max confidence objects', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            // First call - aggregation
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objects: {
                            objectsFilter: {
                                objectIds: {
                                    buckets: [
                                        {
                                            maxConfidenceDetection: {
                                                hits: {
                                                    hits: [{
                                                        _id: 'doc1',
                                                        _source: { id: 'obj1', bbox: {}, confidence: 0.95 }
                                                    }]
                                                }
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            });

            // Second call - get frames
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _id: 'doc1',
                            _source: { id: 'frame1', sensorId: 'sensor123', timestamp: '2023-01-12T12:00:00.000Z' }
                        }]
                    }
                }
            });

            const result = await frames.getMaxConfidenceDetectionOfObjects(elasticDb, input);

            expect(result).to.have.property('objects');
            expect(result.objects).to.be.an('array').with.lengthOf(1);
            expect(result.objects[0]).to.deep.include({
                frameId: 'frame1',
                sensorId: 'sensor123',
                timestamp: '2023-01-12T12:00:00.000Z',
                objectId: 'obj1',
                confidence: 0.95
            });
            expect(result.objects[0]).to.have.keys(['frameId', 'sensorId', 'timestamp', 'objectId', 'confidence', 'bbox', 'embedding']);
        });

        it('should return empty array when index is absent', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await frames.getMaxConfidenceDetectionOfObjects(elasticDb, input);

            expect(result.objects).to.be.an('array').that.is.empty;
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await frames.getMaxConfidenceDetectionOfObjects(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.equal("Invalid input. Error 1: Input should have required properties 'sensorId', 'fromTimestamp' and 'toTimestamp'.");
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T14:20:10.000Z',
                toTimestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await frames.getMaxConfidenceDetectionOfObjects(elasticDb, input);
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
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await frames.getMaxConfidenceDetectionOfObjects(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });

        it('should filter by objectId when provided', async () => {
            const input = {
                sensorId: 'sensor123',
                objectId: 'obj1',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objects: {
                            objectsFilter: {
                                objectIds: {
                                    buckets: [{
                                        maxConfidenceDetection: {
                                            hits: {
                                                hits: [{
                                                    _id: 'doc1',
                                                    _source: { id: 'obj1', bbox: {}, confidence: 0.95, embedding: { vector: [0.1] } }
                                                }]
                                            }
                                        }
                                    }]
                                }
                            }
                        }
                    }
                }
            });

            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _id: 'doc1',
                            _source: { id: 'frame1', sensorId: 'sensor123', timestamp: '2023-01-12T12:00:00.000Z' }
                        }]
                    }
                }
            });

            const result = await frames.getMaxConfidenceDetectionOfObjects(elasticDb, input);

            expect(result.objects).to.be.an('array').with.lengthOf(1);
            expect(result.objects[0]).to.deep.include({
                frameId: 'frame1',
                sensorId: 'sensor123',
                timestamp: '2023-01-12T12:00:00.000Z',
                objectId: 'obj1',
                confidence: 0.95
            });
            expect(result.objects[0].embedding).to.deep.equal([0.1]);
        });

        it('should handle multiple objects in same doc', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objects: {
                            objectsFilter: {
                                objectIds: {
                                    buckets: [
                                        {
                                            maxConfidenceDetection: {
                                                hits: {
                                                    hits: [{
                                                        _id: 'doc1',
                                                        _source: { id: 'obj1', bbox: {}, confidence: 0.95 }
                                                    }]
                                                }
                                            }
                                        },
                                        {
                                            maxConfidenceDetection: {
                                                hits: {
                                                    hits: [{
                                                        _id: 'doc1',
                                                        _source: { id: 'obj2', bbox: {}, confidence: 0.90 }
                                                    }]
                                                }
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            });

            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _id: 'doc1',
                            _source: { id: 'frame1', sensorId: 'sensor123', timestamp: '2023-01-12T12:00:00.000Z' }
                        }]
                    }
                }
            });

            const result = await frames.getMaxConfidenceDetectionOfObjects(elasticDb, input);

            expect(result.objects).to.have.length(2);
            expect(result.objects).to.deep.include.members([
                { frameId: 'frame1', sensorId: 'sensor123', timestamp: '2023-01-12T12:00:00.000Z', objectId: 'obj1', bbox: {}, confidence: 0.95, embedding: null },
                { frameId: 'frame1', sensorId: 'sensor123', timestamp: '2023-01-12T12:00:00.000Z', objectId: 'obj2', bbox: {}, confidence: 0.90, embedding: null }
            ]);
        });
    });

    describe('getLatestTimestampOfFrameWithObject', () => {
        it('should return timestamp of frame', async () => {
            const input = {
                sensorId: 'sensor123',
                objectId: 'obj1',
                frameId: 'frame1'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { timestamp: '2023-01-12T12:00:00.000Z' } }]
                    }
                }
            });

            const result = await frames.getLatestTimestampOfFrameWithObject(elasticDb, input);

            expect(result).to.equal('2023-01-12T12:00:00.000Z');
        });

        it('should return null when no frame found', async () => {
            const input = {
                sensorId: 'sensor123',
                objectId: 'obj1',
                frameId: 'frame1'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await frames.getLatestTimestampOfFrameWithObject(elasticDb, input);

            expect(result).to.be.null;
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                sensorId: 'sensor123',
                objectId: 'obj1'
            };

            try {
                await frames.getLatestTimestampOfFrameWithObject(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.equal("Invalid input. Error 1: Input should have required properties 'sensorId', 'objectId' and 'frameId'.");
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
                objectId: 'obj1',
                frameId: 'frame1'
            };

            try {
                await frames.getLatestTimestampOfFrameWithObject(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('getEmbedding', () => {
        it('should return embedding for object', async () => {
            const input = {
                sensorId: 'sensor123',
                objectId: 'obj1',
                timestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        objects: {
                            objectsFilter: {
                                objectDoc: {
                                    hits: {
                                        hits: [{ _source: { embedding: { vector: [0.1, 0.2, 0.3] } } }]
                                    }
                                }
                            }
                        }
                    }
                }
            });

            const result = await frames.getEmbedding(elasticDb, input);

            // getEmbedding returns a promise that resolves to an array
            const embedding = await result;
            expect(embedding).to.be.an('array').with.lengthOf(3);
            expect(embedding).to.deep.equal([0.1, 0.2, 0.3]);
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                sensorId: 'sensor123',
                objectId: 'obj1'
            };

            try {
                await frames.getEmbedding(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.equal("Invalid input. Error 1: Input should have required properties 'sensorId', 'objectId' and 'timestamp'.");
            }
        });

        it('should throw InvalidInputError for invalid timestamp', async () => {
            const input = {
                sensorId: 'sensor123',
                objectId: 'obj1',
                timestamp: 'invalid-timestamp'
            };

            try {
                await frames.getEmbedding(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.equal('Invalid timestamp.');
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
                objectId: 'obj1',
                timestamp: '2023-01-12T12:00:00.000Z'
            };

            try {
                await frames.getEmbedding(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('getProximityClusters', () => {
        it('should return proximity clusters', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: {
                                id: 'frame1',
                                timestamp: '2023-01-12T12:00:00.000Z',
                                socialDistancing: { clusters: [{ id: 'cluster1' }] }
                            }
                        }]
                    }
                }
            });

            const result = await frames.getProximityClusters(elasticDb, input);

            expect(result).to.have.property('id');
            expect(result).to.have.property('timestamp');
            expect(result).to.have.property('clusters');
            expect(result.id).to.equal('frame1');
            expect(result.timestamp).to.equal('2023-01-12T12:00:00.000Z');
            expect(result.clusters).to.be.an('array').with.lengthOf(1);
            expect(result.clusters[0]).to.deep.equal({ id: 'cluster1' });
        });

        it('should return empty clusters when index is absent', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await frames.getProximityClusters(elasticDb, input);

            expect(result.id).to.be.null;
            expect(result.clusters).to.be.an('array').that.is.empty;
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await frames.getProximityClusters(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.equal("Invalid input. Error 1: Input should have required properties 'sensorId', 'fromTimestamp' and 'toTimestamp'.");
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T14:20:10.000Z',
                toTimestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await frames.getProximityClusters(elasticDb, input);
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
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await frames.getProximityClusters(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('getPts', () => {
        let calibrationStub;

        beforeEach(() => {
            calibrationStub = sinon.stub(Calibration.prototype, 'getCalibration');
        });

        it('should return pts for frameId', async () => {
            const input = {
                sensorId: 'sensor123',
                frameId: 3000
            };

            calibrationStub.resolves({
                calibration: {
                    sensors: [{
                        attributes: [
                            { name: 'fps', value: '30' },
                            { name: 'source', value: 'nvstreamer' }
                        ]
                    }]
                }
            });

            const result = await frames.getPts(elasticDb, input);

            expect(result).to.have.property('pts');
            expect(result.pts).to.equal(100000); // 3000/30*1000
        });

        it('should return pts for timestamp', async () => {
            const input = {
                sensorId: 'sensor123',
                timestamp: '2023-01-12T12:00:00.000Z'
            };

            calibrationStub.resolves({
                calibration: {
                    sensors: [{
                        attributes: [
                            { name: 'fps', value: '30' },
                            { name: 'source', value: 'nvstreamer' }
                        ]
                    }]
                }
            });

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: { id: '3000', timestamp: '2023-01-12T12:00:00.000Z' }
                        }]
                    }
                }
            });

            const result = await frames.getPts(elasticDb, input);

            expect(result).to.have.property('pts');
            expect(result.pts).to.equal(100000); // 3000/30*1000
        });

        it('should throw BadRequestError when neither timestamp nor frameId provided', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await frames.getPts(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.equal("Invalid input. Error 1: Input should have either 'timestamp' or 'frameId'.");
            }
        });

        it('should throw InvalidInputError for invalid timestamp', async () => {
            const input = {
                sensorId: 'sensor123',
                timestamp: 'invalid-timestamp'
            };

            try {
                await frames.getPts(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.equal('Invalid timestamp.');
            }
        });

        it('should throw InvalidInputError when source is not nvstreamer', async () => {
            const input = {
                sensorId: 'sensor123',
                frameId: 3000
            };

            calibrationStub.resolves({
                calibration: {
                    sensors: [{
                        attributes: [
                            { name: 'fps', value: '30' },
                            { name: 'source', value: 'rtsp' }
                        ]
                    }]
                }
            });

            try {
                await frames.getPts(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('nvstreamer');
            }
        });

        it('should throw InvalidInputError when source is not defined', async () => {
            const input = {
                sensorId: 'sensor123',
                frameId: 3000
            };

            calibrationStub.resolves({
                calibration: {
                    sensors: [{
                        attributes: [
                            { name: 'fps', value: '30' }
                        ]
                    }]
                }
            });

            try {
                await frames.getPts(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('source');
            }
        });

        it('should throw InvalidInputError when fps is not defined', async () => {
            const input = {
                sensorId: 'sensor123',
                frameId: 3000
            };

            calibrationStub.resolves({
                calibration: {
                    sensors: [{
                        attributes: [
                            { name: 'source', value: 'nvstreamer' }
                        ]
                    }]
                }
            });

            try {
                await frames.getPts(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('fps');
            }
        });

        it('should return null pts when no frame found for timestamp', async () => {
            const input = {
                sensorId: 'sensor123',
                timestamp: '2023-01-12T12:00:00.000Z'
            };

            calibrationStub.resolves({
                calibration: {
                    sensors: [{
                        attributes: [
                            { name: 'fps', value: '30' },
                            { name: 'source', value: 'nvstreamer' }
                        ]
                    }]
                }
            });

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await frames.getPts(elasticDb, input);

            expect(result.pts).to.be.null;
        });

        it('should calculate pts when timestamp is greater than frame timestamp', async () => {
            const input = {
                sensorId: 'sensor123',
                timestamp: '2023-01-12T12:00:01.000Z'
            };

            calibrationStub.resolves({
                calibration: {
                    sensors: [{
                        attributes: [
                            { name: 'fps', value: '30' },
                            { name: 'source', value: 'nvstreamer' }
                        ]
                    }]
                }
            });

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: { id: '3000', timestamp: '2023-01-12T12:00:00.000Z' }
                        }]
                    }
                }
            });

            const result = await frames.getPts(elasticDb, input);

            expect(result).to.have.property('pts');
            expect(result.pts).to.be.a('number');
            // dataPts = 3000/30*1000 = 100000, input is 1s after frame, so pts = 100000 + 1000 = 101000
            expect(result.pts).to.equal(101000);
        });

        it('should calculate pts when timestamp is less than frame timestamp', async () => {
            const input = {
                sensorId: 'sensor123',
                timestamp: '2023-01-12T11:59:59.000Z'
            };

            calibrationStub.resolves({
                calibration: {
                    sensors: [{
                        attributes: [
                            { name: 'fps', value: '30' },
                            { name: 'source', value: 'nvstreamer' }
                        ]
                    }]
                }
            });

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: { id: '3000', timestamp: '2023-01-12T12:00:00.000Z' }
                        }]
                    }
                }
            });

            const result = await frames.getPts(elasticDb, input);

            expect(result).to.have.property('pts');
            expect(result.pts).to.be.a('number');
            // dataPts = 3000/30*1000 = 100000, input is 1s before frame, so pts = 100000 - 1000 = 99000
            expect(result.pts).to.equal(99000);
        });
    });

    describe('getFrames', () => {
        it('should return frames with time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'frame1', sensorId: 'sensor123' } }]
                    }
                }
            });

            const result = await frames.getFrames(elasticDb, input);

            expect(result).to.have.property('frames');
            expect(result.frames).to.be.an('array').with.lengthOf(1);
            expect(result.frames[0]).to.deep.equal({ id: 'frame1', sensorId: 'sensor123' });
        });

        it('should return frames with timestamp', async () => {
            const input = {
                sensorId: 'sensor123',
                timestamp: '2023-01-12T12:00:00.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'frame1' } }]
                    }
                }
            });

            const result = await frames.getFrames(elasticDb, input);

            expect(result).to.have.property('frames');
            expect(result.frames).to.be.an('array').with.lengthOf(1);
            expect(result.frames[0]).to.deep.equal({ id: 'frame1' });
        });

        it('should return frames with frameId', async () => {
            const input = {
                sensorId: 'sensor123',
                frameId: 'frame1'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'frame1' } }]
                    }
                }
            });

            const result = await frames.getFrames(elasticDb, input);

            expect(result).to.have.property('frames');
            expect(result.frames).to.be.an('array').with.lengthOf(1);
            expect(result.frames[0]).to.deep.equal({ id: 'frame1' });
        });

        it('should return empty array when index is absent', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await frames.getFrames(elasticDb, input);

            expect(result.frames).to.be.an('array').that.is.empty;
        });

        it('should throw BadRequestError for missing time params', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await frames.getFrames(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.equal("Invalid input. Error 1: Input can either have 'frameId', 'timestamp' or a time range i.e. 'fromTimestamp' and 'toTimestamp'.");
            }
        });

        it('should throw InvalidInputError for invalid timestamp', async () => {
            const input = {
                sensorId: 'sensor123',
                timestamp: 'invalid-timestamp'
            };

            try {
                await frames.getFrames(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.equal('Invalid timestamp.');
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T14:20:10.000Z',
                toTimestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await frames.getFrames(elasticDb, input);
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
                sensorId: 'sensor123',
                frameId: 'frame1'
            };

            try {
                await frames.getFrames(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('getEnhancedFrames', () => {
        it('should return enhanced frames', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'frame1' } }]
                    }
                }
            });

            const result = await frames.getEnhancedFrames(elasticDb, input);

            expect(result).to.have.property('enhancedFrames');
            expect(result.enhancedFrames).to.be.an('array').with.lengthOf(1);
            expect(result.enhancedFrames[0]).to.deep.equal({ id: 'frame1' });
        });

        it('should throw BadRequestError for missing time params', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await frames.getEnhancedFrames(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.equal("Invalid input. Error 1: Input can either have 'frameId', 'timestamp' or a time range i.e. 'fromTimestamp' and 'toTimestamp'.");
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
                frameId: 'frame1'
            };

            try {
                await frames.getEnhancedFrames(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('getBevFrames', () => {
        it('should return BEV frames', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'frame1' } }]
                    }
                }
            });

            const result = await frames.getBevFrames(elasticDb, input);

            expect(result).to.have.property('bevFrames');
            expect(result.bevFrames).to.be.an('array').with.lengthOf(1);
            expect(result.bevFrames[0]).to.deep.equal({ id: 'frame1' });
        });

        it('should throw BadRequestError for missing time params', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await frames.getBevFrames(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.equal("Invalid input. Error 1: Input can either have 'frameId', 'timestamp' or a time range i.e. 'fromTimestamp' and 'toTimestamp'.");
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
                frameId: 'frame1'
            };

            try {
                await frames.getBevFrames(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('getAlerts', () => {
        it('should return alerts', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await frames.getAlerts(elasticDb, input);

            expect(result).to.have.property('alerts');
            expect(result.alerts).to.be.an('array').that.is.empty;
        });

        it('should return alerts with sensorId filter', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await frames.getAlerts(elasticDb, input);

            expect(result).to.have.property('alerts');
            expect(result.alerts).to.be.an('array').that.is.empty;
        });

        it('should return alerts with proximity type filter', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                type: 'proximity'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await frames.getAlerts(elasticDb, input);

            expect(result).to.have.property('alerts');
            expect(result.alerts).to.be.an('array').that.is.empty;
        });

        it('should return alerts with restricted-area type filter', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                type: 'restricted-area'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await frames.getAlerts(elasticDb, input);

            expect(result).to.have.property('alerts');
            expect(result.alerts).to.be.an('array').that.is.empty;
        });

        it('should return alerts with confined-area type filter', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                type: 'confined-area'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await frames.getAlerts(elasticDb, input);

            expect(result).to.have.property('alerts');
            expect(result.alerts).to.be.an('array').that.is.empty;
        });

        it('should return empty alerts when index is absent', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await frames.getAlerts(elasticDb, input);

            expect(result.alerts).to.be.an('array').that.is.empty;
        });

        it('should process confined area alerts', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: {
                                sensorId: 'sensor123',
                                timestamp: '2023-01-12T12:00:00.000Z',
                                info: {
                                    confinedAreaViolation: 'true',
                                    confinedAreaViolationObjects: 'obj1,obj2|obj3'
                                },
                                socialDistancing: { info: { safetyViolation: 'false' } },
                                rois: [],
                                objects: [
                                    { id: 'obj1', type: 'Person', bbox: {} },
                                    { id: 'obj2', type: 'Person', bbox: {} },
                                    { id: 'obj3', type: 'Person', bbox3d: { coordinates: {} } }
                                ]
                            }
                        }]
                    }
                }
            });

            const result = await frames.getAlerts(elasticDb, input);

            expect(result.alerts).to.have.length(1);
            expect(result.alerts[0]).to.deep.include({
                sensorId: 'sensor123',
                timestamp: '2023-01-12T12:00:00.000Z',
                confinedArea: { objectIds: ['obj1', 'obj2', 'obj3'] }
            });
            expect(result.alerts[0].objects).to.have.length(3);
        });

        it('should process proximity alerts', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: {
                                sensorId: 'sensor123',
                                timestamp: '2023-01-12T12:00:00.000Z',
                                info: { confinedAreaViolation: 'false' },
                                socialDistancing: {
                                    info: {
                                        proximityViolation: 'true',
                                        proximityViolationObjects: 'obj1,obj2'
                                    }
                                },
                                rois: [],
                                objects: [
                                    { id: 'obj1', type: 'Person', bbox: {} },
                                    { id: 'obj2', type: 'Person', bbox: {} }
                                ]
                            }
                        }]
                    }
                }
            });

            const result = await frames.getAlerts(elasticDb, input);

            expect(result.alerts).to.have.length(1);
            expect(result.alerts[0]).to.deep.include({
                sensorId: 'sensor123',
                timestamp: '2023-01-12T12:00:00.000Z',
                proximity: {
                    info: {
                        proximityViolation: 'true',
                        proximityViolationObjects: 'obj1,obj2'
                    }
                }
            });
            expect(result.alerts[0].objects).to.have.length(2);
        });

        it('should process restricted area alerts', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: {
                                sensorId: 'sensor123',
                                timestamp: '2023-01-12T12:00:00.000Z',
                                info: { confinedAreaViolation: 'false' },
                                socialDistancing: { info: { safetyViolation: 'false' } },
                                rois: [
                                    {
                                        id: 'roi1',
                                        info: { restrictedAreaViolation: 'true' },
                                        objectIds: ['obj1', 'obj2']
                                    },
                                    {
                                        id: 'roi1',
                                        info: { restrictedAreaViolation: 'true' },
                                        objectIds: ['obj3']
                                    }
                                ],
                                objects: [
                                    { id: 'obj1', type: 'Person', bbox: {} },
                                    { id: 'obj2', type: 'Person', bbox: {} },
                                    { id: 'obj3', type: 'Person', bbox: {} }
                                ]
                            }
                        }]
                    }
                }
            });

            const result = await frames.getAlerts(elasticDb, input);

            expect(result.alerts).to.have.length(1);
            expect(result.alerts[0]).to.deep.include({
                sensorId: 'sensor123',
                timestamp: '2023-01-12T12:00:00.000Z'
            });
            expect(result.alerts[0].restrictedArea).to.deep.equal([
                { roiId: 'roi1', objectIds: ['obj1', 'obj2', 'obj3'] }
            ]);
            expect(result.alerts[0].objects).to.have.length(3);
        });

        it('should throw BadRequestError for missing timestamps', async () => {
            const input = {};

            try {
                await frames.getAlerts(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.equal("Invalid input. Error 1: Input should have required properties 'fromTimestamp' and 'toTimestamp'.");
            }
        });

        it('should throw InvalidInputError for invalid time range', async () => {
            const input = {
                fromTimestamp: '2023-01-12T14:20:10.000Z',
                toTimestamp: '2023-01-12T11:20:10.000Z'
            };

            try {
                await frames.getAlerts(elasticDb, input);
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
                await frames.getAlerts(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });

        it('should throw BadRequestError for invalid type enum value', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                type: 'invalid-type'
            };

            try {
                await frames.getAlerts(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('type');
            }
        });

        it('should throw BadRequestError for additional properties', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                invalidProperty: 'value'
            };

            try {
                await frames.getAlerts(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('Invalid additional Input');
            }
        });

        it('should throw BadRequestError when maxResultSize exceeds maximum', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                maxResultSize: 10000
            };

            try {
                await frames.getAlerts(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('maxResultSize');
            }
        });

        it('should throw BadRequestError when maxResultSize is less than minimum', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                maxResultSize: 0
            };

            try {
                await frames.getAlerts(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('maxResultSize');
            }
        });

        it('should use default maxResultSize when not provided', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await frames.getAlerts(elasticDb, input);

            expect(result).to.have.property('alerts');
            expect(result.alerts).to.be.an('array').that.is.empty;
        });

        it('should process alert with combined alert types', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: {
                                sensorId: 'sensor123',
                                timestamp: '2023-01-12T12:00:00.000Z',
                                info: {
                                    confinedAreaViolation: 'true',
                                    confinedAreaViolationObjects: 'obj1'
                                },
                                socialDistancing: {
                                    info: {
                                        proximityViolation: 'true',
                                        proximityViolationObjects: 'obj2'
                                    }
                                },
                                rois: [
                                    {
                                        id: 'roi1',
                                        info: { restrictedAreaViolation: 'true' },
                                        objectIds: ['obj3']
                                    }
                                ],
                                objects: [
                                    { id: 'obj1', type: 'Person', bbox: {} },
                                    { id: 'obj2', type: 'Person', bbox: {} },
                                    { id: 'obj3', type: 'Person', bbox: {} }
                                ]
                            }
                        }]
                    }
                }
            });

            const result = await frames.getAlerts(elasticDb, input);

            expect(result.alerts).to.have.length(1);
            expect(result.alerts[0]).to.include.all.keys('confinedArea', 'proximity', 'restrictedArea');
            expect(result.alerts[0].objects).to.have.length(3);
        });

        it('should include bbox3d coordinates when present in object', async () => {
            const input = {
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: {
                                sensorId: 'sensor123',
                                timestamp: '2023-01-12T12:00:00.000Z',
                                info: {
                                    confinedAreaViolation: 'true',
                                    confinedAreaViolationObjects: 'obj1'
                                },
                                socialDistancing: { info: { safetyViolation: 'false' } },
                                rois: [],
                                objects: [
                                    { 
                                        id: 'obj1', 
                                        type: 'Person', 
                                        bbox: { x: 10, y: 20, w: 100, h: 200 },
                                        bbox3d: { 
                                            coordinates: { x: 1.5, y: 2.5, z: 0 }
                                        }
                                    }
                                ]
                            }
                        }]
                    }
                }
            });

            const result = await frames.getAlerts(elasticDb, input);

            expect(result.alerts).to.have.length(1);
            expect(result.alerts[0].objects).to.have.length(1);
            expect(result.alerts[0].objects[0]).to.deep.include({
                id: 'obj1',
                type: 'Person',
                bbox: { x: 10, y: 20, w: 100, h: 200 },
                bbox3d: { coordinates: { x: 1.5, y: 2.5, z: 0 } }
            });
        });
    });
});
