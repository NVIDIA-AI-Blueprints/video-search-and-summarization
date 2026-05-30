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
const path = require('path');
const RoadNetwork = require('../../../../src/web-api-core/Services/RoadNetwork');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const Utils = require('../../../../src/web-api-core/Utils/Utils');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');
const ResourceNotFoundError = require('../../../../src/web-api-core/Errors/ResourceNotFoundError');

describe('RoadNetwork', () => {
    let roadNetwork;
    let elasticDb;
    let searchStub;
    let mockEsClient;

    beforeEach(() => {
        roadNetwork = new RoadNetwork();
        mockEsClient = {
            index: sinon.stub().resolves({ result: 'created' }),
            indices: {
                existsIndexTemplate: sinon.stub().resolves(true),
                putIndexTemplate: sinon.stub().resolves({}),
                refresh: sinon.stub().resolves({})
            },
            ingest: {
                getPipeline: sinon.stub().resolves({}),
                putPipeline: sinon.stub().resolves({})
            }
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

    describe('upload', () => {
        it('should upload road network file successfully', async () => {
            const fixturePath = path.resolve(__dirname, '../../fixtures/road-network.json');
            // Clear require cache to ensure fresh load
            delete require.cache[require.resolve(fixturePath)];
            const deleteFilesStub = sinon.stub(Utils, 'deleteFiles').resolves();

            const result = await roadNetwork.upload(elasticDb, {
                fileDetails: { configFiles: [{ path: fixturePath }] },
                fieldName: 'configFiles'
            });

            expect(result).to.deep.equal({ success: true });
            expect(deleteFilesStub.calledOnce).to.be.true;
            expect(mockEsClient.index.calledOnce).to.be.true;
        });

        it('should throw InternalServerError for unsupported database during upload', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map([['indexPrefix', 'mdx-']])
            };
            const fixturePath = path.resolve(__dirname, '../../fixtures/road-network.json');
            // Clear require cache to ensure fresh load
            delete require.cache[require.resolve(fixturePath)];
            sinon.stub(Utils, 'deleteFiles').resolves();

            try {
                await roadNetwork.upload(unsupportedDb, {
                    fileDetails: { configFiles: [{ path: fixturePath }] },
                    fieldName: 'configFiles'
                });
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should create index template if it does not exist', async () => {
            mockEsClient.indices.existsIndexTemplate.resolves(false);
            const fixturePath = path.resolve(__dirname, '../../fixtures/road-network.json');
            // Clear require cache to ensure fresh load
            delete require.cache[require.resolve(fixturePath)];
            sinon.stub(Utils, 'deleteFiles').resolves();

            const result = await roadNetwork.upload(elasticDb, {
                fileDetails: { configFiles: [{ path: fixturePath }] },
                fieldName: 'configFiles'
            });

            expect(result).to.deep.equal({ success: true });
            expect(mockEsClient.indices.putIndexTemplate.calledOnce).to.be.true;
        });

        it('should throw BadRequestError for invalid road network schema', async () => {
            // Create a path to an invalid fixture (wrong schema)
            const invalidFixturePath = path.resolve(__dirname, '../../fixtures/behavior.json');
            delete require.cache[require.resolve(invalidFixturePath)];
            sinon.stub(Utils, 'deleteFiles').resolves();

            try {
                await roadNetwork.upload(elasticDb, {
                    fileDetails: { configFiles: [{ path: invalidFixturePath }] },
                    fieldName: 'configFiles'
                });
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("doesn't follow roadNetwork schema");
            }
        });

        it('should verify ingest pipeline exists during upload', async () => {
            mockEsClient.ingest.getPipeline.resolves({});
            sinon.stub(Elasticsearch, 'checkIngestPipelineExists').resolves(true);
            const fixturePath = path.resolve(__dirname, '../../fixtures/road-network.json');
            // Clear require cache to ensure fresh load
            delete require.cache[require.resolve(fixturePath)];
            sinon.stub(Utils, 'deleteFiles').resolves();

            const result = await roadNetwork.upload(elasticDb, {
                fileDetails: { configFiles: [{ path: fixturePath }] },
                fieldName: 'configFiles'
            });

            expect(result).to.deep.equal({ success: true });
            expect(Elasticsearch.checkIngestPipelineExists.calledOnce).to.be.true;
        });
        it('should throw BadRequestError when fieldName is null', async () => {
            try {
                await roadNetwork.upload(elasticDb, { fileDetails: null, fieldName: null });
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fieldName');
            }
        });

        it('should throw BadRequestError when fileDetails is null', async () => {
            try {
                await roadNetwork.upload(elasticDb, { fileDetails: null, fieldName: 'configFiles' });
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('No file has been uploaded');
            }
        });

        it('should throw BadRequestError when fieldName not in fileDetails', async () => {
            try {
                await roadNetwork.upload(elasticDb, { 
                    fileDetails: { otherField: [] }, 
                    fieldName: 'configFiles' 
                });
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('No file has been uploaded');
            }
        });

        it('should throw BadRequestError when file array is empty', async () => {
            try {
                await roadNetwork.upload(elasticDb, { 
                    fileDetails: { configFiles: [] }, 
                    fieldName: 'configFiles' 
                });
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('No file has been uploaded');
            }
        });
    });

    describe('getRoadNetwork', () => {
        it('should return road network when found', async () => {
            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: {
                                roadNetwork: {
                                    city: 'testCity',
                                    intersections: []
                                },
                                timestamp: '2023-01-12T14:20:10.000Z'
                            }
                        }]
                    }
                }
            });

            const result = await roadNetwork.getRoadNetwork(elasticDb);

            expect(result).to.have.property('roadNetwork');
            expect(result.roadNetwork).to.deep.equal({
                city: 'testCity',
                intersections: []
            });
            expect(result).to.have.property('timestamp');
        });

        it('should throw ResourceNotFoundError when index is absent and configMissingErr is true', async () => {
            searchStub.resolves({ indexAbsent: true });

            try {
                await roadNetwork.getRoadNetwork(elasticDb, true);
                throw new Error('Expected ResourceNotFoundError');
            } catch (error) {
                expect(error).to.be.instanceOf(ResourceNotFoundError);
                expect(error.message).to.include('roadNetwork not found');
            }
        });

        it('should return empty result when index is absent and configMissingErr is false', async () => {
            searchStub.resolves({ indexAbsent: true });

            const result = await roadNetwork.getRoadNetwork(elasticDb, false);

            expect(result).to.have.property('roadNetwork');
            expect(result.roadNetwork).to.deep.equal({});
        });

        it('should throw ResourceNotFoundError when no results and configMissingErr is true', async () => {
            searchStub.resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            try {
                await roadNetwork.getRoadNetwork(elasticDb, true);
                throw new Error('Expected ResourceNotFoundError');
            } catch (error) {
                expect(error).to.be.instanceOf(ResourceNotFoundError);
                expect(error.message).to.include('roadNetwork not found');
            }
        });

        it('should return empty result when no results and configMissingErr is false', async () => {
            searchStub.resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            const result = await roadNetwork.getRoadNetwork(elasticDb, false);

            expect(result).to.have.property('roadNetwork');
            expect(result.roadNetwork).to.deep.equal({});
            expect(result).to.have.property('timestamp');
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };

            try {
                await roadNetwork.getRoadNetwork(unsupportedDb);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });
    });

    describe('getIntersectionInfoMap', () => {
        it('should return intersection info map', () => {
            const testRoadNetwork = {
                city: 'testCity',
                intersections: [
                    { name: 'intersection1', segments: [] },
                    { name: 'intersection2', segments: [] }
                ]
            };

            const result = roadNetwork.getIntersectionInfoMap(testRoadNetwork);

            expect(result).to.be.instanceOf(Map);
            expect(result.size).to.equal(2);
            expect(result.has('city=testCity/intersection=intersection1')).to.be.true;
            expect(result.has('city=testCity/intersection=intersection2')).to.be.true;
        });

        it('should return empty map for empty intersections', () => {
            const testRoadNetwork = {
                city: 'testCity',
                intersections: []
            };

            const result = roadNetwork.getIntersectionInfoMap(testRoadNetwork);

            expect(result).to.be.instanceOf(Map);
            expect(result.size).to.equal(0);
        });
    });

    describe('getSegmentMap', () => {
        it('should return segment map from intersection list', () => {
            const intersectionList = [
                {
                    name: 'intersection1',
                    segments: [
                        {
                            id: 'seg1',
                            direction: 'north',
                            start: { lat: 0, lng: 0 },
                            end: { lat: 1, lng: 1 },
                            points: [{ lat: 0.5, lng: 0.5 }]
                        }
                    ]
                }
            ];

            const result = roadNetwork.getSegmentMap(intersectionList);

            expect(result).to.be.instanceOf(Map);
            expect(result.size).to.equal(1);
            expect(result.has('seg1')).to.be.true;
            expect(result.get('seg1')).to.deep.include({
                direction: 'north',
                start: { lat: 0, lng: 0 },
                end: { lat: 1, lng: 1 },
                points: [{ lat: 0.5, lng: 0.5 }],
                speed: 0,
                objectCount: 0
            });
        });

        it('should handle multiple segments across intersections', () => {
            const intersectionList = [
                {
                    name: 'intersection1',
                    segments: [
                        { id: 'seg1', direction: 'north', start: {}, end: {}, points: [] },
                        { id: 'seg2', direction: 'south', start: {}, end: {}, points: [] }
                    ]
                },
                {
                    name: 'intersection2',
                    segments: [
                        { id: 'seg3', direction: 'east', start: {}, end: {}, points: [] }
                    ]
                }
            ];

            const result = roadNetwork.getSegmentMap(intersectionList);

            expect(result.size).to.equal(3);
        });
    });

    describe('getMinimalSegmentMap', () => {
        it('should return minimal segment map with only speed and objectCount', () => {
            const segmentMap = new Map([
                ['seg1', { direction: 'north', start: {}, end: {}, points: [], speed: 50, objectCount: 10 }],
                ['seg2', { direction: 'south', start: {}, end: {}, points: [], speed: 30, objectCount: 5 }]
            ]);

            const result = roadNetwork.getMinimalSegmentMap(segmentMap);

            expect(result).to.be.instanceOf(Map);
            expect(result.size).to.equal(2);
            expect(result.get('seg1')).to.deep.equal({ speed: 50, objectCount: 10 });
            expect(result.get('seg2')).to.deep.equal({ speed: 30, objectCount: 5 });
        });
    });
});
