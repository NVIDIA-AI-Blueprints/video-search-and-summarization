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
const UsdAssets = require('../../../../src/web-api-core/Services/UsdAssets');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const Utils = require('../../../../src/web-api-core/Utils/Utils');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');
const ResourceNotFoundError = require('../../../../src/web-api-core/Errors/ResourceNotFoundError');

describe('UsdAssets', () => {
    let usdAssets;
    let elasticDb;
    let searchStub;
    let mockEsClient;

    beforeEach(() => {
        usdAssets = new UsdAssets();
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
        it('should upload usd assets file successfully', async () => {
            const fixturePath = path.resolve(__dirname, '../../fixtures/usd-assets.json');
            const deleteFilesStub = sinon.stub(Utils, 'deleteFiles').resolves();

            const result = await usdAssets.upload(elasticDb, {
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
                getConfigs: () => new Map()
            };
            const fixturePath = path.resolve(__dirname, '../../fixtures/usd-assets.json');
            sinon.stub(Utils, 'deleteFiles').resolves();

            try {
                await usdAssets.upload(unsupportedDb, {
                    fileDetails: { configFiles: [{ path: fixturePath }] },
                    fieldName: 'configFiles'
                });
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should throw BadRequestError for invalid usd assets schema', async () => {
            // Use behavior.json which doesn't match usd-assets schema
            const invalidFixturePath = path.resolve(__dirname, '../../fixtures/behavior.json');
            sinon.stub(Utils, 'deleteFiles').resolves();

            try {
                await usdAssets.upload(elasticDb, {
                    fileDetails: { configFiles: [{ path: invalidFixturePath }] },
                    fieldName: 'configFiles'
                });
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("doesn't follow usd assets schema");
            }
        });

        it('should create index template if it does not exist', async () => {
            mockEsClient.indices.existsIndexTemplate.resolves(false);
            const fixturePath = path.resolve(__dirname, '../../fixtures/usd-assets.json');
            sinon.stub(Utils, 'deleteFiles').resolves();

            const result = await usdAssets.upload(elasticDb, {
                fileDetails: { configFiles: [{ path: fixturePath }] },
                fieldName: 'configFiles'
            });

            expect(result).to.deep.equal({ success: true });
            expect(mockEsClient.indices.putIndexTemplate.calledOnce).to.be.true;
        });

        it('should verify ingest pipeline exists during upload', async () => {
            mockEsClient.ingest.getPipeline.resolves({});
            sinon.stub(Elasticsearch, 'checkIngestPipelineExists').resolves(true);
            const fixturePath = path.resolve(__dirname, '../../fixtures/usd-assets.json');
            sinon.stub(Utils, 'deleteFiles').resolves();

            const result = await usdAssets.upload(elasticDb, {
                fileDetails: { configFiles: [{ path: fixturePath }] },
                fieldName: 'configFiles'
            });

            expect(result).to.deep.equal({ success: true });
            expect(Elasticsearch.checkIngestPipelineExists.calledOnce).to.be.true;
        });
        it('should throw BadRequestError when fieldName is null', async () => {
            try {
                await usdAssets.upload(elasticDb, { fileDetails: null, fieldName: null });
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('fieldName');
            }
        });

        it('should throw BadRequestError when fileDetails is null', async () => {
            try {
                await usdAssets.upload(elasticDb, { fileDetails: null, fieldName: 'configFiles' });
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('No file has been uploaded');
            }
        });

        it('should throw BadRequestError when fieldName not in fileDetails', async () => {
            try {
                await usdAssets.upload(elasticDb, { 
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
                await usdAssets.upload(elasticDb, { 
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

    describe('getAssets', () => {
        it('should return usd assets when found', async () => {
            searchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [{
                            _source: {
                                usdAssets: {
                                    assets: [
                                        { name: 'asset1', bbox: { dimension: { x: 1, y: 2, z: 3 } } }
                                    ]
                                },
                                timestamp: '2023-01-12T14:20:10.000Z'
                            }
                        }]
                    }
                }
            });

            const result = await usdAssets.getAssets(elasticDb);

            expect(result).to.deep.equal({
                assets: [
                    { name: 'asset1', bbox: { dimension: { x: 1, y: 2, z: 3 } } }
                ]
            });
        });

        it('should throw ResourceNotFoundError when index is absent and configMissingErr is true', async () => {
            searchStub.resolves({ indexAbsent: true });

            try {
                await usdAssets.getAssets(elasticDb, true);
                throw new Error('Expected ResourceNotFoundError');
            } catch (error) {
                expect(error).to.be.instanceOf(ResourceNotFoundError);
                expect(error.message).to.include('usdAssets not found');
            }
        });

        it('should return empty result when index is absent and configMissingErr is false', async () => {
            searchStub.resolves({ indexAbsent: true });

            const result = await usdAssets.getAssets(elasticDb, false);

            expect(result).to.deep.equal({});
        });

        it('should throw ResourceNotFoundError when no results and configMissingErr is true', async () => {
            searchStub.resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            try {
                await usdAssets.getAssets(elasticDb, true);
                throw new Error('Expected ResourceNotFoundError');
            } catch (error) {
                expect(error).to.be.instanceOf(ResourceNotFoundError);
                expect(error.message).to.include('usdAssets not found');
            }
        });

        it('should return empty object when no results and configMissingErr is false', async () => {
            searchStub.resolves({
                indexAbsent: false,
                body: { hits: { hits: [] } }
            });

            const result = await usdAssets.getAssets(elasticDb, false);

            expect(result).to.deep.equal({});
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };

            try {
                await usdAssets.getAssets(unsupportedDb);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });
    });
});
