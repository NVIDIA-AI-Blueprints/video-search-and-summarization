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

const { expect } = require('chai');
const sinon = require('sinon');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch.js');
const Database = require('../../../../src/web-api-core/Utils/Database.js');
const IndexNotFoundError = require('../../../../src/web-api-core/Errors/IndexNotFoundError.js');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError.js');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError.js');

describe('Elasticsearch', () => {

    describe('constructor', () => {
        it('should create an Elasticsearch instance that extends Database', () => {
            const connectionObject = { node: 'http://localhost:9200' };
            const configs = new Map([['timeout', 30000]]);
            const es = new Elasticsearch(connectionObject, configs);

            expect(es).to.be.an.instanceof(Elasticsearch);
            expect(es).to.be.an.instanceof(Database);
        });

        it('should set the database name to "Elasticsearch"', () => {
            const connectionObject = { node: 'http://localhost:9200' };
            const configs = new Map();
            const es = new Elasticsearch(connectionObject, configs);

            expect(es.getName()).to.equal('Elasticsearch');
        });

        it('should store the configs', () => {
            const connectionObject = { node: 'http://localhost:9200' };
            const configs = new Map([['key', 'value']]);
            const es = new Elasticsearch(connectionObject, configs);

            expect(es.getConfigs()).to.equal(configs);
        });

        it('should create an Elasticsearch client', () => {
            const connectionObject = { node: 'http://localhost:9200' };
            const configs = new Map();
            const es = new Elasticsearch(connectionObject, configs);

            expect(es.getClient()).to.not.be.undefined;
            expect(es.getClient()).to.have.property('search');
        });
    });

    describe('getElasticErrors', () => {
        it('should return elasticsearch errors object', () => {
            const errors = Elasticsearch.getElasticErrors();

            expect(errors).to.be.an('object');
            expect(errors).to.have.property('ElasticsearchClientError');
        });
    });

    describe('searchResultFormatter', () => {
        it('should format elasticsearch search results correctly', () => {
            const esResult = {
                hits: {
                    hits: [
                        { _source: { id: 1, name: 'doc1' } },
                        { _source: { id: 2, name: 'doc2' } },
                        { _source: { id: 3, name: 'doc3' } }
                    ]
                }
            };

            const result = Elasticsearch.searchResultFormatter(esResult);

            expect(result).to.be.an('array');
            expect(result).to.have.lengthOf(3);
            expect(result[0]).to.deep.equal({ id: 1, name: 'doc1' });
            expect(result[1]).to.deep.equal({ id: 2, name: 'doc2' });
            expect(result[2]).to.deep.equal({ id: 3, name: 'doc3' });
        });

        it('should return empty array when no hits', () => {
            const esResult = {
                hits: {
                    hits: []
                }
            };

            const result = Elasticsearch.searchResultFormatter(esResult);

            expect(result).to.be.an('array');
            expect(result).to.have.lengthOf(0);
        });
    });

    describe('getIndex', () => {
        it('should return correct index for "behavior"', () => {
            const index = Elasticsearch.getIndex('behavior');
            expect(index).to.equal('behavior-*');
        });

        it('should return correct index for "calibration"', () => {
            const index = Elasticsearch.getIndex('calibration');
            expect(index).to.equal('calibration');
        });

        it('should return correct index for "alerts"', () => {
            const index = Elasticsearch.getIndex('alerts');
            expect(index).to.equal('alerts-*');
        });

        it('should return correct index for "frames"', () => {
            const index = Elasticsearch.getIndex('frames');
            expect(index).to.equal('frames-*');
        });

        it('should return correct index for "events"', () => {
            const index = Elasticsearch.getIndex('events');
            expect(index).to.equal('events-*');
        });

        it('should throw InvalidInputError for invalid index key', () => {
            expect(() => {
                Elasticsearch.getIndex('invalidKey');
            }).to.throw(InvalidInputError, /Invalid index key/);
        });

        it('should throw InvalidInputError with list of valid keys for invalid key', () => {
            expect(() => {
                Elasticsearch.getIndex('nonexistent');
            }).to.throw(InvalidInputError, /Valid keys are:/);
        });
    });

    describe('getSearchResults', () => {
        let mockClient;

        beforeEach(() => {
            mockClient = {
                indices: {
                    exists: sinon.stub()
                },
                search: sinon.stub()
            };
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should return search results when index exists', async () => {
            const queryObject = { index: 'test-index', body: { query: { match_all: {} } } };
            const mockSearchResult = { hits: { hits: [{ _source: { id: 1 } }] } };

            mockClient.indices.exists.resolves(true);
            mockClient.search.resolves(mockSearchResult);

            const result = await Elasticsearch.getSearchResults(mockClient, queryObject);

            expect(result.body).to.deep.equal(mockSearchResult);
            expect(result.indexAbsent).to.be.false;
        });

        it('should return indexAbsent true when index does not exist and indexAbsentErr is false', async () => {
            const queryObject = { index: 'nonexistent-index', body: { query: { match_all: {} } } };

            mockClient.indices.exists.resolves(false);

            const result = await Elasticsearch.getSearchResults(mockClient, queryObject, false);

            expect(result.body).to.be.null;
            expect(result.indexAbsent).to.be.true;
        });

        it('should throw IndexNotFoundError when index does not exist and indexAbsentErr is true', async () => {
            const queryObject = { index: 'nonexistent-index', body: { query: { match_all: {} } } };

            mockClient.indices.exists.resolves(false);

            try {
                await Elasticsearch.getSearchResults(mockClient, queryObject, true);
                expect.fail('Should have thrown IndexNotFoundError');
            } catch (error) {
                expect(error).to.be.an.instanceof(IndexNotFoundError);
                expect(error.message).to.include('nonexistent-index');
            }
        });

        it('should throw IndexNotFoundError by default when index does not exist', async () => {
            const queryObject = { index: 'missing-index', body: { query: { match_all: {} } } };

            mockClient.indices.exists.resolves(false);

            try {
                await Elasticsearch.getSearchResults(mockClient, queryObject);
                expect.fail('Should have thrown IndexNotFoundError');
            } catch (error) {
                expect(error).to.be.an.instanceof(IndexNotFoundError);
            }
        });
    });

    describe('getScrollSearchResults', () => {
        let mockClient;

        beforeEach(() => {
            mockClient = {
                indices: {
                    exists: sinon.stub()
                },
                search: sinon.stub(),
                scroll: sinon.stub(),
                clearScroll: sinon.stub()
            };
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should return scroll search results when index exists', async () => {
            const queryObject = { index: 'test-index', body: { query: { match_all: {} } } };
            
            mockClient.indices.exists.resolves(true);
            mockClient.search.resolves({
                _scroll_id: 'scroll123',
                hits: { hits: [{ _source: { id: 1 } }, { _source: { id: 2 } }] }
            });
            mockClient.scroll.resolves({
                _scroll_id: 'scroll123',
                hits: { hits: [] }
            });
            mockClient.clearScroll.resolves({});

            const result = await Elasticsearch.getScrollSearchResults(mockClient, queryObject);

            expect(result.hitSources).to.be.an('array');
            expect(result.hitSources).to.have.lengthOf(2);
            expect(result.indexAbsent).to.be.false;
        });

        it('should return indexAbsent true when index does not exist and indexAbsentErr is false', async () => {
            const queryObject = { index: 'nonexistent-index', body: { query: { match_all: {} } } };

            mockClient.indices.exists.resolves(false);

            const result = await Elasticsearch.getScrollSearchResults(mockClient, queryObject, false);

            expect(result.hitSources).to.be.null;
            expect(result.indexAbsent).to.be.true;
        });

        it('should throw IndexNotFoundError when index does not exist and indexAbsentErr is true', async () => {
            const queryObject = { index: 'nonexistent-index', body: { query: { match_all: {} } } };

            mockClient.indices.exists.resolves(false);

            try {
                await Elasticsearch.getScrollSearchResults(mockClient, queryObject, true);
                expect.fail('Should have thrown IndexNotFoundError');
            } catch (error) {
                expect(error).to.be.an.instanceof(IndexNotFoundError);
                expect(error.message).to.include('nonexistent-index');
            }
        });
    });

    describe('getDocCount', () => {
        let mockClient;

        beforeEach(() => {
            mockClient = {
                indices: {
                    exists: sinon.stub()
                },
                count: sinon.stub()
            };
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should return document count when index exists', async () => {
            const queryObject = { index: 'test-index', body: { query: { match_all: {} } } };

            mockClient.indices.exists.resolves(true);
            mockClient.count.resolves({ count: 42 });

            const result = await Elasticsearch.getDocCount(mockClient, queryObject);

            expect(result.count).to.equal(42);
            expect(result.indexAbsent).to.be.false;
        });

        it('should return count 0 and indexAbsent true when index does not exist and indexAbsentErr is false', async () => {
            const queryObject = { index: 'nonexistent-index', body: { query: { match_all: {} } } };

            mockClient.indices.exists.resolves(false);

            const result = await Elasticsearch.getDocCount(mockClient, queryObject, false);

            expect(result.count).to.equal(0);
            expect(result.indexAbsent).to.be.true;
        });

        it('should throw IndexNotFoundError when index does not exist and indexAbsentErr is true', async () => {
            const queryObject = { index: 'nonexistent-index', body: { query: { match_all: {} } } };

            mockClient.indices.exists.resolves(false);

            try {
                await Elasticsearch.getDocCount(mockClient, queryObject, true);
                expect.fail('Should have thrown IndexNotFoundError');
            } catch (error) {
                expect(error).to.be.an.instanceof(IndexNotFoundError);
                expect(error.message).to.include('nonexistent-index');
            }
        });

        it('should throw IndexNotFoundError by default when index does not exist', async () => {
            const queryObject = { index: 'missing-index', body: { query: { match_all: {} } } };

            mockClient.indices.exists.resolves(false);

            try {
                await Elasticsearch.getDocCount(mockClient, queryObject);
                expect.fail('Should have thrown IndexNotFoundError');
            } catch (error) {
                expect(error).to.be.an.instanceof(IndexNotFoundError);
            }
        });
    });

    describe('checkIngestPipelineExists', () => {
        let mockClient;

        beforeEach(() => {
            mockClient = {
                ingest: {
                    getPipeline: sinon.stub()
                }
            };
        });

        it('should return true when pipeline exists', async () => {
            mockClient.ingest.getPipeline.resolves({ 'test-pipeline': {} });

            const result = await Elasticsearch.checkIngestPipelineExists(mockClient, 'test-pipeline');

            expect(result).to.be.true;
            expect(mockClient.ingest.getPipeline.calledOnce).to.be.true;
        });

        it('should throw InternalServerError with 404 message when pipeline does not exist', async () => {
            const elasticErrors = Elasticsearch.getElasticErrors();
            const error404 = new elasticErrors.ResponseError({
                statusCode: 404,
                body: { error: 'not_found' },
                headers: {}
            });
            mockClient.ingest.getPipeline.rejects(error404);

            try {
                await Elasticsearch.checkIngestPipelineExists(mockClient, 'missing-pipeline');
                expect.fail('Should have thrown InternalServerError');
            } catch (error) {
                expect(error).to.be.an.instanceof(InternalServerError);
                expect(error.message).to.include("does not exist in Elasticsearch");
                expect(error.message).to.include('missing-pipeline');
            }
        });

        it('should throw InternalServerError with generic message for non-404 errors', async () => {
            const genericError = new Error('Connection refused');
            mockClient.ingest.getPipeline.rejects(genericError);

            try {
                await Elasticsearch.checkIngestPipelineExists(mockClient, 'test-pipeline');
                expect.fail('Should have thrown InternalServerError');
            } catch (error) {
                expect(error).to.be.an.instanceof(InternalServerError);
                expect(error.message).to.include('Error checking ingest pipeline');
                expect(error.message).to.include('test-pipeline');
            }
        });
    });

});
