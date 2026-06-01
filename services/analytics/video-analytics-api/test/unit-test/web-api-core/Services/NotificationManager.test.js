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
const NotificationManager = require('../../../../src/web-api-core/Services/NotificationManager');
const Kafka = require('../../../../src/web-api-core/Utils/Kafka');
const BadRequestError = require('../../../../src/web-api-core/Errors/BadRequestError');
const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError');
const InternalServerError = require('../../../../src/web-api-core/Errors/InternalServerError');

describe('NotificationManager', () => {
    let notificationManager;
    let kafkaBroker;
    let kafkaProduceStub;

    beforeEach(() => {
        notificationManager = new NotificationManager();
        kafkaBroker = {
            getName: () => 'Kafka',
            getClient: () => ({}),
            getAdminClient: () => ({})
        };
        kafkaProduceStub = sinon.stub(Kafka, 'produceMessages').resolves({ success: true });
        sinon.stub(Kafka, 'getTopic').returns('mdx-notification');
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('produceCalibrationNotification', () => {
        it('should produce calibration notification successfully', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                eventType: 'upsert-all',
                calibration: {
                    version: '1.0',
                    osmURL: '',
                    calibrationType: 'geo',
                    sensors: []
                }
            };

            const result = await notificationManager.produceCalibrationNotification(kafkaBroker, input);

            expect(result).to.deep.equal({ success: true });
            expect(kafkaProduceStub.calledOnce).to.be.true;
        });

        it('should throw InvalidInputError when messageBroker is null', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                eventType: 'upsert',
                calibration: {}
            };

            try {
                await notificationManager.produceCalibrationNotification(null, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('message broker');
            }
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                eventType: 'upsert'
            };

            try {
                await notificationManager.produceCalibrationNotification(kafkaBroker, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('calibration');
            }
        });

        it('should throw InvalidInputError for invalid timestamp', async () => {
            const input = {
                timestamp: 'invalid-timestamp',
                eventType: 'upsert',
                calibration: { calibrationType: 'geo', sensors: [] }
            };

            try {
                await notificationManager.produceCalibrationNotification(kafkaBroker, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('Invalid timestamp');
            }
        });

        it('should throw BadRequestError for invalid eventType', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                eventType: 'invalid',
                calibration: {}
            };

            try {
                await notificationManager.produceCalibrationNotification(kafkaBroker, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('eventType');
            }
        });

        it('should accept delete eventType', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                eventType: 'delete',
                calibration: {
                    version: '1.0',
                    osmURL: '',
                    calibrationType: 'geo',
                    sensors: []
                }
            };

            const result = await notificationManager.produceCalibrationNotification(kafkaBroker, input);

            expect(result).to.deep.equal({ success: true });
        });

        it('should throw InvalidInputError for invalid calibration schema', async () => {
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                eventType: 'upsert',
                calibration: {
                    calibrationType: 'invalid-type',
                    sensors: []
                }
            };

            try {
                await notificationManager.produceCalibrationNotification(kafkaBroker, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include("Calibration doesn't follow schema");
            }
        });

        it('should allow empty calibrationType with empty sensors (no calibration file)', async () => {
            // Special case: empty calibrationType with empty sensors is allowed
            // This represents when no calibration file is present
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                eventType: 'upsert-all',
                calibration: {
                    version: '1.0',
                    osmURL: '',
                    calibrationType: '',
                    sensors: []
                }
            };

            const result = await notificationManager.produceCalibrationNotification(kafkaBroker, input);

            expect(result).to.deep.equal({ success: true });
        });

        it('should throw InternalServerError for unsupported message broker', async () => {
            const unsupportedBroker = {
                getName: () => 'UnsupportedBroker',
                getClient: () => ({})
            };
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                eventType: 'upsert-all',
                calibration: {
                    version: '1.0',
                    osmURL: '',
                    calibrationType: 'geo',
                    sensors: []
                }
            };

            try {
                await notificationManager.produceCalibrationNotification(unsupportedBroker, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid message broker');
            }
        });

        it('should throw InvalidInputError when calibrationType is empty but sensors array has items', async () => {
            // This covers line 122 - when calibrationType is empty string but sensors exist
            const input = {
                timestamp: '2023-01-12T14:20:10.000Z',
                eventType: 'upsert',
                calibration: {
                    version: '1.0',
                    osmURL: '',
                    calibrationType: '',  // Empty calibrationType
                    sensors: [{
                        id: 'sensor1',
                        type: 'camera',
                        origin: { lat: 0, lng: 0 },
                        geoLocation: { lat: 0, lng: 0 },
                        coordinates: { x: 0, y: 0 },
                        scaleFactor: 1,
                        attributes: [],
                        place: [],
                        imageCoordinates: [],
                        globalCoordinates: []
                    }]
                }
            };

            try {
                await notificationManager.produceCalibrationNotification(kafkaBroker, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include("Calibration doesn't follow schema");
            }
        });
    });

    describe('consumeAndProcessNotification', () => {
        it('should throw InvalidInputError when messageBroker is null', async () => {
            try {
                await notificationManager.consumeAndProcessNotification(null, {}, {}, {});
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('message broker');
            }
        });

        it('should throw InternalServerError for unsupported message broker', async () => {
            const unsupportedBroker = {
                getName: () => 'UnsupportedBroker',
                getClient: () => ({})
            };

            try {
                await notificationManager.consumeAndProcessNotification(unsupportedBroker, {}, {}, {});
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid message broker');
            }
        });

        it('should call Kafka consumer initialization for Kafka broker', async () => {
            const initConsumerStub = sinon.stub(Kafka, 'initializeConsumer').resolves();

            await notificationManager.consumeAndProcessNotification(kafkaBroker, {}, {}, {});

            expect(initConsumerStub.calledOnce).to.be.true;
        });
    });

    describe('produceConfigNotification', () => {
        it('should produce config notification successfully', async () => {
            const input = {
                docType: 'behavior-analytics',
                timestamp: '2023-01-12T14:20:10.000Z',
                referenceId: 'request-1',
                eventType: 'upsert-all',
                config: JSON.stringify({ app: [{ name: 'x', value: '' }] })
            };

            const result = await notificationManager.produceConfigNotification(kafkaBroker, input);

            expect(result).to.deep.equal({ success: true });
        });

        it('should throw InvalidInputError when messageBroker is null', async () => {
            const input = {
                docType: 'behavior-analytics',
                timestamp: '2023-01-12T14:20:10.000Z',
                referenceId: 'request-1',
                eventType: 'upsert',
                config: JSON.stringify({ app: [{ name: 'x', value: '' }] })
            };

            try {
                await notificationManager.produceConfigNotification(null, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('message broker');
            }
        });

        it('should throw BadRequestError for missing required properties', async () => {
            const input = {
                docType: 'behavior-analytics',
                timestamp: '2023-01-12T14:20:10.000Z'
            };

            try {
                await notificationManager.produceConfigNotification(kafkaBroker, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('eventType');
            }
        });

        it('should throw InvalidInputError for invalid timestamp', async () => {
            const input = {
                docType: 'behavior-analytics',
                timestamp: 'invalid-timestamp',
                referenceId: 'request-1',
                eventType: 'upsert',
                config: JSON.stringify({ app: [{ name: 'x', value: '' }] })
            };

            try {
                await notificationManager.produceConfigNotification(kafkaBroker, input);
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('Invalid timestamp');
            }
        });

        it('should throw BadRequestError for invalid docType', async () => {
            const input = {
                docType: 'invalid-doc-type',
                timestamp: '2023-01-12T14:20:10.000Z',
                referenceId: 'request-1',
                eventType: 'upsert',
                config: JSON.stringify({ app: [{ name: 'x', value: '' }] })
            };

            try {
                await notificationManager.produceConfigNotification(kafkaBroker, input);
                throw new Error('Expected BadRequestError');
            } catch (error) {
                expect(error).to.be.instanceOf(BadRequestError);
                expect(error.message).to.include('docType');
            }
        });

        it('should allow null config with upsert-all eventType', async () => {
            const input = {
                docType: 'behavior-analytics',
                timestamp: '2023-01-12T14:20:10.000Z',
                referenceId: 'request-1',
                eventType: 'upsert-all',
                config: null,
                isBootstrap: true
            };

            const result = await notificationManager.produceConfigNotification(kafkaBroker, input);

            expect(result).to.deep.equal({ success: true });
        });

        it('should produce failure payload for null config with non-bootstrap eventType', async () => {
            const input = {
                docType: 'behavior-analytics',
                timestamp: '2023-01-12T14:20:10.000Z',
                referenceId: 'request-1',
                eventType: 'upsert',
                config: null
            };

            const result = await notificationManager.produceConfigNotification(kafkaBroker, input);

            expect(result).to.deep.equal({ success: true });
            const messageValue = JSON.parse(kafkaProduceStub.firstCall.args[2][0].value);
            expect(messageValue).to.deep.equal({
                status: 'failure',
                config: null,
                error: 'no config for behavior-analytics in Elasticsearch'
            });
        });

        it('should throw SyntaxError for invalid config JSON', async () => {
            const input = {
                docType: 'behavior-analytics',
                timestamp: '2023-01-12T14:20:10.000Z',
                referenceId: 'request-1',
                eventType: 'upsert',
                config: '{invalid'
            };

            try {
                await notificationManager.produceConfigNotification(kafkaBroker, input);
                throw new Error('Expected SyntaxError');
            } catch (error) {
                expect(error).to.be.instanceOf(SyntaxError);
            }
        });

        it('should throw InternalServerError for unsupported message broker', async () => {
            const unsupportedBroker = {
                getName: () => 'UnsupportedBroker',
                getClient: () => ({})
            };
            const input = {
                docType: 'behavior-analytics',
                timestamp: '2023-01-12T14:20:10.000Z',
                referenceId: 'request-1',
                eventType: 'upsert-all',
                config: JSON.stringify({ app: [{ name: 'x', value: '' }] })
            };

            try {
                await notificationManager.produceConfigNotification(unsupportedBroker, input);
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid message broker');
            }
        });
    });

    describe('consumeAndProcessNotification', () => {
        let kafkaInitStub;
        let capturedCallback;

        beforeEach(() => {
            // Capture the callback passed to initializeConsumer
            kafkaInitStub = sinon.stub(Kafka, 'initializeConsumer').callsFake(async (callback) => {
                capturedCallback = callback;
                return {};
            });
        });

        it('should throw InvalidInputError when messageBroker is null', async () => {
            try {
                await notificationManager.consumeAndProcessNotification(null, {}, {}, {});
                throw new Error('Expected InvalidInputError');
            } catch (error) {
                expect(error).to.be.instanceOf(InvalidInputError);
                expect(error.message).to.include('message broker');
            }
        });

        it('should throw InternalServerError for unsupported message broker', async () => {
            const unsupportedBroker = {
                getName: () => 'UnsupportedBroker',
                getClient: () => ({}),
                getAdminClient: () => ({})
            };

            try {
                await notificationManager.consumeAndProcessNotification(unsupportedBroker, {}, {}, {});
                throw new Error('Expected InternalServerError');
            } catch (error) {
                expect(error).to.be.instanceOf(InternalServerError);
                expect(error.message).to.include('Invalid message broker');
            }
        });

        it('should initialize Kafka consumer', async () => {
            const elasticDb = { getName: () => 'Elasticsearch' };
            const configManager = {};
            const calibration = {};

            await notificationManager.consumeAndProcessNotification(kafkaBroker, elasticDb, configManager, calibration);

            expect(kafkaInitStub.calledOnce).to.be.true;
        });

        it('should handle request-calibration message', async () => {
            const elasticDb = { getName: () => 'Elasticsearch' };
            const calibration = {
                getCalibration: sinon.stub().resolves({
                    calibration: { calibrationType: 'geo', sensors: [] },
                    timestamp: '2023-01-12T14:20:10.000Z'
                })
            };
            const configManager = {};

            await notificationManager.consumeAndProcessNotification(kafkaBroker, elasticDb, configManager, calibration);

            // Simulate receiving a message
            await capturedCallback({
                topic: 'mdx-notification',
                partition: 0,
                message: {
                    key: Buffer.from('request-calibration'),
                    value: Buffer.from('{}')
                }
            });

            expect(calibration.getCalibration.calledOnce).to.be.true;
            expect(kafkaProduceStub.called).to.be.true;
        });

        it('should handle behavior analytics config request message', async () => {
            const elasticDb = { getName: () => 'Elasticsearch' };
            const calibration = {};
            const configManager = {
                getConfig: sinon.stub().resolves({
                    config: JSON.stringify({ app: [{ name: 'x', value: '' }] }),
                    timestamp: '2023-01-12T14:20:10.000Z'
                }),
                auditHasSuccessfulConfigUpdate: sinon.stub().resolves(true)
            };

            await notificationManager.consumeAndProcessNotification(kafkaBroker, elasticDb, configManager, calibration);

            // Simulate receiving a message
            await capturedCallback({
                topic: 'mdx-notification',
                partition: 0,
                message: {
                    key: Buffer.from('behavior-analytics-config'),
                    value: Buffer.from('{}'),
                    headers: {
                        'event.type': Buffer.from('request-config'),
                        'reference-id': Buffer.from('request-1')
                    }
                }
            });

            expect(configManager.getConfig.calledOnce).to.be.true;
            expect(configManager.auditHasSuccessfulConfigUpdate.calledOnce).to.be.true;
        });

        it('should recreate direct Kafka upsert referenceId from timestamp header', async () => {
            const elasticDb = { getName: () => 'Elasticsearch' };
            const calibration = {};
            const timestamp = '2026-05-14T06:52:36.000Z';
            const config = { app: [{ name: 'behaviorMaxPoints', value: '400' }] };
            const expectedReferenceId = 'kafka-' + new Date(timestamp).getTime();
            const configManager = {
                recordConfigUpdateRequest: sinon.stub().resolves({ referenceId: expectedReferenceId, status: 'pending', error: null })
            };

            await notificationManager.consumeAndProcessNotification(kafkaBroker, elasticDb, configManager, calibration);

            await capturedCallback({
                topic: 'mdx-notification',
                partition: 0,
                message: {
                    key: Buffer.from('behavior-analytics-config'),
                    value: Buffer.from(JSON.stringify({ status: null, config, error: null })),
                    headers: {
                        'event.type': Buffer.from('upsert'),
                        'reference-id': Buffer.from('kafka'),
                        timestamp: Buffer.from(timestamp)
                    }
                }
            });

            expect(configManager.recordConfigUpdateRequest.calledOnce).to.be.true;
            expect(configManager.recordConfigUpdateRequest.firstCall.args[1]).to.deep.equal({
                docType: 'behavior-analytics',
                referenceId: expectedReferenceId,
                config,
                timestamp,
                eventType: 'upsert',
                error: null
            });
        });

        it('should ignore legacy init-behavior-analytics-config message', async () => {
            const elasticDb = { getName: () => 'Elasticsearch' };
            const calibration = {};
            const configManager = {
                initConfig: sinon.stub().resolves({})
            };

            await notificationManager.consumeAndProcessNotification(kafkaBroker, elasticDb, configManager, calibration);

            const configValue = JSON.stringify({ app: [{ name: 'x', value: '' }] });
            
            // Simulate receiving a message
            await capturedCallback({
                topic: 'mdx-notification',
                partition: 0,
                message: {
                    key: Buffer.from('init-behavior-analytics-config'),
                    value: Buffer.from(configValue)
                }
            });

            expect(configManager.initConfig.called).to.be.false;
        });

        it('should ignore messages with null key', async () => {
            const elasticDb = { getName: () => 'Elasticsearch' };
            const calibration = {
                getCalibration: sinon.stub()
            };
            const configManager = {
                getConfig: sinon.stub(),
                initConfig: sinon.stub()
            };

            await notificationManager.consumeAndProcessNotification(kafkaBroker, elasticDb, configManager, calibration);

            // Simulate receiving a message with null key
            await capturedCallback({
                topic: 'mdx-notification',
                partition: 0,
                message: {
                    key: null,
                    value: Buffer.from('{}')
                }
            });

            // None of the handlers should be called
            expect(calibration.getCalibration.called).to.be.false;
            expect(configManager.getConfig.called).to.be.false;
            expect(configManager.initConfig.called).to.be.false;
        });

        it('should ignore messages with unknown key', async () => {
            const elasticDb = { getName: () => 'Elasticsearch' };
            const calibration = {
                getCalibration: sinon.stub()
            };
            const configManager = {
                getConfig: sinon.stub(),
                initConfig: sinon.stub()
            };

            await notificationManager.consumeAndProcessNotification(kafkaBroker, elasticDb, configManager, calibration);

            // Simulate receiving a message with unknown key
            await capturedCallback({
                topic: 'mdx-notification',
                partition: 0,
                message: {
                    key: Buffer.from('unknown-key'),
                    value: Buffer.from('{}')
                }
            });

            // None of the handlers should be called
            expect(calibration.getCalibration.called).to.be.false;
            expect(configManager.getConfig.called).to.be.false;
            expect(configManager.initConfig.called).to.be.false;
        });
    });
});
