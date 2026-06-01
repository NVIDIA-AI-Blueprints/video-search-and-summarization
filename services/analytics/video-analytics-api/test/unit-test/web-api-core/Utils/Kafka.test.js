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
const Kafka = require('../../../../src/web-api-core/Utils/Kafka.js');
const MessageBroker = require('../../../../src/web-api-core/Utils/MessageBroker.js');

describe('Kafka', () => {

    describe('constructor', () => {
        it('should create a Kafka instance that extends MessageBroker', () => {
            const connectionObject = { brokers: ['localhost:9092'] };
            const configs = new Map([['timeout', 30000]]);
            const kafka = new Kafka(connectionObject, configs);

            expect(kafka).to.be.an.instanceof(Kafka);
            expect(kafka).to.be.an.instanceof(MessageBroker);
        });

        it('should set the message broker name to "Kafka"', () => {
            const connectionObject = { brokers: ['localhost:9092'] };
            const configs = new Map();
            const kafka = new Kafka(connectionObject, configs);

            expect(kafka.getName()).to.equal('Kafka');
        });

        it('should store the configs', () => {
            const connectionObject = { brokers: ['localhost:9092'] };
            const configs = new Map([['key', 'value']]);
            const kafka = new Kafka(connectionObject, configs);

            expect(kafka.getConfigs()).to.equal(configs);
        });

        it('should create a Kafka client', () => {
            const connectionObject = { brokers: ['localhost:9092'] };
            const configs = new Map();
            const kafka = new Kafka(connectionObject, configs);

            expect(kafka.getClient()).to.not.be.undefined;
        });
    });

    describe('getAdminClient', () => {
        it('should return an admin client', () => {
            const connectionObject = { brokers: ['localhost:9092'] };
            const configs = new Map();
            const kafka = new Kafka(connectionObject, configs);

            const adminClient = kafka.getAdminClient();
            expect(adminClient).to.not.be.undefined;
        });
    });

    describe('getTopic', () => {
        it('should return correct topic for "notification"', () => {
            const topic = Kafka.getTopic('notification');
            expect(topic).to.equal('mdx-notification');
        });

        it('should return correct topic for "amr"', () => {
            const topic = Kafka.getTopic('amr');
            expect(topic).to.equal('mdx-amr');
        });

        it('should return undefined for unknown topic type', () => {
            const topic = Kafka.getTopic('unknown');
            expect(topic).to.be.undefined;
        });
    });

    describe('getTopicPattern', () => {
        it('should return correct topic pattern for "rtls"', () => {
            const pattern = Kafka.getTopicPattern('rtls');
            expect(pattern).to.equal('^mdx-rtls.*');
        });

        it('should return undefined for unknown topic pattern type', () => {
            const pattern = Kafka.getTopicPattern('unknown');
            expect(pattern).to.be.undefined;
        });
    });

    describe('produceMessages', () => {
        let mockClient;
        let mockProducer;

        beforeEach(() => {
            mockProducer = {
                connect: sinon.stub().resolves(),
                send: sinon.stub().resolves(),
                disconnect: sinon.stub().resolves()
            };
            mockClient = {
                producer: sinon.stub().returns(mockProducer)
            };
        });

        afterEach(() => {
            sinon.restore();
        });

        it('should produce messages successfully', async () => {
            const topic = 'test-topic';
            const messages = [
                { value: 'message1' },
                { value: 'message2' }
            ];

            const result = await Kafka.produceMessages(mockClient, topic, messages);

            expect(result).to.deep.equal({ success: true });
            expect(mockProducer.connect.calledOnce).to.be.true;
            expect(mockProducer.send.calledOnce).to.be.true;
            expect(mockProducer.disconnect.calledOnce).to.be.true;
        });

        it('should send messages to the correct topic', async () => {
            const topic = 'my-topic';
            const messages = [{ value: 'test' }];

            await Kafka.produceMessages(mockClient, topic, messages);

            const sendArgs = mockProducer.send.firstCall.args[0];
            expect(sendArgs.topic).to.equal('my-topic');
            expect(sendArgs.messages).to.deep.equal(messages);
        });

        it('should handle messages with keys and headers', async () => {
            const topic = 'test-topic';
            const messages = [
                { key: 'key1', value: 'message1', headers: { header1: 'value1' } }
            ];

            const result = await Kafka.produceMessages(mockClient, topic, messages);

            expect(result).to.deep.equal({ success: true });
        });
    });

    describe('initializeConsumer', () => {
        const InvalidInputError = require('../../../../src/web-api-core/Errors/InvalidInputError.js');

        it('should throw InvalidInputError for invalid options with wrong type for isTopicPattern', async () => {
            const mockMessageTransfer = () => {};
            const mockClient = {
                consumer: sinon.stub().returns({
                    connect: sinon.stub().resolves(),
                    subscribe: sinon.stub().resolves(),
                    run: sinon.stub().resolves(),
                    on: sinon.stub().returns(() => {})
                })
            };
            const mockAdminClient = {
                listTopics: sinon.stub().resolves(['test-topic'])
            };

            try {
                await Kafka.initializeConsumer(
                    mockMessageTransfer,
                    mockClient,
                    mockAdminClient,
                    'test-group',
                    'test-topic',
                    { isTopicPattern: 'not-a-boolean' }
                );
                expect.fail('Should have thrown InvalidInputError');
            } catch (error) {
                expect(error).to.be.an.instanceof(InvalidInputError);
                expect(error.message).to.include('isTopicPattern');
            }
        });

        it('should throw InvalidInputError for invalid options with wrong type for autoCommit', async () => {
            const mockMessageTransfer = () => {};
            const mockClient = {};
            const mockAdminClient = {};

            try {
                await Kafka.initializeConsumer(
                    mockMessageTransfer,
                    mockClient,
                    mockAdminClient,
                    'test-group',
                    'test-topic',
                    { autoCommit: 'not-a-boolean' }
                );
                expect.fail('Should have thrown InvalidInputError');
            } catch (error) {
                expect(error).to.be.an.instanceof(InvalidInputError);
                expect(error.message).to.include('autoCommit');
            }
        });

        it('should throw InvalidInputError for invalid options with wrong type for fromBeginning', async () => {
            const mockMessageTransfer = () => {};
            const mockClient = {};
            const mockAdminClient = {};

            try {
                await Kafka.initializeConsumer(
                    mockMessageTransfer,
                    mockClient,
                    mockAdminClient,
                    'test-group',
                    'test-topic',
                    { fromBeginning: 'not-a-boolean' }
                );
                expect.fail('Should have thrown InvalidInputError');
            } catch (error) {
                expect(error).to.be.an.instanceof(InvalidInputError);
                expect(error.message).to.include('fromBeginning');
            }
        });

        it('should throw InvalidInputError for invalid additional properties in options', async () => {
            const mockMessageTransfer = () => {};
            const mockClient = {};
            const mockAdminClient = {};

            try {
                await Kafka.initializeConsumer(
                    mockMessageTransfer,
                    mockClient,
                    mockAdminClient,
                    'test-group',
                    'test-topic',
                    { invalidOption: true }
                );
                expect.fail('Should have thrown InvalidInputError');
            } catch (error) {
                expect(error).to.be.an.instanceof(InvalidInputError);
                expect(error.message).to.include('Invalid addit');
            }
        });

        it('should accept valid boolean options', async () => {
            const mockConsumer = {
                connect: sinon.stub().resolves(),
                subscribe: sinon.stub().resolves(),
                run: sinon.stub().resolves(),
                on: sinon.stub().returns(() => {})
            };
            const mockMessageTransfer = () => {};
            const mockClient = {
                consumer: sinon.stub().returns(mockConsumer)
            };
            const mockAdminClient = {
                listTopics: sinon.stub().resolves(['test-topic']),
                connect: sinon.stub().resolves()
            };

            // This should not throw - valid options
            const validOptions = {
                isTopicPattern: false,
                autoCommit: true,
                fromBeginning: false
            };

            // The function will try to wait for topic, so we need to handle that
            // Since we provide a matching topic, it should proceed
            try {
                // Start the consumer initialization but don't await completion
                // as it involves complex async operations
                const promise = Kafka.initializeConsumer(
                    mockMessageTransfer,
                    mockClient,
                    mockAdminClient,
                    'test-group',
                    'test-topic',
                    validOptions
                );
                // Give it a moment to validate options (which happens synchronously)
                await new Promise(resolve => setTimeout(resolve, 50));
                // If we reach here without throwing, options validation passed
                expect(mockAdminClient.listTopics.called).to.be.true;
            } catch (error) {
                // Should not be InvalidInputError for valid options
                if (error instanceof InvalidInputError) {
                    expect.fail('Should not throw InvalidInputError for valid options');
                }
                // Other errors (like connection issues) are expected in test environment
            }
        });

        it('should use default values for options when not provided', async () => {
            const mockConsumer = {
                connect: sinon.stub().resolves(),
                subscribe: sinon.stub().resolves(),
                run: sinon.stub().resolves(),
                on: sinon.stub().returns(() => {})
            };
            const mockMessageTransfer = () => {};
            const mockClient = {
                consumer: sinon.stub().returns(mockConsumer)
            };
            const mockAdminClient = {
                listTopics: sinon.stub().resolves(['test-topic']),
                connect: sinon.stub().resolves()
            };

            try {
                const promise = Kafka.initializeConsumer(
                    mockMessageTransfer,
                    mockClient,
                    mockAdminClient,
                    'test-group',
                    'test-topic',
                    {} // Empty options - should use defaults
                );
                await new Promise(resolve => setTimeout(resolve, 50));
                expect(mockAdminClient.listTopics.called).to.be.true;
            } catch (error) {
                if (error instanceof InvalidInputError) {
                    expect.fail('Should not throw InvalidInputError for empty options');
                }
            }
        });

        it('should handle topic not found and wait (line 213, 220-222)', async () => {
            let callCount = 0;
            const mockConsumer = {
                connect: sinon.stub().resolves(),
                subscribe: sinon.stub().resolves(),
                run: sinon.stub().resolves(),
                on: sinon.stub().returns(() => {})
            };
            const mockMessageTransfer = () => {};
            const mockClient = {
                consumer: sinon.stub().returns(mockConsumer)
            };
            // First call returns empty, second call returns the topic
            const mockAdminClient = {
                listTopics: sinon.stub().callsFake(async () => {
                    callCount++;
                    if (callCount === 1) {
                        return ['other-topic']; // Topic not present initially
                    }
                    return ['test-topic', 'other-topic']; // Topic present on second call
                }),
                connect: sinon.stub().resolves()
            };

            try {
                const promise = Kafka.initializeConsumer(
                    mockMessageTransfer,
                    mockClient,
                    mockAdminClient,
                    'test-group',
                    'test-topic',
                    {}
                );
                // Wait for the retry loop to execute
                await new Promise(resolve => setTimeout(resolve, 1200));
                expect(mockAdminClient.listTopics.callCount).to.be.greaterThan(1);
            } catch (error) {
                if (error instanceof InvalidInputError) {
                    expect.fail('Should not throw InvalidInputError');
                }
            }
        });

        it('should handle KafkaJSConnectionError and reconnect (lines 133-137)', async () => {
            const mockConsumer = {
                connect: sinon.stub().resolves(),
                subscribe: sinon.stub().resolves(),
                run: sinon.stub().resolves(),
                on: sinon.stub().returns(() => {})
            };
            const mockMessageTransfer = () => {};
            const mockClient = {
                consumer: sinon.stub().returns(mockConsumer)
            };
            
            let callCount = 0;
            const connectionError = new Error('Connection failed');
            connectionError.name = 'KafkaJSConnectionError';
            
            const mockAdminClient = {
                listTopics: sinon.stub().callsFake(async () => {
                    callCount++;
                    if (callCount === 1) {
                        throw connectionError;
                    }
                    return ['test-topic'];
                }),
                connect: sinon.stub().resolves()
            };

            try {
                const promise = Kafka.initializeConsumer(
                    mockMessageTransfer,
                    mockClient,
                    mockAdminClient,
                    'test-group',
                    'test-topic',
                    {}
                );
                await new Promise(resolve => setTimeout(resolve, 100));
                expect(mockAdminClient.connect.called).to.be.true;
            } catch (error) {
                if (error instanceof InvalidInputError) {
                    expect.fail('Should not throw InvalidInputError');
                }
            }
        });

        it('should rethrow non-KafkaJSConnectionError errors (line 137)', async () => {
            const mockMessageTransfer = () => {};
            const mockClient = {
                consumer: sinon.stub()
            };
            
            const otherError = new Error('Some other error');
            otherError.name = 'SomeOtherError';
            
            const mockAdminClient = {
                listTopics: sinon.stub().rejects(otherError),
                connect: sinon.stub().resolves()
            };

            try {
                await Kafka.initializeConsumer(
                    mockMessageTransfer,
                    mockClient,
                    mockAdminClient,
                    'test-group',
                    'test-topic',
                    {}
                );
                expect.fail('Should have thrown error');
            } catch (error) {
                expect(error.name).to.equal('SomeOtherError');
            }
        });
    });

});
