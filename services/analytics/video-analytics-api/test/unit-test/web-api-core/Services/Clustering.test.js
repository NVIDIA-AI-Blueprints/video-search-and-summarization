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
const Clustering = require('../../../../src/web-api-core/Services/Clustering');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');

describe('Clustering', () => {
    let clustering;
    let elasticDb;
    let searchStub;
    let mockEsClient;

    beforeEach(() => {
        clustering = new Clustering();
        mockEsClient = {
            index: sinon.stub().resolves({ result: 'created' }),
            ingest: {
                getPipeline: sinon.stub().resolves({})
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

    describe('getClusterLabels', () => {
        it('should return cluster labels map', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: [
                                {
                                    key: '0',
                                    label: {
                                        hits: {
                                            hits: [{ _source: { label: 'left-turn' } }]
                                        }
                                    }
                                },
                                {
                                    key: '1',
                                    label: {
                                        hits: {
                                            hits: [{ _source: { label: 'right-turn' } }]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await clustering.getClusterLabels(elasticDb, input);

            expect(result).to.be.instanceOf(Map);
            expect(result.get('0')).to.equal('left-turn');
            expect(result.get('1')).to.equal('right-turn');
        });

        it('should return cluster labels with clusterIndex filter', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                clusterIndex: '1'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: [
                                {
                                    key: '1',
                                    label: {
                                        hits: {
                                            hits: [{ _source: { label: 'right-turn' } }]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await clustering.getClusterLabels(elasticDb, input);

            expect(result).to.be.instanceOf(Map);
            expect(result.get('1')).to.equal('right-turn');
        });

        it('should return empty map when index is absent', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await clustering.getClusterLabels(elasticDb, input);

            expect(result).to.be.instanceOf(Map);
            expect(result.size).to.equal(0);
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await clustering.getClusterLabels(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('modelVersion');
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
                modelVersion: '2'
            };

            try {
                await clustering.getClusterLabels(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('getSampledBehaviorClusters', () => {
        it('should return empty clusters when no model version found', async () => {
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

            const result = await clustering.getSampledBehaviorClusters(elasticDb, input);

            expect(result.clusters).to.be.an('array').that.is.empty;
        });

        it('should return empty clusters when index is absent for model version', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await clustering.getSampledBehaviorClusters(elasticDb, input);

            expect(result.clusters).to.be.an('array').that.is.empty;
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                sensorId: 'sensor123'
            };

            try {
                await clustering.getSampledBehaviorClusters(elasticDb, input);
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
                await clustering.getSampledBehaviorClusters(elasticDb, input);
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
                await clustering.getSampledBehaviorClusters(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });

        it('should throw BadRequestError when maxClusterSampleSize is Infinity (schema validation)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                maxClusterSampleSize: Infinity
            };

            try {
                await clustering.getSampledBehaviorClusters(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('must be integer');
            }
        });

        it('should throw BadRequestError when minBehaviorDistance is Infinity (schema validation)', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                minBehaviorDistance: Infinity
            };

            try {
                await clustering.getSampledBehaviorClusters(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('must be number');
            }
        });

        it('should return sampled clusters with data when model version exists', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            // First call: getLatestModelVersion
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: {
                                info: { 'cluster.modelVersion': 'v2' }
                            }
                        }]
                    }
                }
            });

            // Second call: getClusterLabels
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: [
                                {
                                    key: '1',
                                    label: {
                                        hits: { hits: [{ _source: { label: 'left-turn' } }] }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            // Third call: getSampledBehaviorClustersFromEs
            searchStub.onCall(2).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: [
                                {
                                    key: '1',
                                    doc_count: 10,
                                    randomBehaviorSamples: {
                                        hits: {
                                            hits: [
                                                { _source: { id: 'behavior1', locations: [] } },
                                                { _source: { id: 'behavior2', locations: [] } }
                                            ]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await clustering.getSampledBehaviorClusters(elasticDb, input);

            expect(result.clusters).to.be.an('array').with.lengthOf(1);
            expect(result.clusters[0].clusterIndex).to.equal('1');
            expect(result.clusters[0].modelVersion).to.equal('v2');
            expect(result.clusters[0].label).to.equal('left-turn');
            expect(result.clusters[0].count).to.equal(10);
            expect(result.clusters[0].sampledBehaviors).to.have.lengthOf(2);
        });

        it('should return cluster with null label when no label exists', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z',
                clusterIndex: '2'
            };

            // First call: getLatestModelVersion
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: {
                                info: { 'cluster.modelVersion': 'v2' }
                            }
                        }]
                    }
                }
            });

            // Second call: getClusterLabels (no label for cluster 2)
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: []
                        }
                    }
                }
            });

            // Third call: getSampledBehaviorClustersFromEs
            searchStub.onCall(2).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: [
                                {
                                    key: '2',
                                    doc_count: 5,
                                    randomBehaviorSamples: {
                                        hits: {
                                            hits: [
                                                { _source: { id: 'behavior1' } }
                                            ]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await clustering.getSampledBehaviorClusters(elasticDb, input);

            expect(result.clusters).to.be.an('array').with.lengthOf(1);
            expect(result.clusters[0].clusterIndex).to.equal('2');
            expect(result.clusters[0].label).to.be.null;
        });

        it('should return empty clusters when behavior clusters index is absent', async () => {
            const input = {
                sensorId: 'sensor123',
                fromTimestamp: '2023-01-12T11:20:10.000Z',
                toTimestamp: '2023-01-12T14:20:10.000Z'
            };

            // First call: getLatestModelVersion
            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: {
                                info: { 'cluster.modelVersion': 'v2' }
                            }
                        }]
                    }
                }
            });

            // Second call: getClusterLabels
            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: { buckets: [] }
                    }
                }
            });

            // Third call: getSampledBehaviorClustersFromEs returns indexAbsent
            searchStub.onCall(2).resolves({ indexAbsent: true });

            const result = await clustering.getSampledBehaviorClusters(elasticDb, input);

            expect(result.clusters).to.be.an('array').that.is.empty;
        });
    });

    describe('isValidCluster', () => {
        it('should return true for valid cluster', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                clusterIndex: '1'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'behavior1' } }]
                    }
                }
            });

            const result = await clustering.isValidCluster(elasticDb, input);

            expect(result).to.be.true;
        });

        it('should return false when no behaviors found for cluster', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                clusterIndex: '999'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await clustering.isValidCluster(elasticDb, input);

            expect(result).to.be.false;
        });

        it('should return false when index is absent', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                clusterIndex: '1'
            };

            searchStub.resolves({ indexAbsent: true });

            const result = await clustering.isValidCluster(elasticDb, input);

            expect(result).to.be.false;
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2'
            };

            try {
                await clustering.isValidCluster(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('clusterIndex');
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
                modelVersion: '2',
                clusterIndex: '1'
            };

            try {
                await clustering.isValidCluster(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });
    });

    describe('doesClusterLabelExist', () => {
        it('should return true when label exists', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                label: 'left turn'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: [
                                {
                                    key: '0',
                                    label: {
                                        hits: {
                                            hits: [{ _source: { label: 'left-turn' } }]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await clustering.doesClusterLabelExist(elasticDb, input);

            expect(result).to.be.true;
        });

        it('should return false when label does not exist', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                label: 'non-existent'
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: [
                                {
                                    key: '0',
                                    label: {
                                        hits: {
                                            hits: [{ _source: { label: 'left-turn' } }]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await clustering.doesClusterLabelExist(elasticDb, input);

            expect(result).to.be.false;
        });

        it('should normalize label with extra spaces', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                label: '  left   turn  '
            };

            searchStub.resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: [
                                {
                                    key: '0',
                                    label: {
                                        hits: {
                                            hits: [{ _source: { label: 'left-turn' } }]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            const result = await clustering.doesClusterLabelExist(elasticDb, input);

            expect(result).to.be.true;
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2'
            };

            try {
                await clustering.doesClusterLabelExist(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('label');
            }
        });
    });

    describe('addClusterLabel', () => {
        it('should throw InvalidInputError for clusterIndex -1', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                clusterIndex: '-1',
                label: 'new label'
            };

            try {
                await clustering.addClusterLabel(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('Cannot assign label to clusterIndex -1');
            }
        });

        it('should throw InvalidInputError when cluster does not exist', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                clusterIndex: '999',
                label: 'new label'
            };

            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: []
                        }
                    }
                }
            });

            try {
                await clustering.addClusterLabel(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('clusterIndex');
            }
        });

        it('should throw InvalidInputError when label already exists', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                clusterIndex: '1',
                label: 'existing label'
            };

            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'behavior1' } }]
                    }
                }
            });

            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: [
                                {
                                    key: '0',
                                    label: {
                                        hits: {
                                            hits: [{ _source: { label: 'existing-label' } }]
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            });

            try {
                await clustering.addClusterLabel(elasticDb, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('already has label');
            }
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                clusterIndex: '1'
            };

            try {
                await clustering.addClusterLabel(elasticDb, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('label');
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
                modelVersion: '2',
                clusterIndex: '1',
                label: 'new label'
            };

            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'behavior1' } }]
                    }
                }
            });

            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: []
                        }
                    }
                }
            });

            try {
                await clustering.addClusterLabel(unsupportedDb, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.equal('Invalid database: UnsupportedDB.');
            }
        });

        it('should add cluster label successfully', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                clusterIndex: '1',
                label: 'new label'
            };

            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'behavior1' } }]
                    }
                }
            });

            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: []
                        }
                    }
                }
            });

            sinon.stub(Elasticsearch, 'checkIngestPipelineExists').resolves(true);

            const result = await clustering.addClusterLabel(elasticDb, input);

            expect(result).to.deep.equal({ success: true });
        });

        it('should use existing ingest pipeline if already exists', async () => {
            const input = {
                sensorId: 'sensor123',
                modelVersion: '2',
                clusterIndex: '1',
                label: 'new label'
            };

            searchStub.onCall(0).resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{ _source: { id: 'behavior1' } }]
                    }
                }
            });

            searchStub.onCall(1).resolves({
                indexAbsent: false,
                body: {
                    aggregations: {
                        clusterIndices: {
                            buckets: []
                        }
                    }
                }
            });

            mockEsClient.ingest.getPipeline.resolves({
                'addInsertTimestamp-field-timestamp': {}
            });

            sinon.stub(Elasticsearch, 'checkIngestPipelineExists').resolves(true);

            const result = await clustering.addClusterLabel(elasticDb, input);

            expect(result).to.deep.equal({ success: true });
        });
    });
});
