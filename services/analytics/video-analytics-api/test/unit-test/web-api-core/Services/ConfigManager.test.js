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
const ConfigManager = require('../../../../src/web-api-core/Services/ConfigManager');
const Elasticsearch = require('../../../../src/web-api-core/Utils/Elasticsearch');
const NotificationManager = require('../../../../src/web-api-core/Services/NotificationManager');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');

describe('ConfigManager', () => {

    describe('getValidDocTypes', () => {
        it('should return a Set containing valid doc types', () => {
            const result = ConfigManager.getValidDocTypes();

            expect(result).to.be.instanceOf(Set);
            expect(result.has('behavior-analytics')).to.be.true;
        });

        it('should contain behavior-analytics as valid doc type', () => {
            const result = ConfigManager.getValidDocTypes();

            expect(result.size).to.be.at.least(1);
            expect(result.has('behavior-analytics')).to.be.true;
        });
    });

    describe('getConfig', () => {
        let configManager;
        let elasticDb;
        let elasticsearchStub;

        beforeEach(() => {
            configManager = new ConfigManager();
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

        it('should return config when it exists', async () => {
            const docType = 'behavior-analytics';

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { config: '{"test": "value"}', timestamp: '2025-01-10T10:00:00.000Z' } }
                        ]
                    }
                }
            });

            const result = await configManager.getConfig(elasticDb, docType);

            expect(result).to.deep.equal({
                config: '{"test": "value"}',
                timestamp: '2025-01-10T10:00:00.000Z'
            });
        });

        it('should return null config with timestamp when config does not exist', async () => {
            const docType = 'behavior-analytics';

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            const result = await configManager.getConfig(elasticDb, docType);

            expect(result).to.have.property('config', null);
            expect(result).to.have.property('timestamp');
        });

        it('should return null config when index is absent', async () => {
            const docType = 'behavior-analytics';

            elasticsearchStub.resolves({ indexAbsent: true });

            const result = await configManager.getConfig(elasticDb, docType);

            expect(result).to.have.property('config', null);
            expect(result).to.have.property('timestamp');
        });

        it('should throw BadRequestError for invalid docType', async () => {
            const docType = 'invalid-doc-type';

            try {
                await configManager.getConfig(elasticDb, docType);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('Invalid docType');
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const docType = 'behavior-analytics';

            try {
                await configManager.getConfig(unsupportedDb, docType);
                throw new Error('Expected InternalServerError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });
    });

    describe('initConfig', () => {
        let configManager;
        let elasticDb;
        let elasticsearchStub;
        let mockEsClient;

        beforeEach(() => {
            configManager = new ConfigManager();
            mockEsClient = {
                index: sinon.stub().resolves({ result: 'created' }),
                indices: {
                    refresh: sinon.stub().resolves({})
                },
                ingest: {
                    getPipeline: sinon.stub().resolves({})
                }
            };
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => mockEsClient,
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
            sinon.stub(Elasticsearch, 'checkIngestPipelineExists').resolves(true);
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should throw BadRequestError for invalid docType', async () => {
            const docType = 'invalid-doc-type';
            const config = '{}';

            try {
                await configManager.initConfig(elasticDb, docType, config);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('Invalid docType');
            }
        });

        it('should throw BadRequestError when behavior analytics config does not follow schema', async () => {
            const docType = 'behavior-analytics';
            const invalidConfig = '{"invalid": "config"}';

            try {
                await configManager.initConfig(elasticDb, docType, invalidConfig);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Behavior analytics configuration doesn't follow schema");
            }
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const docType = 'behavior-analytics';
            const config = JSON.stringify({ app: [{ name: 'x', value: '' }] });

            try {
                await configManager.initConfig(unsupportedDb, docType, config);
                throw new Error('Expected InternalServerError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should successfully initialize config in Elasticsearch', async () => {
            const docType = 'behavior-analytics';
            const config = JSON.stringify({ app: [{ name: 'x', value: '' }] });

            // First call for insertConfigEs, then getConfigFromEs returns the inserted config
            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { config, timestamp: '2025-01-10T10:00:00.000Z' } }
                        ]
                    }
                }
            });

            await configManager.initConfig(elasticDb, docType, config);

            expect(mockEsClient.index.called).to.be.true;
            expect(mockEsClient.indices.refresh.called).to.be.true;
        });

        it('should throw InternalServerError when config not found after insertion', async () => {
            const docType = 'behavior-analytics';
            const config = JSON.stringify({ app: [{ name: 'x', value: '' }] });

            // After insertion, getConfigFromEs returns null
            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });

            try {
                await configManager.initConfig(elasticDb, docType, config);
                throw new Error('Expected InternalServerError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Insertion of');
            }
        });

        it('should write config audit entry during init', async () => {
            const docType = 'behavior-analytics';
            const config = JSON.stringify({ app: [{ name: 'x', value: '' }] });

            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            { _source: { config, timestamp: '2025-01-10T10:00:00.000Z' } }
                        ]
                    }
                }
            });

            await configManager.initConfig(elasticDb, docType, config);

            expect(mockEsClient.index.calledTwice).to.be.true;
            const auditInsert = mockEsClient.index.secondCall.args[0];
            expect(auditInsert.body).to.include({
                docType,
                config,
                eventType: 'upsert-all'
            });
        });
    });

    describe('update', () => {
        let configManager;
        let elasticDb;
        let messageBroker;
        let elasticsearchStub;
        let notificationStub;
        let mockEsClient;

        beforeEach(() => {
            configManager = new ConfigManager();
            mockEsClient = {
                index: sinon.stub().resolves({ result: 'updated' }),
                indices: {
                    refresh: sinon.stub().resolves({})
                },
                ingest: {
                    getPipeline: sinon.stub().resolves({})
                }
            };
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => mockEsClient,
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            messageBroker = {
                getName: () => 'Kafka',
                getProducer: () => ({})
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults');
            notificationStub = sinon.stub(NotificationManager.prototype, 'produceConfigNotification').resolves({});
            sinon.stub(Elasticsearch, 'checkIngestPipelineExists').resolves(true);
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should throw InvalidInputError when messageBroker is null', async () => {
            const docType = 'behavior-analytics';
            const inputConfig = {};

            try {
                await configManager.update(elasticDb, null, docType, inputConfig);
                throw new Error('Expected InvalidInputError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('message broker');
            }
        });

        it('should throw BadRequestError for invalid docType', async () => {
            const docType = 'invalid-doc-type';
            const inputConfig = {};

            try {
                await configManager.update(elasticDb, messageBroker, docType, inputConfig);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('Invalid docType');
            }
        });

        it('should throw BadRequestError when behavior analytics config does not follow schema', async () => {
            const docType = 'behavior-analytics';
            const invalidConfig = { invalid: 'config' };

            try {
                await configManager.update(elasticDb, messageBroker, docType, invalidConfig);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Behavior analytics configuration doesn't follow schema");
            }
        });

        it('should use strict schema for endpoint updates', async () => {
            const docType = 'behavior-analytics';
            const inputConfig = {
                app: [{ name: 'behaviorMaxPoints', value: '400' }],
                sensors: []
            };

            try {
                await configManager.update(elasticDb, messageBroker, docType, inputConfig);
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Behavior analytics configuration doesn't follow schema");
            }
        });

        it('should record pending update and produce notification', async () => {
            const docType = 'behavior-analytics';
            const inputConfig = { app: [{ name: 'x', value: '' }] };

            const result = await configManager.update(elasticDb, messageBroker, docType, inputConfig);

            expect(result).to.have.property('status', 'pending');
            expect(result.referenceId).to.match(/^video-analytics-api-/);
            expect(mockEsClient.index.calledOnce).to.be.true;
            const auditInsert = mockEsClient.index.firstCall.args[0];
            expect(auditInsert.body).to.include({
                docType,
                eventType: 'upsert',
                status: 'pending',
                referenceId: result.referenceId
            });
            expect(JSON.parse(auditInsert.body.config)).to.deep.equal(inputConfig);
            expect(notificationStub.calledOnce).to.be.true;
            expect(notificationStub.firstCall.args[0]).to.equal(messageBroker);
            expect(notificationStub.firstCall.args[1]).to.include({
                docType,
                eventType: 'upsert',
                referenceId: result.referenceId
            });
            expect(JSON.parse(notificationStub.firstCall.args[1].config)).to.deep.equal(inputConfig);
        });

        it('should throw InternalServerError for unsupported database', async () => {
            const unsupportedDb = {
                getName: () => 'UnsupportedDB',
                getClient: () => ({}),
                getConfigs: () => new Map()
            };
            const docType = 'behavior-analytics';
            const inputConfig = { app: [{ name: 'x', value: '' }] };

            try {
                await configManager.update(unsupportedDb, messageBroker, docType, inputConfig);
                throw new Error('Expected InternalServerError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid database');
            }
        });

        it('should successfully update config in Elasticsearch', async () => {
            const docType = 'behavior-analytics';
            const inputConfig = { app: [{ name: 'x', value: '' }] };

            const result = await configManager.update(elasticDb, messageBroker, docType, inputConfig);

            expect(result).to.have.property('status', 'pending');
            expect(result).to.have.property('referenceId');
            expect(mockEsClient.index.called).to.be.true;
            expect(notificationStub.called).to.be.true;
        });

        it('should return failure status when notification publishing fails', async () => {
            const docType = 'behavior-analytics';
            const inputConfig = { app: [{ name: 'x', value: '' }] };
            notificationStub.rejects(new Error('publish failed'));

            const result = await configManager.update(elasticDb, messageBroker, docType, inputConfig);

            expect(result).to.include({
                status: 'failure',
                config: null,
                error: 'Unable to send config update to behavior analytics via kafka.'
            });
            expect(mockEsClient.index.calledTwice).to.be.true;
            expect(mockEsClient.index.secondCall.args[0].body).to.include({
                docType,
                eventType: 'upsert',
                status: 'failure',
                referenceId: result.referenceId,
                error: 'Unable to send config update to behavior analytics via kafka.'
            });
        });

        it('should serialize requested behavior analytics config in the audit record', async () => {
            const docType = 'behavior-analytics';
            const inputConfig = { 
                app: [{ name: 'mode', value: 'live' }],
                sensors: [{ id: 'sensor-1', configs: [{ name: 'enabled', value: 'true' }] }]
            };

            const result = await configManager.update(elasticDb, messageBroker, docType, inputConfig);

            expect(result).to.have.property('status', 'pending');
            expect(JSON.parse(mockEsClient.index.firstCall.args[0].body.config)).to.deep.equal(inputConfig);
        });

        it('should retry notification publishing before marking update as failed', async () => {
            const docType = 'behavior-analytics';
            const inputConfig = { app: [{ name: 'x', value: '' }] };
            notificationStub.onCall(0).rejects(new Error('temporary failure'));
            notificationStub.onCall(1).resolves({});

            const result = await configManager.update(elasticDb, messageBroker, docType, inputConfig);

            expect(result).to.have.property('status', 'pending');
            expect(notificationStub.calledTwice).to.be.true;
        });
    });

    describe('recordConfigUpdateRequest', () => {
        let configManager;
        let elasticDb;
        let elasticsearchStub;
        let mockEsClient;

        beforeEach(() => {
            configManager = new ConfigManager();
            mockEsClient = {
                index: sinon.stub().resolves({ result: 'created' }),
                indices: {
                    refresh: sinon.stub().resolves({})
                }
            };
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => mockEsClient,
                getConfigs: () => new Map([
                    ['indexPrefix', 'mdx-'],
                    ['rawIndex', 'mdx-raw-*']
                ])
            };
            elasticsearchStub = sinon.stub(Elasticsearch, 'getSearchResults').resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: []
                    }
                }
            });
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should use relaxed schema for direct Kafka update requests', async () => {
            const config = {
                app: [{ name: 'behaviorMaxPoints', value: '400' }],
                sensors: []
            };

            const result = await configManager.recordConfigUpdateRequest(elasticDb, {
                docType: 'behavior-analytics',
                referenceId: 'kafka-1',
                config,
                timestamp: '2026-05-14T06:52:36.000Z',
                eventType: 'upsert',
                error: null
            });

            expect(result).to.deep.equal({ referenceId: 'kafka-1', status: 'pending', error: null });
            expect(elasticsearchStub.calledOnce).to.be.true;
            expect(mockEsClient.index.calledOnce).to.be.true;
            const auditInsert = mockEsClient.index.firstCall.args[0];
            expect(auditInsert.id).to.equal('kafka-1');
            expect(auditInsert.body).to.include({
                docType: 'behavior-analytics',
                timestamp: '2026-05-14T06:52:36.000Z',
                eventType: 'upsert',
                referenceId: 'kafka-1',
                status: 'pending',
                error: null
            });
            expect(JSON.parse(auditInsert.body.config)).to.deep.equal(config);
        });

        it('should allow blank direct Kafka update requests', async () => {
            const config = {};

            const result = await configManager.recordConfigUpdateRequest(elasticDb, {
                docType: 'behavior-analytics',
                referenceId: 'kafka-blank',
                config,
                timestamp: '2026-05-14T06:52:36.000Z',
                eventType: 'upsert',
                error: null
            });

            expect(result).to.deep.equal({ referenceId: 'kafka-blank', status: 'pending', error: null });
            expect(mockEsClient.index.calledOnce).to.be.true;
            const auditInsert = mockEsClient.index.firstCall.args[0];
            expect(auditInsert.id).to.equal('kafka-blank');
            expect(auditInsert.body).to.include({
                docType: 'behavior-analytics',
                timestamp: '2026-05-14T06:52:36.000Z',
                eventType: 'upsert',
                referenceId: 'kafka-blank',
                status: 'pending',
                error: null
            });
            expect(JSON.parse(auditInsert.body.config)).to.deep.equal(config);
        });

        it('should validate kafka-prefixed direct update requests with behavior analytics config schema', async () => {
            const config = {
                unknownBehaviorAnalyticsField: true
            };

            try {
                await configManager.recordConfigUpdateRequest(elasticDb, {
                    docType: 'behavior-analytics',
                    referenceId: 'kafka-invalid',
                    config,
                    timestamp: '2026-05-14T06:52:36.000Z',
                    eventType: 'upsert',
                    error: null
                });
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Behavior analytics configuration doesn't follow schema");
            }

            expect(elasticsearchStub.called).to.be.false;
            expect(mockEsClient.index.called).to.be.false;
        });

        it('should update audit config and record error for duplicate direct Kafka referenceId', async () => {
            const config = {
                app: [{ name: 'behaviorMaxPoints', value: '500' }]
            };
            const existingConfig = { app: [{ name: 'behaviorMaxPoints', value: '400' }] };
            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    docType: 'behavior-analytics',
                                    referenceId: 'kafka-4',
                                    config: JSON.stringify(existingConfig),
                                    timestamp: '2026-05-14T06:52:36.000Z',
                                    eventType: 'upsert',
                                    status: 'pending',
                                    error: null
                                }
                            }
                        ]
                    }
                }
            });

            const result = await configManager.recordConfigUpdateRequest(elasticDb, {
                docType: 'behavior-analytics',
                referenceId: 'kafka-4',
                config,
                timestamp: '2026-05-14T06:53:36.000Z',
                eventType: 'upsert',
                error: null
            });

            const expectedError = 'Duplicate referenceId kafka-4 received via Kafka config update request. Overwriting audit config with the duplicate request config.';
            expect(result).to.deep.equal({ referenceId: 'kafka-4', status: 'pending', error: expectedError });
            expect(mockEsClient.index.calledOnce).to.be.true;
            const auditInsert = mockEsClient.index.firstCall.args[0];
            expect(auditInsert.id).to.equal('kafka-4');
            expect(auditInsert.body).to.include({
                docType: 'behavior-analytics',
                timestamp: '2026-05-14T06:53:36.000Z',
                eventType: 'upsert',
                referenceId: 'kafka-4',
                status: 'pending',
                error: expectedError
            });
            expect(JSON.parse(auditInsert.body.config)).to.deep.equal(config);
        });

        it('should append duplicate direct Kafka referenceId error to existing audit error', async () => {
            const config = {
                app: [{ name: 'behaviorMaxPoints', value: '501' }]
            };
            const existingError = 'previous validation warning';
            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    docType: 'behavior-analytics',
                                    referenceId: 'kafka-with-error',
                                    config: JSON.stringify({ app: [{ name: 'behaviorMaxPoints', value: '400' }] }),
                                    timestamp: '2026-05-14T06:52:36.000Z',
                                    eventType: 'upsert',
                                    status: 'pending',
                                    error: existingError
                                }
                            }
                        ]
                    }
                }
            });

            const result = await configManager.recordConfigUpdateRequest(elasticDb, {
                docType: 'behavior-analytics',
                referenceId: 'kafka-with-error',
                config,
                timestamp: '2026-05-14T06:53:36.000Z',
                eventType: 'upsert',
                error: null
            });

            const duplicateError = 'Duplicate referenceId kafka-with-error received via Kafka config update request. Overwriting audit config with the duplicate request config.';
            const expectedError = `${existingError}; ${duplicateError}`;
            expect(result).to.deep.equal({ referenceId: 'kafka-with-error', status: 'pending', error: expectedError });
            const auditInsert = mockEsClient.index.firstCall.args[0];
            expect(auditInsert.body).to.include({
                timestamp: '2026-05-14T06:53:36.000Z',
                eventType: 'upsert',
                status: 'pending',
                error: expectedError
            });
            expect(JSON.parse(auditInsert.body.config)).to.deep.equal(config);
        });

        it('should ignore incoming request error when composing duplicate direct Kafka referenceId error', async () => {
            const config = {
                app: [{ name: 'behaviorMaxPoints', value: '502' }]
            };
            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    docType: 'behavior-analytics',
                                    referenceId: 'kafka-with-request-error',
                                    config: JSON.stringify({ app: [{ name: 'behaviorMaxPoints', value: '400' }] }),
                                    timestamp: '2026-05-14T06:52:36.000Z',
                                    eventType: 'upsert',
                                    status: 'pending',
                                    error: null
                                }
                            }
                        ]
                    }
                }
            });

            const result = await configManager.recordConfigUpdateRequest(elasticDb, {
                docType: 'behavior-analytics',
                referenceId: 'kafka-with-request-error',
                config,
                timestamp: '2026-05-14T06:53:36.000Z',
                eventType: 'upsert',
                error: 'producer warning'
            });

            const duplicateError = 'Duplicate referenceId kafka-with-request-error received via Kafka config update request. Overwriting audit config with the duplicate request config.';
            const expectedError = duplicateError;
            expect(result).to.deep.equal({ referenceId: 'kafka-with-request-error', status: 'pending', error: expectedError });
            const auditInsert = mockEsClient.index.firstCall.args[0];
            expect(auditInsert.body.error).to.equal(expectedError);
            expect(JSON.parse(auditInsert.body.config)).to.deep.equal(config);
        });

        it('should validate kafka-prefixed update results with behavior analytics config schema', async () => {
            const config = {
                unknownBehaviorAnalyticsField: true
            };

            try {
                await configManager.updateConfigResult(elasticDb, {
                    docType: 'behavior-analytics',
                    referenceId: 'kafka-invalid-result',
                    status: 'success',
                    config,
                    error: null
                });
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Behavior analytics configuration doesn't follow schema");
            }

            expect(elasticsearchStub.called).to.be.false;
            expect(mockEsClient.index.called).to.be.false;
        });

        it('should schema-validate video-analytics-api update results', async () => {
            const config = {
                unknownBehaviorAnalyticsField: true
            };

            try {
                await configManager.updateConfigResult(elasticDb, {
                    docType: 'behavior-analytics',
                    referenceId: 'video-analytics-api-1',
                    status: 'success',
                    config,
                    error: null
                });
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Behavior analytics configuration doesn't follow schema");
            }

            expect(elasticsearchStub.called).to.be.false;
            expect(mockEsClient.index.called).to.be.false;
        });

        it('should use strict schema for video-analytics-api update results', async () => {
            const config = {
                app: [{ name: 'behaviorMaxPoints', value: '400' }],
                sensors: []
            };

            try {
                await configManager.updateConfigResult(elasticDb, {
                    docType: 'behavior-analytics',
                    referenceId: 'video-analytics-api-2',
                    status: 'success',
                    config,
                    error: null
                });
                throw new Error('Expected BadRequestError to be thrown');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include("Behavior analytics configuration doesn't follow schema");
            }

            expect(elasticsearchStub.called).to.be.false;
            expect(mockEsClient.index.called).to.be.false;
        });
    });

    describe('updateConfigResult', () => {
        let configManager;
        let elasticDb;
        let elasticsearchStub;
        let mockEsClient;

        beforeEach(() => {
            configManager = new ConfigManager();
            mockEsClient = {
                index: sinon.stub().resolves({ result: 'updated' }),
                indices: {
                    refresh: sinon.stub().resolves({})
                }
            };
            elasticDb = {
                getName: () => 'Elasticsearch',
                getClient: () => mockEsClient,
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

        it('should use relaxed schema for Kafka update results', async () => {
            const config = {
                app: [{ name: 'behaviorMaxPoints', value: '400' }],
                sensors: []
            };
            elasticsearchStub.resolves({
                indexAbsent: false,
                body: {
                    hits: {
                        hits: [
                            {
                                _source: {
                                    docType: 'behavior-analytics',
                                    referenceId: 'kafka-1',
                                    config: JSON.stringify(config),
                                    timestamp: '2026-05-14T06:52:36.000Z',
                                    eventType: 'upsert',
                                    status: 'pending',
                                    error: null
                                }
                            }
                        ]
                    }
                }
            });

            await configManager.updateConfigResult(elasticDb, {
                docType: 'behavior-analytics',
                referenceId: 'kafka-1',
                status: 'success',
                config,
                error: null
            });

            expect(mockEsClient.index.calledTwice).to.be.true;
            const configInsert = mockEsClient.index.firstCall.args[0];
            expect(configInsert.id).to.equal('behavior-analytics');
            expect(configInsert.body).to.include({
                docType: 'behavior-analytics',
                status: 'success',
                referenceId: 'kafka-1',
                error: null,
                timestamp: '2026-05-14T06:52:36.000Z'
            });
            expect(JSON.parse(configInsert.body.config)).to.deep.equal(config);

            const auditInsert = mockEsClient.index.secondCall.args[0];
            expect(auditInsert.id).to.equal('kafka-1');
            expect(auditInsert.body).to.include({
                docType: 'behavior-analytics',
                timestamp: '2026-05-14T06:52:36.000Z',
                eventType: 'upsert',
                referenceId: 'kafka-1',
                status: 'success',
                error: null
            });
            expect(JSON.parse(auditInsert.body.config)).to.deep.equal(config);
        });
    });

});
